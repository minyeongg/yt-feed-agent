"""core/categories.py 단위 테스트 (docs/03-API명세.md §2.2)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from ytfa.core.categories import list_categories
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
