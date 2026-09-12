"""eval/run_retrieval.py 단위 테스트 (docs/05-구현가이드.md Phase 7, step 31 확인).

`evaluate_retrieval`의 순수 채점 로직만 — 가짜 `search_fn`으로 recall@k와
MRR 계산이 맞는지 확인한다. 실제 L0 기준선 숫자는
`uv run python -m eval.run_retrieval`로 실측했다(golden_set.json이
실제 구독 데이터 기반이라 여기서 재현하지 않는다).
"""

from __future__ import annotations

from eval.run_retrieval import evaluate_retrieval


def test_recall_and_mrr_when_all_hits_at_rank_1():
    golden_set = [
        {"id": "q1", "query": "a", "relevant_video_ids": ["v1"]},
        {"id": "q2", "query": "b", "relevant_video_ids": ["v2"]},
    ]

    def search_fn(query: str, k: int) -> list[str]:
        return {"a": ["v1", "vX"], "b": ["v2", "vY"]}[query][:k]

    report = evaluate_retrieval(golden_set, search_fn, k=5)
    assert report.recall_at_k == 1.0
    assert report.mrr == 1.0


def test_recall_and_mrr_when_all_miss():
    golden_set = [{"id": "q1", "query": "a", "relevant_video_ids": ["v1"]}]

    def search_fn(query: str, k: int) -> list[str]:
        return ["vX", "vY"]

    report = evaluate_retrieval(golden_set, search_fn, k=5)
    assert report.recall_at_k == 0.0
    assert report.mrr == 0.0
    assert report.per_query[0]["hit"] is False
    assert report.per_query[0]["rank"] is None


def test_mrr_penalizes_lower_rank():
    golden_set = [{"id": "q1", "query": "a", "relevant_video_ids": ["v1"]}]

    def search_fn(query: str, k: int) -> list[str]:
        return ["vX", "vY", "v1"]  # 3등에 정답

    report = evaluate_retrieval(golden_set, search_fn, k=5)
    assert report.mrr == 1 / 3
    assert report.per_query[0]["rank"] == 3


def test_result_outside_k_counts_as_miss():
    golden_set = [{"id": "q1", "query": "a", "relevant_video_ids": ["v1"]}]

    def search_fn(query: str, k: int) -> list[str]:
        return ["vX"] * k  # v1은 아예 k개 안에 없음

    report = evaluate_retrieval(golden_set, search_fn, k=3)
    assert report.recall_at_k == 0.0
    assert report.mrr == 0.0


def test_report_level_and_mode_are_recorded():
    report = evaluate_retrieval([], lambda q, k: [], k=5, level="L0", mode="keyword")
    assert report.level == "L0"
    assert report.mode == "keyword"
    assert report.rerank is False
    assert report.timestamp_hit_rate is None  # L2 전까진 항상 None


def test_empty_golden_set_does_not_crash():
    report = evaluate_retrieval([], lambda q, k: [], k=5)
    assert report.recall_at_k == 0.0
    assert report.mrr == 0.0
    assert report.per_query == []
