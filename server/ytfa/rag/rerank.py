"""리랭킹 — 후보 재점수화 (docs/05-구현가이드.md Phase 8 step 35; docs/03 §4.2 Reranker).

하이브리드(RRF)가 순위를 매기지만, RRF 점수 자체는 "어느 쪽에서 몇
등이었냐"만 보고 실제 질의-후보 관련성은 안 본다. 소형 모델 **1회
호출**로 후보 전체를 한 번에 점수화해서 그 격차를 메운다 — 후보마다
LLM을 따로 부르면 30번 호출이 되니(§4.2 리랭커 규약이 명시적으로 금지
하는 바로 그것) 반드시 한 번에 묶는다.

**실패해도 검색은 살아있어야 한다(§4.2 리랭커 규약)** — 파싱 실패든
비용 상한이든 무슨 이유로든 리랭킹이 안 되면 원래 순서(RRF)를 그대로
쓴다. 리랭킹은 "있으면 더 좋고 없어도 검색 자체는 되는" 부가 기능이지,
검색의 필수 경로가 아니다.
"""

from __future__ import annotations

import logging
from sqlite3 import Connection

from pydantic import BaseModel, Field

from ytfa.llm.cost import LLMClient

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 30  # docs §4.2 rerank(candidates, top_k) 규약의 후보 풀 크기
EXCERPT_PREVIEW_CHARS = 200

RERANK_SYSTEM_PROMPT = """검색 후보 목록과 사용자 질의가 주어진다. 각 후보가
질의에 얼마나 관련 있는지 0.0(전혀 관련 없음)~1.0(질의에 정확히 답함) 사이
점수로 평가한다. 후보가 나열된 순서에 얽매이지 말고 내용만 보고 판단한다.
모든 후보에 반드시 점수를 매긴다(빠뜨리지 않는다)."""


class RerankScore(BaseModel):
    idx: int = Field(description="후보 목록의 인덱스(0부터)")
    score: float = Field(ge=0.0, le=1.0)


class RerankResponse(BaseModel):
    scores: list[RerankScore]


def _candidate_text(item: dict, idx: int) -> str:
    video = item["video"]
    body = item.get("excerpt") or video.get("summary") or ""
    return f"[{idx}] {video['title']}\n{body[:EXCERPT_PREVIEW_CHARS]}"


def rerank(client: LLMClient, conn: Connection, query: str, candidates: list[dict], top_k: int = 10) -> list[dict]:
    """`candidates`(예: `search_hybrid`의 `items`, RRF 순서로 정렬돼 있다고
    가정)를 질의 관련성으로 재점수화한다.

    무슨 이유로든(파싱 실패, 비용 상한, 네트워크 오류) 실패하면 예외를
    삼키고 `candidates[:top_k]`(RRF 순서 그대로)를 돌려준다 — 리랭킹이
    검색 자체를 깨뜨리면 안 된다."""
    if not candidates:
        return []
    pool = candidates[:MAX_CANDIDATES]

    try:
        listing = "\n\n".join(_candidate_text(item, i) for i, item in enumerate(pool))
        user_content = f"질의: {query}\n\n후보:\n{listing}"
        response, _cost = client.parse(
            model=client.cfg.llm.small_model,
            system=RERANK_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=1024,
            output_format=RerankResponse,
            kind="rerank",
            user_input=f"query:{query}",
            conn=conn,
        )
        result: RerankResponse = response.parsed_output
        score_by_idx = {s.idx: s.score for s in result.scores}
        if not score_by_idx:
            raise ValueError("빈 scores — 파싱은 됐지만 점수가 하나도 없음")
        ranked = sorted(enumerate(pool), key=lambda pair: score_by_idx.get(pair[0], -1.0), reverse=True)
        return [item for _, item in ranked[:top_k]]
    except Exception:
        logger.exception("리랭킹 실패 — RRF 순서로 대체한다")
        return pool[:top_k]
