"""agent/message_utils.py 단위 테스트.

실제 겪은 버그의 회귀 테스트: Sonnet 5의 adaptive thinking 때문에
`.content`가 문자열이 아니라 thinking+text 블록 리스트로 올 때가 있고,
그걸 `str()`로 그냥 찍으면 thinking 블록의 원문 서명까지 노출됐다.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from ytfa.agent.message_utils import extract_text


def test_extract_text_from_plain_string_content():
    msg = AIMessage(content="안녕하세요")
    assert extract_text(msg) == "안녕하세요"


def test_extract_text_skips_thinking_blocks():
    msg = AIMessage(
        content=[
            {"type": "thinking", "thinking": "", "signature": "긴 서명 문자열..."},
            {"type": "text", "text": "실제 답변입니다"},
        ]
    )
    result = extract_text(msg)
    assert result == "실제 답변입니다"
    assert "서명" not in result


def test_extract_text_joins_multiple_text_blocks():
    msg = AIMessage(content=[{"type": "text", "text": "첫 줄"}, {"type": "text", "text": "둘째 줄"}])
    assert extract_text(msg) == "첫 줄\n둘째 줄"


def test_extract_text_empty_list_returns_empty_string():
    msg = AIMessage(content=[{"type": "thinking", "thinking": "", "signature": "x"}])
    assert extract_text(msg) == ""
