from types import SimpleNamespace

import pytest

from app.config import Settings
from app.modules.ingest.service import IngestService
from app.rag.chunker import Chunk


class _FakeQdrant:
    def __init__(self) -> None:
        self.upserts = []
        self.scroll_records = []

    async def get_collections(self):
        collection = SimpleNamespace(name="test_collection")
        return SimpleNamespace(collections=[collection])

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    async def scroll(self, **kwargs):
        return self.scroll_records, None


class _FakeRedis:
    async def scan_iter(self, match=None):
        if False:
            yield match

    async def delete(self, *keys):
        return len(keys)


class _FakeOpenRouterClient:
    def __init__(self, settings) -> None:
        self.settings = settings

    async def aclose(self) -> None:
        return None


class _FakeEmbedder:
    def __init__(self, client, settings) -> None:
        self.client = client
        self.settings = settings

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.mark.parametrize("source", ["resume.pdf", "https://example.com"])
async def test_embed_and_upsert_chunks(monkeypatch, source):
    monkeypatch.setattr("app.modules.ingest.service.OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setattr("app.modules.ingest.service.Embedder", _FakeEmbedder)
    qdrant = _FakeQdrant()
    service = IngestService(
        qdrant=qdrant,
        redis=_FakeRedis(),
        settings=Settings(qdrant_collection="test_collection"),
    )

    result = await service._embed_and_upsert(
        [Chunk(text="hello world", metadata={"source": source, "kind": "test"})],
        skipped=[],
    )

    assert result.ok is True
    assert result.embedded_chunks == 1
    assert result.points_upserted == 1
    assert result.sources[0].source == source
    point = qdrant.upserts[0]["points"][0]
    assert point.payload["text"] == "hello world"
    assert point.payload["content_hash"]


async def test_list_sources_summarizes_qdrant_payloads():
    qdrant = _FakeQdrant()
    qdrant.scroll_records = [
        SimpleNamespace(payload={"source": "resume.pdf", "kind": "resume"}),
        SimpleNamespace(payload={"source": "resume.pdf", "kind": "resume"}),
        SimpleNamespace(payload={"source": "https://example.com", "kind": "url"}),
    ]
    service = IngestService(
        qdrant=qdrant,
        redis=_FakeRedis(),
        settings=Settings(qdrant_collection="test_collection"),
    )

    result = await service.list_sources(max_points=100)

    assert result.scanned_points == 3
    assert [(s.source, s.kind, s.points) for s in result.sources] == [
        ("https://example.com", "url", 1),
        ("resume.pdf", "resume", 2),
    ]
