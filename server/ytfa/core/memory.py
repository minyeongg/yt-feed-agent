"""메모리 — 장기 선호/사실 기억 (docs/05-구현가이드.md Phase 9 step 38; docs/03 §3.2).

`kind`: `preference`(선호 — 매 대화 시스템 프롬프트에 자동 주입돼서
에이전트가 매번 지킨다) | `fact`(사실/에피소드 — 자동 주입하면 컨텍스트만
불어나니, `recall` 툴로 필요할 때만 찾아본다). §3.2 스펙대로 `content`는
300자 이하로 짧게만 저장한다 — 길게 저장하면 그 무게가 매 대화 프롬프트에
그대로 쌓인다(특히 preference).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlite3 import Connection

MAX_CONTENT_LEN = 300
VALID_KINDS = {"preference", "fact"}


def remember(conn: Connection, kind: str, content: str, source: str | None = None) -> dict:
    """새 기억을 저장한다. `kind`가 `preference`|`fact`가 아니거나 `content`가
    비었거나 300자를 넘으면 예외 대신 `{"error":...}`를 돌려준다(공통 규칙,
    `tool_shapes.safe_tool`이 잡는 "예상 못 한" 실패가 아니라 "예상된"
    입력 오류라 여기서 직접 처리한다)."""
    if kind not in VALID_KINDS:
        return {"error": "INVALID_ARGUMENT", "hint": f"kind는 {sorted(VALID_KINDS)} 중 하나여야 함"}
    content = content.strip()
    if not content:
        return {"error": "INVALID_ARGUMENT", "hint": "content가 비어 있음"}
    if len(content) > MAX_CONTENT_LEN:
        return {"error": "INVALID_ARGUMENT", "hint": f"content는 {MAX_CONTENT_LEN}자 이하여야 함(현재 {len(content)}자)"}

    memory_id = f"mem_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO memories (id, kind, content, source, created_at, use_count) VALUES (?, ?, ?, ?, ?, 0)",
        (memory_id, kind, content, source, now),
    )
    conn.commit()
    return {"id": memory_id, "kind": kind, "content": content, "source": source, "created_at": now}


def forget(conn: Connection, memory_id: str) -> dict:
    cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()
    if cur.rowcount == 0:
        return {"error": "NOT_FOUND", "hint": f"memory '{memory_id}' 없음"}
    return {"ok": True}


def recall(conn: Connection, kind: str = "all", query: str | None = None, limit: int = 20) -> dict:
    """저장된 기억을 최신순으로 찾는다. `query`가 있으면 `content` 부분일치로
    거른다(FTS 없이 LIKE로 충분 — 기억은 몇백 건 규모를 안 넘는다).

    찾아서 돌려준 기억은 `use_count`를 올리고 `last_used_at`을 갱신한다 —
    스키마에 그 두 컬럼을 둔 이유(나중에 안 쓰이는 기억을 골라낼 근거)."""
    sql = "SELECT id, kind, content, source, created_at, last_used_at, use_count FROM memories"
    conditions: list[str] = []
    params: list = []
    if kind != "all":
        if kind not in VALID_KINDS:
            return {"error": "INVALID_ARGUMENT", "hint": f"kind는 'all' 또는 {sorted(VALID_KINDS)} 중 하나"}
        conditions.append("kind = ?")
        params.append(kind)
    if query:
        conditions.append("content LIKE ?")
        params.append(f"%{query}%")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    items = [
        {
            "id": r[0],
            "kind": r[1],
            "content": r[2],
            "source": r[3],
            "created_at": r[4],
            "last_used_at": r[5],
            "use_count": r[6],
        }
        for r in rows
    ]

    if items:
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            "UPDATE memories SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
            [(now, item["id"]) for item in items],
        )
        conn.commit()
        for item in items:  # DB는 갱신됐는데 이미 만든 딕셔너리는 그대로라 반환값도 맞춰준다
            item["use_count"] += 1
            item["last_used_at"] = now

    return {"items": items}


def active_preferences_text(conn: Connection, limit: int = 20) -> str:
    """`kind='preference'`인 기억을 시스템 프롬프트 뒤에 붙일 텍스트로
    만든다. 하나도 없으면 빈 문자열 — 빈 "지킬 선호:" 섹션을 프롬프트에
    남기지 않는다."""
    rows = conn.execute(
        "SELECT content FROM memories WHERE kind = 'preference' ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    if not rows:
        return ""
    lines = "\n".join(f"- {r[0]}" for r in rows)
    return f"\n\n사용자가 이전에 알려준 선호(항상 지킨다):\n{lines}"
