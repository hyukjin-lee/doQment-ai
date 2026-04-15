"""청크별 노트를 하나의 구조로 병합."""

from __future__ import annotations

from dataclasses import dataclass, field

from doqment.chunker import TranscriptChunk


@dataclass
class AggregatedNotes:
    video_id: str
    video_url: str
    total_duration: str
    sections: list[dict]
    metadata: dict = field(default_factory=dict)


def aggregate(
    video_id: str,
    url: str,
    chunks: list[TranscriptChunk],
    chunk_notes: list[dict],
    model: str = "",
) -> AggregatedNotes:
    total_duration = chunks[-1].end_ts if chunks else "Unknown"

    return AggregatedNotes(
        video_id=video_id,
        video_url=url,
        total_duration=total_duration,
        sections=chunk_notes,
        metadata={"model": model, "chunk_count": len(chunks)},
    )
