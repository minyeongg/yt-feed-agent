"""시청 통계 (docs/03-API명세.md §3.1 `get_watch_stats`; Phase 5 step 21).

MCP 전용 툴이다 — REST엔 대응 엔드포인트가 없다(에이전트가 "요즘 뭘 많이
봤어?" 같은 질문에 답할 때 쓰라고 만든 것).
"""

from __future__ import annotations

from sqlite3 import Connection


def get_watch_stats(conn: Connection, since_hours: int = 24 * 30) -> dict:
    """최근 `since_hours` 안의 시청/스킵 통계 + 카테고리 분포.

    `updated_at`은 우리가 저장한 ISO 문자열이라 `datetime()`으로 다시
    파싱해서 비교한다 — 그냥 >=로 비교하면 sqlite의 정규 출력 형식과
    안 맞아서 같은 날짜의 과거 시각도 "범위 안"으로 잘못 걸린다(실측
    버그, cost.py의 같은 패턴과 동일한 원인).
    """
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN state = 'watched' THEN 1 ELSE 0 END),
             SUM(CASE WHEN state = 'skipped' THEN 1 ELSE 0 END),
             SUM(CASE WHEN state = 'not_interested' THEN 1 ELSE 0 END),
             COUNT(*)
           FROM video_states
           WHERE datetime(updated_at) >= datetime('now', ?)""",
        (f"-{since_hours} hours",),
    ).fetchone()
    watched, skipped, not_interested, total = (v or 0 for v in row)

    rows = conn.execute(
        """SELECT cat.id, cat.name, COUNT(*)
           FROM video_states vs
           JOIN videos v ON v.id = vs.video_id
           JOIN channel_categories cc ON cc.channel_id = v.channel_id
           JOIN categories cat ON cat.id = cc.category_id
           WHERE vs.state = 'watched' AND datetime(vs.updated_at) >= datetime('now', ?)
           GROUP BY cat.id
           ORDER BY COUNT(*) DESC""",
        (f"-{since_hours} hours",),
    ).fetchall()

    return {
        "since_hours": since_hours,
        "watched": watched,
        "skipped": skipped,
        "not_interested": not_interested,
        "state_changes_total": total,
        "by_category": {r[0]: {"name": r[1], "watched": r[2]} for r in rows},
    }
