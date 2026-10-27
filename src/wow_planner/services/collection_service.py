"""Progresso, faltantes, alertas e import/export CSV/JSON da coleção."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from wow_planner.models.character import Character
from wow_planner.models.collection import CATEGORIES, CollectionItem, ItemStatus, ProgressRow
from wow_planner.repositories.collection_repo import ACCOUNT, CollectionRepository, ItemFilter

EXPORT_COLUMNS = ["external_source", "external_id", "name", "category", "expansion", "source", "faction",
                  "class_restriction", "race_restriction", "difficulty", "priority", "seasonal",
                  "account_wide", "data_source", "last_verified", "status", "progress"]

_TRUE = {"1", "true", "yes", "sim", "y", "s", "x"}


def effective_status(row: sqlite3.Row | dict, character: Character | None) -> str:
    """Status final, aplicando bloqueios automáticos por facção/classe/raça do personagem."""
    status = row["status"]
    if character is not None and status != ItemStatus.COLLECTED:
        if row["faction"] and row["faction"] != character.faction.value:
            return ItemStatus.BLOCKED
        if row["class_restriction"] and row["class_restriction"] != character.class_name:
            return ItemStatus.BLOCKED
        if row["race_restriction"] and row["race_restriction"] != character.race:
            return ItemStatus.BLOCKED
    return status


class CollectionService:
    def __init__(self, repo: CollectionRepository):
        self.repo = repo

    # ---------------- progresso ----------------
    def _rows(self, character: Character | None, flt: ItemFilter | None = None) -> list[dict[str, Any]]:
        flt = flt or ItemFilter()
        flt.character_id = character.id if character else ACCOUNT
        out = []
        for r in self.repo.items_with_status(flt):
            d = dict(r)
            d["status"] = str(effective_status(r, character))
            out.append(d)
        return out

    @staticmethod
    def _summarize(scope: str, rows: Iterable[dict[str, Any]]) -> ProgressRow:
        rows = list(rows)
        return ProgressRow(
            scope=scope,
            total=len(rows),
            collected=sum(r["status"] == ItemStatus.COLLECTED for r in rows),
            in_progress=sum(r["status"] == ItemStatus.IN_PROGRESS for r in rows),
            blocked=sum(r["status"] == ItemStatus.BLOCKED for r in rows),
        )

    def progress(self, character: Character | None = None) -> tuple[ProgressRow, list[ProgressRow]]:
        rows = self._rows(character)
        by_cat: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_cat[r["category"]].append(r)
        overall = self._summarize("geral" if character is None else character.name, rows)
        cats = [self._summarize(c, by_cat[c]) for c in sorted(by_cat, key=_cat_order)]
        return overall, cats

    def progress_by_character(self, characters: list[Character]) -> list[ProgressRow]:
        return [self._summarize(c.name, self._rows(c)) for c in characters]

    def missing(self, flt: ItemFilter, character: Character | None = None) -> list[dict[str, Any]]:
        """Itens que faltam para 100% (exclui obtidos e bloqueados)."""
        return [r for r in self._rows(character, flt)
                if r["status"] in (ItemStatus.MISSING, ItemStatus.IN_PROGRESS)]

    def restriction_alerts(self, characters: list[Character]) -> list[str]:
        """Itens que só certa raça/classe/facção obtém e que nenhum personagem planejado cobre."""
        alerts = []
        rows = self._rows(None)
        for r in rows:
            if r["status"] == ItemStatus.COLLECTED:
                continue
            needs = {k: r[k] for k in ("faction", "class_restriction", "race_restriction") if r[k]}
            if not needs:
                continue
            ok = any(
                (not r["faction"] or c.faction.value == r["faction"])
                and (not r["class_restriction"] or c.class_name == r["class_restriction"])
                and (not r["race_restriction"] or c.race == r["race_restriction"])
                for c in characters
            )
            if not ok:
                fac_pt = {"horde": "Horda", "alliance": "Aliança"}
                parts = []
                if r["faction"]:
                    parts.append(f"facção {fac_pt.get(r['faction'], r['faction'])}")
                if r["class_restriction"]:
                    parts.append(f"classe {r['class_restriction']}")
                if r["race_restriction"]:
                    parts.append(f"raça {r['race_restriction']}")
                alerts.append(f"{r['name']} ({r['category']}): exige {' + '.join(parts)} e nenhum personagem seu atende.")
        return alerts

    # ---------------- seed ----------------
    def seed(self, items: list[dict[str, Any]]) -> int:
        new = 0
        for raw in items:
            item = CollectionItem(external_source="seed", **raw)
            _, created = self.repo.upsert_item(item)
            new += created
        self.repo.commit()
        return new

    # ---------------- export ----------------
    def export(self, path: Path, fmt: str, character: Character | None = None) -> int:
        rows = [{k: r.get(k) for k in EXPORT_COLUMNS} for r in self._rows(character)]
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            path.write_text(json.dumps({"items": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        elif fmt == "csv":
            with path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=EXPORT_COLUMNS)
                w.writeheader()
                w.writerows(rows)
        else:
            raise ValueError("formato deve ser csv ou json")
        return len(rows)

    # ---------------- import manual ----------------
    def import_file(self, path: Path, character: Character | None = None,
                    default_source: str | None = None) -> tuple[int, int, list[str]]:
        """Importa CSV/JSON no formato do export. Linhas inválidas viram avisos, não erros fatais."""
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            records = data["items"] if isinstance(data, dict) else data
        else:
            with path.open(newline="", encoding="utf-8-sig") as fh:
                records = list(csv.DictReader(fh))
        src = default_source or path.suffix.lower().lstrip(".")
        return self.import_records(records, src, character)

    def import_records(self, records: list[dict[str, Any]], source: str,
                       character: Character | None = None) -> tuple[int, int, list[str]]:
        seen = new = 0
        warnings: list[str] = []
        char_id = character.id if character else ACCOUNT
        for i, rec in enumerate(records, start=1):
            try:
                item = _record_to_item(rec, source)
                status = _norm_status(rec.get("status"))
                progress = rec.get("progress")
                progress = float(progress) if progress not in (None, "") else None
            except Exception as exc:  # noqa: BLE001 - tolerante a falhas
                warnings.append(f"linha {i}: ignorada ({exc})")
                continue
            item_id, created = self.repo.upsert_item(item)
            seen += 1
            new += created
            if status:
                self.repo.set_status(item_id, status, char_id, progress)
        self.repo.commit()
        return seen, new, warnings

    def set_item_status(self, query: str, status: str, character: Character | None = None,
                        progress: float | None = None) -> list[str]:
        rows = self.repo.find_item(query)
        for r in rows:
            self.repo.set_status(r["id"], status, character.id if character else ACCOUNT, progress)
        self.repo.commit()
        return [r["name"] for r in rows]


def _cat_order(cat: str) -> tuple[int, str]:
    return (CATEGORIES.index(cat) if cat in CATEGORIES else len(CATEGORIES), cat)


_STATUS_ALIASES = {
    "missing": "missing", "não obtido": "missing", "nao obtido": "missing", "0": "missing", "false": "missing",
    "collected": "collected", "obtido": "collected", "1": "collected", "true": "collected",
    "in_progress": "in_progress", "em progresso": "in_progress",
    "blocked": "blocked", "bloqueado": "blocked",
}


def _norm_status(value: Any) -> str | None:
    if value is None or value == "":
        return None
    v = str(value).strip().lower()
    if v not in _STATUS_ALIASES:
        raise ValueError(f"status desconhecido '{value}'")
    return _STATUS_ALIASES[v]


def _record_to_item(rec: dict[str, Any], source: str) -> CollectionItem:
    def opt(key: str) -> Any:
        val = rec.get(key)
        return None if val in ("", None) else val

    if not opt("external_id") or not opt("name") or not opt("category"):
        raise ValueError("external_id, name e category são obrigatórios")
    aw = opt("account_wide")
    return CollectionItem(
        external_source=opt("external_source") or source,
        external_id=str(rec["external_id"]),
        name=str(rec["name"]),
        category=str(rec["category"]).strip().lower(),
        expansion=opt("expansion") or "forever",
        source=opt("source"),
        faction=(str(opt("faction")).lower() if opt("faction") else None),
        class_restriction=opt("class_restriction"),
        race_restriction=opt("race_restriction"),
        difficulty=opt("difficulty"),
        priority=int(opt("priority") or 3),
        seasonal=str(opt("seasonal") or "").lower() in _TRUE or rec.get("seasonal") is True,
        account_wide=None if aw is None else (str(aw).lower() in _TRUE or aw is True),
        data_source=opt("data_source"),
        last_verified=opt("last_verified"),
    )
