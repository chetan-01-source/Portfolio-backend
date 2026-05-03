# CHET.ai — Backend Plan (Production-Ready RAG)

> Personal assistant for Chetan Marathe's portfolio. Answers visitor questions about Chetan's experience/projects/skills using RAG, and runs a "Hire Me" lead-capture flow that can email Chetan directly.

---

## 1. Objectives & Non-goals

**Objectives**
- Grounded answers from a curated personal knowledge base — no hallucinated employers, dates, or skills.
- Low p95 latency (< 1.5s for cached, < 3s for cold) and low cost (< $5/mo at 1k conversations).
- A "Hire Me" suggestion that captures structured leads, optionally emails them, and persists every lead to the DB.
- Continuous quality measurement via DeepEval, gated in CI.

**Non-goals (v1)**
- Multi-user accounts / auth.
- Long-running agentic tool use beyond the hire flow.
- Self-hosted LLMs — we use OpenRouter exclusively.

---

## 2. Stack Decision

| Layer | Choice | Why |
|---|---|---|
| API | **FastAPI (Python 3.11+)** | Best-in-class RAG ecosystem, native DeepEval, async/streaming. |
| API structure | **Controller → Service → Repository** layers | Controllers track/validate requests; services hold business logic; repositories own all DB I/O. Keeps routes thin and unit-testable. |
| LLM gateway | **OpenRouter** | Required. Single key, fall-back routing across providers. |
| Embeddings | **OpenAI `text-embedding-3-small` via OpenRouter** | $0.02 / 1M tok, 1536-dim, strong retrieval quality. |
| Vector DB | **Qdrant** (Cloud free tier or self-hosted in Docker) | Fast, supports hybrid + payload filtering. |
| Reranker | **BGE-reranker-v2-m3** (self-hosted in a tiny FastAPI sidecar) OR **Cohere Rerank-3** | Cross-encoder gives a real precision lift for top-K. |
| Cache | **Redis 7 + RediSearch (vector index)** | Used for both exact-match and semantic cache. Upstash free tier is enough. |
| Document DB | **MongoDB 7** (Atlas free tier or self-hosted) | Leads, chat logs, eval runs, hire-flow sessions. Schema-flexible — fits evolving lead/chat shapes without migrations. Driver: **Motor** (async). |
| Email | **SMTP via `aiosmtplib`** | Async SMTP. Provider-agnostic — works with Gmail, Brevo, Mailgun, SendGrid SMTP, AWS SES, or self-hosted. No vendor lock-in; same approach as Nodemailer in Node land. |
| Eval | **DeepEval** | RAGAS-style metrics + custom G-Eval; runnable in CI. |
| Deploy | **Fly.io** or **Railway** | Docker-first, low ops, scale-to-zero on Fly. |
| Observability | **Loguru + OpenTelemetry → Axiom/Highlight** | Structured logs + traces. |

---

## 3. Knowledge Sources

| Source | Loader | Refresh cadence |
|---|---|---|
| `resume.pdf` | `pypdfium2` → text | Manual (rare) |
| Project descriptions (`data/projects.yaml`) | YAML | Manual |
| GitHub READMEs (`github.com/chetanmarathe`) | GitHub REST API | Daily cron |
| LinkedIn highlights (`data/linkedin.md`) | Markdown (manually copied; no scraping) | Manual |
| Naukri profile (`data/naukri.md`) | Markdown | Manual |
| LeetCode stats (`leetcode.com/chetanmarathe`) | LeetCode public GraphQL | Daily cron |
| FAQs / canned answers (`data/faqs.yaml`) | YAML | As needed |

**Why not scrape LinkedIn live?** ToS + brittle. Curated markdown gives full editorial control.

---

## 4. Architecture (one diagram, plain text)

