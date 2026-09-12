"""LLM 클라이언트 + 비용 기록 (docs/05-구현가이드.md Phase 2, step 8).

Anthropic 호출마다 토큰 사용량을 읽어 `runs` 테이블에 비용을 적립한다.
이 모듈을 categorize.py/summarize.py보다 먼저 만드는 이유는 문서
그대로다 — 나중에 붙이면 어디서 돈이 나갔는지 영영 모른다.

아직은 Phase 6 이전이라 "실행"이 다단계 에이전트가 아니라 단발 호출
하나다. 그래서 `runs` 테이블의 `steps`는 항상 1이고, `thread_id` 개념도
없다. 에이전트가 생기면(step 24) 여러 스텝을 한 run_id 아래 누적하는
형태로 확장한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Any

import anthropic
from pydantic import BaseModel

from ytfa.config import Config, load_config

# $/1M 토큰 (input, output). docs/claude-api 스킬 가격표 기준(2026-06-24 캐시).
# 여기 없는 모델로 호출하면 조용히 0원 처리하지 않고 KeyError를 던진다 —
# 상한(Phase 7 step 30)이 실측 비용을 못 보면 의미가 없어지기 때문이다.
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
}

# 캐시 히트 토큰의 단가 비율(입력가 대비). Anthropic 공식 근사치.
CACHE_READ_DISCOUNT = 0.1


def compute_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """토큰 사용량을 달러로 환산한다. 단가표에 없는 모델이면 KeyError."""
    if model not in PRICING_PER_MTOK:
        raise KeyError(f"'{model}' 단가가 PRICING_PER_MTOK에 없음 — 표부터 채우고 호출할 것")
    input_price, output_price = PRICING_PER_MTOK[model]
    cost = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    cost += (cache_read_tokens * input_price * CACHE_READ_DISCOUNT) / 1_000_000
    return cost


def _record_run(
    conn: Connection,
    *,
    kind: str,
    user_input: str | None,
    summary: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cost_usd: float,
    stopped_reason: str | None,
) -> str:
    run_id = f"r_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO runs (id, kind, started_at, finished_at, user_input, summary,
                              steps, input_tokens, output_tokens, cache_read_tokens,
                              cost_usd, stopped_reason)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
        (
            run_id, kind, now, now, user_input, summary,
            input_tokens, output_tokens, cache_read_tokens, cost_usd, stopped_reason,
        ),
    )
    conn.commit()
    return run_id


class LLMClient:
    """비용을 자동으로 기록하는 Anthropic 클라이언트 래퍼.

    `call()` 한 번이 `runs` 테이블의 한 행이다. `conn`을 넘기지 않으면
    기록 없이 순수 호출만 한다(테스트 등에서 유용).
    """

    def __init__(self, cfg: Config | None = None, client: anthropic.Anthropic | None = None):
        self.cfg = cfg or load_config()
        self.client = client or anthropic.Anthropic(api_key=self.cfg.anthropic_api_key)

    def _finish(
        self,
        response: Any,
        *,
        model: str,
        kind: str,
        user_input: str | None,
        summary_text: str | None,
        conn: Connection | None,
    ) -> float:
        """공통 마무리: 비용 계산 + (conn이 있으면) runs 적립."""
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = compute_cost_usd(model, usage.input_tokens, usage.output_tokens, cache_read)

        if conn is not None:
            _record_run(
                conn,
                kind=kind,
                user_input=user_input,
                summary=summary_text,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=cache_read,
                cost_usd=cost,
                stopped_reason=response.stop_reason,
            )
        return cost

    def call(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        kind: str,
        user_input: str | None = None,
        conn: Connection | None = None,
        **kwargs: Any,
    ) -> tuple[Any, float]:
        """단발 호출 1회 → (Anthropic Message, 비용 USD)."""
        response = self.client.messages.create(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            **kwargs,
        )
        summary_text = next((b.text[:200] for b in response.content if b.type == "text"), None)
        cost = self._finish(response, model=model, kind=kind, user_input=user_input, summary_text=summary_text, conn=conn)
        return response, cost

    def parse(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        max_tokens: int,
        output_format: type[BaseModel],
        kind: str,
        user_input: str | None = None,
        conn: Connection | None = None,
        **kwargs: Any,
    ) -> tuple[Any, float]:
        """구조화 출력 호출 1회 → (`.parsed_output`이 붙은 Message, 비용 USD).

        step 9(categorize)·step 11(summarize)가 공통으로 쓰는 경로다 —
        둘 다 자유 텍스트가 아니라 검증된 스키마가 필요하다.
        """
        response = self.client.messages.parse(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            output_format=output_format,
            **kwargs,
        )
        cost = self._finish(
            response, model=model, kind=kind, user_input=user_input,
            summary_text=str(response.parsed_output)[:200], conn=conn,
        )
        return response, cost


def cost_summary(conn: Connection, since_days: int = 30) -> dict:
    """`xba cost` / 향후 `GET /cost/summary`(Phase 7 step 30)가 쓸 집계."""
    total_usd, run_count = conn.execute(
        """SELECT COALESCE(SUM(cost_usd), 0), COUNT(*)
           FROM runs WHERE started_at >= datetime('now', ?)""",
        (f"-{since_days} days",),
    ).fetchone()
    by_kind = dict(
        conn.execute(
            """SELECT kind, COALESCE(SUM(cost_usd), 0) FROM runs
               WHERE started_at >= datetime('now', ?) GROUP BY kind""",
            (f"-{since_days} days",),
        ).fetchall()
    )
    return {"total_usd": round(total_usd, 6), "run_count": run_count, "by_kind": by_kind}
