"""Atualização unificada das fontes ao vivo + cargas iniciais (seeds)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from wow_planner.live import blizzard, warcraftdb, wowhead
from wow_planner.models.game import GameDataBundle

DATA = Path(__file__).resolve().parent.parent / "game_data" / "data"
SOURCES = {
    "warcraftdb": {"label": "Warcraft DB", "url": "https://forever.warcraftdb.com", "what": "tooltips oficiais (dados do cliente)"},
    "blizzard": {"label": "Blizzard (fórum oficial)", "url": "https://us.forums.blizzard.com/en/wow/t/2360696",
                 "what": "Development Notes: mudanças anunciadas"},
    "wowhead": {"label": "Wowhead", "url": "https://www.wowhead.com/forever", "what": "notícias e tooltips (conferência)"},
}


def ensure_seeds(conn: sqlite3.Connection) -> None:
    blizzard.seed_notes(conn, json.loads((DATA / "blizzard_notes_seed.json").read_text(encoding="utf-8"))["notes"])
    if not conn.execute("SELECT COUNT(*) FROM wowhead_news").fetchone()[0]:
        seed = json.loads((DATA / "wowhead_news_seed.json").read_text(encoding="utf-8"))
        for n in seed["news"]:
            conn.execute(
                "INSERT OR IGNORE INTO wowhead_news(link, title, published_at, summary, racial_ids, lines_json, first_seen_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (n["link"], n["title"], n["published_at"], n["summary"], ",".join(n["racial_ids"]),
                 json.dumps(n["lines"], ensure_ascii=False), seed["collected_at"]))
        conn.commit()


def update_all(conn: sqlite3.Connection, bundle: GameDataBundle, fetch_json=None, fetch_text=None) -> dict[str, Any]:
    """Roda as três fontes; uma falhar não impede as outras."""
    kw_json = {"fetch": fetch_json} if fetch_json else {}
    out: dict[str, Any] = {}
    w = warcraftdb.update_from_web(conn, bundle, **kw_json)
    conn.execute("INSERT INTO live_source_runs(source, started_at, finished_at, ok, items, new_items, errors) "
                 "VALUES (?,?,?,?,?,?,?)",
                 ("warcraftdb", w.started_at, blizzard.now_iso(), int(not w.blocked), w.updated + w.unchanged,
                  w.updated, "\n".join(w.errors[:50]) or None))
    conn.commit()
    out["warcraftdb"] = w.as_dict()
    out["blizzard"] = blizzard.collect_notes(conn, bundle, **kw_json).as_dict()
    ids = {k: [v["spell_id"] for v in d["variants"]] for k, d in warcraftdb.live_map(conn).items()
           if k in {r.id for r in bundle.racials}}
    out["wowhead"] = wowhead.update_wowhead(conn, bundle, ids,
                                            **({"fetch_text": fetch_text} if fetch_text else {}),
                                            **({"fetch_json": fetch_json} if fetch_json else {})).as_dict()
    out["blocked"] = w.blocked  # warcraftdb bloqueado -> a interface tenta pelo navegador
    return out


def last_runs(conn: sqlite3.Connection) -> dict[str, Any]:
    out = {}
    for src, meta in SOURCES.items():
        ok = conn.execute("SELECT * FROM live_source_runs WHERE source=? AND ok=1 ORDER BY id DESC LIMIT 1", (src,)).fetchone()
        last = conn.execute("SELECT * FROM live_source_runs WHERE source=? ORDER BY id DESC LIMIT 1", (src,)).fetchone()
        out[src] = meta | {"last_ok": dict(ok) if ok else None, "last_try": dict(last) if last else None}
    return out
