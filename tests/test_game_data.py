from datetime import date
from pathlib import Path

import pytest
import yaml

from wow_planner.game_data import GameDataError, load_game_data, validate_bundle
from wow_planner.game_data.loader import BUILTIN_DIR, stale_records
from wow_planner.services.game_data_service import update_from


def test_builtin_data_is_consistent(bundle):
    assert validate_bundle(bundle) == []
    assert {r.id for r in bundle.races} >= {"orc", "undead", "troll", "gnome", "human", "skyborne_high_order"}


def test_every_record_has_source_and_date(bundle):
    for item in [*bundle.races, *bundle.racials, *bundle.recommendations, *bundle.roadmap]:
        assert item.ref.source, item
        date.fromisoformat(item.ref.last_verified)


def test_skyborne_mage_is_alliance_only(bundle):
    mage_races = {r.id for r in bundle.races if "mage" in r.classes}
    assert "skyborne_high_order" in mage_races
    assert "skyborne_windshaper" not in mage_races
    assert bundle.race("skyborne_high_order").faction == "alliance"
    assert "shaman" in bundle.race("skyborne_windshaper").classes


def test_requested_race_options_exist(bundle):
    assert {r.id for r in bundle.races_for("mage", "horde")} == {"orc", "undead", "troll"}
    assert {r.id for r in bundle.races_for("mage", "alliance")} == {"skyborne_high_order", "gnome", "human"}
    assert {r.id for r in bundle.races_for("paladin", "horde")} == {"undead"}
    assert {r.id for r in bundle.races_for("priest", "horde")} == {"undead", "troll"}


def test_stale_records_detects_old_dates(bundle):
    assert stale_records(bundle, 30, today=date(2026, 10, 27)) == []
    assert len(stale_records(bundle, 30, today=date(2027, 6, 1))) > 0


def test_update_rejects_invalid_data(tmp_path: Path):
    bad = yaml.safe_load((BUILTIN_DIR / "recommendations.yaml").read_text(encoding="utf-8"))
    bad.pop("_refs", None)
    bad["recommendations"][0]["picks"][0]["race"] = "skyborne_windshaper"  # Mage Skyborne na Horda: inválido
    src = tmp_path / "src"
    src.mkdir()
    (src / "recommendations.yaml").write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    target = tmp_path / "data"
    with pytest.raises(GameDataError):
        update_from(src, target)
    assert not target.exists()  # nada foi gravado


def test_update_accepts_valid_override(tmp_path: Path):
    data = yaml.safe_load((BUILTIN_DIR / "races.yaml").read_text(encoding="utf-8"))
    data.pop("_refs", None)
    data["version"] = "2026-11-10"
    src = tmp_path / "races.json"
    import json
    src.write_text(json.dumps(data), encoding="utf-8")
    target = tmp_path / "data"
    bundle, written = update_from(src, target)
    assert written == ["races.json"]
    assert load_game_data(target).version == "2026-11-10" == bundle.version
