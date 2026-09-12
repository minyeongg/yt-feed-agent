"""sources/transcript.py 단위 테스트 (docs/05-구현가이드.md Phase 2, step 10 확인).

실제 유튜브 요청은 하지 않는다 — `ok`/`none`/`failed`를 마음대로 돌려주는
가짜 프로바이더로 상태 기록 로직만 검증한다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from ytfa.db import init_db
from ytfa.sources.transcript import (
    TranscriptResult,
    fetch_and_store_transcript,
    fetch_pending_transcripts,
    get_transcript_excerpt,
)


class FakeProvider:
    """video_id → 결과(TranscriptResult | None | Exception) 매핑을 그대로 재현."""

    def __init__(self, outcomes: dict):
        self.outcomes = outcomes
        self.calls: list[str] = []

    def fetch(self, video_id: str, languages=("ko", "en")):
        self.calls.append(video_id)
        outcome = self.outcomes[video_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC1', '채널', ?)", (now,))
    for vid in ("v_ok", "v_none", "v_failed"):
        c.execute(
            """INSERT INTO videos (id, channel_id, title, published_at, discovered_at, transcript_status)
               VALUES (?, 'UC1', ?, ?, ?, 'pending')""",
            (vid, vid, now, now),
        )
    c.commit()
    return c


def test_ok_stores_segments_and_status(conn):
    provider = FakeProvider({
        "v_ok": TranscriptResult(segments=[{"start": 0.0, "dur": 2.0, "text": "안녕"}], lang="ko", is_auto=True),
    })

    status = fetch_and_store_transcript(conn, "v_ok", provider)

    assert status == "ok"
    assert conn.execute("SELECT transcript_status FROM videos WHERE id='v_ok'").fetchone()[0] == "ok"
    row = conn.execute("SELECT lang, is_auto, segments FROM transcripts WHERE video_id='v_ok'").fetchone()
    assert row[0] == "ko"
    assert row[1] == 1
    assert json.loads(row[2]) == [{"start": 0.0, "dur": 2.0, "text": "안녕"}]


def test_none_when_no_transcript_available(conn):
    provider = FakeProvider({"v_none": None})

    status = fetch_and_store_transcript(conn, "v_none", provider)

    assert status == "none"
    assert conn.execute("SELECT transcript_status FROM videos WHERE id='v_none'").fetchone()[0] == "none"
    assert conn.execute("SELECT COUNT(*) FROM transcripts WHERE video_id='v_none'").fetchone()[0] == 0


def test_failed_does_not_raise(conn):
    provider = FakeProvider({"v_failed": RuntimeError("네트워크 끊김")})

    status = fetch_and_store_transcript(conn, "v_failed", provider)  # 예외가 밖으로 안 나온다

    assert status == "failed"
    assert conn.execute("SELECT transcript_status FROM videos WHERE id='v_failed'").fetchone()[0] == "failed"


def test_fetch_pending_transcripts_mixed_results_no_crash(conn):
    provider = FakeProvider({
        "v_ok": TranscriptResult(segments=[{"start": 0.0, "dur": 1.0, "text": "hi"}], lang="en", is_auto=False),
        "v_none": None,
        "v_failed": RuntimeError("boom"),
    })

    result = fetch_pending_transcripts(conn, provider)

    assert result == {"processed": 3, "ok": 1, "none": 1, "failed": 1}
    # pending이 하나도 안 남아야 재실행 시 다시 안 건드린다
    remaining = conn.execute("SELECT COUNT(*) FROM videos WHERE transcript_status = 'pending'").fetchone()[0]
    assert remaining == 0


def test_get_transcript_excerpt_returns_window(conn):
    provider = FakeProvider({
        "v_ok": TranscriptResult(
            segments=[
                {"start": 0.0, "dur": 5.0, "text": "인트로"},
                {"start": 100.0, "dur": 5.0, "text": "본론 시작"},
                {"start": 300.0, "dur": 5.0, "text": "결론"},
            ],
            lang="ko",
            is_auto=True,
        ),
    })
    fetch_and_store_transcript(conn, "v_ok", provider)

    result = get_transcript_excerpt(conn, "v_ok", start_sec=90, window_sec=60)

    assert result["text"] == "본론 시작"
    assert result["segment_count"] == 1
    assert "인트로" not in result["text"]
    assert "결론" not in result["text"]


def test_get_transcript_excerpt_defaults_to_beginning(conn):
    provider = FakeProvider({
        "v_ok": TranscriptResult(
            segments=[{"start": 0.0, "dur": 5.0, "text": "인트로"}], lang="ko", is_auto=True
        ),
    })
    fetch_and_store_transcript(conn, "v_ok", provider)

    result = get_transcript_excerpt(conn, "v_ok")
    assert result["start_sec"] == 0
    assert "인트로" in result["text"]


def test_get_transcript_excerpt_no_transcript_returns_error(conn):
    result = get_transcript_excerpt(conn, "v_none")
    assert result["error"] == "TRANSCRIPT_UNAVAILABLE"


def test_get_transcript_excerpt_unknown_video_returns_not_found(conn):
    result = get_transcript_excerpt(conn, "totally-unknown")
    assert result["error"] == "NOT_FOUND"
