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
-- 독립 FTS5 테이블이다(external content가 아니다) — channel_title이
-- channels를 조인해야 나오는 값이라 videos의 실제 컬럼이 아니기
-- 때문이다. external content(`content='videos'`) 모드는 색인의 모든
-- 컬럼이 참조 테이블의 실제 컬럼이어야 하는데 이 스키마는 그 전제를
-- 깬다 — 초기 설계 실수였고(어떤 쿼리를 던져도 "no such column:
-- T.channel_title"로 즉시 깨진다), 그래서 text를 그대로 복제해 저장하는
-- 독립 테이블로 바꿨다. 영상 몇천 건 규모에선 중복 저장 비용이 무시할
-- 만하다.
CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
    id UNINDEXED, title, description, summary, channel_title
);

-- 자동으로 안 채워지므로 트리거로 직접 동기화한다(Phase 3 step 15,
-- docs/03 §2.6). channel_title은 channels를 조인해서 채운다.
CREATE TRIGGER IF NOT EXISTS videos_fts_ai AFTER INSERT ON videos BEGIN
    INSERT INTO videos_fts(id, title, description, summary, channel_title)
    VALUES (
        new.id, new.title, new.description, new.summary,
        (SELECT title FROM channels WHERE id = new.channel_id)
    );
END;

CREATE TRIGGER IF NOT EXISTS videos_fts_ad AFTER DELETE ON videos BEGIN
    DELETE FROM videos_fts WHERE id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS videos_fts_au AFTER UPDATE ON videos BEGIN
    DELETE FROM videos_fts WHERE id = old.id;
    INSERT INTO videos_fts(id, title, description, summary, channel_title)
    VALUES (
        new.id, new.title, new.description, new.summary,
        (SELECT title FROM channels WHERE id = new.channel_id)
    );
END;

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

-- 승인 대기 (Phase 6 step 26; docs §2.7 approval_required/§3.2 ask) -----
-- 대화 스레드가 `ask` 등급 툴 앞에서 멈췄을 때의 상태. 메모리(asyncio
-- Future)만으로는 서버 재시작 시 사라지므로, "재시작해도 재개된다"를
-- 만족하려면 결정을 여기 영구 저장해야 한다 — /agent/chat이 재시작 후
-- 다시 불려도 이 테이블을 보고 이미 결정된 걸 찾아 재개할 수 있다.
CREATE TABLE IF NOT EXISTS pending_approvals (
    id           TEXT PRIMARY KEY,   -- approval_id
    thread_id    TEXT NOT NULL,
    tool         TEXT NOT NULL,
    args         TEXT NOT NULL,      -- JSON
    summary      TEXT NOT NULL,
    decision     TEXT,               -- NULL이면 아직 대기 중, 'allow'|'deny'
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_thread ON pending_approvals(thread_id);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # 서버(FastAPI 요청 스레드)와 워커(APScheduler 백그라운드 스레드)가
    # 같은 파일을 동시에 쓴다 — WAL이어도 "쓰기끼리"는 한 번에 하나뿐이라
    # 기본값(0ms)이면 곧바로 "database is locked"로 죽는다. 몇 초 정도는
    # 재시도하며 기다리게 한다(실제로 겪은 버그: 서버 기동 직후 워커가
    # RSS 풀 폴링 중일 때 /briefing/today가 즉시 500을 냈다).
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _backfill_fts_if_needed(conn: sqlite3.Connection) -> None:
    """videos_fts_ai/au/ad 트리거는 새 행에만 적용된다 — 트리거가 생기기
    전부터 있던 영상들은 한 번 수동으로 채워 넣어야 한다. `videos_fts`가
    비어 있을 때만 실행해서 매 connect마다 반복하지 않는다."""
    (fts_count,) = conn.execute("SELECT COUNT(*) FROM videos_fts").fetchone()
    if fts_count > 0:
        return
    conn.execute(
        """INSERT INTO videos_fts(id, title, description, summary, channel_title)
           SELECT v.id, v.title, v.description, v.summary, c.title
           FROM videos v JOIN channels c ON c.id = v.channel_id"""
    )


def _heal_stale_fts_schema(conn: sqlite3.Connection) -> None:
    """옛(external content) `videos_fts` 정의가 남아있으면 지운다.

    `CREATE ... IF NOT EXISTS`는 이미 존재하는(깨진) 정의를 그냥 둔다 —
    그래서 SCHEMA_SQL을 실행하기 *전에* 먼저 건강 검진을 한다. 테이블이
    아예 없는 최초 실행에서도 같은 예외가 나므로(둘 다 OperationalError)
    분기 없이 동일하게 처리해도 안전하다 — `DROP ... IF EXISTS`는
    아무것도 없을 때 그냥 조용히 넘어간다."""
    try:
        conn.execute("SELECT COUNT(*) FROM videos_fts")
    except sqlite3.OperationalError:
        conn.executescript(
            """DROP TRIGGER IF EXISTS videos_fts_ai;
               DROP TRIGGER IF EXISTS videos_fts_ad;
               DROP TRIGGER IF EXISTS videos_fts_au;
               DROP TABLE IF EXISTS videos_fts;"""
        )


def init_db(conn: sqlite3.Connection) -> None:
    _heal_stale_fts_schema(conn)
    conn.executescript(SCHEMA_SQL)
    _backfill_fts_if_needed(conn)
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
