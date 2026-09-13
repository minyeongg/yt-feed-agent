"""임베딩 (docs/05-구현가이드.md Phase 8, step 32; docs/02 ADR-7).

로컬 BGE-m3(sentence-transformers)를 기본으로 쓴다 — 비용 0이라
청킹 전략을 바꿔서 전체 재임베딩을 열 번 돌려도 공짜다(ADR-7의 진짜
이유). `Embedder` 프로토콜 뒤에 숨겨서, "30분 룰"(가이드 step 32 —
로컬 세팅이 30분 안에 안 잡히면 즉시 API 임베딩으로 갈아탄다)을 지킬
때 교체 비용을 싸게 만든다.

**`device="cpu"`를 명시하는 이유(실측 확인, 매우 중요)**: `device`를 안
정하면 sentence-transformers가 이 Mac에서 MPS(GPU)를 자동으로 골라
쓴다. 그런데 이 모델(XLM-RoBERTa-large 기반)의 attention 연산이 이
PyTorch/macOS 조합에서 MPS의 "math" 폴백 경로를 타면서 **텍스트 하나
임베딩에 30분 넘게 걸리는** 걸 실측으로 확인했다(`Batches: 100%
[31:15<00:00, 1875.18s/it]`) — 같은 호출을 `device="cpu"`로 강제하면
로딩 7초, 인코딩 2초로 끝난다. 이게 Phase 8 인덱싱이 예상보다 훨씬
오래 걸렸던 진짜 원인이었다(장시간 고부하로 컴퓨터가 재부팅되기까지
했다). MPS가 이 모델에 더 빨라지기 전까지는 CPU가 사실상 유일한
선택지다."""

from __future__ import annotations

from functools import lru_cache
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

            self._model = SentenceTransformer(self.model_name, device="cpu")
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


@lru_cache(maxsize=1)
def get_embedder(model_name: str = "BAAI/bge-m3") -> LocalBGEEmbedder:
    """요청마다 새로 만들면 모델을 매번 다시 로드하게 된다(수 초~수십 초) —
    API 서버·인덱싱 CLI가 공유해서 쓸 프로세스 전역 싱글턴."""
    return LocalBGEEmbedder(model_name)
