"""core/search.py 단위 테스트 (docs/05-구현가이드.md Phase 3, step 15 확인).

`videos` 테이블에 직접 insert/update만 해도 db.py의 트리거가 `videos_fts`를
동기화한다는 전제까지 함께 검증한다(별도 fixture 없음).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from ytfa.core.search import search_keyword
from ytfa.db import init_db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC1', '파이썬 채널', ?)", (now,))
    c.execute(
        """INSERT INTO videos (id, channel_id, title, description, published_at, discovered_at)
           VALUES ('v1', 'UC1', '파이썬 비동기 프로그래밍 완전정복', 'async/await 다룹니다', ?, ?)""",
        (now, now),
    )
    c.execute(
        """INSERT INTO videos (id, channel_id, title, description, published_at, discovered_at)
           VALUES ('v2', 'UC1', '고양이 브이로그', '귀여운 고양이', ?, ?)""",
        (now, now),
    )
    c.commit()
    return c


def test_search_finds_by_title_keyword(conn):
    result = search_keyword(conn, "비동기")
    assert result["mode"] == "keyword"
    ids = {item["video"]["id"] for item in result["items"]}
    assert ids == {"v1"}
    assert result["hint"] is None


def test_search_no_results_returns_hint(conn):
    result = search_keyword(conn, "존재하지않는검색어XYZ")
    assert result["items"] == []
    assert result["hint"] is not None


def test_search_finds_updated_summary_via_trigger(conn):
    # summarize.py가 하듯 UPDATE로 요약을 붙였을 때도 검색에 반영되는지 —
    # au 트리거가 videos_fts를 다시 채워야 한다.
    conn.execute("UPDATE videos SET summary = '러스트 소유권 모델을 다룬다' WHERE id = 'v2'")
    conn.commit()

    result = search_keyword(conn, "러스트")
    ids = {item["video"]["id"] for item in result["items"]}
    assert ids == {"v2"}


def test_search_deleted_video_disappears_from_index(conn):
    conn.execute("DELETE FROM videos WHERE id = 'v1'")
    conn.commit()

    result = search_keyword(conn, "비동기")
    assert result["items"] == []


def test_search_special_characters_do_not_raise(conn):
    # FTS5 예약 문자(하이픈 등)가 섞여도 MATCH 문법 오류 없이 동작해야 한다.
    result = search_keyword(conn, 'C++ 비동기-프로그래밍 "테스트"')
    assert isinstance(result["items"], list)
