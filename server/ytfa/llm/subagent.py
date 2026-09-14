"""서브에이전트 — 여러 영상 병렬 질의응답 (docs/05-구현가이드.md Phase 9
step 39; docs/03 §3.2 `summarize_videos`).

"서브에이전트"라고 부르지만 각 영상 처리는 툴 호출 없이 구조화 출력
한 번으로 끝난다 — supervisor(이 모듈)가 후보를 이미 다 골라놨고
필요한 컨텍스트(자막/설명)도 DB에 이미 있어서, 워커가 따로 "찾아보는"
단계를 밟을 필요가 없다. MCP 툴 왕복을 또 거치면 5건이 병렬이어도
그만큼 느려지기만 한다. 워커에 애초에 툴을 안 쥐여주므로 "읽기 툴만"
요구 조건은 자동으로 성립한다(쓰기 툴은 아예 없음).

병렬은 `ThreadPoolExecutor`로 돌린다 — 시간이 API 왕복 대기(I/O)에
쓰이지 CPU를 많이 안 써서 스레드로 충분하다(비동기 Anthropic 클라이언트를
새로 끌어올 이유가 없다, ADR류 "필요해지기 전엔 복잡한 걸 안 들인다"
원칙 그대로). 각 워커는 **자기 SQLite 커넥션**을 연다 — 커넥션 객체
하나를 여러 스레드가 같이 쓰는 걸 피한다.

부분 실패 허용: 한 영상이 실패해도(자막 없음, 파싱 실패, 비용 상한 등)
그 항목만 `status: "failed"`로 표시하고 나머지는 정상 반환한다.

부모 컨텍스트 격리: `summarize_videos_subagent()` 하나가 에이전트
그래프 입장에선 **툴 호출 1번**이다 — 내부에서 최대 5번의 LLM 호출이
오가도 그 프롬프트·응답은 부모의 메시지 히스토리에 전혀 안 남고,
최종 `{"results": [...]}` 하나만 ToolMessage로 얹힌다.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from sqlite3 import Connection

from pydantic import BaseModel, Field

from ytfa.db import get_connection
from ytfa.llm.cost import CostLimitExceeded, LLMClient
from ytfa.llm.summarize import MAX_TRANSCRIPT_CHARS, _transcript_text

# 워커마다 자기 커넥션을 열 때 쓸 팩토리. 기본은 실제 앱 DB(`get_connection`)지만,
# 테스트에서는 임시 파일 DB를 가리키는 팩토리를 주입한다 — 스레드 여러 개가
# 같은 파일을 각자 커넥션으로 열어야 해서(`:memory:`는 스레드끼리 공유가
# 안 됨) 임시 "파일" DB가 필요하다.
ConnFactory = Callable[[], AbstractContextManager[Connection]]

MAX_VIDEO_IDS = 5  # docs §3.2 summarize_videos.video_ids maxItems

SYSTEM_PROMPT_TEMPLATE = """유튜브 영상 하나의 내용(자막 또는 제목/설명)을 보고
{question_instruction} 한국어로 답한다.

입력에 자막이 없으면 제목/설명만으로 판단하고, 확신이 부족하면 summary에서
자연스럽게 드러낸다(과도한 확신 금지). 내용에 없는 건 지어내지 않는다.

규칙:
- summary: 2~3문장. 질문이 있으면 그 질문에 대한 직접적인 답으로.
- key_points: 핵심 포인트 1~5개, 각각 한 문장.
- citations: summary/key_points의 근거가 된 자막 구간의 시작 시각(초).
  자막이 없으면 빈 배열.
"""


class Citation(BaseModel):
    start_sec: int | None = Field(default=None, description="근거가 된 자막 구간 시작 초(자막 없으면 null)")


class VideoAnswer(BaseModel):
    summary: str = Field(description="2~3문장 요약 또는 질문에 대한 답")
    key_points: list[str] = Field(description="핵심 포인트 1~5개")
    citations: list[Citation] = Field(default_factory=list)


def _answer_one_video(client: LLMClient, video_id: str, question: str | None, conn_factory: ConnFactory) -> dict:
    """영상 하나를 처리한다. 무슨 일이 있어도 예외를 밖으로 던지지 않고
    `status: "failed"`로 감싼다 — 병렬 배치 전체가 한 건 때문에 죽으면
    부분 실패 허용이 성립하지 않는다."""
    try:
        with conn_factory() as conn:
            row = conn.execute(
                "SELECT title, description, transcript_status FROM videos WHERE id = ?", (video_id,)
            ).fetchone()
            if row is None:
                return {"video_id": video_id, "status": "failed", "error": "NOT_FOUND"}
            title, description, transcript_status = row

            transcript_text = (
                _transcript_text(conn, video_id, MAX_TRANSCRIPT_CHARS) if transcript_status == "ok" else None
            )
            source_block = (
                f"자막:\n{transcript_text}"
                if transcript_text
                else f"(자막 없음 — 제목/설명만으로 판단)\n설명: {(description or '')[:500]}"
            )
            question_instruction = f'다음 질문에 답하기 위해("{question}")' if question else "핵심 내용을 요약하기 위해"
            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(question_instruction=question_instruction)
            user_content = f"제목: {title}\n{source_block}"

            response, _cost = client.parse(
                model=client.cfg.llm.small_model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=1024,
                output_format=VideoAnswer,
                kind="subagent_summarize",
                user_input=f"video:{video_id}" + (f" q:{question}" if question else ""),
                conn=conn,
            )
        result: VideoAnswer = response.parsed_output
        return {
            "video_id": video_id,
            "status": "ok",
            "summary": result.summary,
            "key_points": result.key_points,
            "citations": [c.model_dump() for c in result.citations],
        }
    except CostLimitExceeded as exc:
        return {"video_id": video_id, "status": "failed", "error": f"COST_LIMIT_EXCEEDED: {exc}"}
    except Exception as exc:  # noqa: BLE001 — 의도적으로 전부 잡는다(부분 실패 허용이 이 함수의 계약)
        return {"video_id": video_id, "status": "failed", "error": str(exc)}


def summarize_videos_subagent(
    client: LLMClient,
    video_ids: list[str],
    question: str | None = None,
    conn_factory: ConnFactory = get_connection,
) -> dict:
    """최대 5건까지 병렬로 처리한다(docs §3.2, maxItems=5 초과분은 자른다).

    supervisor 패턴: 이 함수가 supervisor, `_answer_one_video` 각 호출이
    worker다. 반환 순서는 `video_ids` 순서를 그대로 지킨다(완료 순서가
    아니라) — 호출자가 어떤 영상이 몇 번째 결과인지 헷갈리지 않게.

    `conn_factory`는 기본이 실제 앱 DB(`get_connection`)다 — 테스트가
    임시 파일 DB를 가리키는 팩토리를 넣어서 실제 데이터를 안 건드리고
    검증한다.
    """
    if not video_ids:
        return {"results": []}
    video_ids = video_ids[:MAX_VIDEO_IDS]

    results: list[dict | None] = [None] * len(video_ids)
    with ThreadPoolExecutor(max_workers=len(video_ids)) as executor:
        future_to_index = {
            executor.submit(_answer_one_video, client, vid, question, conn_factory): i
            for i, vid in enumerate(video_ids)
        }
        for future in as_completed(future_to_index):
            results[future_to_index[future]] = future.result()

    return {"results": results}
