"""rag/hybrid.py 단위 테스트 (docs/05-구현가이드.md Phase 8, step 34 확인).

`rrf_fuse`는 순수 함수라 그대로 계산으로 검증하고, `search_hybrid`는
`tests/test_indexer.py`와 같은 결정적 가짜 임베더로 "키워드/의미 둘 다
걸린 영상이 위로 오는지", "한쪽에서만 걸려도 결과에 남는지"를 확인한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from tests.fakes import FakeEmbedder
from ytfa.db import init_db
from ytfa.rag.hybrid import rrf_fuse, search_hybrid
from ytfa.rag.indexer import index_l1


def test_rrf_fuse_ranks_items_in_both_lists_higher():
    keyword_hits = ["a", "b", "c"]
    semantic_hits = ["b", "a", "d"]

    fused = rrf_fuse(keyword_hits, semantic_hits, k=60)
    fused_ids = [vid for vid, _ in fused]

    # a, b는 두 목록 모두에 있으니 c, d(한쪽에만)보다 위여야 한다.
    assert fused_ids.index("a") < fused_ids.index("c")
    assert fused_ids.index("b") < fused_ids.index("d")


def test_rrf_fuse_score_matches_formula():
    fused = dict(rrf_fuse(["a"], ["a"], k=60))
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 61)


def test_rrf_fuse_empty_lists_returns_empty():
    assert rrf_fuse([], []) == []


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
           VALUES ('v_rust', 'UC1', '러스트 비동기 런타임', 'futures poll waker', ?, ?)""",
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


def test_search_hybrid_returns_expected_shape(conn):
    result = search_hybrid(conn, FakeEmbedder(), "러스트 비동기", limit=5)

    assert result["mode"] == "hybrid"
    assert result["items"][0]["video"]["id"] == "v_rust"
    assert result["items"][0]["matched_by"] in {"hybrid", "keyword", "semantic"}


def test_search_hybrid_falls_back_to_semantic_when_keyword_misses(conn):
    """키워드 매치가 하나도 없어도 임베딩은 항상 top-k를 내놓는다(관련성
    낮아도 점수만 매겨 반환) — 그래서 하이브리드는 완전히 빈 결과가
    거의 안 나온다. 이 케이스에선 semantic만 걸려 있어야 한다."""
    result = search_hybrid(conn, FakeEmbedder(), "이런단어는절대없다zzz", limit=5)

    assert result["items"] != []
    assert all(item["matched_by"] == "semantic" for item in result["items"])


def test_search_hybrid_no_data_at_all_returns_hint():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)

    result = search_hybrid(c, FakeEmbedder(), "아무거나", limit=5)
    assert result["items"] == []
    assert result["hint"] is not None
