# LLM Context — Change Log

## 2026-05-03: Fix `POST /api/ingest/urls` internal_error (WriteTimeout)

**Root Cause:** When ingesting URLs, the crawler could produce 100+ chunks (e.g., crawling GitHub with `max_depth=1` follows many internal links). All embedding vectors (1536-dim floats as JSON) plus text payloads were sent to Qdrant Cloud in a **single upsert call**, causing `httpx.WriteTimeout` on the Qdrant client's default 5s timeout.

**Files Changed:**

- `app/core/qdrant.py` — Added `timeout=30` to `AsyncQdrantClient` init to handle large payloads to Qdrant Cloud.
- `app/modules/ingest/service.py` — Changed `_embed_and_upsert()` to batch Qdrant upserts in groups of 32 points instead of sending all at once.

**Notes:**
- LinkedIn and LeetCode URLs are still skipped (HTTP 999/403) since they require auth/JS rendering. This is expected behavior and surfaces in the response `skipped` field.
- The GitHub repos page works but with `same_domain_only=True` + `max_depth=1`, it also crawls unrelated GitHub feature pages. Consider using `max_depth=0` for better signal.

## 2026-05-03: Add Playwright headless browser fallback for JS-heavy sites

**Problem:** LinkedIn (HTTP 999) and LeetCode (HTTP 403) block plain HTTP requests. These sites require JavaScript rendering to show profile data.

**Solution:** Added Playwright (headless Chromium) as a fallback in the crawler. When httpx gets an HTTP error, the crawler retries with a real browser.

**Files Changed:**

- `pyproject.toml` — Added `playwright>=1.40.0` dependency.
- `app/modules/ingest/crawler.py` — Added `_fetch_with_browser()` function using Playwright. Known browser-required domains (`linkedin.com`, `leetcode.com`) skip httpx entirely. Other domains fall back to browser on HTTP errors. Browser gets 2× the normal timeout.

**Setup Required:** Run `playwright install chromium` after pip install to download the browser binary.

**Notes:**
- LinkedIn returns a sign-up gate for unauthenticated access — limited profile info. Full profile requires auth cookies.
- LeetCode works fully — renders the profile SPA and extracts rank, solved problems, languages, etc.

## 2026-05-03: Improve chat response quality + auto cache invalidation

**Problem:** Chat responses were thin (1-3 sentences, 53 tokens) because: system prompt forced brevity, max_tokens was 350, irrelevant GitHub feature pages drowned out resume/experience chunks in retrieval, and stale cached responses persisted after new data ingestion.

**Fixes (5 changes):**

- `app/llm/prompts/system.md` — Rewrote system prompt: removed "1–3 short sentences" constraint, added instructions for structured/detailed responses about experience, projects, and skills.
- `app/modules/chat/service.py` — Increased `max_tokens` from 350 → 800.
- `app/rag/retriever.py` — Added kind-based score boosting: resume (+0.15), exp (+0.12), project (+0.10), faq (+0.08) rank higher than generic url chunks (+0.0).
- `app/config.py` + `.env` — Increased `reranker_top_n` from 4 → 6 so more context reaches the LLM.
- `app/cache/exact.py` + `app/cache/semantic.py` — Added `flush_all()` method to both caches.
- `app/modules/ingest/service.py` + `app/modules/ingest/controller.py` — After every successful ingestion, both chat caches are automatically flushed so new context is used immediately.

**Result:** Response improved from 53 tokens (CometChat only) to 141 tokens with structured bullet points covering all roles. Resume/experience chunks now reliably appear in top-6 retrieval results.

## 2026-05-03: Add manual cache flush endpoint

- `app/modules/chat/controller.py` — Added `DELETE /api/chat/cache` endpoint. Flushes both exact and semantic caches. Protected by `x-ingest-api-key` header. Returns `{"ok": true, "flushed": {"exact": N, "semantic": N}}`.

## 2026-05-03: Auto-detect hire/contact intent in chat

- `app/modules/chat/service.py` — Added `_detect_hire_intent()` regex matcher. Matches 15+ patterns: hire, contact, resume, schedule call, collaborate, job opportunity, reach out, etc. When triggered, the SSE `meta` event includes `"intent": "hire"` (otherwise `null`). Frontend should check `meta.intent === "hire"` and auto-open the hire flow.
- `docs/postman/CHET-ai.postman_collection.json` — Added `Chat - Flush Cache` entry.

## 2026-05-03: Fix missing project data + improve subjective question handling

**Problem:** "What's his strongest project?" failed because only 2 projects existed in Qdrant (CHET.ai, Portfolio). WhatsApp Clone, GitTogether, and Britannia Campaign Engine were buried in resume PDF chunks tagged as `kind=resume`, not discoverable as projects.

**Root cause:** `data/projects.yaml` (the source file that feeds the ingestion pipeline) only had 2 entries. The ingestion flow is: `projects.yaml → chunker → embeddings → Qdrant`. At query time, chunks are retrieved FROM Qdrant, not from the YAML directly.

**Fixes:**

- `data/projects.yaml` — Added 3 missing projects: WhatsApp Clone using CometChat, GitTogether, and Britannia Campaign Engine. Total: 5 projects.
- Re-ran `python -m app.rag.ingest` — 3 new project chunks embedded and upserted to Qdrant.
- `app/llm/prompts/system.md` — Added handling for comparative/evaluative questions ("strongest", "best", "most complex"). Added rule to never skip items present in context.
- `app/config.py` + `.env` — Increased `reranker_top_n` from 6 → 8.
- `app/rag/retriever.py` — Increased `mmr_k` from 8 → 12 so more diverse candidates reach the reranker.
- Flushed all stale caches.

## 2026-05-03: Fix CORS preflight failure

- `.env` — `CORS_ORIGINS` had port `5174` but frontend runs on `5173`. Changed to `*` to allow all origins.
- `app/main.py` — When `CORS_ORIGINS=*`, automatically disables `allow_credentials` (Starlette rejects wildcard + credentials combo).

## 2026-05-04: Two-stage semantic cache — vector similarity + keyword overlap

**Problem:** "Tell me about CSAT project" and "experience with Schbang" returned the same cached response. Vector-only similarity (0.93 threshold) can't distinguish topically-adjacent queries about the same person/company.

**Solution — Two-stage verification in `app/cache/semantic.py`:**
- **Stage 1 (vector):** Cosine similarity ≥ 0.93 (unchanged, keeps caching aggressive for cost savings).
- **Stage 2 (keyword):** Extract meaningful keywords from both new and cached queries (minus stopwords), compute Jaccard overlap. Reject if overlap < 0.40.
- Example: "CSAT project" keywords = `{csat, project}`, cached "Schbang experience" keywords = `{schbang, experience}` → Jaccard = 0/4 = 0.0 → cache MISS despite vector match.
- Added detailed logging: `sem-cache HIT/MISS (keyword)` for debugging.
- `app/modules/chat/service.py` — Passes `query_text` to `semantic.lookup()` for Stage 2 verification.
- Threshold reverted to `0.93` — keyword overlap layer handles false positives.
