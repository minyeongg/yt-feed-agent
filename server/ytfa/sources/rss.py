"""채널 RSS 폴링 (docs/05-구현가이드.md Phase 1, step 5; docs/02 ADR-2).

`https://www.youtube.com/feeds/videos.xml?channel_id={id}`는 API 키도
쿼터도 없이 채널당 최근 15개 영상을 준다. 새 영상 감지 전용이며 과거
영상 백필에는 쓸 수 없다(RSS의 한계).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import feedparser

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

_UPSERT_SQL = """
INSERT INTO videos (id, channel_id, title, description, published_at, discovered_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO NOTHING
"""


def fetch_channel_feed(channel_id: str) -> list[dict]:
    """단일 채널의 RSS를 가져와 영상 목록으로 파싱한다.

    네트워크 실패나 파싱 실패는 예외로 던지지 않고 빈 리스트를 반환한다
    (워커가 채널 하나 때문에 멈추면 안 된다).
    """
    try:
        feed = feedparser.parse(FEED_URL.format(channel_id=channel_id))
    except Exception:
        return []
    if feed.bozo and not feed.entries:
        return []

    videos = []
    for entry in feed.entries:
        video_id = entry.get("yt_videoid")
        if not video_id:
            continue
        parsed = entry.get("published_parsed")
        published_at = (
            datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()
            if parsed
            else datetime.now(timezone.utc).isoformat()
        )
        videos.append(
            {
                "id": video_id,
                "channel_id": channel_id,
                "title": entry.get("title", ""),
                "description": entry.get("summary", ""),
                "published_at": published_at,
            }
        )
    return videos


def _store_entries(conn: sqlite3.Connection, channel_id: str, entries: list[dict]) -> int:
    """새 영상만 videos에 넣고, 새로 들어간 행 수를 반환한다."""
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    for v in entries:
        cur = conn.execute(
            _UPSERT_SQL,
            (v["id"], v["channel_id"], v["title"], v["description"], v["published_at"], now),
        )
        new += cur.rowcount
    conn.execute("UPDATE channels SET last_polled_at = ? WHERE id = ?", (now, channel_id))
    return new


def poll_channel(conn: sqlite3.Connection, channel_id: str) -> dict:
    """채널 하나를 폴링한다. `{"fetched": N, "new": M}` 반환 (docs/03 §3.2 poll_now)."""
    entries = fetch_channel_feed(channel_id)
    new = _store_entries(conn, channel_id, entries)
    conn.commit()
    return {"fetched": len(entries), "new": new}


def poll_all_channels(
    conn: sqlite3.Connection,
    channel_ids: Iterable[str] | None = None,
    max_workers: int = 5,
    delay_sec: float = 0.2,
) -> dict:
    """구독 채널 전체를 폴링한다(FR-N1/N7).

    네트워크 요청만 스레드풀로 동시에 보내고(채널 200개 기준 동시성
    5~10 권장, 05번 문서), DB 쓰기는 메인 스레드에서 순차 처리한다
    — sqlite 연결 하나를 여러 스레드가 동시에 쓰지 않기 위해서다.
    배치 사이에 delay_sec만큼 쉬어 요청이 몰리지 않게 한다.
    """
    if channel_ids is None:
        rows = conn.execute("SELECT id FROM channels WHERE subscribed = 1").fetchall()
        channel_ids = [r[0] for r in rows]
    channel_ids = list(channel_ids)

    fetched_total = 0
    new_total = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for i in range(0, len(channel_ids), max_workers):
            batch = channel_ids[i : i + max_workers]
            for channel_id, entries in zip(batch, pool.map(fetch_channel_feed, batch)):
                fetched_total += len(entries)
                new_total += _store_entries(conn, channel_id, entries)
            if i + max_workers < len(channel_ids):
                time.sleep(delay_sec)

    conn.commit()
    return {"fetched": fetched_total, "new": new_total}
