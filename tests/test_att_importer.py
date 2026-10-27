from pathlib import Path

from wow_planner.importers import ATTImporter, load_mapping
from wow_planner.repositories import ItemFilter

from .conftest import FIXTURES

ATT = FIXTURES / "att"


def _status_map(coll_repo, character_id=0):
    return {(r["category"], r["external_id"]): r["status"]
            for r in coll_repo.items_with_status(ItemFilter(character_id=character_id))}


def test_savedvariables_import(coll_repo, horde_mage):
    report = ATTImporter(coll_repo).import_savedvariables(ATT / "AllTheThings.lua", [horde_mage])
    st = _status_map(coll_repo)
    assert st[("achievements", "1001")] == "collected"
    assert st[("achievements", "1003")] == "missing"          # valor 0 = não obtido
    assert st[("titles", "42")] == "collected"
    assert st[("toys", "5001")] == "collected"
    assert st[("toys", "5002")] == "missing"
    assert st[("reputations", "76")] == "collected"            # lista sequencial de ids
    assert report.characters_matched == ["Zarnok"]
    # dados de personagem só valem para ele
    char_st = _status_map(coll_repo, horde_mage.id)
    assert char_st[("reputations", "68")] == "collected"
    assert st[("reputations", "68")] == "missing"
    # personagem desconhecido não cria status, mas é reportado
    assert ("reputations", "999") not in st
    assert report.characters_unmatched == ["Desconhecido (Orc Warrior Forever)"]
    # valor 2 (obtido por outro personagem) e 3 (conquista) contam como obtido
    assert st[("mounts", "580")] == "collected"
    assert st[("achievements", "1002")] == "collected"
    # nomes vindos do ATT são usados quando existem
    names = {r["external_id"]: r["name"] for r in coll_repo.items_with_status()}
    assert names["5001"] == 'Brinquedo "Simulado" de Teste'
    assert names["1001"] == "achievements #1001"


def test_savedvariables_import_is_idempotent(coll_repo, horde_mage):
    imp = ATTImporter(coll_repo)
    first = imp.import_savedvariables(ATT / "AllTheThings.lua", [horde_mage])
    second = imp.import_savedvariables(ATT / "AllTheThings.lua", [horde_mage])
    assert first.items_new > 0 and second.items_new == 0
    assert second.items_seen == first.items_seen


def test_missing_paths_and_broken_tables_are_warnings(coll_repo):
    report = ATTImporter(coll_repo).import_savedvariables(ATT / "AllTheThings.lua", [])
    assert any("Sources" in w for w in report.warnings)
    assert any("ATTBroken" in w for w in report.warnings)
    assert not any("NÃO verificado" in w for w in report.warnings)  # mapeamento padrão é verificado (ATT 5.4.5)


def test_missing_file_does_not_crash(coll_repo, tmp_path: Path):
    report = ATTImporter(coll_repo).import_savedvariables(tmp_path / "nao_existe.lua", [])
    assert report.items_seen == 0 and report.warnings


def test_custom_mapping(coll_repo, tmp_path: Path):
    mapping = tmp_path / "map.yaml"
    mapping.write_text(
        "verified: false\nrules:\n  - {path: ATTAccountWideData.Achievements, category: meta_achievements, scope: account}\n",
        encoding="utf-8",
    )
    report = ATTImporter(coll_repo, load_mapping(mapping)).import_savedvariables(ATT / "AllTheThings.lua", [])
    assert report.items_seen == 3
    assert any("NÃO verificado" in w for w in report.warnings)
    assert {r["category"] for r in coll_repo.items_with_status()} == {"meta_achievements"}


def test_manual_csv_import_with_column_mapping(coll_repo):
    report = ATTImporter(coll_repo).import_manual(ATT / "manual_export.csv")
    st = _status_map(coll_repo)
    assert st[("mounts", "9001")] == "collected"
    assert st[("mounts", "9002")] == "missing"
    assert st[("pets", "9003")] == "collected"                # 'battlepet' -> pets, 'sim' -> obtido
    assert st[("other", "9004")] == "missing"                 # categoria desconhecida -> other
    assert report.items_seen == 4
    assert any("registro 4" in w for w in report.warnings)    # linha sem id


def test_manual_json_import(coll_repo, alliance_mage):
    report = ATTImporter(coll_repo).import_manual(ATT / "manual_export.json", alliance_mage)
    assert report.items_seen == 2
    st = _status_map(coll_repo, alliance_mage.id)
    assert st[("transmogs", "7001")] == "collected"
    rows = {r["external_id"]: r for r in coll_repo.items_with_status()}
    assert rows["7002"]["class_restriction"] == "mage"
    assert any("registro 3" in w for w in report.warnings)
