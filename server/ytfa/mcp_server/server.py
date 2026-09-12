"""MCP 서버 (docs/05-구현가이드.md Phase 5, step 20-21; docs/03 §3).

서버 이름 `ytfa` / 전송 stdio. `core/`의 실제 로직을 감싸는 얇은
어댑터다(ADR-5) — REST(`api/routes_*.py`)와 형제 관계다. 반환은 REST의
`VideoCard`보다 필드가 적은 축약형(토큰 절약).

**공통 규칙(docs §3 "공통 규칙")**
- 실패해도 예외를 던지지 않고 `{"error":"<CODE>","hint":"..."}`를
  정상 반환한다 — `@_safe`가 예상 못 한 예외의 최후 방어선이고,
  NOT_FOUND 같은 예상된 실패는 각 함수가 직접 그 모양으로 반환한다.
- 목록 툴은 `limit` 상한을 두고 축약형을 반환한다.

읽기 툴 8개 중 6개(list_new_videos, get_video, search_videos,
get_transcript_excerpt, list_categories, get_watch_stats)는 `auto_allow`
(가만히 둬도 되는 조회)이고, 쓰기 툴 2개(assign_category,
set_video_state)는 `ask`(사용자 승인) 대상이다(docs §3.2) — 다만 그
승인 게이트 자체는 에이전트 레이어(Phase 6 `agent/permissions.py`)의
책임이라 이 파일은 관여하지 않는다. 15개를 한 번에 다 만들지 않는다
(`list_channels`/`recall`/`remember`/`get_cost_summary`/
`summarize_videos`는 Phase 6·9에서 실제로 필요해질 때 추가한다).

**`mcp<2.0.0`로 고정한 이유**: `mcp` SDK 2.x는 `FastMCP`를 `MCPServer`로
개명하는 등 API가 바뀌었고, 이 파일은 원래 2.x로 만들어져 있었다. 하지만
step 23에서 쓸 `langchain-mcp-adapters`(ADR-6이 지정한 MCP 클라이언트)가
아직 `mcp<2.0.0`만 지원해서 — 최신 버전을 쓰다가 클라이언트 라이브러리와
안 맞는 걸 뒤늦게 발견하고 여기서 1.x(`FastMCP`)로 내렸다. MCP 프로토콜
자체는 SDK 버전과 무관하게 호환되므로 기능 손실은 없다.

실행:
    uv run python -m ytfa.mcp_server.server
확인(MCP Inspector):
    npx @modelcontextprotocol/inspector uv run python -m ytfa.mcp_server.server
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from ytfa.core.categories import assign_category_to_channel, list_categories as _list_categories
from ytfa.core.search import search_keyword
from ytfa.core.stats import get_watch_stats as _get_watch_stats
from ytfa.core.videos import get_video_card, list_feed, set_video_state as _set_video_state
from ytfa.db import get_connection
from ytfa.sources.transcript import get_transcript_excerpt as _get_transcript_excerpt
from ytfa.tool_shapes import parse_since_hours as _parse_since_hours, safe_tool as _safe, to_brief_video as _to_brief

logger = logging.getLogger(__name__)

mcp = FastMCP(name="ytfa")


# --- 읽기 툴 (auto_allow) ---------------------------------------------


@mcp.tool()
@_safe
def list_new_videos(
    category: str | None = None,
    since: str = "7d",
    include_shorts: bool = False,
    limit: int = 30,
) -> dict:
    """카테고리·기간으로 새 영상 목록을 축약형으로 준다."""
    with get_connection() as conn:
        result = list_feed(
            conn,
            category=category,
            states=["new"],
            include_shorts=include_shorts,
            limit=limit,
            since_hours=_parse_since_hours(since),
        )
    return {"items": [_to_brief(c) for c in result["items"]], "total": result["total"]}


@mcp.tool()
@_safe
def get_video(id: str) -> dict:
    """영상 하나의 상세 정보(요약·verdict·자막 상태·카테고리 포함)."""
    with get_connection() as conn:
        card = get_video_card(conn, id)
    if card is None:
        return {"error": "NOT_FOUND", "hint": f"video '{id}' 없음"}
    return card


@mcp.tool()
@_safe
def search_videos(
    query: str,
    mode: str = "hybrid",
    scope: str = "all",
    video_id: str | None = None,
    limit: int = 10,
) -> dict:
    """제목·설명·요약 전문검색(L0). `mode`/`video_id` 스코프는 아직 일부만 지원한다.

    현재는 키워드(L0) 검색만 실제로 동작한다 — `mode`에 뭘 넣든 항상
    keyword로 처리하고, 응답의 `mode` 필드가 실제로 뭘 썼는지 정직하게
    알려준다(hybrid/semantic은 Phase 8 임베딩이 있어야 함).
    `scope="video"`(영상 하나 안에서 검색)는 자막 청킹(L2, Phase 8)이
    있어야 의미가 있어서 아직 NOT_IMPLEMENTED를 돌려준다.
    """
    if scope == "video":
        return {"error": "NOT_IMPLEMENTED", "hint": "scope='video'는 자막 청킹(Phase 8) 이후에 지원 예정"}

    with get_connection() as conn:
        result = search_keyword(conn, query, limit=limit, scope=scope)

    items = [
        {
            **_to_brief({**item["video"], "state": "new", "categories": []}),
            "score": item["score"],
            "start_sec": None,  # L2(타임스탬프 청킹) 전이라 아직 없음
            "deep_link": item["video"]["url"],
        }
        for item in result["items"]
    ]
    return {"mode": result["mode"], "query_used": result["query_used"], "items": items, "hint": result["hint"]}


@mcp.tool()
@_safe
def get_transcript_excerpt(video_id: str, start_sec: float | None = None, window_sec: float = 120) -> dict:
    """자막 전문이 아니라 `start_sec` 주변 `window_sec`초만 잘라서 준다."""
    with get_connection() as conn:
        return _get_transcript_excerpt(conn, video_id, start_sec=start_sec, window_sec=window_sec)


@mcp.tool()
@_safe
def list_categories() -> dict:
    """카테고리 목록(채널 수·새 영상 수 포함)."""
    with get_connection() as conn:
        items = _list_categories(conn)
    return {
        "items": [
            {"id": c["id"], "name": c["name"], "channel_count": c["channel_count"], "new_video_count": c["new_video_count"]}
            for c in items
        ]
    }


@mcp.tool()
@_safe
def get_watch_stats(since: str = "30d") -> dict:
    """시청/스킵 통계 + 카테고리 분포."""
    with get_connection() as conn:
        return _get_watch_stats(conn, since_hours=_parse_since_hours(since, default_hours=24 * 30))


# --- 쓰기 툴 (ask — 승인 게이트는 Phase 6 agent 레이어의 몫) -----------


@mcp.tool()
@_safe
def assign_category(channel_id: str, category_ids: list[str]) -> dict:
    """채널의 카테고리를 사용자 의도로 확정한다. `category_locked=true`로 잠긴다."""
    with get_connection() as conn:
        return assign_category_to_channel(conn, channel_id, category_ids)


@mcp.tool()
@_safe
def set_video_state(video_id: str, state: str, watch_seconds: int | None = None) -> dict:
    """영상 시청 상태를 바꾼다. `watched`면 L2 인덱싱 대상으로 표시된다(Phase 8)."""
    with get_connection() as conn:
        return _set_video_state(conn, video_id, state, watch_seconds=watch_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
