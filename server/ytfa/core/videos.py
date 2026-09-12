"""피드 서비스 (docs/05-구현가이드.md Phase 3, step 14; docs/03 §2.3).

`api/routes_feed.py`(REST)가 감쌀 실제 로직. ADR-5: 로직은 코어에만
있고, 라우터는 얇은 어댑터다.

`video_states` 행이 없는 영상은 논리적으로 `state='new'`로 취급한다
(`core/ranking.py`와 같은 규칙) — Phase 3 REST가 아직 상태 변경
엔드포인트를 안 갖췄으니 지금 DB의 모든 영상이 이 경로를 탄다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlite3 import Connection

DEFAULT_FEED_LIMIT = 30


def _video_categories(conn: Connection, channel_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT category_id FROM channel_categories WHERE channel_id = ?", (channel_id,)
    ).fetchall()
    return [r[0] for r in rows]


def _row_to_card(conn: Connection, row) -> dict:
    video_id, title, channel_id, channel_title, channel_thumb = row[0], row[1], row[2], row[3], row[4]
    published_at, duration_sec, kind, thumbnail_url = row[5], row[6], row[7], row[8]
    summary, verdict_json, state, transcript_status = row[9], row[10], row[11], row[12]
    return {
        "id": video_id,
        "title": title,
        "url": f"https://youtu.be/{video_id}",
        "channel": {"id": channel_id, "title": channel_title, "thumbnail_url": channel_thumb or ""},
        "published_at": published_at,
        "duration_sec": duration_sec,
        "kind": kind,
        "thumbnail_url": thumbnail_url,
        "summary": summary,
        "verdict": json.loads(verdict_json) if verdict_json else None,
        "state": state,
        "categories": _video_categories(conn, channel_id),
        "transcript_status": transcript_status,
    }


_CARD_SELECT = """
    SELECT v.id, v.title, v.channel_id, c.title, c.thumbnail_url,
           v.published_at, v.duration_sec, v.kind, v.thumbnail_url,
           v.summary, v.verdict, COALESCE(vs.state, 'new'), v.transcript_status
    FROM videos v
    JOIN channels c ON c.id = v.channel_id
    LEFT JOIN video_states vs ON vs.video_id = v.id
"""


def get_video_card(conn: Connection, video_id: str) -> dict | None:
    """`GET /videos/{id}` 및 브리핑(core/briefing.py)이 공유하는 단건 조회."""
    row = conn.execute(f"{_CARD_SELECT} WHERE v.id = ?", (video_id,)).fetchone()
    return _row_to_card(conn, row) if row else None


def list_feed(
    conn: Connection,
    category: str | None = None,
    states: list[str] | None = None,
    include_shorts: bool = False,
    limit: int = DEFAULT_FEED_LIMIT,
    since_hours: int | None = None,
) -> dict:
    """`GET /feed` — VideoCard 목록(docs/03 §2.3).

    `since_hours`는 REST에는 없고 MCP `list_new_videos`(docs §3.1,
    Phase 5 step 20)가 쓴다 — "새 영상"은 상태뿐 아니라 최근성도 봐야
    해서 추가했다.
    """
    states = states or ["new", "seen"]
    placeholders = ",".join("?" for _ in states)
    params: list = list(states)

    query = f"{_CARD_SELECT} WHERE COALESCE(vs.state, 'new') IN ({placeholders})"
    if not include_shorts:
        query += " AND v.kind != 'short'"
    if category:
        query += " AND v.channel_id IN (SELECT channel_id FROM channel_categories WHERE category_id = ?)"
        params.append(category)
    if since_hours is not None:
        # datetime()으로 감싸는 이유는 ranking.py의 같은 패턴 주석 참고 —
        # 저장 형식과 sqlite 출력 형식이 달라서 안 감싸면 날짜 경계에서
        # 틀린다(실측 버그).
        query += " AND datetime(v.published_at) >= datetime('now', ?)"
        params.append(f"-{since_hours} hours")
    query += " ORDER BY v.published_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    items = [_row_to_card(conn, r) for r in rows]

    return {"items": items, "next_cursor": None, "total": len(items)}


def set_video_state(conn: Connection, video_id: str, state: str, watch_seconds: int | None = None) -> dict:
    """`POST /videos/{id}/state` / MCP `set_video_state`(docs §2.4, §3.2).

    `watched`로 바뀌면 L2 자막 인덱싱 대상이 된다(ADR-7) — 다만 실제
    인덱싱 워커는 Phase 8에나 생긴다. 지금은 신호만 정직하게 돌려준다
    (있지도 않은 큐에 넣은 척은 안 하지만, 나중에 Phase 8이 이 신호를
    보고 그대로 소비하면 되게 필드는 미리 맞춰둔다).
    """
    row = conn.execute("SELECT id FROM videos WHERE id = ?", (video_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "NOT_FOUND", "hint": f"video '{video_id}' 없음"}

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO video_states (video_id, state, watch_seconds, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(video_id) DO UPDATE SET
               state = excluded.state, watch_seconds = excluded.watch_seconds, updated_at = excluded.updated_at""",
        (video_id, state, watch_seconds, now),
    )
    conn.commit()
    return {"ok": True, "queued_for_index": state == "watched"}


def feed_counts(conn: Connection, state: str = "new") -> dict:
    """`GET /feed/counts` — 탭 바 뱃지 숫자(docs/03 §2.3)."""
    total = conn.execute(
        """SELECT COUNT(*) FROM videos v
           LEFT JOIN video_states vs ON vs.video_id = v.id
           WHERE COALESCE(vs.state, 'new') = ?""",
        (state,),
    ).fetchone()[0]

    rows = conn.execute(
        """SELECT cat.id, COUNT(DISTINCT v.id)
           FROM categories cat
           JOIN channel_categories cc ON cc.category_id = cat.id
           JOIN videos v ON v.channel_id = cc.channel_id
           LEFT JOIN video_states vs ON vs.video_id = v.id
           WHERE COALESCE(vs.state, 'new') = ?
           GROUP BY cat.id""",
        (state,),
    ).fetchall()

    return {"total": total, "by_category": {r[0]: r[1] for r in rows}}
