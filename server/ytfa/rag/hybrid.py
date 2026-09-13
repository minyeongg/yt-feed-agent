"""하이브리드 검색 — RRF (docs/05-구현가이드.md Phase 8 step 34; docs/03 §4.2 rrf_fuse).

L1 평가에서 확인한 것: 의미 검색은 "큰 주제"는 잘 잡지만 같은 포맷의
연속 시리즈물 안에서 구체적인 소재까지는 못 가른다(q006 "ARS 안내영어"
사례 — 정답의 코사인은 정상이었지만 같은 채널의 비슷한 영상들이 더
높은 점수를 받았다). 반대로 키워드 검색은 정확한 단어가 없으면 아예
못 찾는다(조사 붙은 단어, 줄임말). RRF는 두 순위 목록을 점수 정규화
없이 합쳐서 — 어느 한쪽이 1등을 놓쳐도 둘 다 상위권에 걸리는 후보가
위로 올라오게 한다.
"""

from __future__ import annotations

from sqlite3 import Connection

from ytfa.core.search import NO_RESULT_HINT, search_keyword
from ytfa.rag.embedder import Embedder
from ytfa.rag.vector_search import search_semantic

DEFAULT_POOL = 30
DEFAULT_RRF_K = 60


def rrf_fuse(keyword_hits: list[str], semantic_hits: list[str], k: int = DEFAULT_RRF_K) -> list[tuple[str, float]]:
    """score(id) = Σ 1/(k + rank(id)), rank는 1부터. 점수 정규화가 불필요해서
    bm25(음수, 스케일 제각각)와 코사인(0~1)처럼 단위가 다른 두 점수를
    그냥 더하는 실수를 피할 수 있다."""
    scores: dict[str, float] = {}
    for hits in (keyword_hits, semantic_hits):
        for rank, vid in enumerate(hits, start=1):
            scores[vid] = scores.get(vid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def search_hybrid(
    conn: Connection,
    embedder: Embedder,
    query: str,
    limit: int = 10,
    scope: str = "all",
    pool: int = DEFAULT_POOL,
) -> dict:
    """`GET /search&mode=hybrid` — FTS5 상위 `pool` + 임베딩 상위 `pool`을 RRF로 결합."""
    kw_result = search_keyword(conn, query, limit=pool, scope=scope)
    sem_result = search_semantic(conn, embedder, query, limit=pool, scope=scope)

    kw_by_id = {item["video"]["id"]: item for item in kw_result["items"]}
    sem_by_id = {item["video"]["id"]: item for item in sem_result["items"]}

    fused = rrf_fuse(list(kw_by_id), list(sem_by_id))[:limit]

    items = []
    for vid, score in fused:
        kw_item = kw_by_id.get(vid)
        sem_item = sem_by_id.get(vid)
        matched_by = "hybrid" if kw_item and sem_item else ("keyword" if kw_item else "semantic")
        base = kw_item or sem_item
        items.append(
            {
                "video": base["video"],
                "score": round(score, 5),
                "matched_by": matched_by,
                # excerpt/start_sec/deep_link는 semantic 쪽 청크에서만 나온다
                # (L2 자막 청크가 타임스탬프를 갖는 유일한 경로) — 키워드만
                # 걸린 영상은 일반 링크로 대체한다.
                "excerpt": sem_item["excerpt"] if sem_item else None,
                "start_sec": sem_item["start_sec"] if sem_item else None,
                "deep_link": sem_item["deep_link"] if sem_item else f"https://youtu.be/{vid}",
            }
        )

    return {"mode": "hybrid", "query_used": query, "items": items, "hint": None if items else NO_RESULT_HINT}
