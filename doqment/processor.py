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
    "key_points": [],
    "notable_quotes": [],
    "definitions": [],
    "examples_mentioned": [],
    "context_summary": "",
}


def _parse_json(raw: str) -> dict:
    """
    LLM 출력에서 JSON 객체를 안전하게 파싱.
    모델이 ```json ... ``` 블록으로 감싸거나 앞뒤에 텍스트를 붙이는 경우 대응.
    """
    # ```json ... ``` 또는 ``` ... ``` 블록 우선 추출
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        # 중괄호로 둘러싸인 첫 번째 JSON 객체 추출
        obj_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if obj_match:
            raw = obj_match.group(0)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 파싱 실패 시 raw 텍스트를 key_points에 보존 (정보 손실 방지)
        return {
            **_EMPTY_NOTES,
            "key_points": [line.strip() for line in raw.splitlines() if line.strip()],
            "context_summary": raw[:400],
        }


class NoteProcessor:
    """Ollama를 통해 Gemma4 모델로 청크별 노트를 생성."""

    def __init__(
        self,
        model: str = "gemma3:4b",
        host: str = "http://localhost:11434",
        temperature: float = 0.2,
        num_predict: int = 1500,
        num_ctx: int = 8192,
    ) -> None:
        self.model = model
        self.client = Client(host=host)
        self._options = {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
            "repeat_penalty": 1.3,   # 반복 억제 (1.0=없음, 높을수록 강함)
            "repeat_last_n": 128,    # 이전 N토큰 내 반복 감지 범위
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

        # ollama Python 라이브러리 v0.5+: ListResponse Pydantic 모델
        raw_models = response.models if hasattr(response, "models") else response.get("models", [])

        model_names: list[str] = []
        for m in raw_models:
            # Model 객체는 .model 속성 사용 (name이 아님)
            name = getattr(m, "model", None) or getattr(m, "name", None)
            if name:
                model_names.append(name)

        # 정확한 이름 또는 태그 없는 이름으로 매칭
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

    def process_chunk(self, chunk: TranscriptChunk, previous_context: str) -> dict:
        """단일 청크를 처리해 노트 dict 반환."""
        prompt = self._build_prompt(chunk, previous_context)
        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options=self._options,
                stream=False,
            )
            raw = response["response"] if isinstance(response, dict) else response.response
        except ResponseError as e:
            # format="json"이 실패하면 일반 모드로 재시도
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                options=self._options,
                stream=False,
            )
            raw = response["response"] if isinstance(response, dict) else response.response

        notes = _parse_json(raw)

        # 타임스탬프가 누락된 경우 청크 정보로 채움
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

            # 다음 청크를 위한 문맥 업데이트
            previous_context = notes.get("context_summary", "")

        return results
