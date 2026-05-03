"""DeepEval orchestration. Loads dataset, runs against the live RAG pipeline, persists scores."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.cache.exact import ExactCache
from app.cache.semantic import SemanticCache
from app.config import Settings
from app.core.logging import logger
from app.llm.openrouter import OpenRouterClient
from app.modules.chat.repository import ChatRepository
from app.modules.chat.service import ChatService
from app.modules.eval.repository import EvalRepository
from app.rag.embeddings import Embedder
from app.rag.retriever import Retriever

DATASET_PATH = Path(__file__).resolve().parent / "dataset.json"


def _load_dataset() -> list[dict[str, Any]]:
    if not DATASET_PATH.exists():
        return []
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


class EvalService:
    def __init__(
        self,
        *,
        repo: EvalRepository,
        chat: ChatService,
        settings: Settings,
    ) -> None:
        self.repo = repo
        self.chat = chat
        self.settings = settings

    async def _generate(self, query: str) -> tuple[str, list[str], str]:
        """Run a single query through the chat pipeline; return (answer, doc_ids, model)."""
        chunks: list[str] = []
        doc_ids: list[str] = []
        model = self.settings.llm_cheap_model
        async for c in self.chat.stream_answer(session_id="eval", message=query):
            if c.type == "meta":
                doc_ids = c.payload.get("retrieved_ids", []) or []
                model = c.payload.get("model") or model
            elif c.type == "token":
                chunks.append(c.payload.get("delta", ""))
        return "".join(chunks).strip(), doc_ids, model

    async def run(self) -> dict[str, Any]:
        try:
            from deepeval import evaluate  # type: ignore
            from deepeval.test_case import LLMTestCase  # type: ignore

            from app.modules.eval.metrics import build_metrics
        except ImportError:
            logger.warning("DeepEval not installed; install [eval] extras to run evals")
            return {"ok": False, "error": "deepeval not installed"}

        dataset = _load_dataset()
        if not dataset:
            return {"ok": False, "error": "empty dataset"}

        cases = []
        scored: list[dict[str, Any]] = []
        for item in dataset:
            query = item["question"]
            expected = item.get("expected_answer", "")
            answer, doc_ids, model = await self._generate(query)
            case = LLMTestCase(
                input=query,
                actual_output=answer,
                expected_output=expected,
                retrieval_context=item.get("contexts", []),
            )
            cases.append(case)
            scored.append({"query": query, "answer": answer, "model": model, "doc_ids": doc_ids})

        metrics = build_metrics()
        result = evaluate(test_cases=cases, metrics=metrics)

        # Persist per-case scores.
        for case_idx, s in enumerate(scored):
            scores: dict[str, float] = {}
            try:
                tr = result.test_results[case_idx]
                for m in tr.metrics_data:
                    name = (m.name or "").lower()
                    if "faith" in name:
                        scores["faithfulness"] = float(m.score or 0)
                    elif "relev" in name:
                        scores["answer_relev"] = float(m.score or 0)
                    elif "recall" in name:
                        scores["context_recall"] = float(m.score or 0)
                    elif "tone" in name:
                        scores["tone_match"] = float(m.score or 0)
            except (AttributeError, IndexError):
                pass
            await self.repo.insert_run(
                query=s["query"], answer=s["answer"], model=s["model"], **scores
            )

        return {"ok": True, "n": len(cases), "means": await self.repo.recent_means(len(cases))}


async def run_cli() -> None:
    """Entry point used by `scripts/eval.sh` and CI."""
    from app.config import get_settings
    from app.core.logging import configure_logging
    from app.core.mongo import get_db
    from app.core.qdrant import get_qdrant
    from app.core.redis import get_redis

    configure_logging()
    settings = get_settings()
    db = get_db()
    redis = get_redis()
    qdrant = get_qdrant()
    llm = OpenRouterClient(settings)

    try:
        from app.rag.reranker import Reranker

        chat = ChatService(
            repo=ChatRepository(db),
            retriever=Retriever(qdrant, Reranker(settings), settings),
            embedder=Embedder(llm, settings),
            llm=llm,
            exact_cache=ExactCache(redis, settings),
            semantic_cache=SemanticCache(redis, settings),
            settings=settings,
        )
        service = EvalService(repo=EvalRepository(db), chat=chat, settings=settings)
        out = await service.run()
        logger.info(f"Eval result: {out}")
    finally:
        await llm.aclose()


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_cli())
