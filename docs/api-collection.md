# CHET.ai API Collection

Base URL: `http://localhost:8000`

## GET `/api/health`

Purpose: Check backend dependency health for MongoDB, Redis, Qdrant, and OpenRouter configuration.

Query params: none.

Request body: none.

Example:

```bash
curl http://localhost:8000/api/health
```

Response shape:

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

## POST `/api/chat`

Purpose: Stream a RAG answer for a user message. The response is server-sent events.

Query params: none.

Request body:

```json
{
  "session_id": "string, required",
  "message": "string, required, 1-2000 chars"
}
```

Example:

```bash
curl -N http://localhost:8000/api/chat \
  -H "content-type: application/json" \
  -d '{"session_id":"demo-session","message":"Tell me about Chetan projects"}'
```

SSE events:

```text
event: meta
data: {"cache":null,"retrieved_ids":["..."],"model":"..."}

event: token
data: {"text":"..."}

event: done
data: {"latency_ms":1234,"tokens_in":100,"tokens_out":200}
```

## POST `/api/hire/start`

Purpose: Begin the hire lead-capture flow and receive the first question.

Query params: none.

Request body:

```json
{
  "source": "chat | dock | terminal:sudo-hire, optional, defaults to chat",
  "website": "string|null, optional honeypot; must be empty"
}
```

Example:

```bash
curl http://localhost:8000/api/hire/start \
  -H "content-type: application/json" \
  -d '{"source":"chat","website":null}'
```

Response shape:

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

```json
{ "detail": "invalid request" }
```

## POST `/api/hire/answer`

Purpose: Submit one hire-flow answer. Returns the next question, a validation error for the same field, or final completion.

Query params: none.

Request body:

```json
{
  "session_id": "string, required",
  "answer": "string, required, max 600 chars",
  "website": "string|null, optional honeypot; must be empty"
}
```

Field order:

```text
name -> company -> contact -> email -> address -> message -> send_details
```

Validation notes:

- `contact` expects a valid phone number with country code.
- `email` must be valid and deliverable.
- `address` and `message` may be empty.
- `send_details` accepts `yes` or `no`.

Example:

```bash
curl http://localhost:8000/api/hire/answer \
  -H "content-type: application/json" \
  -d '{"session_id":"<session_id>","answer":"Asha Kumar","website":null}'
```

Next-question response:

```json
{
  "session_id": "uuid",
  "question": "Which company are you with?",
  "field": "company",
  "lead_id": null
}
```

Validation response:

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

```json
{ "detail": "session not found or expired" }
```

## GET `/api/eval/means`

Purpose: Return recent evaluation score means. This is an admin-ish inspection endpoint.

Query params:

| Name | Type | Required | Default | Description |
|---|---|---:|---:|---|
| `limit` | integer | no | `100` | Maximum number of recent eval means to return. |

Request body: none.

Example:

```bash
curl "http://localhost:8000/api/eval/means?limit=25"
```

Response shape:

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

## POST `/api/ingest/resume`

Purpose: upload a PDF resume, extract text, chunk it, create embeddings, and upsert the chunks into Qdrant.

Headers:

| Name | Required | Description |
|---|---:|---|
| `x-ingest-api-key` | only if `INGEST_API_KEY` is set | Protects ingestion writes in non-local environments. |

Query params: none.

Request body: `multipart/form-data`

| Field | Type | Required | Description |
|---|---|---:|---|
| `file` | PDF file | yes | Resume PDF to parse and embed. |

Example:

```bash
curl http://localhost:8000/api/ingest/resume \
  -H "x-ingest-api-key: $INGEST_API_KEY" \
  -F "file=@/path/to/resume.pdf;type=application/pdf"
```

Response shape:

```json
{
  "ok": true,
  "collection": "chet_kb",
  "embedded_chunks": 8,
  "points_upserted": 8,
  "sources": [
    { "source": "resume.pdf", "chunks": 8 }
  ],
  "skipped": []
}
```

## GET `/api/ingest/sources`

Purpose: inspect what data sources are currently embedded in Qdrant.

Headers:

| Name | Required | Description |
|---|---:|---|
| `x-ingest-api-key` | only if `INGEST_API_KEY` is set | Protects ingestion metadata reads in non-local environments. |

Query params:

| Name | Type | Required | Default | Description |
|---|---|---:|---:|---|
| `max_points` | integer | no | `1000` | Maximum Qdrant points to scan for source summaries. |

Request body: none.

Example:

```bash
curl "http://localhost:8000/api/ingest/sources?max_points=1000" \
  -H "x-ingest-api-key: $INGEST_API_KEY"
```

Response shape:

```json
{
  "ok": true,
  "collection": "chet_kb",
  "scanned_points": 25,
  "sources": [
    { "source": "resume.pdf", "kind": "resume", "points": 8 },
    { "source": "projects.yaml", "kind": "project", "points": 2 },
    { "source": "https://chetanmarathe.dev", "kind": "url", "points": 5 }
  ]
}
```

## POST `/api/ingest/urls`

Purpose: crawl portfolio, GitHub, or other supplied URLs, extract readable text, chunk it, create embeddings, and upsert into Qdrant.

Headers:

| Name | Required | Description |
|---|---:|---|
| `x-ingest-api-key` | only if `INGEST_API_KEY` is set | Protects ingestion writes in non-local environments. |

Query params: none.

Request body:

```json
{
  "urls": ["https://chetanmarathe.dev", "https://github.com/chetanmarathe/some-repo"],
  "max_pages": 10,
  "max_depth": 1,
  "same_domain_only": true,
  "source_label": "api:url"
}
```

Body fields:

| Field | Required | Default | Description |
|---|---:|---|---|
| `urls` | yes | none | 1-20 seed URLs to crawl. |
| `max_pages` | no | `INGEST_DEFAULT_MAX_PAGES` | Maximum total pages to embed. |
| `max_depth` | no | `INGEST_DEFAULT_MAX_DEPTH` | Link depth from each seed URL. |
| `same_domain_only` | no | `true` | Restricts discovered links to the seed URL domain. |
| `source_label` | no | `api:url` | Metadata label stored on chunks. |

Example:

```bash
curl http://localhost:8000/api/ingest/urls \
  -H "content-type: application/json" \
  -H "x-ingest-api-key: $INGEST_API_KEY" \
  -d '{
    "urls": ["https://chetanmarathe.dev", "https://github.com/chetanmarathe"],
    "max_pages": 10,
    "max_depth": 1,
    "same_domain_only": true
  }'
```

GitHub behavior:

- GitHub repository roots also try the repository `README.md` through `raw.githubusercontent.com`.
- GitHub `blob/...` URLs are converted to raw file URLs before embedding.

Response shape:

```json
{
  "ok": true,
  "collection": "chet_kb",
  "embedded_chunks": 12,
  "points_upserted": 12,
  "sources": [
    { "source": "https://chetanmarathe.dev", "chunks": 5 },
    { "source": "https://raw.githubusercontent.com/owner/repo/HEAD/README.md", "chunks": 2 }
  ],
  "skipped": [
    "https://example.com/image.png (unsupported content-type: image/png)"
  ]
}
```
