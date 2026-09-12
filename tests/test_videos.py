"""core/videos.py 단위 테스트 (docs/05-구현가이드.md Phase 3, step 14 확인)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from ytfa.core.videos import feed_counts, list_feed
from ytfa.db import init_db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = datetime.now(timezone.utc).isoformat()

    c.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC1', '채널1', ?)", (now,))
    c.execute("INSERT INTO categories (id, name) VALUES ('dev', '개발')")
    c.execute("INSERT INTO channel_categories (channel_id, category_id, assigned_by) VALUES ('UC1', 'dev', 'auto')")

    c.execute(
        """INSERT INTO videos (id, channel_id, title, published_at, discovered_at, kind)
           VALUES ('v_new', 'UC1', '새 영상', ?, ?, 'video')""",
        (now, now),
    )
    c.execute(
        """INSERT INTO videos (id, channel_id, title, published_at, discovered_at, kind)
           VALUES ('v_short', 'UC1', '숏폼', ?, ?, 'short')""",
        (now, now),
    )
    c.execute(
        """INSERT INTO videos (id, channel_id, title, published_at, discovered_at, kind)
           VALUES ('v_watched', 'UC1', '이미 본 영상', ?, ?, 'video')""",
        (now, now),
    )
    c.execute(
        "INSERT INTO video_states (video_id, state, updated_at) VALUES ('v_watched', 'watched', ?)", (now,)
    )
    c.commit()
    return c


def test_list_feed_defaults_to_new_and_seen(conn):
    result = list_feed(conn)
    ids = {item["id"] for item in result["items"]}
    assert "v_new" in ids
    assert "v_watched" not in ids  # 기본 상태 필터(new,seen)에 안 걸림


def test_list_feed_excludes_shorts_by_default(conn):
    result = list_feed(conn, include_shorts=False)
    ids = {item["id"] for item in result["items"]}
    assert "v_short" not in ids

    result_with_shorts = list_feed(conn, include_shorts=True)
    ids_with_shorts = {item["id"] for item in result_with_shorts["items"]}
    assert "v_short" in ids_with_shorts


def test_list_feed_video_card_shape(conn):
    result = list_feed(conn)
    card = next(item for item in result["items"] if item["id"] == "v_new")
    assert card["channel"] == {"id": "UC1", "title": "채널1", "thumbnail_url": ""}
    assert card["url"] == "https://youtu.be/v_new"
    assert card["categories"] == ["dev"]
    assert card["state"] == "new"


def test_list_feed_filters_by_category(conn):
    conn.execute("INSERT INTO categories (id, name) VALUES ('music', '음악')")
    conn.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC2', '채널2', datetime('now'))")
    conn.execute(
        "INSERT INTO channel_categories (channel_id, category_id, assigned_by) VALUES ('UC2', 'music', 'auto')"
    )
    conn.execute(
        """INSERT INTO videos (id, channel_id, title, published_at, discovered_at)
           VALUES ('v_music', 'UC2', '음악 영상', datetime('now'), datetime('now'))"""
    )
    conn.commit()

    result = list_feed(conn, category="music")
    ids = {item["id"] for item in result["items"]}
    assert ids == {"v_music"}


def test_feed_counts_by_category(conn):
    result = feed_counts(conn, state="new")
    # v_new + v_short 둘 다 'new' 상태, 둘 다 dev — counts는 kind(숏폼) 필터가 없다
    assert result["total"] == 2
    assert result["by_category"] == {"dev": 2}


def test_feed_counts_watched_state(conn):
    result = feed_counts(conn, state="watched")
    assert result["total"] == 1
