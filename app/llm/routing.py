"""cheap → strong escalation rule (plan §9)."""

import re

UNCERTAIN_RE = re.compile(
    r"\b(i'?m not sure|i don'?t know|no info|insufficient context|cannot determine)\b",
    re.IGNORECASE,
)


def should_escalate(answer: str, *, faithfulness: float | None = None, min_chars: int = 30) -> bool:
    text = (answer or "").strip()
    if len(text) < min_chars:
        return True
    if UNCERTAIN_RE.search(text):
        return True
    if faithfulness is not None and faithfulness < 0.7:
        return True
    return False
