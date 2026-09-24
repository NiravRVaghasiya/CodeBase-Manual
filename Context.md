# Context: Codebase-Manual upgrade progress

This file exists so a new session (human or agent) can pick up this work
without re-deriving it. It tracks *what has been done and why*, not
day-to-day task state -- see `docs/implementation-plan-remaining-phases.md`
for what's left.

## What this project is

`codebase-manual`: deterministic code analysis (Python AST) plus
AI-driven interpretation (Q&A, change planning, impact analysis,
summaries) of a software repository, with the explicit architectural
mandate that **every repository-specific fact must be traceable to
deterministic analysis; the AI explains and organizes facts, it cannot
manufacture them.**

The full engineering brief this work follows is the 40-section
instruction set the user provided at the start of this effort (not
reproduced here -- it lives in the conversation/session history). It
prescribes an 8-phase implementation order:

0. Baseline audit
1. Trust boundary (evidence model, candidate IDs, grounding validator,
   deterministic confidence)
2. Graph correctness (module resolution, call scoping, call sites, test
   detection)
3. Retrieval (multi-signal, relationship-aware)
4. Persistence/scalability (working-copy identity, concurrency,
   fingerprinting, AI caching)
5. Security
6. API/CLI
7. Benchmarking
8. Documentation

**All phases (0-8) are complete** as of this writing. See
`docs/implementation-plan-remaining-phases.md` for the full per-phase
record and the handful of items that were measured/documented but
deliberately not fixed (they're independent follow-up work, not part of
this 9-phase build).

## Environment gotcha (read this before running anything)

The repo exists in two checkouts on this machine:
`C:\Users\nrvhari\Desktop\Codebase-Manual` (older) and
`C:\Users\nrvhari\Desktop\AmazonQuick\Codebase-Manual` (current working
directory for this effort). The `.venv` under the `AmazonQuick` checkout
was copied from the other one, so its editable-install pointer
(`.venv/Lib/site-packages/_editable_impl_codebase_manual.pth`) originally
pointed at the *wrong* checkout's `src/` -- meaning tests would silently
run against stale code. This was fixed by editing that `.pth` file to
point at `C:\Users\nrvhari\Desktop\AmazonQuick\Codebase-Manual\src`. If
tests seem to ignore your edits, check that file first.

`uv` is not on PATH in this shell. Use the venv directly:
```
".venv/Scripts/python.exe" -m pytest -q
".venv/Scripts/python.exe" -m mypy src
".venv/Scripts/python.exe" -m ruff check .
```

Current gate status: **258/258 tests passing, mypy strict clean, ruff
clean.**

## Phase 0 — Baseline audit

Full results in `docs/engineering-baseline.md`. Headline finding: the
existing architecture (domain/repository/analyzer/persistence/query/ai/
cli/api) was sound and matched the brief's target pipeline shape, but the
AI trust boundary was essentially unbuilt (no evidence model, no
grounding validator, LLM self-reported its own confidence), plus two
confirmed analyzer correctness bugs (nested-function call leakage,
src-layout module names).

## Phase 1 — Trust boundary

**New files:**
- `domain/evidence.py` -- `Evidence`, `EvidenceType`, `EvidenceStrength`
  (DIRECT/RESOLVED/INFERRED/UNKNOWN), `evidence_from_relationship()`.
  Evidence is only ever built from an already-derived deterministic
  `Relationship`, never from an LLM string.
- `query/candidates.py` -- `CandidateSet`/`Candidate`/`build_candidate_set()`.
  Assigns opaque `FILE_xxx`/`TEST_xxx`/`SYMBOL_xxx` IDs to retrieval
  results; the LLM may only reference entities by these IDs.
- `ai/grounding.py` -- `GroundingValidator`, `ValidationVerdict`
  (VALID/PARTIALLY_VALID/INVALID), `combine_verdicts()`. Resolves
  candidate IDs and proposed-new-file paths against deterministic facts;
  invalid references are quarantined, not silently trusted.
- `ai/confidence.py` -- `confidence_from_strengths()`,
  `confidence_for_summary()`. Confidence is *computed* from evidence
  strength, never read from the model.

