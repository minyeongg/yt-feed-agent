"""메시지 콘텐츠에서 사람이 읽을 텍스트만 뽑아낸다.

Claude Sonnet 5는 기본적으로 adaptive thinking이 켜져 있어서(명시적으로
끄지 않는 한) 응답의 `.content`가 단순 문자열이 아니라 `{"type":
"thinking", ...}` 블록과 `{"type": "text", ...}` 블록이 섞인 리스트로
온다. `str(message.content)`로 그냥 찍으면 thinking 블록의 원문 서명까지
그대로 노출된다(실제로 겪음) — text 블록만 골라 이어붙인다.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage


def extract_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return str(content)
