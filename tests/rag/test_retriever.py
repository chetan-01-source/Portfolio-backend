from types import SimpleNamespace

from app.config import Settings
from app.rag.retriever import Retriever


class _FakeQdrant:
    def __init__(self) -> None:
        self.calls = []

    async def query_points(self, **kwargs):
        self.calls.append(kwargs)
        point = SimpleNamespace(
            id="point-1",
            score=0.9,
            vector=[0.1, 0.2, 0.3],
            payload={"text": "Chetan project details", "source": "test"},
        )
        return SimpleNamespace(points=[point])


class _FakeReranker:
    async def rerank(self, query: str, docs: list[dict]) -> list[dict]:
        return docs


async def test_retriever_uses_qdrant_query_points():
    qdrant = _FakeQdrant()
    settings = Settings(qdrant_collection="test_collection")
    retriever = Retriever(qdrant, _FakeReranker(), settings)

    docs = await retriever.search(query_vec=[0.1, 0.2, 0.3], query_text="projects")

    assert qdrant.calls == [
        {
            "collection_name": "test_collection",
            "query": [0.1, 0.2, 0.3],
            "limit": 20,
            "with_payload": True,
            "with_vectors": True,
        }
    ]
    assert docs[0]["id"] == "point-1"
    assert docs[0]["text"] == "Chetan project details"
