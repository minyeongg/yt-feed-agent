"""에이전트 상태 (docs/05-구현가이드.md Phase 6, step 24; docs/02 ADR-6).

`TypedDict`로 최소로 시작한다 — 지금은 메시지 목록 하나뿐이다. 압축
정책(step 28)이나 승인 정책(step 26)이 상태에 더 담을 게 생기면 그때
필드를 추가한다.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
