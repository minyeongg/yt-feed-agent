"""임베딩 (docs/05-구현가이드.md Phase 8, step 32; docs/02 ADR-7).

로컬 BGE-m3(sentence-transformers)를 기본으로 쓴다 — 비용 0이라
청킹 전략을 바꿔서 전체 재임베딩을 열 번 돌려도 공짜다(ADR-7의 진짜
이유). `Embedder` 프로토콜 뒤에 숨겨서, "30분 룰"(가이드 step 32 —
로컬 세팅이 30분 안에 안 잡히면 즉시 API 임베딩으로 갈아탄다)을 지킬
때 교체 비용을 싸게 만든다.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

EMBEDDING_DIM = 1024  # BGE-m3 기본 차원


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """텍스트 목록 → (N, dim) L2 정규화된 벡터 배열."""
        ...


class LocalBGEEmbedder:
    """`sentence-transformers`의 BGE-m3. 첫 `embed()` 호출 때 모델을 lazy 로드한다.

    (다국어 지원이 한국어·영어가 섞인 이 프로젝트의 자막/제목에 맞고,
    로컬이라 시청 이력이 외부로 안 나간다 — ADR-7 그대로.)
    """

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        model = self._load()
        vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return vectors.astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """L2 정규화된 벡터끼리는 내적이 곧 코사인 유사도다."""
    return float(np.dot(a, b))
