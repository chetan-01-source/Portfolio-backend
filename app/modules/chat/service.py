"""Chat orchestration: cache check → retrieve → generate (stream) → cache write → log."""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.cache.exact import ExactCache
from app.cache.semantic import SemanticCache
from app.config import Settings
from app.core.logging import logger
from app.llm.openrouter import OpenRouterClient
from app.modules.chat.repository import ChatRepository
from app.rag.embeddings import Embedder
from app.rag.prompt import build_messages
from app.rag.retriever import Retriever

_HISTORY_TURN_LIMIT = 8
_HISTORY_CHAR_LIMIT = 1600
_HISTORY_ITEM_CHAR_LIMIT = 360
_UNHELPFUL_ASSISTANT_MEMORY = (
    "i don't have that on file",
    "best to ask chetan directly",
)

# ── Hire-intent detection ──────────────────────────────────────────
# Fast keyword-based check. Runs before any LLM call so it's zero-cost.
# If any pattern matches, the SSE `meta` event includes `intent: "hire"`
# so the frontend can auto-trigger the hire flow.
_HIRE_PATTERNS = re.compile(
    r"""
    hire\b
    | \bhiring\b
    | \brecruit
    | \bjob\s+open
    | \bwork\s+(?:with|for|together)
    | \bcollaborat
    | \bcontact\s+(?:him|chetan|you)
    | \breach\s+(?:out|him|chetan|you)
    | \bget\s+(?:in\s+touch|his\s+(?:details|resume|cv|email|contact|number|phone))
    | \bsend\s+(?:details|resume|cv|email)
    | \bresume\b
    | \b(?:cv|curriculum\s+vitae)\b
    | \bemail\s+(?:id|address|him|chetan)
    | \bphone\s+(?:number|no)
    | \binterested\s+in\s+(?:him|chetan|working)
    | \bopportunit
    | \bconnect\s+with
    | \bschedule\s+(?:an?\s+)?(?:call|meeting|interview)
    | \bbook\s+(?:an?\s+)?(?:call|meeting|interview)
    | \bavailab(?:le|ility)\s+(?:for|to)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _detect_hire_intent(message: str) -> bool:
    """Return True if the message signals hiring or contact intent."""
    return bool(_HIRE_PATTERNS.search(message))


def _compact_text(value: str, *, limit: int) -> str:
    compacted = re.sub(r"\s+", " ", (value or "").strip())
    if len(compacted) <= limit:
        return compacted
    return compacted[: limit - 3].rstrip() + "..."


def _build_conversation_summary(logs: list[dict]) -> str:
    """Small extractive memory for follow-up questions in the same chat session."""
    lines: list[str] = []
    for log in logs[-_HISTORY_TURN_LIMIT:]:
        role = "User" if log.get("role") == "user" else "Assistant"
        content = _compact_text(str(log.get("content") or ""), limit=_HISTORY_ITEM_CHAR_LIMIT)
        if role == "Assistant":
            lowered = content.lower()
            if any(phrase in lowered for phrase in _UNHELPFUL_ASSISTANT_MEMORY):
                continue
        if content:
            lines.append(f"{role}: {content}")

    summary = "\n".join(lines).strip()
    if len(summary) <= _HISTORY_CHAR_LIMIT:
        return summary

    # Keep the newest turns when the session is long.
    trimmed: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) + 1 > _HISTORY_CHAR_LIMIT:
            break
        trimmed.append(line)
        used += len(line) + 1
    return "\n".join(reversed(trimmed))


def _contextual_query(message: str, conversation_summary: str) -> str:
    if not conversation_summary:
        return message
    return (
        "Recent chat summary:\n"
        f"{conversation_summary}\n\n"
        f"Current user question: {message}"
    )


@dataclass
class StreamChunk:
    type: str  # "meta" | "token" | "done" | "error"
    payload: dict


class ChatService:
    def __init__(
        self,
        *,
        repo: ChatRepository,
        retriever: Retriever,
        embedder: Embedder,
        llm: OpenRouterClient,
        exact_cache: ExactCache,
        semantic_cache: SemanticCache,
        settings: Settings,
    ) -> None:
        self.repo = repo
        self.retriever = retriever
        self.embedder = embedder
        self.llm = llm
        self.exact = exact_cache
        self.semantic = semantic_cache
        self.settings = settings

    async def stream_answer(
        self, *, session_id: str, message: str
    ) -> AsyncIterator[StreamChunk]:
        started = time.perf_counter()
        model = self.settings.llm_cheap_model
        intent = "hire" if _detect_hire_intent(message) else None
        conversation_summary = ""
        try:
            history = await self.repo.recent_logs(session_id=session_id, limit=_HISTORY_TURN_LIMIT)
            conversation_summary = _build_conversation_summary(history)
        except Exception as e:
            logger.warning(f"chat history lookup failed: {e}")
        cache_query = _contextual_query(message, conversation_summary)

        # Log user turn first.
        await self.repo.insert_log(session_id=session_id, role="user", content=message)

        # 1. Exact cache.
        hit = await self.exact.get(cache_query, model)
        if hit is not None:
            yield StreamChunk(
                "meta",
                {
                    "cache": "exact",
                    "retrieved_ids": hit.get("doc_ids", []),
                    "model": model,
                    "intent": intent,
                    "memory": bool(conversation_summary),
                },
            )
            yield StreamChunk("token", {"delta": hit["answer"]})
            yield StreamChunk(
                "done",
                {
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "tokens_in": 0,
                    "tokens_out": 0,
                },
            )
            await self.repo.insert_log(
                session_id=session_id,
                role="assistant",
                content=hit["answer"],
                retrieved_ids=hit.get("doc_ids", []),
                model=model,
                cache="exact",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            return

        # 2. Embed query (used by semantic cache + retrieval).
        try:
            qvec = await self.embedder.embed_one(cache_query)
        except Exception as e:
            logger.error(f"embed failed: {e}")
            yield StreamChunk("error", {"message": "embedding failed"})
            return

        # 3. Semantic cache.
        sem = await self.semantic.lookup(qvec)
        if sem is not None:
            yield StreamChunk(
                "meta",
                {
                    "cache": "semantic",
                    "retrieved_ids": sem.get("doc_ids", []),
                    "model": model,
                    "intent": intent,
                    "memory": bool(conversation_summary),
                },
            )
            yield StreamChunk("token", {"delta": sem["answer"]})
            yield StreamChunk(
                "done",
                {
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "tokens_in": 0,
                    "tokens_out": 0,
                },
            )
            await self.repo.insert_log(
                session_id=session_id,
                role="assistant",
                content=sem["answer"],
                retrieved_ids=sem.get("doc_ids", []),
                model=model,
                cache="semantic",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            return

        # 4. Retrieve.
        docs = await self.retriever.search(query_vec=qvec, query_text=cache_query)
        doc_ids = [d["id"] for d in docs]

        yield StreamChunk(
            "meta",
            {
                "cache": None,
                "retrieved_ids": doc_ids,
                "model": model,
                "intent": intent,
                "memory": bool(conversation_summary),
            },
        )

        # 5. Generate (stream).
        messages = build_messages(message, docs, conversation_summary=conversation_summary)
        collected: list[str] = []
        try:
            async for delta in self.llm.stream(model=model, messages=messages, max_tokens=2500):
                collected.append(delta)
                yield StreamChunk("token", {"delta": delta})
        except Exception as e:
            logger.error(f"llm stream failed: {e}")
            yield StreamChunk("error", {"message": "generation failed"})
            return

        answer = "".join(collected).strip()
        latency_ms = int((time.perf_counter() - started) * 1000)

        yield StreamChunk(
            "done",
            {"latency_ms": latency_ms, "tokens_in": 0, "tokens_out": 0},
        )

        await self.repo.insert_log(
            session_id=session_id,
            role="assistant",
            content=answer,
            retrieved_ids=doc_ids,
            model=model,
            latency_ms=latency_ms,
        )

        # 6. Cache writes.
        if answer:
            try:
                await self.exact.set(cache_query, model, answer=answer, doc_ids=doc_ids)
                await self.semantic.store(
                    embedding=qvec,
                    query=cache_query,
                    answer=answer,
                    doc_ids=doc_ids,
                    model=model,
                )
            except Exception as e:
                logger.warning(f"cache write failed: {e}")
