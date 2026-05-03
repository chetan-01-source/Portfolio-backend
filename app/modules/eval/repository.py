from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase


class EvalRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.runs = db.eval_runs

    async def insert_run(
        self,
        *,
        query: str,
        answer: str,
        model: str,
        faithfulness: float | None = None,
        answer_relev: float | None = None,
        context_recall: float | None = None,
        tone_match: float | None = None,
    ) -> None:
        await self.runs.insert_one(
            {
                "query": query,
                "answer": answer,
                "model": model,
                "faithfulness": faithfulness,
                "answer_relev": answer_relev,
                "context_recall": context_recall,
                "tone_match": tone_match,
                "created_at": datetime.now(timezone.utc),
            }
        )

    async def recent_means(self, limit: int = 100) -> dict[str, float]:
        cursor = self.runs.find().sort("created_at", -1).limit(limit)
        rows = [r async for r in cursor]
        if not rows:
            return {}
        out: dict[str, float] = {}
        for k in ("faithfulness", "answer_relev", "context_recall", "tone_match"):
            vals = [r[k] for r in rows if r.get(k) is not None]
            if vals:
                out[k] = sum(vals) / len(vals)
        return out
