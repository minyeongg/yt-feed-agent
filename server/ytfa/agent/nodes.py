"""그래프 노드 (docs/05-구현가이드.md Phase 6, step 24; docs/02 ADR-6).

프리빌트(step 22-23)를 4개 노드로 분해한다: `agent`(모델 호출) →
`approve`(승인 게이트) → `tools`(툴 실행) → `compact`(컨텍스트 압축) →
다시 `agent`로 순환.

**`approve`/`compact`는 지금은 통과만 한다.** 실제 정책은 각각 step 26
(interrupt 기반 승인)과 step 28(오래된 툴 결과 요약 치환)에서 채운다 —
지금 할 일은 "프리빌트와 동일하게 동작하는 배관을 먼저 세우는 것"이다.
노드가 이미 4개로 나뉘어 있어야 step 26/28에서 그래프 모양을 다시 안
바꾸고 노드 내용만 채우면 된다.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.messages import AIMessage, SystemMessage

from ytfa.agent.prompts import SYSTEM_PROMPT
from ytfa.agent.state import AgentState


def make_agent_node(model_with_tools) -> Callable[[AgentState], dict]:
    """모델 호출 노드를 만든다. 시스템 프롬프트는 상태엔 안 남기고 호출 때만 앞에 붙인다."""

    def agent_node(state: AgentState) -> dict:
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        response = model_with_tools.invoke(messages)
        return {"messages": [response]}

    return agent_node


def approve_node(state: AgentState) -> dict:
    """승인 게이트 자리. 실제 interrupt()는 step 26에서 붙인다 — 지금은 통과."""
    return {}


def compact_node(state: AgentState) -> dict:
    """컨텍스트 압축 자리. 실제 로직은 step 28에서 붙인다 — 지금은 통과."""
    return {}


def route_after_agent(state: AgentState) -> str:
    """에이전트가 툴을 부르려 하면 승인 게이트로, 아니면 끝낸다."""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "approve"
    return "end"
