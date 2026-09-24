# Architecture: the end-to-end pipeline

This is the map. Each stage below has its own doc with the real detail --
this page exists to show how they connect and where each one lives in the
code, not to duplicate them.

```
Repository --scan--> Facts --analyze--> Facts --derive--> Evidence Graph
    |                                                          |
    +-- persist (SQLite/Postgres) <--------------------------- +
    |
    +-- retrieve (query) --> Candidate IDs --> LLM --> GroundingValidator --> Result
```

## 1. Repository -> Scanner

`repository.scanner.RepositoryScanner.scan()` walks the filesystem,
respecting `.gitignore` plus the always-on
`analyzer.config.DEFAULT_IGNORED_PATTERNS` and any repository-configured
`ignored-paths`/`ignored-extensions`/`max-file-size` (see
`docs/security.md`). For each surviving file it records a `FileRecord`:
path, size, language, a SHA-256 content hash when affordable and safe
(`FileHashStrategy.FULL_HASH`), or `METADATA_ONLY` for binaries, oversized
files, and `.env*` files (whose bytes are never read into memory at all).
It also reads Git metadata (`read_git_metadata`) to identify the
repository (`repository_identity`) and its commit.

Output: `ScanResult` (`domain.models`) -- files, directories, Git
metadata. Purely filesystem facts; no source code has been parsed yet.

## 2. Scanner -> Analyzer -> Facts

`analyzer.registry.analyze_repository` dispatches each file to a
registered `LanguageAnalyzer` by `FileLanguage` (today, only
`FileLanguage.PYTHON` has one -- see `docs/analyzers.md`). The Python
analyzer (`analyzer.python_analyzer.analyze_module`) parses the file with
`ast` and extracts structural facts -- module docstring, imports,
functions/classes/methods (with parameters, decorators, docstrings, and
scoped `CallSite`s), module-level variables -- into a `PythonModule`
(`domain.models`). A syntax error is recorded as `PythonModule.parse_error`,
never raised past this boundary: one unparseable file must not abort
indexing the rest of the repository.

`analyzer.config.detect_source_roots`/`AnalysisContext` resolve a `src/`
layout (or an explicit `[tool.codebase-manual] python-source-roots`
override) so `src/app/service.py` resolves to the module name `app.service`,
not `src.app.service`.

Output: `list[PythonModule]` -- one per analyzed file. Still just facts
about *one file at a time*; nothing here knows about any other file yet.

## 3. Facts -> Evidence Graph

`domain.relationships.build_relationships(modules)` is the only place
that looks at facts *across* files, deriving `Relationship`s
(`CONTAINS`/`IMPORTS`/`CALLS`/`INHERITS`/`TESTS`) purely from concrete
syntactic evidence -- a resolved import, a call expression that resolves
to a known symbol, a base class that resolves to a known class, a test
function's resolved call into non-test code. Ambiguous references (a call
through an arbitrary local variable, an unresolved base, an
instance-attribute call -- see `docs/performance.md`'s benchmark findings
for a concrete example of the last one) are **dropped, not fabricated**.
Every `Relationship` carries an `evidence` string and, for calls, the
exact `SourceLocation` of the call site.

**Known gap:** a dropped reference currently leaves no trace at all --
there is no `CALL_UNKNOWN` fact recording "a call happened here, but it
didn't resolve." `tests/unit/test_invariants.py` checks the *drop* half
of this ("never fabricated") but the *record it as unknown instead of
silently discarding it* half was never built -- it would need a new fact
type plus persistence plumbing. See `docs/cli.md`'s framework-detection
section for the one other feature blocked on the same underlying
`CallSite`-has-no-arguments limitation.

`domain.evidence.evidence_from_relationship` wraps a `Relationship` into a
structured `Evidence` record with a deterministically assigned
`EvidenceStrength` (`DIRECT`/`RESOLVED`/`INFERRED`/`UNKNOWN`, fixed per
relationship kind in `_RELATIONSHIP_STRENGTH` -- never a per-instance
judgment call). See `docs/evidence-model.md`.

Output: `list[Relationship]`, plus `list[FileRecord]` and
`list[PythonModule]` from steps 1-2 -- together these three lists are
everything `persistence.snapshot.RepositorySnapshot` holds, and everything
every layer downstream is allowed to treat as fact.

