"""권한 정책 — 어떤 툴이 승인 대상인지 (docs/05-구현가이드.md Phase 6, step 26; docs/03 §3.2).

ADR-6: LangGraph의 `interrupt()`는 "멈추고 재개하는 방법"만 제공한다.
"뭘 멈출지"(권한 정책)는 프레임워크가 대신해줄 수 없는, 우리가 직접
판단해야 하는 부분이다.

docs §3.2: 쓰기·비용 발생 툴(assign_category, set_video_state,
summarize_videos, remember, forget)은 `ask`. §3.1 읽기 툴(recall 포함)은
`auto_allow`. remember/forget이 ask인 이유는 비용이 아니라 영향
범위다 — `remember`로 저장한 선호는 `build_system_prompt`(Phase 9
step 38)를 거쳐 **앞으로의 모든 대화**에 자동으로 주입된다. 잘못
저장되면 한 번의 실수가 아니라 계속 반복되는 실수가 된다.
summarize_videos(step 39)는 반대로 순수하게 비용 때문에 ask다 — 최대
5건 병렬 LLM 호출이라 한 번 승인 안 받고 도는 걸 반복하면 순식간에
비용이 쌓인다.
"""

from __future__ import annotations

ASK_TOOLS: frozenset[str] = frozenset(
    {"assign_category", "set_video_state", "remember", "forget", "summarize_videos"}
)


def requires_approval(tool_name: str) -> bool:
    return tool_name in ASK_TOOLS


def summarize_tool_call(tool_name: str, args: dict) -> str:
    """승인 다이얼로그에 보여줄 한 줄 설명(docs §2.7 `approval_required`의 `summary` 필드)."""
    if tool_name == "assign_category":
        return f"채널 '{args.get('channel_id', '?')}'의 카테고리를 {args.get('category_ids', [])}로 재배정합니다"
    if tool_name == "set_video_state":
        return f"영상 '{args.get('video_id', '?')}' 상태를 '{args.get('state', '?')}'로 바꿉니다"
    if tool_name == "remember":
        kind = args.get("kind", "?")
        content = str(args.get("content", "?"))
        preview = content if len(content) <= 40 else content[:40] + "…"
        return f"새 기억을 저장합니다({kind}): {preview}"
    if tool_name == "forget":
        return f"기억 '{args.get('id', '?')}'을(를) 삭제합니다"
    if tool_name == "summarize_videos":
        video_ids = args.get("video_ids", [])
        return f"영상 {len(video_ids)}건을 병렬로 분석합니다(비용 발생): {video_ids}"
    return f"{tool_name} 실행"
