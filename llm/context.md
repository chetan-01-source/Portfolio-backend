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

## 2026-05-04: Three-stage semantic cache + query separation (major cache fix)

**Problem:** Cached responses leaked across different projects/companies. "CSAT project details" returned a cached "CometChat experience" answer. Two root causes:
1. The cache stored the _contextual query_ (which includes full conversation history), so two different questions with the same chat history looked identical to the cache.
2. Vector-only similarity (0.93 threshold) can't distinguish topically-adjacent queries about the same person.

**Solution — Three architectural changes:**

### 1. Query Separation (`app/modules/chat/service.py`)
- `user_query = message` — raw user input, used for **cache lookup/store only**. Never includes conversation history.
- `retrieval_query = _contextual_query(message, conversation_summary)` — enriched with history, used for **embedding, retrieval, and LLM context**.
- This prevents the cache from storing/comparing queries that contain previous assistant answers.

### 2. Memory-Aware Caching (`app/modules/chat/service.py`)
- **Follow-up questions** (when `conversation_summary` exists) **skip semantic cache entirely** — both lookup and storage.
- Follow-up answers depend on conversation state and would be misleading if served to a different session.
- Exact cache also skips storage for follow-ups.
- Standalone queries (no memory) use full cache pipeline normally.

### 3. Three-Stage Semantic Cache (`app/cache/semantic.py`)
- **Stage 1 (vector):** Cosine similarity ≥ 0.93 (unchanged).
- **Stage 2 (entity conflict gate):** Known entities registry (companies: schbang, cometchat; projects: csat, gittogether, britannia, etc.; tech domains: voice, webflow). If new query and cached query reference **different entities in the same category** (e.g., two different companies), force MISS regardless of similarity.
- **Stage 3 (keyword overlap):** Jaccard similarity of keyword sets ≥ 0.40 (unchanged from previous).
- Added `should_accept_cache_hit()` — testable function encapsulating all 3 stages.
- Cache `store()` now persists detected entities alongside each entry.
- Expanded stopwords with generic HR/professional filler: `role`, `responsibilities`, `overview`, `info`, `working`, `experience`, `current`, `job`, `position`.

**Files Changed:**
- `app/cache/semantic.py` — Three-stage cache with entity registry, conflict detection, `should_accept_cache_hit()`.
- `app/modules/chat/service.py` — Query separation (`user_query` vs `retrieval_query`), memory-aware cache bypass.
- `tests/test_semantic_cache.py` — 68 tests: keyword extraction, entity detection, entity conflict, Jaccard, `should_accept_cache_hit()`, 15-pair MISS matrix, 6-pair HIT matrix, edge cases, 4 regression tests.
- `tests/modules/chat/test_service_memory.py` — 7 tests: follow-up skips cache, standalone uses cache, cache never stores history, exact cache uses raw message, regression test.

**Result:** 79/79 tests pass. RediSearch index dropped and recreated with `entities` field. All cache entries flushed.

## 2026-05-04: Generate Detailed LinkedIn Post on RAG Optimization

**Changes:**
- Rewrote `linkedin_post.md` to provide a highly detailed, professional, and engaging overview of the portfolio's RAG backend architecture.
- Outlined the tech stack (FastAPI, Qdrant, Redis RediSearch, OpenRouter, SSE).
- Deeply explained the 3-stage Semantic Caching system (Exact Match, Vector Similarity, Entity Conflict Gate, and Keyword Overlap) that achieves O(1) lookups and drastically reduces token spending.
- Highlighted the advanced retrieval pipeline featuring MMR (Maximal Marginal Relevance), Kind-Based Boosting, and Cross-Encoder Reranking for context compression.
