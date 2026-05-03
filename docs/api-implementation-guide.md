# CHET.ai API Implementation Guide

This guide explains every public backend API and how the request moves through the codebase.

Base URL for local development: `http://localhost:8000`

## Architecture

The app is a FastAPI backend with feature modules under `app/modules`.

Request flow:

```text
HTTP request
  -> FastAPI controller
  -> service
  -> repository or infrastructure client
  -> MongoDB / Redis / Qdrant / OpenRouter / Gmail SMTP
  -> response
```

Layer responsibilities:

| Layer | Responsibility |
|---|---|
| Controller | Defines route, validates request body with Pydantic, reads dependencies, maps domain errors to HTTP errors. |
| Service | Owns business flow, state transitions, cache/retrieval orchestration, and email decisions. |
| Repository | Owns MongoDB reads/writes only. |
| Infrastructure clients | Wrap Redis, Qdrant, OpenRouter, SMTP, logging, and request metadata. |

The backend adds request tracking middleware, so controllers can read client IP, user-agent, and request id from `request.state`.

## `GET /api/health`

Purpose: return dependency health for MongoDB, Redis, Qdrant, and OpenRouter.

Query params: none.

Body: none.

Example:

```bash
curl http://localhost:8000/api/health
```

Response:

```json
{
  "ok": true,
  "deps": {
    "mongo": { "ok": true },
    "redis": { "ok": true },
    "qdrant": { "ok": true },
    "openrouter": { "ok": true }
  }
}
```

Under the hood:

1. `app/modules/health/controller.py` receives the request.
2. Dependencies inject Mongo, Redis, Qdrant, and settings.
3. `HealthService.status()` runs checks concurrently with `asyncio.gather`.
4. Mongo runs `ping`.
5. Redis runs `ping`.
6. Qdrant calls `get_collections()`.
7. OpenRouter calls `/models` only when `OPENROUTER_API_KEY` is configured.
8. The endpoint returns `ok=true` only if all dependency checks pass.

## `POST /api/chat`

Purpose: stream a RAG answer as server-sent events.

Query params: none.

Body:

```json
{
  "session_id": "demo-session",
  "message": "Tell me about Chetan's projects"
}
```

Validation:

| Field | Required | Rules |
|---|---:|---|
| `session_id` | yes | string |
| `message` | yes | string, 1 to 2000 chars |

Example:

```bash
curl -N http://localhost:8000/api/chat \
  -H "content-type: application/json" \
  -d '{"session_id":"demo-session","message":"Tell me about Chetan projects"}'
```

Response type: `text/event-stream`

Events:

```text
event: meta
data: {"cache":null,"retrieved_ids":["..."],"model":"google/gemini-2.5-flash"}

event: token
data: {"delta":"..."}

event: done
data: {"latency_ms":1234,"tokens_in":0,"tokens_out":0}
```

Error event:

```text
event: error
data: {"message":"embedding failed"}
```

Under the hood:

1. `app/modules/chat/controller.py` builds `ChatService`.
2. The service logs the user message to Mongo through `ChatRepository`.
3. It checks the exact Redis cache using the raw message and selected model.
4. If exact cache hits, it streams cached answer and writes assistant log with `cache="exact"`.
5. If exact cache misses, it embeds the query using OpenRouter embeddings.
6. It checks semantic cache with the query vector.
7. If semantic cache hits, it streams cached answer and writes assistant log with `cache="semantic"`.
8. If semantic cache misses, it searches Qdrant through the retriever.
9. Retrieved documents are converted into prompt messages.
10. OpenRouter streams model tokens.
11. The controller converts each service chunk into SSE events.
12. Final answer is logged to Mongo.
13. Exact and semantic caches are written for future requests.

Important behavior:

- The stream can return cached answers without calling Qdrant or generation.
- If embedding fails, the endpoint emits an `error` event and stops.
- If generation fails, the endpoint emits an `error` event and stops.
- Cache write failures are logged but do not fail the response.

## `POST /api/hire/start`

Purpose: start the Hire Me lead-capture flow.

Query params: none.

Body:

```json
{
  "source": "chat",
  "website": null
}
```

Validation:

| Field | Required | Rules |
|---|---:|---|
| `source` | no | one of `chat`, `dock`, `terminal:sudo-hire`; defaults to `chat` |
| `website` | no | honeypot field; must be empty |

Example:

```bash
curl http://localhost:8000/api/hire/start \
  -H "content-type: application/json" \
  -d '{"source":"chat","website":null}'
```

Response:

```json
{
  "session_id": "uuid",
  "question": "What's your name?",
  "field": "name",
  "choices": null,
  "error": null,
  "lead_id": null
}
```

Errors:

| Status | Cause |
|---:|---|
| `400` | Honeypot field was filled. |

Under the hood:

1. `app/modules/hire/controller.py` receives the request.
2. The request metadata dependency provides IP and user-agent.
3. `HireService.start()` rejects the request if `website` is filled.
4. A UUID session id is created.
5. `HireRepository.create_session()` stores a Mongo `hire_sessions` document.
6. The source is stored as an internal `_source` answer on the session.
7. The session state becomes `ASK_NAME`.
8. The API returns the first question.

