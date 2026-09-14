"""rag/rerank.py 단위 테스트 (docs/05-구현가이드.md Phase 8, step 35 확인).

실제 Anthropic 호출은 하지 않는다 — 가짜 `LLMClient.parse()`로 재정렬
로직과 "실패해도 RRF 순서로 대체한다"는 계약만 검증한다.
"""

from __future__ import annotations

from ytfa.llm.cost import CostLimitExceeded
from ytfa.rag.rerank import MAX_CANDIDATES, RerankResponse, RerankScore, rerank


class _FakeConfig:
    class _Llm:
        small_model = "claude-haiku-4-5"

    llm = _Llm()


class _FakeResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


def _candidate(vid: str, title: str = "제목", excerpt: str = "본문") -> dict:
    return {"video": {"id": vid, "title": title, "summary": None}, "score": 0.1, "matched_by": "hybrid", "excerpt": excerpt}


class FakeLLMClient:
    def __init__(self, scores: list[RerankScore] | None = None, raise_exc: Exception | None = None):
        self.cfg = _FakeConfig()
        self._scores = scores
        self._raise = raise_exc
        self.calls = 0
        self.seen_user_contents: list[str] = []

    def parse(self, *, messages, **kwargs):
        self.calls += 1
        self.seen_user_contents.append(messages[0]["content"])
        if self._raise:
            raise self._raise
        return _FakeResponse(RerankResponse(scores=self._scores)), 0.001


def test_rerank_reorders_candidates_by_score():
    candidates = [_candidate("v0"), _candidate("v1"), _candidate("v2")]
    # v2(idx=2)가 가장 관련 있다고 점수 매김 — RRF 순서(v0,v1,v2)와 반대로 나와야 함
    client = FakeLLMClient(scores=[RerankScore(idx=0, score=0.1), RerankScore(idx=1, score=0.5), RerankScore(idx=2, score=0.9)])

    result = rerank(client, None, "질의", candidates, top_k=3)

    assert [item["video"]["id"] for item in result] == ["v2", "v1", "v0"]
    assert client.calls == 1  # 후보 전체를 한 번의 호출로 묶었는지(§4.2 리랭커 규약)


def test_rerank_respects_top_k():
    candidates = [_candidate(f"v{i}") for i in range(5)]
    scores = [RerankScore(idx=i, score=i / 4.0) for i in range(5)]
    client = FakeLLMClient(scores=scores)

    result = rerank(client, None, "질의", candidates, top_k=2)

    assert len(result) == 2
    assert [item["video"]["id"] for item in result] == ["v4", "v3"]


def test_rerank_truncates_to_max_candidates():
    candidates = [_candidate(f"v{i}") for i in range(MAX_CANDIDATES + 10)]
    scores = [RerankScore(idx=i, score=1.0) for i in range(MAX_CANDIDATES)]
    client = FakeLLMClient(scores=scores)

    rerank(client, None, "질의", candidates, top_k=5)

    # 프롬프트에 실제로 몇 개 후보가 들어갔는지 — MAX_CANDIDATES를 넘지 않아야 함
    sent = client.seen_user_contents[0]
    assert f"[{MAX_CANDIDATES - 1}]" in sent
    assert f"[{MAX_CANDIDATES}]" not in sent


def test_rerank_falls_back_to_rrf_order_on_parse_failure():
    """§4.2 리랭커 규약: 파싱 실패 시 리랭킹을 건너뛰고 RRF 순서를 그대로 쓴다."""
    candidates = [_candidate("v0"), _candidate("v1"), _candidate("v2")]
    client = FakeLLMClient(raise_exc=ValueError("모델이 JSON이 아닌 걸 뱉음"))

    result = rerank(client, None, "질의", candidates, top_k=3)

    assert [item["video"]["id"] for item in result] == ["v0", "v1", "v2"]  # 원래 순서 그대로


def test_rerank_falls_back_on_cost_limit_exceeded():
    candidates = [_candidate("v0"), _candidate("v1")]
    client = FakeLLMClient(raise_exc=CostLimitExceeded("일일", 1.0, 0.5))

    result = rerank(client, None, "질의", candidates, top_k=2)

    assert [item["video"]["id"] for item in result] == ["v0", "v1"]


def test_rerank_falls_back_when_scores_empty():
    candidates = [_candidate("v0"), _candidate("v1")]
    client = FakeLLMClient(scores=[])

    result = rerank(client, None, "질의", candidates, top_k=2)

    assert [item["video"]["id"] for item in result] == ["v0", "v1"]


def test_rerank_missing_idx_treated_as_low_score():
    """모델이 일부 후보 점수를 빠뜨려도(idx가 scores에 없음) 죽지 않고
    그 후보를 최하위로 취급해야 한다."""
    candidates = [_candidate("v0"), _candidate("v1"), _candidate("v2")]
    client = FakeLLMClient(scores=[RerankScore(idx=1, score=0.9)])  # 0, 2는 빠짐

    result = rerank(client, None, "질의", candidates, top_k=3)

    assert result[0]["video"]["id"] == "v1"  # 점수 받은 게 1등


def test_rerank_empty_candidates_returns_empty_without_calling_llm():
    client = FakeLLMClient(scores=[])
    assert rerank(client, None, "질의", [], top_k=5) == []
    assert client.calls == 0
