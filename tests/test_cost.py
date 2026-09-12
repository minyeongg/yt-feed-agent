"""llm/cost.py 단위 테스트 (docs/05-구현가이드.md Phase 2, step 8 확인)."""

from __future__ import annotations

import pytest

from ytfa.llm.cost import compute_cost_usd


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
