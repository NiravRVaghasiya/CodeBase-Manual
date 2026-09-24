"""Tests for the AI prompt context-size ceiling."""

from __future__ import annotations

from codebase_manual.ai.context_limits import truncate_context


def test_truncate_context_leaves_short_text_untouched() -> None:
    text = "short context"

    assert truncate_context(text, max_chars=100) == text


def test_truncate_context_caps_long_text_with_an_explicit_notice() -> None:
    text = "x" * 1000

    result = truncate_context(text, max_chars=100)

    assert len(result) <= 100
    assert "truncated" in result
    assert result.startswith("x" * 10)
