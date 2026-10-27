import pytest

from wow_planner.services import RecommendationError, build_roadmap, decision_table, recommend


def test_mage_horde_pvp_top_picks(bundle):
    res = recommend(bundle, "mage", "horde", "pvp")
    races = [p.race_id for p in res.picks]
    assert races[0] == "undead"
    assert set(races) == {"undead", "orc", "troll"}
    assert any(r.id == "will_of_the_forsaken" for r in res.picks[0].key_racials)


def test_aliases_in_portuguese(bundle):
    res = recommend(bundle, "mago", "horda", "pvp")
    assert res.rec.class_id == "mage" and res.rec.faction == "horde"


def test_horde_mage_warns_about_skyborne(bundle):
    res = recommend(bundle, "mage", "horde", "pve")
    assert any("Windshaper" in n and "High Order" in n for n in res.excluded_notes)
    assert all(p.race_id != "skyborne_high_order" for p in res.picks)


def test_alliance_mage_includes_skyborne_and_purchase_flag(bundle):
    res = recommend(bundle, "mage", "alliance", "collection")
    top = res.picks[0]
    assert top.race_id == "skyborne_high_order"
    assert top.requires_purchase is True


def test_no_paladin_for_troll_horde_only_undead(bundle):
    res = recommend(bundle, "paladin", "horde", "pvp")
    assert [p.race_id for p in res.picks] == ["undead"]


def test_invalid_class_faction_raises(bundle):
    with pytest.raises(RecommendationError):
        recommend(bundle, "shaman", "alliance", "pvp")  # sem recomendação cadastrada


def test_recommendations_have_spec_and_professions(bundle):
    for cls in ("mage", "priest", "paladin"):
        for faction in ("horde", "alliance"):
            for mode in ("pvp", "pve", "collection"):
                res = recommend(bundle, cls, faction, mode)
                assert res.rec.starting_spec
                assert res.rec.professions
                assert res.rec.ref.confidence == "analysis"


def test_decision_table_shape(bundle):
    rows = decision_table(bundle)
    assert [r["Classe"] for r in rows] == ["Mage", "Priest", "Paladin"]
    paladin = rows[2]
    assert paladin["Melhor Horda PvP"] == "Undead"
    assert "Human" in paladin["Melhor Aliança PvE"]


def test_roadmap_order_and_content(bundle, horde_mage):
    entries = build_roadmap(bundle, [horde_mage])
    titles = [(e.step.class_id, e.step.faction) for e in entries]
    assert titles == [("mage", "horde"), ("mage", "alliance"), ("priest", "horde"), ("paladin", "horde")]
    first = entries[0]
    assert first.best_by_mode["pvp"][0] == "Undead"
    assert first.existing and first.existing[0].name == "Zarnok"
    assert any("Skyborne" in r for r in first.risks)
    second = entries[1]
    assert any("($)" in name for name in second.best_by_mode["pve"])
    # Priest e Paladin comparam as duas facções
    assert [p.faction for p in entries[2].plans] == ["horde", "alliance"]
    assert [p.faction for p in entries[3].plans] == ["horde", "alliance"]
