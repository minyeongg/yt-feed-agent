"""agent/streaming.py 단위 테스트 (docs/05-구현가이드.md Phase 6, step 25 확인).

실제 SSE 응답 전체는 `curl --no-buffer`로 실측 검증했다(README/세션
기록 참고). 여기선 네트워크 없이 순수 변환 로직만 — 특히 Sonnet 5의
thinking/tool_use 블록이 섞인 청크에서 text만 골라내는 부분(실제로 겪은
버그와 같은 종류)과 SSE 인코딩 포맷을 확인한다.
"""

from __future__ import annotations

from ytfa.agent.streaming import SSEEvent, result_size, text_delta


def test_text_delta_from_plain_string():
    assert text_delta("안녕") == "안녕"


def test_text_delta_extracts_text_blocks_only():
    chunk = [
        {"type": "thinking", "thinking": ""},
        {"type": "tool_use", "id": "x", "name": "get_video", "input": {}},
        {"type": "text", "text": "실제 "},
        {"type": "text", "text": "답변"},
    ]
    assert text_delta(chunk) == "실제 답변"


def test_text_delta_returns_none_for_empty_or_non_text():
    assert text_delta([{"type": "thinking", "thinking": ""}]) is None
    assert text_delta([]) is None
    assert text_delta(123) is None


def test_result_size_reads_items_array():
    class FakeOutput:
        content = '{"items": [1, 2, 3], "total": 3}'

    assert result_size(FakeOutput()) == 3


def test_result_size_none_when_no_items_key():
    class FakeOutput:
        content = '{"error": "NOT_FOUND"}'

    assert result_size(FakeOutput()) is None


def test_result_size_none_for_unparseable_content():
    class FakeOutput:
        content = "not json"

    assert result_size(FakeOutput()) is None


def test_result_size_reads_mcp_content_block_list():
    # MCP 툴(step 23) 결과는 프로토콜상 문자열이 아니라
    # [{"type": "text", "text": "...json..."}] 형태로 온다(실제로 겪음 —
    # 로컬 툴에서 만든 테스트로는 이 형태를 못 잡아서 처음엔 놓쳤다).
    class FakeOutput:
        content = [{"type": "text", "text": '{"items": [1, 2]}'}]

    assert result_size(FakeOutput()) == 2


def test_sse_event_encode_format():
    event = SSEEvent("text", {"delta": "안녕"})
    encoded = event.encode()
    assert encoded == 'event: text\ndata: {"delta": "안녕"}\n\n'
    assert encoded.endswith("\n\n")  # SSE 이벤트 구분자
