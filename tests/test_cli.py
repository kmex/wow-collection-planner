from typer.testing import CliRunner

from wow_planner.cli.main import app

from .conftest import FIXTURES

runner = CliRunner()


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("WOW_PLAN_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("WOW_PLAN_DATA_DIR", str(tmp_path / "gd"))
    monkeypatch.setenv("ATT_SAVEDVARIABLES_PATH", "")
    monkeypatch.setenv("COLUMNS", "160")


def test_full_flow(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    assert runner.invoke(app, ["init"]).exit_code == 0

    r = runner.invoke(app, ["character", "add", "--name", "Zarnok", "--faction", "horde", "--race", "undead",
                            "--class", "mage", "--order", "1"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["character", "add", "--name", "Nope", "--faction", "horde",
                            "--race", "skyborne_windshaper", "--class", "mage"])
    assert r.exit_code == 1 and "High Order" in r.output

    r = runner.invoke(app, ["roadmap"])
    assert r.exit_code == 0
    for text in ("1. Main: Mage Horda", "2. Mage High Order Skyborne", "3. Alt: Priest", "4. Alt: Paladin", "Zarnok"):
        assert text in r.output

    r = runner.invoke(app, ["recommend", "--class", "mage", "--faction", "horde", "--mode", "pvp"])
    assert r.exit_code == 0 and "Undead" in r.output

    r = runner.invoke(app, ["collection", "import", "--source", "att", "--file", str(FIXTURES / "att" / "AllTheThings.lua")])
    assert r.exit_code == 0 and "personagens casados: Zarnok" in r.output

    for args in (["collection", "status"], ["collection", "missing", "--category", "mounts", "--faction", "horde"],
                 ["collection", "alerts"], ["decision-table"], ["update-game-data"]):
        res = runner.invoke(app, args)
        assert res.exit_code == 0, (args, res.output)

    out = tmp_path / "x.csv"
    r = runner.invoke(app, ["collection", "export", "--format", "csv", "--out", str(out)])
    assert r.exit_code == 0 and out.read_text(encoding="utf-8").startswith("external_source")


def test_att_import_without_file_explains_config(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    # garante que um .env real do usuário não aponte para o WoW instalado durante o teste
    monkeypatch.setenv("WOW_PATH", str(tmp_path / "sem_wow"))
    r = runner.invoke(app, ["collection", "import", "--source", "att"])
    assert r.exit_code == 1 and "ATT_SAVEDVARIABLES_PATH" in r.output


def test_resolve_att_file_falls_back_to_forever_folder(tmp_path, monkeypatch):
    from wow_planner.config import Settings
    sv = tmp_path / "_forever_" / "WTF" / "Account" / "MINHACONTA" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "AllTheThings.lua").write_text("X = {}\n", encoding="utf-8")
    monkeypatch.setenv("WOW_PATH", str(tmp_path))
    monkeypatch.setenv("WOW_FLAVOR", "_classic_beta_")  # .env antigo
    monkeypatch.setenv("WOW_ACCOUNT", "")
    monkeypatch.setenv("ATT_SAVEDVARIABLES_PATH", "")
    assert Settings.load().resolve_att_file() == sv / "AllTheThings.lua"