**Key design decision:** every LLM-facing JSON schema in `ai/qa.py`,
`ai/change_planner.py`, `ai/impact.py`, `ai/summarizer.py` had its
`confidence` field **removed entirely** -- it's structurally impossible
for the model to set it, not just discouraged by prompt wording.
`ai/change_planner.py` and `ai/qa.py` now require candidate IDs (not raw
paths/names) for existing-entity references; `files_to_create` in
`ChangePlan` stays free-text (new files can't have a candidate ID) but is
validated as *not already existing*.

**Docs written:** `evidence-model.md`, `confidence.md`, `ai-grounding.md`.

## Phase 2 — Graph correctness

**New files:**
- `analyzer/config.py` -- `AnalysisContext`, `detect_source_roots()`.
  Auto-detects a conventional `src/` layout, or reads
  `[tool.codebase-manual] python-source-roots` from `pyproject.toml`.

**Key fixes in `analyzer/python_analyzer.py`:**
- `infer_module_name(path, source_roots)` strips the source root prefix
  so `src/app/service.py` -> `app.service`, not `src.app.service`.
- Replaced unrestricted `ast.walk()` (which leaked nested-function calls
  into the parent) with `_ScopedCallVisitor`, which stops at
  `def`/`async def`/`class`/`lambda` boundaries. Decorator expressions and
  parameter defaults/annotations of a nested def still count in the
  *enclosing* scope (they execute there).
- Nested functions/classes (at any depth, including inside
  `if`/`for`/`try` blocks) are now extracted as their own
  `FunctionSymbol`/`ClassSymbol` entries with qualified names like
  `module.outer.inner`, via `_extract_nested_defs`/`_iter_scope_defs`.
- Added `CallSite` (expression, line, column, containing_symbol_id) --
  `FunctionSymbol.calls` is now `list[CallSite]`, not `list[str]`. The
  `CALLS` relationship's evidence/location now points at the actual call
  site, not the containing function's `def` line.

**Key fix in `domain/relationships.py`:** `_tests_relationships` no
longer asserts `TESTS` from "test module imports X" alone -- it requires
a resolved call/construction from a test function into the target module
(same resolution path as `CALLS`). This bumped `TESTS`'s evidence
strength from INFERRED to RESOLVED in `domain/evidence.py`.

**Key fix in `query/graph.py`:** `transitive_dependents_traversal()`
returns a `Traversal(entities, truncated)` -- `truncated=True` when BFS
hit `max_depth` with more left unexplored. `ai/impact.py` surfaces this
and caps confidence at MEDIUM when truncated (an incomplete picture can't
be HIGH confidence).

**Fixtures:** added `tests/fixtures/src_layout/` +
`tests/integration/test_src_layout_fixture.py`.

**Docs written:** `relationship-model.md`.

## Phase 3 — Retrieval

**Rewrote `query/retrieval.py`** from flat keyword-overlap-count scoring
to multi-signal, explainable, weighted retrieval:

| Signal | `MatchReason` | Weight |
|---|---|---|
| Exact bare-name match | `NAME_EXACT` | 5 |
| Qualified-name/path component match | `QUALIFIED_NAME` | 4 |
| Docstring match | `DOCSTRING` | 3 |
| Graph-connected (CALLS/IMPORTS/INHERITS/TESTS, 1 hop) | `RELATIONSHIP` | 2 |
| Same directory as a matched file | `DIRECTORY_PROXIMITY` | 1 |

Every `RetrievedFile`/`RetrievedSymbol` carries `signals: list[MatchSignal]`
with a human-readable `detail` -- retrieval always answers "why was this
retrieved." Also added `relationship_chains: list[RelationshipChain]`:
bounded forward-`CALLS` path search from top matches (e.g.
`AuthRouter.login --calls--> AuthService.authenticate --calls-->
UserRepository.find_user`), so "how does X flow" questions get an actual
call-flow answer instead of forcing the LLM to guess architecture from
isolated files.

`ai/change_planner.py`'s bespoke `_sibling_files()` helper was removed --
directory proximity is now retrieval's job, and sibling files get real
candidate IDs (previously just inert text in the prompt).

**No new correctness bugs found this phase** -- purely additive capability
on top of already-correct Phase 1/2 work.

**Docs written:** `retrieval.md`.

## Phase 4 — Persistence/scalability

**Fixed the large-file/binary fingerprinting bug:** files were
unconditionally `content_hash=None` if binary or >5MB, and
`detect_drift` treated *any* missing hash as "changed" -- so such files
showed as changed on every single run, forever. Added
`FileHashStrategy` (`FULL_HASH`/`METADATA_ONLY`) + `FileRecord.mtime`;
drift detection falls back to `size_bytes`/`mtime` comparison instead of
assuming change.

**Renamed "documentation drift" to "index drift"** (`DriftReport` ->
`IndexDriftReport`, `detect_drift` -> `detect_index_drift`, CLI text
fixed) -- it only ever compared file fingerprints, never doc-vs-code
semantics, so it's now named for what it does.

**Added working-copy identity:** `WorkingCopyORM` (one checkout on disk)
is now distinct from `RepositoryORM` (logical repo by remote URL or
root). `IndexStore.latest_snapshot(identity, working_copy_root=...)`
prefers the requested working copy's own latest run instead of whichever
working copy indexed most recently -- fixes two clones of the same remote
silently overwriting each other's "latest."

**Added DB concurrency safety:** `_get_or_create_repository`/
`_get_or_create_working_copy` catch `IntegrityError` from the unique
constraint and recover by re-querying the concurrent writer's row;
`save()` retries once on a commit-dedup race. Verified with deterministic
race-simulation tests (two `Session`s manually interleaved -- real
thread-timing tests are unreliable against SQLite's file-level locking).

**Added AI summary caching:** `ai/summary_cache.py` keys a cached
`FileSummary` on content hash + prompt version + `provider.model_identifier`
+ analyzer version. Required adding `model_identifier` as a required
property on the `AIProvider` protocol (implemented on `AnthropicProvider`;
test stub providers that don't exercise the caching path didn't need
updating -- only `test_manual.py`'s stub did, since only
`ai/manual.py`/`generate_manual` actually reads it). `IndexStore` gained
generic `get_cached_value`/`set_cached_value` methods backed by a new
`ai_cache` table -- kept generic (opaque payload) so the persistence layer
doesn't need to depend on `ai.models`; `ai/summary_cache.py` does the
typed serialize/deserialize.

**Schema note:** this phase added columns/tables (`working_copies`,
`ai_cache`, new `files` columns) with no migration tooling -- any existing
local SQLite index needs re-indexing. Documented as a known gap, not
fixed (Alembic would be disproportionate at this project's size).

**Docs written:** `indexing.md`.

## Phase 5 — Security

**New files:**
- `ai/context_limits.py` -- `truncate_context()`. A hard character ceiling
  on the assembled prompt text `ai/qa.py`/`ai/change_planner.py` build, with
  an explicit `[... context truncated at N characters ...]` marker rather
  than a silent cutoff.
- `docs/security.md` -- the full data-flow writeup (what's read, what's
  sent, what's stored, what's excluded and why).
- `tests/integration/test_security_boundaries.py` -- the phase's
  acceptance test: a repo with `.env`, an RSA key, and `node_modules/` is
  scanned/analyzed/persisted/queried, and the test asserts the secrets
  never reach a raw SQLite file's bytes or an AI prompt.

**Key additions to `analyzer/config.py`:** `SecurityConfig` (`ignored_paths`,
`ignored_extensions`, `max_file_size`, `max_context_size`) and
`load_security_config()`, read from the same `[tool.codebase-manual]`
pyproject table as `python-source-roots`. `DEFAULT_IGNORED_PATTERNS`
(`*.pem`, `*.key`, `*.p12`, `credentials.*`, `secrets/`, `node_modules/`,
`vendor/`) are always excluded from scanning, on top of `.gitignore` and
any repository-specific `ignored-paths`.

**Key fix in `repository/scanner.py`:** `RepositoryScanner` now takes an
optional `SecurityConfig` (defaults via `load_security_config`), applies
`ignore_patterns`/`ignored_extensions` during the walk, and the old
hardcoded `_MAX_HASHABLE_BYTES` module constant became the configurable
`SecurityConfig.max_file_size` (this also resolves the "hash-threshold
configurability" item deferred from Phase 4). **`.env*` files are now
always `METADATA_ONLY`** regardless of size -- `_fingerprint` never reads
their bytes into memory at all. This was the resolved design decision for
the "should `.env*` be scanned-but-never-sent-to-AI, or excluded outright"
question the plan posed: keep `.env*` *paths* visible (the manual's
Configuration section depends on this) but make their *content*
structurally unreachable, rather than losing that visibility by excluding
them outright.

**`ai/qa.py` and `ai/change_planner.py`** now call
`load_security_config(Path(snapshot.repository_root))` and run their
assembled context through `truncate_context` before building the prompt.

**Logging:** audited, not changed -- no `typer.echo` site in `cli/main.py`
touches an API key, `.env` content, or raw source text (they print
AI-generated prose and fact counts only). No structured logger exists yet;
that's Phase 6. Documented as a rule of thumb in `docs/security.md` rather
than enforced in code, since there's nothing to enforce against yet.

**Decision NOT made this phase:** `.env`/`.env.*` were listed in the
brief's suggested default-ignore-patterns bullet, but adding them there
would have removed the manual's env-file-visibility feature entirely
(excluded files never appear in `ScanResult.files` at all). The
hashing-level exclusion above achieves the brief's actual goal --
`.env` content never reaches the AI or the database -- without that
regression. If this reasoning turns out to be wrong, `.env`/`.env.*` can be
added to `DEFAULT_IGNORED_PATTERNS` directly; nothing else depends on the
current split.

**Docs written:** `security.md`.

## Phase 6 — API/CLI

**New files:**
- `cli/exit_codes.py` -- `ExitCode` IntEnum (0 success, 1 user error, 2
  config error, 3 provider error, 4 internal error). `check`'s drift-found
  exit 1 is a documented exception (diff/grep convention), not the general
  "user error" 1.
- `logging_config.py` -- `configure_logging()`/`get_logger()`, gated on
  `--verbose`/`--quiet`, called once from `cli/main.py`'s `main()`
  callback.
- `docs/cli.md` -- exit codes, global flags, per-command JSON schemas, and
  the web-API/CALL_UNKNOWN deferral decisions below.
- `tests/unit/test_cli.py` (new) -- exercises the exit-code scheme and
  `--json` round-trips end to end via `typer.testing.CliRunner`, with a
  stub `AIProvider` monkeypatched onto `cli.main.get_provider`.
- `tests/unit/test_anthropic_provider.py` (new) -- SDK-exception-to-
  domain-error mapping (auth -> `AIProviderNotConfiguredError`, everything
  else -> `AIProviderError`), using real `httpx.Request`/`Response`
  objects to construct `anthropic` SDK exceptions.

**Key additions to `ai/provider.py`:** new `AIProviderError` (rate limit /
timeout / network failure -- a request-level failure, distinct from
`AIProviderNotConfiguredError`'s "nothing to retry, fix the config").
`ai/anthropic_provider.py`'s `complete()` now catches `anthropic.APIError`
and maps `AuthenticationError` -> `AIProviderNotConfiguredError`, everything
else -> `AIProviderError`, and logs request outcome/duration (never
prompt/response content). `ai/manual.py` and `api/app.py`'s AI-error
`except` clauses were extended to include the new `AIProviderError`.

**`cli/context.py` rewritten:** added `CliState` (`json_output`/`verbose`/
`quiet`, set once in `main()`'s callback, read via `cli_state(ctx)`),
`fail()` (prints a message, a traceback only if `--verbose`, then exits
with the given `ExitCode`), and `run_ai_call()` (generic -- `def
run_ai_call[T](...)`, PEP 695 syntax -- maps an AI-backed thunk's
exceptions to the exit-code scheme). `load_snapshot_or_exit`/
`resolve_entity_ref` now route through `fail()` instead of a bare
`typer.Exit(code=1)`.

**`cli/main.py` rewritten:** every command takes `ctx: typer.Context`;
`ask`/`change` call their AI logic through `run_ai_call` (hard-exits on
failure, exit code depends on failure kind); `impact` keeps its original
graceful-degrade-without-AI behavior for the three known AI error types
(deterministic facts stand alone) and only maps a genuinely unexpected
error to `INTERNAL_ERROR` (via a new public `confidence_for_impact_facts`
in `ai/impact.py`, renamed from `_confidence_for_facts` so the CLI's
fallback path can reuse it). `--json` support added for `index` (new
`IndexSummary` pydantic model, local to `cli/main.py` -- deterministic
counts only, no AI, kept out of `domain/models.py` since nothing else
consumes it), `ask`/`change`/`impact` (existing `ai.models` types),
`check` (`query.drift.IndexDriftReport`, converted from a dataclass to a
pydantic `BaseModel` for this). `manual`/`serve` are unaffected by `--json`
(documented why in `docs/cli.md`). `--quiet` suppresses each command's
secondary/framing output, never its primary result.

**`query/api_endpoints.py`:** added `@app.api_route(path, methods=[...])`
detection alongside the existing `<name>.<method>(path)` shape (`methods`
defaults to `["GET"]` per Starlette's own default); each `ApiEndpoint` now
carries `detection: EndpointDetection` recording *which regex shape*
matched, deliberately not a guessed framework name (FastAPI/Starlette
share this decorator syntax; naming one would be fabrication).
`router.add_api_route(...)` (a call-statement registration, not a
decorator) is still **not** detected -- it needs a call expression's
*arguments*, which `CallSite` doesn't capture; this is an analyzer gap,
tracked in `docs/cli.md` and the deferred-items table below.

**Decision NOT made this phase:** a typed JSON surface for `api/app.py`
(the web UI) was explicitly deferred -- the CLI's `--json` already
delivers the phase's actual goal (machine-readable output via the same
pydantic models), and the web UI's HTML-form interaction model doesn't fit
REST-style status codes anyway (see `docs/cli.md`'s reasoning). The
`CALL_UNKNOWN` fact-representation item carried forward from Phase 5 was
**also not done** -- it's what would be needed to detect
`add_api_route(...)` and to log "relationships unresolved" (see next
phase's deferred-items table).

**Docs written:** `cli.md`.

## Phase 7 — Benchmarking

**New files:**
- `scripts/benchmark.py` -- the on-demand harness (`python scripts/
  benchmark.py [accuracy|hallucination|performance|all]`). No API key
  needed anywhere in it -- accuracy/performance are pure deterministic
  code, hallucination/calibration use a scripted `StubProvider`. See its
  module docstring for exactly what that does and doesn't prove.
- `docs/performance.md` -- the full numbers writeup for all three
  categories (the brief's own phrasing names this one file as the
  reference target for accuracy/hallucination/performance together, so
  that's where all of it lives, not just perf).
- `tests/unit/test_invariants.py` -- property-based invariant tests, hand-
  rolled (no `hypothesis` dependency added). A seeded generator writes
  real Python source (deliberately including dangling calls/bases/imports)
  for 5 seeds x 2 sizes = 10 repositories, each run through the real scan/
  analyze/build_relationships pipeline; every relationship/entity across
  all 10 is checked against 5 invariants (session-scoped fixture so the
  10 repos are generated/analyzed once each, not once per test -- dropped
  runtime from 12s to 3.7s).

**Accuracy findings (against `tests/fixtures/fixture_project`, all hand-
verified against its actual source first -- see the script's docstring
comments):** retrieval recall is 1.00 across 3 queries, precision 0.67-
0.78 (relationship-expansion spillover, by design, not a bug). 5/5
relationship spot-checks passed, including one *expected-absent* check
that concretely demonstrates the still-deferred "call resolution levels
4-5" gap (`self._user_repository.get_or_create(...)`, an instance-
attribute call, has no resolved `CALLS` edge). Impact precision/recall is
1.00/1.00 at module granularity -- but a real, newly-documented finding:
`TESTS` only ever targets a module (never a class/function), so `impact`
against a class/function target returns an empty affected-tests list even
when tests clearly exercise it.

**Hallucination findings:** 33.3% unsupported-claim rate across 3
scripted hallucinating scenarios, 100% of invented IDs caught in every
one, cross-checked against the real `answer_question` code path (not
just a direct validator call). Two new pytest cases widen the net
permanently: an off-by-one ID (`FILE_002` when only `FILE_001` exists) is
rejected with no fuzzy leniency
(`test_qa.py::test_answer_question_rejects_a_plausible_looking_id_the_same_as_any_other`),
and hallucinations spread across three `ChangePlan` fields at once are
each quarantined independently
(`test_change_planner.py::test_plan_change_quarantines_invented_ids_independently_per_field`).
**Real, newly-documented gap:** `ai.impact.analyze_impact`'s free-prose
`explanation` has no candidate-ID citation mechanism at all -- its
unsupported-claim rate is structurally unmeasurable, not zero.

**Confidence-calibration finding:** `ask`/`change`'s confidence is
structurally `LOW`/`MEDIUM` only -- retrieval-cited evidence is always
`EvidenceStrength.INFERRED`, so `confidence_from_strengths` can never
return `HIGH` for those two commands (see `ai/confidence.py`). Only
`impact` (RESOLVED/DIRECT facts) can reach `HIGH`. This means "does HIGH
correlate with correctness" has no HIGH case to test for `ask`/`change` --
that's the honest answer for those commands, not a benchmark shortfall.

**Performance findings (synthetic linear-chain repos, one Windows dev
machine, real measured numbers -- see `docs/performance.md`'s table for
100/1,000/5,000/10,000/50,000 files):** scan+analyze scale roughly
linearly (~1.1 ms/file scan + ~1.5 ms/file analyze at 50,000 files);
relationship resolution/graph build/traversal are never the bottleneck at
any measured size; peak memory (via `tracemalloc`) is roughly 25 KB/file
with no superlinear growth through 50,000 files; full end-to-end
scan+analyze+relationships for 50,000 files takes ~2 minutes. No
pre-optimization recommended by these numbers.

**Fixture-breadth decision:** none of the plan's up-to-13 dedicated
fixture directories were built. Checked first that `relative_imports`/
`aliased_imports`/`nested_functions`/`nested_classes`/`decorators`/
`async`/`malformed_python` are already unit-tested directly in
`test_python_analyzer.py`, and that `inheritance`/`pytest`-style/`fastapi`-
shaped decorators already exist in `fixture_project` (reused as this
phase's benchmark repo rather than duplicated). `large_repository` is what
the performance benchmark's synthetic generator is for. `dynamic_dispatch`
and `unittest`-style are the one real gap, deferred until call-resolution
levels 4-5 are actually worked on (see `docs/performance.md`'s "Fixture
breadth: decision" section for the full reasoning).

**Docs written:** `performance.md`.

## Phase 8 — Documentation

Documentation-only phase -- no `src/`/`tests/` changes, gate stays at
258/258 (the same number Phase 7 ended on).

**New docs:** `architecture.md` (the end-to-end pipeline, cross-
referencing every phase's own doc rather than duplicating them --
Repository -> Scanner -> Analyzer -> Facts -> Evidence Graph -> Retrieval
-> Candidate IDs -> LLM -> GroundingValidator -> Result, plus where
CLI/web API/benchmark harness each sit on top of it), `analyzers.md`
(the `AnalyzerRegistry`/`LanguageAnalyzer` protocol, what the Python
analyzer actually extracts, and a concrete numbered list of what a second
language would need -- including the currently-undone rename of
`PythonModule` to something language-neutral, flagged as expected future
work, not done preemptively), `contributing.md` (dev setup including the
venv-editable-path gotcha, pre-PR checklist, and the "conventions worth
knowing" list lifted from this file's own "Conventions established"
section below), `testing.md` (suite layout, why `tests/fixtures` is
`--ignore`d by pytest, unit-vs-integration-vs-benchmark, and a concrete
"how to add a grounding test" recipe with the exact json.dumps-not-an-
f-string gotcha `scripts/benchmark.py` hit during Phase 7).

**README.md updated** (not originally listed in the Phase 8 doc list, but
stale relative to Phases 5-7's actual behavior): fixed "documentation
drift" wording to "index drift" (renamed in Phase 4, README never caught
up), added the `--json`/`--verbose`/`--quiet`/exit-code mention (Phase 6),
added the `[tool.codebase-manual]` security config surface (Phase 5), and
added a documentation index linking every `docs/*.md` in build order.

**Nothing was fixed this phase** -- by design; Phase 8 is documentation
only. The three carried-forward items (`CALL_UNKNOWN` representation,
`router.add_api_route(...)` detection, call resolution levels 4-5) remain
open, now documented as explicit "known gap"/"future work" sections in
`architecture.md`, `analyzers.md`, and `performance.md` respectively,
rather than scattered only across `Context.md` and the phase-plan file.

**Docs written:** `architecture.md`, `analyzers.md`, `contributing.md`,
`testing.md`.

## Where things stand file-by-file (new since the original baseline)

```
src/codebase_manual/
  ai/confidence.py          Phase 1
  ai/grounding.py           Phase 1
  ai/summary_cache.py       Phase 4
  ai/context_limits.py      Phase 5
  analyzer/config.py        Phase 2 (extended Phase 5: SecurityConfig)
  domain/evidence.py        Phase 1
  query/candidates.py       Phase 1
  cli/exit_codes.py         Phase 6
  logging_config.py         Phase 6

docs/
  engineering-baseline.md                   Phase 0
  evidence-model.md                         Phase 1
  confidence.md                             Phase 1
  ai-grounding.md                           Phase 1
  relationship-model.md                     Phase 2
  retrieval.md                              Phase 3
  indexing.md                               Phase 4
  security.md                               Phase 5
  cli.md                                    Phase 6
  performance.md                            Phase 7
  architecture.md                           Phase 8
  analyzers.md                              Phase 8
  contributing.md                           Phase 8
  testing.md                                Phase 8
  implementation-plan-remaining-phases.md   (this session)

scripts/benchmark.py                        Phase 7

tests/fixtures/src_layout/                  Phase 2
tests/integration/test_src_layout_fixture.py Phase 2
tests/integration/test_security_boundaries.py Phase 5
tests/unit/test_cli.py                      Phase 6
tests/unit/test_anthropic_provider.py       Phase 6
tests/unit/test_invariants.py               Phase 7
```

Every other `src/`/`tests/` file touched across Phases 1-4 was modified
in place (see `git status`/`git log` for the exact diff -- nothing has
been committed yet, so the full cumulative diff is sitting in the working
tree relative to the initial commit `3a2bac5`).

## Conventions established, worth preserving

- **No `confidence` field in any LLM-facing prompt schema.** If you add a
  new AI-backed feature, don't add one -- compute confidence from
  evidence strength via `ai/confidence.py` instead.
- **Candidate IDs, not raw strings**, for any structured LLM output that
  references an existing repository entity. Build a `CandidateSet` from
  retrieval, validate through `GroundingValidator`.
- **`EvidenceStrength` is a property of *how* a relationship kind is
  derived**, fixed in `domain/evidence.py`'s `_RELATIONSHIP_STRENGTH` map
  -- not a per-instance judgment call. If you make a relationship kind's
  derivation more rigorous (like `TESTS` in Phase 2), bump its strength
  there and update the confidence docs.
- **Deterministic layers never import from `ai/`.** `ai/` depends on
  `persistence.snapshot`/`domain`/`query`, never the reverse (the one
  close call was AI summary caching -- resolved by keeping
  `persistence.store`'s cache methods generic/untyped rather than
  depending on `ai.models.FileSummary`).
- Every new module-level docstring in this codebase explains *why*, not
  just what -- keep doing that; it's what makes `docs/*.md` easy to write
  accurately.

## Next step

All 9 phases (0-8) of the original brief are complete. There is no next
phase -- `docs/implementation-plan-remaining-phases.md` now describes
finished work, not a queue; its status line reflects that ("Phases 1-8
complete"). Anyone picking this project up next is doing independent
follow-up work, not continuing a phase plan.

Three items are still carried forward and unresolved -- deliberately
measured/documented rather than fixed, since none of them fit any
remaining phase's scope. The first two stem from the same root cause --
`domain.models.CallSite` doesn't capture a call expression's *arguments*,
only the callee text/line/column:
1. The `CALL_UNKNOWN` fact representation (unresolved calls are dropped
   silently rather than recorded) -- slotted into Phase 5, then Phase 6,
   then Phase 7, and landed in none of them. Phase 7's invariant tests
   *check* that unresolved calls are dropped rather than fabricated
   (`test_dangling_calls_and_bases_are_dropped_not_fabricated`) but don't
   add the `CALL_UNKNOWN` representation itself -- that's still a real
   fact-model gap, not just a test gap. Documented as a "known gap" in
   `docs/architecture.md` (evidence-graph section) now.
2. `router.add_api_route(path, handler, methods=[...])` API-endpoint
   detection (a call-statement registration, not a decorator) -- blocked
   on the same `CallSite` argument-capture gap. Documented in
   `docs/cli.md`.
3. Call resolution levels 4-5 (instance-attribute / type-aware inference)
   -- concretely demonstrated in Phase 7 by `scripts/benchmark.py`'s
   relationship checks (see the Phase 7 section above):
   `self._user_repository.get_or_create(...)` has no resolved `CALLS`
   edge. Also blocks `impact`'s empty-affected-tests-for-a-class-or-
   function-target finding from the same section, and is the prerequisite
   for a real `dynamic_dispatch`/`unittest` fixture. Documented in
   `docs/performance.md`'s fixture-breadth decision and
   `docs/architecture.md`.

If argument capture on `CallSite` ever gets built for item 1 or 2, do
both at once -- it's the same underlying analyzer change. Item 3 is a
separate, larger piece of work (real type inference). None of these are
blocking anything currently shipped; they're documented limitations, not
open bugs.
