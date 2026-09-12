"""MCP 서버를 LangChain 툴로 로드 (docs/05-구현가이드.md Phase 6, step 23; docs/02 ADR-6).

step 22의 로컬 함수 툴 3개(`tools_local.py`)를 이걸로 갈아끼운다 —
**동작이 똑같아야 정상이다.** 툴 출처만(파이썬 함수 → MCP 서버, stdio)
바뀐 것이다.

**stdio는 절대경로를 쓴다**(가이드 경고 — 상대경로는 에이전트 프로세스의
작업 디렉토리에 따라 깨진다). venv의 python 인터프리터를 `sys.executable`
로 절대경로 지정해서, 이 프로세스와 똑같은 환경(의존성·editable
install)으로 MCP 서버 서브프로세스를 띄운다 — `cwd`도 프로젝트 루트로
명시해서 이중으로 안전하게 둔다(둘 다 없어도 editable install이라
동작하는 건 실측 확인했지만, 명시가 더 안전하다).

`MultiServerMCPClient.get_tools()`는 **툴 호출마다 새 stdio 세션을
띄운다**(라이브러리 자체 동작) — 그래서 이 파일은 세션을 직접 들고
있지 않아도 된다.
"""

from __future__ import annotations

import sys

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StdioConnection

from ytfa.config import load_config

SERVER_NAME = "ytfa"
_SERVER_MODULE = "ytfa.mcp_server.server"


def _mcp_connections() -> dict[str, StdioConnection]:
    cfg = load_config()
    return {
        SERVER_NAME: {
            "transport": "stdio",
            "command": sys.executable,  # 이 프로세스와 같은 venv의 절대경로 python
            "args": ["-m", _SERVER_MODULE],
            "cwd": str(cfg.project_root),
        }
    }


async def get_mcp_tools() -> list:
    """MCP 서버 `ytfa`의 툴을 LangChain 툴 목록으로 가져온다."""
    client = MultiServerMCPClient(_mcp_connections())
    return await client.get_tools()
