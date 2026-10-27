from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Faction(StrEnum):
    HORDE = "horde"
    ALLIANCE = "alliance"


class Goal(StrEnum):
    PVP = "pvp"
    PVE = "pve"
    COLLECTION = "collection"


class CharacterStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"


STATUS_LABELS_PT = {
    CharacterStatus.PLANNED: "planejado",
    CharacterStatus.IN_PROGRESS: "em progresso",
    CharacterStatus.DONE: "concluído",
}


class Character(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=48)
    faction: Faction
    race: str
    class_name: str
    spec: str | None = None
    level: int = Field(default=1, ge=1, le=60)  # Forever mantém o cap em 60
    realm: str | None = None
    goal: Goal = Goal.PVP
    status: CharacterStatus = CharacterStatus.PLANNED
    priority: int = Field(default=3, ge=1, le=5)  # 1 = mais alta
    creation_order: int = Field(default=99, ge=1)
    notes: str | None = None
    created_at: datetime | None = None

    model_config = {"populate_by_name": True}