## `POST /api/hire/answer`

Purpose: submit one answer for the current Hire Me flow field.

Query params: none.

Body:

```json
{
  "session_id": "uuid-from-start",
  "answer": "Asha Kumar",
  "website": null
}
```

Validation:

| Field | Required | Rules |
|---|---:|---|
| `session_id` | yes | existing active hire session id |
| `answer` | yes | string, max 600 chars |
| `website` | no | honeypot field; must be empty |

Flow order:

```text
name -> company -> contact -> email -> address -> message -> send_details -> done
```

Field validation:

| Field | Rules |
|---|---|
| `name` | Must be a realistic non-empty name. |
| `company` | Must be non-empty. |
| `contact` | Must be a valid phone number, preferably with country code. |
| `email` | Validated by `email-validator` with deliverability check. |
| `address` | Optional. |
| `message` | Optional. |
| `send_details` | Must be `yes` or `no`. |

Next-question response:

```json
{
  "session_id": "uuid",
  "question": "Which company are you with?",
  "field": "company",
  "lead_id": null
}
```

Validation-error response:

```json
{
  "session_id": "uuid",
  "question": "What's your email?",
  "field": "email",
  "error": "Enter a valid email address.",
  "lead_id": null
}
```

Consent question response:

```json
{
  "session_id": "uuid",
  "question": "Want me to email you Chetan's full details (resume, portfolio, contact)?",
  "field": "send_details",
  "choices": ["yes", "no"],
  "lead_id": "mongodb_object_id"
}
```

Done response:

```json
{
  "done": true,
  "session_id": "uuid",
  "lead_id": "mongodb_object_id",
  "emailed": true
}
```

Errors:

| Status | Cause |
|---:|---|
| `400` | Honeypot field was filled. |
| `404` | Session was not found or expired. |

Under the hood:

1. The controller builds `HireService` with `HireRepository`, `EmailClient`, and settings.
2. `HireService.handle_answer()` loads the session from Mongo.
3. The current session state decides which field is being answered.
4. The answer is validated by `app/modules/hire/validators.py`.
5. Invalid answers return the same question with an `error`; the session does not advance.
6. Valid answers are stored into `session.answers`.
7. For ordinary fields, the service advances to the next state and updates Mongo.
8. After `message` is answered, the service persists the lead before asking consent.
9. `HireRepository.insert_lead()` writes a `leads` document with `emailed=false` and `send_details_choice=null`.
10. If the freeform message explicitly asks to send/share the resume, portfolio, contact, or details, that message is treated as consent and the service sends the details email immediately.
11. Otherwise, the service optionally sends Chetan an internal notification email.
12. The API asks `send_details` and includes the created `lead_id`.
13. If user answers `no`, consent is saved and flow ends with `emailed=false`.
14. If user answers `yes`, the service loads the lead and calls `EmailClient.send_chetan_details()`.
15. If SMTP succeeds, `mark_emailed()` sets `emailed=true`, `email_msgid`, and `emailed_at`.
16. If SMTP fails, the exception is logged; the lead remains saved and final response has `emailed=false`.

Why the lead is saved before consent:

- If a visitor abandons after the message field, Chetan still has the lead.
- Email delivery is not allowed to control lead persistence.
- The consent answer only controls whether Chetan’s details are emailed to the lead.

## Email Implementation With fastapi-mail

Email runs inside FastAPI in `app/modules/hire/email_client.py`.

Config:

```env
MAIL_USERNAME=your-gmail-address@gmail.com
MAIL_PASSWORD=your-gmail-app-password
MAIL_FROM=your-gmail-address@gmail.com
MAIL_PORT=587
MAIL_SERVER=smtp.gmail.com
MAIL_FROM_NAME=CHET.ai
MAIL_STARTTLS=true
MAIL_SSL_TLS=false
USE_CREDENTIALS=true
VALIDATE_CERTS=true
CHETAN_RESUME_ATTACHMENT_PATH=data/resume.pdf
```

Where environment values come from:

| Variable | Value |
|---|---|
| `MAIL_USERNAME` | Your Gmail address. |
| `MAIL_PASSWORD` | Gmail app password generated after enabling 2-step verification. |
| `MAIL_FROM` | Usually the same Gmail address as `MAIL_USERNAME`. |
| `MAIL_PORT` | `587` for Gmail STARTTLS. |
| `MAIL_SERVER` | `smtp.gmail.com`. |
| `MAIL_FROM_NAME` | Display name, for example `CHET.ai`. |
| `MAIL_STARTTLS` | `true` for port `587`. |
| `MAIL_SSL_TLS` | `false` for port `587`; use SSL/TLS only for port `465`. |
| `USE_CREDENTIALS` | `true` for Gmail login. |
| `VALIDATE_CERTS` | `true` to validate TLS certificates. |
| `CHETAN_RESUME_ATTACHMENT_PATH` | Local PDF path attached to the lead-facing details email; leave empty to send links only. |

How sending works:

