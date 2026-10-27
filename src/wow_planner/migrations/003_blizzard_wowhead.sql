-- 003: notas oficiais da Blizzard, notícias e tooltips do Wowhead, histórico de execuções por fonte

CREATE TABLE IF NOT EXISTS blizzard_notes (
    id             TEXT PRIMARY KEY,          -- hash(racial + texto)
    racial_id      TEXT NOT NULL,
    region         TEXT NOT NULL,             -- us | eu
    topic_id       INTEGER NOT NULL,
    topic_title    TEXT,
    post_number    INTEGER NOT NULL,
    author         TEXT,
    posted_at      TEXT,                      -- created_at do post (UTC)
    edited_at      TEXT,                      -- updated_at do post (UTC)
    url            TEXT NOT NULL,
    text           TEXT NOT NULL,             -- linha original em inglês
    first_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bnotes_racial ON blizzard_notes(racial_id);

CREATE TABLE IF NOT EXISTS wowhead_news (
    link           TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    published_at   TEXT,
    summary        TEXT,
    racial_ids     TEXT,
    lines_json     TEXT,
    first_seen_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wowhead_tooltips (
    spell_id     INTEGER PRIMARY KEY,
    racial_id    TEXT,
    name         TEXT,
    cast         TEXT,
    cooldown     TEXT,
    description  TEXT,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_source_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,               -- warcraftdb | blizzard | wowhead
    started_at   TEXT NOT NULL,
    finished_at  TEXT NOT NULL,
    ok           INTEGER NOT NULL,
    items        INTEGER NOT NULL DEFAULT 0,
    new_items    INTEGER NOT NULL DEFAULT 0,
    errors       TEXT
);
