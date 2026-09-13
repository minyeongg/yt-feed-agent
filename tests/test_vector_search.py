"""rag/vector_search.py 단위 테스트 (docs/05-구현가이드.md Phase 8, step 33 확인).

`tests/test_indexer.py`와 같은 결정적 가짜 임베더(단어 겹침 기반)로
"의미가 겹치는 영상이 위로 온다", "watched scope가 필터링된다"를 확인한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import numpy as np
import pytest

from ytfa.db import init_db
from ytfa.rag.indexer import index_l1
from ytfa.rag.vector_search import search_semantic

DIM = 16


class FakeEmbedder:
    """단어 겹침을 L2 정규화된 벡터로 흉내 내는 결정적 가짜(테스트 전용)."""

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for text in texts:
            v = np.zeros(DIM, dtype=np.float32)
            for word in text.split():
                v[hash(word) % DIM] += 1.0
            norm = np.linalg.norm(v)
            vectors.append(v / norm if norm > 0 else v)
        return np.array(vectors, dtype=np.float32)


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
