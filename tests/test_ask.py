"""The ask CLI's output, from a fixed eucc.rag.answer result."""

from eucc.ask import format_result


def _result(**overrides) -> dict:
    return {
        "status": "answered",
        "answer": "Within 72 hours [1].",
        "citations": [
            {
                "n": 1,
                "chunk_id": "gdpr-en-article-33-1-d",
                "reg": "GDPR",
                "unit_type": "article",
                "article": "33",
                "paragraph": None,
                "heading": "Notification",
                "source_url": "https://eur-lex.europa.eu/x",
            }
        ],
        "latency_ms": 1200,
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "reasoning_effort": "medium",
        "attempts": 1,
        **overrides,
    }


def test_answer_with_citations_and_links():
    text = format_result(_result())
    assert text.startswith("Status: answered\n\nWithin 72 hours [1].\n")
    assert "  [1] GDPR Art. 33 — Notification\n      https://eur-lex.europa.eu/x" in text
    assert text.endswith("1200 ms, 100 prompt + 20 completion tokens, reasoning effort medium")


def test_unsupported_shows_its_source_list_without_citations_none():
    answer = "The retrieved sources do not support an answer to this question.\n\n..."
    text = format_result(_result(status="unsupported", answer=answer, citations=[]))
    assert "Citations: none" not in text
    assert answer in text


def test_retry_is_reported():
    assert format_result(_result(attempts=2)).endswith(", retried once")
