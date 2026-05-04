"""Tests for ChatService memory handling and query separation.

Covers:
  - Follow-up questions use contextual retrieval but skip semantic cache
  - Standalone queries use semantic cache
  - Cache stores only raw user messages, never conversation history
"""

import pytest

from app.config import Settings
from app.modules.chat.service import ChatService


# ── Fakes ──────────────────────────────────────────────────────────

class _Repo:
    def __init__(self, history: list[dict] | None = None) -> None:
        self.inserted: list[dict] = []
        self._history = history or []

    async def recent_logs(self, *, session_id: str, limit: int):
        return self._history

    async def insert_log(self, **kwargs):
        self.inserted.append(kwargs)


class _Exact:
    def __init__(self) -> None:
        self.lookup_query: str | None = None
        self.stored_query: str | None = None

    async def get(self, query: str, model: str):
        self.lookup_query = query
        return None

    async def set(self, query: str, model: str, *, answer: str, doc_ids: list[str]):
        self.stored_query = query


class _Semantic:
    """Fake semantic cache matching the updated lookup() signature."""

    def __init__(self) -> None:
        self.lookup_called = False
        self.lookup_query_text: str | None = None
        self.stored: dict | None = None

    async def lookup(self, embedding: list[float], *, query_text: str = ""):
        self.lookup_called = True
        self.lookup_query_text = query_text
        return None

    async def store(self, **kwargs):
        self.stored = kwargs


class _Embedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed_one(self, text: str):
        self.texts.append(text)
        return [0.1, 0.2, 0.3]


class _Retriever:
    def __init__(self) -> None:
        self.query_text: str | None = None

    async def search(self, *, query_vec: list[float], query_text: str):
        self.query_text = query_text
        return [
            {
                "id": "schbang-doc",
                "payload": {"kind": "exp", "section": "Current — Schbang"},
                "text": "Schbang workstreams include CSAT automation and AI voice calling.",
            }
        ]


class _LLM:
    def __init__(self) -> None:
        self.messages: list[dict] | None = None

    async def stream(self, *, model: str, messages: list[dict[str, str]], max_tokens: int):
        self.messages = messages
        yield "Schbang workstreams include CSAT automation."


# ── Helpers ────────────────────────────────────────────────────────

def _history_with_schbang_context():
    return [
        {"role": "user", "content": "Tell me about his current work"},
        {
            "role": "assistant",
            "content": "Chetan is currently at Schbang as an AI Full Stack Engineer.",
        },
    ]


def _build_service(*, history: list[dict] | None = None):
    repo = _Repo(history=history)
    exact = _Exact()
    semantic = _Semantic()
    embedder = _Embedder()
    retriever = _Retriever()
    llm = _LLM()
    service = ChatService(
        repo=repo,
        retriever=retriever,
        embedder=embedder,
        llm=llm,
        exact_cache=exact,
        semantic_cache=semantic,
        settings=Settings(llm_cheap_model="test-model"),
    )
    return service, repo, exact, semantic, embedder, retriever, llm


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_followup_uses_contextual_retrieval():
    """Follow-up questions should use enriched retrieval_query for search."""
    service, repo, exact, semantic, embedder, retriever, llm = _build_service(
        history=_history_with_schbang_context()
    )

    chunks = [
        chunk
        async for chunk in service.stream_answer(
            session_id="session-1",
            message="What projects is he working on there?",
        )
    ]

    assert chunks[0].payload["memory"] is True
    # Retriever should get the contextual query with history
    assert "Schbang" in retriever.query_text or "current work" in retriever.query_text
    assert "What projects is he working on there?" in retriever.query_text


