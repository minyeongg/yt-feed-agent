"""llm/categorize.py 단위 테스트 (docs/05-구현가이드.md Phase 2, step 9 확인).

실제 Anthropic 호출은 하지 않는다 — `LLMClient.parse()`를 흉내 낸 가짜
클라이언트로 저장 로직(카테고리 생성·재사용·잠금 존중)만 검증한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from ytfa.db import init_db
from ytfa.llm.categorize import (
    CategorizeResult,
    CategoryAssignment,
    categorize_all_pending,
    categorize_channel,
)


class _FakeLlmConfig:
    small_model = "claude-haiku-4-5"


class _FakeConfig:
    llm = _FakeLlmConfig()


class _FakeResponse:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class FakeLLMClient:
    """실제 API를 부르지 않는 가짜 `LLMClient`. `parse()`가 미리 정해둔 결과를 순서대로 반환한다."""

    def __init__(self, results: list[CategorizeResult]):
        self.cfg = _FakeConfig()
        self._results = list(results)
        self.calls = 0

    def parse(self, **kwargs):
        result = self._results[self.calls]
        self.calls += 1
        return _FakeResponse(result), 0.001


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = datetime.now(timezone.utc).isoformat()
    c.execute(
        "INSERT INTO channels (id, title, description, added_at, category_locked) VALUES (?, ?, ?, ?, 0)",
        ("UC1", "테스트 채널", "설명", now),
    )
    c.commit()
    return c


def test_categorize_channel_creates_new_category(conn):
    result = CategorizeResult(categories=[CategoryAssignment(category_id="dev", category_name="개발", is_new=True)])
    client = FakeLLMClient([result])

    outcome = categorize_channel(client, conn, "UC1")
    assert outcome is not None
    _assignments, cost = outcome
    assert cost == 0.001

    cats = [tuple(r) for r in conn.execute("SELECT id, name FROM categories WHERE is_default = 0").fetchall()]
    assert cats == [("dev", "개발")]

    links = [
        tuple(r)
        for r in conn.execute("SELECT category_id, assigned_by FROM channel_categories WHERE channel_id='UC1'").fetchall()
    ]
    assert links == [("dev", "auto")]

    needs_review = conn.execute("SELECT needs_review FROM channels WHERE id='UC1'").fetchone()[0]
    assert needs_review == 1


def test_categorize_channel_self_heals_wrong_is_new_flag(conn):
    # 모델이 is_new=False라고 잘못 말해도(대소문자·공백 차이 등) FK 위반 없이
    # 스스로 카테고리를 만들어야 한다 — 실제로 겪은 버그(FK constraint failed).
    result = CategorizeResult(categories=[CategoryAssignment(category_id="Music ", category_name="음악", is_new=False)])
    client = FakeLLMClient([result])

    categorize_channel(client, conn, "UC1")

    cats = {tuple(r) for r in conn.execute("SELECT id, name FROM categories WHERE is_default = 0").fetchall()}
    assert cats == {("music", "음악")}


def test_categorize_channel_skips_locked(conn):
    conn.execute("UPDATE channels SET category_locked = 1 WHERE id = 'UC1'")
    conn.commit()
    client = FakeLLMClient([])  # 호출되면 IndexError로 바로 드러남

    outcome = categorize_channel(client, conn, "UC1")
    assert outcome is None
    assert client.calls == 0


def test_categorize_all_pending_skips_already_classified(conn):
    result = CategorizeResult(categories=[CategoryAssignment(category_id="dev", category_name="개발", is_new=True)])
    client = FakeLLMClient([result])

    first = categorize_all_pending(client, conn)
    assert first["classified"] == 1

    second = categorize_all_pending(client, conn)
    assert second == {"classified": 0, "candidates": 0, "cost_usd": 0.0}
    assert client.calls == 1  # 두 번째 실행에서 LLM을 다시 부르지 않음
