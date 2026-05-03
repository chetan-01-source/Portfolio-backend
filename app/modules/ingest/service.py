from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from tempfile import NamedTemporaryFile

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels
from redis.asyncio import Redis

from app.cache.exact import ExactCache
from app.cache.semantic import SemanticCache
from app.config import Settings
from app.core.logging import logger
from app.llm.openrouter import OpenRouterClient
from app.modules.ingest.crawler import crawl_urls
from app.modules.ingest.schemas import (
    EmbeddedSourcesResponse,
    EmbeddedSourceSummary,
    IngestedSource,
    IngestResponse,
)
from app.rag.chunker import Chunk, chunk_markdown_sections
from app.rag.embeddings import Embedder


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _read_pdf_text(pdf_bytes: bytes) -> str:
    import pypdfium2 as pdfium

    with NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        pdf = pdfium.PdfDocument(tmp.name)
        return "\n\n".join(page.get_textpage().get_text_range() for page in pdf)


class IngestService:
    def __init__(self, *, qdrant: AsyncQdrantClient, redis: Redis, settings: Settings) -> None:
        self.qdrant = qdrant
        self.redis = redis
        self.settings = settings
        self._exact_cache = ExactCache(redis, settings)
        self._semantic_cache = SemanticCache(redis, settings)

    async def _ensure_collection(self) -> None:
        collections = await self.qdrant.get_collections()
        names = {c.name for c in collections.collections}
        if self.settings.qdrant_collection in names:
            return
        await self.qdrant.create_collection(
            collection_name=self.settings.qdrant_collection,
            vectors_config=qmodels.VectorParams(
                size=self.settings.embed_dim,
                distance=qmodels.Distance.COSINE,
            ),
        )

    async def ingest_resume(self, *, filename: str, pdf_bytes: bytes) -> IngestResponse:
        text = _read_pdf_text(pdf_bytes)
        chunks = chunk_markdown_sections(
            text,
            base_meta={"source": filename or "resume.pdf", "kind": "resume", "ingest": "api"},
        )
        return await self._embed_and_upsert(chunks, skipped=[] if text.strip() else ["empty resume"])

    async def ingest_urls(
        self,
        *,
        urls: list[str],
        max_pages: int,
        max_depth: int,
        same_domain_only: bool,
        source_label: str,
    ) -> IngestResponse:
        docs, skipped = await crawl_urls(
            urls,
            max_pages=max_pages,
            max_depth=max_depth,
            same_domain_only=same_domain_only,
            timeout_seconds=self.settings.ingest_fetch_timeout_seconds,
            max_bytes=self.settings.ingest_max_url_bytes,
        )
        chunks: list[Chunk] = []
        for doc in docs:
            title = doc.title or doc.url
            chunks.extend(
                chunk_markdown_sections(
                    f"# {title}\n\n{doc.text}",
                    base_meta={
                        "source": doc.url,
                        "kind": "url",
                        "ingest": source_label,
                        "title": doc.title,
                    },
                )
            )
        return await self._embed_and_upsert(chunks, skipped=skipped)

    async def list_sources(self, *, max_points: int = 1000) -> EmbeddedSourcesResponse:
        await self._ensure_collection()
        offset = None
        scanned = 0
        counts: Counter[tuple[str, str | None]] = Counter()
        while scanned < max_points:
            records, offset = await self.qdrant.scroll(
                collection_name=self.settings.qdrant_collection,
                limit=min(100, max_points - scanned),
                offset=offset,
                with_payload=["source", "kind"],
                with_vectors=False,
            )
            if not records:
                break
            scanned += len(records)
            for record in records:
                payload = record.payload or {}
                source = str(payload.get("source") or "unknown")
                kind = payload.get("kind")
                counts[(source, str(kind) if kind is not None else None)] += 1
            if offset is None:
                break

        sources = [
            EmbeddedSourceSummary(source=source, kind=kind, points=points)
            for (source, kind), points in sorted(counts.items())
        ]
        return EmbeddedSourcesResponse(
            ok=True,
            collection=self.settings.qdrant_collection,
            scanned_points=scanned,
            sources=sources,
        )

    async def _embed_and_upsert(self, chunks: list[Chunk], *, skipped: list[str]) -> IngestResponse:
        if not chunks:
            return IngestResponse(
                ok=True,
                collection=self.settings.qdrant_collection,
                embedded_chunks=0,
                points_upserted=0,
                sources=[],
                skipped=skipped,
            )

        client = OpenRouterClient(self.settings)
        try:
            vectors = await Embedder(client, self.settings).embed_many([c.text for c in chunks])
        finally:
            await client.aclose()

        await self._ensure_collection()
        points = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            content_hash = _hash(chunk.text)
            points.append(
                qmodels.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, content_hash)),
                    vector=vector,
                    payload={**chunk.metadata, "text": chunk.text, "content_hash": content_hash},
                )
            )
        # Batch upserts to avoid WriteTimeout on large payloads (Qdrant Cloud)
        UPSERT_BATCH = 32
        for i in range(0, len(points), UPSERT_BATCH):
            await self.qdrant.upsert(
                collection_name=self.settings.qdrant_collection,
                points=points[i : i + UPSERT_BATCH],
            )

        # Flush chat caches so responses reflect the new context
        try:
            n_exact = await self._exact_cache.flush_all()
            n_sem = await self._semantic_cache.flush_all()
            if n_exact or n_sem:
                logger.info("Flushed chat caches after ingest: exact={}, semantic={}", n_exact, n_sem)
        except Exception as e:
            logger.warning("Cache flush after ingest failed (non-fatal): {}", e)

        counts = Counter(str(c.metadata.get("source", "unknown")) for c in chunks)
        return IngestResponse(
            ok=True,
            collection=self.settings.qdrant_collection,
            embedded_chunks=len(chunks),
            points_upserted=len(points),
            sources=[IngestedSource(source=source, chunks=count) for source, count in counts.items()],
            skipped=skipped,
        )
