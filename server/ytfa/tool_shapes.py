"""툴 응답 축약형 + 공통 에러 래핑 (docs/03-API명세.md §3 "공통 규칙").

`mcp_server/server.py`(Phase 5)와 `agent/tools_local.py`(Phase 6 step 22)
둘 다 쓴다 — **일부러 공유한다.** step 23("MCP로 교체")이 "동작이 똑같아야
정상"이려면 두 툴 구현이 같은 포맷 함수를 통과해야 한다. 에이전트/MCP
어댑터가 아니라 파이썬 함수·MCP 툴 양쪽이 참조하는 위치라 `core/`도
`mcp_server/`도 아닌 최상위에 둔다.
"""

from __future__ import annotations

import functools
import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

_SINCE_RE = re.compile(r"^(\d+)([dh])$")


def parse_since_hours(since: str, default_hours: int = 24 * 7) -> int:
    """"7d"/"24h" 같은 문자열을 시간 단위로 바꾼다. 못 읽으면 기본값(7일)."""
    match = _SINCE_RE.match(since.strip())
    if not match:
        return default_hours
    value, unit = match.groups()
    return int(value) * 24 if unit == "d" else int(value)


def to_brief_video(card: dict) -> dict:
    """VideoCard(REST 전체형) → 툴 축약형(docs §3.1 예시 그대로)."""
    return {
        "id": card["id"],
        "title": card["title"],
        "channel": card["channel"]["title"],
        "duration_sec": card["duration_sec"],
        "published_at": card["published_at"],
        "summary": card["summary"],
        "state": card["state"],
        "categories": card["categories"],
    }


def safe_tool(fn: Callable[..., dict]) -> Callable[..., dict]:
    """예상 못 한 예외를 `{"error": "INTERNAL_ERROR", ...}`로 바꾼다(공통 규칙).

    예상된 실패(NOT_FOUND 등)는 각 함수가 이미 그 모양으로 직접
    반환한다 — 이건 그 바깥의 최후 방어선이다.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — 의도적으로 전부 잡는다(공통 규칙)
            logger.exception("tool '%s' 실패", fn.__name__)
            return {"error": "INTERNAL_ERROR", "hint": str(exc)}

    return wrapper
