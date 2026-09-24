# Contributing

## Local setup

The canonical workflow (README.md):

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy src
```

If `uv` isn't on `PATH` in your shell, use the venv's Python directly --
everything below assumes Windows/PowerShell-style paths (`.venv/Scripts/`);
on Linux/macOS it's `.venv/bin/`:

```bash
".venv/Scripts/python.exe" -m pytest -q
".venv/Scripts/python.exe" -m mypy src scripts
".venv/Scripts/python.exe" -m ruff check .
```

### Environment gotcha: stale editable installs

If this repository exists in more than one checkout on your machine (e.g.
you cloned it again somewhere else, or copied a `.venv` between
checkouts), the editable-install pointer
(`.venv/Lib/site-packages/_editable_impl_codebase_manual.pth` --
`.venv/lib/python*/site-packages/...` on Linux/macOS) can end up pointing
at the *other* checkout's `src/`. Tests will then run against stale code
with zero error or warning -- your edits simply don't take effect, and
nothing tells you why. If a test's behavior doesn't match what you just
changed, check that file first before assuming the bug is in your code.
Fix it by editing the `.pth` file to point at the checkout you're
actually working in, or by re-running `uv sync` from that checkout.

## Before opening a PR

1. Run the three gates above. All three must be clean --
   `pytest`/`mypy --strict`/`ruff` are the only correctness signal this
   project has (no CI is configured in this repository as checked into
   version control).
2. If you touched `analyzer/`, `domain/`, or `query/`, check whether
   `tests/unit/test_invariants.py` still needs updating -- it checks
   whole-snapshot properties (every relationship endpoint resolves, no
   dangling `CALLS`/`TESTS` targets, traversal never escapes the
   snapshot), and a new relationship kind or entity kind should extend
   its coverage, not just get a hand-picked example test.
3. If you touched anything AI-facing (`ai/qa.py`, `ai/change_planner.py`,
   `ai/impact.py`, `ai/summarizer.py`), add a grounding test per
   `docs/testing.md`'s "How to add a grounding test" -- both an
   all-invented case and a mixed case.
4. If your change is user-visible from the CLI or affects retrieval/
   relationship/impact accuracy, consider whether `scripts/benchmark.py`'s
   numbers need re-running and `docs/performance.md` updating. Not every
   change needs this -- but if you changed `query/retrieval.py`'s
   weights, `domain/relationships.py`'s resolution logic, or anything in
   `analyzer/`, the accuracy numbers in that doc are now measuring the
   *old* behavior.
5. Update whichever `docs/*.md` describes what you changed. This
   project's own instruction throughout its build has been "update
   documentation only after behavior is implemented" -- not before, and
   not skipped after.

## Conventions worth knowing before you write code here

These aren't style preferences -- each one exists because relaxing it
once already caused (or would have caused) a real problem in this
codebase:

- **No `confidence` field in any LLM-facing prompt schema.** Confidence is
  computed from evidence strength (`ai/confidence.py`), never read from
  the model. Adding a `confidence` field to a new prompt schema would
  reintroduce exactly the "LLM self-reports its own trustworthiness" gap
  the trust boundary (Phase 1) exists to close.
- **Candidate IDs, not raw strings**, for any structured LLM output that
  references an existing repository entity. Build a `CandidateSet` from
  retrieval (`query.candidates.build_candidate_set`), validate references
  through `GroundingValidator` (`ai.grounding`) before they can appear in
  a returned result. A new field that lets the model write a path/name
  directly is a hallucination vector with no quarantine mechanism.
- **`EvidenceStrength` is a property of *how* a relationship kind is
  derived** (`domain/evidence.py`'s `_RELATIONSHIP_STRENGTH` map), not a
  per-instance judgment call. If you make a relationship kind's
  derivation more rigorous (e.g. `TESTS` moved from `INFERRED` to
  `RESOLVED` in Phase 2 when its detection stopped accepting a bare
  import as evidence), bump its strength there and update
  `docs/confidence.md`/`docs/evidence-model.md` to match.
- **Deterministic layers never import from `ai/`.** `domain/`,
  `repository/`, `analyzer/`, `persistence/`, `query/` must never depend
  on anything under `ai/` -- `ai/` depends on them, never the reverse.
  When this got close to being violated (AI summary caching, Phase 4),
  the fix was to keep `persistence.store`'s cache methods
  generic/untyped rather than have persistence depend on `ai.models.
  FileSummary`. If you find yourself importing from `ai/` in one of the
  deterministic packages, that's a sign the abstraction belongs the other
  way around.
- **Never fabricate a fact.** An unresolvable call, an ambiguous base
  class, an import that doesn't resolve -- all of these are *dropped*,
  never guessed at. This is checked mechanically now
  (`test_invariants.py::test_dangling_calls_and_bases_are_dropped_not_fabricated`),
  but it's a design principle first, not just a test.
- Every module-level docstring explains *why*, not just what. This is
  what makes `docs/*.md` possible to write and keep accurate -- if you
  add a module without one, the next person writing documentation against
  it (including a future you) has to reconstruct the reasoning from
  scratch.

## Where the fuller history lives

`Context.md` (repository root) tracks what was done in each phase of this
project's build and why, including design decisions that didn't make it
into a `docs/*.md` file (e.g. why `.env*` files are excluded at the
content level rather than the scan-visibility level -- Phase 5). It's
written for picking the work back up, not for a first read of the
architecture -- start with `docs/architecture.md` instead.
