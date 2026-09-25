# Codebase Manual

**Evidence-backed code intelligence for understanding, analyzing, and documenting software repositories.**

Codebase Manual builds a deterministic structural/semantic model of a
repository -- files, Git metadata, Python symbols, and the import/call/
inheritance/test relationships between them -- and lets an AI layer explain
that model instead of guessing at architecture from raw source. The AI
never invents a file path, a symbol, or a relationship: it can only
reference entities the deterministic analysis already found, by an opaque
candidate ID, and every reference it returns is validated against the real
facts before it reaches a result.

Documentation generation (`manual`) is one output of this system, not its
purpose -- the same evidence graph also answers repository questions
(`ask`), plans changes (`change`), and traces dependency impact (`impact`).

## What it does

Modern repositories are hard to reason about because the knowledge that
explains them -- what calls what, what inherits from what, what tests what,
what depends on what -- is scattered across files, imports, and tests
rather than written down anywhere. Asking an LLM to read raw source and
answer questions about a codebase means trusting it to reconstruct that
structure from context alone, with no way to check its answer against
anything.

Codebase Manual inverts that: static analysis extracts structural facts
*first*, those facts are turned into a relationship graph with explicit
evidence, and only *then* does an AI layer get involved -- and even then,
only to interpret and explain a bounded, retrieved subset of facts it was
actually given, never to invent new ones.

## Core architecture

```
Repository
   |
   v
Scanner (repository.scanner) -- filesystem walk, Git metadata,
   |                            content hashing, secret/path exclusion
   v
Language Analyzer (Python via ast, TypeScript via a conservative scanner --
   |                dispatched by analyzer.registry, incrementally reused
   |                for unchanged files)
   v
Structural Facts (domain.models) -- PythonModule per file
   |
   v
Evidence / Relationship Graph (domain.relationships, domain.evidence)
   |                -- CONTAINS / IMPORTS / CALLS / INHERITS / TESTS,
   |                   each with a deterministic evidence strength
   v
Deterministic Retrieval (query.retrieval, query.graph)
   |                -- multi-signal, explainable, relationship-aware
   v
Bounded AI Context (query.candidates, ai.context_limits)
   |                -- opaque candidate IDs only, hard size ceiling
   v
LLM Interpretation (ai.qa / ai.change_planner / ai.impact / ai.manual)
   |
   v
Grounding / Validation (ai.grounding.GroundingValidator)
   |                -- every model-cited ID resolved against real facts
   v
Answer / Impact Report / Change Plan / Manual (ai.models)
   |                -- confidence computed from evidence, never self-reported
```

Persistence (SQLite by default, PostgreSQL optionally) sits alongside the
graph stage: a `RepositorySnapshot` is written once by `index` and read by
every query-side command, so `ask`/`change`/`impact`/`manual` operate on an
already-indexed repository rather than re-analyzing it per query.

## Why this is different

- **Deterministic first.** Every file, symbol, import, call, inheritance
  edge, and test link comes from parsing source with Python's `ast` module
  and cross-referencing the results -- never from asking a model what it
  thinks is there.
- **Evidence-backed.** Every relationship carries an `evidence` string, a
  source location where applicable, and a deterministically assigned
  `EvidenceStrength` (`DIRECT`/`RESOLVED`/`INFERRED`/`UNKNOWN`) -- a
  property of *how* that kind of fact is derived, not a per-instance
  judgment call.
- **Grounded AI.** The model is only ever given a bounded set of retrieved
  entities, addressed by opaque IDs (`FILE_001`, `SYMBOL_014`, `TEST_003`).
  It can select among those IDs and write explanatory prose; it cannot
  write a raw path or symbol name into a structured field. Every ID it
  returns is resolved against the real candidate set before it survives
  into a result.
- **Fail-closed.** An ambiguous call, an unresolved base class, a call
  through a variable with no determinable type -- all of these are
  *dropped*, never guessed at. `UNKNOWN` is a legitimate, tracked outcome;
  a fabricated relationship is not an acceptable one under any
  circumstances.
