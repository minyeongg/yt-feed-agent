"""`GET /feed`, `GET /feed/counts` — core/videos.py의 얇은 REST 어댑터(ADR-5).

docs/05-구현가이드.md Phase 3, step 14; docs/03-API명세.md §2.3.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ytfa.core.videos import feed_counts, list_feed
from ytfa.db import get_connection
from ytfa.security import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])


@router.get("/feed")
def get_feed(
    category: str | None = None,
    state: str = "new,seen",
    include_shorts: bool = False,
    limit: int = 30,
) -> dict:
    states = [s.strip() for s in state.split(",") if s.strip()]
    with get_connection() as conn:
        return list_feed(conn, category=category, states=states, include_shorts=include_shorts, limit=limit)


@router.get("/feed/counts")
def get_feed_counts(state: str = "new") -> dict:
    with get_connection() as conn:
        return feed_counts(conn, state=state)
