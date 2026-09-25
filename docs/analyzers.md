# Analyzers: the language registry, and what adding a language needs

Python and TypeScript are analyzed today -- TypeScript as a proof that the
domain model and every layer above `analyzer/` (`domain.relationships`,
`query.*`, `ai.*`) genuinely talk in language-agnostic terms (`PythonModule`
is still the one Python-specific *name* in the pipeline; see the note at
the end for why it wasn't renamed despite a second implementation now
existing), not a breadth push toward supporting many languages. Adding
TypeScript was exactly the "register an analyzer, restructure nothing
above `analyzer/`" exercise this doc originally described as the goal.

## The registry

`analyzer.registry` is the seam:

```python
class LanguageAnalyzer(Protocol):
    def __call__(
        self, *, file_path: Path, repo_relative_path: str, context: AnalysisContext
    ) -> PythonModule:
        """Extract structural facts from a single source file."""
```

`analyze_repository(root, scan_result)` looks up a registered analyzer by
each file's `FileLanguage` (`_REGISTRY: dict[FileLanguage, LanguageAnalyzer]`)
and calls it. A file whose language has no registered analyzer (or that's
marked binary) is skipped entirely -- it still has a `FileRecord` from the
scanner (path, size, hash), but never a `PythonModule`, and therefore
never appears in retrieval, relationships, or an AI prompt. That's not a
language-specific gap; it's the same mechanism `docs/security.md` relies
on to guarantee `.env`/`.pem`/etc. never reach interpretation.

`analyze_repository_incremental(root, scan_result, previous_files=,
previous_modules=, previous_analyzer_version=)` is the same dispatch, plus
reuse: a file whose fingerprint matches its entry in `previous_files`
returns the corresponding entry from `previous_modules` instead of calling
the analyzer at all, as long as `previous_analyzer_version` matches
`domain.models.PYTHON_MODULE_SCHEMA_VERSION` (see `docs/indexing.md`).
`cli.main index` is the only caller; `analyze_repository` is a thin
wrapper over it with empty previous-run arguments, kept as the simple
entry point for tests and any other caller with no previous run to reuse.

`AnalysisContext` is the one piece of per-run configuration threaded into
every analyzer call -- today just `source_roots` (`analyzer.config.
detect_source_roots`, for resolving `src/`-layout module names), unused by
the TypeScript analyzer (TypeScript has no equivalent convention this
project resolves). A third language would likely need its own context
fields, added to the same dataclass rather than inventing a parallel one.

## What the Python analyzer actually extracts

`analyzer.python_analyzer.analyze_module` parses one file with the
standard-library `ast` module and returns a `PythonModule`
(`domain.models`): module docstring, `imports` (`ImportedName`, tracking
alias/relative-level), top-level `functions`/`classes` (with parameters,
decorators, docstrings, `is_async`), and nested functions/classes at any
depth (extracted as their own qualified entries, e.g.
`module.outer.inner` -- see `docs/relationship-model.md`). Each function's
`calls: list[CallSite]` is scoped to *that function's own body* --
`_ScopedCallVisitor` stops at nested `def`/`async def`/`class`/`lambda`
boundaries, so a call inside a nested function never leaks into its
parent's call list. A syntax error is caught and stored as
`PythonModule.parse_error`, never raised -- one bad file must not abort
indexing the rest of the repository.

The same scoped visitor also records `assignments: list[Assignment]`
alongside `calls` -- a `target = value` statement in the function's own
scope, but only when both sides have a recognizable shape: the target is a
plain local name or a `self.`/`cls.` attribute, and the value is a call, a
bare name, or a dotted attribute access (`domain.models.Assignment`/
`AssignedValueKind`). Tuple unpacking, subscript targets, and literal/
binary-expression values carry no usable type information and are simply
not recorded -- absence, not a placeholder "unknown" entry. This is a
second, separate fact stream from `calls`; recording it doesn't change
what counts as a call. `domain.type_inference` is the only consumer: it
turns these into best-effort attribute/local-variable types for
type-aware call resolution (see `docs/relationship-model.md`).

`infer_module_name` is a path-based heuristic (strip `source_roots`,
join the remaining segments with `.`), not real `sys.path` resolution --
it's only meaningful for a path that already lies inside a Python
package.

## The TypeScript analyzer (`analyzer.typescript_analyzer`)

Registered under `FileLanguage.TYPESCRIPT` (`.ts`/`.tsx`), built as the
architecture-proof second language rather than a breadth push. Unlike the
Python analyzer, there is no real parser underneath it: this environment
had no way to safely install a `tree-sitter` grammar or equivalent (no
`pip`/`uv` available, and the project's own dependency-minimalism
precedent argued against adding one speculatively), so it is a
deliberately conservative brace-depth scanner over regex-recognized
declaration shapes. It extracts:

- `import` statements (named/default/namespace/side-effect; `import type`
  treated as a value import).
