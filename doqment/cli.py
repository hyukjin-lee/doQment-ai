"""doQment CLI — YouTube transcript → 상세 Markdown 노트."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from doqment.aggregator import aggregate
from doqment.chunker import chunk_transcript
from doqment.processor import NoteProcessor
from doqment.renderer import render_markdown, save_markdown
from doqment.transcript import extract_video_id, fetch_transcript, get_available_languages

app = typer.Typer(
    name="doqment",
    help="YouTube transcript를 Gemma4 로컬 LLM으로 상세 노트로 정리합니다.",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True, style="bold red")


def _print_step(step: int, total: int, message: str, done: bool = False) -> None:
    status = "[green]✓[/green]" if done else "[yellow]...[/yellow]"
    console.print(f"  [{step}/{total}] {message} {status}")


@app.command()
def main(
    url: str = typer.Argument(..., help="YouTube 영상 URL"),
    lang: str = typer.Option("en", "--lang", "-l", help="선호 transcript 언어 코드 (예: en, ko)"),
    output: Path = typer.Option(
        Path("./output"),
        "--output", "-o",
        help="Markdown 파일 저장 경로",
    ),
    model: str = typer.Option("gemma3:4b", "--model", "-m", help="Ollama 모델명 (예: gemma3:4b, gemma4:4b)"),
    chunk_size: int = typer.Option(1500, "--chunk-size", help="청크당 최대 단어 수"),
    ollama_host: str = typer.Option(
        "http://localhost:11434",
        "--ollama-host",
        help="Ollama 서버 주소",
    ),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="노트 제목 (기본: URL 기반 자동 생성)"),
    list_langs: bool = typer.Option(False, "--list-langs", help="사용 가능한 transcript 언어 목록만 출력"),
) -> None:
    """YouTube 영상 URL을 입력하면 상세 Markdown 노트를 생성합니다."""

    TOTAL_STEPS = 5
    console.print()
    console.print(f"[bold cyan]doQment[/bold cyan] — [dim]{url}[/dim]")
    console.print()

    # ── Step 0: Video ID 추출 ─────────────────────────
    try:
        video_id = extract_video_id(url)
    except ValueError as e:
        err_console.print(f"\n오류: {e}")
        raise typer.Exit(1)

    # 언어 목록 출력 모드
    if list_langs:
        langs = get_available_languages(video_id)
        if langs:
            table = Table(title="사용 가능한 transcript 언어", show_header=False)
            table.add_column("언어", style="cyan")
            for lang_item in langs:
                table.add_row(lang_item)
            console.print(table)
        else:
            console.print("[yellow]이 영상에는 transcript가 없습니다.[/yellow]")
        raise typer.Exit(0)

    # ── Step 1: Transcript 수집 ───────────────────────
    console.print(f"  [1/{TOTAL_STEPS}] YouTube transcript 수집 중...", end=" ")
    try:
        languages = [lang] if lang else ["en"]
        segments = fetch_transcript(video_id, languages)
    except RuntimeError as e:
        console.print()
        err_console.print(f"\n{e}")
        raise typer.Exit(1)

    total_words = sum(len(s.text.split()) for s in segments)
    console.print(f"[green]✓[/green] ({len(segments):,}개 세그먼트, ~{total_words:,} 단어)")

    # ── Step 2: 청킹 ──────────────────────────────────
    console.print(f"  [2/{TOTAL_STEPS}] 청크 분할 중...", end=" ")
    chunks = chunk_transcript(segments, max_words=chunk_size)
    console.print(f"[green]✓[/green] ({len(chunks)}개 청크, 청크당 ~{chunk_size} 단어)")

    # ── Step 3: LLM 노트 추출 ─────────────────────────
    console.print(f"  [3/{TOTAL_STEPS}] Gemma4 노트 추출 중...")
    console.print()

    try:
        processor = NoteProcessor(model=model, host=ollama_host)
    except RuntimeError as e:
        err_console.print(f"\n{e}")
        raise typer.Exit(1)

    chunk_notes: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("    [progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TaskProgressColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("청크 처리 중", total=len(chunks))

        def on_progress(current: int, total: int) -> None:
            progress.update(task, completed=current)

        chunk_notes = processor.process_all_chunks(chunks, progress_callback=on_progress)
        progress.update(task, completed=len(chunks))

    console.print()

    # ── Step 4: 노트 병합 ─────────────────────────────
    console.print(f"  [4/{TOTAL_STEPS}] 노트 병합 중...", end=" ")
    aggregated = aggregate(video_id, url, chunks, chunk_notes, model=model)
    console.print("[green]✓[/green]")

    # ── Step 5: Markdown 저장 ─────────────────────────
    console.print(f"  [5/{TOTAL_STEPS}] Markdown 파일 저장 중...", end=" ")
    note_title = title or f"Notes — {url}"
    md_content = render_markdown(aggregated, note_title)
    filepath = save_markdown(md_content, note_title, output)
    console.print("[green]✓[/green]")

    # ── 완료 출력 ─────────────────────────────────────
    console.print()
    console.print(f"[bold green]→[/bold green] {filepath}")
    console.print()

    # 간단한 통계
    total_points = sum(len(s.get("key_points", [])) for s in aggregated.sections)
    console.print(
        f"  [dim]섹션 {len(aggregated.sections)}개 · "
        f"핵심 포인트 {total_points}개 · "
        f"인용 {len(aggregated.all_quotes)}개 · "
        f"용어 {len(aggregated.all_definitions)}개[/dim]"
    )
    console.print()
