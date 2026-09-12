"""core/videos.py 단위 테스트 (docs/05-구현가이드.md Phase 3, step 14 확인)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from ytfa.core.videos import feed_counts, get_video_card, list_feed, set_video_state
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


def test_list_feed_since_hours_excludes_old_videos(conn):
    conn.execute(
        """INSERT INTO videos (id, channel_id, title, published_at, discovered_at)
           VALUES ('v_old', 'UC1', '오래된 영상', datetime('now', '-100 hours'), datetime('now'))"""
    )
    conn.commit()

    result = list_feed(conn, since_hours=48)
    ids = {item["id"] for item in result["items"]}
    assert "v_new" in ids
    assert "v_old" not in ids


def test_get_video_card_returns_none_for_unknown(conn):
    assert get_video_card(conn, "nope") is None


def test_get_video_card_matches_list_feed_shape(conn):
    card = get_video_card(conn, "v_new")
    assert card["id"] == "v_new"
    assert card["channel"]["id"] == "UC1"
    assert card["categories"] == ["dev"]


def test_set_video_state_upserts_and_signals_index_queue(conn):
    result = set_video_state(conn, "v_new", "watched", watch_seconds=120)
    assert result == {"ok": True, "queued_for_index": True}

    row = conn.execute("SELECT state, watch_seconds FROM video_states WHERE video_id='v_new'").fetchone()
    assert tuple(row) == ("watched", 120)

    # 상태를 바꿔도(watched가 아니면) queued_for_index는 False
    result2 = set_video_state(conn, "v_new", "skipped")
    assert result2 == {"ok": True, "queued_for_index": False}


def test_set_video_state_unknown_video_returns_error(conn):
    result = set_video_state(conn, "nope", "watched")
    assert result["ok"] is False
    assert result["error"] == "NOT_FOUND"
