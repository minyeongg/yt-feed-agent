"""rag/indexer.py의 index_l2 단위 테스트 (docs/05-구현가이드.md Phase 8,
step 36 확인 — "watched만 인덱싱", "타임스탬프가 저장된다").

실제 BGE-m3는 로드하지 않는다 — tests/test_indexer.py와 같은 결정적
가짜 임베더를 쓴다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from tests.fakes import FakeEmbedder
from ytfa.db import init_db
from ytfa.rag.indexer import index_l2


SEGMENTS = [
    {"start": 0.0, "dur": 5.0, "text": "안녕하세요 오늘은"},
    {"start": 5.0, "dur": 5.0, "text": "파이썬 비동기 프로그래밍을 다룹니다"},
    {"start": 10.0, "dur": 5.0, "text": "먼저 이벤트 루프부터 설명할게요"},
]


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC1', '채널', ?)", (now,))

    def add_video(video_id: str, watched: bool, has_transcript: bool) -> None:
        c.execute(
            """INSERT INTO videos (id, channel_id, title, published_at, discovered_at)
               VALUES (?, 'UC1', ?, ?, ?)""",
            (video_id, f"제목-{video_id}", now, now),
        )
        if watched:
            c.execute(
                "INSERT INTO video_states (video_id, state, updated_at) VALUES (?, 'watched', ?)",
                (video_id, now),
            )
        if has_transcript:
            c.execute(
                "INSERT INTO transcripts (video_id, lang, is_auto, segments, fetched_at) VALUES (?, 'ko', 1, ?, ?)",
                (video_id, json.dumps(SEGMENTS), now),
            )

    add_video("v_watched_with_transcript", watched=True, has_transcript=True)
    add_video("v_watched_no_transcript", watched=True, has_transcript=False)
    add_video("v_unwatched_with_transcript", watched=False, has_transcript=True)
    c.commit()
    return c


def test_index_l2_only_indexes_watched_videos_with_transcript(conn):
    """안 본 영상이나 자막 없는 영상은 대상에서 빠져야 한다(ADR-7 —
    비용 0이어도 안 볼 영상까지 자막 청킹하는 건 낭비)."""
    result = index_l2(conn, FakeEmbedder())

    assert result["indexed"] == 1
    row = conn.execute("SELECT indexed_level FROM videos WHERE id='v_watched_with_transcript'").fetchone()
    assert row[0] == 2
    row = conn.execute("SELECT indexed_level FROM videos WHERE id='v_watched_no_transcript'").fetchone()
    assert row[0] == 0
    row = conn.execute("SELECT indexed_level FROM videos WHERE id='v_unwatched_with_transcript'").fetchone()
    assert row[0] == 0


def test_index_l2_stores_chunks_with_start_sec(conn):
    index_l2(conn, FakeEmbedder())

    chunks = conn.execute(
        "SELECT id, seq, source, start_sec FROM chunks WHERE video_id='v_watched_with_transcript' ORDER BY seq"
    ).fetchall()
    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk[2] == "transcript"
        assert chunk[3] is not None  # start_sec이 있어야 타임스탬프 딥링크가 가능
        assert chunk[1] >= 1  # L1(seq=0)과 안 겹침


def test_index_l2_skips_already_indexed_videos(conn):
    first = index_l2(conn, FakeEmbedder())
    assert first["indexed"] == 1

    second = index_l2(conn, FakeEmbedder())
    assert second["indexed"] == 0


def test_index_l2_no_pending_videos_returns_zero():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)

    result = index_l2(c, FakeEmbedder())
    assert result == {"indexed": 0, "remaining": 0}


def test_index_l2_limit_and_progress(conn):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO videos (id, channel_id, title, published_at, discovered_at)
           VALUES ('v2', 'UC1', '제목2', ?, ?)""",
        (now, now),
    )
    conn.execute("INSERT INTO video_states (video_id, state, updated_at) VALUES ('v2', 'watched', ?)", (now,))
    conn.execute(
        "INSERT INTO transcripts (video_id, lang, is_auto, segments, fetched_at) VALUES ('v2', 'ko', 1, ?, ?)",
        (json.dumps(SEGMENTS), now),
    )
    conn.commit()

    progress: list[tuple[int, int]] = []
    result = index_l2(conn, FakeEmbedder(), limit=1, on_batch=lambda d, t: progress.append((d, t)))

    assert result["indexed"] == 1
    assert result["remaining"] == 1
    assert progress == [(1, 1)]
