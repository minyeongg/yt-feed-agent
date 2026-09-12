"""agent/mcp_tools.py 확인 (docs/05-구현가이드.md Phase 6, step 23).

실제 MCP stdio 서브프로세스를 띄운다(LLM 호출은 없음 — 비용 0). "step
22와 툴 출처만 다르고 동작은 같아야 한다"를 검증하는 핵심은 툴 이름
집합이 일치하는지다.
"""

from __future__ import annotations

import pytest

from ytfa.agent.mcp_tools import get_mcp_tools
from ytfa.agent.tools_local import TOOLS as LOCAL_TOOLS


@pytest.mark.asyncio
async def test_mcp_tools_include_the_same_three_as_local_tools():
    mcp_tools = await get_mcp_tools()
    mcp_names = {t.name for t in mcp_tools}
    local_names = {t.name for t in LOCAL_TOOLS}

    # MCP 서버는 8개를 노출하지만(step 20-21), step 22의 로컬 3개는
    # 전부 그 안에 있어야 한다 — "툴 출처만 바뀐다"의 전제.
    assert local_names <= mcp_names


@pytest.mark.asyncio
async def test_get_video_via_mcp_returns_not_found_shape_for_unknown_id():
    mcp_tools = await get_mcp_tools()
    get_video = next(t for t in mcp_tools if t.name == "get_video")

    result = await get_video.ainvoke({"id": "totally-fake-id"})
    assert "NOT_FOUND" in str(result)
