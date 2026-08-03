-- Independent schema derived from the reference research.sqlite structure.
-- This file is a design contract; it does not copy any existing database.

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,
    source_sha256 TEXT NOT NULL,
    source_type TEXT NOT NULL,
    legacy_import INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    project TEXT NOT NULL,
    workstream TEXT,
    summary TEXT NOT NULL,
    source_path TEXT,
    source_heading TEXT,
    source_sha256 TEXT,
    line_start INTEGER,
    line_end INTEGER,
    legacy_import INTEGER NOT NULL DEFAULT 0,
    requires_human_review INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL
);

CREATE TABLE relations (
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    target TEXT NOT NULL,
    PRIMARY KEY (event_id, relation_type, target)
);

CREATE TABLE artifacts (
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    sha256 TEXT,
    role TEXT,
    PRIMARY KEY (event_id, path)
);

CREATE INDEX events_date_idx ON events(date);
CREATE INDEX events_status_idx ON events(status);
CREATE INDEX events_type_idx ON events(event_type);
CREATE INDEX relations_target_idx ON relations(target);

CREATE VIRTUAL TABLE event_fts USING fts5(
    event_id UNINDEXED,
    summary,
    source_heading,
    source_path,
    tokenize = 'unicode61'
);
