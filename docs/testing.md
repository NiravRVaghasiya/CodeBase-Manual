# Testing: suite layout, and how to add coverage for the trust boundary

## Layout

```
tests/
  unit/          One file per module under src/, same name (test_*.py).
                 Fast, no filesystem/network beyond tmp_path, no AI provider.
  integration/   Multiple modules together, usually against a real fixture
                 or a real tmp_path repository written to disk.
  fixtures/      Static repositories test code points a scanner/analyzer
                 at. Ignored by pytest's own collection (see below) --
                 they contain their own test_*.py files that are fixture
                 *content*, not tests of this project.
scripts/
  benchmark.py   Accuracy/hallucination/performance -- see below. Not
                 pytest, not in the default run.
```

`pyproject.toml`'s `[tool.pytest.ini_options]` sets
`addopts = "--ignore=tests/fixtures"`. Without it, pytest would collect
`tests/fixtures/fixture_project/tests/test_auth_service.py` as if it were
a real test of this project -- it's fixture content (a test file that
`domain.relationships`'s `TESTS`-detection logic is supposed to notice),
not a test of `codebase_manual` itself. If you add a new fixture
directory with its own tests inside it, it's covered by this same
`--ignore` automatically (it's a path prefix, not a per-directory list).

## Unit vs. integration vs. benchmark

- **Unit** (`tests/unit/`): one concern, hand-built inputs (a `PythonModule`
  constructed directly, a `RepositorySnapshot` built in a local `_snapshot()`
  helper, a stub `AIProvider`). This is almost everything -- 27 files, one
  per `src/codebase_manual/**/*.py` roughly, including
  `test_typescript_analyzer.py`. Includes `test_invariants.py`: property-based,
  hand-rolled generators (no `hypothesis` dependency), fast enough to stay in
  the default run -- see its module docstring for why a *generator* rather
  than a handful of hand-picked examples is worth the extra code for the
  specific invariants it checks (including, now, that the generator's
  dangling references actually produce `UnresolvedCall` facts, not just
  that nothing is fabricated in their place).
- **Integration** (`tests/integration/`): the real pipeline against a real
  fixture or a real `tmp_path` repository written to disk --
  `test_index_fixture.py`/`test_src_layout_fixture.py`/
  `test_typescript_fixture.py` run the actual scanner + analyzer against
  `tests/fixtures/`; `test_security_boundaries.py` round-trips scan ->
  analyze -> persist -> `ai.qa.answer_question` through a real SQLite file
  to verify secrets never reach it (`docs/security.md`); `test_web_app.py`
  exercises `api.app.create_app` with FastAPI's test client.
- **Benchmark** (`scripts/benchmark.py`, Phase 7): a report, not a
  correctness gate -- deliberately outside `tests/` and outside the
  default `pytest` run (`docs/performance.md`). Run it on demand; it
  doesn't need `ANTHROPIC_API_KEY` (accuracy/performance are pure
  deterministic code, hallucination/calibration use a scripted stub
  provider -- see the script's own docstring for exactly what that
  does and doesn't prove).

## Fixture repositories

- **`fixture_project`**: the rich one -- an auth service coordinating
  OAuth providers (inheritance: `GithubProvider`/`GitlabProvider` ->
  `OAuthProvider`), aliased and relative-feeling imports, async methods, a
  user repository with its own tests (`pytest`-style, `@pytest.mark.asyncio`),
  a FastAPI-shaped `router.post(...)`/`router.get(...)` API surface, and a
  `.env.example`. This is also what `scripts/benchmark.py`'s accuracy
  section measures against -- if you change this fixture's structure,
  re-run `python scripts/benchmark.py accuracy` and update
  `docs/performance.md`'s hand-verified expected sets to match, or the
  numbers there go stale.
- **`src_layout`**: minimal, exists solely to prove `src/app/service.py`
  resolves to module name `app.service`, not `src.app.service`
  (`analyzer.config.detect_source_roots`).
- **`typescript_project`**: minimal (two files), proves the second-language
  architecture end to end -- same-file `INHERITS`/`CALLS` resolution
  (including `this.`-based inheritance-aware dispatch) through the
  unmodified `domain.relationships` pipeline, and a cross-file relative
  import that fails closed as an `UnresolvedCall` rather than resolving by
  accident (`docs/analyzers.md`). Also exercised by `scripts/benchmark.py`'s
  accuracy section as a second, distinct-language evaluation corpus.

`docs/performance.md`'s "Fixture breadth: decision" section explains why
most Python shapes (relative imports, aliased imports, nested scopes,
decorators, async, malformed Python) don't have their own dedicated
fixture directory -- they're already covered directly in
`test_python_analyzer.py` without one.

## How to add a grounding test

Three AI-facing features -- `ai.qa`, `ai.change_planner`, and `ai.impact`
-- are grounded the same way: retrieval/computation produces facts, a
`CandidateSet` gives the model opaque IDs (`build_candidate_set` for
retrieval results, `build_candidate_set_from_refs` for `ai.impact`'s fixed
fact list), `GroundingValidator` resolves whatever the model cited back
against real entities. `ai.summarizer` is the one AI-facing feature that
does *not* use this mechanism -- it paraphrases facts already handed to
it in the prompt rather than selecting among candidate IDs, and its
confidence comes from `confidence_for_summary` (docstring/structure
presence, not citation strength; see `docs/confidence.md`), so there is
nothing for a grounding test to exercise there. A grounding test proves
an invented reference is caught, not proves the feature works (that's the
rest of the test file). The shape, from
`test_qa.py`/`test_change_planner.py`/`test_impact.py`:

1. Build a `RepositorySnapshot` with a couple of real modules/functions/
   classes (a local `_snapshot()` helper -- copy an existing one, don't
   design a new fixture for this).
2. A stub `AIProvider` whose `complete()` returns a fixed JSON string
   (via `json.dumps`, not hand-written -- an f-string embedding a Python
   list produces single-quoted, non-JSON text; `scripts/benchmark.py`
   hit exactly this bug once, see its git history).
3. Call the real function (`answer_question`/`plan_change`/...) with the
   stub.
4. Assert on the **result's** `grounding`/`confidence`/`evidence` --
   never on the stub's raw response. The point is that an invented ID
   never survives into the returned model.

Widen the net, don't just add one more happy-path case: Phase 7 added
`test_answer_question_rejects_a_plausible_looking_id_the_same_as_any_other`
(an off-by-one guess like `FILE_002` when only `FILE_001` exists is
rejected with no fuzzy leniency) and
`test_plan_change_quarantines_invented_ids_independently_per_field`
(hallucinations spread across three `ChangePlan` fields at once, each
quarantined independently, overall verdict reflecting the mix). If you're
adding a new AI-facing feature, add at least one case where *every*
reference is invented (expect `ValidationVerdict.INVALID`, empty
evidence, `Confidence.LOW`) and one where it's a mix (expect
`PARTIALLY_VALID`, and check the valid half still made it through).

## Running the gates

```bash
".venv/Scripts/python.exe" -m pytest -q
".venv/Scripts/python.exe" -m mypy src scripts
".venv/Scripts/python.exe" -m ruff check .
```

See `docs/contributing.md` if these commands don't work as expected --
there's a known environment gotcha with stale editable installs that
looks exactly like "my edits aren't taking effect."
