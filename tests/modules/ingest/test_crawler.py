import httpx

from app.modules.ingest.crawler import crawl_urls


def _handler(request: httpx.Request) -> httpx.Response:
    pages = {
        "https://example.com/": (
            "text/html",
            """
            <html><head><title>Home</title></head>
            <body><h1>Portfolio</h1><p>Chetan builds AI products.</p>
            <a href="/projects">Projects</a>
            <a href="https://external.test/">External</a>
            </body></html>
            """,
        ),
        "https://example.com/projects": (
            "text/html",
            "<html><body><h1>Projects</h1><p>RAG backend and portfolio app.</p></body></html>",
        ),
    }
    content_type, text = pages[str(request.url)]
    return httpx.Response(200, headers={"content-type": content_type}, text=text)


async def test_crawl_urls_extracts_text_and_same_domain_links(monkeypatch):
    real_async_client = httpx.AsyncClient

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.client = real_async_client(transport=httpx.MockTransport(_handler))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            await self.client.aclose()

        async def get(self, url):
            return await self.client.get(url)

    monkeypatch.setattr("app.modules.ingest.crawler.httpx.AsyncClient", _FakeAsyncClient)

    docs, skipped = await crawl_urls(
        ["https://example.com/"],
        max_pages=5,
        max_depth=1,
        same_domain_only=True,
        timeout_seconds=5,
        max_bytes=100_000,
    )

    assert skipped == []
    assert [doc.url for doc in docs] == ["https://example.com/", "https://example.com/projects"]
    assert "Chetan builds AI products" in docs[0].text
    assert "RAG backend" in docs[1].text
