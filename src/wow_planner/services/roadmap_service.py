"""Gera o roadmap de mains e alts a partir do game data + personagens cadastrados."""

from __future__ import annotations

from dataclasses import dataclass, field

from wow_planner.models.character import Character
from wow_planner.models.game import GameDataBundle, RoadmapStep
from wow_planner.services.recommendation_service import RecommendationError, recommend

MODES = ("pvp", "pve", "collection")


@dataclass
class FactionPlan:
    faction: str
    best_by_mode: dict[str, list[str]]           # modo -> nomes de raças em ordem
    key_racials: list[tuple[str, str, str]]      # (nome (raça), efeito PT, racial_id) do modo principal
    starting_spec: str | None
    professions: list[str]
    notes: list[str]


@dataclass
class RoadmapEntry:
    step: RoadmapStep
    plans: list[FactionPlan]                     # 1ª = facção principal da etapa
    risks: list[str]
    existing: list[Character] = field(default_factory=list)

    # atalhos para a facção principal
    @property
    def best_by_mode(self) -> dict[str, list[str]]:
        return self.plans[0].best_by_mode

    @property
    def starting_spec(self) -> str | None:
        return self.plans[0].starting_spec


def _plan_for(bundle: GameDataBundle, class_id: str, faction: str, main_mode: str) -> FactionPlan:
    best: dict[str, list[str]] = {}
    main = None
    for mode in MODES:
        try:
            res = recommend(bundle, class_id, faction, mode)
        except RecommendationError:
            continue
        best[mode] = [p.race_name + (" ($)" if p.requires_purchase else "") for p in res.picks]
        if mode == main_mode:
            main = res
    key_racials: list[tuple[str, str, str]] = []
    if main:
        seen: set[str] = set()
        for p in main.picks[:2]:
            for r in p.key_racials:
                if r.id not in seen:
                    seen.add(r.id)
                    key_racials.append((f"{r.name} ({p.race_name})", r.effect, r.id))
    return FactionPlan(
        faction=faction,
        best_by_mode=best,
        key_racials=key_racials,
        starting_spec=main.rec.starting_spec if main else None,
        professions=main.rec.professions if main else [],
        notes=main.excluded_notes if main else [],
    )


def build_roadmap(bundle: GameDataBundle, characters: list[Character] | None = None) -> list[RoadmapEntry]:
    characters = characters or []
    entries = []
    for step in sorted(bundle.roadmap, key=lambda s: s.order):
        factions = [step.faction]
        if step.compare_factions:
            factions.append("alliance" if step.faction == "horde" else "horde")
        plans = [_plan_for(bundle, step.class_id, f, step.mode) for f in factions]

        risks = list(step.risks)
        for note in plans[0].notes:  # restrições (ex.: Skyborne) que a etapa ainda não citou
            if not any("Skyborne" in r for r in risks) or "Skyborne" not in note:
                risks.append(note)

        classes_existing = [c for c in characters if c.class_name == step.class_id]
        existing = [c for c in classes_existing if c.faction.value in factions]
        entries.append(RoadmapEntry(step=step, plans=plans, risks=risks, existing=existing))
    return entries
