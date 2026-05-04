from app.config import Settings
from app.modules.chat.service import ChatService


class _Repo:
    def __init__(self) -> None:
        self.inserted = []

    async def recent_logs(self, *, session_id: str, limit: int):
        return [
            {"role": "user", "content": "Tell me about his current work"},
            {
                "role": "assistant",
                "content": "Chetan is currently at Schbang as an AI Full Stack Engineer.",
            },
        ]

    async def insert_log(self, **kwargs):
        self.inserted.append(kwargs)


class _Exact:
    def __init__(self) -> None:
        self.query = None

    async def get(self, query: str, model: str):
        self.query = query
        return None

    async def set(self, query: str, model: str, *, answer: str, doc_ids: list[str]):
        self.stored_query = query


class _Semantic:
    async def lookup(self, embedding: list[float]):
        return None

    async def store(self, **kwargs):
        self.stored = kwargs


class _Embedder:
    def __init__(self) -> None:
        self.text = None

    async def embed_one(self, text: str):
        self.text = text
        return [0.1, 0.2, 0.3]


class _Retriever:
    def __init__(self) -> None:
        self.query_text = None

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
        self.messages = None

    async def stream(self, *, model: str, messages: list[dict[str, str]], max_tokens: int):
        self.messages = messages
        yield "Schbang workstreams include CSAT automation."


async def test_stream_answer_contextualizes_follow_up_questions():
    repo = _Repo()
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

    chunks = [
        chunk
        async for chunk in service.stream_answer(
            session_id="session-1",
            message="What projects is he working on there?",
        )
    ]

    assert chunks[0].payload["memory"] is True
    assert "Chetan is currently at Schbang" in exact.query
    assert embedder.text == exact.query
    assert retriever.query_text == exact.query
    assert "RECENT CHAT SUMMARY:" in llm.messages[0]["content"]
    assert "What projects is he working on there?" in llm.messages[1]["content"]
    assert repo.inserted[0]["role"] == "user"
    assert repo.inserted[-1]["role"] == "assistant"
