# Benchmarking: accuracy, hallucination resistance, performance

This is the Phase 7 report: measured, reproducible numbers for "is this
actually accurate, and how much does it hallucinate," per
`docs/implementation-plan-remaining-phases.md`. Every number below was
produced by `scripts/benchmark.py`, run on demand (not part of the default
`pytest` gate -- it's a report, not a correctness check):

```
python scripts/benchmark.py accuracy
python scripts/benchmark.py hallucination
python scripts/benchmark.py performance --sizes 100 1000 5000 10000 50000
python scripts/benchmark.py all
```

No `ANTHROPIC_API_KEY` is required for any of this -- see "What this
harness does and doesn't measure" below.

## Fixture breadth: decision

The plan floated up to thirteen dedicated fixture directories (relative
imports, aliased imports, nested functions/classes, inheritance,
decorators, async, pytest, unittest, fastapi, dynamic dispatch, malformed
Python, large repository). None of those thirteen were built. Checked
first: `relative_imports`, `aliased_imports`, `nested_functions`,
`nested_classes`, `decorators`, `async`, and `malformed_python`'s
behaviors are already covered directly in
`tests/unit/test_python_analyzer.py` (e.g.
`test_analyze_module_extracts_relative_import`,
`test_analyze_module_reports_a_parse_error_without_raising`) without a
fixture directory. `inheritance`, `pytest`-style tests, and FastAPI-shaped
route decorators are all already present in
`tests/fixtures/fixture_project` (`GithubProvider(OAuthProvider)`,
`@pytest.mark.asyncio` tests, `@router.post(...)`) -- reused as this
phase's benchmark repository rather than duplicated into a new fixture.
`large_repository` is what the performance benchmark's synthetic
generator is for -- a fixture directory checked into the repo isn't the
right shape for "arbitrarily many files," a generator is.

Type-aware call resolution (constructor-injected dependencies, direct
construction, dataclass-style attributes, factories, inheritance --
`domain.type_inference`) was built after this phase and follows the same
precedent: covered directly in `tests/unit/test_relationships.py` (e.g.
`test_calls_relationship_resolves_constructor_injected_attribute_call`)
rather than a new fixture directory, and cross-checked against the real
fixture project via the "Relationship checks" row above. `unittest`-style
tests (`self.assertEqual` on a `unittest.TestCase` subclass, rather than a
bare `assert` in a pytest function) remain untested -- `_is_test_module`
detects them by path/filename the same as pytest-style, but no fixture
exercises a `TestCase` method specifically. Genuine *dynamic dispatch*
(the actual method invoked depends on a runtime value, e.g. a factory
selecting between subclasses based on a config flag) is not a deferred
fixture gap -- it's statically undecidable in general, which is exactly
why this resolver stops at "assign one determinable type," never "guess
which subtype at runtime."

## Accuracy: second-language corpus (`tests/fixtures/typescript_project`)

`scripts/benchmark.py accuracy` now also runs a small relationship-check
section against `tests/fixtures/typescript_project` (`print_typescript_accuracy_report`,
`TYPESCRIPT_RELATIONSHIP_CHECKS`) -- diversifying the accuracy corpus beyond
one hand-crafted Python fixture, per the same "measure it, don't just claim
it" standard the rest of this document holds itself to. 4/4 checks pass:
`INHERITS` (`extends`), same-class `CALLS` via `this.`, inherited-method
`CALLS` via `this.` (inheritance-aware dispatch, unmodified from the Python
resolution path), and one deliberately-expected-**absent** check --
`buildRepository()`'s cross-file `new UserRepository()` construction, which
cannot resolve given `_resolve_import_module`'s Python-package-relative
import logic (`docs/analyzers.md`) -- confirming that gap fails closed as
an `UnresolvedCall`, not a fabricated relationship. This is a small corpus
(two files) proportionate to the TypeScript analyzer's own proof-of-
architecture scope, not a claim of general TypeScript accuracy.

## Accuracy (against `tests/fixtures/fixture_project`)

The harness's expected/relevant sets are hand-verified against the
fixture's actual source (imports, calls, inheritance), not guessed -- see
`scripts/benchmark.py`'s `RETRIEVAL_SCENARIOS`/`RELATIONSHIP_CHECKS` for
the exact reasoning behind each one.

### Retrieval precision/recall

| Query | Precision | Recall |
|---|---|---|
| "how does login with a provider work" | 0.75 | 1.00 |
| "user repository" | 0.88 | 1.00 |
| "authentication service" | 0.75 | 1.00 |
| **mean** | **0.79** | **1.00** |

**Finding:** recall is perfect across all three queries -- retrieval never
missed a genuinely relevant file in this fixture. Precision is
consistently 0.75-0.88 (up from an earlier-measured 0.67-0.78: type-aware
call resolution, below, resolves more real `CALLS` edges, which shifts
1-hop relationship expansion's results -- more of what it now pulls in is
genuinely connected), and every remaining miss traces to the same cause:
`query.retrieval._expand_via_relationships` pulling in a file connected to
a lexical match but not actually on-topic (e.g. `config/settings.py`
surfaces for "login" because `get_database()` transitively calls
`get_settings()`, three hops from the login handler). This is retrieval
behaving exactly as designed -- favoring recall (never silently missing
something connected) at a measured, bounded precision cost -- not a bug.

### Relationship checks

5/5 passed. `AuthService.login_with_provider` now has a resolved `CALLS`
edge to `UserRepository.get_or_create`: `self._user_repository.
get_or_create(...)` is an instance-attribute call, and `domain.
type_inference` resolves `_user_repository`'s type from the
constructor-injected parameter it was assigned from
(`__init__(self, user_repository: UserRepository)`, then
`self._user_repository = user_repository`) -- see
`docs/relationship-model.md`'s "Type-aware call resolution" section. This
was previously a deliberately-expected-absent check documenting a real,
measured recall gap ("call resolution levels 4-5"); it is closed for this
concrete shape (constructor-injected dependency, direct attribute
assignment). It is not a general type checker -- an attribute set through
an unrecognized shape (e.g. `self.factory()` where `factory` is itself a
method, or a dynamically computed callee) still resolves to nothing rather
than a guess.

### Impact precision/recall

Module-level target (`app.users.repository`): direct-dependent
precision/recall = 1.00/1.00, affected-tests precision/recall = 1.00/1.00.

**`TESTS` now also targets functions/classes, not only modules** (see
`docs/relationship-model.md`) -- `domain.relationships._tests_relationships`
asserts a symbol-level edge (test function -> resolved function/class)
alongside the module-level one whenever a test's call resolution actually
reaches that specific symbol. This narrows, but does not eliminate, the
previously-documented blind spot: a class/function target now gets
affected tests when some test's resolved call reaches it directly (e.g. a
pytest fixture parameter annotated with the class under test), but a test
that only imports the module without a resolved call into that specific
symbol still contributes nothing at that granularity -- by design, per the
"explicit evidence only" rule (`docs/relationship-model.md`). This
fixture's test style (module-level constructs/calls, not per-symbol
fixtures) is why the measured numbers above are unchanged at module
granularity; `tests/unit/test_relationships.py`'s
`test_tests_relationship_reaches_the_specific_method_via_a_typed_fixture`
demonstrates the finer granularity directly.

