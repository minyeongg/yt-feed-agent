"""agent/permissions.py 단위 테스트 (docs/05-구현가이드.md Phase 6, step 26)."""

from __future__ import annotations

from ytfa.agent.permissions import requires_approval, summarize_tool_call


def test_write_tools_require_approval():
    assert requires_approval("assign_category") is True
    assert requires_approval("set_video_state") is True


def test_read_tools_do_not_require_approval():
    for name in ["list_new_videos", "search_videos", "get_video", "list_categories", "get_watch_stats"]:
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
