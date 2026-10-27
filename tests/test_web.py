import json
import threading
import urllib.error
import urllib.request

import pytest

from wow_planner.config import Settings
from wow_planner.web.server import make_server

from .conftest import FIXTURES


@pytest.fixture()
def base_url(tmp_path, monkeypatch, request):
    monkeypatch.setenv("WOW_PLAN_DB", str(tmp_path / "w.db"))
    monkeypatch.setenv("WOW_PLAN_DATA_DIR", str(tmp_path / "gd"))
    monkeypatch.setenv("WOW_PATH", str(tmp_path / "sem_wow"))
    att = FIXTURES / "att" / "AllTheThings.lua" if request.node.get_closest_marker("with_att") else ""
    monkeypatch.setenv("ATT_SAVEDVARIABLES_PATH", str(att))
    httpd = make_server(Settings.load(), port=18765)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _call(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_index_and_meta(base_url):
    status, body = _call(base_url + "/")
    assert status == 200 and b"Planejador Forever" in body
    status, body = _call(base_url + "/api/meta")
    meta = json.loads(body)
    assert status == 200 and any(r["id"] == "skyborne_high_order" for r in meta["races"])


def test_roadmap_recommend_decision(base_url):
    roadmap = json.loads(_call(base_url + "/api/roadmap")[1])
    assert [s["order"] for s in roadmap] == [1, 2, 3, 4]
    rec = json.loads(_call(base_url + "/api/recommend?class=mage&faction=horde&mode=pvp")[1])
    assert rec["picks"][0]["race_id"] == "undead"
    table = json.loads(_call(base_url + "/api/decision-table")[1])
    assert table[0]["Classe"] == "Mage"


def test_character_crud_and_validation(base_url):
    s, b = _call(base_url + "/api/characters", "POST",
                 {"name": "Nope", "faction": "horde", "race": "skyborne_windshaper", "class_name": "mage"})
    assert s == 400 and "High Order" in json.loads(b)["error"]
    s, b = _call(base_url + "/api/characters", "POST",
                 {"name": "Zarnok", "faction": "horde", "race": "undead", "class_name": "mage", "goal": "pvp"})
    assert s == 200
    cid = json.loads(b)["id"]
    s, b = _call(base_url + f"/api/characters/{cid}", "POST", {"status": "in_progress", "level": 20})
    assert s == 200 and json.loads(b)["level"] == 20
    s, _ = _call(base_url + f"/api/characters/{cid}", "DELETE")
    assert s == 200
    assert json.loads(_call(base_url + "/api/characters")[1]) == []


@pytest.mark.with_att
def test_collection_endpoints(base_url):
    s, b = _call(base_url + "/api/collection/import-att", "POST")
    assert s == 200 and json.loads(b)["items_seen"] > 0
    prog = json.loads(_call(base_url + "/api/collection/progress")[1])
    assert prog["overall"]["collected"] > 0
    items = json.loads(_call(base_url + "/api/collection/items?status=all")[1])
    s, b = _call(base_url + "/api/collection/status", "POST", {"item_id": items[0]["id"], "status": "collected"})
    assert s == 200
    s, b = _call(base_url + "/api/collection/export?format=csv")
    assert s == 200 and b.startswith(b"external_source")


def test_import_without_att_file_gives_guidance(base_url):
    s, b = _call(base_url + "/api/collection/import-att", "POST")
    assert s == 404 and "/reload" in json.loads(b)["error"]


def test_racials_endpoints(base_url):
    data = json.loads(_call(base_url + "/api/racials?class=mage")[1])
    eureka = next(r for r in data["items"] if r["id"] == "eureka")
    assert eureka["en"]["classes"] == ["mage"] and eureka["en"]["url"].endswith("/1259817")
    assert all(r["class_only"] in (None, []) or "mage" in r["class_only"] for r in data["items"])
    ids = json.loads(_call(base_url + "/api/racials/spell-ids")[1])
    assert 20572 in ids["blood_fury"]
    html = "<tt-double-line><tt-title>Blood Fury</tt-title></tt-double-line><tt-line data-line-type=\"required-race\">Requires Orc</tt-line>Patched text."
    s, b = _call(base_url + "/api/racials/ingest", "POST", {"items": {"blood_fury": [{"spell_id": 20572, "html": html}]}})
    assert s == 200 and json.loads(b)["updated"] == 1
    rec = json.loads(_call(base_url + "/api/recommend?class=mage&faction=horde&mode=pvp")[1])
    orc = next(p for p in rec["picks"] if p["race_id"] == "orc")
    assert any(r["en"] and r["en"]["description"] == "Patched text." for r in orc["racials"])


def test_racials_show_blizzard_notice(base_url):
    data = json.loads(_call(base_url + "/api/racials?class=mage")[1])
    bf = next(r for r in data["items"] if r["id"] == "blood_fury")
    assert bf["en"]["notice"]["tooltip_status"] == "outdated"
    assert bf["en"]["notice"]["change"]["new"] == "2 min"
    assert bf["notes"][0]["posted_at"].startswith("2026-10-27")
    ap = next(r for r in data["items"] if r["id"] == "artisans_precision")
    assert ap["notes"] and ap["en"] is None
    assert set(data["sources"]) == {"warcraftdb", "blizzard", "wowhead"}
    assert data["news"][0]["published_at"].startswith("2026-10-27")
