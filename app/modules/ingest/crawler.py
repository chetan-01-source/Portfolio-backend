from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from app.core.logging import logger


@dataclass
class CrawledDocument:
    url: str
    title: str | None
    text: str


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title: str | None = None
        self._tag_stack: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        self._tag_stack.append(tag)
        if tag == "title":
            self._in_title = True
        if tag == "a":
            for key, value in attrs:
                if key == "href" and value:
                    self.links.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if self._tag_stack:
            self._tag_stack.pop()
        if tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if any(tag in {"script", "style", "noscript", "svg"} for tag in self._tag_stack):
            return
        clean = data.strip()
        if not clean:
            return
        if self._in_title:
            self.title = clean
        self.parts.append(clean)


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") or "/", "", parsed.query, ""))


def _github_raw_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 5 and parts[2] == "blob":
        owner, repo, _, branch = parts[:4]
        path = "/".join(parts[4:])
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    if len(parts) == 2:
        owner, repo = parts
        return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
    return None


def _is_allowed_link(base_url: str, next_url: str, same_domain_only: bool) -> bool:
    parsed = urlparse(next_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not same_domain_only:
        return True
    return parsed.netloc == urlparse(base_url).netloc


# ---------------------------------------------------------------------------
# Playwright headless browser fallback
# ---------------------------------------------------------------------------

# Domains that are known to block plain HTTP and need a headless browser
_BROWSER_DOMAINS = {"linkedin.com", "www.linkedin.com", "leetcode.com", "www.leetcode.com"}


def _needs_browser(url: str) -> bool:
    """Check if a URL is known to require a headless browser."""
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith(f".{d}") for d in _BROWSER_DOMAINS)


async def _fetch_with_browser(
    url: str,
    *,
    timeout_ms: int = 30_000,
    max_bytes: int = 2_000_000,
) -> CrawledDocument | None:
    """Render a page with Playwright (headless Chromium) and extract visible text."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("playwright not installed — skipping browser fetch for {}", url)
        return None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                java_script_enabled=True,
            )
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            # Extra wait for JS-heavy SPAs (LeetCode, LinkedIn)
            await page.wait_for_timeout(3000)

            # Extract title
            title = await page.title()

            # Extract visible text content, stripping scripts/styles
            text = await page.evaluate("""() => {
                // Remove script, style, noscript, svg elements
                const removeTags = ['script', 'style', 'noscript', 'svg', 'link', 'meta'];
                removeTags.forEach(tag => {
                    document.querySelectorAll(tag).forEach(el => el.remove());
                });
                return document.body ? document.body.innerText : '';
            }""")

            text = _clean_text(text or "")
            if not text:
                return None

            # Respect max_bytes limit
            text = text[:max_bytes]

            return CrawledDocument(url=url, title=title or None, text=text)
        except Exception as e:
            logger.warning("Playwright fetch failed for {}: {}", url, e)
            return None
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Main crawler
# ---------------------------------------------------------------------------

async def crawl_urls(
    urls: list[str],
    *,
    max_pages: int,
    max_depth: int,
    same_domain_only: bool,
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[list[CrawledDocument], list[str]]:
    docs: list[CrawledDocument] = []
    skipped: list[str] = []
    queue: deque[tuple[str, str, int]] = deque()

    for url in urls:
        queue.append((url, url, 0))
        raw = _github_raw_url(url)
        if raw:
            queue.append((raw, url, 0))

    seen: set[str] = set()
    # Collect URLs that failed with httpx and need browser fallback
    browser_fallback_urls: list[str] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout_seconds,
        headers={"User-Agent": "CHET.ai-ingest/1.0"},
    ) as client:
        while queue and len(docs) < max_pages:
            url, root, depth = queue.popleft()
            normalized = _normalize_url(url)
            if normalized in seen:
                continue
            seen.add(normalized)

            # If this domain is known to need a browser, skip httpx entirely
            if _needs_browser(url):
                browser_fallback_urls.append(url)
                continue

            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPStatusError:
                # HTTP error (403, 999, etc.) — try browser fallback
                browser_fallback_urls.append(url)
                continue
            except Exception as e:
                skipped.append(f"{url} ({type(e).__name__})")
                continue

            raw_content = response.content[:max_bytes]
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                html = raw_content.decode(response.encoding or "utf-8", errors="replace")
                parser = _HTMLTextParser()
                parser.feed(html)
                text = _clean_text("\n".join(parser.parts))
                if text:
                    docs.append(CrawledDocument(url=str(response.url), title=parser.title, text=text))
                if depth < max_depth:
                    for href in parser.links:
                        next_url = _normalize_url(urljoin(str(response.url), href))
                        if _is_allowed_link(root, next_url, same_domain_only):
                            queue.append((next_url, root, depth + 1))
                continue

            if any(kind in content_type for kind in ("text/plain", "text/markdown", "application/json")):
                text = _clean_text(raw_content.decode(response.encoding or "utf-8", errors="replace"))
                if text:
                    docs.append(CrawledDocument(url=str(response.url), title=None, text=text))
                continue

            skipped.append(f"{url} (unsupported content-type: {content_type or 'unknown'})")

    # --- Playwright fallback for failed URLs ---
    if browser_fallback_urls:
        remaining_slots = max_pages - len(docs)
        for url in browser_fallback_urls[:remaining_slots]:
            logger.info("Attempting browser fetch for {}", url)
            doc = await _fetch_with_browser(
                url,
                timeout_ms=int(timeout_seconds * 2 * 1000),  # give browser 2x the timeout
                max_bytes=max_bytes,
            )
            if doc:
                docs.append(doc)
                logger.info("Browser fetch succeeded for {} ({} chars)", url, len(doc.text))
            else:
                skipped.append(f"{url} (browser fetch failed)")

    return docs, skipped
