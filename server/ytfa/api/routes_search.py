"""`GET /search` — core/search.py의 얇은 REST 어댑터(ADR-5).

docs/05-구현가이드.md Phase 3 step 15, Phase 8 step 33-34; docs/03-API명세.md §2.6.

`mode`는 `keyword`(L0, FTS5), `semantic`(L1, 요약 임베딩), `hybrid`(RRF
결합)를 받는다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ytfa.core.search import search_keyword
from ytfa.db import get_connection
from ytfa.rag.embedder import get_embedder
from ytfa.rag.hybrid import search_hybrid
from ytfa.rag.vector_search import search_semantic
from ytfa.security import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])


@router.get("/search")
def search(q: str = Query(...), mode: str = "keyword", limit: int = 10) -> dict:
    if mode == "keyword":
        with get_connection() as conn:
            return search_keyword(conn, q, limit=limit)
    if mode == "semantic":
        with get_connection() as conn:
            return search_semantic(conn, get_embedder(), q, limit=limit)
    if mode == "hybrid":
        with get_connection() as conn:
            return search_hybrid(conn, get_embedder(), q, limit=limit)
    raise HTTPException(status_code=422, detail=f"mode='{mode}'는 지원 안 함 (keyword|semantic|hybrid)")
