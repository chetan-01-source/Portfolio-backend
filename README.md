# CHET.ai — Backend

RAG-powered personal assistant for Chetan Marathe's portfolio. Implements
[plan.md](plan.md): MongoDB persistence, controller → service → repository
layering, hire-flow that emails Chetan's details to the lead.

## Stack

FastAPI · MongoDB (Motor) · Redis 7 (RediSearch) · Qdrant · OpenRouter · fastapi-mail · DeepEval

## Layout

Module-per-feature under `app/modules/<feature>/`. Each module has the same
shape: `controller.py` → `service.py` → `repository.py` → `schemas.py`.
Cross-cutting infra (Mongo, Redis, Qdrant clients; logging; request-tracking
middleware) lives in `app/core/`. RAG primitives are shared across modules
under `app/rag/`. See [plan.md §11](plan.md) for the layer-import rules.

## Quick start (Docker)

```bash
cp .env.example .env
# Add OPENROUTER_API_KEY. For real emails, add Gmail SMTP app-password settings.

docker compose up -d
docker compose exec api python -m app.rag.ingest
curl http://localhost:8000/api/health
```

## Local (without Docker)

Requires Python 3.11+, plus a Mongo + Redis (with RediSearch) + Qdrant
reachable from the URLs in `.env`.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

python scripts/seed_indexes.py            # creates Mongo indexes + Qdrant collection
python -m app.rag.ingest                  # builds the knowledge base
uvicorn app.main:app --reload
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/chat` | RAG chat — SSE stream (`meta`, `token`, `done`) |
| POST | `/api/hire/start` | Begin hire-flow, returns first question |
| POST | `/api/hire/answer` | Submit one answer, returns next question or `done` |
| GET | `/api/health` | Pings Mongo / Redis / Qdrant / OpenRouter |
| GET | `/api/eval/means` | Recent eval score means |
| GET | `/api/ingest/sources` | Summarize what sources are currently embedded in Qdrant |
| POST | `/api/ingest/resume` | Upload and embed a resume PDF into Qdrant |
| POST | `/api/ingest/urls` | Crawl URLs and embed extracted text into Qdrant |

OpenAPI: `http://localhost:8000/docs`
Detailed API docs: [docs/api-collection.md](docs/api-collection.md)
Implementation guide: [docs/api-implementation-guide.md](docs/api-implementation-guide.md)
Postman collection: [docs/postman/CHET-ai.postman_collection.json](docs/postman/CHET-ai.postman_collection.json)

## Hire-flow behaviour (key spec)

The lead document is written to Mongo **before** the consent question, so an
abandoned consent doesn't lose the lead. After the user answers
`send_details=yes`, `EmailClient.send_chetan_details(lead)` mails Chetan's
resume PDF + portfolio + contact details to the lead's address, and the lead
doc is updated with `emailed=true`, `email_msgid`, `emailed_at`.
`send_details=no` just records the choice. If the lead's freeform message
explicitly asks to send/share the resume, portfolio, or details, that message
is treated as consent and the details email is sent immediately. See
[plan.md §8](plan.md#8-hire-me-flow).

## Gmail SMTP With fastapi-mail

Email delivery runs inside FastAPI with `fastapi-mail` and Gmail SMTP over
STARTTLS. There is no separate mail sidecar.

To configure real Gmail delivery:

1. Enable 2-step verification on the Gmail account.
2. Create an app password in Google Account security settings.
3. Set `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`, `MAIL_PORT=587`,
   `MAIL_SERVER=smtp.gmail.com`, `MAIL_FROM_NAME`, `MAIL_STARTTLS=true`,
   `MAIL_SSL_TLS=false`, `USE_CREDENTIALS=true`, and `VALIDATE_CERTS=true`
   in `.env`.
4. Use the generated Gmail app password for `MAIL_PASSWORD`; do not use the
   normal Gmail login password.

## Tests

```bash
pytest                          # unit tests (mongomock-motor for Mongo)
pytest -m eval                  # DeepEval gate (requires [eval] extras and live LLM)
```

Service-layer tests verify the persist-then-consent ordering with an in-memory
Mongo and a stubbed `EmailClient` — no live infra required.

## Config knobs (see `.env.example` for the full list)

- `LLM_CHEAP_MODEL` / `LLM_STRONG_MODEL` — OpenRouter routing tiers (plan §9)
- `SEMANTIC_CACHE_THRESHOLD=0.93` — KNN cosine similarity floor for semantic cache
- `RERANKER_URL` — set to a BGE sidecar's URL to enable cross-encoder rerank; empty disables
- `INCLUDE_PHONE_IN_EMAIL=false` — whether the lead-facing email shows Chetan's phone
- `CHETAN_RESUME_ATTACHMENT_PATH=data/resume.pdf` — local resume PDF attached to the lead-facing details email; leave empty to send links only
- `NOTIFY_CHETAN_ON_LEAD=true` — sends a separate internal notification on every capture

## Layering rules (enforced by import-linter)

| Layer | May import | May NOT import |
|---|---|---|
| Controller | service, schemas, FastAPI, deps | repository, motor |
| Service | repository, schemas, core clients | FastAPI types, motor |
| Repository | motor, schemas | service, controller, FastAPI |

Run `lint-imports` to verify.
