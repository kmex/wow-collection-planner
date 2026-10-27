"""Carrega e valida o banco de conhecimento versionado (YAML/JSON).

Ordem de precedência: arquivos em `override_dir` (WOW_PLAN_DATA_DIR) substituem os embutidos
de mesmo nome. Assim `wow-plan update-game-data --from pasta/` atualiza sem tocar no código.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from wow_planner.models.game import GameDataBundle

BUILTIN_DIR = Path(__file__).parent / "data"
DATA_FILES = ["races.yaml", "racials.yaml", "recommendations.yaml", "roadmap.yaml", "collection_seed.yaml"]


class GameDataError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise GameDataError(f"{path}: conteúdo raiz deve ser um mapa")
    data.pop("_refs", None)  # âncoras YAML auxiliares
    return data


def _resolve(name: str, override_dir: Path | None) -> Path:
    if override_dir:
        for candidate in (override_dir / name, override_dir / name.replace(".yaml", ".json")):
            if candidate.is_file():
                return candidate
    return BUILTIN_DIR / name


def load_raw(override_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    return {name: _read(_resolve(name, override_dir)) for name in DATA_FILES}


def load_game_data(override_dir: Path | None = None) -> GameDataBundle:
    raw = load_raw(override_dir)
    races = raw["races.yaml"]
    try:
        bundle = GameDataBundle(
            version=str(races.get("version", "unknown")),
            game=str(races.get("game", "")),
            races=races["races"],
            classes=races.get("classes", []),
            general_grades=races.get("general_grades", {}),
            general_grades_ref=races.get("general_grades_ref"),
            racials=raw["racials.yaml"]["racials"],
            recommendations=raw["recommendations.yaml"]["recommendations"],
            roadmap=raw["roadmap.yaml"]["roadmap"],
        )
    except Exception as exc:  # pydantic.ValidationError, KeyError
        raise GameDataError(f"game data inválido: {exc}") from exc
    problems = validate_bundle(bundle)
    if problems:
        raise GameDataError("inconsistências no game data:\n- " + "\n- ".join(problems))
    return bundle


def load_collection_seed(override_dir: Path | None = None) -> list[dict[str, Any]]:
    return list(load_raw(override_dir)["collection_seed.yaml"].get("items", []))


def validate_bundle(bundle: GameDataBundle) -> list[str]:
    """Checagens de integridade referencial (raça x classe x facção, racials existentes)."""
    problems: list[str] = []
    racial_ids = {r.id for r in bundle.racials}
    race_ids = {r.id for r in bundle.races}
    for race in bundle.races:
        for rid in race.racials:
            if rid not in racial_ids:
                problems.append(f"raça {race.id}: racial desconhecida '{rid}'")
    for rec in bundle.recommendations:
        for pick in rec.picks:
            if pick.race not in race_ids:
                problems.append(f"recomendação {rec.class_id}/{rec.faction}: raça desconhecida '{pick.race}'")
                continue
            race = bundle.race(pick.race)
            if race.faction != rec.faction:
                problems.append(f"{rec.class_id}/{rec.faction}/{rec.mode}: {race.id} é da facção {race.faction}")
            if rec.class_id not in race.classes:
                problems.append(f"{rec.class_id}/{rec.faction}/{rec.mode}: {race.id} não pode ser {rec.class_id}")
            for rid in pick.key_racials:
                if rid not in race.racials:
                    problems.append(f"{rec.class_id}: racial '{rid}' não pertence a {race.id}")
    for step in bundle.roadmap:
        for rid in step.candidate_races:
            if rid not in race_ids:
                problems.append(f"roadmap {step.order}: raça desconhecida '{rid}'")
            elif step.class_id not in bundle.race(rid).classes or bundle.race(rid).faction != step.faction:
                problems.append(f"roadmap {step.order}: {rid} inválido para {step.class_id}/{step.faction}")
    return problems


def stale_records(bundle: GameDataBundle, max_age_days: int, today: date | None = None) -> list[str]:
    """Lista registros cujo last_verified é mais antigo que max_age_days."""
    today = today or date.today()
    out: list[str] = []

    def check(label: str, last: str) -> None:
        try:
            age = (today - date.fromisoformat(last)).days
        except ValueError:
            out.append(f"{label}: last_verified inválido '{last}'")
            return
        if age > max_age_days:
            out.append(f"{label}: verificado há {age} dias ({last})")

    for r in bundle.races:
        check(f"raça {r.id}", r.ref.last_verified)
    for r in bundle.racials:
        check(f"racial {r.id}", r.ref.last_verified)
    for rec in bundle.recommendations:
        check(f"recomendação {rec.class_id}/{rec.faction}/{rec.mode}", rec.ref.last_verified)
    return out
