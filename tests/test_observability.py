"""observability.py 단위 테스트 (docs/05-구현가이드.md Phase 7, step 29 확인).

실제 대화 한 번 돌리고 `jq`로 읽는 것까지는 실측으로 따로 확인했다 —
여기선 파일이 정확한 JSONL 형태로 쌓이는지, 기록 실패가 예외를 안
던지는지를 확인한다.
"""

from __future__ import annotations

import json

import pytest

from ytfa.observability import Tracer, trace_path


@pytest.fixture
def run_id(tmp_path, monkeypatch):
    # data_dir을 임시 디렉터리로 돌려서 실제 프로젝트 data/traces를 안 건드린다.
    class _FakeConfig:
        data_dir = tmp_path

    monkeypatch.setattr("ytfa.observability.load_config", lambda: _FakeConfig())
    return "r_test123"


def test_tracer_writes_one_json_line_per_event(run_id):
    tracer = Tracer(run_id)
    tracer.llm_call(
        model="claude-sonnet-5", input_tokens=100, output_tokens=20, cache_read_tokens=0,
        latency_ms=123.4, stop_reason="end_turn", cost_usd=0.0005,
    )
    tracer.tool_call(
        tool="search_videos", args_summary={"q": "test"}, decision="auto_allow",
        latency_ms=88.0, result_size=3, error=None,
    )
    tracer.compaction(before_chars=1000, after_chars=200, compacted_count=2)
    tracer.run_end(steps=3, stopped_reason="end_turn", total_cost_usd=0.0005, total_latency_ms=500)

    lines = trace_path(run_id).read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 4

    records = [json.loads(line) for line in lines]
    assert [r["type"] for r in records] == ["llm_call", "tool_call", "compaction", "run_end"]
    assert [r["seq"] for r in records] == [1, 2, 3, 4]
    assert all(r["run_id"] == run_id for r in records)
    assert all("ts" in r for r in records)

    assert records[0]["cost_usd"] == 0.0005
    assert records[1]["tool"] == "search_videos"
    assert records[2]["compacted_count"] == 2
    assert records[3]["stopped_reason"] == "end_turn"


def test_tracer_swallows_write_failures(run_id, monkeypatch):
    tracer = Tracer(run_id)

    def _boom(*args, **kwargs):
        raise OSError("디스크 가득 참(가짜)")

    monkeypatch.setattr("builtins.open", _boom)
    tracer.llm_call(
        model="x", input_tokens=1, output_tokens=1, cache_read_tokens=0,
        latency_ms=1, stop_reason=None, cost_usd=0.0,
    )  # 예외를 던지면 이 줄에서 테스트가 실패한다 — 안 던지는 게 확인 포인트
