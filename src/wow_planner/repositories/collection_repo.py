from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from wow_planner.models.collection import CollectionItem

ACCOUNT = 0  # character_id usado para status de conta

_ITEM_FIELDS = ["external_source", "external_id", "name", "category", "expansion", "source", "faction",
                "class_restriction", "race_restriction", "difficulty", "priority", "seasonal",
                "account_wide", "data_source", "last_verified"]


@dataclass
class ItemFilter:
    category: str | None = None
    expansion: str | None = None
    source: str | None = None
    faction: str | None = None          # inclui itens sem facção (ambas)
    class_name: str | None = None       # inclui itens sem restrição de classe
    difficulty: str | None = None
    max_priority: int | None = None     # 1 = só os mais prioritários
    character_id: int = ACCOUNT
    status: list[str] | None = None


class CollectionRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- itens ----------
    def upsert_item(self, item: CollectionItem) -> tuple[int, bool]:
        """Insere ou atualiza; retorna (id, criado?)."""
        row = self.conn.execute(
            "SELECT id FROM collection_items WHERE external_source=? AND category=? AND external_id=?",
            (item.external_source, item.category, item.external_id),
        ).fetchone()
        values = [getattr(item, f) for f in _ITEM_FIELDS]
        if row:
            sets = ", ".join(f"{f} = ?" for f in _ITEM_FIELDS)
            self.conn.execute(f"UPDATE collection_items SET {sets} WHERE id = ?", (*values, row["id"]))
            return row["id"], False
        cur = self.conn.execute(
            f"INSERT INTO collection_items ({', '.join(_ITEM_FIELDS)}) VALUES ({', '.join('?' * len(_ITEM_FIELDS))})",
            values,
        )
        return cur.lastrowid, True  # type: ignore[return-value]

    def set_status(self, item_id: int, status: str, character_id: int = ACCOUNT,
                   progress: float | None = None, note: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO collection_status(item_id, character_id, status, progress, note)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(item_id, character_id) DO UPDATE SET
                 status=excluded.status, progress=excluded.progress,
                 note=COALESCE(excluded.note, collection_status.note), updated_at=datetime('now')""",
            (item_id, character_id, status, progress, note),
        )

    def commit(self) -> None:
        self.conn.commit()

    def find_item(self, query: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM collection_items WHERE external_id = ? OR lower(name) LIKE lower(?) ORDER BY id",
            (query, f"%{query}%"),
        ).fetchall()

    def items_with_status(self, flt: ItemFilter | None = None) -> list[sqlite3.Row]:
        """Itens + status efetivo. Status do personagem tem precedência; senão usa o de conta."""
        flt = flt or ItemFilter()
        sql = [
            """SELECT i.*,
                      COALESCE(cs.status, acc.status, 'missing') AS status,
                      COALESCE(cs.progress, acc.progress) AS progress
               FROM collection_items i
               LEFT JOIN collection_status acc ON acc.item_id = i.id AND acc.character_id = 0
               LEFT JOIN collection_status cs ON cs.item_id = i.id AND cs.character_id = ? AND ? <> 0
               WHERE 1=1"""
        ]
        params: list = [flt.character_id, flt.character_id]
        if flt.category:
            sql.append("AND i.category = ?"); params.append(flt.category)
        if flt.expansion:
            sql.append("AND i.expansion = ?"); params.append(flt.expansion)
        if flt.source:
            sql.append("AND lower(i.source) LIKE lower(?)"); params.append(f"%{flt.source}%")
        if flt.faction:
            sql.append("AND (i.faction IS NULL OR i.faction = ?)"); params.append(flt.faction)
        if flt.class_name:
            sql.append("AND (i.class_restriction IS NULL OR i.class_restriction = ?)"); params.append(flt.class_name)
        if flt.difficulty:
            sql.append("AND i.difficulty = ?"); params.append(flt.difficulty)
        if flt.max_priority:
            sql.append("AND i.priority <= ?"); params.append(flt.max_priority)
        query = "\n".join(sql)
        if flt.status:
            query = f"SELECT * FROM ({query}) WHERE status IN ({', '.join('?' * len(flt.status))})"
            params.extend(flt.status)
        query += " ORDER BY priority, category, name"
        return self.conn.execute(query, params).fetchall()

    def categories(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT category FROM collection_items ORDER BY 1")]

    def log_import(self, source: str, file_path: str | None, seen: int, new: int, warnings: list[str]) -> None:
        self.conn.execute(
            "INSERT INTO import_runs(source, file_path, items_seen, items_new, warnings) VALUES (?,?,?,?,?)",
            (source, file_path, seen, new, "\n".join(warnings) or None),
        )
        self.conn.commit()