## Persist

`persistence.store.IndexStore.save(scan_result, modules, relationships)`
writes one `IndexRun` (one working copy, one commit) into SQLite (or
Postgres via `CODEBASE_MANUAL_DATABASE_URL`). `latest_snapshot(identity,
working_copy_root=...)` reconstructs a `RepositorySnapshot` from the most
recent run for that specific working copy. See `docs/indexing.md` for
identity/concurrency/fingerprinting/caching detail. Only `content_hash`
(a one-way SHA-256 digest) is ever persisted for a file -- never its raw
bytes; see `docs/security.md`.

## 4. Retrieval -> Candidate IDs

Everything past this point is per-query, not per-index. `query.retrieval.
retrieve_relevant(query, snapshot)` runs lexical name/docstring matching,
then expands one hop via the relationship graph and by directory
proximity, producing a bounded, weighted, *explainable* `RetrievalResult`
(every match carries `signals: list[MatchSignal]` saying why it was
retrieved). See `docs/retrieval.md`.

`query.candidates.build_candidate_set(retrieval)` assigns opaque IDs
(`FILE_001`, `SYMBOL_002`, `TEST_003`, ...) to what was retrieved. This is
the airlock: everything past this point that references a specific
repository entity does so **only** by one of these IDs, never by a raw
path or name the model could instead invent a plausible-sounding
substitute for.

## 5. LLM

`ai.qa.answer_question`, `ai.change_planner.plan_change`,
`ai.impact.analyze_impact`, and `ai.summarizer.summarize_file`/
`summarize_function` each build a prompt from retrieved/computed facts
(docstrings, signatures, call chains, dependency lists -- never raw file
content) via `ai.provider.AIProvider.complete`/`complete_json`, capped by
`ai.context_limits.truncate_context` (`docs/security.md`). The model
proposes prose and, for `ask`/`change`, selects among the candidate IDs
it was given. It is never asked to self-report a confidence level -- every
prompt schema in `ai/` has that field removed entirely, not just
discouraged (`docs/confidence.md`).

`ai.anthropic_provider.AnthropicProvider` is the only concrete provider;
it maps every `anthropic` SDK failure to a domain-level error
(`AIProviderNotConfiguredError`/`AIProviderError`/`AISynthesisError` --
`docs/cli.md`) before it can escape as a raw SDK exception.

## 6. GroundingValidator -> Result

`ai.grounding.GroundingValidator` resolves every candidate ID the model
referenced back against the same `CandidateSet` it was given. An invented
ID is rejected and quarantined (`ValidationVerdict.PARTIALLY_VALID`/
`INVALID`), never silently trusted into the result. `ai.confidence.
confidence_from_strengths` then computes a `Confidence` level from the
`EvidenceStrength` of whatever survived validation -- never read from the
model. See `docs/ai-grounding.md` and `docs/confidence.md`; see
`docs/performance.md` for measured numbers on how often this actually
catches something (33.3% unsupported-claim rate across the benchmark's
scripted hallucinating scenarios, 100% caught).

Output: `Answer` / `ChangePlan` / `ImpactReport` / `FileSummary` (`ai.models`)
-- each carrying `confidence`, `evidence`, and (for `Answer`/`ChangePlan`)
`grounding`. This is what `cli.main` and `api.app` render.

## Where each interface sits on top of this

- **CLI** (`cli.main`, `cli.context`): the pipeline above, one command per
  stage combination (`index` = steps 1-3 + persist; `ask`/`change`/
  `impact`/`manual` = load snapshot + steps 4-6). `docs/cli.md` covers
  exit codes, `--json`/`--verbose`/`--quiet`, and logging.
- **Web API** (`api.app`): the same `ai.*`/`query.*` functions, called
  from FastAPI route handlers instead of Typer commands, rendered as
  server-rendered HTML (Jinja2, `api/templates/`) rather than JSON --
  `docs/cli.md` explains why a separate JSON surface was deferred.
- **Benchmark harness** (`scripts/benchmark.py`): calls the same
  `query.retrieval`/`ai.grounding`/`domain.relationships`/`query.graph`
  functions directly, outside of both interfaces, to measure the pipeline
  rather than expose it -- `docs/performance.md`.
