"""Section-aware splitter.

- Resume: one chunk per heading-delimited section.
- Projects YAML: one chunk per project.
- READMEs / markdown: recursive split, ~400 tokens / 60 overlap.

We use a tokenizer-approximated character budget (token≈4 chars for English).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CHARS_PER_TOKEN = 4
DEFAULT_CHUNK_TOKENS = 400
DEFAULT_OVERLAP_TOKENS = 60

HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _split_by_chars(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    parts: list[str] = []
    n = len(text)
    if n <= max_chars:
        return [text] if text.strip() else []
    step = max(1, max_chars - overlap_chars)
    for start in range(0, n, step):
        chunk = text[start : start + max_chars]
        if chunk.strip():
            parts.append(chunk)
        if start + max_chars >= n:
            break
    return parts


def chunk_markdown_sections(
    text: str,
    *,
    base_meta: dict[str, Any],
    max_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Section-first split: break on headings, then char-split if a section is too long."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN

    # Find heading positions.
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return [
            Chunk(text=p, metadata={**base_meta, "section": None})
            for p in _split_by_chars(text, max_chars, overlap_chars)
        ]

    chunks: list[Chunk] = []
    for i, m in enumerate(matches):
        section_title = m.group(2).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_text = text[start:end].strip()
        if not section_text:
            continue
        meta = {**base_meta, "section": section_title}
        for piece in _split_by_chars(section_text, max_chars, overlap_chars):
            chunks.append(Chunk(text=piece, metadata=meta))
    return chunks


def chunk_project(project: dict[str, Any]) -> Chunk:
    """One chunk per project — summary + stack + highlights."""
    name = project.get("name", "Untitled")
    summary = project.get("summary", "")
    stack = ", ".join(project.get("stack", []) or [])
    highlights = "\n".join(f"- {h}" for h in project.get("highlights", []) or [])
    body = f"# {name}\n\n{summary}\n\nStack: {stack}\n\nHighlights:\n{highlights}"
    return Chunk(
        text=body,
        metadata={
            "source": "projects.yaml",
            "kind": "project",
            "tags": project.get("tags", []),
            "year": project.get("year"),
            "name": name,
        },
    )
