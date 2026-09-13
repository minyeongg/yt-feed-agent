"""청킹 (docs/05-구현가이드.md Phase 8 step 33/36; docs/03 §4.2 Chunker).

L1: 영상 하나당 제목+요약을 청크 1개로. 자막 세그먼트를 토큰 단위로
나누는 L2(`chunk_transcript`)는 step 36에서 붙인다.
"""

from __future__ import annotations

from datetime import datetime, timezone


def chunk_summary(video_id: str, title: str, summary: str | None, description: str | None = None) -> dict:
    """L1 청크 하나. `text`가 임베딩 입력이자 검색 결과의 `excerpt` 원본이다.

    요약이 아직 없는 영상(LLM 비용 문제로 전체 백필이 안 된 경우가
    흔하다)은 `description`으로 대체한다 — `llm/summarize.py`가 자막 없을
    때 제목+설명으로 대체하는 것과 같은 이유: 커버리지 없이 L1을 평가하면
    "임베딩이 나쁘다"가 아니라 "대상이 없다"를 재는 셈이 된다."""
    body = summary or description
    text = f"{title}\n{body}" if body else title
    return {
        "id": f"{video_id}:0",
        "video_id": video_id,
        "seq": 0,
        "text": text,
        "source": "summary",
        "start_sec": None,
        "end_sec": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
