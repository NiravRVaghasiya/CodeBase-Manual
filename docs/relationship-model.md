# Relationship model: graph correctness

This documents what changed in how `domain.relationships` and the Python
analyzer derive facts, and why. It describes actual behavior, not intended
future behavior.

## Python module resolution (`src/` layouts)

`analyzer.python_analyzer.infer_module_name(relative_path, source_roots)`
strips a configured/detected source root from the front of a path before
dotting it, so `src/app/service.py` resolves to `app.service`, not
`src.app.service`.

`analyzer.config.detect_source_roots(repo_root)` determines the roots for
one indexing run, in order:

1. An explicit override: `[tool.codebase-manual] python-source-roots = [...]`
   in the repository's `pyproject.toml`.
2. A conventional `src/` layout: a top-level `src/` directory whose
   immediate subdirectories look like packages (contain `__init__.py`).
3. Otherwise, no source root -- paths are used as-is.

`analyzer.registry.analyze_repository` computes this once per run and
threads it through every file via `AnalysisContext`.

## Scope-aware call extraction

`analyzer.python_analyzer._ScopedCallVisitor` walks a function's body but
stops at the boundary of any nested `def`/`async def`/`class`/`lambda`
-- calls made inside those belong to that scope, not the enclosing one.
Decorator expressions and parameter defaults/annotations of a nested
`def`/`class` still evaluate in the *enclosing* scope at definition time,
so those remain visited (a decorator's own call is attributed to whichever
function's execution actually triggers it).

A nested `def`/`class` -- wherever it lexically appears, including inside
`if`/`for`/`while`/`with`/`try` blocks that don't introduce a new scope --
is now extracted as its own `FunctionSymbol`/`ClassSymbol` (flattened into
the owning module's `functions`/`classes` lists, with a qualified name like
`module.outer.inner`), so its own calls have somewhere correct to live. A
`lambda` has no such symbol (Python lambdas are anonymous), so calls inside
one are not tracked at all -- absence, not misattribution.

## Call sites

Every call recorded on a `FunctionSymbol.calls` entry is a `CallSite`:
`expression`, `line`, `column`, `containing_symbol_id`. The `CALLS`
relationship's `location` points at the call site itself (the `line`/
`column` of the call expression), not the containing function's
definition line.

## TESTS relationship

`domain.relationships._tests_relationships` no longer asserts `TESTS` from
"test module imports X" alone. It requires a resolved call or construction:
a test function calling (or constructing, since `X(...)` is a call like any
other) something whose owning module is `X`. This uses the same call
resolution as `CALLS` (`domain.relationships._resolve_call`), so `TESTS`
now carries `EvidenceStrength.RESOLVED`, not `INFERRED` (see
`docs/evidence-model.md`).

Known limitation: pytest fixture-based usage (a fixture injected as a test
function parameter, then used without ever being imported/constructed by
name) is not yet detected -- this requires fixture-resolution support that
hasn't been built yet, so such tests will not produce a `TESTS` edge even
though they may genuinely exercise the target module.

## Impact traversal truncation

`query.graph.RelationshipGraph.transitive_dependents_traversal(ref,
max_depth)` returns a `Traversal(entities, truncated)`. `truncated` is set
when the BFS hit `max_depth` with more dependents left unexplored -- the
true transitive set may be larger than what's returned. `ai.impact`
surfaces this as `ImpactFacts.truncated`/`ImpactReport.truncated`, includes
a note in the LLM-facing facts block, and caps confidence at `MEDIUM` when
set (an incomplete picture cannot be `HIGH` confidence, regardless of how
strong the partial evidence is). The CLI's `impact` command prints a
truncation note when this happens. `transitive_dependents` (no traversal
object) remains as a convenience wrapper for callers that don't need the
flag.
