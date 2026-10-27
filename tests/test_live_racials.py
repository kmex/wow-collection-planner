import urllib.error

from wow_planner.live import ingest_tooltips, live_map, parse_tooltip, pick_variant, recent_changes, update_from_web
from wow_planner.live.warcraftdb import select_variants

BLOOD_FURY = ('<tt-double-line><tt-title>Blood Fury</tt-title><tt-subtitle>Racial</tt-subtitle></tt-double-line>'
              '<tt-double-line><span>Instant</span><span>3 min cooldown</span></tt-double-line>'
              '<tt-line data-line-type="required-race">Requires Orc, Dark Iron Dwarf</tt-line>'
              'Increases Attack Power and Spell Power by 10% for 15 sec.'
              '<tt-line data-line-type="spell-aura">\nAura</tt-line>Attack Power and Spell Power increased by 10%.')
CHASTISE = ('<tt-double-line><tt-title>Chastise</tt-title><tt-subtitle>Rank 5</tt-subtitle></tt-double-line>'
            '<tt-double-line><span>225 Mana</span><span>20 yd range</span></tt-double-line>'
            '<tt-double-line><span>Instant</span><span>2 min cooldown</span></tt-double-line>'
            '<tt-line data-line-type="required-class">Requires Priest</tt-line>'
            '<tt-line data-line-type="required-race">Requires Dwarf, Vulpera</tt-line>'
            '<tt-line data-line-type="required-level">Requires Level 60</tt-line>'
            'Chastise the target, causing 272 to 306 Holy damage.')


def test_parse_tooltip_fields():
    t = parse_tooltip(BLOOD_FURY)
    assert t["name"] == "Blood Fury" and t["subtitle"] == "Racial"
    assert t["cast"] == "Instant" and t["cooldown"] == "3 min"
    assert t["description"] == "Increases Attack Power and Spell Power by 10% for 15 sec."
    assert t["aura"] == "Attack Power and Spell Power increased by 10%."
    assert t["required_race"].startswith("Orc")
    c = parse_tooltip(CHASTISE)
    assert (c["cost"], c["range"], c["required_level"], c["classes"]) == ("225 Mana", "20 yd range", "60", ["priest"])


def test_select_variants_keeps_player_versions_and_highest_rank():
    tips = [
        {"spell_id": 1, "subtitle": "Rank 1", "classes": ["priest"], "required_race": "Dwarf"},
        {"spell_id": 5, "subtitle": "Rank 5", "classes": ["priest"], "required_race": "Dwarf"},
        {"spell_id": 9, "subtitle": "", "classes": [], "required_race": "Highland Drake"},   # montaria/NPC
        {"spell_id": 7, "subtitle": "Racial", "classes": [], "required_race": None},          # sem raça
    ]
    assert [v["spell_id"] for v in select_variants(tips)] == [5]


def test_pick_variant_by_class():
    vs = [{"spell_id": 1, "classes": ["mage"]}, {"spell_id": 2, "classes": ["priest"]}]
    assert pick_variant(vs, "priest")["spell_id"] == 2
    assert pick_variant(vs, "paladin")["spell_id"] == 1
    assert pick_variant([], "mage") is None


def test_seed_has_official_english_text(conn):
    live = live_map(conn)
    assert len(live) == 49
    eureka = pick_variant(live["eureka"]["variants"], "mage")
    assert eureka["classes"] == ["mage"] and "Mana cost reduced by 10%" in eureka["description"]
    assert pick_variant(live["will_to_survive"]["variants"], None)["cooldown"] == "3 min"


