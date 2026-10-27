"""Recomendações de raça por classe/facção/modo e tabela de decisão."""

from __future__ import annotations

from dataclasses import dataclass, field

from wow_planner.models.game import GameDataBundle, Racial, RecommendationSet

CLASS_ALIASES = {"mago": "mage", "sacerdote": "priest", "paladino": "paladin"}
FACTION_ALIASES = {"horda": "horde", "alianca": "alliance", "aliança": "alliance"}
MODE_ALIASES = {"colecionismo": "collection", "estetica": "collection", "estética": "collection"}


def normalize(value: str, aliases: dict[str, str]) -> str:
    v = value.strip().lower()
    return aliases.get(v, v)


class RecommendationError(ValueError):
    pass


@dataclass
class EnrichedPick:
    race_id: str
    race_name: str
    rank: int
    reason: str
    general_grade: str | None
    key_racials: list[Racial] = field(default_factory=list)
    requires_purchase: bool = False


@dataclass
class RecommendationResult:
    rec: RecommendationSet
    picks: list[EnrichedPick]
    excluded_notes: list[str]


def recommend(bundle: GameDataBundle, class_id: str, faction: str, mode: str) -> RecommendationResult:
    class_id = normalize(class_id, CLASS_ALIASES)
    faction = normalize(faction, FACTION_ALIASES)
    mode = normalize(mode, MODE_ALIASES)
    if not bundle.races_for(class_id, faction):
        raise RecommendationError(f"Nenhuma raça da facção '{faction}' pode jogar de '{class_id}'.")
    rec = next(
        (r for r in bundle.recommendations if r.class_id == class_id and r.faction == faction and r.mode == mode),
        None,
    )
    if rec is None:
        raise RecommendationError(f"Sem recomendação cadastrada para {class_id}/{faction}/{mode}.")

    grades = bundle.general_grades.get(class_id, {})
    picks = []
    for p in sorted(rec.picks, key=lambda x: x.rank):
        race = bundle.race(p.race)
        picks.append(EnrichedPick(
            race_id=race.id, race_name=race.name, rank=p.rank, reason=p.reason,
            general_grade=grades.get(race.id),
            key_racials=[bundle.racial(r) for r in p.key_racials],
            requires_purchase=race.requires_purchase,
        ))
    return RecommendationResult(rec=rec, picks=picks, excluded_notes=_exclusion_notes(bundle, class_id, faction))


def _exclusion_notes(bundle: GameDataBundle, class_id: str, faction: str) -> list[str]:
    """Explica restrições relevantes, ex.: Skyborne da Horda não pode ser Mage."""
    notes = []
    for race in bundle.races:
        if race.base_race and race.faction == faction and class_id not in race.classes:
            twins = [r for r in bundle.races if r.base_race == race.base_race and r.faction != faction
                     and class_id in r.classes]
            if twins:
                notes.append(
                    f"{race.name} ({faction}) NÃO pode ser {class_id}; "
                    f"essa combinação só existe como {twins[0].name} ({twins[0].faction})."
                )
    return notes


def decision_table(bundle: GameDataBundle, classes: tuple[str, ...] = ("mage", "priest", "paladin")) -> list[dict[str, str]]:
    """Linhas: Classe | Melhor Horda PvP | Melhor Horda PvE | Melhor Aliança PvP | Melhor Aliança PvE | Observações."""
    rows = []
    for cid in classes:
        row = {"Classe": cid.capitalize()}
        obs = []
        for faction, flabel in (("horde", "Horda"), ("alliance", "Aliança")):
            for mode, mlabel in (("pvp", "PvP"), ("pve", "PvE")):
                try:
                    res = recommend(bundle, cid, faction, mode)
                    top = res.picks[0]
                    alt = f" (alt: {res.picks[1].race_name})" if len(res.picks) > 1 else ""
                    row[f"Melhor {flabel} {mlabel}"] = f"{top.race_name}{alt}"
                except RecommendationError:
                    row[f"Melhor {flabel} {mlabel}"] = "—"
            for rec in bundle.recommendations:
                if rec.class_id == cid and rec.faction == faction and rec.mode in ("pvp", "pve") and rec.notes:
                    if rec.notes not in obs:
                        obs.append(rec.notes)
        row["Observações"] = " ".join(obs)
        rows.append(row)
    return rows


def decision_table_markdown(bundle: GameDataBundle) -> str:
    rows = decision_table(bundle)
    headers = list(rows[0].keys())
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(r[h] for h in headers) + " |" for r in rows]
    return "\n".join(lines)
