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
right shape for "arbitrarily many files," a generator is. `dynamic_dispatch`
and `unittest`-style tests are the one genuine gap: worth a fixture if/when
call-resolution levels 4-5 (type-aware inference) are worked on, since
that's what would need to exercise them -- not before.

## Accuracy (against `tests/fixtures/fixture_project`)

The harness's expected/relevant sets are hand-verified against the
fixture's actual source (imports, calls, inheritance), not guessed -- see
`scripts/benchmark.py`'s `RETRIEVAL_SCENARIOS`/`RELATIONSHIP_CHECKS` for
the exact reasoning behind each one.

### Retrieval precision/recall

| Query | Precision | Recall |
|---|---|---|
| "how does login with a provider work" | 0.75 | 1.00 |
| "user repository" | 0.78 | 1.00 |
| "authentication service" | 0.67 | 1.00 |
| **mean** | **0.73** | **1.00** |

**Finding:** recall is perfect across all three queries -- retrieval never
missed a genuinely relevant file in this fixture. Precision is
consistently 0.67-0.78, and every miss traces to the same cause:
1-hop relationship expansion (`query.retrieval._expand_via_relationships`)
pulling in a file connected to a lexical match but not actually on-topic
(e.g. `config/settings.py` surfaces for "login" because
`get_database()` transitively calls `get_settings()`, three hops from the
login handler). This is retrieval behaving exactly as designed --
favoring recall (never silently missing something connected) at a
measured, bounded precision cost -- not a bug.

### Relationship checks

5/5 passed, including one deliberately-expected-absent check:
`AuthService.login_with_provider` does **not** have a resolved `CALLS`
edge to `UserRepository.get_or_create`, because
`self._user_repository.get_or_create(...)` is an instance-attribute call
and call resolution doesn't do instance-attribute/type-aware inference
yet ("call resolution levels 4-5," deferred since Phase 2). This is a
real, measured recall gap in the relationship graph, not a harness
artifact -- any `CALLS` edge that would require knowing the *type* of an
instance attribute is silently absent rather than fabricated. If levels
4-5 land, this check's `should_exist` flips to `True` (see the comment on
that row in `scripts/benchmark.py`).

### Impact precision/recall

Module-level target (`app.users.repository`): direct-dependent
precision/recall = 1.00/1.00, affected-tests precision/recall = 1.00/1.00.

**Finding, not a bug:** `TESTS` relationships always target a *module*
(`domain.relationships._tests_relationships`), never a class or function.
So `impact`'s "affected tests" is only ever populated for a MODULE-kind
target -- the identical query against the `UserRepository` *class* or
`get_or_create` *function* returns an empty affected-tests list, even
though the same tests clearly exercise them. This is a real blind spot in
`impact`'s current granularity, distinct from the instance-attribute-call
gap above.

## Hallucination resistance / grounding

Three scenarios, each run twice: once with a stub provider citing only
real candidate IDs ("honest"), once citing the same real IDs plus two
invented ones ("hallucinating"):

| Scenario | Honest verdict | Hallucinating verdict | Rejected/cited |
|---|---|---|---|
| "how does login with a provider work" | valid | partially_valid | 2/4 |
| "user repository" | valid | partially_valid | 2/4 |
| "authentication service" | valid | partially_valid | 2/4 |

**Unsupported-claim rate (hallucinating scenarios): 33.3%** (2 invented
IDs out of every 6 cited across the 3 scenarios) -- and in every case,
100% of the invented IDs were caught and quarantined; 0% of invented IDs
ever survived into `Answer.evidence`. The harness cross-checks this
against the real `answer_question` code path (not just a direct
`GroundingValidator` call) on every scenario -- see the `assert` in
`run_hallucination_benchmark`.

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

**Known unmeasured gap:** `ai.impact.analyze_impact`'s `explanation`
field is free prose with no candidate-ID citation mechanism (its system
prompt says "do not invent any dependency," but nothing structural
enforces it). There's no `GroundingValidator`-checkable claim to measure
an unsupported-claim rate against here -- this is a real coverage gap in
the trust boundary, not a limitation of this script. See
`docs/ai-grounding.md`.

## Performance (synthetic linear-chain repositories)

Generated with `scripts/benchmark.py`'s `_generate_synthetic_repo`: `N`
modules, each importing and calling the previous one, so relationship
resolution has real edges to find rather than timing isolated files.
Measured on one Windows development machine (not a guaranteed absolute
number on other hardware -- these are the "establish measured limits, do
not pre-optimize" numbers the brief asked for, not an SLA):

| Files | Scan (s) | Analyze (s) | Relationships (s) | Graph build (s) | Traversal (s) | Relationships found | Peak memory (MB) |
|---|---|---|---|---|---|---|---|
| 100 | 0.36-0.56 | 0.09-0.19 | 0.01-0.02 | 0.003-0.005 | ~0.001 | 698 | 5.3 |
| 1,000 | 0.56-0.76 | 0.93-0.99 | 0.14-0.23 | 0.035-0.070 | 0.007-0.010 | 6,998 | 26.8 |
| 5,000 | 3.78 | 5.55 | 0.94 | 0.22 | 0.039 | 34,998 | 125.9 |
| 10,000 | 5.91 | 10.85 | 1.82 | 0.62 | 0.115 | 69,998 | 254.6 |
| 50,000 | 36.0 | 73.5 | 11.2 | 3.2 | 0.63 | 349,998 | 1,270.0 |

Traversal time is for the worst case for this synthetic shape: the full
transitive-dependents chain from the root module (every other module
depends on it), at `max_depth` large enough not to truncate.

**Findings:**
- Scan and analyze scale roughly linearly (about 0.7-1.5 ms/file combined
  at the low end, rising to ~1.1 ms/file scan + ~1.5 ms/file analyze at
  50,000 files -- analyze is consistently the larger share, as expected
  for AST-walking work).
- Relationship resolution, graph construction, and traversal are all
  comfortably sub-linear-feeling in wall time relative to file count at
  every measured size -- none of them are the bottleneck; scan+analyze
  dominate end to end.
- Peak memory during scan+analyze+relationships+traversal (as measured by
  `tracemalloc`, not RSS) is roughly 25 KB/file, growing linearly with no
  sign of superlinear blowup through 50,000 files.
- No pre-optimization was done or is recommended by these numbers: a
  50,000-file repository indexes in under two minutes end to end
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
