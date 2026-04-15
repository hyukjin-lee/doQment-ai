"""doQment 웹 UI — FastAPI 서버."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from doqment.aggregator import aggregate
from doqment.chunker import chunk_transcript
from doqment.processor import NoteProcessor
from doqment.renderer import render_markdown, save_markdown
from doqment.transcript import (
    extract_video_id,
    fetch_transcript,
    get_available_languages,
)

_STATIC_DIR = Path(__file__).parent.parent / "static"
_OUTPUT_DIR = Path(__file__).parent.parent / "output"

app = FastAPI(title="doQment", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.mount("/output", StaticFiles(directory=str(_OUTPUT_DIR)), name="output")


# ── 요청/응답 모델 ─────────────────────────────────────────

class GenerateRequest(BaseModel):
    url: str
    lang: str = "en"
    title: str = ""
    model: str = "gemma3:4b"
    chunk_size: int = 1500
    ollama_host: str = "http://localhost:11434"


class LanguagesRequest(BaseModel):
    url: str


# ── SSE 이벤트 헬퍼 ───────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── 엔드포인트 ────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(_STATIC_DIR / "index.html")


@app.post("/api/languages")
async def languages(req: LanguagesRequest):
    """영상에서 사용 가능한 transcript 언어 목록 반환."""
    try:
        video_id = extract_video_id(req.url)
        langs = get_available_languages(video_id)
        return {"languages": langs}
    except Exception as e:
        return {"error": str(e), "languages": []}


@app.get("/api/models")
async def models(ollama_host: str = "http://localhost:11434"):
    """설치된 Ollama 모델 목록 반환."""
    try:
        from ollama import Client
        client = Client(host=ollama_host)
        response = client.list()
        raw_models = response.models if hasattr(response, "models") else []
        names = [
            getattr(m, "model", None) or getattr(m, "name", "")
            for m in raw_models
        ]
        return {"models": [n for n in names if n]}
    except Exception as e:
        return {"error": str(e), "models": []}


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    """
    노트 생성 — Server-Sent Events(SSE)로 진행상황 실시간 스트리밍.

    이벤트 종류:
    - progress : {"step": 1-5, "message": "...", "detail": "..."}
    - chunk    : {"index": N, "total": N, "title": "..."}
    - done     : {"markdown": "...", "filename": "...", "stats": {...}}
    - error    : {"message": "..."}
    """
    async def stream() -> AsyncGenerator[str, None]:
        try:
            # Step 1: Video ID + Transcript
            yield _sse("progress", {"step": 1, "message": "YouTube transcript 수집 중..."})
            await asyncio.sleep(0)

            try:
                video_id = extract_video_id(req.url)
            except ValueError as e:
                yield _sse("error", {"message": str(e)})
                return

            try:
                languages = [req.lang] if req.lang else ["en"]
                segments = await asyncio.to_thread(fetch_transcript, video_id, languages)
            except RuntimeError as e:
                yield _sse("error", {"message": str(e)})
                return

            total_words = sum(len(s.text.split()) for s in segments)
            yield _sse("progress", {
                "step": 1,
                "message": "transcript 수집 완료",
                "detail": f"{len(segments):,}개 세그먼트 · ~{total_words:,} 단어",
                "done": True,
            })
            await asyncio.sleep(0)

            # Step 2: 청킹
            yield _sse("progress", {"step": 2, "message": "청크 분할 중..."})
            await asyncio.sleep(0)

            chunks = chunk_transcript(segments, max_words=req.chunk_size)
            yield _sse("progress", {
                "step": 2,
                "message": "청크 분할 완료",
                "detail": f"{len(chunks)}개 청크",
                "done": True,
            })
            await asyncio.sleep(0)

            # Step 3: LLM 노트 추출
            yield _sse("progress", {"step": 3, "message": f"LLM 노트 추출 중 ({req.model})..."})
            await asyncio.sleep(0)

            try:
                processor = NoteProcessor(model=req.model, host=req.ollama_host)
            except RuntimeError as e:
                yield _sse("error", {"message": str(e)})
                return

            chunk_notes: list[dict] = []
            for i, chunk in enumerate(chunks):
                yield _sse("chunk", {
                    "index": i + 1,
                    "total": len(chunks),
                    "start_ts": chunk.start_ts,
                    "end_ts": chunk.end_ts,
                })
                await asyncio.sleep(0)

                notes = await asyncio.to_thread(
                    processor.process_chunk, chunk,
                    chunk_notes[-1].get("context_summary", "") if chunk_notes else ""
                )
                chunk_notes.append(notes)

            yield _sse("progress", {
                "step": 3,
                "message": "노트 추출 완료",
                "detail": f"{len(chunks)}개 청크 처리됨",
                "done": True,
            })
            await asyncio.sleep(0)

            # Step 4: 병합
            yield _sse("progress", {"step": 4, "message": "노트 병합 중..."})
            await asyncio.sleep(0)

            aggregated = aggregate(video_id, req.url, chunks, chunk_notes, model=req.model)
            yield _sse("progress", {"step": 4, "message": "노트 병합 완료", "done": True})
            await asyncio.sleep(0)

            # Step 5: Markdown 저장
            yield _sse("progress", {"step": 5, "message": "Markdown 파일 저장 중..."})
            await asyncio.sleep(0)

            note_title = req.title or f"Notes — {req.url}"
            md_content = render_markdown(aggregated, note_title)
            _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            filepath = save_markdown(md_content, note_title, _OUTPUT_DIR)

            yield _sse("progress", {"step": 5, "message": "저장 완료", "done": True})
            await asyncio.sleep(0)

            # 완료
            total_points = sum(len(s.get("key_points", [])) for s in aggregated.sections)
            yield _sse("done", {
                "markdown": md_content,
                "filename": filepath.name,
                "download_url": f"/output/{filepath.name}",
                "stats": {
                    "sections": len(aggregated.sections),
                    "key_points": total_points,
                    "quotes": 0,
                    "definitions": 0,
                },
            })

        except Exception as e:
            yield _sse("error", {"message": f"예기치 못한 오류: {e}"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def serve():
    """웹 서버 시작 (doqment-web 엔트리포인트)."""
    uvicorn.run(
        "doqment.web:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="warning",
    )


if __name__ == "__main__":
    serve()
