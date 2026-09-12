"""core/ranking.py 단위 테스트 (docs/05-구현가이드.md Phase 2, step 12 확인).

핵심 확인 대상: (1) LLM 없이 순위가 매겨지는지, (2) 원본 후보가 몇 건이든
요약(LLM 호출)은 항상 `candidate_limit` 이하로 구조적으로 제한되는지.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ytfa.core.ranking import build_briefing, rank_candidates
from ytfa.db import init_db
from ytfa.llm.summarize import SummarizeResult, Verdict


class _FakeLlmConfig:
    small_model = "claude-haiku-4-5"


class _FakeConfig:
    llm = _FakeLlmConfig()


class _FakeResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class FakeTranscriptProvider:
    """자막이 아예 없다고 항상 답하는 가짜 프로바이더 — 실제 네트워크 요청 없음."""

    def fetch(self, video_id, languages=("ko", "en")):
        return None


class FakeLLMClient:
    """summarize_videos가 부르는 `.parse()`를 흉내 낸다 — 실제 API 호출 없음."""

    def __init__(self):
        self.cfg = _FakeConfig()
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        result = SummarizeResult(
            summary=f"요약-{self.calls}",
            verdict=Verdict(topics=["t"], level="일반", hands_on=0.0, one_liner="ㅇㅇ"),
        )
        return _FakeResponse(result), 0.001


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = _iso(0)

    # 채널 둘: 가중치가 다르다.
    c.execute("INSERT INTO channels (id, title, added_at, weight) VALUES ('UC_high', '고가중치', ?, 1.8)", (now,))
    c.execute("INSERT INTO channels (id, title, added_at, weight) VALUES ('UC_low', '저가중치', ?, 0.2)", (now,))
    c.commit()
    return c


def _add_video(conn, video_id, channel_id, title, hours_ago, kind="video"):
    conn.execute(
        """INSERT INTO videos (id, channel_id, title, published_at, discovered_at, kind)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (video_id, channel_id, title, _iso(hours_ago), _iso(hours_ago), kind),
    )
    conn.commit()


def test_rank_candidates_excludes_old_videos(conn):
    _add_video(conn, "v_new", "UC_high", "최근 영상", hours_ago=1)
    _add_video(conn, "v_old", "UC_high", "오래된 영상", hours_ago=200)

    ranked = rank_candidates(conn, since_hours=48)

    ids = [r.video_id for r in ranked]
    assert "v_new" in ids
    assert "v_old" not in ids


def test_rank_candidates_excludes_shorts_by_default(conn):
    _add_video(conn, "v_long", "UC_high", "롱폼", hours_ago=1, kind="video")
    _add_video(conn, "v_short", "UC_high", "숏폼", hours_ago=1, kind="short")

    ranked = rank_candidates(conn, since_hours=48, include_shorts=False)

    ids = [r.video_id for r in ranked]
    assert "v_long" in ids
    assert "v_short" not in ids


def test_channel_weight_affects_ranking(conn):
    _add_video(conn, "v_high", "UC_high", "제목A", hours_ago=1)
    _add_video(conn, "v_low", "UC_low", "제목B", hours_ago=1)

    ranked = rank_candidates(conn, since_hours=48)

    order = [r.video_id for r in ranked]
    assert order.index("v_high") < order.index("v_low")


def test_title_similarity_boosts_score_for_watched_like_titles(conn):
    _add_video(conn, "v_similar", "UC_low", "파이썬 비동기 프로그래밍 튜토리얼", hours_ago=1)
    _add_video(conn, "v_unrelated", "UC_low", "고양이 브이로그", hours_ago=1)
    _add_video(conn, "v_watched", "UC_low", "파이썬 비동기 프로그래밍 기초", hours_ago=100)
    conn.execute(
        "INSERT INTO video_states (video_id, state, updated_at) VALUES ('v_watched', 'watched', ?)", (_iso(0),)
    )
    conn.commit()

    ranked = rank_candidates(conn, since_hours=48)

    order = [r.video_id for r in ranked]
    assert order.index("v_similar") < order.index("v_unrelated")
    similar = next(r for r in ranked if r.video_id == "v_similar")
    assert "예전에 본 영상과 비슷한 제목" in similar.reasons


def test_build_briefing_caps_llm_calls_regardless_of_candidate_pool_size(conn):
    # 새 영상 20건을 만들어도 LLM 호출(=summarize)은 candidate_limit(기본 5) 이하여야 한다.
    for i in range(20):
        _add_video(conn, f"v{i}", "UC_high", f"영상 {i}", hours_ago=1)

    client = FakeLLMClient()
    result = build_briefing(
        client, conn, candidate_limit=5, briefing_limit=3, since_hours=48,
        transcript_provider=FakeTranscriptProvider(),
    )

    assert result["candidates_considered"] == 5
    assert client.calls <= 5
    assert result["summarize"]["summarized"] == 5
    assert len(result["picks"]) == 3
    for pick in result["picks"]:
        assert pick["summary"] is not None
        assert pick["verdict"] is not None
        assert pick["reasons"]


def test_rank_candidates_empty_when_no_new_videos(conn):
    assert rank_candidates(conn, since_hours=48) == []
