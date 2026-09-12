"""카테고리 조회 서비스 (docs/03-API명세.md §2.2).

Phase 3(step 14)에서 `/feed`, `/feed/counts`만 만들고 일부러 미뤄뒀던
조각 — Phase 4(크롬 확장)의 카테고리 탭 바가 표시 이름(`name`)을 쓰려면
필요해서 지금 채운다. `core/videos.py`와 같은 원칙(ADR-5): 실제 로직은
여기, 라우터는 얇게.
"""

from __future__ import annotations

from sqlite3 import Connection


def list_categories(conn: Connection) -> list[dict]:
    """`GET /categories` — 채널 수·새 영상 수 집계를 곁들인 카테고리 목록."""
    rows = conn.execute(
        """SELECT cat.id, cat.name, cat.ord, cat.color, cat.is_default,
                  COUNT(DISTINCT cc.channel_id) AS channel_count,
                  COUNT(DISTINCT CASE WHEN COALESCE(vs.state, 'new') = 'new' THEN v.id END) AS new_video_count
           FROM categories cat
           LEFT JOIN channel_categories cc ON cc.category_id = cat.id
           LEFT JOIN videos v ON v.channel_id = cc.channel_id
           LEFT JOIN video_states vs ON vs.video_id = v.id
           GROUP BY cat.id
           ORDER BY cat.ord, cat.id"""
    ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "order": r[2],
            "color": r[3],
            "is_default": bool(r[4]),
            "channel_count": r[5],
            "new_video_count": r[6],
        }
        for r in rows
    ]


def assign_category_to_channel(conn: Connection, channel_id: str, category_ids: list[str]) -> dict:
    """사용자가 직접 카테고리를 지정한다(docs §2.2 `PATCH /channels`, §3.2 `assign_category`).

    기존 배정(자동이든 수동이든)을 전부 교체하고 `category_locked=True`로
    잠근다 — 이후 `llm/categorize.py`의 자동 재분류 대상에서 빠진다
    (FR-K2: "사용자가 손댔으면 자동 재분류 금지").
    """
    channel = conn.execute("SELECT id, title FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if channel is None:
        return {"error": "NOT_FOUND", "hint": f"channel '{channel_id}' 없음"}

    known_ids = {r[0] for r in conn.execute("SELECT id FROM categories").fetchall()}
    unknown = [c for c in category_ids if c not in known_ids]
    if unknown:
        return {"error": "NOT_FOUND", "hint": f"존재하지 않는 카테고리: {unknown}"}

    conn.execute("DELETE FROM channel_categories WHERE channel_id = ?", (channel_id,))
    for category_id in category_ids:
        conn.execute(
            "INSERT INTO channel_categories (channel_id, category_id, assigned_by, confidence) VALUES (?, ?, 'user', NULL)",
            (channel_id, category_id),
        )
    conn.execute("UPDATE channels SET category_locked = 1, needs_review = 0 WHERE id = ?", (channel_id,))
    conn.commit()

    return {
        "id": channel[0],
        "title": channel[1],
        "category_ids": category_ids,
        "category_locked": True,
        "assigned_by": "user",
    }
