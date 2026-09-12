"""`GET /search` — core/search.py의 얇은 REST 어댑터(ADR-5).

docs/05-구현가이드.md Phase 3, step 15; docs/03-API명세.md §2.6.

`mode`는 지금 `keyword`만 받는다 — `semantic`/`hybrid`는 아직 없는
기능이라 요청하면 422로 거절한다(있는 척 200을 돌려주지 않는다).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ytfa.core.search import search_keyword
from ytfa.db import get_connection
from ytfa.security import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])


@router.get("/search")
def search(q: str = Query(...), mode: str = "keyword", limit: int = 10) -> dict:
    if mode != "keyword":
        raise HTTPException(status_code=422, detail=f"mode='{mode}'는 아직 지원 안 함 — Phase 8에서 추가 예정(L0만 있음)")
    with get_connection() as conn:
        return search_keyword(conn, q, limit=limit)
