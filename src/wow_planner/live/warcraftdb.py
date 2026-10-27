"""Atualização ao vivo das racials a partir do Warcraft DB (forever.warcraftdb.com).

Endpoints usados (os mesmos que o site chama no navegador; não há API documentada):
  GET /api/v1/search?q=<nome>          -> [{id, type: "spell", name, ...}]
  GET /api/v1/tooltip/spell/<spellId>  -> {"html": "<tt-title>…", "name": …}   (CORS: *)

O tooltip vem em inglês, exatamente como no jogo. Se o site mudar o formato ou bloquear o
acesso, o updater devolve erros legíveis e o app continua usando os últimos dados salvos.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from wow_planner.models.game import GameDataBundle

BASE_URL = "https://forever.warcraftdb.com"
SEED_FILE = Path(__file__).resolve().parent.parent / "game_data" / "data" / "racials_live_seed.json"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36 wow-collection-planner/0.3")
COMPARED_FIELDS = ("cast", "cooldown", "range", "cost", "required_level", "description", "aura", "subtitle")

# primeiro nome de raça que aparece em "Requires X, Y" -> nossas raças (o DB também lista raças do Retail)
RACE_TOKENS = {"dwarf", "gnome", "human", "night elf", "orc", "tauren", "troll", "undead", "skyborne racial"}

Fetcher = Callable[[str], Any]


# ---------------------------------------------------------------------------------- parser
class _TooltipParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None]] = []
        self.title = ""
        self.subtitle = ""
        self.spans: list[str] = []
        self.lines: dict[str, list[str]] = {}
        self.body: list[str] = []
        self.aura: list[str] = []
        self.after_aura = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack.append((tag, dict(attrs).get("data-line-type")))
        self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        name, ltype = self.stack.pop()
        text = "".join(self._buf).strip()
        self._buf = []
        if name == "tt-title":
            self.title = text
        elif name == "tt-subtitle":
            self.subtitle = text
        elif name == "span" and any(t == "tt-double-line" for t, _ in self.stack):
            self.spans.append(text)
        elif name == "tt-line":
            if ltype == "spell-aura":
                self.after_aura = True
            elif ltype:
                self.lines.setdefault(ltype, []).append(text)

    def handle_data(self, data: str) -> None:
        if self.stack:
            self._buf.append(data)
        elif self.after_aura:
            self.aura.append(data)
        else:
            self.body.append(data)


def _clean(text: str) -> str | None:
    t = re.sub(r"\s+", " ", text).strip()
    return t or None


def parse_tooltip(html: str) -> dict[str, Any]:
    """Converte o HTML do tooltip em campos (inglês, como no jogo)."""
    p = _TooltipParser()
    p.feed(html or "")
    out: dict[str, Any] = {"name": p.title or None, "subtitle": p.subtitle or None, "cast": None,
                           "cooldown": None, "range": None, "cost": None}
    for s in p.spans:
        if re.search(r"cooldown", s, re.I):
            out["cooldown"] = re.sub(r"\s*cooldown", "", s, flags=re.I).strip()
        elif re.search(r"range|\byd\b", s, re.I):
            out["range"] = s
        elif re.search(r"mana|rage|energy|health|of base", s, re.I):
            out["cost"] = s
        elif s:
            out["cast"] = s
    def req(kind: str) -> str | None:
        vals = p.lines.get(kind)
        return re.sub(r"^Requires\s+(Level\s+)?", "", vals[0]).strip() if vals else None
    classes = req("required-class")
    out["classes"] = [c.strip().lower() for c in classes.split(",")] if classes else []
    out["required_race"] = req("required-race") or req("required-skill")
    out["required_level"] = req("required-level")
    out["description"] = _clean("".join(p.body))
    out["aura"] = _clean("".join(p.aura))
    return out


def _rank(subtitle: str | None) -> int:
    m = re.match(r"Rank (\d+)", subtitle or "")
    return int(m.group(1)) if m else 0


def _is_player_racial(t: dict[str, Any]) -> bool:
    race = (t.get("required_race") or "").split(",")[0].strip().lower()
    return race in RACE_TOKENS


def select_variants(tooltips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mantém só versões de jogador; por grupo de classes, fica com o maior rank."""
    best: dict[tuple[str, ...], dict[str, Any]] = {}
    for t in tooltips:
        if not _is_player_racial(t):
            continue
        key = tuple(t.get("classes") or [])
        cur = best.get(key)
        if cur is None or _rank(t.get("subtitle")) > _rank(cur.get("subtitle")):
            best[key] = t
    return sorted(best.values(), key=lambda v: (len(v.get("classes") or []) == 0, v.get("classes") or [], v["spell_id"]))


