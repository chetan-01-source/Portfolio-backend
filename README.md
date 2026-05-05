<div align="center">

# CHET.ai — Backend

**A production-grade RAG backend powering [chetanmarathe.dev](https://chetan-portfolio-connect.web.app/)**
*Streaming · Multi-stage caching · Cross-encoder reranking · Lead capture · Eval-gated*

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Qdrant](https://img.shields.io/badge/Qdrant-vector%20DB-DC382D)](https://qdrant.tech/)
[![Redis](https://img.shields.io/badge/Redis-RediSearch-DC382D?logo=redis&logoColor=white)](https://redis.io/)
[![MongoDB](https://img.shields.io/badge/MongoDB-Motor-47A248?logo=mongodb&logoColor=white)](https://www.mongodb.com/)
[![OpenRouter](https://img.shields.io/badge/OpenRouter-LLM%20gateway-000000)](https://openrouter.ai/)

</div>

---

## Overview

CHET.ai is the backend that turns my portfolio site into a conversational, grounded assistant. Visitors can ask anything about my experience, projects, or stack — answers stream back token-by-token, every claim is sourced from a curated knowledge base, and visitors interested in hiring me are walked through a structured lead-capture flow that emails my details only after explicit consent.

The system is designed around three goals: **fast** (sub-second time-to-first-token via multi-stage caching), **cheap** (most queries never hit an LLM), and **honest** (the model can only answer from retrieved context — no hallucinated employers, dates, or links).

---

## Architecture

```
                 ┌─────────────────────────────────────────────────────────┐
                 │                      Browser (SSE)                      │
                 └─────────────────────────────────────────────────────────┘
                                          │  POST /api/chat
                                          ▼
                 ┌─────────────────────────────────────────────────────────┐
                 │  FastAPI · controller → service → repository · DI deps  │
                 └─────────────────────────────────────────────────────────┘
                                          │
        ┌─────────────────────────────────┼──────────────────────────────────┐
        ▼                                 ▼                                  ▼
 ┌──────────────┐               ┌─────────────────────┐            ┌──────────────────┐
 │ Exact Cache  │   miss        │  Semantic Cache     │  miss      │  RAG Pipeline    │
 │ Redis SHA-256│ ────────────▶ │  3-stage gate       │ ─────────▶ │                  │
 │ O(1) lookup  │               │  vec ≥ 0.93         │            │ embed → Qdrant   │
 └──────────────┘               │  entity conflict    │            │ → MMR diversify  │
        │ hit                   │  Jaccard ≥ 0.40     │            │ → BGE rerank     │
        ▼                       └─────────────────────┘            │ → top-K context  │
   stream answer                          │ hit                    └──────────────────┘
                                          ▼                                  │
                                    stream answer                            ▼
                                                                ┌─────────────────────┐
                                                                │  OpenRouter LLM     │
                                                                │  (cheap → strong)   │
                                                                │  SSE token stream   │
                                                                └─────────────────────┘
                                                                            │
                                                                            ▼
                                                              ┌─────────────────────────┐
                                                              │ MongoDB (Motor)         │
                                                              │ chat_logs · leads · eval│
                                                              └─────────────────────────┘
```

**Key design choices**

- **Controller / Service / Repository** layering enforced by `import-linter` so business logic never leaks into HTTP or DB layers.
- **Query separation**: the cache key is the *raw* user message; retrieval embedding uses the *enriched* query (with conversation summary). Prevents history bleed across cache entries.
- **Reference-aware rewrite**: when the new turn contains a demonstrative ("this project", "that role"), a cheap LLM call resolves it against the chat summary before retrieval — so follow-ups don't fall off a cliff.
- **Hire flow persists *before* consent** so an abandoned consent question never loses the lead.

---

## Stack

| Layer | Tech | Why |
|---|---|---|
| API | **FastAPI** + Uvicorn | Async, type-safe, native SSE |
| LLM gateway | **OpenRouter** | One API, dynamic routing between cheap + strong tiers |
| Vector DB | **Qdrant Cloud** | HNSW search at scale, batched upserts |
| Cache + KV | **Redis 7 + RediSearch** | Exact (hash) + semantic (HNSW) cache in one process |
| Persistence | **MongoDB (Motor)** | Chat logs, hire leads, eval runs |
| Embeddings | OpenAI-compatible 1536-dim via OpenRouter | Same API as the LLM client |
| Reranker | BGE cross-encoder (HTTP sidecar, optional) | Re-scores diversified candidates |
| Email | **SendGrid Web API** + dynamic templates | In-process, no mail sidecar |
| Quality gate | **DeepEval** | CI gate on faithfulness / relevancy |
| Crawler | httpx + **Playwright** fallback | Handles JS-heavy sites (LinkedIn, LeetCode) |
| Streaming | Server-Sent Events | `meta` → `token`* → `done` |

---

## RAG pipeline

### Ingestion

```
data/  ──┬──  resume.pdf       ──▶ pypdfium2 → section-aware chunks (kind=exp)
         ├──  projects.yaml    ──▶ one chunk per project (kind=project, repo_url in payload)
         ├──  linkedin.md      ──▶ markdown headings → chunks (kind=exp)
         ├──  naukri.md        ──▶ markdown headings → chunks (kind=exp)
         └──  faqs.yaml        ──▶ Q/A pairs (kind=faq)
                                          │
                                          ▼
                          embed (1536-dim) ──▶ Qdrant upsert (batch=32)
                                          │
                                          ▼
                       SHA-256 per chunk → idempotent re-ingest
                                          │
                                          ▼
                          flush exact + semantic caches
```

Run with `python -m app.rag.ingest`. Re-runs are idempotent — only changed chunks re-embed.

### Retrieval

1. **Embed** the (history-enriched) query.
2. **Qdrant top-N** with cosine similarity.
3. **Kind boost** — chunks about Chetan rank above generic crawled URLs:
   `resume +0.15`, `exp +0.12`, `project +0.10`, `faq +0.08`, `url +0.0`.
4. **MMR** diversification (`λ=0.5`, `mmr_k=12`) — kills redundant near-duplicates.
5. **Cross-encoder rerank** (BGE) — top-N → top-K (default 8) sent to the LLM.
6. **Prompt assembly** — chunks formatted with their `kind`/`name`; project chunks always carry their `Repository:` line.

### Caching

| Tier | Key | Lookup | Purpose |
|---|---|---|---|
| Exact | `SHA-256(normalized_query + model)` | O(1) Redis `GET` | Catch verbatim repeats — 0 tokens |
| Semantic | 1536-dim vector | RediSearch HNSW + 3-stage gate | Catch paraphrases without context bleed |

The 3-stage semantic gate: **(1)** vector cosine ≥ 0.93, **(2)** entity-conflict gate (different companies/projects in the same category force a miss), **(3)** Jaccard keyword overlap ≥ 0.40 on stop-word-stripped tokens. Follow-up turns (anything where the conversation has memory) skip the semantic cache entirely — their answers are too context-bound to be reused safely.

---

## Repository layout

```
app/
├── main.py                    FastAPI factory, lifespan, middleware wire-up
├── config.py                  Settings (pydantic-settings) — single source of env truth
├── deps.py                    DI providers for every module
│
├── core/                      Cross-cutting infra (singletons)
│   ├── mongo.py               Motor client + index seeding
│   ├── redis.py               Redis + RediSearch index management
│   ├── qdrant.py              Async Qdrant client + collection bootstrap
│   ├── logging.py             Structured logging (loguru)
│   └── middleware.py          Request-ID + access logs
│
├── modules/                   Feature slices (controller → service → repository)
│   ├── chat/                  RAG chat — SSE streaming, intent detection
│   ├── hire/                  Lead capture flow — persist-then-consent → SendGrid
│   ├── ingest/                Resume/URL ingestion endpoints + crawler
│   ├── eval/                  DeepEval scoring + means endpoint
│   └── health/                Liveness probes for Mongo / Redis / Qdrant / OpenRouter
│
├── rag/                       RAG primitives (shared by modules)
│   ├── chunker.py             Section-aware splitting (resume / projects / markdown)
│   ├── embeddings.py          Embedding facade
│   ├── retriever.py           Qdrant + MMR + kind boost
│   ├── reranker.py            BGE cross-encoder HTTP client
│   ├── prompt.py              System-prompt assembly
│   └── ingest.py              Offline ingestion CLI
│
├── cache/
│   ├── exact.py               SHA-256 exact cache
│   └── semantic.py            3-stage semantic cache + entity registry
│
└── llm/
    ├── openrouter.py          Async chat + embed client (retry, streaming)
    ├── routing.py             Cheap vs. strong model selection
    └── prompts/
        ├── system.md          Grounded system prompt + profile/repo link rules
        └── rewrite.md         Follow-up query reframer

data/                          Knowledge sources (committed) + .cache/ingest_hashes.json
docs/                          API collection + Postman exports
llm/context.md                 Architectural change log (read me for history)
plan.md                        Original spec + layering rules
scripts/
├── seed_indexes.py            Mongo + Qdrant bootstrap
├── ingest.sh                  python -m app.rag.ingest
└── eval.sh                    DeepEval CI gate
tests/                         pytest + mongomock-motor — no live infra needed
```

---

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/chat` | RAG chat — SSE stream emits `meta`, `token`*, `done` |
| DELETE | `/api/chat/cache` | Flush both caches (`x-ingest-api-key`) |
| POST | `/api/hire/start` | Begin lead capture, returns first question |
| POST | `/api/hire/answer` | Submit answer, returns next question or `done` |
| GET | `/api/health` | Pings Mongo · Redis · Qdrant · OpenRouter |
| GET | `/api/eval/means` | Recent DeepEval score means |
| GET | `/api/ingest/sources` | Summary of sources currently embedded |
| POST | `/api/ingest/resume` | Upload + embed a new resume PDF |
| POST | `/api/ingest/urls` | Crawl + embed external URLs |

Interactive docs: `http://localhost:8000/docs`
Postman collection: [docs/postman/CHET-ai.postman_collection.json](docs/postman/CHET-ai.postman_collection.json)
Detailed API guide: [docs/api-implementation-guide.md](docs/api-implementation-guide.md)

### `/api/chat` SSE protocol

```
event: meta
data: {"cache":"exact|semantic|null", "retrieved_ids":[...], "model":"...",
       "intent":"hire|null", "memory":true|false}

event: token
data: {"delta":"token text"}
... (streamed)

event: done
data: {"latency_ms":1234, "tokens_in":N, "tokens_out":N}
```

`meta.intent === "hire"` is set by a regex pre-filter. The frontend can auto-open the hire flow on that signal — zero LLM cost for the detection.

---

## Quick start (Docker)

```bash
cp .env.example .env
# Required:  OPENROUTER_API_KEY
# For email: SENDGRID_API_KEY + verified SENDGRID_FROM_EMAIL

docker compose up -d
docker compose exec api python -m app.rag.ingest
curl http://localhost:8000/api/health
```

## Local development

Requires Python 3.11+ and a reachable Mongo · Redis (with RediSearch) · Qdrant. URLs in `.env`.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

python scripts/seed_indexes.py     # Mongo indexes + Qdrant collection
python -m app.rag.ingest           # build the knowledge base
uvicorn app.main:app --reload
```

---

## Hire flow

The hire-me path is a deliberate, persisted state machine — not a chat tangent.

1. `POST /api/hire/start` → first structured question (name, etc.).
2. Each `POST /api/hire/answer` advances the lead document, which is **persisted on every step**.
3. The final consent step (`send_details=yes`) triggers `EmailClient.send_chetan_details(lead)` — a SendGrid dynamic-template email with my résumé PDF attached, contact details, and portfolio link.
4. The lead doc is updated with `emailed`, `email_msgid`, `emailed_at`. `send_details=no` records the choice without sending.
5. If the lead's freeform message explicitly asks for the resume/details, that itself is treated as consent.

See [plan.md §8](plan.md#8-hire-me-flow) for the formal spec.

---

## Tests & quality

```bash
pytest                 # 111 tests — service + cache + retrieval + ingest, all in-memory
pytest -m eval         # DeepEval faithfulness/relevancy gate (live LLM)
lint-imports           # enforces controller→service→repository layering
```

Service-layer tests use `mongomock-motor` and a stubbed SendGrid client — no live infra required, no flakiness.

---

## Configuration

All settings flow through [`app/config.py`](app/config.py). Key knobs:

| Variable | Purpose |
|---|---|
| `LLM_CHEAP_MODEL` / `LLM_STRONG_MODEL` | OpenRouter routing tiers |
| `EMBED_MODEL` | Embedding model id (1536-dim) |
| `SEMANTIC_CACHE_THRESHOLD=0.93` | Vector cosine floor for semantic-cache hit |
| `RERANKER_URL` | BGE sidecar URL — empty disables reranking |
| `RERANKER_TOP_N=8` | Final context size after rerank |
| `MMR_K=12` | Candidate pool size before rerank |
| `SENDGRID_TEMPLATE_ID` | Dynamic template used for the lead-facing email |
| `INCLUDE_PHONE_IN_EMAIL=false` | Whether to include phone in lead email |
| `CHETAN_RESUME_ATTACHMENT_PATH=data/resume.pdf` | Resume attached to lead email |
| `NOTIFY_CHETAN_ON_LEAD=true` | Send internal notification on every capture |
| `CORS_ORIGINS` | Comma-separated origins, or `*` |

See [`.env.example`](.env.example) for the full list.

---

## Layering rules

Enforced at CI time by [`import-linter`](pyproject.toml):

| Layer | May import | May NOT import |
|---|---|---|
| **Controller** | service · schemas · FastAPI · deps | repository · motor |
| **Service** | repository · schemas · core clients | FastAPI types · motor |
| **Repository** | motor · schemas | service · controller · FastAPI |

Run `lint-imports` to verify.

---

## Project history

Architectural changes are documented in [llm/context.md](llm/context.md) — a running engineering log indexed by date. Highlights:

- **3-stage semantic cache** — vector + entity gate + Jaccard, eliminates cross-topic cache pollution.
- **Query separation** — raw query for caching, enriched query for retrieval.
- **Follow-up rewrite** — LLM-assisted reference resolution before retrieval.
- **Playwright fallback** — JS-rendered crawl for sites that block plain httpx.
- **Batched Qdrant upserts** — fixed `WriteTimeout` on large URL ingests.

---

## License & contact

Personal project. Repo is open for reading; email [chetanmarathe0412@gmail.com](mailto:chetanmarathe0412@gmail.com) before forking commercially.

[LinkedIn](https://www.linkedin.com/in/chetan-marathe-235932231) · [GitHub](https://github.com/chetan-01-source) · [LeetCode](https://leetcode.com/u/chetanmarathe0412/)
