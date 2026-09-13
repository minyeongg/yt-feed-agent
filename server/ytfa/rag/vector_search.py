"""L1/L2 의미 검색 — numpy 브루트포스 코사인 (docs/05-구현가이드.md Phase 8
step 33/36; docs/03 §2.6, §4.2 VectorIndex.search).

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
    """`GET /search&mode=semantic` — 임베딩 코사인 검색.

    L1(요약) 청크는 `start_sec`이 없어 `deep_link`가 그냥 영상 링크다.
    L2(자막) 청크가 최고점이면 `start_sec`이 실려서
    `youtu.be/xxx?t=743` 형태의 타임스탬프 딥링크가 나온다(step 36 확인
    기준).
    """
    query_sql = """SELECT e.chunk_id, e.vector, c.video_id, c.text, c.start_sec
                   FROM embeddings e JOIN chunks c ON c.id = e.chunk_id"""
    if scope == "watched":
        query_sql += " JOIN video_states vs ON vs.video_id = c.video_id AND vs.state = 'watched'"

    rows = conn.execute(query_sql).fetchall()
    if not rows:
        return {"mode": "semantic", "query_used": query, "items": [], "hint": NO_RESULT_HINT}

    qvec = embedder.embed([query])[0]
    matrix = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    scores = matrix @ qvec  # 둘 다 L2 정규화돼 있어 내적 = 코사인

    # video_id별 최고 점수 청크만 남긴다 — L2가 붙으면 같은 영상의 청크가
    # 여럿 매치될 수 있어서, 그중 가장 잘 맞은 청크의 text/start_sec을
    # excerpt/딥링크로 쓴다.
    best: dict[str, dict] = {}
    for row, score in zip(rows, scores):
        vid = row[2]
        s = float(score)
        if vid not in best or s > best[vid]["score"]:
            best[vid] = {"score": s, "text": row[3], "start_sec": row[4]}

    top = sorted(best.items(), key=lambda kv: kv[1]["score"], reverse=True)[:limit]
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
    for vid, info in top:
        r = by_id.get(vid)
        if r is None:  # 인덱싱된 뒤 영상이 삭제된 경우 방어
            continue
        start_sec = info["start_sec"]
        deep_link = f"https://youtu.be/{vid}" + (f"?t={start_sec}" if start_sec is not None else "")
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
                "score": round(info["score"], 4),
                "matched_by": "semantic",
                "excerpt": info["text"][:160],
                "start_sec": start_sec,
                "deep_link": deep_link,
            }
        )

    return {"mode": "semantic", "query_used": query, "items": items, "hint": None if items else NO_RESULT_HINT}
