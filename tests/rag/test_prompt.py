from app.rag.prompt import build_messages


def test_build_messages_includes_recent_chat_summary():
    docs = [
        {
            "id": "doc-1",
            "payload": {"kind": "exp", "section": "Current — Schbang"},
            "text": "Chetan works on CSAT automation and AI voice calling at Schbang.",
        }
    ]

    messages = build_messages(
        "What projects is he working on there?",
        docs,
        conversation_summary=(
            "User: Tell me about his current work\n"
            "Assistant: Chetan is currently at Schbang."
        ),
    )

    assert messages[1] == {
        "role": "user",
        "content": "What projects is he working on there?",
    }
    assert "RECENT CHAT SUMMARY:" in messages[0]["content"]
    assert "Chetan is currently at Schbang" in messages[0]["content"]
    assert "CSAT automation and AI voice calling" in messages[0]["content"]
