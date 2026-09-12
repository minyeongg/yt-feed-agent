"""`GET /cost/summary` — docs/05-구현가이드.md Phase 7 step 30; docs/03-API명세.md §2.8.

`llm/cost.py`의 얇은 REST 어댑터(ADR-5). `throttled: true`면 상한 초과로
LLM 작업이 중단된 상태다(FR-P6) — 확장은 이때 배너를 띄운다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ytfa.db import get_connection
from ytfa.llm.cost import cost_summary_detailed
from ytfa.security import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])


@router.get("/cost/summary")
def cost_summary(since: str = "30d") -> dict:
    since_days = int(since.rstrip("d")) if since.rstrip("d").isdigit() else 30
    with get_connection() as conn:
        return cost_summary_detailed(conn, since_days=since_days)
