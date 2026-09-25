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
`expression`, `line`, `column`, `containing_symbol_id`, and `arguments`
(`domain.models.CallArgument` -- each argument's unparsed text and, for a
keyword argument, its parameter name). The `CALLS` relationship's
`location` points at the call site itself (the `line`/`column` of the call
expression), not the containing function's definition line. Argument
capture exists for consumers that need to resolve an argument to a known
symbol themselves -- `query.api_endpoints`'s `add_api_route(path, handler,
methods=[...])` detection is the one that does today; `domain.relationships`
does not resolve arguments as part of `CALLS` derivation.

`PythonModule.calls` (module scope, not inside any function) is extracted
the same way, using the same `_ScopedCallVisitor` over `tree.body` --
`containing_symbol_id` is the module's own name. This makes a call written
directly at module level (`router.add_api_route(...)`, a module-level
`setup()` call) a real fact instead of invisible to everything downstream;
`domain.relationships._calls_relationships` resolves these the same way it
resolves function-scoped calls, producing a `CALLS` edge sourced from the
module itself (`EntityKind.MODULE`) when one resolves.

## Type-aware call resolution (`domain.type_inference`)

`_resolve_call` handles more than a bare name or a same-class `self.method()`
call. Four levels, most to least direct:

1. `self.method()` / `cls.method()` (or `this.method()` -- `_resolve_call`
   recognizes `this` as a self-reference too, which is what lets
   `analyzer.typescript_analyzer`'s facts resolve through this exact same
   function with no TypeScript-specific branch; see `docs/analyzers.md`) --
   resolved on the owning class, walking resolvable base classes (so a
   method defined only on a base class still resolves from a subclass
   method body).
2. `self.attr.method()` -- `attr`'s type is looked up first, then the
   method is resolved on that type. The attribute's type comes from
   whichever of these a class's methods/annotations establish (see
   `domain.type_inference.build_attribute_types`): a constructor-injected
   parameter assigned straight to the attribute (`self.repo = repository`
   where `repository: UserRepository`), a direct construction
   (`self.repo = UserRepository()`), a dataclass/pydantic-style
   class-level annotation (`repo: UserRepository`, no `__init__` needed),
   or a factory call with a resolvable return annotation. Attribute
   lookup walks resolvable base classes too, so an attribute set only in
   a base class's `__init__` still resolves from a subclass method.
3. `Name()` -- a bare call, resolved via the module's own import/definition
   namespace (unchanged from before).
4. `Name.method()` -- `Name` resolved as a locally-known class (inheritance-
   aware method resolution, not just a class reference) or as a local
   variable of known type (`domain.type_inference.build_local_var_types`):
   a typed parameter, a direct construction, a factory return, or simple
   propagation from another already-typed name (`x = repo; x.find()`). A
   variable reassigned to two different (or one unresolvable) type anywhere
   in the function is treated as ambiguous and dropped entirely, rather
   than resolved to whichever assignment the pass happened to see last.

This is deliberately not a type checker -- it resolves only these concrete
shapes, all derived from `Assignment` facts the analyzer extracts alongside
`CallSite`s (`domain.models.Assignment`/`AssignedValueKind`; see
`docs/analyzers.md`). A shape it doesn't recognize (a call through an
untyped parameter, a dynamic/computed callee, a classmethod factory like
`Class.create()`, `self.factory()` where `factory` is itself a method)
resolves to `None`. Dropped from the relationship graph -- but not
untraced: `domain.relationships.build_relationships_with_unresolved`
records a `domain.models.UnresolvedCall` for it (see "CALL_UNKNOWN" in
`docs/architecture.md`), so "dropped" means "not fabricated into a
relationship," not "silently discarded." See `docs/performance.md` for
the concrete case type-aware resolution closed:
`self._user_repository.get_or_create(...)` now resolves to
`UserRepository.get_or_create`.

## TESTS relationship

`domain.relationships._tests_relationships` no longer asserts `TESTS` from
"test module imports X" alone. It requires a resolved call or construction:
a test function calling (or constructing, since `X(...)` is a call like any
other) something whose owning module is `X`. This uses the same call
resolution as `CALLS` (`domain.relationships._resolve_call`), so `TESTS`
now carries `EvidenceStrength.RESOLVED`, not `INFERRED` (see
`docs/evidence-model.md`).

**Two granularities are asserted from the same resolved call**: a
module-level edge (test module -> target module, as before) and a
symbol-level edge (the specific test function -> the specific function or
class it resolved to, via `EntityKind.FUNCTION` source). The symbol-level
edge only exists when resolution actually reached a specific symbol -- e.g.
a test function whose fixture parameter is annotated with the class under
test (`def test_x(service: Service): service.do_work()`), or a constructor-
injected attribute the type-aware resolution above can follow. This makes
`impact <function>`/`impact <class>` find tests that exercise that specific
symbol directly, not only tests of its whole module -- but it is still
bound by the same "explicit evidence only" rule: a test living in the same
module as a function, with no resolved call reaching that function
specifically, gets the module-level edge only, never a fabricated
symbol-level one.

Known limitation: pytest fixture-based usage where the fixture itself is a
`@pytest.fixture`-decorated *factory function* (rather than a directly
annotated parameter) is not resolved -- that would need the fixture
function's own return type inferred and threaded through pytest's
dependency-injection mechanism, which is a separate, larger piece of work
this type inference does not attempt.

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
