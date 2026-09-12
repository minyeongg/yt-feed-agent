"""최소 에이전트 — 프리빌트 루프 (docs/05-구현가이드.md Phase 6, step 22).

`langchain.agents.create_agent`(가이드 문서가 부르는 이름 그대로 —
`langgraph.prebuilt.create_react_agent`는 LangGraph 1.0에서 이쪽으로
옮겨졌고 v2.0에서 제거 예정이라 처음부터 이걸 쓴다)로
`agent/tools_local.py`의 툴 3개를 돌린다. `StateGraph`를 직접 조립하지
않는다 — 프리빌트가 동작하는 걸 먼저 본 다음에 분해한다(step 24).

확인: `uv run python -m ytfa.agent.minimal "AI 카테고리 새 영상 알려줘"`
"""

from __future__ import annotations

import sys

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic

from ytfa.agent.message_utils import extract_text
from ytfa.agent.prompts import SYSTEM_PROMPT
from ytfa.agent.tools_local import TOOLS
from ytfa.config import load_config


def build_agent():
    cfg = load_config()
    model = ChatAnthropic(model=cfg.llm.large_model, api_key=cfg.anthropic_api_key, max_tokens=2048)
    return create_agent(model=model, tools=TOOLS, system_prompt=SYSTEM_PROMPT)


def run_once(message: str) -> str:
    agent = build_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": message}]})
    final = result["messages"][-1]
    return extract_text(final)


def main() -> None:
    message = " ".join(sys.argv[1:]) or "AI 카테고리 새 영상 알려줘"
    print(f"> {message}\n")
    print(run_once(message))


if __name__ == "__main__":
    main()
