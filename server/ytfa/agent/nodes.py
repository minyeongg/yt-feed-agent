"""그래프 노드 (docs/05-구현가이드.md Phase 6, step 24/26/27; docs/02 ADR-6).

프리빌트(step 22-23)를 4개 노드로 분해한다: `agent`(모델 호출) →
`approve`(승인 게이트) → `tools`(툴 실행) → `compact`(컨텍스트 압축) →
다시 `agent`로 순환.

**`approve`(step 26)**: `ask` 등급 툴(permissions.py) 호출마다
`interrupt()`로 멈춘다. 거부되면 그 tool_call을 AIMessage에서 빼고
`DENIED_BY_USER` ToolMessage를 대신 끼워 넣는다 — `tools` 노드가 거부된
걸 다시 실행하지 않게.

**`tools`(step 27)**: 실제 실행은 `ToolNode`(프레임워크)에게 맡기되,
결과가 상태에 들어가기 전에 `security.py`로 외부 텍스트를 격리 봉투에
감싼다(ADR-11) — "툴 바인딩은 프레임워크, 툴 결과를 어떻게 다룰지는
우리 정책"이라는 ADR-6의 역할 분담 그대로다.

**`compact`는 아직 통과만 한다** — 실제 로직은 step 28.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from ytfa.agent.permissions import requires_approval, summarize_tool_call
from ytfa.agent.prompts import SYSTEM_PROMPT
from ytfa.agent.state import AgentState
from ytfa.security import sanitize_tool_message_content


def make_agent_node(model_with_tools) -> Callable[[AgentState], dict]:
    """모델 호출 노드를 만든다. 시스템 프롬프트는 상태엔 안 남기고 호출 때만 앞에 붙인다."""

    def agent_node(state: AgentState) -> dict:
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        response = model_with_tools.invoke(messages)
        return {"messages": [response]}

    return agent_node


def approve_node(state: AgentState) -> dict:
    """`ask` 등급 툴마다 `interrupt()`로 멈춘다. `auto_allow` 툴은 손대지 않는다.

    `interrupt()`는 재개될 때 노드를 처음부터 다시 실행하되, 이미 결정된
    호출은 "리플레이"로 즉시 통과시킨다(LangGraph 자체 동작) — 그래서
    이 함수는 루프 안에서 여러 번 `interrupt()`를 불러도 안전하다.
    """
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return {}

    denied_ids: set[str] = set()
    denial_messages: list[ToolMessage] = []

    for tool_call in last.tool_calls:
        if not requires_approval(tool_call["name"]):
            continue
        decision = interrupt(
            {
                "tool": tool_call["name"],
                "args": tool_call["args"],
                "summary": summarize_tool_call(tool_call["name"], tool_call["args"]),
            }
        )
        if decision != "allow":
            denied_ids.add(tool_call["id"])
            denial_messages.append(
                ToolMessage(content="DENIED_BY_USER", tool_call_id=tool_call["id"], name=tool_call["name"])
            )

    if not denied_ids:
        return {}

    # 거부된 tool_call은 AIMessage에서 빼서 tools 노드가 다시 안 돌리게 한다.
    # 같은 id로 돌려주면 add_messages가 새로 추가하지 않고 교체한다.
    remaining_calls = [tc for tc in last.tool_calls if tc["id"] not in denied_ids]
    updated_ai = last.model_copy(update={"tool_calls": remaining_calls})
    return {"messages": [updated_ai, *denial_messages]}


def make_tools_node(tools: list) -> Callable[[AgentState], dict]:
    """`ToolNode`로 실제 실행하고, 결과가 상태에 들어가기 전에 격리 봉투로 감싼다(step 27).

    노드를 반드시 **async**로 만들어야 한다 — MCP 툴(step 23)은 서브프로세스를
    띄우는 async 전용 `StructuredTool`이라, 동기 `.invoke()`로 부르면
    "StructuredTool does not support sync invocation"으로 죽는다(실측
    확인). 그래프 자체가 `ainvoke`/`astream_events`로만 도니(step 24)
    async 노드로 바꿔도 다른 데는 영향 없다.
    """
    tool_node = ToolNode(tools)

    async def tools_node(state: AgentState) -> dict:
        result = await tool_node.ainvoke(state)
        sanitized = [
            msg.model_copy(update={"content": sanitize_tool_message_content(msg.content)})
            if isinstance(msg, ToolMessage)
            else msg
            for msg in result["messages"]
        ]
        return {"messages": sanitized}

    return tools_node


def compact_node(state: AgentState) -> dict:
    """컨텍스트 압축 자리. 실제 로직은 step 28에서 붙인다 — 지금은 통과."""
    return {}


def route_after_agent(state: AgentState) -> str:
    """에이전트가 툴을 부르려 하면 승인 게이트로, 아니면 끝낸다."""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "approve"
    return "end"
