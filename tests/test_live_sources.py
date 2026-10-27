import json

from wow_planner.live import (ensure_seeds, last_runs, news, notes_by_racial, pick_variant, live_map,
                              tooltip_status, update_all)
from wow_planner.live.blizzard import collect_notes, match_racials, racial_patterns
from wow_planner.live.common import leaf_lines
from wow_planner.live.wowhead import parse_rss, parse_tooltip as wh_parse

TOPIC = {
    "title": "WoW Forever Beta Development Notes – Updated October 26",
    "last_posted_at": "2026-10-27T15:37:37.304Z",
    "post_stream": {"posts": [
        {"post_number": 7, "username": "Kaivax", "staff": True, "created_at": "2026-10-26T20:35:27Z",
         "updated_at": "2026-10-27T15:36:57Z",
         "cooked": "<h3>Human</h3><ul><li>Ruthless now increases critical chance by 1% (was 2%).</li>"
                   "<li>Human Spirit now increases Spirit by 10% (was 5%).</li></ul>"
                   "<h3>Orc</h3><ul><li>Savage removed (1% increased critical strike chance).</li>"
                   "<li>Blood Fury’s cooldown reduced to 2 minutes (was 3 minutes).</li></ul>"
                   "<p>Weaponmaster’s Sword Specialization fix.</p>"},
        {"post_number": 9, "username": "Jogador", "staff": False, "created_at": "2026-10-27T16:00:00Z",
         "updated_at": "2026-10-27T16:00:00Z", "cooked": "<p>Ruthless is now 50%!!</p>"},
    ]},
}
RSS = """<?xml version="1.0"?><rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0"><channel>
<item><title>More Racial Ability Tuning on WoW: Forever</title><link>https://www.wowhead.com/news=1/x</link>
<pubDate>Tue, 27 Oct 2026 10:47:39 -0500</pubDate><content:encoded><![CDATA[<p>Orc</p><ul><li>Blood Fury’s cooldown reduced to 2 minutes (was 3 minutes).</li></ul>]]></content:encoded></item>
<item><title>Poster signing</title><link>https://www.wowhead.com/news=2/y</link><pubDate>Mon, 26 Oct 2026 14:09:41 -0500</pubDate>
<description>Meet the devs</description></item></channel></rss>"""
WH_TIP = {"name": "Blood Fury", "tooltip": '<table><tr><td>Instant</td><th><!--cooldownText-->3 min cooldown<!--cooldownText--></th></tr></table>'
          '<table><tr><td><div class="q">Increases Attack Power and Spell Power by 10% for 15 sec.</div></td></tr></table>'}


def fake_json(url):
    if "search.json" in url:
        return {"topics": [{"id": 2360696, "title": TOPIC["title"]}, {"id": 1, "title": "Druid cat form quest"}]}
    if "/t/" in url:
        return TOPIC
    if "nether.wowhead.com" in url:
        return WH_TIP
    raise OSError("warcraftdb offline")


def test_leaf_lines_skip_parents():
    lines = [t for _, t in leaf_lines("<ul><li>Pai<ul><li>Filho</li></ul></li></ul><p>Solto</p>")]
    assert "Filho" in lines and "Solto" in lines and not any(l.startswith("Pai") for l in lines)


def test_match_racials_aliases_and_false_positives(bundle):
    pats = racial_patterns(bundle)
    assert match_racials("Wind-Blessed Haste bonus reduced to 2% (was 3%).", pats) == ["wind_blessed"]
    assert match_racials("Earth Fury (2% increased critical strike damage and healing) removed.", pats) == ["artisans_precision"]
    assert match_racials("Command re-added, increasing Warlock and Hunter pet damage by 5%.", pats) == ["command"]
    assert match_racials("Fixed an issue where Weaponmaster’s Sword Specialization could be activated.", pats) == []
    assert match_racials("Lingering Rage no longer stops the player’s Health Regeneration after combat.", pats) == []


def test_tooltip_status_detects_outdated_and_removed():
    old = {"cooldown": "3 min", "description": "Increases Attack Power and Spell Power by 10% for 15 sec."}
    st = tooltip_status("Blood Fury’s cooldown reduced to 2 minutes (was 3 minutes).", old)
    assert st == {"status": "outdated", "old": "3 min", "new": "2 min"}
    assert tooltip_status("Blood Fury’s cooldown reduced to 2 minutes (was 3 minutes).", {"cooldown": "2 min"})["status"] == "applied"
    assert tooltip_status("Savage removed (1% increased critical strike chance).", None)["status"] == "removed"
    assert tooltip_status("Charged persists through death, and is removed upon entering a raid.", None) is None
    st = tooltip_status("Ruthless now increases critical chance by 1% (was 2%).",
                        {"description": "Increases your critical strike chance with all spells and attacks by 2%."})
    assert st["status"] == "outdated"


def test_collect_notes_only_staff_with_dates(conn, bundle):
    rep = collect_notes(conn, bundle, fetch=fake_json)
    assert rep.as_dict()["ok"] and rep.new_notes >= 4
    notes = notes_by_racial(conn)
    assert notes["ruthless"][0]["posted_at"].startswith("2026-10-26")
    assert notes["ruthless"][0]["edited_at"].startswith("2026-10-27")
    assert all("50%" not in n["text"] for n in notes["ruthless"])  # post de jogador ignorado
    assert "command" in notes  # "Savage removed" aponta para a substituta
    assert collect_notes(conn, bundle, fetch=fake_json).new_notes == 0  # idempotente


def test_wowhead_parsers():
    items = parse_rss(RSS)
    assert items[0]["title"].startswith("More Racial") and items[0]["published_at"].startswith("2026-10-27")
    assert "Blood Fury" in items[0]["text"]
    t = wh_parse(WH_TIP["tooltip"])
    assert t["cooldown"] == "3 min" and t["cast"] == "Instant" and t["description"].startswith("Increases Attack Power")


def test_update_all_mixed_failures(conn, bundle):
    ensure_seeds(conn)
    r = update_all(conn, bundle, fetch_json=fake_json, fetch_text=lambda u: RSS)
    assert r["blocked"] is True                    # warcraftdb fora: interface usa o navegador
    assert r["blizzard"]["ok"] and r["wowhead"]["ok"]
    assert any(n["title"].startswith("More Racial") for n in news(conn))
    runs = last_runs(conn)
    assert runs["blizzard"]["last_ok"] and runs["wowhead"]["last_ok"] and runs["warcraftdb"]["last_ok"] is None
    # dados salvos do Warcraft DB continuam valendo
    assert pick_variant(live_map(conn)["blood_fury"]["variants"], None)["cooldown"] == "3 min"


def test_seeds_include_launch_notes(conn):
    ensure_seeds(conn)
    notes = notes_by_racial(conn)
    bf = notes["blood_fury"][0]
    assert bf["text"].startswith("Blood Fury’s cooldown reduced to 2 minutes") and bf["posted_at"].startswith("2026-10-27")
    assert len(news(conn)) == 2
