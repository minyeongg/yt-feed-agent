"""rag/embedder.py 단위 테스트 (docs/05-구현가이드.md Phase 8, step 32 확인).

실제 BGE-m3 모델은 로드하지 않는다(2GB+ 다운로드/로드 비용) — 가짜
`SentenceTransformer`로 배선(normalize_embeddings 전달, lazy load, 빈
입력 처리)만 검증한다. "임의 문장 두 개의 유사도가 상식과 맞는다"는
확인 기준은 실제 모델로 터미널에서 직접 실측했다(날씨 문장 쌍 0.815 vs
날씨/주식 쌍 0.386 — 상식과 일치).
"""

from __future__ import annotations

import numpy as np

from ytfa.rag.embedder import EMBEDDING_DIM, LocalBGEEmbedder, cosine_similarity


class _FakeSentenceTransformer:
    """실제 모델 대신 텍스트마다 결정적인 벡터를 만드는 가짜."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.encode_calls: list[dict] = []

    def encode(self, texts, *, normalize_embeddings, convert_to_numpy):
        self.encode_calls.append(
            {"texts": texts, "normalize_embeddings": normalize_embeddings, "convert_to_numpy": convert_to_numpy}
        )
        # 텍스트 길이를 시드로 써서 재현 가능한 벡터를 만들고 L2 정규화한다.
        vectors = []
        for text in texts:
            rng = np.random.default_rng(seed=len(text))
            v = rng.random(EMBEDDING_DIM).astype(np.float32)
            vectors.append(v / np.linalg.norm(v))
        return np.array(vectors, dtype=np.float32)


def test_cosine_similarity_identical_vector_is_one():
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_similarity(v, v) == 1.0


def test_cosine_similarity_orthogonal_vectors_is_zero():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert cosine_similarity(a, b) == 0.0


def test_embed_empty_list_returns_correctly_shaped_empty_array_without_loading_model():
    embedder = LocalBGEEmbedder()
    vectors = embedder.embed([])
    assert vectors.shape == (0, EMBEDDING_DIM)
    assert embedder._model is None  # 모델을 아예 안 건드렸는지(lazy load 확인)


def test_embed_lazy_loads_model_only_once_and_passes_normalize_flag():
    embedder = LocalBGEEmbedder()
    fake_model = _FakeSentenceTransformer("BAAI/bge-m3")
    embedder._model = fake_model  # 실제 로드를 건너뛰고 가짜를 주입

    vectors = embedder.embed(["안녕하세요", "반갑습니다"])

    assert vectors.shape == (2, EMBEDDING_DIM)
    assert vectors.dtype == np.float32
    assert len(fake_model.encode_calls) == 1
    assert fake_model.encode_calls[0]["normalize_embeddings"] is True
    assert fake_model.encode_calls[0]["convert_to_numpy"] is True


def test_embed_output_vectors_are_l2_normalized():
    embedder = LocalBGEEmbedder()
    embedder._model = _FakeSentenceTransformer("BAAI/bge-m3")

    vectors = embedder.embed(["a", "bb", "ccc"])

    for v in vectors:
        assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5