```
                ┌─────────────────────┐
                │  React ChatPanel    │
                │  (Vite portfolio)   │
                └──────┬──────────────┘
                       │ SSE / fetch
        ┌──────────────▼────────────────┐
        │       FastAPI (Python)        │
        │  ┌─────────────────────────┐  │
        │  │  /chat   /hire  /health │  │
        │  └────┬─────────┬──────────┘  │
        │       │         │             │
        │  ┌────▼─────────▼──────────┐  │
        │  │  Cache layer (Redis)    │  │
        │  │  exact + semantic       │  │
        │  └────┬────────────────────┘  │
        │       │ miss                  │
        │  ┌────▼────────┐              │
        │  │ Retriever   │ MMR → Rerank │
        │  │ (Qdrant)    │              │
        │  └────┬────────┘              │
        │       │                       │
        │  ┌────▼────────┐              │
        │  │ Generator   │ OpenRouter   │
        │  │ (small→big) │              │
        │  └────┬────────┘              │
        │       │                       │
        │  ┌────▼────────┐              │
        │  │ Hire flow   │ → MongoDB    │
        │  │ controller →│   (leads)    │
        │  │ service →   │ → SMTP       │
        │  │ repository  │   aiosmtplib │
        │  │             │   (sends     │
        │  │             │   Chetan's   │
        │  │             │   details to │
        │  │             │   the lead)  │
        │  └─────────────┘              │
        └───────────────────────────────┘
```

---

## 5. Ingestion Pipeline

Run as a one-shot CLI: `python -m app.rag.ingest`.

**Steps:**
1. **Load** every source into `Document(text, metadata)` records.
2. **Chunk** with section-aware splitting:
   - Resume → one chunk per logical section (Experience-Schbang, Experience-CometChat, Education, Skills-Frontend, …).
   - Projects → one chunk per project (summary + stack + highlights).
   - GitHub READMEs → recursive split, 400 tokens / 60 overlap.
3. **Enrich metadata** — `source`, `kind` (`exp|project|skill|edu|faq`), `tags` (skills extracted via simple keyword pass), `year`, `company`.
4. **Embed** in batches of 100 with `text-embedding-3-small`.
5. **Upsert** into Qdrant collection `chet_kb` (cosine, 1536-dim).
6. **Snapshot** a content hash → if unchanged, skip re-embed (cost saver on cron runs).

Estimated corpus: ~120 chunks total. One-time embed cost: < $0.01.

---

## 6. Retrieval Pipeline (per query)

**Stages, in order:**

| # | Stage | Purpose | Cost |
|---|---|---|---|
| 1 | **Exact-match cache** (Redis) | Skip everything if seen | ~0 |
| 2 | **Semantic cache** (Redis vector, sim ≥ 0.93) | Skip if a near-duplicate was answered | 1 embed call |
| 3 | **Query rewrite** (cheap LLM, optional) | Resolve pronouns ("his projects" → "Chetan's projects"), expand acronyms | < 100 tokens via Haiku/Flash |
| 4 | **Embed query** | Vector input for retrieval | 1 embed call |
| 5 | **Vector search** (Qdrant, top-K=20) | Recall | local |
| 6 | **MMR** (λ=0.5, pick K=8 from 20) | Diversity, removes near-duplicates | local |
| 7 | **Cross-encoder rerank** (BGE-reranker-v2-m3, top-N=4) | Precision | ~30ms self-hosted |
| 8 | **Prompt assembly** | Compress system + 4 docs + question | local |
| 9 | **Generate** (small model first) | Stream answer | $$ |
| 10 | **Write to caches** | Both exact + semantic, TTL 24h | local |
| 11 | **Log + async DeepEval sample** (1% of traffic) | Quality drift detection | local |

**Hard token budget per call**
- 4 docs × ~350 tokens = 1400 doc tokens
- ~250 tokens system prompt
- ~50 tokens user message
- ~250 tokens output
- **Total: ~2000 in / 250 out → ~$0.0002 per uncached query on Gemini Flash via OpenRouter**

---

## 7. Caching Strategy

Two Redis namespaces, both with eviction LRU.

### 7.1 Exact-match cache
- Key: `chat:exact:{sha256(normalized_query + model)}`
- Value: JSON `{ answer, doc_ids, model, ts }`
- TTL: 7 days

### 7.2 Semantic cache (RediSearch vector index)
- Index: `idx:chat:sem` over hash entries `chat:sem:{uuid}` with fields `embedding` (1536-d FLOAT32), `query`, `answer`, `doc_ids`, `ts`.
- Lookup: KNN-1 with cosine sim threshold **≥ 0.93** (tuneable; lower → more hits but riskier).
- TTL: 24 hours (profile is stable; avoid stale answers if knowledge is updated).
- On hit: return cached answer immediately, log `cache=semantic`.