- Top-level and nested `function` declarations -- not arrow functions or
  function expressions, which have no reliable brace-scoped signature to
  anchor on without a real parser.
- `class` declarations with an `extends` base and method declarations
  inside them.
- Call expressions (`name(...)`, `a.b.c(...)`, `new Name(...)`), scoped
  exactly like the Python analyzer scopes them: a call inside a nested
  function/method belongs to that scope; a call directly in a class body
  (not inside a method) is not recorded at all -- no confident scope to
  attribute it to, the same reasoning the Python analyzer applies to a
  lambda body.

It produces the same `PythonModule` shape the Python analyzer does --
`domain.relationships.build_relationships` needed exactly one change to
work identically over TypeScript facts: recognizing `this` as a
self-reference alongside Python's `self`/`cls` in `_resolve_call`. Nothing
else in `domain/`, `query/`, `persistence/`, `ai/`, `cli/`, or `api/`
changed. `tests/fixtures/typescript_project` + `tests/integration/
test_typescript_fixture.py` demonstrate this end to end: same-file
`CONTAINS`/`INHERITS`/`CALLS` resolve, including inheritance-aware method
dispatch through `this.`, and a cross-file relative import that can't
resolve fails closed as an `UnresolvedCall` rather than a fabricated
relationship.

**No `Assignment` facts are extracted for TypeScript at all**, so
`domain.type_inference`'s attribute/local-variable typing has nothing to
work with for a TS class: `this.method()` resolves, `this.injectedField.
method()` does not (unlike the equivalent Python `self.attr.method()`).
Other disclosed gaps, in `analyzer.typescript_analyzer`'s own module
docstring: template-literal `${...}` interpolation isn't scanned; regex
literals aren't specially recognized (a `//` inside one can be misread as
a comment); cross-file relative-import resolution doesn't work, since
`_resolve_import_module`'s relative-import logic assumes Python package
semantics (dotted names, `__init__.py`-based package detection) that don't
apply to a `./service`-style TypeScript path.

## What a third language would need

The pattern the TypeScript analyzer actually followed, concretely:

1. **A fact producer** matching `PythonModule`'s shape -- a real parser
   (`tree-sitter`/a compiler API) if one is installable in the target
   environment; a conservative scanner like TypeScript's own if not.
   Nothing in `domain.models` requires the source to actually be Python;
   it requires the *shape* (module-level docstring/imports/functions/
   classes, CONTAINS-able nesting, scoped call sites and assignments).
2. **A new `LanguageAnalyzer` implementation**, registered in
   `analyzer.registry._REGISTRY` under a new `FileLanguage` member.
3. **Scoped call-site (and, ideally, assignment) extraction** matching the
   "belongs to this scope, not a nested one" rule both existing analyzers
   enforce. Assignment extraction is what unlocks `domain.type_inference`'s
   attribute/local-variable typing for `self.attr.method()`-shaped calls
   -- TypeScript's own gap above is exactly what skipping this step costs.
4. **A self-reference check in `domain.relationships._resolve_call`** if
   the language's own-instance keyword isn't already `self`/`cls`/`this`.
5. **Cross-file resolution reuse or extension.** `domain.relationships.
   build_relationships` is already language-agnostic for same-file
   resolution -- it just matches qualified names across every analyzed
   `PythonModule` regardless of which analyzer produced them. Cross-file
   import resolution is only as good as `_resolve_import_module`'s
   assumptions; a language whose import semantics don't fit the Python-
   package model TypeScript already ran into needs either an adapter there
   or an accepted, disclosed gap like TypeScript's own. Cross-*language*
   relationships (a Python file importing a TypeScript module, or vice
   versa) are not handled by anything today.
6. **Fixtures and tests** mirroring `tests/unit/test_python_analyzer.py`'s
   coverage for the new language, plus an integration fixture analogous to
   `tests/fixtures/typescript_project` proving same-file relationship
   resolution actually works end to end, not just that facts are extracted.

Nothing above touches `query/`, `ai/`, `persistence/`, `cli/`, or `api/`
-- they already operate on `RepositorySnapshot`'s `modules: list[PythonModule]`
without caring which analyzer produced any given entry.

## Renaming `PythonModule`

A second language now exists, which is the condition this doc previously
named as the trigger for renaming `PythonModule` to something
language-neutral (e.g. `AnalyzedModule`). It was not done in this change:
the rename is purely cosmetic (every consumer already treats the type as
a language-neutral fact shape, as this doc has argued since before a
second language existed) and touches every layer of the codebase --
`domain/`, `analyzer/`, `persistence/`, `query/`, `ai/`, `cli/`, `api/`,
and every test file that imports the name -- for no behavioral change.
Given the size of the change this session already made, bundling in a
purely-cosmetic, high-blast-radius rename was judged not worth the risk of
missing a spot. It remains available as a follow-up with no functional
prerequisite left blocking it.
