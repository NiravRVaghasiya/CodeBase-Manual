# Engineering Baseline

Snapshot of the Codebase-Manual repository before the evidence-grounding upgrade described in the project brief. This reflects actual behavior as of commit `3a2bac5`, not intended future behavior.

## Quality gates

| Gate | Result |
|---|---|
| Tests | 92/92 passing (`tests/unit`, `tests/integration`) |
| mypy | Clean, strict mode, 35 files, 0 errors |
| ruff | Clean, 0 violations |
| Package/build validation | Not run — `build`/equivalent not installed in the venv; no CI step exists yet |

The codebase is small, clean, and well-typed. The existing architectural seams (`domain` / `repository` / `analyzer` / `persistence` / `query` / `ai` / `cli` / `api`) already match the target pipeline shape in the brief and should be preserved and extended, not replaced.

## Current supported scope

- Language: Python only, single `PythonAnalyzer` behind a `LanguageAnalyzer` registry (`analyzer/registry.py`) — a sound extension seam, just not yet exercised by a second language.
- Frameworks: no framework-specific analysis; API-endpoint detection is a single generic regex over `.get(`/`.post(`/etc. calls, not FastAPI/Starlette-aware.
- Persistence: SQLAlchemy ORM over SQLite or PostgreSQL; `Repository` + `IndexRun` modeled, no `WorkingCopy` concept.
- AI: single Anthropic provider, JSON-mode structured outputs for Q&A, change planning, impact analysis, manual/summarization.
- Fixtures: one flat-layout fixture project; no src-layout, nested-function, decorator, pytest-pattern, FastAPI, dynamic-dispatch, malformed-Python, or large-repo fixtures.

## Status by spec area

| # | Area | Status | Evidence |
|---|---|---|---|
| 3 | Evidence model (typed, strength-graded) | ABSENT | `Relationship.evidence` is a free-text string (`domain/models.py:198`); `EvidenceItem` (`ai/models.py:27-30`) has no `EvidenceType`/`EvidenceStrength`/entity refs — just `description`/`file_path`/`line`. |
| 4 | Evidence graph as first-class structure | PARTIAL | `RelationshipGraph` (`query/graph.py`) is a real adjacency index over deterministic `Relationship`s, but relationships carry string evidence, not structured `Evidence` records. |
| 5 | src/ layout module resolution | ABSENT | `infer_module_name` (`analyzer/python_analyzer.py:28-44`) is pure path-to-dots; `src/pkg/mod.py` → `src.pkg.mod`, not `pkg.mod`. No source-root detection, no override config, no test coverage. |
| 6 | Scope-aware call extraction | ABSENT (confirmed bug) | `_extract_calls` (`analyzer/python_analyzer.py:145-152`) calls `ast.walk(node)` on the whole function body, descending into nested `FunctionDef`/`Lambda`/`ClassDef` — calls inside `inner()` get attributed to `outer()` too. |
| 7 | Real call-site locations | ABSENT | `calls: list[str]` on `FunctionSymbol` (`domain/models.py:75`) is callee text only; the `CALLS` relationship's location is the containing function's location (`domain/relationships.py:335`), not the call site. No `CallSite` model exists. |
| 8 | Progressive call resolution (levels 1-5) | PARTIAL | `_resolve_call` (`domain/relationships.py:260-279`) covers local functions, `self./cls.` methods, and imported names — roughly levels 1-3. No type-aware inference; unresolved calls are dropped silently rather than recorded as `CALL_UNKNOWN`. |
| 9 | Test relationship detection | ABSENT (confirmed anti-pattern) | `_tests_relationships` (`domain/relationships.py:342-373`) asserts `TESTS` purely from "test module imports X" — the exact pattern the brief forbids. `tests/unit/test_relationships.py:121` only sets up an import, no call. |
| 10 | Framework-aware API detection | PARTIAL | `query/api_endpoints.py` uses one regex matching any `x.get(...)` decorator — no `@app.api_route`, no `router.add_api_route(...)`, no verification the target is actually FastAPI/Starlette, no framework tagging. |
| 11-12 | Multi-signal, relationship-aware retrieval | ABSENT | `query/retrieval.py` is single-signal keyword-overlap only. No relationship expansion, no directory proximity, no semantic stage. `ai/qa.py` / `ai/change_planner.py` never pull in graph edges. |
| 13 | Grounding validator | ABSENT | No such component anywhere. LLM JSON payloads are parsed straight into domain models with no check that referenced paths/symbols exist in the snapshot. |
| 14 | Candidate-ID constraining of LLM output | ABSENT | `ai/change_planner.py:101-110,143-145` — `files_to_modify` / `files_to_create` / `relevant_symbols` / `tests_to_update` are raw LLM strings, unvalidated against the snapshot. |
| 15 | Evidence-derived confidence | ABSENT | `ai/qa.py:82`, `ai/change_planner.py:106,147`, `ai/impact.py:94`, `ai/summarizer.py:89,116` all do `Confidence(payload["confidence"])` — 100% LLM self-reported. |
| 16 | Q&A grounding | PARTIAL | `ai/qa.py` retrieves bounded facts and instructs the model not to guess, but answers are never checked post-hoc for invented names, and relationship evidence isn't included. |
| 17 | Change plan existing-vs-proposed distinction | ABSENT | `FileAction.MODIFY`/`CREATE` exists as intent (`ai/models.py:22-24`) but nothing verifies MODIFY targets an existing file or CREATE targets a new path. |
| 18 | Impact traversal depth/truncation reporting | PARTIAL | `transitive_dependents(ref, max_depth=5)` (`query/graph.py:48-67`) bounds depth but never reports whether the bound was hit. |
| 19 | Repository/WorkingCopy/IndexRun identity | PARTIAL | `RepositoryORM`/`IndexRunORM` (`persistence/orm.py:21-56`) model Repository+IndexRun. Identity is only `remote_url or root` (`repository/scanner.py:114-116`); no `WorkingCopy` concept, so two clones or two path-colliding non-git dirs collide. |
| 20 | Database concurrency | PARTIAL | `IndexRunORM` has `UniqueConstraint(repository_id, commit_sha)` (`orm.py:36`), but `IndexStore.save` (`persistence/store.py:66-77`) does check-then-insert with no `IntegrityError` handling or upsert; concurrent first-index races. |
| 21 | Large-file fingerprinting | ABSENT (confirmed bug) | `_content_hash` (`repository/scanner.py:57-63`) returns `None` above 5MB; `detect_drift` (`query/drift.py:41`) treats `None` as always-changed — large files permanently show as changed. |
| 22 | "check" command naming | Mislabeled, confirmed | `query/drift.py`'s own docstring says it says nothing about documentation staleness; CLI (`cli/main.py:177-195`) reports it as "Documentation drift detected." |
| 23 | AI summary caching | ABSENT | `ai/manual.py` calls `summarize_file` unconditionally every run — no cache keyed by content hash + prompt version + model. |
| 24 | AI provider error handling | PARTIAL | `anthropic_provider.py` handles missing API key and missing package only. Rate limits, timeouts, network errors, and schema mismatches propagate as raw SDK exceptions. |
| 25 | Secret/path exclusion | ABSENT | `.env*` files are tagged (`scanner.py:43-44`) but never excluded from scanning/hashing. No configurable ignore list, no `docs/security.md`. |
| 26 | Multi-language analyzer registry | PRESENT | `analyzer/registry.py` cleanly separates the `LanguageAnalyzer` protocol from dispatch; good seam to build on. |
| 27 | Fixture suite breadth | ABSENT | Single flat-layout fixture; none of the required categories (src_layout, nested_functions, pytest, fastapi, dynamic_dispatch, malformed_python, large_repository, etc.) exist. |
| 28 | AI grounding / hallucination tests | ABSENT | No validator exists, so no tests exercise it. |
| 32 | API quality | PARTIAL | Server-rendered HTML only; reasonable 409 handling for un-indexed repos (`api/app.py:54-58`); no pagination anywhere. |
| 33 | CLI exit codes / --json / --verbose | ABSENT | Every failure path uses `typer.Exit(code=1)` regardless of cause; no `--json`/`--verbose`/`--quiet` flags. |
| 34 | Structured observability logging | ABSENT | No `logging` module usage in `src/codebase_manual`; only human-readable `typer.echo`. |

