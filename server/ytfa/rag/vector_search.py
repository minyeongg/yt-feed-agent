"""L1 의미 검색 — numpy 브루트포스 코사인 (docs/05-구현가이드.md Phase 8
step 33; docs/03 §2.6, §4.2 VectorIndex.search).

영상 몇천 건 규모에선 브루트포스가 ANN 인덱스보다 단순하고 충분히
빠르다 — 필요해지기 전엔 복잡한 걸 안 들인다는 ADR-7 정신 그대로.
"""

from __future__ import annotations

import json
from sqlite3 import Connection

import numpy as np

from ytfa.core.search import NO_RESULT_HINT
from ytfa.rag.embedder import Embedder


def search_semantic(conn: Connection, embedder: Embedder, query: str, limit: int = 10, scope: str = "all") -> dict:
    """`GET /search&mode=semantic` — 요약 임베딩 코사인 검색."""
    query_sql = """SELECT e.chunk_id, e.vector, c.video_id
                   FROM embeddings e JOIN chunks c ON c.id = e.chunk_id"""
    if scope == "watched":
        query_sql += " JOIN video_states vs ON vs.video_id = c.video_id AND vs.state = 'watched'"

    rows = conn.execute(query_sql).fetchall()
    if not rows:
        return {"mode": "semantic", "query_used": query, "items": [], "hint": NO_RESULT_HINT}

    qvec = embedder.embed([query])[0]
    matrix = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    scores = matrix @ qvec  # 둘 다 L2 정규화돼 있어 내적 = 코사인
    video_ids = [r[2] for r in rows]

    # video_id별 최고 점수만 남긴다. L1은 영상당 청크가 1개라 지금은
    # 중복이 없지만, L2(자막 청킹)가 붙으면 같은 영상의 청크가 여럿
    # 매치될 수 있어서 미리 대비해 둔다.
    best: dict[str, float] = {}
    for vid, score in zip(video_ids, scores):
        s = float(score)
        if vid not in best or s > best[vid]:
            best[vid] = s

    top = sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    if not top:
        return {"mode": "semantic", "query_used": query, "items": [], "hint": NO_RESULT_HINT}

    placeholders = ",".join("?" * len(top))
    video_rows = conn.execute(
        f"""SELECT v.id, v.title, v.channel_id, c.title, v.thumbnail_url,
                   v.published_at, v.duration_sec, v.kind, v.summary, v.verdict
            FROM videos v JOIN channels c ON c.id = v.channel_id
            WHERE v.id IN ({placeholders})""",
        [vid for vid, _ in top],
    ).fetchall()
    by_id = {r[0]: r for r in video_rows}

    items = []
    for vid, score in top:
        r = by_id.get(vid)
        if r is None:  # 인덱싱된 뒤 영상이 삭제된 경우 방어
            continue
        items.append(
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
                "score": round(score, 4),
                "matched_by": "semantic",
            }
        )

    return {"mode": "semantic", "query_used": query, "items": items, "hint": None if items else NO_RESULT_HINT}
