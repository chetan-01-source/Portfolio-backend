from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=2000)


class ChatStreamMeta(BaseModel):
    cache: Literal["exact", "semantic"] | None = None
    retrieved_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    intent: Literal["hire"] | None = None
    memory: bool = False


class ChatStreamDone(BaseModel):
    latency_ms: int
    tokens_in: int
    tokens_out: int


class ChatLog(BaseModel):
    id: str | None = None
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    retrieved_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cache: Literal["exact", "semantic"] | None = None
    created_at: datetime
