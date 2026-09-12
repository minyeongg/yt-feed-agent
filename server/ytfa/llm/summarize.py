"""요약 + 볼가치 판정 (docs/05-구현가이드.md Phase 2, step 11).

자막(있으면) → 한 줄 요약(80자 내외) + `verdict`. **영상당 1회만
생성**하고 영구 저장한다(`summarized_at`이 채워지면 다시 안 건드림) —
재실행해도 이미 요약된 영상은 LLM을 다시 안 부른다.

자막이 없으면 제목+설명만으로 축소 동작한다(ADR-3). 자막이 있어도
너무 길면(팟캐스트류) 앞부분만 잘라 보낸다 — 한 줄 요약에 영상 전체
정밀도가 필요하진 않다.

**중요: 이 모듈은 "무엇을 요약할지" 결정하지 않는다.** `summarize_video`는
video_id 하나를 받아서 요약할 뿐이고, 무엇을 요약할지는 호출자(step 12
`core/ranking.py`)가 정한다 — 여기에 "미요약 영상 전체 스캔" 같은 함수를
일부러 두지 않았다. 그런 함수가 있으면 언젠가 실수로 백로그 수천 건에
LLM을 돌리게 된다(ADR-8 FR-B1이 막으려는 바로 그 상황).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlite3 import Connection

from pydantic import BaseModel, Field

from ytfa.llm.cost import LLMClient

MAX_TRANSCRIPT_CHARS = 8000

SYSTEM_PROMPT = """유튜브 영상 하나를 보고 한국어로 짧게 요약하고 볼만한 가치를 판정하는
도우미다.

입력은 자막 전문이거나(있으면), 없으면 제목/설명뿐이다("자막 없음"
표시가 있으면 제목/설명만으로 추론하고, 불확실하면 one_liner에서
자연스럽게 드러낸다 — 과도한 확신 금지).

규칙:
- summary: 영상 내용을 80자 내외 한국어 한 줄로. 제목의 클릭베이트
  문구를 그대로 베끼지 말고 실제 내용을 요약한다.
- verdict.topics: 핵심 키워드 2~4개.
- verdict.level: 학습/기술 콘텐츠면 "입문"/"중급"/"고급", 그 외
  (브이로그·음악·뉴스 등)는 "일반".
- verdict.hands_on: 0.0(순수 시청/감상용)~1.0(따라하기 실습형). 학습
  콘텐츠가 아니면 0.0.
- verdict.one_liner: 이 영상을 볼지 말지 판단하는 데 도움되는 한 문장
  (예: "이론보다 구현 위주", "잔잔한 배경음악 위주, 정보성 낮음").
"""


class Verdict(BaseModel):
    topics: list[str] = Field(description="핵심 키워드 2~4개")
    level: str = Field(description='"입문"/"중급"/"고급" 또는 학습 콘텐츠가 아니면 "일반"')
    hands_on: float = Field(ge=0.0, le=1.0, description="0=순수 시청, 1=완전 실습형")
    one_liner: str = Field(description="볼지 말지 판단에 도움되는 한 문장")


class SummarizeResult(BaseModel):
    summary: str = Field(description="80자 내외 한국어 한 줄 요약")
    verdict: Verdict


def _transcript_text(conn: Connection, video_id: str, max_chars: int = MAX_TRANSCRIPT_CHARS) -> str | None:
    row = conn.execute("SELECT segments FROM transcripts WHERE video_id = ?", (video_id,)).fetchone()
    if row is None:
        return None
    segments = json.loads(row[0])
    text = " ".join(s["text"] for s in segments)
    if len(text) > max_chars:
        text = text[:max_chars] + " …(이하 생략)"
    return text


def summarize_video(
    client: LLMClient, conn: Connection, video_id: str
) -> tuple[SummarizeResult, float] | None:
    """영상 하나를 요약해 저장한다. 이미 요약됐으면(`summarized_at` 있음) None."""
    row = conn.execute(
        "SELECT title, description, summarized_at, transcript_status FROM videos WHERE id = ?",
        (video_id,),
    ).fetchone()
    if row is None or row[2] is not None:
        return None
    title, description, _summarized_at, transcript_status = row

    transcript_text = _transcript_text(conn, video_id) if transcript_status == "ok" else None
    source_block = (
        f"자막:\n{transcript_text}"
        if transcript_text
        else f"(자막 없음 — 제목/설명만으로 판단)\n설명: {description[:500]}"
    )
    user_content = f"제목: {title}\n{source_block}"

    response, cost = client.parse(
        model=client.cfg.llm.small_model,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        max_tokens=1024,
        output_format=SummarizeResult,
        kind="summarize",
        user_input=f"video:{video_id}",
        conn=conn,
    )
    result: SummarizeResult = response.parsed_output

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE videos SET summary = ?, verdict = ?, summarized_at = ? WHERE id = ?",
        (result.summary, json.dumps(result.verdict.model_dump(), ensure_ascii=False), now, video_id),
    )
    conn.commit()
    return result, cost


def summarize_videos(client: LLMClient, conn: Connection, video_ids: list[str]) -> dict:
    """명시적으로 주어진 video_id 목록만 요약한다(호출자가 후보를 이미 추렸다고 가정).

    이미 요약된 영상은 조용히 건너뛴다 — 재실행해도 비용이 늘지 않는다.
    """
    summarized = 0
    total_cost = 0.0
    for video_id in video_ids:
        outcome = summarize_video(client, conn, video_id)
        if outcome is not None:
            summarized += 1
            total_cost += outcome[1]
    return {"summarized": summarized, "requested": len(video_ids), "cost_usd": round(total_cost, 6)}
