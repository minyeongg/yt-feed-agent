"""rag/vector_search.py 단위 테스트 (docs/05-구현가이드.md Phase 8, step 33/36 확인).

`tests/test_indexer.py`와 같은 결정적 가짜 임베더(단어 겹침 기반)로
"의미가 겹치는 영상이 위로 온다", "watched scope가 필터링된다",
"L2 자막 청크가 최고점이면 타임스탬프 딥링크가 나온다"를 확인한다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from tests.fakes import FakeEmbedder
from ytfa.db import init_db
from ytfa.rag.indexer import index_l1, index_l2
from ytfa.rag.vector_search import search_semantic


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
           VALUES ('v_rust', 'UC1', '러스트 비동기 런타임 파헤치기', 'futures poll waker 설명', ?, ?)""",
        (now, now),
    )
    c.execute(
        """INSERT INTO videos (id, channel_id, title, summary, published_at, discovered_at)
           VALUES ('v_cat', 'UC1', '고양이 브이로그', '귀여운 고양이가 나온다', ?, ?)""",
        (now, now),
    )
    c.commit()
    index_l1(c, FakeEmbedder())
    return c


def test_search_semantic_ranks_matching_video_first(conn):
    result = search_semantic(conn, FakeEmbedder(), "러스트 비동기 futures", limit=5)

    assert result["mode"] == "semantic"
    assert result["items"][0]["video"]["id"] == "v_rust"
    assert result["items"][0]["matched_by"] == "semantic"


def test_search_semantic_l1_only_chunk_has_no_start_sec(conn):
    """L1(요약) 청크는 start_sec이 없다 — 딥링크가 그냥 영상 링크여야 한다."""
    result = search_semantic(conn, FakeEmbedder(), "러스트 비동기 futures", limit=5)

    item = result["items"][0]
    assert item["start_sec"] is None
    assert item["deep_link"] == "https://youtu.be/v_rust"


def test_search_semantic_l2_chunk_surfaces_timestamp_deep_link(conn):
    """L2(자막) 청크가 최고점으로 매치되면 start_sec이 실려서
    `youtu.be/xxx?t=..` 형태의 딥링크가 나와야 한다(step 36 확인 기준)."""
    now = datetime.now(timezone.utc).isoformat()
    # 첫 세그먼트를 충분히 길게(기본 target_tokens=500 이상) 채워서 그
    # 자체로 청크 하나를 다 채우게 한다 — 그래야 두 번째 세그먼트가 별도
    # 청크로 갈라져서 start_sec=90을 그대로 갖는다.
    filler = "필러단어" * 600  # 약 600 토큰(len//4) 분량
    segments = [
        {"start": 0.0, "dur": 80.0, "text": filler},
        {"start": 90.0, "dur": 5.0, "text": "러스트 비동기 futures waker 핵심 설명"},
    ]
    conn.execute(
        "INSERT INTO video_states (video_id, state, updated_at) VALUES ('v_rust', 'watched', ?)", (now,)
    )
    conn.execute(
        "INSERT INTO transcripts (video_id, lang, is_auto, segments, fetched_at) VALUES ('v_rust', 'ko', 1, ?, ?)",
        (json.dumps(segments), now),
    )
    conn.commit()
    index_l2(conn, FakeEmbedder())

    result = search_semantic(conn, FakeEmbedder(), "러스트 비동기 futures waker", limit=5)

    item = result["items"][0]
    assert item["video"]["id"] == "v_rust"
    assert item["start_sec"] == 90
    assert item["deep_link"] == "https://youtu.be/v_rust?t=90"


def test_search_semantic_no_embeddings_returns_hint():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)

    result = search_semantic(c, FakeEmbedder(), "아무거나", limit=5)
    assert result["items"] == []
    assert result["hint"] is not None


def test_search_semantic_scope_watched_filters_unwatched(conn):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO video_states (video_id, state, updated_at) VALUES ('v_cat', 'watched', ?)", (now,)
    )
    conn.commit()

    result = search_semantic(conn, FakeEmbedder(), "러스트 비동기 futures", limit=5, scope="watched")

    ids = [item["video"]["id"] for item in result["items"]]
    assert ids == ["v_cat"]  # v_rust는 watched가 아니라서 제외
