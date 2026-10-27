"""Wowhead: notícias do Forever (RSS) e tooltips da base Forever (segunda conferência).

  GET https://www.wowhead.com/news/rss/forever                      -> RSS 2.0 (title, link, pubDate, content:encoded)
  GET https://nether.wowhead.com/forever/tooltip/spell/<spellId>    -> {"name", "tooltip": "<table>…</table>"}
"""

from __future__ import annotations

import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Callable

from wow_planner.live.blizzard import match_racials, racial_patterns
from wow_planner.live.common import err_text, http_get_json, http_get_text, now_iso
from wow_planner.models.game import GameDataBundle

RSS_URL = "https://www.wowhead.com/news/rss/forever"
TOOLTIP_URL = "https://nether.wowhead.com/forever/tooltip/spell/{id}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("br", "p", "li", "div", "h2", "h3", "tr"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_text(html: str) -> str:
    p = _Text()
    p.feed(html or "")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", "".join(p.parts))).strip()


def _iso(rfc822: str | None) -> str | None:
    if not rfc822:
        return None
    try:
        return parsedate_to_datetime(rfc822).astimezone().isoformat()
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        content = it.findtext(CONTENT_NS) or it.findtext("description") or ""
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "published_at": _iso(it.findtext("pubDate")),
            "text": html_text(content),
        })
    return items


def parse_tooltip(html: str) -> dict[str, Any]:
    cd = re.search(r"<!--cooldownText-->(.*?)<!--cooldownText-->", html or "")
    desc = re.search(r'<div class="q">(.*?)</div>', html or "", re.S)
    cast = re.search(r"<td>((?:Instant|[\d.]+ sec cast|Channeled)[^<]*)</td>", html or "")
    return {
        "cooldown": re.sub(r"\s*cooldown", "", cd.group(1)).strip() if cd else None,
        "cast": cast.group(1).strip() if cast else None,
        "description": html_text(desc.group(1)) if desc else None,
    }


@dataclass
class WowheadReport:
    started_at: str
    news_seen: int = 0
    new_news: int = 0
    tooltips: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"started_at": self.started_at, "news_seen": self.news_seen, "new_news": self.new_news,
                "tooltips": self.tooltips, "errors": self.errors[:20], "ok": bool(self.news_seen or self.tooltips)}


def update_wowhead(conn: sqlite3.Connection, bundle: GameDataBundle, spell_ids: dict[str, list[int]],
                   fetch_text: Callable[[str], str] = http_get_text,
                   fetch_json: Callable[[str], Any] = http_get_json, workers: int = 6) -> WowheadReport:
    rep = WowheadReport(started_at=now_iso())
    patterns = racial_patterns(bundle)
    seen_at = now_iso()
    # --- notícias
    try:
        for item in parse_rss(fetch_text(RSS_URL)):
            ids = match_racials(item["title"] + "\n" + item["text"], patterns)
            if not ids and not re.search(r"racial", item["title"] + item["text"], re.I):
                continue
            rep.news_seen += 1
            if not conn.execute("SELECT 1 FROM wowhead_news WHERE link=?", (item["link"],)).fetchone():
                rep.new_news += 1
            lines = [ln for ln in item["text"].split("\n") if match_racials(ln, patterns)]
            conn.execute(
                """INSERT INTO wowhead_news(link, title, published_at, summary, racial_ids, lines_json, first_seen_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(link) DO UPDATE SET title=excluded.title, summary=excluded.summary,
                     racial_ids=excluded.racial_ids, lines_json=excluded.lines_json""",
                (item["link"], item["title"], item["published_at"], item["text"][:600], ",".join(ids),
                 json.dumps(lines[:40], ensure_ascii=False), seen_at))
    except Exception as exc:  # noqa: BLE001
        rep.errors.append(f"RSS de notícias: {err_text(exc)}")

    # --- tooltips (conferência)
    jobs = [(rid, sid) for rid, lst in spell_ids.items() for sid in lst]

    def one(job: tuple[str, int]) -> tuple[str, int, dict[str, Any] | None, str | None]:
        rid, sid = job
        try:
            data = fetch_json(TOOLTIP_URL.format(id=sid))
            return rid, sid, parse_tooltip(data.get("tooltip", "")) | {"name": data.get("name")}, None
        except Exception as exc:  # noqa: BLE001
            return rid, sid, None, f"tooltip #{sid}: {err_text(exc)}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rid, sid, tip, err in pool.map(one, jobs):
            if err:
                rep.errors.append(err)
                continue
            rep.tooltips += 1
            conn.execute(
                """INSERT INTO wowhead_tooltips(spell_id, racial_id, name, cast, cooldown, description, fetched_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(spell_id) DO UPDATE SET name=excluded.name, cast=excluded.cast,
                     cooldown=excluded.cooldown, description=excluded.description, fetched_at=excluded.fetched_at""",
                (sid, rid, tip.get("name"), tip.get("cast"), tip.get("cooldown"), tip.get("description"), seen_at))
    conn.execute("INSERT INTO live_source_runs(source, started_at, finished_at, ok, items, new_items, errors) "
                 "VALUES (?,?,?,?,?,?,?)",
                 ("wowhead", rep.started_at, now_iso(), int(bool(rep.news_seen or rep.tooltips)),
                  rep.news_seen + rep.tooltips, rep.new_news, "\n".join(rep.errors[:50]) or None))
    conn.commit()
    return rep


def wowhead_tooltips(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    return {r["spell_id"]: dict(r) for r in conn.execute("SELECT * FROM wowhead_tooltips")}


def news(conn: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM wowhead_news ORDER BY published_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["racial_ids"] = [x for x in (d.get("racial_ids") or "").split(",") if x]
        d["lines"] = json.loads(d.pop("lines_json") or "[]")
        out.append(d)
    return out


def same_text(a: str | None, b: str | None) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9%]+", "", (s or "").lower())  # noqa: E731
    return norm(a) == norm(b)
