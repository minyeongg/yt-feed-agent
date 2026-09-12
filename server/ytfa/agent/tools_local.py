"""로컬 함수 툴 — MCP 없는 최소 에이전트 (docs/05-구현가이드.md Phase 6, step 22).

**MCP를 아직 안 붙인다.** 파이썬 함수 3개를 `@tool`로 감싸 프리빌트
루프(`langgraph.prebuilt.create_react_agent` — 가이드 문서의 "create_agent"
와 같은 것, 설치된 langgraph 1.x에서의 실제 이름)로 돌리는 게 이 단계의
전부다. 처음 쓰는 기술 두 개(LangGraph, MCP)를 동시에 붙이면 어디서
깨졌는지 구분이 안 된다(가이드 원칙) — 그래서 순서가 22(로컬 함수) →
23(MCP로 교체) → 24(커스텀 그래프로 분해)다.

여기 3개는 `mcp_server/server.py`의 `list_new_videos`/`search_videos`/
`get_video`와 **의도적으로 같은 core 함수·같은 축약형**(`tool_shapes.py`)
을 쓴다 — step 23에서 이 3개를 MCP 버전으로 갈아끼웠을 때 동작이
그대로여야 "툴 출처만 바뀐 것"이 검증된다.
"""

from __future__ import annotations

from langchain_core.tools import tool

from ytfa.core.search import search_keyword
from ytfa.core.videos import get_video_card, list_feed
from ytfa.db import get_connection
from ytfa.tool_shapes import parse_since_hours, safe_tool, to_brief_video


@tool
@safe_tool
def list_new_videos(
    category: str | None = None,
    since: str = "7d",
    include_shorts: bool = False,
    limit: int = 30,
) -> dict:
    """카테고리·기간으로 새 영상 목록을 가져온다.

    Args:
        category: 카테고리 slug(예: "dev"). 생략하면 전체.
        since: "7d"(7일) / "24h"(24시간) 같은 기간 문자열. 기본 7일.
        include_shorts: Shorts 포함 여부. 기본 제외.
        limit: 최대 개수. 기본 30.
    """
    with get_connection() as conn:
        result = list_feed(
            conn,
            category=category,
            states=["new"],
            include_shorts=include_shorts,
            limit=limit,
            since_hours=parse_since_hours(since),
        )
    return {"items": [to_brief_video(c) for c in result["items"]], "total": result["total"]}


@tool
@safe_tool
def search_videos(query: str, limit: int = 10) -> dict:
    """제목·설명·요약을 전문검색한다(키워드 기반, L0).

    Args:
        query: 검색어.
        limit: 최대 결과 수. 기본 10.
    """
    with get_connection() as conn:
        result = search_keyword(conn, query, limit=limit)
    items = [
        {**to_brief_video({**item["video"], "state": "new", "categories": []}), "score": item["score"]}
        for item in result["items"]
    ]
    return {"query_used": result["query_used"], "items": items, "hint": result["hint"]}


@tool
@safe_tool
def get_video(id: str) -> dict:
    """영상 하나의 상세 정보(요약·verdict·카테고리 포함)를 가져온다.

    Args:
        id: 11자 유튜브 영상 ID.
    """
    with get_connection() as conn:
        card = get_video_card(conn, id)
    if card is None:
        return {"error": "NOT_FOUND", "hint": f"video '{id}' 없음"}
    return card


TOOLS = [list_new_videos, search_videos, get_video]
