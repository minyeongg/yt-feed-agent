"""llm/subagent.py 단위 테스트 (docs/05-구현가이드.md Phase 9, step 39 확인).

확인 기준 그대로 검증한다: "5건이 병렬로 처리되고 1건이 실패해도 나머지
4건이 온다"(병렬성 + 부분 실패 허용), "부모 컨텍스트에 중간 과정이 안
올라온다"(반환값이 `{"results": [...]}` 하나뿐).

실제 Anthropic 호출은 하지 않는다 — 가짜 `LLMClient.parse()`로 배선만
검증한다. DB는 임시 파일(스레드끼리 커넥션을 공유 안 하므로 `:memory:`가
아니라 파일이어야 함)에 진짜 스키마로 만들어서, 실제 앱 DB는 안 건드린다.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ytfa.db import init_db
from ytfa.llm.cost import CostLimitExceeded
from ytfa.llm.subagent import MAX_VIDEO_IDS, summarize_videos_subagent


def _make_conn_factory(db_path: Path):
    """`get_connection()`과 같은 모양(파일 하나 → 매번 새 커넥션)의 팩토리를
    만든다. 스레드마다 이 팩토리를 불러 자기 커넥션을 연다."""

    def factory():
        @contextmanager
        def _open():
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            try:
                yield conn
            finally:
                conn.close()

        return _open()

    return factory


@pytest.fixture
def conn_factory(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)

    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC1', '채널', ?)", (now,))
    for i in range(1, 6):
        conn.execute(
            """INSERT INTO videos (id, channel_id, title, description, published_at, discovered_at)
               VALUES (?, 'UC1', ?, ?, ?, ?)""",
            (f"v{i}", f"제목{i}", f"설명{i}", now, now),
        )
    conn.commit()
    conn.close()

    return _make_conn_factory(db_path)


class _FakeConfig:
    class _Llm:
        small_model = "claude-haiku-4-5"

    llm = _Llm()


class _FakeResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class FakeLLMClient:
    """`user_input`에 박힌 video_id로 동작을 분기한다.

    - `fail_video_ids`에 있으면 예외를 던진다(부분 실패 시뮬레이션).
    - `delay`만큼 sleep해서, 병렬로 돌면 총 시간이 `N * delay`보다
      훨씬 짧다는 걸로 실제 동시 실행을 확인한다.
    """

    def __init__(self, fail_video_ids: set[str] | None = None, delay: float = 0.0):
        self.cfg = _FakeConfig()
        self.fail_video_ids = fail_video_ids or set()
        self.delay = delay
        self.call_video_ids: list[str] = []
        self._lock = threading.Lock()

    def parse(self, *, messages, user_input, **kwargs):
        video_id = user_input.split(":")[1].split(" ")[0]
        with self._lock:
            self.call_video_ids.append(video_id)
        if self.delay:
            time.sleep(self.delay)
        if video_id in self.fail_video_ids:
            raise RuntimeError(f"모의 실패: {video_id}")

        from ytfa.llm.subagent import Citation, VideoAnswer

        result = VideoAnswer(
            summary=f"{video_id} 요약", key_points=[f"{video_id} 포인트"], citations=[Citation(start_sec=10)]
        )
        return _FakeResponse(result), 0.001


def test_processes_all_videos_and_preserves_input_order(conn_factory):
    client = FakeLLMClient()
    result = summarize_videos_subagent(client, ["v3", "v1", "v2"], conn_factory=conn_factory)

    assert [r["video_id"] for r in result["results"]] == ["v3", "v1", "v2"]
    assert all(r["status"] == "ok" for r in result["results"])


def test_partial_failure_does_not_break_the_batch(conn_factory):
    """확인 기준: 5건 중 1건이 실패해도 나머지 4건은 정상으로 온다."""
    client = FakeLLMClient(fail_video_ids={"v3"})
    video_ids = ["v1", "v2", "v3", "v4", "v5"]

    result = summarize_videos_subagent(client, video_ids, conn_factory=conn_factory)

    by_id = {r["video_id"]: r for r in result["results"]}
    assert len(by_id) == 5
    assert by_id["v3"]["status"] == "failed"
    assert "모의 실패" in by_id["v3"]["error"]
    for vid in ["v1", "v2", "v4", "v5"]:
        assert by_id[vid]["status"] == "ok"
        assert by_id[vid]["summary"] == f"{vid} 요약"


def test_unknown_video_id_fails_gracefully(conn_factory):
    client = FakeLLMClient()
    result = summarize_videos_subagent(client, ["v1", "v_없음"], conn_factory=conn_factory)

    by_id = {r["video_id"]: r for r in result["results"]}
    assert by_id["v_없음"] == {"video_id": "v_없음", "status": "failed", "error": "NOT_FOUND"}
    assert by_id["v1"]["status"] == "ok"


def test_cost_limit_exceeded_is_reported_as_failure_not_crash(conn_factory):
    class _RaisingClient(FakeLLMClient):
        def parse(self, *, messages, user_input, **kwargs):
            raise CostLimitExceeded("일일", 1.0, 0.5)

    result = summarize_videos_subagent(_RaisingClient(), ["v1"], conn_factory=conn_factory)

    assert result["results"][0]["status"] == "failed"
    assert "COST_LIMIT_EXCEEDED" in result["results"][0]["error"]


def test_videos_are_actually_processed_concurrently(conn_factory):
    """확인 기준의 "병렬로 처리"를 실측한다 — 5건이 각각 0.2초씩 걸려도
    순차(1초)가 아니라 병렬(0.2초 근처)로 끝나야 한다."""
    client = FakeLLMClient(delay=0.2)
    video_ids = ["v1", "v2", "v3", "v4", "v5"]

    start = time.monotonic()
    summarize_videos_subagent(client, video_ids, conn_factory=conn_factory)
    elapsed = time.monotonic() - start

    assert elapsed < 0.6  # 순차였다면 1.0초 이상 걸렸을 것


def test_more_than_max_video_ids_is_truncated(conn_factory):
    client = FakeLLMClient()
    video_ids = [f"v{i}" for i in range(1, 6)] + ["v6", "v7"]  # 7개, DB엔 5개뿐이라 초과분은 NOT_FOUND

    result = summarize_videos_subagent(client, video_ids, conn_factory=conn_factory)

    assert len(result["results"]) == MAX_VIDEO_IDS


def test_empty_video_ids_returns_empty_results(conn_factory):
    assert summarize_videos_subagent(FakeLLMClient(), [], conn_factory=conn_factory) == {"results": []}


def test_return_shape_only_exposes_final_results_no_intermediate_state(conn_factory):
    """확인 기준: 부모 컨텍스트에 중간 과정이 안 올라온다 — 반환값이
    `results` 하나뿐이어야 한다(프롬프트·원시 응답 등이 안 섞여야 함)."""
    result = summarize_videos_subagent(FakeLLMClient(), ["v1"], conn_factory=conn_factory)

    assert set(result.keys()) == {"results"}
    assert set(result["results"][0].keys()) == {"video_id", "status", "summary", "key_points", "citations"}
