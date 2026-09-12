"""SSE 이벤트 스트리밍 (docs/05-구현가이드.md Phase 6, step 25-26; docs/03 §2.7).

`graph.astream_events()`(LangGraph v2 이벤트 스트림, 실측 확인: 모델
토큰은 `on_chat_model_stream`, 툴 실행은 `on_tool_start`/`on_tool_end`,
`interrupt()`는 이름이 `"LangGraph"`인 체인의 `on_chain_stream`에
`{"__interrupt__": (Interrupt(...),)}` 청크로 온다)를 API 계약의
이벤트(run_started/text/tool_call/tool_result/approval_required/
run_end)로 바꾼다. `compaction`(step 28)/`citations`(step 37)는 아직
없다.

**승인 대기 중에도 연결이 끊기지 않는다**(docs §2.7 "스트림은 끊기지
않는다") — `interrupt()`를 만나면 `approval_required`를 내보내고 그
자리에서 `/agent/approve`의 결정을 기다린다(`agent/approvals.py`).
결정이 오면 `Command(resume=decision)`으로 **같은 `astream_events`
스트림이 아니라 새로 하나 더 부르는 방식**으로 이어간다 — LangGraph는
재개를 그렇게 하게 만들어져 있다(실측 확인, 같은 스트림 안에서 이어지지
않는다).

**모델 청크의 content는 문자열이 아니라 블록 리스트다**(`message_utils.
extract_text`와 같은 이유 — Sonnet 5는 thinking/tool_use 블록이 섞여
온다). `text` 이벤트로는 `type: "text"` 블록만 내보낸다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from ytfa.agent import approvals
from ytfa.agent.permissions import requires_approval
from ytfa.db import get_connection


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


def _extract_interrupt(ev: dict) -> dict | None:
    """이벤트가 `interrupt()`로 인한 정지면 그 payload(dict)를, 아니면 None을 반환한다."""
    if ev["event"] != "on_chain_stream" or ev.get("name") != "LangGraph":
        return None
    chunk = ev["data"].get("chunk")
    if not isinstance(chunk, dict) or "__interrupt__" not in chunk:
        return None
    interrupts = chunk["__interrupt__"]
    return interrupts[0].value if interrupts else None


async def stream_chat(graph: Any, thread_id: str, message: str, conn: Any | None = None) -> AsyncIterator[SSEEvent]:
    """대화 한 턴을 실행하며 SSE 이벤트를 순서대로 낸다. 승인 대기도 이 안에서 처리한다.

    `conn`을 넘기면 그 커넥션을 그대로 쓴다(테스트에서 임시 DB를 주입하기
    위한 용도) — 안 넘기면 앱 DB에 직접 연다.
    """
    run_id = f"r_{uuid.uuid4().hex[:12]}"
    yield SSEEvent("run_started", {"run_id": run_id, "thread_id": thread_id})

    seq = 0
    steps = 0
    stopped_reason = "end_turn"
    config = {"configurable": {"thread_id": thread_id}}

    with (nullcontext(conn) if conn is not None else get_connection()) as conn:
        # 그래프 자체가 승인 지점에 멈춰있는지 직접 물어본다 — 우리 장부
        # (pending_approvals)만 보고 판단하면 "이미 결정은 났는데 그래프엔
        # 아직 Command(resume=...)를 안 넣어준" 상태(서버가 죽어서
        # /agent/chat 스트림이 결정을 못 받은 경우)를 놓친다(실제로 겪은
        # 버그 — 재시작 재현 테스트가 무한 대기에 빠졌다).
        graph_state = await graph.aget_state(config)
        if graph_state.next:  # 비어있지 않으면 뭔가에 멈춰있는 것
            latest = approvals.find_latest_for_thread(conn, thread_id)
            if latest is None:
                stopped_reason = "error"
                yield SSEEvent("text", {"delta": "\n[오류: 승인 대기 상태인데 기록을 못 찾음]"})
                yield SSEEvent("run_end", {"run_id": run_id, "steps": steps, "stopped_reason": stopped_reason})
                return
            if latest["decision"] is None:
                seq += 1
                yield SSEEvent(
                    "approval_required",
                    {"seq": seq, "tool": latest["tool"], "approval_id": latest["approval_id"], "summary": latest["summary"]},
                )
                decision = await approvals.wait_for_decision(conn, latest["approval_id"])
            else:
                # 재시작 사이에 이미 /agent/approve가 왔다 — 그래프에 아직 안 알려줬을 뿐.
                decision = latest["decision"]
            graph_input: Any = Command(resume=decision)
        else:
            graph_input = {"messages": [{"role": "user", "content": message}]}

        try:
            while True:
                interrupt_payload: dict | None = None

                async for ev in graph.astream_events(graph_input, config, version="v2"):
                    interrupt_payload = _extract_interrupt(ev)
                    if interrupt_payload is not None:
                        break

                    kind = ev["event"]
                    if kind == "on_chat_model_stream":
                        delta = text_delta(ev["data"]["chunk"].content)
                        if delta:
                            yield SSEEvent("text", {"delta": delta})
                    elif kind == "on_tool_start":
                        seq += 1
                        tool_name = ev["name"]
                        yield SSEEvent(
                            "tool_call",
                            {
                                "seq": seq,
                                "tool": tool_name,
                                "args_summary": ev["data"].get("input", {}),
                                "decision": "user_allowed" if requires_approval(tool_name) else "auto_allow",
                            },
                        )
                    elif kind == "on_tool_end":
                        steps += 1
                        yield SSEEvent(
                            "tool_result",
                            {"seq": seq, "ok": True, "result_size": result_size(ev["data"].get("output"))},
                        )

                if interrupt_payload is None:
                    break  # 정상 종료 — 더 이상 재개할 게 없다

                seq += 1
                approval_id = approvals.create_pending(
                    conn, thread_id, interrupt_payload["tool"], interrupt_payload["args"], interrupt_payload["summary"]
                )
                yield SSEEvent(
                    "approval_required",
                    {"seq": seq, "tool": interrupt_payload["tool"], "approval_id": approval_id, "summary": interrupt_payload["summary"]},
                )
                decision = await approvals.wait_for_decision(conn, approval_id)
                graph_input = Command(resume=decision)
        except Exception as exc:  # noqa: BLE001 — 스트림 도중 예외도 정상적으로 run_end까지 보내야 한다
            stopped_reason = "error"
            yield SSEEvent("text", {"delta": f"\n[오류: {exc}]"})

    yield SSEEvent("run_end", {"run_id": run_id, "steps": steps, "stopped_reason": stopped_reason})
