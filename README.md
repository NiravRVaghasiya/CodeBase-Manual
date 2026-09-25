<div align="center">

# 📖 Codebase Manual

**Evidence-backed code intelligence for understanding, analyzing, and documenting software repositories.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

</div>

---

## The idea

Ask an LLM to read raw source and explain a codebase, and you're trusting it
to reconstruct architecture from context alone — with no way to check its
answer against anything.

**Codebase Manual inverts that.** It runs deterministic static analysis
*first* — parsing source, resolving calls, building a relationship graph
with real evidence — and only *then* lets an AI layer interpret that graph.
The model never invents a file path, a symbol, or a relationship. It can
only reference facts the analysis already found, addressed by an opaque
candidate ID, and every ID it returns is validated before it reaches you.

```
Source code  →  Static analysis  →  Evidence graph  →  Bounded AI context  →  Grounded answer
              (facts, not guesses)                    (only real entities)   (every reference checked)
```

Documentation generation is one output of this pipeline, not its purpose —
the same evidence graph also answers questions, plans changes, and traces
dependency impact.

## Who this is for

- **Engineers onboarding onto an unfamiliar repo** who want answers they can
  trust, not a plausible-sounding guess.
- **Reviewers scoping a change** who need to know what a function actually
  calls and what actually tests it before touching it.
- **Teams that want living documentation** generated from real structure
  instead of docs that drift out of sync with the code.

## What you get

| Command | Purpose |
|---|---|
| `index` | Scan + analyze + build the relationship graph, persisted for reuse |
| `ask` | Ask a question about the repo, answered from retrieved evidence |
| `change` | Plan a change: files to touch, tests to update, likely impact |
| `impact` | Trace what depends on a file/symbol, and why |
| `manual` | Generate a Markdown manual from the same underlying facts |
| `check` | Detect drift between the working tree and the last index |
| `serve` | Browse it all through a web UI |

Under the hood, every one of these runs on the same facts: files, Git
metadata, symbols, and the import/call/inheritance/test relationships
between them, extracted by parsing — never by asking a model what it thinks
is there.

## Quickstart

```bash
uv sync

# 1. Index a repository
uv run codebase-manual index /path/to/repository

# 2. Ask it something
uv run codebase-manual ask /path/to/repository "How does authentication work?"

# 3. Plan a change
uv run codebase-manual change /path/to/repository "Add Google OAuth"

# 4. Trace impact before you touch something
uv run codebase-manual impact /path/to/repository app/auth/service.py

# 5. Generate the manual
uv run codebase-manual manual /path/to/repository --out MANUAL.md
```

`ask`/`change`/`impact`/`manual` read from the persisted index — run `index`
first. `impact` and `manual` work without an AI provider configured (they
report deterministic facts and say so); `ask` and `change` need one
(`ANTHROPIC_API_KEY`, currently Anthropic only).

Every command supports `--json`, `--verbose`, `--quiet`, and documented exit
codes — see [`docs/cli.md`](docs/cli.md).

## What "grounded" actually means

When you `ask` a question, the model doesn't see your source code — it sees
a bounded set of retrieved facts, each addressed by an opaque ID:

```
Model receives:
  FILE_001 = app/auth/service.py
  SYMBOL_014 = app.users.repository.UserRepository.find_user

Model may cite: [SYMBOL_014]        → resolved against real facts → accepted
Model may cite: [FILE_999]          → never issued                → rejected, quarantined
```

An invented reference is mechanically detectable and never survives into a
result. `confidence` on every answer is likewise computed from the evidence
strength behind it — never a number the model reports about itself.

Every relationship carries one of four fixed evidence strengths:

| Strength | Meaning |
|---|---|
| `DIRECT` | Read straight off the AST/filesystem — no resolution step |
| `RESOLVED` | Required a resolution step that can fail (import → module, call → callee) |
| `INFERRED` | A deterministic heuristic with acknowledged false-positive risk (retrieval) |
| `UNKNOWN` | Could not be determined — tracked honestly, never dropped or guessed |

