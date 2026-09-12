"""core/briefing.py 단위 테스트 (docs/03-API명세.md §2.5, Phase 4 step 19 확인).

핵심 확인 대상: 하루 안에 재요청하면(force 없이) 캐시를 재사용해서
LLM을 다시 안 부르는지 — 팝업을 여러 번 열어도 돈이 안 나가야 한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ytfa.core.briefing import get_today_briefing
from ytfa.db import init_db
from ytfa.llm.summarize import SummarizeResult, Verdict


class _FakeLlmConfig:
    small_model = "claude-haiku-4-5"


class _FakeConfig:
    llm = _FakeLlmConfig()


class _FakeResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class FakeLLMClient:
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


def _iso(hours_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = _iso()
    c.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC1', '채널', ?)", (now,))
    for i in range(5):
        c.execute(
            """INSERT INTO videos (id, channel_id, title, published_at, discovered_at)
               VALUES (?, 'UC1', ?, ?, ?)""",
            (f"v{i}", f"영상 {i}", _iso(1), now),
        )
    c.commit()
    return c


def test_first_call_generates_and_caches(conn):
    client = FakeLLMClient()
    result = get_today_briefing(client, conn)

    assert result["cached"] is False
    assert len(result["picks"]) == 3
    assert client.calls == 5  # candidate_limit 기본값만큼만 요약


def test_second_call_same_day_uses_cache_no_llm_calls(conn):
    client = FakeLLMClient()
    get_today_briefing(client, conn)
    calls_after_first = client.calls

    result = get_today_briefing(client, conn)  # force 없이 재요청

    assert result["cached"] is True
    assert client.calls == calls_after_first  # LLM을 다시 안 부름


def test_force_regenerates_briefing_but_not_already_summarized_videos(conn):
    # force=True는 "브리핑(순위·픽)"을 다시 계산하되, 이미 요약된 영상을
    # 다시 요약하진 않는다 — 영상 요약은 영구 1회(FR-U4)가 더 강한 규칙이다.
    client = FakeLLMClient()
    get_today_briefing(client, conn)
    calls_after_first = client.calls

    result = get_today_briefing(client, conn, force=True)

    assert result["cached"] is False
    assert client.calls == calls_after_first  # 이미 요약된 5건을 또 요약하지 않음

    brief_runs = conn.execute("SELECT COUNT(*) FROM runs WHERE kind = 'brief'").fetchone()[0]
    assert brief_runs == 2  # force가 새 캐시 행을 만들었다(재사용이 아니라 재생성)
