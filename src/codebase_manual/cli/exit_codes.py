"""The CLI's exit code scheme.

0 SUCCESS         The command completed.
1 USER_ERROR      Bad input the user can fix directly: an unresolvable
                   entity name, a repository that hasn't been indexed yet.
2 CONFIG_ERROR    The AI provider isn't usable as configured: no API key,
                   the `anthropic` package isn't installed, or the provider
                   rejected the credentials outright.
3 PROVIDER_ERROR  A request to a configured provider failed at the request
                   level (timeout, rate limit, network failure) or its
                   response didn't parse/validate.
4 INTERNAL_ERROR  Anything else unexpected -- a bug, not a usage or
                   provider problem.

`check` is a deliberate exception to this scheme: like `diff`/`grep`, exit
1 there means "drift was found," a successful comparison with a nonzero
result, not a failure. See `cli/main.py::check`.

Typer/Click's own argument validation (e.g. a `--repository-path` that
doesn't exist) exits 2 on its own, before any command body runs -- that
already lines up with CONFIG_ERROR without this module's help.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    USER_ERROR = 1
    CONFIG_ERROR = 2
    PROVIDER_ERROR = 3
    INTERNAL_ERROR = 4