Details: [`docs/evidence-model.md`](docs/evidence-model.md),
[`docs/ai-grounding.md`](docs/ai-grounding.md),
[`docs/confidence.md`](docs/confidence.md).

## Capabilities at a glance

- Filesystem + Git-aware repository scanning, with always-on secret exclusion.
- Python analysis via `ast`; a second, independently-registered TypeScript
  analyzer proving the pipeline is language-agnostic.
- Type-aware call resolution — constructor injection, direct construction,
  dataclass attributes, simple factories, inheritance-aware lookup — with
  anything unresolved recorded explicitly, never guessed.
- Two-granularity test-to-code linking (module-level and, where a call
  resolves precisely, symbol-level).
- API endpoint detection (decorator-based and call-based registration).
- Multi-signal explainable retrieval — every result can say *why* it surfaced.
- Incremental analysis — unchanged files are reused, not re-parsed.
- Structured logging of retrieval, grounding, and resolution decisions
  (never file content or secrets).

See [Current limitations](#current-limitations) for what this deliberately
does *not* do yet.

## Security posture

- Secret-shaped files (`*.pem`, `*.key`, `credentials.*`, …) are excluded
  before the scanner ever reads them.
- `.env*` content is never read into memory — only path/size/language.
- Raw file bytes never leave the scanner; only a one-way SHA-256 hash persists.
- AI context is capped by a hard character ceiling, with explicit truncation.
- Model output cannot inject arbitrary repository entities — see
  [AI grounding](#what-grounded-actually-means).

Full data-flow writeup: [`docs/security.md`](docs/security.md).

## Current limitations

Stated plainly:

- **TypeScript analysis is a conservative scanner, not a real parser** — no
  `tree-sitter`/compiler-API dependency. Arrow functions aren't extracted as
  symbols, and cross-file import resolution assumes Python package semantics
  today. Full list: [`docs/analyzers.md`](docs/analyzers.md).
- **Type inference covers a fixed set of concrete shapes**, not general
  inference. An untyped or dynamically computed callee resolves to nothing,
  never a guess.
- **Retrieval favors recall over precision by design** — a graph-connected
  but off-topic file can be pulled into context. Intentional trade-off, not
  a bug.
- **Test-to-code linking requires an explicit resolved call** — a test with
  no resolving call to a function produces no symbol-level edge, so
  `impact <function>` can under-report indirectly-exercised tests.
- **AI features require Anthropic's API** — no local/offline model path yet.
- **No database migration tooling** — a schema change means re-indexing.

## Development

Requires Python ≥ 3.12.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy src
```

```bash
# Benchmark harness (not part of the default test run)
python scripts/benchmark.py [accuracy|hallucination|performance|all]
```

**Configuration** (env vars / `pyproject.toml`):

| Setting | Purpose |
|---|---|
| `CODEBASE_MANUAL_DATABASE_URL` | SQLAlchemy URL; defaults to a local SQLite file |
| `ANTHROPIC_API_KEY` | Required for `ask`/`change` and AI summaries |
| `CODEBASE_MANUAL_AI_MODEL` | Overrides the default Anthropic model |
| `[tool.codebase-manual]` | `python-source-roots`, `ignored-paths`, `max-context-size`, etc. |

**Project layout:**

```
src/codebase_manual/
    cli/            command-line interface (Typer)
    domain/         core entities, relationships, evidence, type inference
    repository/     repository scanning, Git metadata
    analyzer/       Python (ast) and TypeScript analysis, language registry
    persistence/    SQLAlchemy schema, idempotent indexing
    query/          retrieval, relationship graph traversal, drift detection
    ai/             provider interface + grounded Q&A/change/impact/manual
    api/            FastAPI web UI (server-rendered, Jinja2)
```

## Documentation

Start with [`docs/architecture.md`](docs/architecture.md) — the end-to-end
pipeline, linking out to everything else: evidence model, confidence,
AI grounding, relationship model, retrieval, indexing, security, CLI,
performance, analyzers, testing, and [contributing](docs/contributing.md).

## License

[MIT](LICENSE)
