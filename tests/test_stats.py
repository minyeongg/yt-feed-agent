"""core/stats.py 단위 테스트 (docs/03-API명세.md §3.1 `get_watch_stats`)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ytfa.core.stats import get_watch_stats
from ytfa.db import init_db


def _iso(hours_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    now = _iso()
    c.execute("INSERT INTO channels (id, title, added_at) VALUES ('UC1', '채널', ?)", (now,))
    c.execute("INSERT INTO categories (id, name) VALUES ('dev', '개발')")
    c.execute("INSERT INTO channel_categories (channel_id, category_id, assigned_by) VALUES ('UC1', 'dev', 'auto')")
    for vid, state, hours_ago in [
        ("v1", "watched", 1),
        ("v2", "watched", 2),
        ("v3", "skipped", 1),
        ("v4", "watched", 1000),  # 범위 밖
    ]:
        c.execute(
            "INSERT INTO videos (id, channel_id, title, published_at, discovered_at) VALUES (?, 'UC1', ?, ?, ?)",
            (vid, vid, now, now),
        )
        c.execute(
            "INSERT INTO video_states (video_id, state, updated_at) VALUES (?, ?, ?)",
            (vid, state, _iso(hours_ago)),
        )
    c.commit()
    return c


def test_get_watch_stats_counts_within_window(conn):
    result = get_watch_stats(conn, since_hours=48)

    assert result["watched"] == 2
    assert result["skipped"] == 1
    assert result["not_interested"] == 0
    assert result["state_changes_total"] == 3  # v4는 범위 밖


def test_get_watch_stats_by_category(conn):
    result = get_watch_stats(conn, since_hours=48)
    assert result["by_category"]["dev"]["watched"] == 2
    assert result["by_category"]["dev"]["name"] == "개발"


def test_get_watch_stats_empty_window_returns_zeros(conn):
    result = get_watch_stats(conn, since_hours=0)
    assert result["watched"] == 0
    assert result["by_category"] == {}
