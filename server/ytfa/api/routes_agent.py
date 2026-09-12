"""`POST /agent/chat`, `POST /agent/approve` (docs/05-구현가이드.md Phase 6, step 25-26; docs/03 §2.7).

`agent/streaming.py`의 얇은 REST 어댑터(ADR-5와 같은 원칙 — 다만 여기선
`core/`가 아니라 `agent/`를 감싼다). `thread_id`가 없으면 새로 발급하고
`run_started` 이벤트로 클라이언트에게 알려준다 — 클라이언트는 그 값을
저장해뒀다가 다음 메시지에 그대로 실어 보내야 대화가 이어진다(step 24
체크포인터가 `thread_id` 기준이라서).

`/agent/approve`는 `/agent/chat`과 **별도의 HTTP 요청**이다 — 승인
다이얼로그에서 사용자가 답하면 이걸 부르고, 대기 중이던 `/agent/chat`
스트림(`agent/approvals.py`가 중개)이 그걸 받아 이어간다.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from ytfa.agent import approvals
from ytfa.agent.graph import build_graph, checkpoint_db_path
from ytfa.agent.mcp_tools import get_mcp_tools
from ytfa.agent.streaming import stream_chat
from ytfa.db import get_connection
from ytfa.security import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ApproveRequest(BaseModel):
    approval_id: str
    decision: Literal["allow", "deny"]


@router.post("/agent/chat")
async def agent_chat(body: ChatRequest) -> StreamingResponse:
    thread_id = body.thread_id or f"t_{uuid.uuid4().hex[:12]}"

    async def event_source():
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_db_path())) as saver:
            tools = await get_mcp_tools()
            graph = build_graph(tools, checkpointer=saver)
            async for event in stream_chat(graph, thread_id, body.message):
                yield event.encode()

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.post("/agent/approve")
def agent_approve(body: ApproveRequest) -> dict:
    with get_connection() as conn:
        ok = approvals.resolve(conn, body.approval_id, body.decision)
    if not ok:
        raise HTTPException(status_code=404, detail=f"approval_id '{body.approval_id}' 없음(이미 처리됐거나 존재하지 않음)")
    return {"ok": True}
