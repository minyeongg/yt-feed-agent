"""검색 품질 평가 — recall@k, MRR (docs/05-구현가이드.md Phase 7 step 31; docs/03 §5.4-5.5).

`eval/golden_set.json`을 `core/search.py`(L0, FTS5)로 돌려서 기준선
숫자를 낸다. **이 숫자가 이후 모든 RAG 단계(Phase 8)의 판단 기준이다**
— L1(요약 임베딩)/하이브리드/리랭킹/L2(자막 청킹) 전부 "이 숫자보다
나아졌는가"로 살아남거나 버려진다(ADR-7, ADR-9).

실행:
    uv run python -m eval.run_retrieval
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ytfa.core.search import search_keyword
from ytfa.db import get_connection

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"


class RetrievalReport(BaseModel):
    level: Literal["L0", "L1", "L2"]
    mode: Literal["keyword", "semantic", "hybrid"]
    rerank: bool
    k: int
    recall_at_k: float
    mrr: float
    timestamp_hit_rate: float | None
    per_query: list[dict]


def load_golden_set(path: Path = GOLDEN_SET_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_retrieval(
    golden_set: list[dict],
    search_fn: Callable[[str, int], list[str]],
    *,
    k: int = 5,
    level: Literal["L0", "L1", "L2"] = "L0",
    mode: Literal["keyword", "semantic", "hybrid"] = "keyword",
    rerank: bool = False,
) -> RetrievalReport:
    """`search_fn(query, k) -> [video_id, ...]`(순위대로)를 골든셋으로 채점한다.

    recall@k: 질의당 "정답 중 top-k 안에 들어온 비율"의 평균(정답이
    보통 1개뿐이라 대부분 0 또는 1이 된다). MRR: 첫 정답이 나온 순위의
    역수(top-k 밖이면 0) 평균.
    """
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    per_query: list[dict] = []

    for item in golden_set:
        relevant = set(item["relevant_video_ids"])
        retrieved = search_fn(item["query"], k)

        hit_count = sum(1 for vid in retrieved if vid in relevant)
        recall = hit_count / len(relevant) if relevant else 0.0
        recalls.append(recall)

        rank = next((i + 1 for i, vid in enumerate(retrieved) if vid in relevant), None)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)

        per_query.append(
            {
                "id": item["id"],
                "query": item["query"],
                "hit": hit_count > 0,
                "rank": rank,
                "retrieved": retrieved,
                "relevant": sorted(relevant),
            }
        )

    return RetrievalReport(
        level=level,
        mode=mode,
        rerank=rerank,
        k=k,
        recall_at_k=sum(recalls) / len(recalls) if recalls else 0.0,
        mrr=sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0,
        timestamp_hit_rate=None,  # L2(타임스탬프 청킹, Phase 8)가 생기기 전까진 항상 None
        per_query=per_query,
    )


def make_l0_search_fn() -> Callable[[str, int], list[str]]:
    """`core/search.py`(FTS5)를 `evaluate_retrieval`이 기대하는 모양으로 감싼다."""

    def search_fn(query: str, k: int) -> list[str]:
        with get_connection() as conn:
            result = search_keyword(conn, query, limit=k)
        return [item["video"]["id"] for item in result["items"]]

    return search_fn


def main() -> None:
    golden_set = load_golden_set()
    report = evaluate_retrieval(golden_set, make_l0_search_fn(), k=5, level="L0", mode="keyword")

    print(f"L0(FTS5) 기준선 — 골든셋 {len(golden_set)}건, k={report.k}")
    print(f"  recall@{report.k}: {report.recall_at_k:.3f}")
    print(f"  MRR:       {report.mrr:.3f}")
    print()
    print("질의별 결과:")
    for pq in report.per_query:
        mark = "O" if pq["hit"] else "X"
        print(f"  [{mark}] {pq['id']}: {pq['query'][:40]} (rank={pq['rank']})")


if __name__ == "__main__":
    main()
