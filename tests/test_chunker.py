"""rag/chunker.py의 chunk_transcript 단위 테스트 (docs/05-구현가이드.md
Phase 8, step 36 확인 — "검색 결과에 타임스탬프 딥링크가 나온다"의 전제:
청크가 세그먼트 경계를 지키고 start_sec을 정확히 보존해야 한다).

chunk_summary(L1)는 tests/test_indexer.py에서 이미 간접적으로 검증된다.
"""

from __future__ import annotations

from ytfa.rag.chunker import _approx_tokens, chunk_transcript


def _make_segments(n: int, words_per_seg: int = 20, dur: float = 5.0) -> list[dict]:
    """세그먼트 n개 — 각각 텍스트 길이가 균일해서 토큰 수 계산이 예측 가능하다."""
    return [
        {"start": i * dur, "dur": dur, "text": " ".join(["word"] * words_per_seg)}
        for i in range(n)
    ]


def test_chunk_transcript_empty_segments_returns_empty():
    assert chunk_transcript("v1", []) == []


def test_chunk_transcript_single_short_segment_becomes_one_chunk():
    segments = [{"start": 10.0, "dur": 3.0, "text": "안녕하세요 반갑습니다"}]

    chunks = chunk_transcript("v1", segments)

    assert len(chunks) == 1
    assert chunks[0]["id"] == "v1:1"  # L1이 seq=0을 쓰므로 L2는 1부터
    assert chunks[0]["source"] == "transcript"
    assert chunks[0]["start_sec"] == 10
    assert chunks[0]["end_sec"] == 13


def test_chunk_transcript_never_splits_a_segment_across_chunks():
    """target_tokens를 세그먼트 하나가 이미 넘어도, 그 세그먼트를
    반으로 쪼개지 않는다 — start_sec이 항상 실제 발화 시작점이어야
    딥링크가 의미 있다."""
    long_text = "word " * 1000  # _approx_tokens로 250토큰 근처
    segments = [{"start": 0.0, "dur": 100.0, "text": long_text}]

    chunks = chunk_transcript("v1", segments, target_tokens=50)

    assert len(chunks) == 1
    assert chunks[0]["text"] == long_text


def test_chunk_transcript_splits_long_transcript_into_multiple_chunks_by_target_tokens():
    # 세그먼트 하나당 대략 _approx_tokens(20 * "word ") 토큰. target=50이면
    # 여러 청크로 나뉘어야 한다.
    segments = _make_segments(n=20, words_per_seg=20)
    one_seg_tokens = _approx_tokens(segments[0]["text"])
    assert one_seg_tokens > 0

    chunks = chunk_transcript("v1", segments, target_tokens=one_seg_tokens * 3, overlap_tokens=0)

    assert len(chunks) > 1
    # seq는 1부터 연속으로 증가
    assert [c["seq"] for c in chunks] == list(range(1, 1 + len(chunks)))


def test_chunk_transcript_start_sec_matches_first_segment_in_chunk():
    segments = _make_segments(n=10, words_per_seg=20)
    one_seg_tokens = _approx_tokens(segments[0]["text"])

    chunks = chunk_transcript("v1", segments, target_tokens=one_seg_tokens * 2, overlap_tokens=0)

    # 각 청크의 start_sec은 그 청크에 포함된 세그먼트 중 가장 이른 것의 start와 같아야 한다.
    for chunk in chunks:
        assert chunk["start_sec"] in {int(s["start"]) for s in segments}


def test_chunk_transcript_overlap_reuses_trailing_segments():
    segments = _make_segments(n=20, words_per_seg=20)
    one_seg_tokens = _approx_tokens(segments[0]["text"])

    no_overlap = chunk_transcript("v1", segments, target_tokens=one_seg_tokens * 3, overlap_tokens=0)
    with_overlap = chunk_transcript(
        "v1", segments, target_tokens=one_seg_tokens * 3, overlap_tokens=one_seg_tokens * 2
    )

    # 겹치게 하면 같은 자막을 커버하는 데 청크가 더 많이(또는 같게) 필요하다.
    assert len(with_overlap) >= len(no_overlap)
    # 두 번째 청크가 첫 번째 청크의 끝부분 텍스트를 다시 포함해야 한다.
    assert with_overlap[1]["start_sec"] < with_overlap[0]["end_sec"]


def test_chunk_transcript_progresses_even_with_huge_overlap_request():
    """overlap_tokens가 비정상적으로 커도(target_tokens보다 큼) 무한
    루프에 빠지지 않고 끝까지 진행해야 한다."""
    segments = _make_segments(n=5, words_per_seg=20)

    chunks = chunk_transcript("v1", segments, target_tokens=10, overlap_tokens=10_000)

    assert len(chunks) >= 1
    assert chunks[-1]["end_sec"] is not None
