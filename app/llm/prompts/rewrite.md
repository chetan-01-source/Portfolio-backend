Rewrite the user's question into a self-contained search query for a knowledge base about Chetan Marathe (AI Full Stack Engineer).

Use the RECENT CHAT SUMMARY to resolve references like "this project", "that role", "it", "there", "he", "his". Replace the reference with the specific entity (project name, company, etc.) that the summary points to.

Rules:
- Resolve pronouns ("his", "he", "him") → "Chetan".
- Resolve demonstratives ("this project", "that company", "it") → the specific name from the summary.
- Expand obvious acronyms once if you're confident (e.g. "RAG" → "RAG (retrieval-augmented generation)").
- Keep it under 25 words.
- If the question is already self-contained and has no references, return it unchanged.
- Output the rewritten question only — no preamble, no quotes, no explanation.

RECENT CHAT SUMMARY:
{chat_summary}

Question: {query}