1. FastAPI renders `chetan_details.html` and `chetan_details.txt` with Jinja.
2. `EmailClient` builds a `fastapi-mail.ConnectionConfig` from `MAIL_*` settings.
3. `MessageSchema` creates the email payload.
4. Lead-facing details email uses `MessageType.html` with a plain-text `alternative_body` and attaches the local resume PDF when configured.
5. Internal notification email uses `MessageType.plain`.
6. `FastMail.send_message()` sends through Gmail SMTP.
7. A generated tracking ID is added as `X-CHET-Message-ID`, returned, and stored on the lead after success.

Missing SMTP config behavior:

- `send_chetan_details()` returns `stub-no-mail-config`.
- This lets local/dev flows finish without real email credentials.
- Notification email is skipped when mail config is missing.

Gmail setup:

1. Enable 2-step verification on the Gmail account.
2. Create a Gmail app password.
3. Use the app password for `MAIL_PASSWORD`.
4. Do not use the normal Gmail login password.

## `GET /api/eval/means`

Purpose: inspect recent eval metric means.

Query params:

| Name | Required | Default | Description |
|---|---:|---:|---|
| `limit` | no | `100` | Number of latest eval runs to include. |

Body: none.

Example:

```bash
curl "http://localhost:8000/api/eval/means?limit=25"
```

Response:

```json
{
  "means": {
    "faithfulness": 0.93,
    "answer_relev": 0.91,
    "context_recall": 0.88,
    "tone_match": 0.95
  },
  "limit": 25
}
```

Under the hood:

1. `app/modules/eval/controller.py` reads the optional `limit` query param.
2. `EvalRepository.recent_means()` fetches recent `eval_runs` from Mongo.
3. It calculates averages for non-null metric values.
4. If there are no eval runs, `means` is `{}`.

## Data Stores

MongoDB collections:

| Collection | Used by | Purpose |
|---|---|---|
| `chat_logs` | Chat | Stores user and assistant turns. |
| `hire_sessions` | Hire | Tracks current field, answers, lead id, expiration. |
| `leads` | Hire | Stores completed lead details and email status. |
| `eval_runs` | Eval | Stores evaluation metric rows. |

Redis:

- Exact answer cache.
- Semantic cache metadata and vectors through Redis Stack / RediSearch.

Qdrant:

- Stores knowledge-base chunks and vectors for RAG retrieval.
- Receives embedded chunks from static ingest, resume uploads, and URL crawling.

OpenRouter:

- Embeddings for user queries.
- Streaming chat generation.

Gmail SMTP:

- Sends lead-facing details email.
- Sends internal notification email to Chetan when enabled.

## Operational Notes

- OpenAPI docs are available at `/docs`.
- Importable Postman collection is in `docs/postman/CHET-ai.postman_collection.json`.
- The detailed request/response collection is in `docs/api-collection.md`.
- Unit tests avoid live infrastructure by mocking Mongo or SMTP where needed.
- Run tests with:

```bash
.venv/bin/pytest
```

## Ingestion APIs

The backend can push new knowledge into Qdrant through API endpoints under `/api/ingest`.

Security:

- Set `INGEST_API_KEY` in `.env` for protected environments.
- When set, callers must pass `x-ingest-api-key`.
- When empty, ingestion endpoints are open for local development.

Current embedded data sources:

| Source | How it is embedded |
|---|---|
| `data/resume.pdf` | Existing CLI ingest reads the PDF if present. |
| `data/projects.yaml` | One chunk per project. |
| `data/linkedin.md` | Markdown section chunks. |
| `data/naukri.md` | Markdown section chunks. |
| `data/faqs.yaml` | One FAQ chunk per question/answer pair. |
| Uploaded resume API | Extracts uploaded PDF text and chunks it as `kind=resume`. |
| URL crawl API | Crawls pages, extracts text, chunks it as `kind=url`. |

`POST /api/ingest/resume`:

1. Accepts `multipart/form-data` with a PDF field named `file`.
2. Extracts text using `pypdfium2`.
3. Splits text with `chunk_markdown_sections`.
4. Embeds chunks through OpenRouter embeddings.
5. Upserts points into Qdrant with payload fields including `source`, `kind`, `ingest`, `text`, and `content_hash`.

`GET /api/ingest/sources`:

1. Scrolls Qdrant payloads without loading vectors.
2. Reads `source` and `kind` from each point payload.
3. Groups points by source/kind so you can see what data is currently embedded.
4. Uses `max_points` to cap how many points are scanned.

`POST /api/ingest/urls`:

1. Accepts seed URLs plus crawl limits.
2. Fetches pages with `httpx`.
3. Extracts visible text using a lightweight HTML parser.
4. Discovers links up to `max_depth`.
5. Keeps discovered links on the same domain when `same_domain_only=true`.
6. Converts GitHub repository roots and `blob` URLs to raw README/file URLs when possible.
7. Embeds and upserts chunks into Qdrant.

Upsert idempotency:

- Point ids are deterministic UUIDv5 values based on chunk content hash.
- Re-ingesting the same chunk updates the same point instead of creating duplicate points.
