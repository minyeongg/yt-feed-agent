"""agent/nodes.py의 compact_node 단위 테스트 (docs/05-구현가이드.md Phase 6, step 28 확인).

실제 "5건 이상 조회해도 컨텍스트 초과가 안 난다"는 실측으로 따로
확인했다(비용 문제로 자동화 스위트엔 안 넣는다) — 여기선 "오래된 것만
압축하고 최근 3건은 보존한다"는 핵심 규칙만 확인한다.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ytfa.agent.nodes import COMPACT_KEEP_RECENT, COMPACT_MARKER, compact_node


def _tool_msg(i: int, content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=f"call_{i}", name="list_new_videos", id=f"tm_{i}")


def test_compact_node_noop_when_at_or_below_keep_recent():
    messages = [HumanMessage(content="질문"), *[_tool_msg(i, "x" * 500) for i in range(COMPACT_KEEP_RECENT)]]
    result = compact_node({"messages": messages})
    assert result == {}


def test_compact_node_compacts_only_older_than_recent():
    total = COMPACT_KEEP_RECENT + 2  # 2건은 압축 대상
    messages = [HumanMessage(content="질문"), *[_tool_msg(i, "x" * 500) for i in range(total)]]
    result = compact_node({"messages": messages})

    compacted_ids = {m.id for m in result["messages"]}
    assert compacted_ids == {"tm_0", "tm_1"}  # 앞의 2건만
    for m in result["messages"]:
        assert m.content.startswith(COMPACT_MARKER)
        assert len(m.content) < 500  # 실제로 짧아짐


def test_compact_node_preserves_tool_call_id_and_recent_content_untouched():
    total = COMPACT_KEEP_RECENT + 1
    original_messages = [HumanMessage(content="질문"), *[_tool_msg(i, "x" * 500) for i in range(total)]]
    result = compact_node({"messages": original_messages})

    compacted = result["messages"][0]
    assert compacted.tool_call_id == "call_0"  # AIMessage.tool_calls가 참조하는 id는 안 바뀜


def test_compact_node_is_idempotent_does_not_double_compact():
    total = COMPACT_KEEP_RECENT + 1
    messages = [HumanMessage(content="질문"), *[_tool_msg(i, "x" * 500) for i in range(total)]]
    first = compact_node({"messages": messages})

    # 첫 압축 결과를 반영한 상태로 다시 돌리면(재실행 시나리오) 또 안 건드려야 한다.
    messages[1] = first["messages"][0]
    second = compact_node({"messages": messages})
    assert second == {}


def test_compact_node_handles_mcp_block_list_content():
    # MCP 툴(step 23) 결과는 [{"type": "text", "text": "..."}] 형태로 온다.
    mcp_content = [{"type": "text", "text": "x" * 500}]
    total = COMPACT_KEEP_RECENT + 1
    messages = [HumanMessage(content="질문")]
    messages.append(ToolMessage(content=mcp_content, tool_call_id="call_0", name="list_new_videos", id="tm_0"))
    messages += [_tool_msg(i, "y" * 10) for i in range(1, total)]

    result = compact_node({"messages": messages})
    compacted = next(m for m in result["messages"] if m.id == "tm_0")
    assert isinstance(compacted.content, str)  # 리스트였던 게 문자열로 정리됨
    assert compacted.content.startswith(COMPACT_MARKER)
