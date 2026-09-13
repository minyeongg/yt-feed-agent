"""회귀 테스트 — L0 검색 품질이 무너지면 빨간불 (docs/05-구현가이드.md
Phase 8 step 37; docs/03 §5.4-5.5).

`eval/golden_set.json` + 실제 DB로 recall@5를 다시 재서, step 31에서
커밋한 기준선(0.850)보다 0.05 이상 떨어지면 실패한다. `core/search.py`의
토큰화·질의 로직을 건드리는 변경이 조용히 검색 품질을 깎아먹는 걸 잡기
위한 안전망이다 — 인용(citation)이 아무리 잘 되게 프롬프트를 짜도,
애초에 검색이 정답을 못 찾아오면 근거 자체가 없다.

**실제 DB에 의존한다**(`eval/test_eval_retrieval.py`와 같은 이유 —
golden_set.json이 실제 구독 데이터를 대상으로 하기 때문에 여기서
가짜 DB로 재현하지 않는다). 골든셋 대상 영상이 지워지거나 제목이
바뀌어서 실패하는 건 "회귀"가 아니라 데이터가 바뀐 것 — 그럴 땐
`eval/run_retrieval.py`를 다시 돌려 숫자를 확인하고 BASELINE_RECALL_AT_5를
갱신한다.
"""

from __future__ import annotations

from eval.run_retrieval import evaluate_retrieval, load_golden_set, make_l0_search_fn

BASELINE_RECALL_AT_5 = 0.850  # step 31 실측 (golden_set.json 20건, k=5)
MAX_ALLOWED_DROP = 0.05


def test_l0_recall_has_not_regressed():
    golden_set = load_golden_set()
    report = evaluate_retrieval(golden_set, make_l0_search_fn(), k=5, level="L0", mode="keyword")

    threshold = BASELINE_RECALL_AT_5 - MAX_ALLOWED_DROP
    assert report.recall_at_k >= threshold, (
        f"L0 recall@5가 기준선({BASELINE_RECALL_AT_5})보다 {MAX_ALLOWED_DROP} 이상 떨어졌다: "
        f"{report.recall_at_k:.3f} < {threshold:.3f}. core/search.py의 토큰화·질의 로직 변경을 "
        f"의심할 것 (또는 골든셋 대상 영상/제목이 바뀐 것일 수도 있음)."
    )


def test_regression_check_actually_catches_a_degraded_search():
    """이 테스트 자체가 상시 통과하는 허수아비가 아님을 확인한다 —
    검색 함수를 일부러 망가뜨려서(항상 빈 결과) 진짜로 빨간불이 뜨는지
    본다(step 37 확인 기준: "프롬프트[검색 로직]를 일부러 망가뜨리면
    테스트가 빨간불")."""
    golden_set = load_golden_set()

    def broken_search_fn(query: str, k: int) -> list[str]:
        return []

    report = evaluate_retrieval(golden_set, broken_search_fn, k=5, level="L0", mode="keyword")

    assert report.recall_at_k < BASELINE_RECALL_AT_5 - MAX_ALLOWED_DROP
