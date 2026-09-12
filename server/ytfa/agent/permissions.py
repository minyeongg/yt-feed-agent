"""권한 정책 — 어떤 툴이 승인 대상인지 (docs/05-구현가이드.md Phase 6, step 26; docs/03 §3.2).

ADR-6: LangGraph의 `interrupt()`는 "멈추고 재개하는 방법"만 제공한다.
"뭘 멈출지"(권한 정책)는 프레임워크가 대신해줄 수 없는, 우리가 직접
판단해야 하는 부분이다.

docs §3.2: 쓰기·비용 발생 툴(assign_category, set_video_state,
summarize_videos, remember)은 `ask`. §3.1 읽기 툴은 `auto_allow`.
지금 그래프에 실제로 붙어있는 쓰기 툴은 두 개뿐이다 — summarize_videos/
remember는 아직 안 만들었다(Phase 9).
"""

from __future__ import annotations

ASK_TOOLS: frozenset[str] = frozenset({"assign_category", "set_video_state"})


def requires_approval(tool_name: str) -> bool:
    return tool_name in ASK_TOOLS


def summarize_tool_call(tool_name: str, args: dict) -> str:
    """승인 다이얼로그에 보여줄 한 줄 설명(docs §2.7 `approval_required`의 `summary` 필드)."""
    if tool_name == "assign_category":
        return f"채널 '{args.get('channel_id', '?')}'의 카테고리를 {args.get('category_ids', [])}로 재배정합니다"
    if tool_name == "set_video_state":
        return f"영상 '{args.get('video_id', '?')}' 상태를 '{args.get('state', '?')}'로 바꿉니다"
    return f"{tool_name} 실행"
