import json
import urllib.request

import pytest
from typer.testing import CliRunner

from wow_planner.cli.main import app
from wow_planner.services import ProfessionError, account_plan, load_professions, recommend_professions
from wow_planner.services.profession_service import validate

from .test_web import base_url  # noqa: F401  (fixture)


def test_data_is_valid_and_sourced():
    data = load_professions()
    assert validate(data) == []
    assert data["rules"]["primary_slots"] == 2
    for p in data["professions"]:
        assert p["refs"] and all(r["last_verified"] for r in p["refs"])


def test_bags_and_elixirs_gives_tailoring_alchemy():
    r = recommend_professions(["bags", "elixirs"], "mage")
    assert set(r["pairs"][0]["ids"]) == {"tailoring", "alchemy"}
    assert r["pairs"][0]["uncovered"] == []
    assert any("Herbalism" in c for c in r["pairs"][0]["caveats"])
    assert r["account_plan"] is None


def test_portuguese_aliases():
    r = recommend_professions(["bolsa", "elixir"])
    assert [l["id"] for l in r["likes"]] == ["bags", "elixirs"]


def test_healing_potions_are_first_aid_and_dont_use_a_slot():
    r = recommend_professions(["healing_potions", "bags"], "priest")
    assert "tailoring" in r["pairs"][0]["ids"]
    fa = next(s for s in r["secondary"] if s["id"] == "first_aid")
    assert "Poções de cura" in fa["why"]


def test_too_many_likes_splits_across_account():
    r = recommend_professions(["bags", "elixirs", "enchants", "gadgets_pvp"], "mage")
    assert r["account_plan"]
    plan = account_plan(["bags", "elixirs", "enchants", "gadgets_pvp"])
    all_profs = {p for a in plan for p in a["professions"]}
    assert {"Tailoring", "Alchemy", "Enchanting", "Engineering"} <= all_profs
    assert all(len(a["professions"]) <= 2 for a in plan)
    paladin = next(a for a in plan if a["class_id"] == "paladin")
    assert "Tailoring" not in paladin["professions"]


def test_paladin_weapons_prefers_blacksmithing_mining():
    r = recommend_professions(["weapons"], "paladin")
    assert set(r["pairs"][0]["ids"]) == {"blacksmithing", "mining"}


def test_unknown_interest_errors():
    with pytest.raises(ProfessionError):
        recommend_professions(["dragons"])


def test_cli_professions(tmp_path, monkeypatch):
    monkeypatch.setenv("WOW_PLAN_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("COLUMNS", "160")
    r = CliRunner().invoke(app, ["professions", "--likes", "bags,elixirs", "--class", "mage"])
    assert r.exit_code == 0, r.output
    assert "Alchemy + Tailoring" in r.output or "Tailoring + Alchemy" in r.output
    r = CliRunner().invoke(app, ["professions", "--likes", "xyz"])
    assert r.exit_code == 1


def test_api_professions(base_url):  # noqa: F811
    with urllib.request.urlopen(base_url + "/api/professions") as resp:
        data = json.load(resp)
    assert len(data["professions"]) >= 12 and data["interests"]
    with urllib.request.urlopen(base_url + "/api/professions/recommend?likes=bags,elixirs&class=mage&goal=") as resp:
        rec = json.load(resp)
    assert set(rec["pairs"][0]["ids"]) == {"tailoring", "alchemy"}