- **Security-conscious.** Secret-shaped files (`.pem`, `.key`,
  `credentials.*`, etc.) are excluded before the scanner even reads them;
  `.env*` file content is never hashed or read into memory (only the path
  is visible); raw source text is never sent to an AI provider, only
  extracted facts, capped by a hard character ceiling.
- **Explainable retrieval.** Every retrieved file/symbol carries the
  specific signals that surfaced it (exact name match, qualified-name
  match, docstring match, a graph relationship, directory proximity) --
  retrieval can always answer "why was this here," not just "here's what I
  found."

None of this claims the system never produces an unhelpful or incomplete
answer, or that hallucination is impossible in principle -- see
[Current limitations](#current-limitations). What it claims is narrower and
verifiable: the facts feeding the AI are real, and every specific entity
reference in a result was checked against them.

## Capabilities

Everything below currently exists and is exercised by the test suite; see
[Current limitations](#current-limitations) for what does not.

- **Repository scanning** -- filesystem walk respecting `.gitignore`, a
  configurable ignore list, and always-on secret-pattern exclusion; Git
  metadata (commit, branch, dirty state, remote URL).
- **Python static analysis** -- module docstrings, imports (with
  alias/relative-level tracking), functions/classes/methods (parameters,
  decorators, docstrings, async), nested functions/classes at any depth,
  scoped call sites (with unparsed call arguments) and assignments used
  for type inference (see below).
- **TypeScript static analysis** -- a second, independently registered
  `LanguageAnalyzer` (imports, functions, classes with `extends`, scoped
  calls), built as a proof that the pipeline above the analyzer layer is
  genuinely language-agnostic. Deliberately more conservative than the
  Python analyzer -- see [Current limitations](#current-limitations).
- **Relationship graph** -- `CONTAINS`, `IMPORTS`, `CALLS`, `INHERITS`,
  `TESTS`, each derived from concrete syntactic evidence across the whole
  analyzed repository, not just one file at a time, uniformly over facts
  from either analyzer.
- **Type-aware call resolution** -- beyond bare-name and same-class
  `self.method()` calls: constructor-injected dependencies, direct
  construction, dataclass-style class-level annotations, simple factory
  return-type inference, local-variable propagation, and inheritance-aware
  method/attribute lookup. Deliberately not a type checker -- an
  unrecognized shape resolves to nothing, never a guess, and is recorded
  explicitly as an `UnresolvedCall` rather than silently dropped.
  See [`docs/relationship-model.md`](docs/relationship-model.md).
- **Test-to-code linking at two granularities** -- a module-level `TESTS`
  edge (test module -> target module) and, when a test's resolved call
  reaches a specific function or class, a symbol-level edge straight to
  it -- so `impact <function>` can be more useful than `impact <module>`
  when the evidence supports it.
- **API endpoint detection** -- route decorators (`router.post(...)`,
  `app.api_route(...)`) and call-based registration
  (`router.add_api_route(path, handler, ...)`), recording which shape
  matched rather than guessing a framework name.
- **Evidence-backed retrieval** -- multi-signal, weighted, explainable
  matching (exact name, qualified name, docstring, one-hop relationship
  expansion, directory proximity) plus bounded call-chain discovery
  between retrieved entities. See [`docs/retrieval.md`](docs/retrieval.md).
- **Grounded AI Q&A, change planning, and impact analysis** -- `ask`,
  `change`, and `impact` each give the model a bounded candidate set and
  validate every entity reference it returns.
  See [`docs/ai-grounding.md`](docs/ai-grounding.md).
- **Documentation/manual generation** -- a Markdown manual assembled from
  the same deterministic facts plus AI-generated summaries.
- **Deterministic confidence** -- never read from the model; computed from
  the evidence strength backing whatever survived grounding.
  See [`docs/confidence.md`](docs/confidence.md).
- **Incremental analysis** -- re-indexing reuses a previous run's analysis
  for any file whose content fingerprint hasn't changed, instead of
  re-parsing it; the relationship graph is still recomputed in full every
  run (cheap relative to analysis, and correctness-preserving).
  See [`docs/indexing.md`](docs/indexing.md).
- **Index drift detection** -- fingerprint comparison (`check`) between the
  working tree and the last index.
- **Structured logging** -- retrieval match reasons, graph traversal
  truncation, relationship-derivation and unresolved-call counts, and AI
  grounding rejections are all logged (never file content or secrets).
- **Secret-aware handling** -- see [Security](#security).
- **CLI and a server-rendered web UI** -- the same underlying pipeline
  behind both.

## Example workflow

```bash
# Index a repository (scan + analyze + derive relationships + persist).
uv run codebase-manual index /path/to/repository

# Ask a question, grounded in retrieved evidence.
uv run codebase-manual ask /path/to/repository "How does authentication work?"

# Plan a change: files to modify/create, tests, potential impact.
uv run codebase-manual change /path/to/repository "Add Google OAuth"

# See what depends on a file/module/class/function, and why.
uv run codebase-manual impact /path/to/repository app/auth/service.py

# Generate the living manual (Markdown).
uv run codebase-manual manual /path/to/repository --out MANUAL.md

# Compare the working tree against the last index (fingerprints only).
uv run codebase-manual check /path/to/repository

# Serve the web UI over an already-indexed repository.
uv run codebase-manual serve /path/to/repository
```

`ask`, `change`, `impact`, and `manual` read from the persisted index, so
run `index` first. `impact` and `manual` still work without an AI provider
configured -- they report deterministic facts and say so honestly; `ask`
and `change` require one (currently Anthropic only).

Every command accepts global `--json`, `--verbose`, and `--quiet` flags,
and exits with a documented code (0 success, 1 user error, 2 configuration
error, 3 provider error, 4 internal error) -- see
[`docs/cli.md`](docs/cli.md).

## Example question

```
$ codebase-manual ask . "How does authentication reach the user repository?"
```

`ask` retrieves the bounded set of files/symbols relevant to the question,
assigns them candidate IDs, and gives the model the retrieved
docstrings/signatures plus any discovered call chains (e.g.
`AuthRouter.login --calls--> AuthService.authenticate --calls-->
UserRepository.find_user`). The model answers using only that context and
must cite which candidates it relied on:

```json
{
  "question": "How does authentication reach the user repository?",
  "text": "AuthRouter.login calls AuthService.authenticate, which calls UserRepository.find_user...",
  "confidence": "medium",
  "evidence": [
    { "description": "cited candidate `app.auth.service.AuthService`", "file_path": "app.auth.service.AuthService" },
    { "description": "cited candidate `app.users.repository.UserRepository`", "file_path": "app.users.repository.UserRepository" }
  ],
  "grounding": "valid"
}
```

If the model had cited an ID it wasn't given, `grounding` would read
`partially_valid` or `invalid` and that reference would be absent from
`evidence` -- quarantined, not trusted. `confidence` here is capped at
`medium` because `ask`'s evidence comes from retrieval (a deterministic
heuristic, `INFERRED` strength) rather than a directly resolved fact --
see [Evidence model](#evidence-model).

## Evidence model

Every relationship and every piece of evidence an AI result cites carries
one of four strengths, fixed per relationship kind rather than judged case
by case:

| Strength | Meaning |
|---|---|
| `DIRECT` | Read straight off the AST/filesystem, no resolution step (a symbol's own definition, a docstring). |
| `RESOLVED` | Required a deterministic resolution step that can fail (an import resolved to a module, a call resolved to a callee, a base class resolved to a class). |
| `INFERRED` | A deterministic heuristic with acknowledged false-positive risk (retrieval matches, one-hop graph expansion). |
| `UNKNOWN` | The fact could not be determined. Its presence *is* the evidence of "unknown" -- never treated as a negative fact or silently dropped. |

`CONTAINS` is `DIRECT`; `IMPORTS`/`CALLS`/`INHERITS`/`TESTS` are `RESOLVED`
(each requires a resolution step, so each can also legitimately fail to
resolve rather than being fabricated). See
[`docs/evidence-model.md`](docs/evidence-model.md) and
[`docs/confidence.md`](docs/confidence.md) for how strength turns into a
result's `confidence` field.

## AI grounding

No LLM-facing prompt schema in this project has a `confidence` field --
that's not a prompting convention, it's structural: the model has nowhere
to put a self-reported trust level even if it tried. Structured references
to real entities work through candidate IDs instead:

```
Model receives (as its entire view of the repository for this query):
  FILE_001 = app/auth/service.py
  SYMBOL_014 = app.users.repository.UserRepository.find_user
  TEST_003  = tests/test_auth_service.py

Model may return:
  cited_ids: [SYMBOL_014, TEST_003]
Validator: resolves both against the real candidate set -> accepted

Model may instead return:
  cited_ids: [FILE_999]
Validator: FILE_999 was never issued -> rejected, quarantined,
           does not appear in the result's evidence
```

This constrains *which entities* a result may reference and makes an
invented reference mechanically detectable -- it does not, by itself,
prove every sentence of free-form prose is correct. `ai.impact`'s
explanatory prose is grounded the same way as `ai.qa`/`ai.change_planner`:
see [`docs/ai-grounding.md`](docs/ai-grounding.md) for the exact mechanism
and its limits.

## Security

- **Secret-shaped files are excluded before scanning reads them**:
  `*.pem`, `*.key`, `*.p12`, `credentials.*`, `secrets/`, `node_modules/`,
  `vendor/` by default, plus `.gitignore` and any repository-configured
  `ignored-paths`/`ignored-extensions`.
- **`.env*` file content is never read into memory**, at any size -- only
  the path/size/language are recorded, so a manual can still say
  "configuration exists here" without ever touching a value.
- **Raw file bytes never leave the scanner.** Only a one-way SHA-256
  content hash is persisted; nothing downstream (retrieval, prompts,
  storage) has access to a file's actual bytes, only structural facts
  extracted from it.
- **Only Python files are analyzed.** A file with no registered analyzer
  can never produce a fact and therefore can never appear in retrieval, a
  candidate set, or an AI prompt.
- **AI context has a hard character ceiling** (`SecurityConfig.
  max_context_size`), applied after retrieval's own bounded candidate
  counts, with an explicit truncation marker rather than a silent cutoff.
- **Model output cannot inject arbitrary repository entities** -- see
  [AI grounding](#ai-grounding).

Repository metadata (paths, structure, git history) is itself potentially
sensitive and is treated as part of the same trust boundary as source
content -- "no source code sent" is not treated as equivalent to "nothing
sensitive sent." See [`docs/security.md`](docs/security.md) for the full,
precise data-flow writeup, including what is deliberately *not* covered
yet (no logging in the relationship/retrieval layer; see
[Current limitations](#current-limitations)).

## Performance

Measured with `scripts/benchmark.py` on synthetic linear-chain
repositories, one Windows development machine -- see
[`docs/performance.md`](docs/performance.md) for the full table and for
why these are *measured limits*, not an SLA:

| Files | Scan+analyze+relationships | Peak memory |
|---|---|---|
| 1,000 | ~2.2s | ~27 MB |
| 10,000 | ~26s | ~263 MB |
| 50,000 | ~223s | ~1.3 GB |

Retrieval precision/recall and hallucination-quarantine rates against a
small, hand-verified fixture repository are also in that document,
explicitly labeled as measuring *this fixture* and *a scripted stub
provider's interaction with the grounding pipeline* -- not a general claim
about retrieval quality or hallucination rates against a real model or
real repositories.

## Current limitations

Stated plainly, not hidden:

- **TypeScript analysis is deliberately conservative, not a real parser.**
  No `tree-sitter`/compiler-API dependency was added (this environment had
  no way to install one safely, and the project favors minimal
  dependencies) -- it's a brace-depth scanner over regex-recognized
  declaration shapes. Arrow functions and function expressions aren't
  extracted as symbols; template-literal interpolation isn't scanned; no
  attribute/local-variable type inference exists for TypeScript at all
  (`this.method()` resolves, `this.injectedField.method()` doesn't); and
  cross-file relative-import resolution doesn't work, since the resolver's
  relative-import logic assumes Python package semantics. See
  [`docs/analyzers.md`](docs/analyzers.md) for the complete, disclosed list.
- **Type inference is deliberately incomplete.** It resolves a fixed set
  of concrete shapes (constructor injection, direct construction,
  dataclass-style annotations, simple factories, name propagation,
  resolvable inheritance) -- not general type inference. A call through an
  untyped parameter, a dynamically computed callee, or a classmethod
  factory (`Class.create()`) resolves to nothing rather than a guess. True
  dynamic dispatch (runtime-value-dependent method selection) is not
  attempted; it's statically undecidable in general, not a deferred
  feature.
- **Retrieval favors recall over precision by design.** One-hop
  relationship expansion can pull in a file that's graph-connected but not
  actually on-topic for a given question; this is a measured, intentional
  trade-off (see [Performance](#performance)), not a bug, but it means
  retrieved context is not guaranteed to be tightly relevant.
- **Test-to-code linking still requires an explicit resolved call.** A
  test living in the same module or file as a function, with no call that
  resolves specifically to it, produces no symbol-level `TESTS` edge --
  by design (no fabrication), but it means `impact <function>` can still
  under-report affected tests for real, indirectly-exercised code.
- **Incremental analysis skips re-parsing, not relationship derivation.**
  The full relationship graph is recomputed from every analyzed module on
  every `index` run, whether reused or freshly parsed -- there is no
  partial graph diff. Correctness over micro-optimization: recomputing the
  graph is cheap relative to re-parsing every file at every measured
  repository size.
- **AI features depend on Anthropic's API being configured and reachable.**
  There is one concrete `AIProvider` implementation; no local/offline model
  path exists today.
- **Grounding constrains references, not natural language correctness.**
  A cited ID is guaranteed to be real; the sentence built around it is not
  independently fact-checked beyond what candidate-ID citation covers.
- **No database migration tooling.** A schema change requires re-indexing
  from scratch; there's no Alembic-style upgrade path.

## Roadmap

**Implemented:** deterministic scan/analyze/relationship pipeline over two
independently registered language analyzers (Python, TypeScript); evidence
model with fixed per-kind strength; candidate-ID grounding for
`ask`/`change`/`impact`; type-aware call resolution (constructor injection,
construction, dataclass attributes, factories, inheritance, a shared
self-reference check covering `self`/`cls`/`this`); explicit `UnresolvedCall`
facts for anything that doesn't resolve, persisted and logged, never
fabricated; two-granularity test-to-code linking; call-based API endpoint
detection (`add_api_route(...)`) alongside decorator-based detection;
multi-signal explainable retrieval with bounded relationship-chain
discovery and structured logging of *why* an entity was retrieved;
fingerprint-based drift detection and incremental re-analysis (unchanged
files reuse prior analysis; the relationship graph is always recomputed in
full); security boundary (secret exclusion, `.env*` content isolation,
bounded AI context); CLI with JSON output and documented exit codes; a
server-rendered web UI; an on-demand accuracy/hallucination/performance
benchmark harness covering two distinct-language fixtures.

**In progress / partial:** type-aware resolution covers common
dependency-injection and construction patterns, not general type
inference (and doesn't exist at all yet for TypeScript); test-to-code
linking is precise where a call resolves, coarse otherwise; the TypeScript
analyzer covers same-file resolution well and cross-file resolution not at
all (a disclosed gap, not a silent one).

**Planned (not started):** a real parser (`tree-sitter` or equivalent) for
TypeScript, if that dependency becomes installable/justified, to replace
the conservative scanner and add assignment/type-inference parity with
Python; hybrid retrieval (the current deterministic signals plus an
interface for semantic/vector similarity, without replacing or obscuring
the deterministic ones); a third language as further architecture
validation; a larger evaluation corpus beyond the current two fixtures.

## Development

Requires Python >= 3.12.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy src
```

If `uv` isn't on `PATH`, use the virtualenv's Python directly (Windows
paths shown; `.venv/bin/` on Linux/macOS):

```bash
".venv/Scripts/python.exe" -m pytest -q
".venv/Scripts/python.exe" -m mypy src scripts
".venv/Scripts/python.exe" -m ruff check .
```

Run the benchmark harness on demand (not part of the default test run):

```bash
python scripts/benchmark.py [accuracy|hallucination|performance|all]
```

### Configuration

- `CODEBASE_MANUAL_DATABASE_URL` -- any SQLAlchemy URL. Defaults to a
  SQLite file at `<repository>/.codebase_manual/index.db`. Point this at
  PostgreSQL in production (`uv sync --extra postgres`).
- `ANTHROPIC_API_KEY` -- required for `ask`/`change`, and for AI summaries
  in `manual`/`impact`. Without it, those commands still report the
  deterministic facts they have.
- `CODEBASE_MANUAL_AI_MODEL` -- overrides the default Anthropic model.
- `[tool.codebase-manual]` in `pyproject.toml` -- `python-source-roots`,
  `ignored-paths`, `ignored-extensions`, `max-file-size`,
  `max-context-size`. See [`docs/security.md`](docs/security.md).

### Project layout

```
src/codebase_manual/
    cli/            command-line interface (Typer)
    domain/         core entities, relationships, evidence, type inference
    repository/     repository scanning, Git metadata, Git history
    analyzer/       Python (ast) and TypeScript (scanner) analysis,
                    dispatched by a language registry, incrementally reused
    persistence/    SQLAlchemy schema, idempotent indexing, snapshot reads
    query/          retrieval, relationship graph traversal, drift detection
    ai/             provider interface + grounded Q&A/change planning/
                    impact analysis/manual generation (confidence + evidence)
    api/            FastAPI web UI (server-rendered, Jinja2)
```

## Architecture documentation

Start with [`docs/architecture.md`](docs/architecture.md) -- the
end-to-end pipeline; it links out to everything else. In build order:
[`docs/evidence-model.md`](docs/evidence-model.md),
[`docs/confidence.md`](docs/confidence.md),
[`docs/ai-grounding.md`](docs/ai-grounding.md) (the AI trust boundary),
[`docs/relationship-model.md`](docs/relationship-model.md) (includes
type-aware call resolution),
[`docs/retrieval.md`](docs/retrieval.md),
[`docs/indexing.md`](docs/indexing.md),
[`docs/security.md`](docs/security.md),
[`docs/cli.md`](docs/cli.md),
[`docs/performance.md`](docs/performance.md),
[`docs/analyzers.md`](docs/analyzers.md),
[`docs/testing.md`](docs/testing.md),
[`docs/contributing.md`](docs/contributing.md).
`docs/engineering-baseline.md` and `Context.md` (repository root) track
the project's build history and the reasoning behind decisions that
didn't make it into a topic-specific doc.

## Contributing

See [`docs/contributing.md`](docs/contributing.md) for setup, the
pre-PR checklist, and conventions worth knowing before changing this
codebase (why there's no `confidence` field on any LLM prompt schema, why
candidate IDs exist, why evidence strength is fixed per relationship kind).
The [Current limitations](#current-limitations) and
[Roadmap](#roadmap) sections above are the most concrete map of where
contributions would matter most -- particularly a real TypeScript parser
(replacing the conservative scanner), TypeScript assignment/type-inference
parity with Python, and hybrid retrieval's semantic-similarity interface.

## License

No license file is present in this repository. Treat it as unlicensed
(all rights reserved by default) until a `LICENSE` file is added.
