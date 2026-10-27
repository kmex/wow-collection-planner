from wow_planner.importers.lua_table import get_path, parse_savedvariables

from .conftest import FIXTURES


def test_parses_basic_types():
    res = parse_savedvariables(
        'X = {\n ["a"] = 1, ["b"] = -2.5, ["c"] = "q\\"uote", [10] = true, ["n"] = nil, nested = { 1, 2, 3 },\n}\nY = 0x10\n'
    )
    assert res.warnings == []
    assert res.globals["X"]["a"] == 1
    assert res.globals["X"]["b"] == -2.5
    assert res.globals["X"]["c"] == 'q"uote'
    assert res.globals["X"][10] is True
    assert res.globals["X"]["nested"] == [1, 2, 3]
    assert res.globals["Y"] == 16


def test_tolerates_broken_assignment():
    res = parse_savedvariables((FIXTURES / "att" / "AllTheThings.lua").read_text(encoding="utf-8"))
    assert "ATTBroken" not in res.globals
    assert res.globals["ATTAfterBroken"] == {"ok": True}  # continua depois do erro
    assert any("ATTBroken" in w for w in res.warnings)
    assert res.globals["ATTAccountWideData"]["Note"] == "texto\nlongo"


def test_get_path_with_numeric_keys():
    data = {"A": {"B": {42: "x"}}}
    assert get_path(data, ["A", "B", "42"]) == "x"
    assert get_path(data, ["A", "missing"]) is None


def test_comments_are_ignored():
    res = parse_savedvariables("-- comentário\nZ = { -- inline\n [1] = 5, --[[ bloco ]] [2] = 6 }\n")
    # chaves explícitas continuam dict (evita confundir tabela {id=valor} com lista de ids)
    assert res.globals["Z"] == {1: 5, 2: 6}