@pytest.mark.asyncio
async def test_followup_skips_semantic_cache():
    """Follow-up questions with conversation memory must NOT use semantic cache."""
    service, repo, exact, semantic, embedder, retriever, llm = _build_service(
        history=_history_with_schbang_context()
    )

    chunks = [
        chunk
        async for chunk in service.stream_answer(
            session_id="session-1",
            message="What about the CSAT project?",
        )
    ]

    # Semantic cache should NOT be consulted for follow-ups
    assert semantic.lookup_called is False
    # But the answer should still be generated
    assert any(c.type == "token" for c in chunks)


@pytest.mark.asyncio
async def test_followup_does_not_cache_answer():
    """Follow-up answers should NOT be stored in any cache."""
    service, repo, exact, semantic, embedder, retriever, llm = _build_service(
        history=_history_with_schbang_context()
    )

    chunks = [
        chunk
        async for chunk in service.stream_answer(
            session_id="session-1",
            message="Tell me more about that",
        )
    ]

    # Neither cache should have stored anything
    assert exact.stored_query is None
    assert semantic.stored is None


@pytest.mark.asyncio
async def test_standalone_query_uses_semantic_cache():
    """Standalone queries (no memory) should check and write to semantic cache."""
    service, repo, exact, semantic, embedder, retriever, llm = _build_service(
        history=[]  # no conversation history
    )

    chunks = [
        chunk
        async for chunk in service.stream_answer(
            session_id="session-1",
            message="Tell me about the CSAT project",
        )
    ]

    # Semantic cache should be consulted
    assert semantic.lookup_called is True
    # The lookup should use the RAW message, not any context blob
    assert semantic.lookup_query_text == "Tell me about the CSAT project"
    # Cache should store the raw message
    assert semantic.stored is not None
    assert semantic.stored["query"] == "Tell me about the CSAT project"


@pytest.mark.asyncio
async def test_exact_cache_uses_raw_message():
    """Exact cache should always use the raw user message as key."""
    service, repo, exact, semantic, embedder, retriever, llm = _build_service(
        history=_history_with_schbang_context()
    )

    chunks = [
        chunk
        async for chunk in service.stream_answer(
            session_id="session-1",
            message="CSAT project details",
        )
    ]

    # Exact cache lookup should use raw message, NOT contextual query
    assert exact.lookup_query == "CSAT project details"
    assert "Recent chat summary" not in (exact.lookup_query or "")


@pytest.mark.asyncio
async def test_cache_never_stores_conversation_history():
    """The cache query must NEVER contain assistant responses or conversation blobs."""
    service, repo, exact, semantic, embedder, retriever, llm = _build_service(
        history=[]
    )

    chunks = [
        chunk
        async for chunk in service.stream_answer(
            session_id="session-1",
            message="cometchat experience",
        )
    ]

    # Verify what was stored in semantic cache
    stored_query = semantic.stored["query"]
    assert "Recent chat summary" not in stored_query
    assert "Assistant:" not in stored_query
    assert stored_query == "cometchat experience"


@pytest.mark.asyncio
async def test_regression_schbang_cached_csat_miss():
    """REGRESSION: A cached Schbang overview must NOT serve a CSAT project question.

    This reproduces the exact bug from the screenshots:
    1. User asks "cometchat experience" → cached
    2. User asks "CSAT project details" → must NOT return cometchat cache
    """
    # Simulate: first query was standalone and got cached
    service, repo, exact, semantic, embedder, retriever, llm = _build_service(
        history=[]
    )

    # First query — gets cached
    chunks1 = [
        chunk
        async for chunk in service.stream_answer(
            session_id="session-1",
            message="cometchat experience",
        )
    ]
    assert semantic.stored is not None
    assert semantic.stored["query"] == "cometchat experience"

    # Now simulate second query in a new session
    # The semantic cache should NOT match because entities conflict
    from app.cache.semantic import should_accept_cache_hit
    accepted, reason = should_accept_cache_hit(
        "CSAT project details",
        "cometchat experience",
        vector_similarity=0.95,  # high similarity (same domain)
    )
    assert accepted is False, f"Should have MISSED but: {reason}"
    assert "entity conflict" in reason or "keyword overlap" in reason
