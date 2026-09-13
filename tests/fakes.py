"""여러 RAG 테스트 파일이 공유하는 가짜 임베더.

`hash(word)`는 파이썬 프로세스마다 해시 시드가 랜덤이라(문자열 해시
랜덤화) 실행할 때마다 단어→버킷 매핑이 달라진다 — 같은 테스트가 어떤
실행에선 통과하고 어떤 실행에선 실패하는 진짜 플레이키 버그였다.
`zlib.crc32`는 입력이 같으면 항상 같은 값을 내는 결정적 해시라 이 문제가
없다.
"""

from __future__ import annotations

import zlib

import numpy as np

DIM = 16


class FakeEmbedder:
    """단어 겹침을 L2 정규화된 벡터로 흉내 내는 결정적 가짜(테스트 전용)."""

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for text in texts:
            v = np.zeros(DIM, dtype=np.float32)
            for word in text.split():
                v[zlib.crc32(word.encode()) % DIM] += 1.0
            norm = np.linalg.norm(v)
            vectors.append(v / norm if norm > 0 else v)
        return np.array(vectors, dtype=np.float32)
