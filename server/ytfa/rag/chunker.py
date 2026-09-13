"""청킹 (docs/05-구현가이드.md Phase 8 step 33/36; docs/03 §4.2 Chunker).

L1(`chunk_summary`): 영상 하나당 제목+요약을 청크 1개로.
L2(`chunk_transcript`): `watched` 영상의 자막을 500토큰/50오버랩으로
분할하며 `start_sec`/`end_sec`을 보존한다 — 타임스탬프 딥링크의 근거.
"""

from __future__ import annotations

from datetime import datetime, timezone

DEFAULT_TARGET_TOKENS = 500
DEFAULT_OVERLAP_TOKENS = 50
# L1 청크가 항상 seq=0을 쓰므로(chunk_summary) L2는 1부터 시작해 같은
# 영상 안에서 id("{video_id}:{seq}")가 겹치지 않게 한다.
TRANSCRIPT_START_SEQ = 1


def _approx_tokens(text: str) -> int:
    """정확한 토크나이저 없이 대략치(4글자≈1토큰, 영어 기준 경험칙)만
    쓴다 — 청킹 경계를 대략 맞추는 용도라 정밀할 필요가 없다. 한국어는
    이보다 토큰이 더 나올 수 있어 청크가 목표보다 살짝 작게 잡히는 쪽으로
    치우치는데, 잘리는 것보다는 안전하다."""
    return max(1, len(text) // 4)


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


def chunk_transcript(
    video_id: str,
    segments: list[dict],
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    start_seq: int = TRANSCRIPT_START_SEQ,
) -> list[dict]:
    """자막 `segments`([{start, dur, text}], `sources/transcript.py` 포맷)를
    세그먼트 경계를 지키며 target_tokens 단위로 나눈다 — 세그먼트 중간을
    끊지 않아야 `start_sec`가 항상 실제 발화 시작점을 가리킨다. 청크
    사이 overlap_tokens만큼 겹쳐서(뒤 청크가 앞 청크 끝을 다시 포함)
    경계에서 문맥이 뚝 끊기는 걸 줄인다."""
    if not segments:
        return []

    now = datetime.now(timezone.utc).isoformat()
    chunks: list[dict] = []
    seq = start_seq
    i = 0
    n = len(segments)

    while i < n:
        start_i = i
        token_count = 0
        chunk_segments: list[dict] = []
        while i < n and (not chunk_segments or token_count < target_tokens):
            seg = segments[i]
            chunk_segments.append(seg)
            token_count += _approx_tokens(seg["text"])
            i += 1

        chunks.append(
            {
                "id": f"{video_id}:{seq}",
                "video_id": video_id,
                "seq": seq,
                "text": " ".join(s["text"] for s in chunk_segments),
                "source": "transcript",
                "start_sec": int(chunk_segments[0]["start"]),
                "end_sec": int(chunk_segments[-1]["start"] + chunk_segments[-1].get("dur", 0)),
                "created_at": now,
            }
        )
        seq += 1

        if i >= n:
            break

        # 다음 청크의 시작점을 overlap_tokens만큼 뒤로 물린다. start_i+1
        # 아래로는 절대 안 내려가게 해서(최소 한 세그먼트는 항상 전진)
        # 무한 루프를 방지한다.
        back = i
        overlap = 0
        while back > start_i + 1 and overlap < overlap_tokens:
            back -= 1
            overlap += _approx_tokens(segments[back]["text"])
        i = back

    return chunks
