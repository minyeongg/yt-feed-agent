"""SSE 이벤트 스트리밍 (docs/05-구현가이드.md Phase 6, step 25; docs/03 §2.7).

`graph.astream_events()`(LangGraph v2 이벤트 스트림, 실측 확인: 모델
토큰은 `on_chat_model_stream`, 툴 실행은 `on_tool_start`/`on_tool_end`)를
API 계약의 이벤트 5종(run_started/text/tool_call/tool_result/run_end)으로
바꾼다. `approval_required`(step 26)/`compaction`(step 28)/`citations`
(step 37)는 아직 없다 — 해당 단계에서 이 함수에 분기를 추가한다.

**모델 청크의 content는 문자열이 아니라 블록 리스트다**(`message_utils.
extract_text`와 같은 이유 — Sonnet 5는 thinking/tool_use 블록이 섞여
온다). `text` 이벤트로는 `type: "text"` 블록만 내보낸다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass
class SSEEvent:
    event: str
    data: dict[str, Any]

    def encode(self) -> str:
        return f"event: {self.event}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


def text_delta(chunk_content: Any) -> str | None:
    """모델 스트리밍 청크에서 text 블록만 뽑는다. tool_use/thinking 블록은 버린다."""
    if isinstance(chunk_content, str):
        return chunk_content or None
    if isinstance(chunk_content, list):
        parts = [b.get("text", "") for b in chunk_content if isinstance(b, dict) and b.get("type") == "text"]
        joined = "".join(parts)
        return joined or None
    return None


def result_size(tool_output: Any) -> int | None:
    """툴 결과에서 `items` 배열 길이를 뽑는다(우리 툴 대부분의 공통 모양). 안 맞으면 None.

    로컬 함수 툴의 `ToolMessage.content`는 JSON 문자열 그대로지만, MCP
    툴(step 23)은 `[{"type": "text", "text": "...json..."}]` 형태의
    콘텐츠 블록 리스트로 온다(MCP 프로토콜 자체가 그렇게 생겼다, 실측
    확인) — 그래서 먼저 텍스트를 뽑아낸 뒤에 파싱한다.
    """
    content = getattr(tool_output, "content", tool_output)
    if isinstance(content, list):
        text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        content = "".join(text_parts)
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return None
    items = parsed.get("items") if isinstance(parsed, dict) else None
    return len(items) if isinstance(items, list) else None


async def stream_chat(graph: Any, thread_id: str, message: str) -> AsyncIterator[SSEEvent]:
    """대화 한 턴을 실행하며 SSE 이벤트를 순서대로 낸다."""
    run_id = f"r_{uuid.uuid4().hex[:12]}"
    yield SSEEvent("run_started", {"run_id": run_id, "thread_id": thread_id})

    seq = 0
    steps = 0
    stopped_reason = "end_turn"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        async for ev in graph.astream_events(
            {"messages": [{"role": "user", "content": message}]}, config, version="v2"
        ):
            kind = ev["event"]
            if kind == "on_chat_model_stream":
                delta = text_delta(ev["data"]["chunk"].content)
                if delta:
                    yield SSEEvent("text", {"delta": delta})
            elif kind == "on_tool_start":
                seq += 1
                yield SSEEvent(
                    "tool_call",
                    {
                        "seq": seq,
                        "tool": ev["name"],
                        "args_summary": ev["data"].get("input", {}),
                        # 승인 정책(step 26)이 아직 없다 — 지금은 전부 자동 허용된다.
                        "decision": "auto_allow",
                    },
                )
            elif kind == "on_tool_end":
                steps += 1
                yield SSEEvent(
                    "tool_result",
                    {"seq": seq, "ok": True, "result_size": result_size(ev["data"].get("output"))},
                )
    except Exception as exc:  # noqa: BLE001 — 스트림 도중 예외도 정상적으로 run_end까지 보내야 한다
        stopped_reason = "error"
        yield SSEEvent("text", {"delta": f"\n[오류: {exc}]"})

    yield SSEEvent("run_end", {"run_id": run_id, "steps": steps, "stopped_reason": stopped_reason})
