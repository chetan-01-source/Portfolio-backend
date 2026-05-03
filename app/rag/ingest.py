"""One-shot ingestion CLI: `python -m app.rag.ingest`.

Loads sources → chunks → embeds → upserts into Qdrant.
Idempotent: stores a per-source content hash and skips re-embed if unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import yaml
from qdrant_client.http import models as qmodels

from app.config import get_settings
from app.core.logging import configure_logging, logger
from app.core.qdrant import ensure_collection, get_qdrant
from app.llm.openrouter import OpenRouterClient
from app.rag.chunker import Chunk, chunk_markdown_sections, chunk_project
from app.rag.embeddings import Embedder

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
HASH_FILE = DATA_DIR / ".cache" / "ingest_hashes.json"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _load_hashes() -> dict[str, str]:
    if not HASH_FILE.exists():
        return {}
    try:
        return json.loads(HASH_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_hashes(h: dict[str, str]) -> None:
    HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HASH_FILE.write_text(json.dumps(h, indent=2))


def _load_resume_text() -> str:
    pdf_path = DATA_DIR / "resume.pdf"
    if not pdf_path.exists():
        return ""
    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError:
        logger.warning("pypdfium2 not installed; skipping resume.pdf")
        return ""
    pdf = pdfium.PdfDocument(str(pdf_path))
    return "\n\n".join(p.get_textpage().get_text_range() for p in pdf)


def _load_yaml_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return data if isinstance(data, list) else []


def _load_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _build_chunks() -> list[Chunk]:
    chunks: list[Chunk] = []

    resume_text = _load_resume_text()
    if resume_text:
        chunks.extend(
            chunk_markdown_sections(
                resume_text,
                base_meta={"source": "resume.pdf", "kind": "exp"},
            )
        )

    for proj in _load_yaml_list(DATA_DIR / "projects.yaml"):
        chunks.append(chunk_project(proj))

    for fn, kind in [
        ("linkedin.md", "exp"),
        ("naukri.md", "exp"),
        ("faqs.yaml", "faq"),
    ]:
        path = DATA_DIR / fn
        if not path.exists():
            continue
        if fn.endswith(".yaml"):
            for item in _load_yaml_list(path):
                q, a = item.get("q", ""), item.get("a", "")
                if not (q and a):
                    continue
                chunks.append(
                    Chunk(text=f"Q: {q}\nA: {a}", metadata={"source": fn, "kind": "faq"})
                )
        else:
            chunks.extend(
                chunk_markdown_sections(
                    _load_markdown(path), base_meta={"source": fn, "kind": kind}
                )
            )
    return chunks


async def run() -> None:
    configure_logging()
    settings = get_settings()
    chunks = _build_chunks()
    if not chunks:
        logger.warning("No chunks produced — populate data/ before running ingest")
        return
    logger.info(f"Built {len(chunks)} chunks")

    hashes = _load_hashes()
    new_hashes: dict[str, str] = {}
    fresh_chunks: list[Chunk] = []
    for c in chunks:
        h = _hash(c.text)
        new_hashes[h] = c.metadata.get("source", "")
        if h not in hashes:
            fresh_chunks.append(c)

    if not fresh_chunks:
        logger.info("All chunks already embedded — nothing to do")
        return

    logger.info(f"{len(fresh_chunks)} new chunks to embed")
    client = OpenRouterClient(settings)
    try:
        embedder = Embedder(client, settings)
        vectors = await embedder.embed_many([c.text for c in fresh_chunks])
    finally:
        await client.aclose()

    await ensure_collection()
    qdrant = get_qdrant()
    points = [
        qmodels.PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload={**c.metadata, "text": c.text},
        )
        for c, vec in zip(fresh_chunks, vectors)
    ]
    await qdrant.upsert(collection_name=settings.qdrant_collection, points=points)
    logger.info(f"Upserted {len(points)} points to {settings.qdrant_collection}")

    _save_hashes(new_hashes)
    logger.info("Ingest complete")


if __name__ == "__main__":
    asyncio.run(run())
