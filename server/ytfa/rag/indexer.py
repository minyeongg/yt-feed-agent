"""L1/L2 인덱싱 — 임베딩 생성/저장 (docs/05-구현가이드.md Phase 8 step 33/36).

L1(`index_l1`): `indexed_level < 1`인 영상 전체가 대상이다 — 요약이
있으면 요약을, 없으면(LLM 비용 때문에 전체 백필이 안 된 경우가 흔하다)
설명으로 대체한다(`chunk_summary` 참고).

L2(`index_l2`): **`watched` 표시된 영상만** 대상이다(ADR-7 — 안 본
영상까지 자막을 전부 청킹하는 건 낭비). 자막을 500토큰/50오버랩으로
나눠 `start_sec`을 보존한다 — 타임스탬프 딥링크(`youtu.be/xxx?t=743`)의
근거.

둘 다 청크+임베딩을 `chunks`/`embeddings`에 저장하고 `indexed_level`을
올린다. 이미 그 레벨로 인덱싱된 영상은 다시 건드리지 않는다 — 로컬
연산이라 비용은 0이지만, 매 폴링마다 전체를 재임베딩하는 건 불필요한
낭비다.

`batch_size` 단위로 나눠 임베딩+커밋한다 — 처음 대량 백필(수천 건)을
한 번에 `embed()`로 밀어넣었더니 중간 진행 상황을 전혀 볼 수 없었고,
도중에 멈추면(컴퓨터 절전 등) 계산한 게 전부 날아갔다(step 33 실측 중
겪은 문제 — 실제로 컴퓨터가 재부팅됐다). 배치마다 커밋하면 중단돼도
이미 처리한 만큼은 남는다.

`limit`으로 이번 호출에서 처리할 최대 건수를 제한할 수 있다 — 수천
건을 한 프로세스로 몇 시간씩 돌리는 대신, 여러 번에 걸쳐 짧게 나눠
돌리고 싶을 때 쓴다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from sqlite3 import Connection

from ytfa.rag.chunker import chunk_summary, chunk_transcript
from ytfa.rag.embedder import Embedder

DEFAULT_BATCH_SIZE = 64


def _store_chunks(conn: Connection, chunks: list[dict], vectors, model_name: str) -> None:
    for chunk, vec in zip(chunks, vectors):
        conn.execute(
            """INSERT OR REPLACE INTO chunks (id, video_id, seq, text, source, start_sec, end_sec, created_at)
               VALUES (:id, :video_id, :seq, :text, :source, :start_sec, :end_sec, :created_at)""",
            chunk,
        )
        conn.execute(
            """INSERT OR REPLACE INTO embeddings (chunk_id, model_name, dim, vector, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (chunk["id"], model_name, vec.shape[0], vec.tobytes(), chunk["created_at"]),
        )


def index_l1(
    conn: Connection,
    embedder: Embedder,
    model_name: str = "BAAI/bge-m3",
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Callable[[int, int], None] | None = None,
    limit: int | None = None,
) -> dict:
    """`on_batch(done, total)`이 주어지면 배치가 끝날 때마다 진행 상황을 알린다.

    `total`은 이번 호출에서 처리할 대상 수(= `min(대기 중인 영상 수, limit)`)다 —
    아직 남은 전체 수가 아니다."""
    rows = conn.execute("SELECT id, title, summary, description FROM videos WHERE indexed_level < 1").fetchall()
    if not rows:
        return {"indexed": 0, "remaining": 0}
    remaining_before = len(rows)
    if limit is not None:
        rows = rows[:limit]

    total = len(rows)
    indexed = 0

    for start in range(0, total, batch_size):
        batch_rows = rows[start : start + batch_size]
        chunks = [chunk_summary(r[0], r[1], r[2], r[3]) for r in batch_rows]
        vectors = embedder.embed([c["text"] for c in chunks])

        _store_chunks(conn, chunks, vectors, model_name)
        for chunk in chunks:
            conn.execute(
                "UPDATE videos SET indexed_level = 1 WHERE id = ? AND indexed_level < 1", (chunk["video_id"],)
            )

        conn.commit()
        indexed += len(batch_rows)
        if on_batch:
            on_batch(indexed, total)

    return {"indexed": indexed, "remaining": remaining_before - indexed}


def index_l2(
    conn: Connection,
    embedder: Embedder,
    model_name: str = "BAAI/bge-m3",
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Callable[[int, int], None] | None = None,
    limit: int | None = None,
) -> dict:
    """`watched` 표시되고 자막이 있는 영상 중 `indexed_level < 2`인 것만
    대상 — 안 본 영상까지 자막을 전부 청킹하는 건 낭비다(ADR-7).
    `batch_size`는 영상 수 기준이다(영상마다 청크 개수가 자막 길이에
    따라 달라서, 실제 임베딩 건수는 배치마다 다르다)."""
    rows = conn.execute(
        """SELECT v.id, t.segments FROM videos v
           JOIN video_states vs ON vs.video_id = v.id AND vs.state = 'watched'
           JOIN transcripts t ON t.video_id = v.id
           WHERE v.indexed_level < 2"""
    ).fetchall()
    if not rows:
        return {"indexed": 0, "remaining": 0}
    remaining_before = len(rows)
    if limit is not None:
        rows = rows[:limit]

    total = len(rows)
    indexed = 0

    for start in range(0, total, batch_size):
        batch_rows = rows[start : start + batch_size]
        batch_chunks: list[dict] = []
        for video_id, segments_json in batch_rows:
            segments = json.loads(segments_json)
            batch_chunks.extend(chunk_transcript(video_id, segments))

        if batch_chunks:
            vectors = embedder.embed([c["text"] for c in batch_chunks])
            _store_chunks(conn, batch_chunks, vectors, model_name)

        for video_id, _ in batch_rows:
            conn.execute("UPDATE videos SET indexed_level = 2 WHERE id = ? AND indexed_level < 2", (video_id,))

        conn.commit()
        indexed += len(batch_rows)
        if on_batch:
            on_batch(indexed, total)

    return {"indexed": indexed, "remaining": remaining_before - indexed}
