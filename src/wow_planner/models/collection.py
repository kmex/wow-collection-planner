from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

CATEGORIES = [
    "mounts", "pets", "toys", "transmogs", "appearances", "achievements",
    "meta_achievements", "titles", "reputations", "reputation_mounts",
    "recipes", "seasonal", "other",
]


class ItemStatus(StrEnum):
    MISSING = "missing"          # não obtido
    COLLECTED = "collected"      # obtido
    IN_PROGRESS = "in_progress"  # em progresso
    BLOCKED = "blocked"          # bloqueado por facção/classe/temporada


STATUS_LABELS_PT = {
    ItemStatus.MISSING: "não obtido",
    ItemStatus.COLLECTED: "obtido",
    ItemStatus.IN_PROGRESS: "em progresso",
    ItemStatus.BLOCKED: "bloqueado",
}


class CollectionItem(BaseModel):
    id: int | None = None
    external_source: str = "manual"          # manual | att | seed | csv | json
    external_id: str                          # id no jogo/ATT ou slug manual
    name: str
    category: str
    expansion: str = "forever"
    source: str | None = None                 # onde se obtém (drop, vendor, rep...)
    faction: str | None = None                # horde | alliance | None (ambas)
    class_restriction: str | None = None
    race_restriction: str | None = None
    difficulty: str | None = None
    priority: int = Field(default=3, ge=1, le=5)
    seasonal: bool = False
    account_wide: bool | None = None          # None = não verificado no Forever
    data_source: str | None = None            # URL/referência do dado
    last_verified: str | None = None          # AAAA-MM-DD


class CollectionStatus(BaseModel):
    item_id: int
    character_id: int | None = None           # None = status de conta
    status: ItemStatus = ItemStatus.MISSING
    progress: float | None = Field(default=None, ge=0, le=100)
    note: str | None = None


class ProgressRow(BaseModel):
    scope: str
    total: int
    collected: int
    in_progress: int
    blocked: int

    @property
    def obtainable(self) -> int:
        return self.total - self.blocked

    @property
    def missing(self) -> int:
        return self.obtainable - self.collected

    @property
    def percent(self) -> float:
        return 100.0 * self.collected / self.obtainable if self.obtainable else 0.0
