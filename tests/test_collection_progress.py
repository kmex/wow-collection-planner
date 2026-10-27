import json
from pathlib import Path

import pytest

from wow_planner.game_data import load_collection_seed
from wow_planner.models.collection import CollectionItem
from wow_planner.repositories import ItemFilter
from wow_planner.services import CollectionService


@pytest.fixture()
def svc(coll_repo):
    return CollectionService(coll_repo)


@pytest.fixture()
def populated(svc, coll_repo):
    items = [
        CollectionItem(external_id="m1", name="Montaria Horda", category="mounts", faction="horde", priority=1),
        CollectionItem(external_id="m2", name="Montaria Aliança", category="mounts", faction="alliance"),
        CollectionItem(external_id="m3", name="Montaria Neutra", category="mounts"),
        CollectionItem(external_id="t1", name="Título de Mage", category="titles", class_restriction="mage"),
        CollectionItem(external_id="t2", name="Título de Priest", category="titles", class_restriction="priest"),
        CollectionItem(external_id="s1", name="Item Sazonal", category="seasonal", seasonal=True),
    ]
    ids = {}
    for it in items:
        ids[it.external_id], _ = coll_repo.upsert_item(it)
    coll_repo.set_status(ids["m1"], "collected")
    coll_repo.set_status(ids["m3"], "in_progress", progress=40)
    coll_repo.set_status(ids["s1"], "blocked", note="evento fora de época")
    coll_repo.commit()
    return ids


def test_account_progress(svc, populated):
    overall, cats = svc.progress()
    assert overall.total == 6
    assert overall.collected == 1
    assert overall.blocked == 1
    assert overall.obtainable == 5
    assert overall.missing == 4
    assert overall.percent == pytest.approx(20.0)
    mounts = next(c for c in cats if c.scope == "mounts")
    assert (mounts.total, mounts.collected, mounts.in_progress) == (3, 1, 1)


def test_character_progress_blocks_by_faction_and_class(svc, populated, horde_mage):
    overall, _ = svc.progress(horde_mage)
    # bloqueados: montaria da Aliança, título de Priest e o sazonal
    assert overall.blocked == 3
    assert overall.collected == 1
    assert overall.percent == pytest.approx(100 / 3)


def test_character_status_overrides_account(svc, populated, coll_repo, horde_mage):
    coll_repo.set_status(populated["t1"], "collected", horde_mage.id)
    coll_repo.commit()
    assert svc.progress(horde_mage)[0].collected == 2
    assert svc.progress()[0].collected == 1  # conta não muda


def test_missing_filters(svc, populated, horde_mage):
    rows = svc.missing(ItemFilter(category="mounts", faction="horde"))
    assert {r["external_id"] for r in rows} == {"m3"}  # m1 obtida, m2 é da Aliança
    rows = svc.missing(ItemFilter(category="mounts"), horde_mage)
    assert {r["external_id"] for r in rows} == {"m3"}  # m2 bloqueada para personagem Horda
    assert svc.missing(ItemFilter(max_priority=1)) == []
    rows = svc.missing(ItemFilter(class_name="mage", category="titles"))
    assert {r["external_id"] for r in rows} == {"t1"}


def test_restriction_alerts(svc, populated, horde_mage):
    alerts = svc.restriction_alerts([horde_mage])
    assert any("Montaria Aliança" in a for a in alerts)
    assert any("Título de Priest" in a for a in alerts)
    assert not any("Título de Mage" in a for a in alerts)


def test_export_import_roundtrip(svc, populated, coll_repo, tmp_path: Path, conn):
    out_csv = tmp_path / "c.csv"
    out_json = tmp_path / "c.json"
    assert svc.export(out_csv, "csv") == 6
    assert svc.export(out_json, "json") == 6
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert {i["status"] for i in data["items"]} == {"collected", "missing", "in_progress", "blocked"}

    from wow_planner.repositories import CollectionRepository, connect, migrate
    other = connect(":memory:")
    migrate(other)
    svc2 = CollectionService(CollectionRepository(other))
    seen, new, warnings = svc2.import_file(out_csv)
    assert (seen, new, warnings) == (6, 6, [])
    assert svc2.progress()[0].model_dump() == svc.progress()[0].model_dump()


def test_import_tolerates_bad_rows(svc, tmp_path: Path):
    f = tmp_path / "bad.json"
    f.write_text(json.dumps([
        {"external_id": "x1", "name": "Ok", "category": "pets", "status": "obtido"},
        {"name": "sem id", "category": "pets"},
        {"external_id": "x2", "name": "Status ruim", "category": "pets", "status": "talvez"},
    ]), encoding="utf-8")
    seen, new, warnings = svc.import_file(f)
    assert seen == 1 and len(warnings) == 2


def test_seed_is_idempotent_and_sourced(svc):
    seed = load_collection_seed()
    assert svc.seed(seed) == len(seed)
    assert svc.seed(seed) == 0
    for raw in seed:
        assert raw["data_source"].startswith("https://") and raw["last_verified"]
