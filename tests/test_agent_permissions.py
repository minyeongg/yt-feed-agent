"""agent/permissions.py 단위 테스트 (docs/05-구현가이드.md Phase 6, step 26)."""

from __future__ import annotations

from ytfa.agent.permissions import requires_approval, summarize_tool_call


def test_write_tools_require_approval():
    assert requires_approval("assign_category") is True
    assert requires_approval("set_video_state") is True
    assert requires_approval("remember") is True
    assert requires_approval("forget") is True
    assert requires_approval("summarize_videos") is True


def test_read_tools_do_not_require_approval():
    for name in ["list_new_videos", "search_videos", "get_video", "list_categories", "get_watch_stats", "recall"]:
        assert requires_approval(name) is False


def test_summarize_tool_call_assign_category():
    summary = summarize_tool_call("assign_category", {"channel_id": "UC1", "category_ids": ["dev", "ai"]})
    assert "UC1" in summary
    assert "dev" in summary


def test_summarize_tool_call_set_video_state():
    summary = summarize_tool_call("set_video_state", {"video_id": "abc123", "state": "watched"})
    assert "abc123" in summary
    assert "watched" in summary


def test_summarize_tool_call_unknown_tool_has_fallback():
    assert summarize_tool_call("mystery_tool", {}) == "mystery_tool 실행"


def test_summarize_tool_call_remember():
    summary = summarize_tool_call("remember", {"kind": "preference", "content": "요약은 짧게"})
    assert "preference" in summary
    assert "요약은 짧게" in summary


def test_summarize_tool_call_forget():
    summary = summarize_tool_call("forget", {"id": "mem_abc123"})
    assert "mem_abc123" in summary


def test_summarize_tool_call_summarize_videos():
    summary = summarize_tool_call("summarize_videos", {"video_ids": ["v1", "v2", "v3"]})
    assert "3" in summary
    assert "v1" in summary
