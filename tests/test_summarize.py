"""llm/summarize.py 단위 테스트 (docs/05-구현가이드.md Phase 2, step 11 확인).

실제 Anthropic 호출은 하지 않는다 — `LLMClient.parse()`를 흉내 낸 가짜
클라이언트로 저장·재실행 로직만 검증한다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from ytfa.db import init_db
from ytfa.llm.summarize import SummarizeResult, Verdict, summarize_video, summarize_videos


class _FakeLlmConfig:
    small_model = "claude-haiku-4-5"


class _FakeConfig:
    llm = _FakeLlmConfig()


class _FakeResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class FakeLLMClient:
    def __init__(self, results: list[SummarizeResult]):
        self.cfg = _FakeConfig()
        self._results = list(results)
        self.calls = 0
        self.seen_user_contents: list[str] = []

    def parse(self, *, messages, **kwargs):
        self.seen_user_contents.append(messages[0]["content"])
        result = self._results[self.calls]
        self.calls += 1
        return _FakeResponse(result), 0.002


def _make_result(summary="요약", level="일반") -> SummarizeResult:
    return SummarizeResult(
        summary=summary,
        verdict=Verdict(topics=["a", "b"], level=level, hands_on=0.3, one_liner="한줄평"),
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC1', '채널', ?)", (now,))
    c.execute(
        """INSERT INTO videos (id, channel_id, title, description, published_at, discovered_at, transcript_status)
           VALUES ('v_with_transcript', 'UC1', '제목1', '설명1', ?, ?, 'ok')""",
        (now, now),
    )
    c.execute(
        "INSERT INTO transcripts (video_id, lang, is_auto, segments, fetched_at) VALUES (?, 'ko', 1, ?, ?)",
        ("v_with_transcript", json.dumps([{"start": 0.0, "dur": 1.0, "text": "안녕하세요"}]), now),
    )
    c.execute(
        """INSERT INTO videos (id, channel_id, title, description, published_at, discovered_at, transcript_status)
           VALUES ('v_no_transcript', 'UC1', '제목2', '설명2', ?, ?, 'none')""",
        (now, now),
    )
    c.commit()
    return c


def test_summarize_video_stores_summary_and_verdict(conn):
    client = FakeLLMClient([_make_result(summary="요약됨", level="중급")])

    outcome = summarize_video(client, conn, "v_with_transcript")

    assert outcome is not None
    result, cost = outcome
    assert result.summary == "요약됨"
    assert cost == 0.002

    row = conn.execute(
        "SELECT summary, verdict, summarized_at FROM videos WHERE id='v_with_transcript'"
    ).fetchone()
    assert row[0] == "요약됨"
    assert json.loads(row[1])["level"] == "중급"
    assert row[2] is not None

    # 자막 텍스트가 프롬프트에 실제로 들어갔는지(축소 동작 아님을 확인)
    assert "안녕하세요" in client.seen_user_contents[0]


def test_summarize_video_falls_back_to_title_description_without_transcript(conn):
    client = FakeLLMClient([_make_result()])

    summarize_video(client, conn, "v_no_transcript")

    assert "자막 없음" in client.seen_user_contents[0]
    assert "설명2" in client.seen_user_contents[0]


def test_summarize_video_skips_already_summarized(conn):
    client = FakeLLMClient([_make_result()])
    summarize_video(client, conn, "v_with_transcript")

    outcome = summarize_video(client, conn, "v_with_transcript")

    assert outcome is None
    assert client.calls == 1  # 두 번째 호출에서 LLM을 다시 안 부름


def test_summarize_videos_batch_skips_already_done(conn):
    client = FakeLLMClient([_make_result(), _make_result()])

    first = summarize_videos(client, conn, ["v_with_transcript", "v_no_transcript"])
    assert first == {"summarized": 2, "requested": 2, "cost_usd": 0.004}

    second = summarize_videos(client, conn, ["v_with_transcript", "v_no_transcript"])
    assert second == {"summarized": 0, "requested": 2, "cost_usd": 0.0}
    assert client.calls == 2
