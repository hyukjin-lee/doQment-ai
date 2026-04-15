"""슬라이딩 윈도우 방식의 transcript 청킹."""

from __future__ import annotations

from dataclasses import dataclass, field

from doqment.transcript import TranscriptSegment, format_timestamp


@dataclass
class TranscriptChunk:
    segments: list[TranscriptSegment]
    text: str           # 전체 청크 텍스트 (공백으로 이어붙임)
    start_time: float   # 청크 시작 시간 (초)
    end_time: float     # 청크 종료 시간 (초)
    start_ts: str       # "1:23" 형식
    end_ts: str         # "4:56" 형식
    chunk_index: int    # 0-based
    total_chunks: int = field(default=0)  # 전체 청크 수 (나중에 채워짐)


def _make_chunk(segments: list[TranscriptSegment], index: int) -> TranscriptChunk:
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
    )


def _get_overlap_segments(
    segments: list[TranscriptSegment],
    target_words: int,
) -> list[TranscriptSegment]:
    """
    청크 끝에서 target_words 개의 단어를 포함하는 세그먼트 슬라이스 반환.
    문장 경계를 존중하기 위해 세그먼트 단위로 자름.
    """
    collected_words = 0
    overlap: list[TranscriptSegment] = []
    for seg in reversed(segments):
        overlap.insert(0, seg)
        collected_words += len(seg.text.split())
        if collected_words >= target_words:
            break
    return overlap


def chunk_transcript(
    segments: list[TranscriptSegment],
    max_words: int = 1500,
    overlap_ratio: float = 0.20,
) -> list[TranscriptChunk]:
    """
    슬라이딩 윈도우로 transcript 세그먼트를 청크로 분할.

    - max_words: 청크당 최대 단어 수 (기본 1500)
    - overlap_ratio: 이전 청크와 겹치는 비율 (기본 20%)

    gemma4:4b 기준 1500 단어 ≈ 2000 토큰이므로
    num_ctx=8192 내에서 프롬프트 + 출력 공간 충분히 확보.
    """
    if not segments:
        return []

    overlap_words = max(1, int(max_words * overlap_ratio))
    chunks: list[TranscriptChunk] = []
    current: list[TranscriptSegment] = []
    current_words = 0

    for seg in segments:
        word_count = len(seg.text.split())

        if current_words + word_count > max_words and current:
            # 현재 청크 저장
            chunks.append(_make_chunk(current, len(chunks)))

            # 오버랩 세그먼트로 다음 청크 시작
            overlap = _get_overlap_segments(current, overlap_words)
            current = overlap
            current_words = sum(len(s.text.split()) for s in current)

        current.append(seg)
        current_words += word_count

    # 마지막 청크
    if current:
        chunks.append(_make_chunk(current, len(chunks)))

    # total_chunks 값 채우기
    total = len(chunks)
    for chunk in chunks:
        chunk.total_chunks = total

    return chunks
