"""자막 수집 (docs/05-구현가이드.md Phase 2, step 10; docs/02 ADR-3).

`youtube-transcript-api`는 비공식 경로다(공식 `captions.download`는
영상 소유자 OAuth가 필요해 제3자 영상엔 못 쓴다 — ADR-3). 자막이 아예
없는 영상도 많고 드물게 IP 차단도 있을 수 있다. 그래서 **실패를 예외로
던지지 않고** `transcript_status`(ok|none|failed)로 기록한다 — 자막이
없으면 제목+설명만으로 요약·분류·L0 검색이 도는 게 정상 동작이다
(ADR-3 축소 동작). 워커가 영상 하나 때문에 멈추면 안 된다.

`TranscriptProvider` 뒤에 숨겨 둬서, 나중에 유료 대행 API(ADR-3 선택지
C)로 바꿔도 이 파일 밖은 안 건드린다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Literal, Protocol

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)

logger = logging.getLogger(__name__)

TranscriptStatus = Literal["ok", "none", "failed"]

# 한국어 구독 채널이 많은 개인 목록 기준 — 한국어 우선, 없으면 영어.
DEFAULT_LANGUAGES: tuple[str, ...] = ("ko", "en")

# "자막이 아예 없음"으로 취급하는 것으로 알려진 예외들 — 이건 'failed'가
# 아니라 'none'이다(ADR-3: 자막 없는 영상도 흔하고 정상 케이스).
_NO_TRANSCRIPT_EXCEPTIONS = (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable)


@dataclass
class TranscriptResult:
    segments: list[dict]  # [{"start": float, "dur": float, "text": str}, ...]
    lang: str
    is_auto: bool


class TranscriptProvider(Protocol):
    """자막 공급자 인터페이스. 교체 가능하게 이 뒤에 숨긴다(ADR-3)."""

    def fetch(self, video_id: str, languages: tuple[str, ...] = DEFAULT_LANGUAGES) -> TranscriptResult | None:
        """자막을 가져온다. 자막이 아예 없으면 None. 그 외 실패는 예외로
        던진다 — 호출부(`fetch_and_store_transcript`)가 'failed'로 구분해
        기록한다."""
        ...


class YoutubeTranscriptApiProvider:
    """`youtube-transcript-api` 래퍼 (ADR-3 선택지 B, 비용 0)."""

    def __init__(self) -> None:
        self._api = YouTubeTranscriptApi()

    def fetch(self, video_id: str, languages: tuple[str, ...] = DEFAULT_LANGUAGES) -> TranscriptResult | None:
        try:
            transcript_list = self._api.list(video_id)
            transcript = transcript_list.find_transcript(languages)
        except _NO_TRANSCRIPT_EXCEPTIONS:
            return None

        fetched = self._api.fetch(video_id, languages=[transcript.language_code])
        segments = [{"start": s.start, "dur": s.duration, "text": s.text} for s in fetched]
        return TranscriptResult(segments=segments, lang=transcript.language_code, is_auto=transcript.is_generated)


def fetch_and_store_transcript(
    conn: Connection,
    video_id: str,
    provider: TranscriptProvider | None = None,
) -> TranscriptStatus:
    """자막을 가져와 저장하고, 결과 상태를 `videos.transcript_status`에 남긴다.

    무슨 일이 있어도 예외를 밖으로 던지지 않는다 — 반환값이 곧 결과다.
    """
    provider = provider or YoutubeTranscriptApiProvider()
    now = datetime.now(timezone.utc).isoformat()
    status: TranscriptStatus

    try:
        result = provider.fetch(video_id)
    except Exception:
        logger.warning("자막 수집 실패: %s", video_id, exc_info=True)
        result = None
        status = "failed"
    else:
        status = "none" if result is None else "ok"

    if status == "ok" and result is not None:
        conn.execute(
            """INSERT INTO transcripts (video_id, lang, is_auto, segments, fetched_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET
                   lang = excluded.lang, is_auto = excluded.is_auto,
                   segments = excluded.segments, fetched_at = excluded.fetched_at""",
            (video_id, result.lang, int(result.is_auto), json.dumps(result.segments, ensure_ascii=False), now),
        )

    conn.execute("UPDATE videos SET transcript_status = ? WHERE id = ?", (status, video_id))
    conn.commit()
    return status


def fetch_pending_transcripts(
    conn: Connection,
    provider: TranscriptProvider | None = None,
    limit: int | None = None,
) -> dict:
    """`transcript_status='pending'`인 영상을 전부(또는 `limit`개) 처리한다."""
    provider = provider or YoutubeTranscriptApiProvider()
    rows = conn.execute(
        "SELECT id FROM videos WHERE transcript_status = 'pending' ORDER BY published_at DESC"
    ).fetchall()
    video_ids = [r[0] for r in rows]
    if limit is not None:
        video_ids = video_ids[:limit]

    counts = {"ok": 0, "none": 0, "failed": 0}
    for video_id in video_ids:
        status = fetch_and_store_transcript(conn, video_id, provider)
        counts[status] += 1
    return {"processed": len(video_ids), **counts}
