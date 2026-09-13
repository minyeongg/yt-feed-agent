"""rag/indexer.py 단위 테스트 (docs/05-구현가이드.md Phase 8, step 33 확인).

실제 BGE-m3는 로드하지 않는다 — 단어 겹침 기반의 결정적 가짜 임베더로
"요약이 있으면 요약을, 없으면 설명으로 대체해 청크+임베딩을 저장하고
indexed_level을 올리는지"(요약 없는 영상이 대부분이라 커버리지가 중요),
"이미 인덱싱된 영상은 다시 안 건드리는지"만 검증한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from tests.fakes import DIM, FakeEmbedder
from ytfa.db import init_db
from ytfa.rag.indexer import index_l1


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC1', '채널', ?)", (now,))
    c.execute(
        """INSERT INTO videos (id, channel_id, title, summary, published_at, discovered_at)
           VALUES ('v_summarized', 'UC1', '제목1', '요약1', ?, ?)""",
        (now, now),
    )
    c.execute(
        """INSERT INTO videos (id, channel_id, title, description, published_at, discovered_at)
           VALUES ('v_no_summary', 'UC1', '제목2', '설명2', ?, ?)""",
        (now, now),
    )
    c.commit()
    return c


def test_index_l1_indexes_videos_without_summary_too(conn):
    """대부분의 영상이 요약 없이 방치될 수 있다(LLM 비용) — 커버리지가
    없으면 L1 평가는 "임베딩이 나쁘다"가 아니라 "대상이 없다"를 재게
    된다. 그래서 요약 없는 영상도 설명으로 대체해 인덱싱해야 한다."""
    result = index_l1(conn, FakeEmbedder())

    assert result["indexed"] == 2
    row = conn.execute("SELECT indexed_level FROM videos WHERE id='v_summarized'").fetchone()
    assert row[0] == 1
    row = conn.execute("SELECT indexed_level FROM videos WHERE id='v_no_summary'").fetchone()
    assert row[0] == 1

    chunk = conn.execute("SELECT text FROM chunks WHERE video_id='v_no_summary'").fetchone()
    assert "제목2" in chunk[0] and "설명2" in chunk[0]  # 요약 없으니 설명으로 대체됐는지


def test_index_l1_stores_chunk_and_embedding(conn):
    index_l1(conn, FakeEmbedder())

    chunk = conn.execute("SELECT id, video_id, text, source FROM chunks WHERE video_id='v_summarized'").fetchone()
    assert chunk[0] == "v_summarized:0"
    assert "제목1" in chunk[2] and "요약1" in chunk[2]
    assert chunk[3] == "summary"

    emb = conn.execute("SELECT chunk_id, model_name, dim FROM embeddings WHERE chunk_id='v_summarized:0'").fetchone()
    assert emb[1] == "BAAI/bge-m3"
    assert emb[2] == DIM


def test_index_l1_skips_already_indexed_videos(conn):
    first = index_l1(conn, FakeEmbedder())
    assert first["indexed"] == 2

    second = index_l1(conn, FakeEmbedder())
    assert second["indexed"] == 0


def test_index_l1_no_pending_videos_returns_zero(conn):
    index_l1(conn, FakeEmbedder())  # 전부 인덱싱해서 pending을 비운다

    result = index_l1(conn, FakeEmbedder())
    assert result["indexed"] == 0


def test_index_l1_falls_back_to_title_only_when_nothing_else(conn):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO videos (id, channel_id, title, published_at, discovered_at)
           VALUES ('v_bare', 'UC1', '제목만있음', ?, ?)""",
        (now, now),
    )
    conn.commit()

    index_l1(conn, FakeEmbedder())

    chunk = conn.execute("SELECT text FROM chunks WHERE video_id='v_bare'").fetchone()
    assert chunk[0] == "제목만있음"


def test_index_l1_commits_per_batch_and_reports_progress(conn):
    """대량 백필 도중 중단돼도(컴퓨터 절전 등) 이미 처리한 배치는
    남아야 한다 — 배치 크기를 1로 줘서 두 영상이 각각 별도 커밋으로
    처리되는지, on_batch 콜백이 배치마다 불리는지 확인한다."""
    progress_calls: list[tuple[int, int]] = []

    result = index_l1(conn, FakeEmbedder(), batch_size=1, on_batch=lambda done, total: progress_calls.append((done, total)))

    assert result["indexed"] == 2
    assert progress_calls == [(1, 2), (2, 2)]


def test_index_l1_limit_caps_this_run_and_reports_remaining(conn):
    """장시간 로컬 임베딩 중 컴퓨터가 재부팅된 적이 있어서, 한 번에
    끝까지 돌리지 않고 여러 번에 걸쳐 짧게 나눠 돌릴 수 있어야 한다."""
    first = index_l1(conn, FakeEmbedder(), limit=1)
    assert first["indexed"] == 1
    assert first["remaining"] == 1

    second = index_l1(conn, FakeEmbedder(), limit=1)
    assert second["indexed"] == 1
    assert second["remaining"] == 0

    third = index_l1(conn, FakeEmbedder(), limit=1)
    assert third == {"indexed": 0, "remaining": 0}
