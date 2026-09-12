"""승인 대기 저장소 (docs/05-구현가이드.md Phase 6, step 26; docs/03 §2.7).

`POST /agent/chat`(SSE 연결 하나)이 `interrupt()`를 만나면 여기 등록하고
기다린다. `POST /agent/approve`(별도 HTTP 요청)가 여기서 찾아서 깨운다 —
두 요청이 같은 프로세스 안에서 만나는 지점이 in-memory
`asyncio.Future`다(빠른 경로).

**서버 재시작에도 재개되는 이유는 이게 아니라 DB다.** 프로세스가
죽으면 Future도 같이 사라진다 — 그래서 결정은 `pending_approvals`
테이블에도 영구 저장한다. 재시작 후 새 `/agent/chat` 요청이 같은
`thread_id`로 오면, in-memory Future가 없어도 DB에서 이미 내려진
결정(또는 여전히 대기 중이라는 사실)을 찾아 이어간다.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from sqlite3 import Connection

# 프로세스 하나 안에서만 유효하다 — 재시작되면 당연히 비워진다.
_waiters: dict[str, asyncio.Future] = {}


def create_pending(conn: Connection, thread_id: str, tool: str, args: dict, summary: str) -> str:
    approval_id = f"ap_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO pending_approvals (id, thread_id, tool, args, summary, decision, created_at)
           VALUES (?, ?, ?, ?, ?, NULL, ?)""",
        (approval_id, thread_id, tool, json.dumps(args, ensure_ascii=False), summary, now),
    )
    conn.commit()
    return approval_id


def register_waiter(approval_id: str) -> asyncio.Future:
    """이 프로세스에서 `approval_id`가 풀리길 기다릴 Future를 만든다."""
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _waiters[approval_id] = future
    return future


def resolve(conn: Connection, approval_id: str, decision: str) -> bool:
    """`/agent/approve`가 부른다. 존재하지 않으면 False."""
    row = conn.execute("SELECT id FROM pending_approvals WHERE id = ?", (approval_id,)).fetchone()
    if row is None:
        return False

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE pending_approvals SET decision = ?, resolved_at = ? WHERE id = ?",
        (decision, now, approval_id),
    )
    conn.commit()

    waiter = _waiters.pop(approval_id, None)
    if waiter is not None and not waiter.done():
        waiter.set_result(decision)  # 같은 프로세스에서 기다리는 중이면 즉시 깨운다
    return True


def get_decision(conn: Connection, approval_id: str) -> str | None:
    row = conn.execute("SELECT decision FROM pending_approvals WHERE id = ?", (approval_id,)).fetchone()
    return row[0] if row else None


def find_latest_for_thread(conn: Connection, thread_id: str) -> dict | None:
    """이 스레드의 가장 최근 승인 요청을 찾는다(결정 여부 무관).

    재시작 직후 `/agent/chat`이 "이 스레드, 그래프가 승인 지점에 멈춰
    있나?"(`graph.aget_state(config).next`로 별도 확인)를 본 뒤, 멈춰
    있다면 이걸로 "아직 대답 안 됐는지"(decision=None → 다시 물어보고
    기다림) 또는 "재시작 사이에 이미 /agent/approve가 왔는지"(decision
    있음 → 그걸로 바로 재개)를 구분한다.
    """
    row = conn.execute(
        """SELECT id, tool, args, summary, decision FROM pending_approvals
           WHERE thread_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (thread_id,),
    ).fetchone()
    if row is None:
        return None
    return {"approval_id": row[0], "tool": row[1], "args": json.loads(row[2]), "summary": row[3], "decision": row[4]}


async def wait_for_decision(conn: Connection, approval_id: str, poll_interval: float = 1.0) -> str:
    """결정이 날 때까지 기다린다. 같은 프로세스면 Future로 즉시, 아니면(다른
    프로세스가 먼저 resolve했으면) DB 폴링으로 잡아낸다."""
    future = register_waiter(approval_id)

    async def poll_db() -> str:
        while True:
            decision = get_decision(conn, approval_id)
            if decision is not None:
                return decision
            await asyncio.sleep(poll_interval)

    done, pending = await asyncio.wait(
        {asyncio.ensure_future(future), asyncio.ensure_future(poll_db())},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    _waiters.pop(approval_id, None)
    return next(iter(done)).result()
