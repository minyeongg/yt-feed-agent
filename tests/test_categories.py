"""core/categories.py 단위 테스트 (docs/03-API명세.md §2.2)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from ytfa.core.categories import assign_category_to_channel, list_categories
from ytfa.db import init_db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO categories (id, name, ord) VALUES ('dev', '개발', 0)")
    c.execute("INSERT INTO categories (id, name, ord, is_default) VALUES ('etc', '기타', 99, 1)")
    c.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC1', '채널1', ?)", (now,))
    c.execute("INSERT INTO channel_categories (channel_id, category_id, assigned_by) VALUES ('UC1', 'dev', 'auto')")
    c.execute(
        "INSERT INTO videos (id, channel_id, title, published_at, discovered_at) VALUES ('v1', 'UC1', '영상1', ?, ?)",
        (now, now),
    )
    c.commit()
    return c


def test_list_categories_includes_counts_and_empty_ones(conn):
    items = {c["id"]: c for c in list_categories(conn)}

    assert items["dev"]["name"] == "개발"
    assert items["dev"]["channel_count"] == 1
    assert items["dev"]["new_video_count"] == 1

    # 채널이 하나도 없는 카테고리("기타")도 0건으로 나와야 한다(누락 아님)
    assert items["etc"]["channel_count"] == 0
    assert items["etc"]["new_video_count"] == 0
    assert items["etc"]["is_default"] is True


def test_list_categories_ordered_by_ord(conn):
    items = list_categories(conn)
    assert [c["id"] for c in items] == ["dev", "etc"]


def test_assign_category_replaces_and_locks(conn):
    result = assign_category_to_channel(conn, "UC1", ["etc"])

    assert result["category_ids"] == ["etc"]
    assert result["category_locked"] is True

    links = [
        tuple(r) for r in conn.execute("SELECT category_id, assigned_by FROM channel_categories WHERE channel_id='UC1'").fetchall()
    ]
    assert links == [("etc", "user")]  # 기존 'dev' 자동 배정이 교체됨

    locked = conn.execute("SELECT category_locked FROM channels WHERE id='UC1'").fetchone()[0]
    assert locked == 1


def test_assign_category_unknown_channel_returns_error(conn):
    result = assign_category_to_channel(conn, "UC_NOPE", ["dev"])
    assert result["error"] == "NOT_FOUND"


def test_assign_category_unknown_category_returns_error(conn):
    result = assign_category_to_channel(conn, "UC1", ["ghost"])
    assert result["error"] == "NOT_FOUND"
    # 실패했으면 기존 배정을 건드리지 않아야 한다
    links = conn.execute("SELECT category_id FROM channel_categories WHERE channel_id='UC1'").fetchall()
    assert [r[0] for r in links] == ["dev"]
