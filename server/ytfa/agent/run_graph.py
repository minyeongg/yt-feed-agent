"""커스텀 그래프 실행기 (docs/05-구현가이드.md Phase 6, step 24 확인용).

확인:
    uv run python -m ytfa.agent.run_graph t1 "개발 카테고리 최근 2일 영상 알려줘"
    uv run python -m ytfa.agent.run_graph t1 "그중에 제일 짧은 거 뭐야"
같은 thread_id(t1)로 두 번째 호출했을 때 첫 대화를 기억하면 체크포인터가
동작하는 것이다 — 프로세스가 두 번 다르게 떠도(매 실행이 새 파이썬
프로세스다) 이어지는 게 핵심이다.
"""

from __future__ import annotations

import asyncio
import sys

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ytfa.agent.graph import build_graph, checkpoint_db_path
from ytfa.agent.mcp_tools import get_mcp_tools
from ytfa.agent.message_utils import extract_text


async def run(thread_id: str, message: str) -> str:
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_db_path())) as saver:
        tools = await get_mcp_tools()
        graph = build_graph(tools, checkpointer=saver)

        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            config={"configurable": {"thread_id": thread_id}},
        )
        final = result["messages"][-1]
        return extract_text(final)


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: uv run python -m ytfa.agent.run_graph <thread_id> <message>")
        raise SystemExit(1)
    thread_id, message = sys.argv[1], " ".join(sys.argv[2:])
    print(f"[thread={thread_id}] > {message}\n")
    print(asyncio.run(run(thread_id, message)))


if __name__ == "__main__":
    main()
