"""SSE 이벤트 스트리밍 (docs/05-구현가이드.md Phase 6, step 25-28; Phase 7 step 29-30; docs/03 §2.7, §5.3).

`graph.astream_events()`(LangGraph v2 이벤트 스트림, 실측 확인: 모델
토큰은 `on_chat_model_stream`, 툴 실행은 `on_tool_start`/`on_tool_end`,
`interrupt()`는 이름이 `"LangGraph"`인 체인의 `on_chain_stream`에
`{"__interrupt__": (Interrupt(...),)}` 청크로 온다, `compact` 노드가
실제로 압축했으면 이름이 `"compact"`인 체인의 `on_chain_stream`에 그
결과 메시지들이 청크로 온다)를 API 계약의 이벤트(run_started/text/
tool_call/tool_result/approval_required/compaction/run_end)로 바꾼다.
`citations`(step 37)는 아직 없다.

**Phase 7**: 이 함수가 곧 "에이전트 실행" 그 자체라 비용 추적(step 8,
Phase 2)이 지금까지 안 닿아있던 지점이다 — `ChatAnthropic`은
`llm/cost.py`의 `LLMClient`를 안 거친다. 여기서 직접 토큰·비용을
집계해서 (1) `data/traces/{run_id}.jsonl`에 기록하고(step 29,
`observability.py`) (2) `runs` 테이블에 `kind='chat'`으로 한 행 적립하고
(step 8과 같은 테이블, `xba cost`가 그대로 잡는다) (3) 시작 전에 상한을
넘었으면 그래프를 아예 안 돌린다(step 30, FR-P6).

**`compaction` 이벤트의 `before_chars`/`after_chars`는 토큰이 아니라
글자 수다.** docs §2.7 예시는 `before_tokens`/`after_tokens`인데,
정확한 토큰 수를 세려면 토크나이저 의존성이 추가로 필요하다 — 있지도
않은 정밀도를 있는 척하느니 글자 수로 정직하게 근사한다
(`agent/nodes.py`의 `_compact_summary`가 압축 문자열 안에 "원본 N자"를
남겨두고, 여기서 그걸 다시 읽어낸다).

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

**툴 호출의 시작/끝 짝짓기는 우리 `seq`가 아니라 LangChain이 이벤트마다
주는 자체 `run_id`로 한다** — 한 AIMessage가 툴을 여러 개 동시에
부르면(parallel tool calls, 실측으로 흔히 봄) `on_tool_start`가 전부
먼저 오고 `on_tool_end`가 나중에 뒤섞여 오는데, 바깥쪽 `seq` 하나만
보고 짝지으면 전부 마지막 `seq`로 잘못 찍힌다.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from ytfa.agent import approvals
from ytfa.agent.permissions import requires_approval
from ytfa.db import get_connection
from ytfa.llm.cost import CostLimitExceeded, check_cost_limit, compute_cost_usd, record_run
from ytfa.observability import Tracer

_COMPACT_ORIGINAL_LEN_RE = re.compile(r"원본 (\d+)자")


@dataclass
class SSEEvent:
    event: str
    data: dict[str, Any]

    def encode(self) -> str:
        return f"event: {self.event}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


@dataclass
class _RunAccumulator:
    """한 대화 턴에 걸쳐 누적하는 수치 — 트레이스·`runs` 적립용."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    final_text: str = ""


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


def _extract_compaction(ev: dict) -> dict | None:
    """`compact` 노드가 실제로 뭔가 압축했으면 그 요약을, 아니면 None을 반환한다."""
    if ev["event"] != "on_chain_stream" or ev.get("name") != "compact":
        return None
    chunk = ev["data"].get("chunk")
    messages = chunk.get("messages") if isinstance(chunk, dict) else None
    if not messages:
        return None

    before_chars = 0
    after_chars = 0
    for msg in messages:
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            continue
        after_chars += len(content)
        match = _COMPACT_ORIGINAL_LEN_RE.search(content)
        if match:
            before_chars += int(match.group(1))
    if after_chars == 0:
        return None
    return {"compacted_count": len(messages), "before_chars": before_chars, "after_chars": after_chars}


