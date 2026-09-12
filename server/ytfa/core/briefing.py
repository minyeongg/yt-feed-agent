"""브리핑 캐시 (docs/03-API명세.md §2.5; Phase 4 step 19가 필요로 해서 지금 채움).

`core/ranking.build_briefing()`은 LLM을 쓴다(후보 요약, step 11) —
팝업을 열 때마다 다시 부르면 열 때마다 과금된다. 그래서 하루 1회 결과를
`runs` 테이블(`kind='brief'`)에 캐싱하고, `force=True`가 아니면 오늘 안에는
재사용한다(docs §2.5: "force=false면 오늘 이미 생성된 게 있을 때 재사용").
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from sqlite3 import Connection

from ytfa.core.ranking import DEFAULT_BRIEFING_LIMIT, DEFAULT_CANDIDATE_LIMIT, build_briefing
from ytfa.core.videos import get_video_card
from ytfa.llm.cost import LLMClient


def _get_cached_today(conn: Connection) -> dict | None:
    row = conn.execute(
        """SELECT summary FROM runs
           WHERE kind = 'brief' AND date(started_at) = date('now')
           ORDER BY started_at DESC LIMIT 1"""
    ).fetchone()
    return json.loads(row[0]) if row else None


def _cache_today(conn: Connection, payload: dict) -> None:
    run_id = f"r_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO runs (id, kind, started_at, finished_at, summary, steps) VALUES (?, 'brief', ?, ?, ?, 1)",
        (run_id, now, now, json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()


def get_today_briefing(
    client: LLMClient,
    conn: Connection,
    force: bool = False,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    briefing_limit: int = DEFAULT_BRIEFING_LIMIT,
) -> dict:
    """`GET /briefing/today` / `POST /briefing/generate`가 공유하는 로직."""
    if not force:
        cached = _get_cached_today(conn)
        if cached is not None:
            return {**cached, "cached": True}

    result = build_briefing(client, conn, candidate_limit=candidate_limit, briefing_limit=briefing_limit)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "picks": [
            {
                "video": get_video_card(conn, p["video_id"]),
                "reason": ", ".join(p["reasons"]),
                "score": p["score"],
            }
            for p in result["picks"]
        ],
        "remaining": max(result["candidates_considered"] - len(result["picks"]), 0),
        "cost_usd": result["summarize"]["cost_usd"],
    }
    _cache_today(conn, payload)
    return {**payload, "cached": False}
