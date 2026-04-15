"""AggregatedNotes → Markdown 파일 렌더링."""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from slugify import slugify

from doqment.aggregator import AggregatedNotes


def _anchor(text: str) -> str:
    return re.sub(r"[^\w\s-]", "", text.lower()).strip().replace(" ", "-")


def render_markdown(notes: AggregatedNotes, video_title: str) -> str:
    model = notes.metadata.get("model", "")
    today = datetime.date.today().isoformat()

    lines: list[str] = []

    # ── 헤더 ─────────────────────────────────────────
    lines += [
        f"# {video_title}",
        "",
        f"> **출처:** {notes.video_url}  ",
        f"> **총 길이:** {notes.total_duration}  ",
        f"> **정리 날짜:** {today}  ",
        f"> **모델:** {model}  ",
        "",
        "---",
        "",
    ]

    # ── 목차 ─────────────────────────────────────────
    lines.append("## 목차")
    lines.append("")
    for i, section in enumerate(notes.sections, 1):
        ts = section.get("timestamp_range", "")
        title = section.get("section_title", f"Section {i}")
        anchor = _anchor(f"section-{i}-{title}")
        lines.append(f"- [{i}. {title}](#{anchor}) `{ts}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── 세부 노트 ─────────────────────────────────────
    for i, section in enumerate(notes.sections, 1):
        ts = section.get("timestamp_range", "")
        title = section.get("section_title", f"Section {i}")
        anchor = _anchor(f"section-{i}-{title}")

        lines.append(f'<a name="{anchor}"></a>')
        lines.append(f"### {i}. {title} `[{ts}]`")
        lines.append("")

        # 섹션 요약 단락 (있을 경우)
        summary = section.get("summary", "").strip()
        if summary:
            lines.append(summary)
            lines.append("")

        for point in section.get("key_points", []):
            lines.append(point)

        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def save_markdown(
    content: str,
    video_title: str,
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.date.today().isoformat()
    slug = slugify(video_title, max_length=60, separator="-")
    filename = f"{today}_{slug}.md"
    filepath = output_dir / filename

    counter = 1
    while filepath.exists():
        counter += 1
        filepath = output_dir / f"{today}_{slug}_{counter}.md"

    filepath.write_text(content, encoding="utf-8")
    return filepath