async def stream_chat(graph: Any, thread_id: str, message: str, conn: Any | None = None) -> AsyncIterator[SSEEvent]:
    """대화 한 턴을 실행하며 SSE 이벤트를 순서대로 낸다. 승인 대기·비용 추적·상한도 이 안에서 처리한다.

    `conn`을 넘기면 그 커넥션을 그대로 쓴다(테스트에서 임시 DB를 주입하기
    위한 용도) — 안 넘기면 앱 DB에 직접 연다.
    """
    run_id = f"r_{uuid.uuid4().hex[:12]}"
    tracer = Tracer(run_id)
    run_start = time.monotonic()
    acc = _RunAccumulator()

    yield SSEEvent("run_started", {"run_id": run_id, "thread_id": thread_id})

    seq = 0
    stopped_reason = "end_turn"
    config = {"configurable": {"thread_id": thread_id}}

    with (nullcontext(conn) if conn is not None else get_connection()) as conn:
        # 상한(FR-P6, step 30) — 그래프를 아예 안 돌린다. 호출 후에 검사하면
        # 이미 돈을 쓴 뒤라 의미가 없다.
        try:
            check_cost_limit(conn)
        except CostLimitExceeded as exc:
            yield SSEEvent("text", {"delta": f"\n[비용 상한 초과 — {exc}]"})
            yield SSEEvent("run_end", {"run_id": run_id, "steps": 0, "stopped_reason": "cost_limit_reached"})
            tracer.run_end(
                steps=0, stopped_reason="cost_limit_reached", total_cost_usd=0.0,
                total_latency_ms=(time.monotonic() - run_start) * 1000,
            )
            return

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
                yield SSEEvent("run_end", {"run_id": run_id, "steps": 0, "stopped_reason": stopped_reason})
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

        tool_call_meta: dict[str, dict] = {}  # LangChain 자체 run_id → {"seq", "start", "tool"}
        llm_call_start: float | None = None

        try:
            while True:
                interrupt_payload: dict | None = None

                async for ev in graph.astream_events(graph_input, config, version="v2"):
                    interrupt_payload = _extract_interrupt(ev)
                    if interrupt_payload is not None:
                        break

                    kind = ev["event"]
                    if kind == "on_chat_model_start":
                        llm_call_start = time.monotonic()
                    elif kind == "on_chat_model_stream":
                        delta = text_delta(ev["data"]["chunk"].content)
                        if delta:
                            acc.final_text += delta
                            yield SSEEvent("text", {"delta": delta})
                    elif kind == "on_chat_model_end":
                        latency_ms = (time.monotonic() - llm_call_start) * 1000 if llm_call_start else 0.0
                        llm_call_start = None
                        output = ev["data"].get("output")
                        usage = getattr(output, "usage_metadata", None) or {}
                        resp_meta = getattr(output, "response_metadata", None) or {}
                        model_name = resp_meta.get("model_name") or ""
                        input_tokens = usage.get("input_tokens", 0) or 0
                        output_tokens = usage.get("output_tokens", 0) or 0
                        input_token_details = usage.get("input_token_details") or {}
                        cache_read = input_token_details.get("cache_read", 0) or 0
                        cache_creation = input_token_details.get("cache_creation", 0) or 0
                        # LangChain의 input_tokens는 캐시 히트/기록분을 포함한
                        # 총합이다(원본 Anthropic SDK와 반대 관례) — compute_cost_usd는
                        # "신규분만"을 기대하므로 여기서 미리 빼야 한다. 안 그러면
                        # 캐시 히트 토큰이 전액+10% 할인가로 이중 청구된다(실측으로
                        # 발견한 버그, step 40).
                        fresh_input_tokens = max(input_tokens - cache_read - cache_creation, 0)
                        try:
                            call_cost = compute_cost_usd(
                                model_name, fresh_input_tokens, output_tokens, cache_read, cache_creation
                            )
                        except KeyError:
                            call_cost = 0.0  # 단가표에 없는 모델 — 트레이스엔 남기되 조용히 0원 처리
                        acc.input_tokens += input_tokens
                        acc.output_tokens += output_tokens
                        acc.cache_read_tokens += cache_read
                        acc.cost_usd += call_cost
                        acc.llm_calls += 1
                        tracer.llm_call(
                            model=model_name, input_tokens=input_tokens, output_tokens=output_tokens,
                            cache_read_tokens=cache_read, latency_ms=latency_ms,
                            stop_reason=resp_meta.get("stop_reason"), cost_usd=call_cost,
                        )
                    elif kind == "on_tool_start":
                        seq += 1
                        tool_call_meta[ev["run_id"]] = {"seq": seq, "start": time.monotonic(), "tool": ev["name"]}
                        yield SSEEvent(
                            "tool_call",
                            {
                                "seq": seq,
                                "tool": ev["name"],
                                "args_summary": ev["data"].get("input", {}),
                                "decision": "user_allowed" if requires_approval(ev["name"]) else "auto_allow",
                            },
                        )
                    elif kind == "on_tool_end":
                        meta = tool_call_meta.pop(ev["run_id"], None)
                        call_seq = meta["seq"] if meta else seq
                        latency_ms = (time.monotonic() - meta["start"]) * 1000 if meta else 0.0
                        size = result_size(ev["data"].get("output"))
                        acc.tool_calls += 1
                        tracer.tool_call(
                            tool=(meta or {}).get("tool", ev.get("name", "")), args_summary={},
                            decision="user_allowed" if requires_approval(ev.get("name", "")) else "auto_allow",
                            latency_ms=latency_ms, result_size=size, error=None,
                        )
                        yield SSEEvent("tool_result", {"seq": call_seq, "ok": True, "result_size": size})
                    elif kind == "on_chain_stream" and ev.get("name") == "compact":
                        compaction = _extract_compaction(ev)
                        if compaction:
                            tracer.compaction(**compaction)
                            yield SSEEvent("compaction", compaction)

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

        total_latency_ms = (time.monotonic() - run_start) * 1000
        steps = acc.llm_calls + acc.tool_calls
        try:
            record_run(
                conn, kind="chat", user_input=message[:200], summary=acc.final_text[:200] or None,
                input_tokens=acc.input_tokens, output_tokens=acc.output_tokens,
                cache_read_tokens=acc.cache_read_tokens, cost_usd=acc.cost_usd, stopped_reason=stopped_reason,
            )
        except Exception:  # noqa: BLE001 — 기록 실패가 응답 자체를 막으면 안 된다(트레이서와 같은 원칙)
            pass
        tracer.run_end(
            steps=steps, stopped_reason=stopped_reason, total_cost_usd=acc.cost_usd, total_latency_ms=total_latency_ms
        )

    yield SSEEvent("run_end", {"run_id": run_id, "steps": steps, "stopped_reason": stopped_reason})
