"""tool_shapes.py 단위 테스트 (docs/03-API명세.md §3 "공통 규칙").

`mcp_server/server.py`(Phase 5)와 `agent/tools_local.py`(Phase 6 step 22)
둘 다 이 모듈을 쓴다 — 여기 로직이 맞으면 두 어댑터 다 맞다. 각 어댑터의
실제 툴 호출은 MCP Inspector / 프리빌트 에이전트 실행으로 따로
실측했다(DB 접근을 몽키패치하지 않고는 어댑터 자체를 단위 테스트로
격리하기 어렵다 — REST 라우터를 curl로 검증한 것과 같은 이유).
"""

from __future__ import annotations

from ytfa.tool_shapes import parse_since_hours as _parse_since_hours, safe_tool as _safe, to_brief_video as _to_brief


def test_parse_since_hours_days():
    assert _parse_since_hours("7d") == 168


def test_parse_since_hours_hours():
    assert _parse_since_hours("24h") == 24


def test_parse_since_hours_unparseable_falls_back_to_default():
    assert _parse_since_hours("nonsense", default_hours=99) == 99


def test_to_brief_shape():
    card = {
        "id": "v1",
        "title": "제목",
        "channel": {"id": "UC1", "title": "채널명", "thumbnail_url": ""},
        "duration_sec": 120,
        "published_at": "2026-01-01T00:00:00+00:00",
        "summary": "요약",
        "state": "new",
        "categories": ["dev"],
        "verdict": {"level": "중급"},  # brief 형태엔 안 들어가야 함
    }
    brief = _to_brief(card)
    assert brief == {
        "id": "v1",
        "title": "제목",
        "channel": "채널명",  # 객체가 아니라 이름 문자열(docs §3.1 축약형)
        "duration_sec": 120,
        "published_at": "2026-01-01T00:00:00+00:00",
        "summary": "요약",
        "state": "new",
        "categories": ["dev"],
    }


def test_safe_converts_unexpected_exception_to_error_dict():
    @_safe
    def boom():
        raise RuntimeError("예상 못 한 실패")

    result = boom()
    assert result["error"] == "INTERNAL_ERROR"
    assert "예상 못 한 실패" in result["hint"]


def test_safe_passes_through_normal_return():
    @_safe
    def ok():
        return {"value": 1}

    assert ok() == {"value": 1}
