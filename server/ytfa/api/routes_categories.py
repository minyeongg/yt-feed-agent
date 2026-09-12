"""`GET /categories` — core/categories.py의 얇은 REST 어댑터(ADR-5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ytfa.core.categories import list_categories
from ytfa.db import get_connection
from ytfa.security import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])


@router.get("/categories")
def get_categories() -> dict:
    with get_connection() as conn:
        return {"items": list_categories(conn)}
