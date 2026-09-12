"""agent/streaming.py의 승인 흐름 통합 테스트 (docs/05-구현가이드.md Phase 6, step 26 확인).

실제 LangGraph `interrupt()` 메커니즘을 진짜로 돌린다(모킹 안 함) —
다만 Anthropic 모델도 MCP 서브프로세스도 없는 아주 작은 가짜 그래프로.
이게 `stream_chat`이 실제로 의존하는 메커니즘 그 자체라서, 여기서
확인되면 진짜 그래프에서도 같은 방식으로 동작한다는 근거가 된다(진짜
그래프+모델로 하는 확인은 비용이 들어서 수동 실측으로 따로 했다).
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from ytfa.agent import approvals
from ytfa.agent.streaming import stream_chat
from ytfa.db import init_db


class _FakeState(TypedDict):
    messages: Annotated[list, add_messages]


def _build_fake_graph():
    """agent/nodes.py의 approve_node와 같은 모양으로 interrupt()를 부르는 최소 그래프."""

    def ask_node(state: _FakeState) -> dict:
        decision = interrupt(
            {"tool": "assign_category", "args": {"channel_id": "UC1"}, "summary": "채널 카테고리 재배정"}
        )
        return {"messages": [{"role": "assistant", "content": f"decision was: {decision}"}]}

    builder = StateGraph(_FakeState)
    builder.add_node("a", ask_node)
    builder.set_entry_point("a")
    builder.add_edge("a", END)
    return builder.compile(checkpointer=InMemorySaver())


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    return c


@pytest.mark.asyncio
async def test_stream_chat_pauses_for_approval_then_resumes_on_allow(conn):
    graph = _build_fake_graph()
    events = []
    approval_id_holder: dict = {}

    async def approver_after_a_beat():
        # approval_required 이벤트가 뜬 뒤 결정을 내린다 — /agent/approve가 하는 일.
        while "approval_id" not in approval_id_holder:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)
        approvals.resolve(conn, approval_id_holder["approval_id"], "allow")

    approver_task = asyncio.create_task(approver_after_a_beat())

    async for event in stream_chat(graph, "thread-allow", "카테고리 바꿔줘", conn=conn):
        events.append(event)
        if event.event == "approval_required":
            approval_id_holder["approval_id"] = event.data["approval_id"]

    await approver_task

    event_names = [e.event for e in events]
    assert event_names[0] == "run_started"
    assert "approval_required" in event_names
    assert event_names[-1] == "run_end"

    approval_event = next(e for e in events if e.event == "approval_required")
    assert approval_event.data["tool"] == "assign_category"
    assert approval_event.data["summary"] == "채널 카테고리 재배정"

    # 재개된 뒤 노드가 실제로 "allow"를 받아 실행됐는지 — DB로 승인 후 대기가 풀렸다는 증거.
    assert approvals.get_decision(conn, approval_event.data["approval_id"]) == "allow"


@pytest.mark.asyncio
async def test_stream_chat_resumes_on_deny(conn):
    graph = _build_fake_graph()
    approval_id_holder: dict = {}

    async def denier():
        while "approval_id" not in approval_id_holder:
            await asyncio.sleep(0.01)
        approvals.resolve(conn, approval_id_holder["approval_id"], "deny")

    denier_task = asyncio.create_task(denier())

    events = []
    async for event in stream_chat(graph, "thread-deny", "카테고리 바꿔줘", conn=conn):
        events.append(event)
        if event.event == "approval_required":
            approval_id_holder["approval_id"] = event.data["approval_id"]

    await denier_task

    assert approvals.get_decision(conn, approval_id_holder["approval_id"]) == "deny"
    assert events[-1].event == "run_end"
    assert events[-1].data["stopped_reason"] == "end_turn"  # 거부도 정상 종료 — 에러가 아니다


@pytest.mark.asyncio
async def test_stream_chat_reattaches_to_already_pending_approval(conn):
    """"서버 재시작해도 대기 중이던 대화가 재개된다"의 핵심 시나리오 —
    이미 대기 중인 approval이 있는 상태로 stream_chat을 다시 부르면(=
    재시작 후 새 /agent/chat 요청), 새 메시지로 처음부터 시작하는 게
    아니라 그 대기부터 잇는다."""
    graph = _build_fake_graph()

    # 1단계: 첫 실행에서 interrupt까지만 가고, approve 안 하고 그대로 둔다
    # (프로세스가 죽은 상황을 흉내 — 그냥 for 루프를 승인 전에 끊는다).
    first_approval_id = None
    async for event in stream_chat(graph, "thread-restart", "카테고리 바꿔줘", conn=conn):
        if event.event == "approval_required":
            first_approval_id = event.data["approval_id"]
            break  # 승인 안 하고 스트림만 버려둔다(연결이 끊긴 상황 흉내)

    assert first_approval_id is not None
    pending = approvals.find_latest_for_thread(conn, "thread-restart")
    assert pending is not None and pending["decision"] is None

    # 2단계: 미리 결정을 내려둔다(사용자가 재시작 사이에 /agent/approve를 불렀다고 가정).
    # 이 시점에서 우리 장부(pending_approvals)엔 결정이 있지만, 그래프
    # 자체는 아직 그 결정을 모른다 — 원래 스트림이 죽어서 못 받았으니까.
    approvals.resolve(conn, first_approval_id, "allow")

    # 3단계: "재시작 후" 새 stream_chat 호출 — message는 의미 없다(무시됨).
    # 그래프가 여전히 멈춰있고 결정도 이미 나 있으니, approval_required를
    # 다시 띄우지 않고 바로 Command(resume=...)로 이어가야 한다.
    events = []
    async for event in stream_chat(graph, "thread-restart", "이 메시지는 무시돼야 함", conn=conn):
        events.append(event)

    event_names = [e.event for e in events]
    assert event_names[0] == "run_started"
    assert "approval_required" not in event_names  # 이미 결정 났으니 다시 안 물어본다
    assert event_names[-1] == "run_end"
    assert events[-1].data["stopped_reason"] == "end_turn"
