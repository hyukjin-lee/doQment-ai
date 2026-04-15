"""YouTube transcript 수집 및 전처리."""

from __future__ import annotations

import re
from dataclasses import dataclass

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)


# 제거할 노이즈 패턴 (음악, 박수, 배경음 등)
_NOISE_PATTERNS = [
    re.compile(r"\[.*?\]", re.IGNORECASE),   # [Music], [Applause]
    re.compile(r"\(.*?\)", re.IGNORECASE),   # (inaudible), (crosstalk)
    re.compile(r"♪.*?♪", re.DOTALL),        # ♪ 음악 ♪
    re.compile(r"♫.*?♫", re.DOTALL),
]


@dataclass
class TranscriptSegment:
    text: str       # 정제된 텍스트
    start: float    # 시작 시간 (초)
    duration: float # 지속 시간 (초)
    timestamp: str  # "1:23" 또는 "1:02:03" 형식


def extract_video_id(url: str) -> str:
    """YouTube URL에서 video ID 추출."""
    patterns = [
        r"(?:youtube\.com\/watch\?v=)([^&\n?#]+)",
        r"(?:youtu\.be\/)([^&\n?#]+)",
        r"(?:youtube\.com\/embed\/)([^&\n?#]+)",
        r"(?:youtube\.com\/shorts\/)([^&\n?#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"YouTube URL에서 video ID를 추출할 수 없습니다: {url}")


def format_timestamp(seconds: float) -> str:
    """초(float) → 'M:SS' 또는 'H:MM:SS' 형식 문자열."""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _clean_text(text: str) -> str:
    """노이즈 패턴 제거 후 공백 정리."""
    for pattern in _NOISE_PATTERNS:
        text = pattern.sub("", text)
    # 연속 공백 정리
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_transcript(video_id: str, languages: list[str] | None = None) -> list[TranscriptSegment]:
    """
    YouTube transcript 수집 (youtube-transcript-api v1.0+ API).

    우선순위: 수동 제작 > 자동 생성.
    languages: 선호 언어 목록 (예: ["en"], ["ko", "en"])
    """
    if languages is None:
        languages = ["en"]

    api = YouTubeTranscriptApi()

    try:
        transcript_list = api.list(video_id)
    except TranscriptsDisabled:
        raise RuntimeError(
            "이 영상은 transcript가 비활성화되어 있습니다.\n"
            "자막이 있는 다른 영상을 시도해보세요."
        )

    try:
        transcript = transcript_list.find_manually_created_transcript(languages)
    except NoTranscriptFound:
        try:
            transcript = transcript_list.find_generated_transcript(languages)
        except NoTranscriptFound:
            available = [
                f"{t.language} ({t.language_code})"
                for t in transcript_list
            ]
            raise RuntimeError(
                f"요청한 언어 {languages}의 transcript를 찾을 수 없습니다.\n"
                f"사용 가능한 언어: {', '.join(available)}\n"
                f"--lang 옵션으로 언어를 지정하세요."
            )

    raw_segments = transcript.fetch()

    segments = []
    for seg in raw_segments:
        # v1.0+에서 세그먼트는 FetchedTranscriptSnippet 객체 또는 dict
        text_val = seg.get("text") if isinstance(seg, dict) else seg.text
        start_val = seg.get("start") if isinstance(seg, dict) else seg.start
        duration_val = seg.get("duration") if isinstance(seg, dict) else seg.duration

        text = _clean_text(str(text_val))
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                text=text,
                start=float(start_val),
                duration=float(duration_val),
                timestamp=format_timestamp(float(start_val)),
            )
        )

    return segments


def get_available_languages(video_id: str) -> list[str]:
    """영상에서 사용 가능한 transcript 언어 목록 반환."""
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        return [
            f"{t.language} ({t.language_code})"
            for t in transcript_list
        ]
    except TranscriptsDisabled:
        return []
