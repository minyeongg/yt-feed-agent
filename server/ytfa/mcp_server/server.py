"""MCP 서버 (docs/05-구현가이드.md Phase 5, step 20-21; docs/03 §3).

서버 이름 `ytfa` / 전송 stdio. `core/`의 실제 로직을 감싸는 얇은
어댑터다(ADR-5) — REST(`api/routes_*.py`)와 형제 관계다. 반환은 REST의
`VideoCard`보다 필드가 적은 축약형(토큰 절약).

**공통 규칙(docs §3 "공통 규칙")**
- 실패해도 예외를 던지지 않고 `{"error":"<CODE>","hint":"..."}`를
  정상 반환한다 — `@_safe`가 예상 못 한 예외의 최후 방어선이고,
  NOT_FOUND 같은 예상된 실패는 각 함수가 직접 그 모양으로 반환한다.
- 목록 툴은 `limit` 상한을 두고 축약형을 반환한다.

읽기 툴 7개(list_new_videos, get_video, search_videos,
get_transcript_excerpt, list_categories, get_watch_stats, recall)는
`auto_allow`(가만히 둬도 되는 조회)이고, 쓰기 툴 4개(assign_category,
set_video_state, remember, forget)는 `ask`(사용자 승인) 대상이다
(docs §3.2) — 다만 그 승인 게이트 자체는 에이전트 레이어(Phase 6
`agent/permissions.py`)의 책임이라 이 파일은 관여하지 않는다. 15개를
한 번에 다 만들지 않는다(`list_channels`/`get_cost_summary`/
`summarize_videos`는 실제로 필요해질 때 추가한다 — remember/recall/
forget은 Phase 9 step 38에서 추가함).

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
from ytfa.core.memory import forget as _forget, recall as _recall, remember as _remember
from ytfa.core.search import search_keyword
from ytfa.core.stats import get_watch_stats as _get_watch_stats
from ytfa.core.videos import get_video_card, list_feed, set_video_state as _set_video_state
from ytfa.db import get_connection
from ytfa.rag.embedder import get_embedder
from ytfa.rag.hybrid import search_hybrid
from ytfa.rag.vector_search import search_semantic
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
    """제목·설명·요약·자막(L2 인덱싱된 watched 영상만) 검색.

    `mode`: `keyword`(L0, FTS5) | `semantic`(L1/L2 임베딩 코사인) |
    `hybrid`(RRF 결합, 기본값). semantic/hybrid 결과는 최고점 청크가
    L2(자막)면 `start_sec`/`deep_link`가 실제 타임스탬프를 가리킨다
    (step 36) — L1(요약)뿐이면 `start_sec`은 null, `deep_link`는 그냥
    영상 링크다. `scope="video"`(영상 하나 안에서 검색)는 아직
    NOT_IMPLEMENTED다.
    """
    if scope == "video":
        return {"error": "NOT_IMPLEMENTED", "hint": "scope='video'는 아직 지원 안 함"}
    if mode not in {"keyword", "semantic", "hybrid"}:
        return {"error": "INVALID_ARGUMENT", "hint": f"mode='{mode}' 지원 안 함 (keyword|semantic|hybrid)"}

    with get_connection() as conn:
        if mode == "keyword":
            result = search_keyword(conn, query, limit=limit, scope=scope)
        elif mode == "semantic":
            result = search_semantic(conn, get_embedder(), query, limit=limit, scope=scope)
        else:
            result = search_hybrid(conn, get_embedder(), query, limit=limit, scope=scope)

    items = [
        {
            **_to_brief({**item["video"], "state": "new", "categories": []}),
            "score": item["score"],
            "start_sec": item.get("start_sec"),
            "deep_link": item.get("deep_link", item["video"]["url"]),
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


@mcp.tool()
@_safe
def remember(kind: str, content: str, source: str | None = None) -> dict:
    """기억을 저장한다. `kind='preference'`는 다음 대화부터 시스템
    프롬프트에 자동으로 실린다("요약은 짧게" 같은 지속적인 지시에 쓴다).
    `kind='fact'`는 자동 주입 안 되고 `recall`로 찾아볼 때만 쓰인다.
    `content`는 300자 이하로 짧게."""
    with get_connection() as conn:
        return _remember(conn, kind, content, source=source)


@mcp.tool()
@_safe
def forget(id: str) -> dict:
    """저장된 기억 하나를 지운다."""
    with get_connection() as conn:
        return _forget(conn, id)


@mcp.tool()
@_safe
def recall(kind: str = "all", query: str | None = None, limit: int = 20) -> dict:
    """저장된 기억을 찾는다. `kind`: `all`(기본)|`preference`|`fact`.
    `query`가 있으면 내용 부분일치로 거른다."""
    with get_connection() as conn:
        return _recall(conn, kind=kind, query=query, limit=limit)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
