"""최소 에이전트 — MCP 툴로 교체 (docs/05-구현가이드.md Phase 6, step 23).

`agent/minimal.py`(step 22)와 **완전히 같은 구조**다 — 툴 출처만
로컬 함수(`tools_local.py`)에서 MCP 서버(`mcp_tools.py`)로 바뀌었다.
동작이 22번과 똑같아야 정상이다.

확인:
    uv run python -m ytfa.agent.minimal_mcp "개발 카테고리 최근 2일 영상 알려줘"
"""

from __future__ import annotations

import asyncio
import sys

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic

from ytfa.agent.mcp_tools import get_mcp_tools
from ytfa.agent.message_utils import extract_text
from ytfa.agent.prompts import SYSTEM_PROMPT
from ytfa.config import load_config


async def build_agent():
    cfg = load_config()
    model = ChatAnthropic(model=cfg.llm.large_model, api_key=cfg.anthropic_api_key, max_tokens=2048)
    tools = await get_mcp_tools()
    return create_agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)


async def run_once(message: str) -> str:
    agent = await build_agent()
    result = await agent.ainvoke({"messages": [{"role": "user", "content": message}]})
    final = result["messages"][-1]
    return extract_text(final)


def main() -> None:
    message = " ".join(sys.argv[1:]) or "AI 카테고리 새 영상 알려줘"
    print(f"> {message}\n")
    print(asyncio.run(run_once(message)))


if __name__ == "__main__":
    main()