### 7.3 Why two layers
- Exact is essentially free and catches the long tail of identical questions.
- Semantic catches paraphrases ("what does Chetan do?" ≈ "tell me about him") — biggest cost killer.

---

## 8. Hire-Me Flow

A server-managed state machine. Frontend renders one question at a time so it feels conversational.

**Flow summary:** collect details → **persist lead to MongoDB first** → ask whether the visitor wants Chetan's details (resume PDF link + portfolio + contact info) emailed to them → if yes, send via Resend → confirm.

### 8.1 States
```
START → ASK_NAME → ASK_COMPANY → ASK_CONTACT → ASK_EMAIL → ASK_ADDRESS
      → ASK_MESSAGE (optional) → PERSIST_LEAD
      → ASK_SEND_DETAILS → (SEND_EMAIL) → DONE
```

`PERSIST_LEAD` is a non-interactive transition: the service writes the `leads` document the moment the last question is answered, **before** asking about email. This guarantees no lead is lost if the visitor abandons after the consent prompt.

### 8.2 API

```
POST /api/hire/start
  → 200 { session_id, question: "What's your name?", field: "name" }

POST /api/hire/answer
  body: { session_id, answer }
  → 200 { question, field }                              // next prompt
  →     { question: "Want me to email you Chetan's full details (resume, portfolio, contact)?",
          field: "send_details", choices: ["yes","no"],
          lead_id }                                       // lead already saved at this point
  →     { done: true, lead_id, emailed: true|false }
```

Note `lead_id` is returned **as soon as the lead is persisted** (right before the consent question), so the frontend can confirm capture even if the user closes the tab before answering.

### 8.3 Validation (server-side, per field)
- **name** — 2–80 chars, no URLs
- **company** — 1–120 chars
- **contact** — `phonenumbers` library, must parse to a valid E.164
- **email** — `email-validator` lib, MX record check
- **address** — free-form, 0–200 chars
- **message** — 0–600 chars
- **send_details** — must be exactly `"yes"` or `"no"` (case-insensitive)

On invalid input: return same `field` with `error` string; do not advance state.

### 8.4 Anti-spam
- Honeypot field (`website`) on the frontend; reject if filled.
- Rate-limit `/api/hire/*` to 5 req/min per IP via `slowapi`.
- Optional hCaptcha invisible token on `/start` if abuse appears.

### 8.5 Persistence (MongoDB)
- Collection: `leads` (see schema in §10).
- Every completed detail-collection writes a `leads` document **before** the email-consent question. The document is created with `emailed=false, send_details_choice=null`.
- After the user answers the consent question, the same document is updated with `send_details_choice` and (if email was sent) `emailed=true` + `email_msgid` + `emailed_at`.
- Hire-flow session state lives in a separate `hire_sessions` collection with a TTL index (`expires_at`, 24h) so abandoned half-filled sessions self-clean.

### 8.6 Email (SMTP via `aiosmtplib`) — sends Chetan's details TO the lead

Transport is plain SMTP, configurable per-environment. Any SMTP provider works
(Gmail, Brevo, Mailgun SMTP, AWS SES SMTP, self-hosted Postfix). Connection
modes supported: implicit TLS (port 465), STARTTLS (port 587), or plaintext
for local dev mailcatchers.

If `send_details=yes`:
- From: `${SMTP_FROM_NAME} <${SMTP_FROM}>` — must match an address authorised to send for the configured SMTP user.
- To: **the lead's email**
- Reply-To: `${CHETAN_EMAIL}` (so replies go to Chetan)
- Subject: `Chetan Marathe — details you requested`
- Body: multipart/alternative — plaintext + HTML, both rendered from Jinja templates. Contents:
  - Short intro line ("Hi {name}, here's what you asked for.")
  - **Resume PDF** — public link to `resume.pdf` hosted on the portfolio
  - **Portfolio URL** — `https://chetanmarathe.dev`
  - **Direct contact** — `${CHETAN_EMAIL}`, phone (only if Chetan opts in via env var `INCLUDE_PHONE_IN_EMAIL`)
  - LinkedIn / GitHub / LeetCode profile links
  - Footer: "You're receiving this because you requested Chetan's details on chetanmarathe.dev."
