"""mcp_server/server.py 순수 로직 테스트 (docs/05-구현가이드.md Phase 5, step 20-21).

라우터급 얇은 어댑터라(core 로직은 이미 core/*.py 테스트가 덮는다) 여기선
포맷 변환·에러 래핑 같은 이 파일 고유 로직만 확인한다. 실제 8개 툴 호출은
MCP Inspector로 실측 검증했다(DB 접근을 몽키패치하지 않고는 이 모듈을
단위 테스트로 격리하기 어렵다 — REST 라우터를 curl로 검증한 것과 같은
이유).
"""

from __future__ import annotations

from ytfa.mcp_server.server import _parse_since_hours, _safe, _to_brief


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
