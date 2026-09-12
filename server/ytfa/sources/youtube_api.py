"""YouTube Data API v3 메타 보강 (docs/05-구현가이드.md Phase 1, step 6).

`videos.list`로 duration/viewCount를 채우고, 60초 이하 영상은
kind="short"로 표시한다. 50건씩 배치 처리 — 1유닛/50건(ADR-2).
`search.list`(100유닛)는 절대 쓰지 않는다.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Iterator

from googleapiclient.discovery import build

BATCH_SIZE = 50
SHORT_MAX_SEC = 60

_DURATION_RE = re.compile(
    r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?"
)


def parse_iso8601_duration(value: str) -> int:
    """ISO 8601 duration("PT4M13S")을 초 단위 정수로 변환한다. 파싱 실패 시 0."""
    match = _DURATION_RE.fullmatch(value)
    if not match:
        return 0
    parts = match.groupdict(default="0")
    days, hours, minutes, seconds = (int(parts[k] or 0) for k in ("days", "hours", "minutes", "seconds"))
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def get_client(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def _batched(seq: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def fetch_video_meta(client, video_ids: Iterable[str]) -> dict[str, dict]:
    """videos.list 호출 1회(최대 50개 id). {video_id: {duration_sec, view_count, kind}}."""
    resp = client.videos().list(
        part="contentDetails,statistics,snippet",
        id=",".join(video_ids),
    ).execute()

    meta = {}
    for item in resp.get("items", []):
        duration_sec = parse_iso8601_duration(item["contentDetails"]["duration"])
        stats = item.get("statistics", {})
        view_count = int(stats["viewCount"]) if "viewCount" in stats else None

        live_status = item["snippet"].get("liveBroadcastContent", "none")
        if live_status in ("live", "upcoming"):
            kind = live_status
        elif duration_sec <= SHORT_MAX_SEC:
            kind = "short"
        else:
            kind = "video"

        meta[item["id"]] = {"duration_sec": duration_sec, "view_count": view_count, "kind": kind}
    return meta


def enrich_pending_videos(conn: sqlite3.Connection, api_key: str, batch_size: int = BATCH_SIZE) -> dict:
    """meta_enriched=0인 영상을 전부 보강한다. {"enriched": N, "quota_units": U} 반환."""
    video_ids = [r[0] for r in conn.execute("SELECT id FROM videos WHERE meta_enriched = 0").fetchall()]
    if not video_ids:
        return {"enriched": 0, "quota_units": 0}

    client = get_client(api_key)
    enriched = 0
    quota_units = 0

    for batch in _batched(video_ids, batch_size):
        meta = fetch_video_meta(client, batch)
        quota_units += 1  # videos.list는 id 개수와 무관하게 호출당 1유닛

        for video_id in batch:
            m = meta.get(video_id)
            if m is None:
                # 삭제/비공개 처리된 영상 — 재시도 루프에 계속 걸리지 않게 완료 표시만 한다.
                conn.execute("UPDATE videos SET meta_enriched = 1 WHERE id = ?", (video_id,))
                continue
            conn.execute(
                """UPDATE videos
                   SET duration_sec = ?, view_count = ?, kind = ?, meta_enriched = 1
                   WHERE id = ?""",
                (m["duration_sec"], m["view_count"], m["kind"], video_id),
            )
            enriched += 1
        conn.commit()

    return {"enriched": enriched, "quota_units": quota_units}
