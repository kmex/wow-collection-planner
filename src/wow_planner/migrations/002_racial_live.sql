-- 002: cache das racials buscadas ao vivo no Warcraft DB (texto oficial em inglês)

CREATE TABLE IF NOT EXISTS racial_live (
    racial_id      TEXT PRIMARY KEY,
    variants_json  TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    method         TEXT NOT NULL            -- web | browser
);

CREATE TABLE IF NOT EXISTS racial_live_changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    racial_id    TEXT NOT NULL,
    spell_id     INTEGER,
    field        TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT,
    detected_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS racial_live_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    method        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL,
    updated       INTEGER NOT NULL DEFAULT 0,
    unchanged     INTEGER NOT NULL DEFAULT 0,
    new_variants  INTEGER NOT NULL DEFAULT 0,
    errors        TEXT
);
