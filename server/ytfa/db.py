"""SQLite connection + schema.

One file, no ORM (ADR-4). `get_connection()` opens a connection to
`config.db_path` and ensures the schema exists (all DDL below is
`IF NOT EXISTS`, so re-running it on every connect is cheap and safe).

Schema is copied verbatim from docs/03-API명세.md §1.6 — keep them in sync.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ytfa.config import Config, load_config

SCHEMA_SQL = """
-- 채널 --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS channels (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    thumbnail_url       TEXT NOT NULL DEFAULT '',
    uploads_playlist_id TEXT NOT NULL DEFAULT '',
    subscriber_count    INTEGER,
    subscribed          INTEGER NOT NULL DEFAULT 1,
    category_locked     INTEGER NOT NULL DEFAULT 0,
    needs_review        INTEGER NOT NULL DEFAULT 0,
    weight              REAL    NOT NULL DEFAULT 1.0,
    added_at            TEXT NOT NULL,
    last_polled_at      TEXT
);

CREATE TABLE IF NOT EXISTS categories (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    ord        INTEGER NOT NULL DEFAULT 0,
    color      TEXT,
    is_default INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS channel_categories (
    channel_id  TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    category_id TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    assigned_by TEXT NOT NULL DEFAULT 'auto',   -- auto | user
    confidence  REAL,
    PRIMARY KEY (channel_id, category_id)
);

-- 영상 --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS videos (
    id                TEXT PRIMARY KEY,
    channel_id        TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    published_at      TEXT NOT NULL,
    duration_sec      INTEGER,
    view_count        INTEGER,
    thumbnail_url     TEXT NOT NULL DEFAULT '',
    kind              TEXT NOT NULL DEFAULT 'video',
    discovered_at     TEXT NOT NULL,
    meta_enriched     INTEGER NOT NULL DEFAULT 0,
    transcript_status TEXT NOT NULL DEFAULT 'pending',
    summary           TEXT,
    verdict           TEXT,                      -- JSON
    summarized_at     TEXT,
    indexed_level     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_videos_channel   ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_kind      ON videos(kind);
CREATE INDEX IF NOT EXISTS idx_videos_indexed   ON videos(indexed_level);

CREATE TABLE IF NOT EXISTS video_states (
    video_id      TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    state         TEXT NOT NULL DEFAULT 'new',
    watch_seconds INTEGER,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_states_state ON video_states(state);

-- 자막 --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transcripts (
    video_id   TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    lang       TEXT NOT NULL,
    is_auto    INTEGER NOT NULL DEFAULT 1,      -- 자동 생성 자막 여부
    segments   TEXT NOT NULL,                   -- JSON [{start, dur, text}]
    fetched_at TEXT NOT NULL
);

-- 검색 --------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
    id UNINDEXED, title, description, summary, channel_title,
    content='videos', content_rowid='rowid'
);

CREATE TABLE IF NOT EXISTS chunks (
    id         TEXT PRIMARY KEY,                -- "{video_id}:{seq}"
    video_id   TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,                   -- summary | transcript
    start_sec  INTEGER,
    end_sec    INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_video ON chunks(video_id);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id   TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model_name TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,                   -- float32
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emb_model ON embeddings(model_name);

-- 메모리·실행 이력 ----------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,                 -- preference | fact
    content      TEXT NOT NULL,
    source       TEXT,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    use_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,            -- run_id (트레이스 파일명과 동일)
    kind           TEXT NOT NULL,               -- chat | poll | summarize | brief | index | eval
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    user_input     TEXT,
    summary        TEXT,
    steps          INTEGER DEFAULT 0,
    input_tokens   INTEGER DEFAULT 0,
    output_tokens  INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cost_usd       REAL DEFAULT 0,
    stopped_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


@contextmanager
def get_connection(cfg: Config | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection to the app database, ensuring the schema exists.

    Usage:
        from ytfa.db import get_connection

        with get_connection() as conn:
            conn.execute("SELECT * FROM channels")
    """
    cfg = cfg or load_config()
    conn = _connect(cfg.db_path)
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()