## Top-10 highest-leverage gaps

1. No grounding validator + unvalidated LLM strings become facts (`ai/change_planner.py:101-145`) — the single biggest hallucination surface; directly violates the project's stated goal.
2. Nested-function call leakage (`analyzer/python_analyzer.py:147`, `ast.walk` crossing scopes) — corrupts the CALLS graph for any file with closures/nested defs.
3. TESTS relationship asserted from imports alone (`domain/relationships.py:342-373`) — systematically over-asserts test coverage with no behavioral evidence.
4. LLM self-reported confidence trusted everywhere (`ai/qa.py:82`, `ai/impact.py:94`, etc.) — contradicts the brief's confidence requirement.
5. src/ layout module-name bug (`analyzer/python_analyzer.py:28-44`) — mis-derives module names for one of the two most common Python layouts.
6. Call-site location is the whole function, not the call expression (`domain/relationships.py:335`) — undermines explainability for every CALLS edge.
7. Large files permanently "changed" (`repository/scanner.py:57-63` + `query/drift.py:41`) — `check` becomes noise on any repo with files >5MB.
8. Repository identity collisions (`repository/scanner.py:114-116`) — no WorkingCopy concept.
9. Retrieval is single-signal keyword overlap (`query/retrieval.py`) — architectural questions get no relationship-graph evidence.
10. AI provider error handling covers 2 of ~8 required failure modes (`ai/anthropic_provider.py`) — rate limits/timeouts/network errors surface as raw tracebacks.

## Other observations

- No dead code or duplicated functionality found; the codebase is unusually lean for its scope.
- README's "Known scope limits" section is accurate and matches actual behavior — no doc/behavior mismatch found there.
- `docs/` exists but was empty prior to this file — none of the required docs (architecture, evidence-model, security, etc.) exist yet.
