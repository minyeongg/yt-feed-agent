"""agent/approvals.py 단위 테스트 (docs/05-구현가이드.md Phase 6, step 26 확인).

핵심 확인 대상: (1) 결정이 DB에 영구 저장되는지(재시작 후 재개의
근거), (2) 같은 프로세스에서 기다리던 쪽이 `resolve()` 즉시 깨어나는지.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from ytfa.agent import approvals
from ytfa.db import init_db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    return c


def test_create_pending_then_resolve_persists_decision(conn):
    approval_id = approvals.create_pending(conn, "t1", "assign_category", {"channel_id": "UC1"}, "요약")
    assert approvals.get_decision(conn, approval_id) is None  # 아직 대기 중

    ok = approvals.resolve(conn, approval_id, "allow")
    assert ok is True
    assert approvals.get_decision(conn, approval_id) == "allow"


def test_resolve_unknown_approval_id_returns_false(conn):
    assert approvals.resolve(conn, "ap_nonexistent", "allow") is False


def test_find_latest_for_thread_reflects_decision_state(conn):
    approval_id = approvals.create_pending(conn, "t1", "set_video_state", {"video_id": "v1"}, "요약")

    pending = approvals.find_latest_for_thread(conn, "t1")
    assert pending is not None
    assert pending["approval_id"] == approval_id
    assert pending["tool"] == "set_video_state"
    assert pending["decision"] is None  # 아직 대기 중

    approvals.resolve(conn, approval_id, "deny")
    resolved = approvals.find_latest_for_thread(conn, "t1")
    assert resolved["decision"] == "deny"  # 결정된 뒤에도 여전히 조회는 된다(재개용)


def test_find_latest_for_thread_none_when_no_pending(conn):
    assert approvals.find_latest_for_thread(conn, "empty-thread") is None


@pytest.mark.asyncio
async def test_wait_for_decision_wakes_on_resolve_same_process(conn):
    approval_id = approvals.create_pending(conn, "t1", "assign_category", {}, "요약")

    async def resolver():
        await asyncio.sleep(0.05)
        approvals.resolve(conn, approval_id, "allow")

    resolver_task = asyncio.create_task(resolver())
    decision = await approvals.wait_for_decision(conn, approval_id, poll_interval=1.0)
    await resolver_task

    assert decision == "allow"


@pytest.mark.asyncio
async def test_wait_for_decision_catches_up_via_db_polling(conn):
    # 같은 프로세스의 Future 없이(예: 재시작 시나리오 흉내) 이미 DB에 결정이
    # 있는 채로 기다리기 시작해도 폴링으로 바로 잡아내야 한다.
    approval_id = approvals.create_pending(conn, "t1", "assign_category", {}, "요약")
    approvals.resolve(conn, approval_id, "deny")

    decision = await approvals.wait_for_decision(conn, approval_id, poll_interval=0.05)
    assert decision == "deny"
