"""Gemma4 LLM을 통한 청크별 노트 생성."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from ollama import Client, ResponseError

from doqment.chunker import TranscriptChunk


_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "extract_notes.txt"


def _load_prompt() -> str:
    """매 호출마다 파일에서 읽어 핫리로드 지원."""
    return _PROMPT_PATH.read_text(encoding="utf-8")

_EMPTY_NOTES: dict = {
    "section_title": "Untitled Section",
    "timestamp_range": "",
    "summary": "",
    "key_points": [],
    "context_summary": "",
}


def _rescue_partial_json(raw: str) -> dict | None:
    """
    불완전하게 잘린 JSON에서 핵심 필드를 정규식으로 직접 추출.
    section_title을 찾지 못하면 None 반환 (복구 불가).
    """
    title_m = re.search(r'"section_title"\s*:\s*"([^"]+)"', raw)
    if not title_m:
        return None

    ts_m  = re.search(r'"timestamp_range"\s*:\s*"([^"]+)"', raw)
    sum_m = re.search(r'"summary"\s*:\s*"([^"]{10,})"', raw)
    ctx_m = re.search(r'"context_summary"\s*:\s*"([^"]{10,})"', raw)

    # key_points: 완전한 문자열 항목만 (내용이 충분한 것)
    points = re.findall(r'"(\s*-[^"]+)"', raw)
    points = [p for p in points if len(p.strip()) > 3]

    return {
        **_EMPTY_NOTES,
        "section_title": title_m.group(1),
        "timestamp_range": ts_m.group(1) if ts_m else "",
        "summary": sum_m.group(1) if sum_m else "",
        "key_points": points,
        "context_summary": ctx_m.group(1) if ctx_m else "",
    }


def _parse_json(raw: str) -> dict:
    """
    LLM 출력에서 JSON 객체를 안전하게 파싱.
    1차: 표준 JSON 파싱
    2차: 잘린 JSON 필드 직접 추출 (_rescue_partial_json)
    3차: 전체 raw 텍스트를 key_points에 보존 (정보 손실 최소화)
    """
    # ```json ... ``` 또는 ``` ... ``` 블록 우선 추출
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        obj_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if obj_match:
            raw = obj_match.group(0)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 잘린 JSON 복구 시도
    rescued = _rescue_partial_json(raw)
    if rescued:
        return rescued

    # 최후 폴백: raw 텍스트 보존
    return {
        **_EMPTY_NOTES,
        "key_points": [line.strip() for line in raw.splitlines() if line.strip()],
        "context_summary": raw[:400],
    }


def _is_failed(notes: dict) -> bool:
    """노트 생성 실패로 간주할 조건."""
    return (
        notes.get("section_title") == "Untitled Section"
        or not notes.get("key_points")
    )


class NoteProcessor:
    """Ollama를 통해 Gemma4 모델로 청크별 노트를 생성."""

    def __init__(
        self,
        model: str = "gemma3:4b",
        host: str = "http://localhost:11434",
        temperature: float = 0.2,
        num_ctx: int = 8192,
    ) -> None:
        self.model = model
        self.client = Client(host=host)
        self._options = {
            "temperature": temperature,
            "num_predict": -1,   # 제한 없음 — EOS 토큰까지 자연 생성 (JSON 잘림 방지)
            "num_ctx": num_ctx,
            "repeat_penalty": 1.3,
            "repeat_last_n": 128,
        }
        self._verify_model()

    def _verify_model(self) -> None:
        """Ollama에 모델이 설치되어 있는지 확인."""
        try:
            response = self.client.list()
        except Exception:
            raise RuntimeError(
                "Ollama 서버에 연결할 수 없습니다.\n"
                "다음 명령으로 서버를 시작하세요: ollama serve"
            )

        raw_models = response.models if hasattr(response, "models") else response.get("models", [])

        model_names: list[str] = []
        for m in raw_models:
            name = getattr(m, "model", None) or getattr(m, "name", None)
            if name:
                model_names.append(name)

        base_name = self.model.split(":")[0]
        found = any(
            n == self.model or n.startswith(base_name + ":") or n == base_name
            for n in model_names
        )
        if not found:
            raise RuntimeError(
                f"모델 '{self.model}'이 Ollama에 설치되어 있지 않습니다.\n"
                f"설치 명령: ollama pull {self.model}\n"
                f"설치된 모델: {', '.join(model_names) or '없음'}"
            )

    def _build_prompt(self, chunk: TranscriptChunk, previous_context: str) -> str:
        return _load_prompt().format(
            start_ts=chunk.start_ts,
            end_ts=chunk.end_ts,
            chunk_index=chunk.chunk_index + 1,
            total_chunks=chunk.total_chunks,
            transcript_text=chunk.text,
            previous_context=previous_context or "This is the beginning of the video.",
        )

    def _call_model(self, prompt: str, options: dict) -> str:
        """Ollama 모델 호출. format=json 실패 시 일반 모드로 재시도."""
        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options=options,
                stream=False,
            )
        except ResponseError:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                options=options,
                stream=False,
            )
        return response["response"] if isinstance(response, dict) else response.response

    def process_chunk(self, chunk: TranscriptChunk, previous_context: str) -> dict:
        """
        단일 청크를 처리해 노트 dict 반환.

        파싱 실패(Untitled Section / 빈 key_points) 감지 시
        repeat_penalty를 높여 최대 2회 재시도.
        모든 시도가 실패해도 가장 나은 결과를 반환.
        """
        prompt = self._build_prompt(chunk, previous_context)
        best: dict | None = None

        for attempt in range(3):  # 0(기본), 1(1차 재시도), 2(2차 재시도)
            options = dict(self._options)
            if attempt > 0:
                # 반복 루프 억제 강화 (출력 길이는 제한하지 않음)
                options["repeat_penalty"] = round(1.3 + attempt * 0.2, 1)  # 1.5 → 1.7

            try:
                raw = self._call_model(prompt, options)
            except Exception:
                continue

            notes = _parse_json(raw)

            if not _is_failed(notes):
                # 성공 — 타임스탬프 보정 후 즉시 반환
                if not notes.get("timestamp_range"):
                    notes["timestamp_range"] = f"{chunk.start_ts} - {chunk.end_ts}"
                return notes

            # 실패지만 이전 시도보다 나으면 저장
            if best is None or len(notes.get("key_points", [])) > len(best.get("key_points", [])):
                best = notes

        # 모든 시도 실패 — 최선의 결과 반환
        notes = best or dict(_EMPTY_NOTES)
        if not notes.get("timestamp_range"):
            notes["timestamp_range"] = f"{chunk.start_ts} - {chunk.end_ts}"
        return notes

    def process_all_chunks(
        self,
        chunks: list[TranscriptChunk],
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[dict]:
        """
        모든 청크를 순차 처리.
        이전 청크의 context_summary를 다음 청크에 전달해 연속성 유지.
        """
        results: list[dict] = []
        previous_context = ""

        for i, chunk in enumerate(chunks):
            if progress_callback:
                progress_callback(i, len(chunks))

            notes = self.process_chunk(chunk, previous_context)
            results.append(notes)

            previous_context = notes.get("context_summary", "")

        return results
