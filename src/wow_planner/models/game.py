"""Modelos do banco de conhecimento (game_data). Todo registro carrega fonte e data."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    source: str = Field(description="URL ou referência de onde o dado veio")
    last_verified: str = Field(description="AAAA-MM-DD da última verificação")
    confidence: Literal["official", "guide", "community", "analysis", "unverified"] = "guide"
    patch: str | None = None


class Racial(BaseModel):
    id: str
    name: str
    kind: Literal["active", "passive"]
    cooldown: str | None = None      # None = não verificado
    effect: str
    pvp_value: int = Field(ge=0, le=3, description="0-3, avaliação do app")
    pve_value: int = Field(ge=0, le=3)
    classes: list[str] | None = None  # None = todas; ex.: racial só de priest
    notes: str | None = None
    ref: SourceRef


class Race(BaseModel):
    id: str
    name: str
    faction: Literal["horde", "alliance"]
    base_race: str | None = None      # ex.: skyborne para as duas variantes
    classes: list[str]
    new_class_combos: list[str] = []
    racials: list[str]
    starting_zone: str | None = None
    reputation: str | None = None
    mount_family: str | None = None
    requires_purchase: bool = False
    notes: str | None = None
    ref: SourceRef


class ClassInfo(BaseModel):
    id: str
    name: str
    specs: list[str]
    roles: list[str]
    ref: SourceRef


class Recommendation(BaseModel):
    race: str
    rank: int
    reason: str
    key_racials: list[str] = []


class RecommendationSet(BaseModel):
    class_id: str
    faction: Literal["horde", "alliance"]
    mode: Literal["pvp", "pve", "collection"]
    picks: list[Recommendation]
    starting_spec: str | None = None
    professions: list[str] = []
    notes: str | None = None
    ref: SourceRef


class RoadmapStep(BaseModel):
    order: int
    title: str
    class_id: str
    faction: Literal["horde", "alliance"]
    mode: Literal["pvp", "pve", "collection"]
    candidate_races: list[str]
    condition: str | None = None
    compare_factions: bool = False  # mostra recomendações das duas facções
    risks: list[str] = []
    ref: SourceRef


class GameDataBundle(BaseModel):
    version: str
    game: str
    races: list[Race]
    racials: list[Racial]
    classes: list[ClassInfo]
    recommendations: list[RecommendationSet]
    roadmap: list[RoadmapStep]
    general_grades: dict[str, dict[str, str]] = {}  # classe -> raça -> nota
    general_grades_ref: SourceRef | None = None

    def race(self, race_id: str) -> Race:
        for r in self.races:
            if r.id == race_id:
                return r
        raise KeyError(f"Raça desconhecida: {race_id}")

    def racial(self, racial_id: str) -> Racial:
        for r in self.racials:
            if r.id == racial_id:
                return r
        raise KeyError(f"Racial desconhecida: {racial_id}")

    def races_for(self, class_id: str, faction: str) -> list[Race]:
        return [r for r in self.races if r.faction == faction and class_id in r.classes]
