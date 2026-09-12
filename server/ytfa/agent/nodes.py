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

**`compact`(step 28)**: 오래된 툴 결과(`ToolMessage`)를 짧은 요약
문자열로 치환한다. 최근 `COMPACT_KEEP_RECENT`건과 사용자 메시지는
그대로 둔다. LLM을 안 쓴다 — 전문을 잘라서 "이만큼 압축했다"는 표시만
남기는 구조적 압축이다(비용 0, ADR-8과 같은 절약 원칙).
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

COMPACT_KEEP_RECENT = 3
COMPACT_PREVIEW_CHARS = 120
COMPACT_MARKER = "[이전 툴 결과 압축됨"


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


def _tool_content_to_text(content: object) -> str:
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "".join(parts) or str(content)
    return content if isinstance(content, str) else str(content)


def _compact_summary(content: object) -> str:
    text = _tool_content_to_text(content)
    preview = text[:COMPACT_PREVIEW_CHARS]
    return f"{COMPACT_MARKER} (원본 {len(text)}자)] {preview}..."


def compact_node(state: AgentState) -> dict:
    """오래된 툴 결과를 요약으로 치환한다. 최근 `COMPACT_KEEP_RECENT`건과
    사용자 메시지는 그대로 둔다(docs step 28).

    같은 `tool_call_id`를 유지한 채 `content`만 짧게 바꾼다 — 메시지를
    지우거나(`RemoveMessage`) 순서를 바꾸지 않는다. AIMessage의
    tool_calls가 참조하는 ToolMessage가 그대로 있어야 하는 langchain의
    제약 때문이다.
    """
    messages = state["messages"]
    tool_indices = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
    if len(tool_indices) <= COMPACT_KEEP_RECENT:
        return {}

    to_compact = tool_indices[:-COMPACT_KEEP_RECENT]
    updated: list[ToolMessage] = []
    for i in to_compact:
        msg = messages[i]
        # 이미 압축된 건 다시 안 건드린다 — 재실행마다 더 짧아지는 걸 방지.
        if isinstance(msg.content, str) and msg.content.startswith(COMPACT_MARKER):
            continue
        updated.append(msg.model_copy(update={"content": _compact_summary(msg.content)}))

    return {"messages": updated} if updated else {}


def route_after_agent(state: AgentState) -> str:
    """에이전트가 툴을 부르려 하면 승인 게이트로, 아니면 끝낸다."""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "approve"
    return "end"
