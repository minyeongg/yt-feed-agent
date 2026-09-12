"""Google Takeout 구독 CSV 임포트 (docs/05-구현가이드.md Phase 1, step 4).

Takeout이 주는 CSV는 헤더가 "채널 ID,채널 URL,채널 제목" 세 컬럼뿐이라
설명·썸네일·구독자 수 같은 나머지 필드는 비워둔다. 필요해지면
sources/youtube_api.py의 channels.list로 보강한다.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_UPSERT_SQL = """
INSERT INTO channels (id, title, added_at)
VALUES (?, ?, ?)
ON CONFLICT(id) DO NOTHING
"""
# 기존 채널의 weight/category_locked/needs_review 등은 재수집으로 덮어쓰지
# 않는다 (docs/03-API명세.md의 dedup 규칙과 동일한 원칙).


def import_takeout_csv(csv_path: Path, conn: sqlite3.Connection) -> int:
    """CSV의 각 행을 channels 테이블에 upsert하고, 새로 추가된 행 수를 반환한다."""
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            channel_id = row["채널 ID"].strip()
            title = row["채널 제목"].strip()
            if not channel_id:
                continue
            cur = conn.execute(_UPSERT_SQL, (channel_id, title, now))
            inserted += cur.rowcount

    conn.commit()
    return inserted


def count_channels(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
