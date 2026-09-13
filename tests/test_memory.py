"""core/memory.py 단위 테스트 (docs/05-구현가이드.md Phase 9, step 38 확인;
docs/03 §3.2).
"""

from __future__ import annotations

import sqlite3

import pytest

from ytfa.core.memory import MAX_CONTENT_LEN, active_preferences_text, forget, recall, remember
from ytfa.db import init_db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    return c


def test_remember_stores_and_returns_memory(conn):
    result = remember(conn, "preference", "요약은 짧게")

    assert result["kind"] == "preference"
    assert result["content"] == "요약은 짧게"
    assert result["id"].startswith("mem_")

    row = conn.execute("SELECT kind, content, use_count FROM memories WHERE id=?", (result["id"],)).fetchone()
    assert row[0] == "preference"
    assert row[1] == "요약은 짧게"
    assert row[2] == 0


def test_remember_rejects_invalid_kind(conn):
    result = remember(conn, "opinion", "아무거나")
    assert result["error"] == "INVALID_ARGUMENT"
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_remember_rejects_empty_content(conn):
    result = remember(conn, "fact", "   ")
    assert result["error"] == "INVALID_ARGUMENT"


def test_remember_rejects_content_over_300_chars(conn):
    result = remember(conn, "fact", "x" * (MAX_CONTENT_LEN + 1))
    assert result["error"] == "INVALID_ARGUMENT"
    assert "300" in result["hint"]


def test_remember_accepts_content_at_exactly_the_limit(conn):
    result = remember(conn, "fact", "x" * MAX_CONTENT_LEN)
    assert "error" not in result


def test_forget_deletes_memory(conn):
    created = remember(conn, "fact", "테스트 기억")
    result = forget(conn, created["id"])

    assert result == {"ok": True}
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_forget_unknown_id_returns_not_found(conn):
    result = forget(conn, "mem_없음")
    assert result["error"] == "NOT_FOUND"


def test_recall_filters_by_kind(conn):
    remember(conn, "preference", "선호1")
    remember(conn, "fact", "사실1")

    prefs = recall(conn, kind="preference")
    assert [i["content"] for i in prefs["items"]] == ["선호1"]

    facts = recall(conn, kind="fact")
    assert [i["content"] for i in facts["items"]] == ["사실1"]

    everything = recall(conn, kind="all")
    assert len(everything["items"]) == 2


def test_recall_filters_by_query_substring(conn):
    remember(conn, "fact", "파이썬 관련 채널을 좋아함")
    remember(conn, "fact", "고양이 영상은 스킵함")

    result = recall(conn, query="파이썬")
    assert len(result["items"]) == 1
    assert "파이썬" in result["items"][0]["content"]


def test_recall_invalid_kind_returns_error(conn):
    result = recall(conn, kind="nonsense")
    assert result["error"] == "INVALID_ARGUMENT"


def test_recall_bumps_use_count_and_last_used_at(conn):
    created = remember(conn, "fact", "기억")
    assert created is not None

    first = recall(conn, query="기억")
    assert first["items"][0]["use_count"] == 1
    assert first["items"][0]["last_used_at"] is not None

    second = recall(conn, query="기억")
    assert second["items"][0]["use_count"] == 2


def test_active_preferences_text_empty_when_no_preferences(conn):
    assert active_preferences_text(conn) == ""


def test_active_preferences_text_lists_preferences_only(conn):
    remember(conn, "preference", "요약은 짧게")
    remember(conn, "fact", "이건 프롬프트에 자동으로 안 들어가야 함")

    text = active_preferences_text(conn)

    assert "요약은 짧게" in text
    assert "이건 프롬프트에 자동으로 안 들어가야 함" not in text
