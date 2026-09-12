"""`GET /briefing/today`, `POST /briefing/generate` — core/briefing.py의 얇은 REST 어댑터.

docs/05-구현가이드.md Phase 4, step 19; docs/03-API명세.md §2.5.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ytfa.core.briefing import get_today_briefing
from ytfa.core.ranking import DEFAULT_BRIEFING_LIMIT
from ytfa.db import get_connection
from ytfa.llm.cost import LLMClient
from ytfa.security import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])


class GenerateRequest(BaseModel):
    limit: int = DEFAULT_BRIEFING_LIMIT
    force: bool = False


@router.get("/briefing/today")
def briefing_today() -> dict:
    client = LLMClient()
    with get_connection() as conn:
        return get_today_briefing(client, conn, force=False)


@router.post("/briefing/generate")
def briefing_generate(body: GenerateRequest) -> dict:
    client = LLMClient()
    with get_connection() as conn:
        return get_today_briefing(client, conn, force=body.force, briefing_limit=body.limit)
