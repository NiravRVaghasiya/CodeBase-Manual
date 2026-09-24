"""Structured logging setup, shared by the CLI and the web API.

A single `logging.basicConfig` call, gated on `--verbose`/`--quiet` so the
CLI's own output volume and its log volume move together. Every log call
site in this codebase logs paths, counts, durations, and outcomes only --
never a file's raw content, a `.env*` value, or an `AIProvider`'s API key
(see `docs/security.md`'s logging rule of thumb). That's a discipline
enforced by review, not by a filter, since there's nothing to filter
secrets out of if they're never formatted into a log message in the first
place.
"""

from __future__ import annotations

import logging

_LOGGER_NAME = "codebase_manual"
_configured = False


def configure_logging(*, verbose: bool = False, quiet: bool = False) -> None:
    global _configured
    if _configured:
        return
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger(_LOGGER_NAME).setLevel(level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")
