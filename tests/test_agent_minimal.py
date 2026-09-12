"""agent/minimal.py 배선 테스트 (docs/05-구현가이드.md Phase 6, step 22 확인).

실제 LLM 호출까지는 여기서 안 한다(비용 발생 + 네트워크 필요) — 그건
`uv run python -m ytfa.agent.minimal "..."`로 실측했다(README 참고).
여기선 툴 목록 구성과 에이전트 객체 생성(=네트워크 호출 없음)까지만
확인한다.
"""

from __future__ import annotations

from ytfa.agent.tools_local import TOOLS, get_video, list_new_videos, search_videos


def test_tools_list_matches_mcp_step20_set():
    # step 23에서 "툴 출처만 바뀐 것"이 성립하려면 이름이 MCP 쪽 3개와 같아야 한다.
    assert {t.name for t in TOOLS} == {"list_new_videos", "search_videos", "get_video"}


def test_each_tool_has_a_description_for_the_model():
    for t in TOOLS:
        assert t.description  # 모델이 언제 쓸지 판단할 근거


def test_build_agent_does_not_call_network():
    from ytfa.agent.minimal import build_agent

    agent = build_agent()  # ChatAnthropic 생성 + create_agent 컴파일은 네트워크 호출이 아니다
    assert agent is not None
