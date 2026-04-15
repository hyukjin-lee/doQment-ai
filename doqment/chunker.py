"""시맨틱 임베딩 기반 transcript 청킹.

기본 전략:
1. all-MiniLM-L6-v2 모델로 각 세그먼트를 임베딩
2. 슬라이딩 윈도우로 좌우 평균 임베딩의 코사인 거리 계산
3. 거리 상위 N%(기본 85th percentile)인 위치 = 주제 경계
4. 경계에서 분할, min/max 단어 수로 보정
5. sentence-transformers 미설치 시 기존 단어 수 방식으로 폴백
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from doqment.transcript import TranscriptSegment, format_timestamp


# ── 지연 로딩 ─────────────────────────────────────────────
_encoder: Any = None   # SentenceTransformer 인스턴스 or False(폴백)


def _get_encoder() -> Any:
    """첫 호출 시 all-MiniLM-L6-v2 로딩, 이후 캐시 재사용."""
    global _encoder
    if _encoder is None:
        try:
            from sentence_transformers import SentenceTransformer
            _encoder = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            _encoder = False   # 미설치 → 폴백 플래그
    return _encoder


# ── 데이터클래스 ──────────────────────────────────────────

@dataclass
class TranscriptChunk:
    segments: list[TranscriptSegment]
    text: str           # 전체 청크 텍스트 (공백으로 이어붙임)
    start_time: float   # 청크 시작 시간 (초)
    end_time: float     # 청크 종료 시간 (초)
    start_ts: str       # "1:23" 형식
    end_ts: str         # "4:56" 형식
    chunk_index: int    # 0-based
    total_chunks: int = field(default=0)
    split_reason: str = "word_count"  # "semantic" | "max_words" | "end"


def _make_chunk(
    segments: list[TranscriptSegment],
    index: int,
    split_reason: str = "word_count",
) -> TranscriptChunk:
    text = " ".join(s.text for s in segments)
    start_time = segments[0].start
    end_time = segments[-1].start + segments[-1].duration
    return TranscriptChunk(
        segments=segments,
        text=text,
        start_time=start_time,
        end_time=end_time,
        start_ts=format_timestamp(start_time),
        end_ts=format_timestamp(end_time),
        chunk_index=index,
        split_reason=split_reason,
    )


# ── 시맨틱 경계 감지 ──────────────────────────────────────

def find_semantic_boundaries(
    segments: list[TranscriptSegment],
    window: int = 5,
    percentile: float = 85.0,
) -> set[int]:
    """
    각 위치 i에서 좌우 윈도우 평균 임베딩의 코사인 거리를 계산.
    거리 상위 percentile%인 위치를 주제 경계로 반환.

    Parameters
    ----------
    window : 좌우 비교 윈도우 크기 (세그먼트 수)
    percentile : 상위 N%의 변화점만 경계로 채택 (기본 85 → 상위 15%)

    Returns
    -------
    경계 인덱스 집합 — 이 인덱스부터 새 청크가 시작됨
    """
    import numpy as np

    encoder = _get_encoder()
    if not encoder:
        return set()

    texts = [s.text for s in segments]
    embeddings = encoder.encode(texts, show_progress_bar=False)  # shape: (N, 384)

    distances: list[tuple[int, float]] = []
    for i in range(window, len(segments) - window):
        left = embeddings[i - window:i].mean(axis=0)
        right = embeddings[i:i + window].mean(axis=0)

        # 코사인 거리 = 1 - 코사인 유사도
        norm_l = float(np.linalg.norm(left))
        norm_r = float(np.linalg.norm(right))
        if norm_l == 0 or norm_r == 0:
            continue
        cos_sim = float(np.dot(left, right)) / (norm_l * norm_r)
        distances.append((i, 1.0 - cos_sim))

    if not distances:
        return set()

    threshold = float(np.percentile([d for _, d in distances], percentile))
    return {i for i, d in distances if d >= threshold}


# ── 메인 청킹 함수 ────────────────────────────────────────

def chunk_transcript(
    segments: list[TranscriptSegment],
    max_words: int = 2000,
    min_words: int = 300,
    percentile: float = 85.0,
    use_semantic: bool = True,
) -> list[TranscriptChunk]:
    """
    시맨틱 경계 감지 후 주제 단위로 청크를 분할.

    Parameters
    ----------
    max_words : 단어 수가 이를 초과하면 경계 무관하게 강제 분할
    min_words : 이 미만이면 경계에서도 분할하지 않음 (너무 작은 청크 방지)
    percentile : 시맨틱 경계 민감도 (높을수록 경계 적음, 낮을수록 많음)
    use_semantic : False이면 기존 단어 수 방식만 사용
    """
    if not segments:
        return []

    # 시맨틱 경계 후보 계산
    boundaries = (
        find_semantic_boundaries(segments, percentile=percentile)
        if use_semantic
        else set()
    )

    chunks: list[TranscriptChunk] = []
    current: list[TranscriptSegment] = []
    current_words = 0

    for i, seg in enumerate(segments):
        word_count = len(seg.text.split())

        is_semantic = i in boundaries and current_words >= min_words
        is_forced = current_words >= max_words and bool(current)

        if (is_semantic or is_forced) and current:
            reason = "semantic" if is_semantic else "max_words"
            chunks.append(_make_chunk(current, len(chunks), split_reason=reason))
            current = []
            current_words = 0

        current.append(seg)
        current_words += word_count

    if current:
        chunks.append(_make_chunk(current, len(chunks), split_reason="end"))

    total = len(chunks)
    for c in chunks:
        c.total_chunks = total

    return chunks
