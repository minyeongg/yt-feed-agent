"""llm/cost.py 단위 테스트 (docs/05-구현가이드.md Phase 2 step 8, Phase 7 step 30 확인)."""

from __future__ import annotations

import sqlite3

import pytest

from ytfa.db import init_db
from ytfa.llm.cost import (
    CostLimitExceeded,
    check_cost_limit,
    compute_cost_usd,
    cost_summary_detailed,
    record_run,
)


def test_compute_cost_usd_known_model():
    # 1000 input + 200 output 토큰, Haiku 4.5 단가($1/$5 per 1M)
    cost = compute_cost_usd("claude-haiku-4-5", 1000, 200)
    assert cost == pytest.approx(1000 * 1.00 / 1_000_000 + 200 * 5.00 / 1_000_000)


def test_compute_cost_usd_cache_read_is_cheaper_than_input():
    base = compute_cost_usd("claude-sonnet-5", 1000, 0)
    with_cache = compute_cost_usd("claude-sonnet-5", 1000, 0, cache_read_tokens=1000)
    assert base < with_cache
    # 캐시 읽기는 입력가의 10%만 더해진다
    assert with_cache == pytest.approx(base + 1000 * 2.00 * 0.1 / 1_000_000)


def test_compute_cost_usd_unknown_model_raises():
    with pytest.raises(KeyError):
        compute_cost_usd("gpt-4o", 100, 100)


class _FakeCostConfig:
    def __init__(self):
        self.daily_limit_usd = 0.5
        self.monthly_limit_usd = 5.0


class _FakeConfig:
    def __init__(self):
        # 인스턴스별로 새로 만든다 — 클래스 속성으로 공유하면 한 테스트가
        # cfg.cost.daily_limit_usd를 바꿨을 때 다른 테스트에 새는 걸 실제로
        # 겪었다(테스트 실행 순서에 따라 실패가 갈렸다).
        self.cost = _FakeCostConfig()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    return c


def test_check_cost_limit_passes_when_under_limit(conn):
    record_run(
        conn, kind="chat", user_input=None, summary=None,
        input_tokens=100, output_tokens=10, cache_read_tokens=0, cost_usd=0.01, stopped_reason="end_turn",
    )
    check_cost_limit(conn, _FakeConfig())  # 예외 없이 통과해야 한다


def test_check_cost_limit_raises_when_daily_exceeded(conn):
    record_run(
        conn, kind="chat", user_input=None, summary=None,
        input_tokens=1, output_tokens=1, cache_read_tokens=0, cost_usd=0.6, stopped_reason="end_turn",
    )
    with pytest.raises(CostLimitExceeded) as exc_info:
        check_cost_limit(conn, _FakeConfig())
    assert exc_info.value.scope == "일일"


def test_check_cost_limit_raises_when_monthly_exceeded_but_daily_ok(conn):
    cfg = _FakeConfig()
    cfg.cost.daily_limit_usd = 100.0  # 일일은 넉넉하게 — 월간만 걸리게
    for _ in range(6):
        record_run(
            conn, kind="chat", user_input=None, summary=None,
            input_tokens=1, output_tokens=1, cache_read_tokens=0, cost_usd=1.0, stopped_reason="end_turn",
        )
    with pytest.raises(CostLimitExceeded) as exc_info:
        check_cost_limit(conn, cfg)
    assert exc_info.value.scope == "월간"


def test_cost_summary_detailed_reports_throttled_and_remaining(conn):
    record_run(
        conn, kind="summarize", user_input=None, summary=None,
        input_tokens=1, output_tokens=1, cache_read_tokens=0, cost_usd=0.3, stopped_reason="end_turn",
    )
    result = cost_summary_detailed(conn, _FakeConfig())

    assert result["total_usd"] == pytest.approx(0.3)
    assert result["limit_usd"] == 5.0
    assert result["remaining_usd"] == pytest.approx(4.7)
    assert result["by_kind"] == {"summarize": pytest.approx(0.3)}
    assert result["throttled"] is False
    assert len(result["by_day"]) == 1


def test_cost_summary_detailed_throttled_true_over_limit(conn):
    record_run(
        conn, kind="chat", user_input=None, summary=None,
        input_tokens=1, output_tokens=1, cache_read_tokens=0, cost_usd=0.6, stopped_reason="end_turn",
    )
    result = cost_summary_detailed(conn, _FakeConfig())
    assert result["throttled"] is True