- A `Message-ID` header is generated locally (`<uuid@<from_domain>>`) and stored on the lead doc as `email_msgid`, alongside `emailed=true`, `emailed_at=now()`.
- Optional internal notification: a separate plaintext notification email goes to `${CHETAN_EMAIL}` ("New lead captured: {name} from {company}") regardless of consent — this is for Chetan's awareness, not the lead's email.

If `send_details=no`: lead is already saved; respond with "Got it — Chetan will reach out from {chetan_email}." Update doc with `send_details_choice="no"`.

**Provider-specific config crib:**
- **Gmail SMTP**: host `smtp.gmail.com`, port `587`, STARTTLS, username = your Google address, password = app password (requires 2FA on the account). ~500 sends/day cap.
- **Brevo (free)**: host `smtp-relay.brevo.com`, port `587`, STARTTLS. 300 emails/day free.
- **AWS SES SMTP**: host `email-smtp.<region>.amazonaws.com`, port `587`, STARTTLS, SMTP credentials generated in SES console. Cheapest at scale.
- **Mailcatcher (local dev)**: host `localhost`, port `1025`, no TLS. Use [`docker run mailhog/mailhog`](https://github.com/mailhog/MailHog) and view at `http://localhost:8025`.

### 8.7 UI integration
- ChatPanel always shows a **"Hire Me"** suggestion chip.
- Click → calls `/api/hire/start`, renders the question inline as a bot bubble + input.
- The chat retriever is paused while the hire flow is active to avoid the LLM accidentally answering the form prompts.
- After the consent question is answered, render the `done` confirmation with `emailed` state. If `emailed=true`: "Sent! Check {lead_email} in a minute." If `emailed=false`: "Saved — Chetan will be in touch."

### 8.8 Layered handler split (controller / service / repository)

| Layer | File | Responsibility |
|---|---|---|
| Controller | `app/modules/hire/controller.py` | FastAPI route handlers. Parse/validate request body (Pydantic), pull request metadata (IP, UA), call service, shape HTTP response, attach trace IDs. **No business logic, no DB calls.** |
| Service | `app/modules/hire/service.py` | State machine, validation orchestration, decides when to persist, when to send email. Calls repository + email client. **No HTTP types, no DB driver imports.** |
| Repository | `app/modules/hire/repository.py` | Motor calls only — `insert_lead`, `update_lead_email_status`, `get_session`, `upsert_session`. Returns plain dicts / domain models. |
| Email client | `app/modules/hire/email_client.py` | SMTP wrapper using `aiosmtplib`. Pure I/O — composes a multipart MIME message from a template, opens an async SMTP connection, sends, returns the generated `Message-ID`. |
| Schemas | `app/modules/hire/schemas.py` | Pydantic request/response models + the `Lead` and `HireSession` domain models. |

---

## 9. LLM Routing (cost-tiered)

| Tier | Model (via OpenRouter) | Use case |
|---|---|---|
| **cheap** | `google/gemini-2.5-flash` | Default for chat, query rewrite |
| **strong** | `anthropic/claude-haiku-4.5` | Fallback on low-confidence outputs (escalation rule below) |
| **embed** | `openai/text-embedding-3-small` | Embeddings |

**Escalation rule:** if cheap model output triggers any of:
- Self-reported uncertainty heuristic ("I'm not sure", "no info")
- DeepEval faithfulness sample < 0.7
- Response length < 30 chars on a non-trivial question

→ retry once with **strong** tier. Log both. Stop there.

Set `OPENROUTER_PROVIDER_PREFERENCES` to keep generations on a single provider per session for consistency.

---

## 10. Database Schema (MongoDB, Motor + Pydantic)

Driver: **Motor** (async PyMongo). Models: **Pydantic v2** in `app/modules/<module>/schemas.py`. No ODM (Beanie/ODMantic) — repositories own all `motor` calls and return plain Pydantic models. Keeps the layering honest.

### 10.1 Collection: `leads`

```jsonc
{
  "_id": ObjectId,
  "name": "string (2-80)",
  "company": "string (1-120)",
  "contact": "string (E.164)",
  "email": "string (validated)",
  "address": "string | null",
  "message": "string | null",

  // email-consent + delivery state
  "send_details_choice": "yes | no | null",   // null = lead saved, consent not yet asked
  "emailed": false,                            // true once Resend accepts the send
  "email_msgid": "string | null",              // Resend message id
  "emailed_at": "ISODate | null",

  // request metadata (captured by controller)
  "source": "chat | dock | terminal:sudo-hire",
  "ip": "string",
  "user_agent": "string",

  "created_at": "ISODate",
  "updated_at": "ISODate"
}
```

Indexes:
- `{ email: 1 }` — non-unique, for lookup
- `{ created_at: -1 }` — recent-leads query
- `{ "send_details_choice": 1, emailed: 1 }` — for finding consented-but-not-yet-emailed (retry job)

### 10.2 Collection: `hire_sessions` (transient)

```jsonc
{
  "_id": "uuid",                    // = session_id returned to client
  "state": "ASK_NAME | ASK_COMPANY | ... | DONE",
  "answers": { "name": "...", "company": "...", ... },
  "lead_id": "ObjectId | null",     // set after PERSIST_LEAD transition
  "ip": "string",
  "user_agent": "string",
  "created_at": "ISODate",
  "expires_at": "ISODate"           // TTL index → auto-cleanup of abandoned sessions
}
```

Indexes:
- `{ expires_at: 1 }` with `expireAfterSeconds: 0` — TTL cleanup

### 10.3 Collection: `chat_logs`

```jsonc
{
  "_id": ObjectId,
  "session_id": "uuid",
  "role": "user | assistant",
  "content": "string",
  "retrieved_ids": ["doc_id", ...],
  "model": "string",
  "tokens_in": 0,
  "tokens_out": 0,
  "latency_ms": 0,
  "cache": "null | exact | semantic",
  "created_at": "ISODate"
}
```

Indexes: `{ session_id: 1, created_at: 1 }`, `{ created_at: -1 }`.

### 10.4 Collection: `eval_runs`

```jsonc
{
  "_id": ObjectId,
  "query": "string",
  "answer": "string",
  "faithfulness": 0.0,
  "answer_relev": 0.0,
  "context_recall": 0.0,
  "tone_match": 0.0,
  "model": "string",
  "created_at": "ISODate"
}
```

Indexes: `{ created_at: -1 }`, `{ model: 1, created_at: -1 }`.

### 10.5 Request-tracking collection: `request_logs` (optional, for controller observability)

Written by a controller-level middleware on every `/api/*` call. Useful for the layered structure since controllers are the place where request tracking belongs.

```jsonc
{
  "_id": ObjectId,
  "request_id": "uuid",
  "route": "/api/hire/answer",
  "method": "POST",
  "status": 200,
  "latency_ms": 47,
  "ip": "string",
  "user_agent": "string",
  "session_id": "uuid | null",
  "error": "string | null",
  "created_at": "ISODate"
}
```

TTL index on `created_at` (30 days).

---

## 11. Project Layout

Module-per-feature. Each module is a self-contained vertical slice with the same internal shape: **controller → service → repository → schemas**. Cross-cutting concerns (LLM client, RAG retriever, cache, Mongo client) live in `core/` and are injected via FastAPI `Depends`.

```
chet-ai-backend/
├── app/
│   ├── main.py                       # FastAPI app, CORS, mount routers, exception handlers
│   ├── config.py                     # pydantic-settings (env)
│   ├── deps.py                       # DI providers: get_mongo, get_redis, get_qdrant, get_llm
│   │
│   ├── core/                         # Cross-cutting infrastructure (no business logic)
│   │   ├── mongo.py                  # Motor client + index bootstrapping on startup
│   │   ├── redis.py                  # Redis client
│   │   ├── qdrant.py                 # Qdrant client
│   │   ├── logging.py                # Loguru config + request_id contextvar
│   │   └── middleware.py             # Request-tracking middleware (writes request_logs)
│   │
│   ├── modules/                      # One folder per domain feature
│   │   │
│   │   ├── chat/
│   │   │   ├── controller.py         # POST /api/chat (SSE) — request parsing, streaming response
│   │   │   ├── service.py            # Orchestrates: cache check → retrieve → generate → log
│   │   │   ├── repository.py         # chat_logs Motor calls
│   │   │   └── schemas.py            # ChatRequest, ChatStreamEvent, ChatLog
│   │   │
│   │   ├── hire/
│   │   │   ├── controller.py         # POST /api/hire/start, /api/hire/answer
│   │   │   ├── service.py            # State machine + persist-then-ask-consent flow
│   │   │   ├── repository.py         # leads + hire_sessions Motor calls
│   │   │   ├── email_client.py       # Resend wrapper (sends Chetan's details to lead)
│   │   │   ├── validators.py         # phone (E.164), email (MX), length checks
│   │   │   ├── templates/
│   │   │   │   ├── chetan_details.html
│   │   │   │   └── chetan_details.txt
│   │   │   └── schemas.py            # HireStartRequest, HireAnswerRequest, Lead, HireSession
│   │   │
│   │   ├── eval/
│   │   │   ├── controller.py         # (optional) POST /api/eval/run — admin only
│   │   │   ├── service.py            # DeepEval orchestration
│   │   │   ├── repository.py         # eval_runs Motor calls
│   │   │   ├── metrics.py            # DeepEval metric defs
│   │   │   ├── dataset.json          # 50 gold Q&A pairs
│   │   │   └── run_eval.py           # CI entrypoint (calls service)
│   │   │
│   │   └── health/
│   │       ├── controller.py         # GET /api/health
│   │       └── service.py            # Pings Mongo / Redis / Qdrant / OpenRouter
│   │
│   ├── rag/                          # RAG primitives — used by chat/service.py
│   │   ├── ingest.py                 # CLI ingestion (python -m app.rag.ingest)
│   │   ├── chunker.py                # Section-aware splitter
│   │   ├── embeddings.py             # Batched embed via OpenRouter
│   │   ├── retriever.py              # Qdrant + MMR + rerank
│   │   ├── reranker.py               # BGE cross-encoder client
│   │   └── prompt.py                 # System prompt builder
│   │
│   ├── cache/                        # Cache layer — used by chat/service.py
│   │   ├── exact.py
│   │   └── semantic.py               # RediSearch vector ops
│   │
│   └── llm/
│       ├── openrouter.py             # Async client w/ retry
│       ├── routing.py                # cheap/strong escalation
│       └── prompts/
│           ├── system.md
│           └── rewrite.md
│
├── data/
│   ├── resume.pdf
│   ├── projects.yaml
│   ├── linkedin.md
│   ├── naukri.md
│   └── faqs.yaml
│
├── scripts/
│   ├── ingest.sh
│   ├── eval.sh
│   └── seed_indexes.py               # Idempotent Mongo index creation
│
├── tests/
│   ├── modules/
│   │   ├── hire/
│   │   │   ├── test_controller.py    # FastAPI TestClient — request/response shape
│   │   │   ├── test_service.py       # State machine logic with mocked repo + email
│   │   │   ├── test_repository.py    # Against real Mongo (testcontainers)
│   │   │   └── test_validators.py
│   │   └── chat/
│   │       └── test_service.py
│   └── rag/
│       └── test_retriever.py
│
├── docker-compose.yml                # mongo, redis, qdrant for local
├── Dockerfile
├── pyproject.toml
├── .env.example
└── README.md
```

### 11.1 Layer responsibilities (rules)

| Layer | May import | May NOT import |
|---|---|---|
| **Controller** | service, schemas, FastAPI, deps | repository, motor, resend, qdrant directly |
| **Service** | repository, schemas, core clients (via DI), other services | FastAPI types (`Request`, `HTTPException`), motor |
| **Repository** | motor, schemas | service, controller, FastAPI |
| **Schemas** | pydantic, stdlib | everything else |

Lint enforced via `import-linter` contracts in `pyproject.toml` so violations fail CI.

### 11.2 Request flow example (POST /api/hire/answer)

1. **Middleware** (`core/middleware.py`) generates `request_id`, captures start time, IP, UA.
2. **Controller** (`hire/controller.py`) validates body via `HireAnswerRequest`, pulls request metadata, calls `HireService.handle_answer(session_id, answer, meta)`.
3. **Service** (`hire/service.py`) loads session via `HireRepository.get_session`, validates the field, advances state. On `PERSIST_LEAD` transition: calls `HireRepository.insert_lead`. On `send_details=yes`: calls `EmailClient.send_chetan_details(lead)` (which opens an SMTP connection via `aiosmtplib` and returns a Message-ID), then `HireRepository.mark_emailed(lead_id, message_id)`.
4. **Repository** (`hire/repository.py`) executes Motor ops, returns Pydantic models.
5. **Controller** shapes the next `{question, field}` (or `{done, lead_id, emailed}`) response.
6. **Middleware** writes the `request_logs` entry with final status + latency.

---

## 12. System Prompt (concise, token-cheap)

```
You are CHET.ai — a precise assistant for Chetan Marathe (AI Full Stack Engineer, Mumbai).
Answer ONLY from the CONTEXT below. If the context lacks the fact, say
"I don't have that on file — best to ask Chetan directly: chetanmarathe0412@gmail.com".

Style: 1–3 short sentences, conversational, confident, no marketing fluff.
Never invent employers, dates, metrics, or links. No emojis.

CONTEXT:
{retrieved_chunks}

QUESTION: {query}
```

Total: ~180 tokens before the chunks. Versioned in `app/llm/prompts/system.md`.

---

## 13. DeepEval Setup

### 13.1 Metrics
- `FaithfulnessMetric` — answer must be supported by retrieved context.
- `AnswerRelevancyMetric` — answer addresses the question.
- `ContextualRecallMetric` — gold answer is reachable from the retrieved context.
- `GEval` (custom) — **Tone match**: "Reads like a confident senior engineer talking about himself in third person; no marketing fluff."

### 13.2 Dataset
- 50 hand-written Q&A pairs in `app/eval/dataset.json` covering:
  - Bio/about (5)
  - Each project (3 each × 6 projects = 18)
  - Each role (3 each × 2 roles = 6)
  - Skills probes (8)
  - Edge cases / out-of-scope ("what's his salary?") (8)
  - Hire/contact intent (5)

### 13.3 CI gate
- GitHub Actions: run `pytest -m eval` on PRs touching `app/rag/**` or `data/**`.
- Block merge if **avg faithfulness < 0.85** or **answer_relev < 0.80** vs. baseline.

### 13.4 Production sampling
- 1% of live queries → background DeepEval run → store in `eval_runs`.
- Daily Slack/email digest with mean scores + 5 worst examples.

---

## 14. Endpoints

### `POST /api/chat`
**Body:** `{ session_id: uuid, message: string }`
**Response:** SSE stream
- `event: meta` — `{ cache: null|"exact"|"semantic", retrieved_ids, model }`
- `event: token` — `{ delta: string }` (streamed tokens)
- `event: done` — `{ latency_ms, tokens_in, tokens_out }`

### `POST /api/hire/start`
**Body:** `{ source: "dock"|"chat"|"terminal:sudo-hire" }`
**Response:** `{ session_id, question, field }`

### `POST /api/hire/answer`
**Body:** `{ session_id, answer }`
**Response:** either `{ question, field }`, `{ field, error }`, or `{ done: true, lead_id, emailed }`

### `GET /api/health`
Liveness + dependency checks (Redis, Qdrant, MongoDB, OpenRouter ping).

---

## 15. Frontend Wiring (changes to `src/components/ChatPanel.jsx`)

1. Replace `localAnswer()` heuristics + `window.claude` call with a single `fetch('/api/chat', { ... })` SSE consumer.
2. Add a **"Hire Me"** chip to the existing suggestions (always visible). On click → switch panel into "hire flow" mode (calls `/api/hire/*`, renders question-by-question).
3. While in hire mode: disable free-form chat, show a small "✕ cancel" link to bail back to RAG mode.
4. On `done` from hire flow: render confirmation with `emailed` state, return to RAG mode after 4s.
5. Persist `session_id` in `sessionStorage` so refresh doesn't break the flow.

---

## 16. Local Dev (Docker Compose)

```yaml
services:
  mongo:
    image: mongo:7
    environment:
      MONGO_INITDB_ROOT_USERNAME: dev
      MONGO_INITDB_ROOT_PASSWORD: dev
    ports: ["27017:27017"]
    volumes: ["mongo_data:/data/db"]
  redis:
    image: redis/redis-stack:7.4.0-v0    # includes RediSearch
    ports: ["6379:6379", "8001:8001"]
  qdrant:
    image: qdrant/qdrant:v1.11.0
    ports: ["6333:6333"]
  api:
    build: .
    env_file: .env
    depends_on: [mongo, redis, qdrant]
    ports: ["8000:8000"]
    command: uvicorn app.main:app --reload --host 0.0.0.0

volumes:
  mongo_data:
```

`.env.example` keys: `OPENROUTER_API_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, `SMTP_USE_STARTTLS`, `SMTP_FROM`, `SMTP_FROM_NAME`, `MONGODB_URI`, `MONGODB_DB=chet_ai`, `REDIS_URL`, `QDRANT_URL`, `QDRANT_API_KEY`, `EVAL_SAMPLE_RATE=0.01`, `CORS_ORIGINS`, `CHETAN_EMAIL=chetanmarathe0412@gmail.com`, `CHETAN_RESUME_URL`, `CHETAN_PORTFOLIO_URL`, `INCLUDE_PHONE_IN_EMAIL=false`.

---

## 17. Phased Build (≈ 2 weeks part-time)

| Phase | Deliverable | Time |
|---|---|---|
| 0 | Repo, Docker, FastAPI hello-world, env wiring | 0.5 d |
| 1 | Ingestion CLI + Qdrant indexing of resume + projects YAML | 1.5 d |
| 2 | Retriever with MMR + reranker + basic generation | 2 d |
| 3 | OpenRouter client w/ cheap→strong escalation | 1 d |
| 4 | Redis exact + semantic caches | 1 d |
| 5 | Hire-me state machine (controller/service/repo split) + persist-then-consent flow + Resend email of Chetan's details to lead + MongoDB | 2 d |
| 6 | DeepEval dataset + metrics + CI gate | 1.5 d |
| 7 | Frontend integration in `ChatPanel` | 1 d |
| 8 | Deploy to Fly.io + smoke tests + observability | 1 d |
| 9 | Polish: rate limiting, logging, docs, prod cutover | 0.5 d |

---

## 18. Cost Model (1k conversations / month)

Assumes ~70% cache hit rate after a week (semantic cache is the workhorse).

| Item | Volume | Unit | Total |
|---|---|---|---|
| Embedding (queries) | 1k × 1 call | $0.02 / 1M tok | < $0.01 |
| Generation (Gemini Flash via OpenRouter) | 300 cold × 2k in / 250 out | $0.075 in / $0.30 out / 1M | ~$0.07 |
| Reranker (self-hosted BGE) | included | — | $0 |
| Qdrant Cloud | free tier | — | $0 |
| Upstash Redis | free tier | — | $0 |
| MongoDB Atlas | free tier (M0, 512MB) | — | $0 |
| Resend | < 100 emails | free up to 3k | $0 |
| Fly.io shared CPU | 256MB scale-to-zero | — | ~$0–2 |

**Total: < $3/month** at this volume. Headroom is huge.

---

## 19. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LLM hallucinates an employer or year | Strict system prompt + DeepEval faithfulness gate + low temperature (0.2). |
| Semantic cache returns subtly wrong answer | Conservative similarity threshold (0.93); a/b lower it only after monitoring. |
| Lead form spammed | Honeypot + slowapi rate limit + optional hCaptcha if abuse seen. |
| OpenRouter outage | Multi-provider routing in OpenRouter; cheap→strong fallback already retries. |
| Resume changes, embeddings stale | Ingestion is idempotent; CI re-runs on `data/**` changes; eval gate catches drift. |
| Cost spikes | Hard token budget per call + per-IP daily cap (e.g., 30 messages/IP/day). |

---

## 20. Open Decisions (call before coding)

1. **Reranker**: self-hosted BGE (free, +sidecar) vs Cohere Rerank ($1/1k, no infra).
2. **Email domain**: register / verify a domain on Resend (e.g. `mail.chetanmarathe.dev`) before phase 5 — DNS propagation costs a day. **Critical now** since the email goes to the *lead* (deliverability + reputation matter more than when it was just to Chetan's own inbox).
3. **Hire-flow UX**: question-at-a-time bubbles (recommended) vs single inline form. Bubbles match the chat aesthetic; form is faster for the user.
4. **Source attribution in answers**: include `[exp/schbang]` style citations? Useful for trust, slightly more tokens. Recommend yes for v1.
5. **Internal notification**: also send Chetan a "new lead captured" email on every completion (separate from the lead-facing details email)? Recommend yes — it's the only realtime signal he gets.
6. **Phone in lead-facing email**: include Chetan's phone number? Default `INCLUDE_PHONE_IN_EMAIL=false`; flip when comfortable.

Decide these → start phase 0.
