from pathlib import Path

import pytest

from wow_planner.game_data import load_game_data
from wow_planner.models.character import Character, Faction, Goal
from wow_planner.repositories import CharacterRepository, CollectionRepository, connect, migrate

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def bundle():
    return load_game_data()


@pytest.fixture()
def conn():
    c = connect(":memory:")
    migrate(c)
    yield c
    c.close()


@pytest.fixture()
def char_repo(conn):
    return CharacterRepository(conn)


@pytest.fixture()
def coll_repo(conn):
    return CollectionRepository(conn)


@pytest.fixture()
def horde_mage(char_repo):
    return char_repo.add(Character(name="Zarnok", faction=Faction.HORDE, race="undead", class_name="mage",
                                   goal=Goal.PVP, creation_order=1))


@pytest.fixture()
def alliance_mage(char_repo):
    return char_repo.add(Character(name="Aelyn", faction=Faction.ALLIANCE, race="skyborne_high_order",
                                   class_name="mage", goal=Goal.PVE, creation_order=2))