## Hallucination resistance / grounding

Four scenarios, each run twice: once with a stub provider citing only real
candidate IDs ("honest"), once citing the same real IDs plus invented ones
("hallucinating"). The first three exercise `ai.qa.answer_question`; the
fourth exercises `ai.impact.analyze_impact`'s citation mechanism (see
`docs/ai-grounding.md`):

| Scenario | Honest verdict | Hallucinating verdict | Rejected/cited |
|---|---|---|---|
| "how does login with a provider work" | valid | partially_valid | 2/4 |
| "user repository" | valid | partially_valid | 2/4 |
| "authentication service" | valid | partially_valid | 2/4 |
| impact: app.users.repository | valid | partially_valid | 1/3 |

**Unsupported-claim rate (hallucinating scenarios): 30.4%** (7 invented
IDs rejected out of 23 cited across the 4 scenarios) -- and in every case,
100% of the invented IDs were caught and quarantined; 0% of invented IDs
ever survived into `Answer.evidence`/`ImpactReport.evidence`. The harness
cross-checks this against the real `answer_question`/`analyze_impact` code
paths (not just a direct `GroundingValidator` call) on every scenario --
see the `assert`s in `run_hallucination_benchmark`.

This complements (doesn't replace) the example-based grounding tests in
`tests/unit/test_grounding.py`, `test_qa.py`, and `test_change_planner.py`
-- including two added this phase specifically to widen the net:
`test_answer_question_rejects_a_plausible_looking_id_the_same_as_any_other`
(an off-by-one guess like `FILE_002` when only `FILE_001` exists is
rejected exactly like an obviously-wrong ID -- no fuzzy/nearest-match
leniency) and
`test_plan_change_quarantines_invented_ids_independently_per_field` (three
fields hallucinated at once; each is quarantined independently, and the
overall verdict reflects the mix rather than collapsing to VALID or
INVALID).

### Confidence calibration

`ai.qa.answer_question`/`ai.change_planner.plan_change` cite
retrieval-matched candidates, whose `EvidenceStrength` is always
`INFERRED` (see `ai/confidence.py`) -- so `Answer.confidence`/
`ChangePlan.confidence` are structurally `LOW` or `MEDIUM`, **never**
`HIGH`. Only `ai.impact` (built from `RESOLVED`/`DIRECT` dependency facts)
can reach `HIGH`. This means "does HIGH correlate with correctness more
than MEDIUM/LOW" has no HIGH case to compare against for `ask`/`change` --
that's a structural fact about this design, not a gap the benchmark
found, and it's the honest answer to that question for those two
commands.

### What this harness does and doesn't measure

The hallucination/calibration numbers above use a **scripted stub
provider** with a fixed, known-in-advance response -- fully reproducible
without `ANTHROPIC_API_KEY` or network access. This measures whether the
deterministic grounding/confidence *pipeline* reacts correctly to
citation quality (it does, provably, on every scenario tried). It does
**not** measure a real model's actual hallucination rate or citation
behavior -- that would require running `ask`/`change` against a real
`AIProvider` (see `cli.context.get_provider`) across many real questions
with human-graded correctness, which is not reproducible the way the rest
of this report is and wasn't attempted here.

## Performance (synthetic linear-chain repositories)

Generated with `scripts/benchmark.py`'s `_generate_synthetic_repo`: `N`
modules, each importing and calling the previous one, so relationship
resolution has real edges to find rather than timing isolated files.
Measured on one Windows development machine (not a guaranteed absolute
number on other hardware -- these are the "establish measured limits, do
not pre-optimize" numbers the brief asked for, not an SLA):

| Files | Scan (s) | Analyze (s) | Relationships (s) | Graph build (s) | Traversal (s) | Relationships found | Peak memory (MB) |
|---|---|---|---|---|---|---|---|
| 100 | 0.79 | 0.24 | 0.03 | 0.005 | 0.001 | 698 | 5.3 |
| 1,000 | 0.94 | 1.12 | 0.17 | 0.040 | 0.007 | 6,998 | 27.3 |
| 5,000 | 4.05 | 7.10 | 1.18 | 0.29 | 0.092 | 34,998 | 128.3 |
| 10,000 | 9.46 | 14.07 | 2.11 | 0.56 | 0.065 | 69,998 | 262.7 |
| 50,000 | 99.5 | 106.3 | 17.0 | 5.8 | 0.99 | 349,998 | 1,289.5 |

Traversal time is for the worst case for this synthetic shape: the full
transitive-dependents chain from the root module (every other module
depends on it), at `max_depth` large enough not to truncate.

This table always measures a *full* analysis -- `scripts/benchmark.py`'s
generator writes a fresh synthetic repository every run, so there is never
a previous index run for `analyzer.registry.analyze_repository_incremental`
(`docs/indexing.md`) to reuse against. Incremental reuse's actual saving is
therefore not in this table by construction: on a real repository re-indexed
with only a handful of files changed, the "Analyze" column's cost applies
only to those changed files, not the whole repository (verified functionally
on `tests/fixtures/fixture_project`, 21 files -- an unchanged re-index
reuses all 21, a one-file edit reuses 20 -- see `docs/indexing.md`; not
re-measured at the synthetic sizes above, since the win scales with how much
of a real repository is actually unchanged between runs, a property this
generator doesn't model).

Re-measured after adding assignment extraction (`Assignment`/
`AssignedValueKind`, alongside `CallSite`s) and the `domain.
type_inference` attribute/local-variable typing pass that consumes it
(both described in `docs/relationship-model.md`) -- relationship
derivation time increases visibly (e.g. ~0.94s -> ~1.18s at 5,000 files),
which is the real, expected cost of that extra pass, not noise. Scan
time's increase (this benchmark's scan step does not call either new code
path) is most likely single-machine variance rather than a real
regression -- this is one Windows development machine with no isolation
from other load, not a controlled benchmark environment; treat the shape
of these numbers (roughly linear, no single stage dominating end-to-end
beyond scan+analyze) as the finding, not the exact seconds.

**Findings:**
- Scan and analyze still scale roughly linearly with file count.
- Relationship resolution (now including type inference), graph
  construction, and traversal remain comfortably smaller than scan+analyze
  at every measured size -- none of them are the bottleneck.
- Peak memory during scan+analyze+relationships+traversal (as measured by
  `tracemalloc`, not RSS) is roughly 26 KB/file, growing linearly with no
  sign of superlinear blowup through 50,000 files.
- No pre-optimization was done or is recommended by these numbers: a
  50,000-file repository indexes in a few minutes end to end
  (scan+analyze+relationships) on ordinary development hardware, which is
  acceptable for an on-demand `index` command, not a hot path.

DB write/query time and AI context size are not included here: DB write
time is already covered by `tests/unit/test_persistence.py`'s concurrency
tests at small scale and wasn't re-measured at these sizes (SQLite write
throughput for this schema is a separate, narrower question from the
scan/analyze/graph pipeline this table is about). AI context size is
governed by `SecurityConfig.max_context_size` and `ai.context_limits`
(Phase 5) -- it's a fixed ceiling by construction, not something that
scales with repository size the way the rest of this table does.
