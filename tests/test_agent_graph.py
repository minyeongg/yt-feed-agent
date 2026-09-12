"""agent/graph.py, agent/nodes.py 배선 테스트 (docs/05-구현가이드.md Phase 6, step 24 확인).

실제 대화 흐름(모델 호출 + thread_id 연속성)은
`uv run python -m ytfa.agent.run_graph <thread_id> "..."`로 두 번 연속
실측 검증했다(README 계열 스크립트라 여기선 돈 드는 부분은 안 돌린다).
여기선 라우팅 로직과 그래프가 실제로 컴파일되는지(네트워크 호출 없음)만
확인한다.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ytfa.agent.nodes import route_after_agent
from ytfa.agent.tools_local import TOOLS


def test_route_after_agent_goes_to_approve_when_tool_calls_pending():
    state = {
        "messages": [
            HumanMessage(content="질문"),
            AIMessage(content="", tool_calls=[{"name": "get_video", "args": {"id": "x"}, "id": "1"}]),
        ]
    }
    assert route_after_agent(state) == "approve"


def test_route_after_agent_ends_when_no_tool_calls():
    state = {"messages": [HumanMessage(content="질문"), AIMessage(content="답변입니다")]}
    assert route_after_agent(state) == "end"


def test_route_after_agent_ignores_non_ai_last_message():
    state = {
        "messages": [
            AIMessage(content="", tool_calls=[{"name": "get_video", "args": {}, "id": "1"}]),
            ToolMessage(content="결과", tool_call_id="1"),
        ]
    }
    assert route_after_agent(state) == "end"


def test_build_graph_compiles_without_network_call():
    from ytfa.agent.graph import build_graph

    graph = build_graph(TOOLS, checkpointer=None)
    node_names = set(graph.get_graph().nodes.keys())
    assert {"agent", "approve", "tools", "compact"} <= node_names
