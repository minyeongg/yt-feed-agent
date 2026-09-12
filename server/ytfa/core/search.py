"""L0 키워드 검색 (docs/05-구현가이드.md Phase 3, step 15; docs/03 §2.6).

FTS5 전문검색만 한다 — semantic/hybrid 모드는 임베딩이 생기는 Phase 8
(`rag/hybrid.py`)에서 붙는다. 지금 `mode`는 항상 `"keyword"`다.
"""

from __future__ import annotations

import json
from sqlite3 import Connection

NO_RESULT_HINT = "검색어를 더 일반적인 단어로 바꿔보세요"


def _to_fts_query(raw: str) -> str:
    """사용자 입력을 FTS5 MATCH 문법에 맞게 토큰마다 큰따옴표로 감싼다.

    하이픈·콜론 같은 FTS5 예약 문자가 섞여 있어도 문법 오류 없이
    리터럴로 취급되게 하기 위해서다. 토큰 사이는 기본 AND.
    """
    tokens = [t.replace('"', '""') for t in raw.split() if t.strip()]
    return " ".join(f'"{t}"' for t in tokens)


def search_keyword(conn: Connection, query: str, limit: int = 10, scope: str = "all") -> dict:
    """`GET /search&mode=keyword` — 제목/설명/요약/채널명 전문검색(FR-R7).

    `scope`(docs §2.6, MCP `search_videos` §3.1): `all`(기본) | `watched`
    (시청 완료한 영상 안에서만). `video`(특정 영상 안에서 검색, FR-R6)는
    자막 청킹(L2, Phase 8)이 있어야 의미가 있어서 아직 여기서 안 받는다
    — 호출부가 NOT_IMPLEMENTED로 거절한다.
    """
    fts_query = _to_fts_query(query)
    if not fts_query:
        return {"mode": "keyword", "query_used": query, "items": [], "hint": NO_RESULT_HINT}

    query_sql = """SELECT v.id, v.title, v.channel_id, c.title, v.thumbnail_url,
                  v.published_at, v.duration_sec, v.kind, v.summary, v.verdict,
                  bm25(videos_fts) AS rank
           FROM videos_fts
           JOIN videos v ON v.id = videos_fts.id
           JOIN channels c ON c.id = v.channel_id"""
    if scope == "watched":
        query_sql += " JOIN video_states vs ON vs.video_id = v.id AND vs.state = 'watched'"
    query_sql += " WHERE videos_fts MATCH ? ORDER BY rank LIMIT ?"

    rows = conn.execute(query_sql, (fts_query, limit)).fetchall()

    items = [
        {
            "video": {
                "id": r[0],
                "title": r[1],
                "url": f"https://youtu.be/{r[0]}",
                "channel": {"id": r[2], "title": r[3]},
                "published_at": r[5],
                "duration_sec": r[6],
                "kind": r[7],
                "thumbnail_url": r[4],
                "summary": r[8],
                "verdict": json.loads(r[9]) if r[9] else None,
            },
            # bm25()는 낮을수록 더 관련 있다 — 부호를 뒤집어 "높을수록 좋음"으로 맞춘다.
            "score": round(-r[10], 4),
            "matched_by": "keyword",
        }
        for r in rows
    ]

    return {"mode": "keyword", "query_used": query, "items": items, "hint": None if items else NO_RESULT_HINT}
