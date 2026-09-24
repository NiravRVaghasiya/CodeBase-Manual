# Analyzers: the language registry, and what adding a language needs

Only Python is analyzed today. This is a deliberate scope limit, not an
oversight -- the domain model and every layer above it (`domain.
relationships`, `query.*`, `ai.*`) already talk in language-agnostic
terms (`PythonModule` is actually the one Python-specific name in the
whole pipeline; see the note at the end), specifically so that adding a
second language is a matter of registering an analyzer, not restructuring
anything above `analyzer/`.

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

`AnalysisContext` is the one piece of per-run configuration threaded into
every analyzer call -- today just `source_roots` (`analyzer.config.
detect_source_roots`, for resolving `src/`-layout module names). A second
language would likely need its own context fields (see below), added to
the same dataclass rather than inventing a parallel one.

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

`infer_module_name` is a path-based heuristic (strip `source_roots`,
join the remaining segments with `.`), not real `sys.path` resolution --
it's only meaningful for a path that already lies inside a Python
package.

## What a second language actually needs

Everything a `LanguageAnalyzer` must produce is already spelled out by
the `PythonModule` fields above -- despite the name, nothing in
`domain.models` requires the source to actually be Python; it requires
the *shape* (module-level docstring/imports/functions/classes,
CONTAINS-able nesting, scoped call sites). Concretely, adding a language
(TypeScript is the brief's own example of a *candidate*, not a
commitment -- section 26) needs:

1. **A parser** producing the same fact shape `PythonModule` holds.
   `ast` is Python-specific; a `tree-sitter` grammar or the language's
   own compiler API would fill the equivalent role for anything else.
2. **A new `LanguageAnalyzer` implementation** (e.g.
   `analyzer/typescript_analyzer.py`) matching the `Protocol` above,
   registered in `analyzer.registry._REGISTRY` under the right
   `FileLanguage` member (`domain.models.FileLanguage` would need that
   member added first -- it's currently
   `PYTHON`/`TOML`/`YAML`/`JSON`/`MARKDOWN`/`SQL`/`ENV`/`UNKNOWN`).
3. **Scoped call-site extraction**, matching the same "calls made inside a
   nested function/class defined within this scope belong to that nested
   scope, not this one" rule the Python analyzer enforces -- `domain.
   relationships`'s `CALLS`/`TESTS` derivation depends on this being
   correct, not just present.
4. **Cross-file resolution reuse.** `domain.relationships.build_relationships`
   is already language-agnostic -- it resolves imports/calls/bases by
   matching qualified names across every analyzed `PythonModule`
   regardless of which analyzer produced them. A new analyzer doesn't
   need its own relationship-derivation logic, only correct import/call/
   base-class *facts* for that logic to resolve against. Cross-*language*
   relationships (a Python file importing a TypeScript module, or vice
   versa) are not handled by anything today and would need their own
   design -- nothing currently attempts to resolve an import across a
   language boundary.
5. **Fixtures and tests** mirroring `tests/unit/test_python_analyzer.py`'s
   coverage (nested scopes, decorators, relative imports, malformed
   source) for the new language, plus at least one integration fixture
   analogous to `tests/fixtures/src_layout/` if the new language has an
   equivalent source-root convention.

Nothing above touches `query/`, `ai/`, `persistence/`, `cli/`, or `api/`
-- they already operate on `RepositorySnapshot`'s `modules: list[PythonModule]`
without caring which analyzer produced any given entry.

## Renaming `PythonModule`

If a second language is actually built, `PythonModule` should be renamed
to something language-neutral (e.g. `AnalyzedModule`) at that point --
not preemptively now, since a rename with no second implementation to
validate it against risks guessing the wrong shape. This is flagged here
so whoever adds the next language knows it's an expected, not
surprising, refactor.