def pick_variant(variants: list[dict[str, Any]], class_id: str | None) -> dict[str, Any] | None:
    if not variants:
        return None
    if class_id:
        for v in variants:
            if class_id in (v.get("classes") or []):
                return v
    for v in variants:
        if not v.get("classes"):
            return v
    return variants[0]


# ---------------------------------------------------------------------------------- http
def http_get_json(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               "Referer": BASE_URL + "/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------------------------- storage
def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash(variants: list[dict[str, Any]]) -> str:
    slim = [{k: v.get(k) for k in ("spell_id", *COMPARED_FIELDS)} for v in variants]
    return hashlib.sha256(json.dumps(slim, sort_keys=True).encode()).hexdigest()[:16]


def load_seed() -> dict[str, Any]:
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


def live_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """racial_id -> {variants, fetched_at, method}. Usa a semente embutida para o que não foi buscado."""
    seed = load_seed()
    out = {k: {"variants": v["variants"], "fetched_at": seed["fetched_at"], "method": "seed"}
           for k, v in seed["racials"].items()}
    for row in conn.execute("SELECT racial_id, variants_json, fetched_at, method FROM racial_live"):
        out[row[0]] = {"variants": json.loads(row[1]), "fetched_at": row[2], "method": row[3]}
    return out


def last_update(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM racial_live_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def recent_changes(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM racial_live_changes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------------- update
@dataclass
class UpdateReport:
    method: str
    started_at: str
    updated: int = 0
    unchanged: int = 0
    new_variants: int = 0
    changes: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """True quando nada pôde ser buscado (site fora do ar / bloqueio)."""
        return self.updated == 0 and self.unchanged == 0 and bool(self.errors)

    def as_dict(self) -> dict[str, Any]:
        return {"method": self.method, "started_at": self.started_at, "updated": self.updated,
                "unchanged": self.unchanged, "new_variants": self.new_variants, "changes": self.changes,
                "errors": self.errors[:30], "blocked": self.blocked}


def _diff(racial_id: str, old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changes = []
    old_by = {v["spell_id"]: v for v in old}
    new_by = {v["spell_id"]: v for v in new}
    for sid, nv in new_by.items():
        ov = old_by.get(sid)
        if ov is None:
            changes.append({"racial_id": racial_id, "spell_id": sid, "field": "variant",
                            "old": None, "new": f"{nv.get('name')} ({', '.join(nv.get('classes') or []) or 'all classes'})"})
            continue
        for f in COMPARED_FIELDS:
            if (ov.get(f) or None) != (nv.get(f) or None):
                changes.append({"racial_id": racial_id, "spell_id": sid, "field": f, "old": ov.get(f), "new": nv.get(f)})
    for sid, ov in old_by.items():
        if sid not in new_by:
            changes.append({"racial_id": racial_id, "spell_id": sid, "field": "variant", "old": ov.get("name"), "new": None})
    return changes


def save_results(conn: sqlite3.Connection, results: dict[str, list[dict[str, Any]]], method: str,
                 report: UpdateReport) -> UpdateReport:
    current = live_map(conn)
    now = _now()
    for racial_id, variants in results.items():
        if not variants:
            continue
        old = current.get(racial_id, {}).get("variants", [])
        changes = _diff(racial_id, old, variants)
        if changes:
            report.updated += 1
            report.new_variants += sum(1 for c in changes if c["field"] == "variant" and c["new"])
            report.changes.extend(changes)
            for c in changes:
                conn.execute(
                    "INSERT INTO racial_live_changes(racial_id, spell_id, field, old_value, new_value, detected_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (racial_id, c["spell_id"], c["field"], c["old"], c["new"], now))
        else:
            report.unchanged += 1
        conn.execute(
            """INSERT INTO racial_live(racial_id, variants_json, content_hash, fetched_at, method)
               VALUES (?,?,?,?,?)
               ON CONFLICT(racial_id) DO UPDATE SET variants_json=excluded.variants_json,
                 content_hash=excluded.content_hash, fetched_at=excluded.fetched_at, method=excluded.method""",
            (racial_id, json.dumps(variants, ensure_ascii=False), _hash(variants), now, method))
    conn.execute(
        "INSERT INTO racial_live_runs(method, started_at, finished_at, updated, unchanged, new_variants, errors) "
        "VALUES (?,?,?,?,?,?,?)",
        (method, report.started_at, now, report.updated, report.unchanged, report.new_variants,
         "\n".join(report.errors) or None))
    conn.commit()
    return report


def _name_for(racial_id: str, bundle: GameDataBundle, known: dict[str, Any]) -> str:
    variants = known.get(racial_id, {}).get("variants") or []
    if variants and variants[0].get("name"):
        return variants[0]["name"]
    return bundle.racial(racial_id).name


def update_from_web(conn: sqlite3.Connection, bundle: GameDataBundle, fetch: Fetcher = http_get_json,
                    workers: int = 6) -> UpdateReport:
    """Busca nome -> spells -> tooltips de cada racial; descobre variantes novas e grava as mudanças."""
    report = UpdateReport(method="web", started_at=_now())
    known = live_map(conn)

    def one(racial_id: str) -> tuple[str, list[dict[str, Any]], list[str]]:
        errs: list[str] = []
        name = _name_for(racial_id, bundle, known)
        ids: list[int] = []
        try:
            hits = fetch(f"{BASE_URL}/api/v1/search?q={urllib.parse.quote(name)}")
            ids = [int(h["id"]) for h in hits if h.get("type") == "spell" and str(h.get("name", "")).lower() == name.lower()]
        except Exception as exc:  # noqa: BLE001
            errs.append(f"{name}: busca falhou ({_err(exc)})")
        if not ids:  # cai para os IDs já conhecidos
            ids = [v["spell_id"] for v in known.get(racial_id, {}).get("variants", [])]
        tooltips = []
        for sid in ids:
            try:
                data = fetch(f"{BASE_URL}/api/v1/tooltip/spell/{sid}")
                t = parse_tooltip(data.get("html", ""))
                t["spell_id"] = sid
                t["name"] = t.get("name") or data.get("name") or name
                tooltips.append(t)
            except Exception as exc:  # noqa: BLE001
                errs.append(f"{name} #{sid}: tooltip falhou ({_err(exc)})")
        variants = [_public(v) for v in select_variants(tooltips)]
        return racial_id, variants, errs

    results: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for racial_id, variants, errs in pool.map(one, [r.id for r in bundle.racials]):
            results[racial_id] = variants
            report.errors.extend(errs)
    return save_results(conn, results, "web", report)


def ingest_tooltips(conn: sqlite3.Connection, payload: dict[str, list[dict[str, Any]]]) -> UpdateReport:
    """Recebe tooltips buscados pelo navegador: {racial_id: [{spell_id, html}]}."""
    report = UpdateReport(method="browser", started_at=_now())
    results = {}
    for racial_id, items in payload.items():
        tooltips = []
        for it in items or []:
            try:
                t = parse_tooltip(str(it.get("html", "")))
                t["spell_id"] = int(it["spell_id"])
                tooltips.append(t)
            except (KeyError, ValueError, TypeError) as exc:
                report.errors.append(f"{racial_id}: item inválido ({exc})")
        results[racial_id] = [_public(v) for v in select_variants(tooltips)]
    return save_results(conn, results, "browser", report)


def known_spell_ids(conn: sqlite3.Connection) -> dict[str, list[int]]:
    return {k: [v["spell_id"] for v in d["variants"]] for k, d in live_map(conn).items()}


def _public(t: dict[str, Any]) -> dict[str, Any]:
    keys = ("spell_id", "name", "subtitle", "cast", "cooldown", "range", "cost", "required_level",
            "classes", "description", "aura")
    return {k: t.get(k) for k in keys}


def _err(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"sem conexão: {exc.reason}"
    return type(exc).__name__ + (f": {exc}" if str(exc) else "")
