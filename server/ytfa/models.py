"""Pydantic data models. See docs/03-API명세.md §1 for the field-by-field spec."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Channel(BaseModel):
    id: str  # UC... (채널 ID)
    title: str
    description: str = ""
    thumbnail_url: str = ""
    uploads_playlist_id: str = ""  # UU... (백필용)
    subscriber_count: int | None = None
    subscribed: bool = True  # 구독 해지 시 False, 삭제하지 않음
    category_ids: list[str] = []
    category_locked: bool = False  # 사용자가 손댔으면 True → 자동 재분류 금지
    needs_review: bool = False  # 새로 분류됐고 확인 안 됨
    weight: float = 1.0  # 랭킹 가중치. 스킵이 쌓이면 내려감
    added_at: datetime
    last_polled_at: datetime | None = None


class Category(BaseModel):
    id: str  # slug ("dev", "ai", "music")
    name: str  # 표시명 ("개발")
    order: int = 0  # 탭 정렬 순서
    color: str | None = None
    is_default: bool = False  # 자동 생성된 "기타"


class Video(BaseModel):
    id: str  # 11자 video ID
    channel_id: str
    title: str
    description: str = ""
    published_at: datetime
    duration_sec: int | None = None  # videos.list 로 보강. None이면 미보강
    view_count: int | None = None
    thumbnail_url: str = ""
    kind: Literal["video", "short", "live", "upcoming"] = "video"

    discovered_at: datetime  # 우리가 처음 본 시각
    meta_enriched: bool = False
    transcript_status: Literal["pending", "ok", "none", "failed"] = "pending"
    summary: str | None = None  # 한 줄 요약 (80자 내외)
    verdict: dict | None = None  # 볼 가치 판정
    summarized_at: datetime | None = None
    indexed_level: int = 0  # 0=미인덱싱, 1=요약임베딩, 2=자막청킹


class VideoState(BaseModel):
    video_id: str
    state: Literal["new", "seen", "watched", "skipped", "not_interested"] = "new"
    # new: 아직 피드에 안 뜸 / seen: 피드에서 봄 / watched: 시청 완료
    # skipped: 넘김 / not_interested: 관심 없음 (채널 가중치 하락)
    updated_at: datetime
    watch_seconds: int | None = None


class Chunk(BaseModel):
    id: str  # "{video_id}:{seq}"
    video_id: str
    seq: int
    text: str
    source: Literal["summary", "transcript"]
    start_sec: int | None = None  # transcript 청크만. 타임스탬프 점프의 근거
    end_sec: int | None = None
