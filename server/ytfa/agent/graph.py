"""LangGraph `StateGraph` 조립 (docs/05-구현가이드.md Phase 6, step 24).

프리빌트(step 22-23)를 커스텀 그래프로 분해한다. 노드 4개(agent/approve/
tools/compact)는 `nodes.py` 참고. `AsyncSqliteSaver`(비동기 그래프라
동기 `SqliteSaver`는 `NotImplementedError`를 던진다 — 실측 확인)로
`thread_id`별 대화가 프로세스를 재시작해도 이어진다 — 앱 본체 DB
(`data/ytfa.db`)와는 별개 파일(`data/agent_checkpoints.db`)을 쓴다.
대화 체크포인트는 영상·채널 스키마와 관심사가 다르고, LangGraph가 자기
테이블(checkpoints/writes)을 그 안에 직접 만들게 두는 편이 우리
스키마와 안 섞여서 깔끔하다.
"""

from __future__ import annotations

from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from ytfa.agent.nodes import approve_node, compact_node, make_agent_node, route_after_agent
from ytfa.agent.state import AgentState
from ytfa.config import load_config


def checkpoint_db_path() -> Path:
    return load_config().data_dir / "agent_checkpoints.db"


def build_graph(tools: list, checkpointer: BaseCheckpointSaver | None = None):
    """`tools`(로컬 함수든 MCP든 — step 23과 마찬가지로 출처 무관)로 그래프를 컴파일한다."""
    cfg = load_config()
    model = ChatAnthropic(model=cfg.llm.large_model, api_key=cfg.anthropic_api_key, max_tokens=2048)
    model_with_tools = model.bind_tools(tools)

    builder = StateGraph(AgentState)
    builder.add_node("agent", make_agent_node(model_with_tools))
    builder.add_node("approve", approve_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("compact", compact_node)

    builder.set_entry_point("agent")
    builder.add_conditional_edges("agent", route_after_agent, {"approve": "approve", "end": END})
    builder.add_edge("approve", "tools")
    builder.add_edge("tools", "compact")
    builder.add_edge("compact", "agent")

    return builder.compile(checkpointer=checkpointer)
