"""Caches AI file summaries so repeated `manual` runs don't re-request one per file.

Cache keys combine everything that could change what a cached summary
means: the file's content hash, the summarizer's prompt/schema version,
the model that produced it, and the analyzer's output version. If a file's
content hasn't changed and nothing about how summaries are produced has
changed either, the cached summary is reused verbatim; otherwise it's
regenerated. A file with no full content hash (see `domain.models`'s
`FileHashStrategy`) is never cached -- there's nothing stable to key it on.
"""

from __future__ import annotations

from typing import Protocol

from codebase_manual.ai.models import FileSummary
from codebase_manual.domain.models import PYTHON_MODULE_SCHEMA_VERSION

# Bump when `ai.summarizer`'s prompt or expected JSON schema changes in a
# way that could change what a summary means.
SUMMARY_PROMPT_VERSION = "1"


class SummaryCacheStore(Protocol):
    """The minimal key-value contract a cache backend must satisfy.

    `persistence.store.IndexStore` implements this structurally -- no
    inheritance required.
    """

    def get_cached_value(self, cache_key: str) -> str | None: ...

    def set_cached_value(self, cache_key: str, payload: str) -> None: ...


def summary_cache_key(*, content_hash: str | None, model_identifier: str) -> str | None:
    """The cache key for a file summary, or `None` if the file can't be cached.

    A file without a full content hash has nothing stable to key on --
    caching it would either never hit (if keyed on something that changes
    every scan) or risk serving a stale summary for a changed file.
    """
    if content_hash is None:
        return None
    return (
        f"file_summary:{content_hash}:{SUMMARY_PROMPT_VERSION}:"
        f"{model_identifier}:{PYTHON_MODULE_SCHEMA_VERSION}"
    )


def get_cached_summary(store: SummaryCacheStore, cache_key: str) -> FileSummary | None:
    payload = store.get_cached_value(cache_key)
    return FileSummary.model_validate_json(payload) if payload is not None else None


def cache_summary(store: SummaryCacheStore, cache_key: str, summary: FileSummary) -> None:
    store.set_cached_value(cache_key, summary.model_dump_json())