class FakeDB:
    """Simula o Warcraft DB: Blood Fury foi 'patchado' para 12%; Shatter Curse ganhou variante nova."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def __call__(self, url: str):
        self.calls += 1
        if self.fail:
            raise urllib.error.URLError("offline")
        if "/search?q=" in url:
            q = url.split("q=")[1].replace("%20", " ").replace("%21", "!").replace("%27", "'")
            if q == "Blood Fury":
                return [{"id": 20572, "type": "spell", "name": "Blood Fury"}, {"id": 1, "type": "item", "name": "Blood Fury"}]
            if q == "Shatter Curse":
                return [{"id": 1299026, "type": "spell", "name": "Shatter Curse"},
                        {"id": 1299999, "type": "spell", "name": "Shatter Curse"}]
            return []  # resto: usa IDs conhecidos
        sid = int(url.rsplit("/", 1)[1])
        if sid == 20572:
            return {"html": BLOOD_FURY.replace("10%", "12%"), "name": "Blood Fury"}
        if sid == 1299999:
            return {"html": BLOOD_FURY.replace("Blood Fury", "Shatter Curse")
                    .replace("Requires Orc", "Requires Priest</tt-line><tt-line data-line-type=\"required-race\">Requires Orc")
                    .replace('<tt-line data-line-type="required-race">Requires Priest</tt-line>',
                             '<tt-line data-line-type="required-class">Requires Priest</tt-line>'),
                    "name": "Shatter Curse"}
        from wow_planner.live.warcraftdb import load_seed
        for rid, d in load_seed()["racials"].items():
            for v in d["variants"]:
                if v["spell_id"] == sid:
                    return {"html": _to_html(v), "name": v["name"]}
        raise urllib.error.HTTPError(url, 404, "nf", None, None)


def _to_html(v):
    parts = [f"<tt-double-line><tt-title>{v['name']}</tt-title><tt-subtitle>{v.get('subtitle') or ''}</tt-subtitle></tt-double-line>"]
    if v.get("cost") or v.get("range"):
        parts.append(f"<tt-double-line><span>{v.get('cost') or ''}</span><span>{v.get('range') or ''}</span></tt-double-line>")
    cd = f"<span>{v['cooldown']} cooldown</span>" if v.get("cooldown") else ""
    parts.append(f"<tt-double-line><span>{v.get('cast') or ''}</span>{cd}</tt-double-line>")
    if v.get("classes"):
        parts.append(f'<tt-line data-line-type="required-class">Requires {", ".join(c.title() for c in v["classes"])}</tt-line>')
    race = "Skyborne Racial" if v["name"] in ("Walk on Air", "Read Leyline", "Skysight", "Wind Blessed", "Elemental Insight") else "Orc"
    kind = "required-skill" if race == "Skyborne Racial" else "required-race"
    parts.append(f'<tt-line data-line-type="{kind}">Requires {race}</tt-line>')
    if v.get("required_level"):
        parts.append(f'<tt-line data-line-type="required-level">Requires Level {v["required_level"]}</tt-line>')
    parts.append(v.get("description") or "")
    if v.get("aura"):
        parts.append(f'<tt-line data-line-type="spell-aura">\nAura</tt-line>{v["aura"]}')
    return "".join(parts)


def test_update_detects_patch_changes_and_new_variants(conn, bundle):
    rep = update_from_web(conn, bundle, fetch=FakeDB(), workers=2)
    assert not rep.blocked
    fields = {(c["racial_id"], c["field"]) for c in rep.changes}
    assert ("blood_fury", "description") in fields
    assert ("shatter_curse", "variant") in fields
    assert rep.new_variants >= 1
    bf = pick_variant(live_map(conn)["blood_fury"]["variants"], "mage")
    assert "12%" in bf["description"]
    assert recent_changes(conn)
    # segunda rodada: nada novo
    rep2 = update_from_web(conn, bundle, fetch=FakeDB(), workers=2)
    assert rep2.changes == [] and rep2.unchanged > 0


def test_update_offline_keeps_saved_data(conn, bundle):
    rep = update_from_web(conn, bundle, fetch=FakeDB(fail=True), workers=2)
    assert rep.blocked and rep.errors
    assert pick_variant(live_map(conn)["blood_fury"]["variants"], None)["description"].endswith("10% for 15 sec.")


def test_ingest_from_browser(conn):
    rep = ingest_tooltips(conn, {"blood_fury": [{"spell_id": 20572, "html": BLOOD_FURY.replace("15 sec", "20 sec")}]})
    assert rep.method == "browser" and rep.updated == 1
    assert "20 sec" in live_map(conn)["blood_fury"]["variants"][0]["description"]
