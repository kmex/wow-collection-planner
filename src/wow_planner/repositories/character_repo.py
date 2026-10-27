from __future__ import annotations

import sqlite3

from wow_planner.models.character import Character

_FIELDS = ["name", "faction", "race", "class_name", "spec", "level", "realm", "goal",
           "status", "priority", "creation_order", "notes"]


class CharacterRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, ch: Character) -> Character:
        values = [getattr(ch, f) for f in _FIELDS]
        values = [v.value if hasattr(v, "value") else v for v in values]
        cur = self.conn.execute(
            f"INSERT INTO characters ({', '.join(_FIELDS)}) VALUES ({', '.join('?' * len(_FIELDS))})",
            values,
        )
        self.conn.commit()
        return self.get(cur.lastrowid)  # type: ignore[arg-type]

    def get(self, char_id: int) -> Character | None:
        row = self.conn.execute("SELECT * FROM characters WHERE id = ?", (char_id,)).fetchone()
        return Character(**dict(row)) if row else None

    def find_by_name(self, name: str) -> Character | None:
        row = self.conn.execute("SELECT * FROM characters WHERE lower(name) = lower(?)", (name,)).fetchone()
        return Character(**dict(row)) if row else None

    def list(self) -> list[Character]:
        rows = self.conn.execute("SELECT * FROM characters ORDER BY creation_order, priority, id").fetchall()
        return [Character(**dict(r)) for r in rows]

    def update_status(self, char_id: int, status: str, level: int | None = None) -> None:
        if level is not None:
            self.conn.execute("UPDATE characters SET status = ?, level = ? WHERE id = ?", (status, level, char_id))
        else:
            self.conn.execute("UPDATE characters SET status = ? WHERE id = ?", (status, char_id))
        self.conn.commit()

    def delete(self, char_id: int) -> None:
        self.conn.execute("DELETE FROM characters WHERE id = ?", (char_id,))
        self.conn.execute("DELETE FROM collection_status WHERE character_id = ?", (char_id,))
        self.conn.commit()
