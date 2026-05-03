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
        parts.append(f"{head}\n{d.get('text', '').strip()}")
    return "\n\n".join(parts)


def build_messages(query: str, docs: list[dict]) -> list[dict[str, str]]:
    chunks = format_chunks(docs)
    system = _SYSTEM.replace("{retrieved_chunks}", chunks).replace("{query}", query)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ]


def build_rewrite_messages(query: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": _REWRITE.replace("{query}", query)}]
