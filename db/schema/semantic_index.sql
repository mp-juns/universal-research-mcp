-- Independent schema derived from the reference semantic.sqlite structure.
-- Vector values are serialized float32 blobs and remain derived data.

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE embeddings (
    event_id TEXT PRIMARY KEY,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    retrieval_text_sha256 TEXT NOT NULL
);

CREATE TABLE passage_embeddings (
    passage_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_heading TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    retrieval_text_sha256 TEXT NOT NULL
);

CREATE INDEX passage_event_idx ON passage_embeddings(event_id);
