"""Notas oficiais da Blizzard (fóruns Discourse) sobre racials.

Fóruns usam Discourse, que expõe JSON público:
  GET https://us.forums.blizzard.com/en/wow/t/<topic_id>.json   -> posts (cooked HTML, created_at, updated_at)
  GET https://us.forums.blizzard.com/en/wow/search.json?q=...   -> descoberta de tópicos novos da Blizzard

Cada linha de patch note (<li>/<p>) que cita uma racial vira uma nota com data de postagem e de edição.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from wow_planner.live.common import Fetcher, err_text, http_get_json, leaf_lines, now_iso
from wow_planner.models.game import GameDataBundle

FORUMS = {
    "us": "https://us.forums.blizzard.com/en/wow",
    "eu": "https://eu.forums.blizzard.com/en/wow",
}
# Tópicos conhecidos (Development Notes do Forever). A busca abaixo descobre tópicos novos.
DEFAULT_TOPICS = [("us", 2360696), ("eu", 631316)]
DISCOVERY_QUERIES = ["Forever @Kaivax order:latest"]
TOPIC_TITLE = re.compile(r"Development Notes|Hotfix|Patch Notes|Tuning|Racial|Class Changes|Known Issues", re.I)
MAX_DISCOVERED = 6

# nomes alternativos usados pela Blizzard; racials removidas apontam para a substituta
EXTRA_ALIASES: dict[str, list[str]] = {
    "the_human_spirit": [r"Human Spirit"],
    "wind_blessed": [r"Wind[- ]Blessed"],
    "read_ley_line": [r"Read Ley ?line", r"\bAttuned\b"],
    "skysight": [r"\bCharged\b"],
    "artisans_precision": [r"Artisan[’']s Precision", r"Earth Fury", r"^Mace Specialization:"],
    "command": [r"^Command re-added", r"^Savage\b", r"^Axe Specialization:"],
    "ruthless": [r"^Sword Specialization:"],
    "eureka": [r"\bEureka\b"],
    "elunes_light": [r"Elune[’']s Light"],
    "elunes_grace": [r"Elune[’']s Grace"],
    "regeneration": [r"(?<!Rapid )(?<!Health )\bRegeneration:"],
}
SKIP_DEFAULT = {"regeneration", "command"}  # nomes genéricos demais: só os aliases acima


def racial_patterns(bundle: GameDataBundle) -> dict[str, re.Pattern[str]]:
    pats: dict[str, re.Pattern[str]] = {}
    for r in bundle.racials:
        alts = list(EXTRA_ALIASES.get(r.id, []))
        if r.id not in SKIP_DEFAULT:
            name = re.escape(r.name.rstrip("!")).replace(r"\'", "['’]").replace("'", "['’]")
            alts.append(rf"\b{name}\b")
        pats[r.id] = re.compile("|".join(alts))
    return pats


def match_racials(text: str, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    return [rid for rid, pat in patterns.items() if pat.search(text)]


# ------------------------------------------------------------------------- comparação com tooltip
_UNIT = r"(?:%|seconds?|sec|minutes?|min|hours?|yards?|yd)"
_CHANGE = re.compile(rf"(\d+(?:\.\d+)?)\s*({_UNIT})?[^()]*?\(was\s+(\d+(?:\.\d+)?)\s*({_UNIT})?\)", re.I)
_NORM = {"second": "sec", "seconds": "sec", "minute": "min", "minutes": "min", "hour": "hr", "hours": "hr",
         "yard": "yd", "yards": "yd"}


def _norm(num: str, unit: str | None) -> str:
    u = (unit or "").lower()
    u = _NORM.get(u, u)
    return f"{num}%" if u == "%" else (f"{num} {u}" if u else num)


def tooltip_status(note_text: str, variant: dict[str, Any] | None) -> dict[str, Any] | None:
    """'outdated' quando o tooltip ainda mostra o valor antigo anunciado em '(was X)'; 'removed' se a nota remove."""
    if re.search(r"\bremoved\b", note_text, re.I) and not re.search(r"removed upon|is removed when", note_text, re.I):
        return {"status": "removed"}
    m = _CHANGE.search(note_text)
    if not m or not variant:
        return None
    new = _norm(m.group(1), m.group(2) or m.group(4))
    old = _norm(m.group(3), m.group(4) or m.group(2))
    blob = " ".join(str(variant.get(k) or "") for k in ("cooldown", "cast", "description", "aura")).lower()
    blob = re.sub(r"(\d) (seconds?|minutes?)", lambda x: f"{x.group(1)} {_NORM[x.group(2)]}", blob)
    if old.lower() in blob and new.lower() not in blob:
        return {"status": "outdated", "old": old, "new": new}
    if new.lower() in blob:
        return {"status": "applied", "old": old, "new": new}
    return {"status": "unknown", "old": old, "new": new}


# ------------------------------------------------------------------------- coleta
@dataclass
class NotesReport:
    started_at: str
    topics: list[dict[str, Any]] = field(default_factory=list)
    lines_seen: int = 0
    new_notes: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"started_at": self.started_at, "topics": self.topics, "lines_seen": self.lines_seen,
                "new_notes": self.new_notes, "errors": self.errors[:20], "ok": bool(self.topics)}


def _note_id(text: str, racial_id: str) -> str:
    norm = re.sub(r"\W+", " ", text.lower()).strip()
    return hashlib.sha1(f"{racial_id}|{norm}".encode()).hexdigest()[:16]


def discover_topics(fetch: Fetcher) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for q in DISCOVERY_QUERIES:
        data = fetch(f"{FORUMS['us']}/search.json?q={urllib.parse.quote(q)}")
        for t in data.get("topics", []):
            if TOPIC_TITLE.search(t.get("title", "")):
                found.append(("us", int(t["id"])))
    return found[:MAX_DISCOVERED]


def collect_notes(conn: sqlite3.Connection, bundle: GameDataBundle, fetch: Fetcher = http_get_json) -> NotesReport:
    rep = NotesReport(started_at=now_iso())
    patterns = racial_patterns(bundle)
    topics = list(DEFAULT_TOPICS)
    try:
        for t in discover_topics(fetch):
            if t not in topics:
                topics.append(t)
    except Exception as exc:  # noqa: BLE001
        rep.errors.append(f"busca de tópicos falhou ({err_text(exc)})")

    seen_at = now_iso()
    for region, topic_id in topics:
        base = FORUMS[region]
        try:
            data = fetch(f"{base}/t/{topic_id}.json")
        except Exception as exc:  # noqa: BLE001
            rep.errors.append(f"tópico {region.upper()} {topic_id}: {err_text(exc)}")
            continue
        title = data.get("title", "")
        rep.topics.append({"region": region, "topic_id": topic_id, "title": title,
                           "last_posted_at": data.get("last_posted_at")})
        for post in data.get("post_stream", {}).get("posts", []):
            if not (post.get("staff") or post.get("admin") or post.get("moderator")):
                continue  # só posts da Blizzard
            url = f"{base}/t/{topic_id}/{post['post_number']}"
            for _tag, text in leaf_lines(post.get("cooked", "")):
                ids = match_racials(text, patterns)
                if not ids:
                    continue
                rep.lines_seen += 1
                for rid in ids:
                    nid = _note_id(text, rid)
                    if not conn.execute("SELECT 1 FROM blizzard_notes WHERE id=?", (nid,)).fetchone():
                        rep.new_notes += 1
                    conn.execute(
                        """INSERT INTO blizzard_notes(id, racial_id, region, topic_id, topic_title, post_number,
                               author, posted_at, edited_at, url, text, first_seen_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET
                             -- mesma linha em outro post/região: fica a ocorrência mais recente (ex.: adendo)
                             post_number = CASE WHEN excluded.posted_at > blizzard_notes.posted_at
                                                THEN excluded.post_number ELSE blizzard_notes.post_number END,
                             url = CASE WHEN excluded.posted_at > blizzard_notes.posted_at
                                        THEN excluded.url ELSE blizzard_notes.url END,
                             posted_at = MAX(blizzard_notes.posted_at, excluded.posted_at),
                             edited_at = MAX(COALESCE(blizzard_notes.edited_at, ''), COALESCE(excluded.edited_at, '')),
                             topic_title = excluded.topic_title""",
                        (nid, rid, region, topic_id, title, post["post_number"], post.get("username"),
                         post.get("created_at"), post.get("updated_at"), url, text, seen_at))
    conn.execute("INSERT INTO live_source_runs(source, started_at, finished_at, ok, items, new_items, errors) "
                 "VALUES (?,?,?,?,?,?,?)",
                 ("blizzard", rep.started_at, now_iso(), int(bool(rep.topics)), rep.lines_seen, rep.new_notes,
                  "\n".join(rep.errors) or None))
    conn.commit()
    return rep


def notes_by_racial(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Notas por racial, mais recentes primeiro; dedup entre US/EU (fica a versão US)."""
    rows = conn.execute(
        "SELECT * FROM blizzard_notes ORDER BY COALESCE(posted_at, '') DESC, region DESC, post_number DESC").fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for r in rows:
        d = dict(r)
        key = (d["racial_id"], re.sub(r"\W+", " ", d["text"].lower()).strip())
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(d["racial_id"], []).append(d)
    # US primeiro quando houver a mesma data
    for lst in out.values():
        lst.sort(key=lambda d: (d.get("posted_at") or "", d["region"] == "us"), reverse=True)
    return out


def seed_notes(conn: sqlite3.Connection, seed: list[dict[str, Any]]) -> None:
    """Carrega as notas coletadas na criação do app, se o banco ainda não tem nenhuma."""
    if conn.execute("SELECT COUNT(*) FROM blizzard_notes").fetchone()[0]:
        return
    for n in seed:
        conn.execute(
            """INSERT OR IGNORE INTO blizzard_notes(id, racial_id, region, topic_id, topic_title, post_number, author,
                   posted_at, edited_at, url, text, first_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_note_id(n["text"], n["racial_id"]), n["racial_id"], n["region"], n["topic_id"], n["topic_title"],
             n["post_number"], n.get("author"), n["posted_at"], n.get("edited_at"), n["url"], n["text"],
             n.get("first_seen_at", n["posted_at"])))
    conn.commit()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
