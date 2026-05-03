"""Chat HTTP controller — POST /api/chat (SSE), DELETE /api/chat/cache."""

import json

from fastapi import APIRouter, Header, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.cache.exact import ExactCache
from app.cache.semantic import SemanticCache
from app.deps import DbDep, QdrantDep, RedisDep, SettingsDep
from app.llm.openrouter import OpenRouterClient
from app.modules.chat.repository import ChatRepository
from app.modules.chat.schemas import ChatRequest
from app.modules.chat.service import ChatService
from app.rag.embeddings import Embedder
from app.rag.reranker import Reranker
from app.rag.retriever import Retriever

router = APIRouter(prefix="/chat", tags=["chat"])

INGEST_API_KEY_HEADER = Header(default=None)


def _build_service(db, redis, qdrant, settings) -> tuple[ChatService, OpenRouterClient]:
    llm = OpenRouterClient(settings)
    service = ChatService(
        repo=ChatRepository(db),
        retriever=Retriever(qdrant, Reranker(settings), settings),
        embedder=Embedder(llm, settings),
        llm=llm,
        exact_cache=ExactCache(redis, settings),
        semantic_cache=SemanticCache(redis, settings),
        settings=settings,
    )
    return service, llm


@router.post("")
async def chat(
    body: ChatRequest,
    db: DbDep,
    redis: RedisDep,
    qdrant: QdrantDep,
    settings: SettingsDep,
):
    service, llm = _build_service(db, redis, qdrant, settings)

    async def event_source():
        try:
            async for chunk in service.stream_answer(
                session_id=body.session_id, message=body.message
            ):
                yield {"event": chunk.type, "data": json.dumps(chunk.payload)}
        finally:
            await llm.aclose()

    return EventSourceResponse(event_source())


@router.delete("/cache")
async def flush_cache(
    redis: RedisDep,
    settings: SettingsDep,
    x_ingest_api_key: str | None = INGEST_API_KEY_HEADER,
):
    """Flush all chat caches (exact + semantic). Protected by ingest API key."""
    if settings.ingest_api_key and x_ingest_api_key != settings.ingest_api_key:
        raise HTTPException(status_code=401, detail="invalid ingest api key")

    exact = ExactCache(redis, settings)
    semantic = SemanticCache(redis, settings)
    n_exact = await exact.flush_all()
    n_semantic = await semantic.flush_all()

    return {
        "ok": True,
        "flushed": {"exact": n_exact, "semantic": n_semantic},
    }

