"""L1 인덱싱 — 요약 임베딩 생성/저장 (docs/05-구현가이드.md Phase 8 step 33).

`indexed_level < 1`인 영상 전체가 대상이다 — 요약이 있으면 요약을,
없으면(LLM 비용 때문에 전체 백필이 안 된 경우가 흔하다) 설명으로
대체한다(`chunk_summary` 참고). 청크 1개 + 임베딩 1개를 만들어
`chunks`/`embeddings`에 저장하고 `indexed_level`을 1로 올린다. 이미
인덱싱된 영상은 다시 건드리지 않는다 — 로컬 연산이라 비용은 0이지만,
매 폴링마다 전체를 재임베딩하는 건 불필요한 낭비다.

`batch_size` 단위로 나눠 임베딩+커밋한다 — 처음 대량 백필(수천 건)을
한 번에 `embed()`로 밀어넣었더니 중간 진행 상황을 전혀 볼 수 없었고,
도중에 멈추면(컴퓨터 절전 등) 계산한 게 전부 날아갔다(step 33 실측 중
겪은 문제). 배치마다 커밋하면 중단돼도 이미 처리한 만큼은 남는다.

`limit`으로 이번 호출에서 처리할 최대 건수를 제한할 수 있다 — 수천
건을 한 프로세스로 몇 시간씩 돌리다가 실제로 컴퓨터가 재부팅된 적이
있어서(로컬 GPU 없이 장시간 고부하), 여러 번에 걸쳐 짧게 나눠 돌리고
싶을 때 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection

from ytfa.rag.chunker import chunk_summary
from ytfa.rag.embedder import Embedder

DEFAULT_BATCH_SIZE = 64


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
            conn.execute(
                "UPDATE videos SET indexed_level = 1 WHERE id = ? AND indexed_level < 1", (chunk["video_id"],)
            )

        conn.commit()
        indexed += len(batch_rows)
        if on_batch:
            on_batch(indexed, total)

    return {"indexed": indexed, "remaining": remaining_before - indexed}
