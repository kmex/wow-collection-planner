-- 001: schema inicial do wow-collection-planner

CREATE TABLE IF NOT EXISTS characters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    faction         TEXT NOT NULL CHECK (faction IN ('horde','alliance')),
    race            TEXT NOT NULL,
    class_name      TEXT NOT NULL,
    spec            TEXT,
    level           INTEGER NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 60),
    realm           TEXT,
    goal            TEXT NOT NULL DEFAULT 'pvp' CHECK (goal IN ('pvp','pve','collection')),
    status          TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','in_progress','done')),
    priority        INTEGER NOT NULL DEFAULT 3,
    creation_order  INTEGER NOT NULL DEFAULT 99,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name, realm)
);

CREATE TABLE IF NOT EXISTS collection_items (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    external_source    TEXT NOT NULL DEFAULT 'manual',
    external_id        TEXT NOT NULL,
    name               TEXT NOT NULL,
    category           TEXT NOT NULL,
    expansion          TEXT NOT NULL DEFAULT 'forever',
    source             TEXT,
    faction            TEXT CHECK (faction IN ('horde','alliance') OR faction IS NULL),
    class_restriction  TEXT,
    race_restriction   TEXT,
    difficulty         TEXT,
    priority           INTEGER NOT NULL DEFAULT 3,
    seasonal           INTEGER NOT NULL DEFAULT 0,
    account_wide       INTEGER,
    data_source        TEXT,
    last_verified      TEXT,
    UNIQUE (external_source, category, external_id)
);

-- character_id = 0 representa o status "de conta"
CREATE TABLE IF NOT EXISTS collection_status (
    item_id       INTEGER NOT NULL REFERENCES collection_items(id) ON DELETE CASCADE,
    character_id  INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'missing' CHECK (status IN ('missing','collected','in_progress','blocked')),
    progress      REAL,
    note          TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (item_id, character_id)
);

CREATE TABLE IF NOT EXISTS game_data_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    version     TEXT NOT NULL,
    origin      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    loaded_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS import_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    file_path   TEXT,
    items_seen  INTEGER NOT NULL DEFAULT 0,
    items_new   INTEGER NOT NULL DEFAULT 0,
    warnings    TEXT,
    ran_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_category ON collection_items(category);
CREATE INDEX IF NOT EXISTS idx_status_char ON collection_status(character_id);
