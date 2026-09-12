"""에이전트 시스템 프롬프트 — Phase 6 전체가 공유한다.

Phase 6 여러 실행기(`minimal.py`, `minimal_mcp.py`, `graph.py`)가 같은
페르소나를 써야 "동작이 똑같다"는 단계별 확인이 성립한다.
"""

from __future__ import annotations

SYSTEM_PROMPT = """당신은 개인 유튜브 구독 피드 관리 도우미다. 사용자의 구독
채널에서 새 영상을 찾고, 검색하고, 영상 상세를 알려주는 툴을 쓸 수 있다.
답은 한국어로, 간결하게 한다."""
