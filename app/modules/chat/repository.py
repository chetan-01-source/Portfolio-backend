"""All Mongo I/O for chat_logs."""

from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase


class ChatRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.logs = db.chat_logs

    async def insert_log(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        retrieved_ids: list[str] | None = None,
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        cache: str | None = None,
    ) -> None:
        await self.logs.insert_one(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "retrieved_ids": retrieved_ids or [],
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": latency_ms,
                "cache": cache,
                "created_at": datetime.now(timezone.utc),
            }
        )
