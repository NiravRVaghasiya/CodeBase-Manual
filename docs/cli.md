# CLI: exit codes, global flags, JSON schemas

## Exit code scheme

Defined in `cli/exit_codes.py`:

| Code | Meaning | Example |
|---|---|---|
| 0 | Success | |
| 1 | User error -- fixable directly by the caller | unresolvable entity name, repository not yet indexed |
| 2 | Configuration error -- the AI provider isn't usable as set up | no `ANTHROPIC_API_KEY`, `anthropic` not installed, credentials rejected |
| 3 | Provider error -- a request to a configured provider failed | timeout, rate limit, network failure, malformed response |
| 4 | Internal error -- unexpected, not a usage or provider problem | a bug |

Two deliberate exceptions:

- **Typer/Click's own argument validation** (e.g. `--repository-path` that
  doesn't exist) exits 2 on its own, before any command body runs -- this
  already lines up with CONFIG_ERROR's spirit ("something about how the
  command was invoked is wrong") without any code in this project doing
  anything.
- **`check`** follows `diff`/`grep` convention: exit 1 means "drift was
  found," a successful comparison with a nonzero result, not a failure.
  This overlaps numerically with USER_ERROR but is a different kind of
  "1" -- `check` never uses exit 1 for an actual error (a missing index
  still exits 1 via `load_snapshot_or_exit`, which *is* the general
  USER_ERROR case).

Errors from an AI-backed command route through `cli.context.run_ai_call`,
which maps `AIProviderNotConfiguredError` -> 2, `AIProviderError`/
`AISynthesisError` -> 3, anything else -> 4. `impact` is the one exception:
it degrades gracefully on the three known AI failure types (its
deterministic dependency facts stand alone without an AI explanation) and
only maps a genuinely unexpected error to 4 -- see `cli/main.py::impact`
and `_impact_explanation`.

## Global flags

Set once in `main()`'s Typer callback, available to every command via
`cli.context.cli_state(ctx)`:

- **`--json`**: emit the command's result as a single JSON object via a
  pydantic model's `.model_dump_json()`, instead of formatted text.
  `ask` -> `Answer`, `change` -> `ChangePlan`, `impact` -> `ImpactReport`,
  `index` -> `IndexSummary` (defined in `cli/main.py`; deterministic counts
  only, no AI), `check` -> `IndexDriftReport`. `manual` and `serve` ignore
  `--json`: a manual is prose with no natural structured form, and `serve`
  doesn't print a result at all.
- **`--verbose`**: on a failure, print the full traceback of the
  underlying exception (via `traceback.format_exception`) in addition to
  the concise message. Also raises the log level to DEBUG.
- **`--quiet`**: suppress the secondary/framing output of a command
  (confidence lines, evidence lists, the plain-text index report body,
  the drift "Unchanged: N" line, the "Manual written to X" confirmation)
  while keeping its primary result (answer text, plan file lists, impact
  facts, drift file lists). No effect together with `--json`, since JSON
  output is already minimal. Also lowers the log level to WARNING.

## Logging

`logging_config.configure_logging()` is called once, from `main()`'s
callback, gated on `--verbose`/`--quiet`. Log call sites cover: index
started/finished (file/module/relationship/**unresolved-call** counts,
duration -- `cli/main.py`), analysis reuse during an incremental index
(`analyzer.registry`'s `reused`/`reanalyzed` counts), AI request
count/latency/success-or-failure (`ai/anthropic_provider.py`),
retrieval/grounding counts and *why* an entity was pulled in by graph
expansion (`query/retrieval.py`, DEBUG level), and traversal truncation
(`query/graph.py`). Every log line carries paths, counts, durations, and
outcomes only -- never a file's raw content, a `.env*` value, or an API
key (see `docs/security.md`'s logging rule of thumb).

"Relationships unresolved" is now tracked, not just "relationships
discovered": `domain.models.UnresolvedCall`, logged as part of
`domain.relationships.build_relationships_with_unresolved`'s summary line
and surfaced in `index`'s own count (`cli.main.IndexSummary.
unresolved_calls`) -- see `docs/architecture.md`'s evidence-graph section.

## Web API: JSON surface deliberately deferred

`api/app.py` stays server-rendered HTML only this phase. The brief listed
a typed JSON surface (request/response models, pagination, 404-vs-409-vs-
200-empty distinctions) as optional for this phase ("decide whether ... in
scope or deferred"). Deferred, because:

- The CLI's `--json` flag already delivers this phase's actual goal --
  a machine-readable interface reusing the same pydantic models -- without
  needing a second, parallel serialization surface.
- The web UI's interaction model is HTML form submission, not resource
  fetching: an unresolvable `impact` target renders as an inline form
  error (in-context, immediately actionable), not a `404` (which fits a
  "fetch resource by ID" API shape, not a form-submission one). Retrofitting
  REST-style status codes onto a form-based UI would fight its own grain
  rather than improve it.

If a JSON API surface for the web app is wanted later, it belongs in its
own phase with its own typed models -- not bolted onto the HTML routes.

## Framework-aware API endpoint detection

`query/api_endpoints.py` recognizes three shapes: `<name>.<method>(path)`,
`<name>.api_route(path, methods=[...])` (`methods` defaults to `["GET"]`
when omitted, matching Starlette), and `<name>.add_api_route(path, handler,
methods=[...])` -- a *call statement* registering a separately-defined
handler, not a decorator on it. The third needed `CallSite` to capture a
call expression's arguments (`domain.models.CallArgument`, added
alongside `domain.models.UnresolvedCall`'s prerequisite work); it was
deferred until that landed and is now built. The handler argument is
resolved to a qualified name only when it's a bare name matching a
function/method defined in the *same module* -- a handler imported from
elsewhere produces an `ApiEndpoint` with `function_qualified_name=None`
rather than a guess; the path/method registration is still real evidence
even when the handler can't be resolved. Each `ApiEndpoint` records which
shape matched via `EndpointDetection`, not a guessed framework name --
FastAPI and Starlette share this decorator syntax, and code that merely
imitates it is syntactically indistinguishable from either without
inspecting imports. Naming the framework from syntax alone would be
exactly the kind of fabrication this codebase's evidence model exists to
prevent.
