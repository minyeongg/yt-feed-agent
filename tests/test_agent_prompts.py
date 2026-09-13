"""agent/prompts.py::build_system_prompt 단위 테스트 (docs/05-구현가이드.md
Phase 9, step 38 확인 — "요약은 짧게"라고 말한 뒤 새 세션에서 반영된다).

`remember`로 저장한 선호가 시스템 프롬프트에 실제로 들어가는지는 여기서
DB 수준으로 확인한다. 모델이 그 지시를 실제로 지키는지는 LLM 호출이
필요해서 라이브 대화로 별도 확인했다(prompts.py 모듈 docstring 참고).
"""

from __future__ import annotations

import sqlite3

from ytfa.agent.prompts import SYSTEM_PROMPT, build_system_prompt
from ytfa.core.memory import remember
from ytfa.db import init_db


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    return c


def test_build_system_prompt_without_preferences_equals_base_prompt():
    conn = _conn()
    assert build_system_prompt(conn) == SYSTEM_PROMPT


def test_build_system_prompt_includes_remembered_preference():
    conn = _conn()
    remember(conn, "preference", "요약은 짧게")

    prompt = build_system_prompt(conn)

    assert prompt.startswith(SYSTEM_PROMPT)  # 기존 지시는 그대로 유지
    assert "요약은 짧게" in prompt


def test_build_system_prompt_excludes_fact_memories():
    conn = _conn()
    remember(conn, "fact", "이건 매 대화에 자동으로 실리면 안 됨")

    prompt = build_system_prompt(conn)

    assert "이건 매 대화에 자동으로 실리면 안 됨" not in prompt


def test_build_system_prompt_reflects_newly_added_preference_immediately():
    """"새 세션에서 반영된다"의 핵심 — remember가 끝난 시점부터 그 다음
    `build_system_prompt` 호출(=다음 턴/새 스레드)에 바로 보여야 한다.
    별도 캐시나 재시작이 필요 없음을 확인한다."""
    conn = _conn()
    before = build_system_prompt(conn)
    assert "요약은 짧게" not in before

    remember(conn, "preference", "요약은 짧게")

    after = build_system_prompt(conn)
    assert "요약은 짧게" in after
