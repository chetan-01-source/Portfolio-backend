"""Builds the system + retrieved-context prompt."""

from pathlib import Path

_SYSTEM_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "system.md"
_REWRITE_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "rewrite.md"

_SYSTEM = _SYSTEM_PATH.read_text(encoding="utf-8")
_REWRITE = _REWRITE_PATH.read_text(encoding="utf-8")


def format_chunks(docs: list[dict]) -> str:
    parts: list[str] = []
    for i, d in enumerate(docs, start=1):
        payload = d.get("payload", {})
        tag = payload.get("kind") or payload.get("source", "doc")
        name = payload.get("section") or payload.get("name") or ""
        head = f"[{i}] ({tag}{':' + name if name else ''})"
        body = d.get("text", "").strip()
        repo_url = payload.get("repo_url")
        if repo_url and "Repository:" not in body:
            body += f"\n\nLinks:\n- Repository: {repo_url}"
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts)


def build_messages(
    query: str, docs: list[dict], *, conversation_summary: str = ""
) -> list[dict[str, str]]:
    chunks = format_chunks(docs)
    chat_summary = conversation_summary.strip() or "No prior conversation in this session."
    system = (
        _SYSTEM.replace("{retrieved_chunks}", chunks)
        .replace("{chat_summary}", chat_summary)
        .replace("{query}", query)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ]


def build_rewrite_messages(query: str, *, conversation_summary: str = "") -> list[dict[str, str]]:
    chat_summary = conversation_summary.strip() or "No prior conversation in this session."
    content = _REWRITE.replace("{chat_summary}", chat_summary).replace("{query}", query)
    return [{"role": "user", "content": content}]
