"""Caps how much assembled context text is placed into a single AI prompt.

Retrieval already bounds candidate *counts* (`query.retrieval`'s
`top_k_files`/`top_k_symbols`), but a pathological repository -- huge
docstrings, many matched symbols -- could still produce a prompt body large
enough to be a cost/context-window risk. This is a hard ceiling on the
assembled text itself, with an explicit truncation marker rather than a
silent cutoff, so a truncated context can be recognized as such rather than
mistaken for a complete one.
"""

from __future__ import annotations

_TRUNCATION_NOTICE_TEMPLATE = "\n[... context truncated at {limit} characters ...]"


def truncate_context(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    notice = _TRUNCATION_NOTICE_TEMPLATE.format(limit=max_chars)
    return text[: max(0, max_chars - len(notice))] + notice
