# Security: what's read, what's sent, what's stored

Repository contents are potentially sensitive. This document traces the
data flow end to end -- scan -> analyze -> retrieve -> AI prompt -- and
states exactly what's excluded by default and why.

## Data flow

```
disk --scan--> FileRecord (path, size, language, hash, mtime)
     --analyze (Python only)--> PythonModule (docstrings, signatures,
                                 imports, calls -- never raw source text)
     --persist--> SQLite (FileRecord + PythonModule facts, by content hash)
     --retrieve--> bounded candidate set (opaque IDs)
     --prompt--> AI provider (docstrings/signatures/call-chains of
                 *retrieved* candidates only, capped in size)
```

Two structural properties hold at every stage, independent of any
config:

1. **Raw file bytes never leave the scanner.** `repository.scanner`
   computes a SHA-256 `content_hash` (one-way; the original bytes cannot be
   recovered from it) and discards the bytes. Nothing downstream --
   persistence, retrieval, AI prompts -- ever has access to a file's raw
   content, only to structural facts the Python analyzer extracted from it
   (docstrings, signatures, import targets, call expressions) or to
   `FileRecord` metadata (path, size, language, hash). See
   `persistence/orm.py`'s `FileORM`: it has a `content_hash` column, never a
   content column.
2. **Only Python files are analyzed.** `analyzer.registry.analyze_repository`
   only has a registered analyzer for `FileLanguage.PYTHON`
   (`analyzer/registry.py`). A `.env`, `.pem`, or arbitrary config file
   never produces a `PythonModule` and therefore can never appear in
   retrieval, a candidate set, or an AI prompt -- there's no code path that
   could put one there.

## What's excluded from scanning entirely

`analyzer.config.DEFAULT_IGNORED_PATTERNS` (gitignore syntax, always
active on top of `.gitignore`):

```
*.pem  *.key  *.p12  credentials.*  secrets/  node_modules/  vendor/
```

These have no legitimate reason to be indexed at all -- they're either
pure secret material or third-party trees with no first-party code
intelligence value -- so they're excluded before the scanner even stats
them. They never appear in `ScanResult.files`, only in
`ignored_file_paths`.

Repository-specific additions go in `pyproject.toml`:

```toml
[tool.codebase-manual]
ignored-paths = ["build/", "*.local"]      # merged with the defaults above
ignored-extensions = [".log"]               # skipped regardless of path
max-file-size = 5242880                     # bytes; larger files get METADATA_ONLY
max-context-size = 24000                    # characters; see "Context-size limits" below
```

(`python-source-roots` is the pre-existing, unrelated source-root override
-- see `docs/relationship-model.md`.)

## The `.env*` decision

`.env*` files are deliberately **not** in the default-ignore list. Their
*existence* is useful, low-risk information -- the generated manual's
Configuration section lists `.env*` paths so a reader knows configuration
exists and where, without ever seeing a value
(`ai/manual.py`'s `_configuration_section`). What's excluded is their
*content*: `repository.scanner._fingerprint` never reads `.env*` bytes into
memory at all -- it always returns `METADATA_ONLY` regardless of file size,
the same code path used for binaries and oversized files. So a `.env`
file's path, size, and language are recorded; its bytes are read by
nothing, ever. This is enforced by
`tests/unit/test_scanner.py::test_scan_never_hashes_env_file_content_regardless_of_size`.

## Context-size limits

Retrieval (`query.retrieval.retrieve_relevant`) already bounds candidate
*counts* (`top_k_files=8`, `top_k_symbols=12` by default). `ai.qa` and
`ai.change_planner` additionally cap the assembled context *text* itself at
`SecurityConfig.max_context_size` characters
(`ai.context_limits.truncate_context`), appending an explicit
`[... context truncated at N characters ...]` marker rather than silently
cutting the prompt -- so a truncated context is recognizable as such, not
mistaken for a complete one. This protects against a pathological
repository (huge docstrings, many matches) producing a prompt large enough
to be a cost or context-window risk, independent of the AI provider's own
limits.

## Logging

`logging_config.configure_logging()`/`get_logger(name)` set up a
per-module `logging.Logger` (module-qualified names like `ai.qa`,
`ai.anthropic_provider`, `cli`), gated by the CLI's `--verbose`/`--quiet`
flags and called once from `cli.main.main()`'s Typer callback. Existing
call sites log counts and outcomes, never content: `ai.qa`/
`ai.change_planner` log retrieval result counts
(`retrieval found files=%d symbols=%d chains=%d`) and, when
`GroundingValidator` rejects a citation, a warning with the rejected/cited
counts and verdict (`grounding rejected %d/%d cited ids verdict=%s`) --
`ai.impact` logs the same rejection shape for its own citations (see
`docs/ai-grounding.md`). `ai.anthropic_provider` logs request
outcome/duration, never prompt or response text. `cli.main`'s `index`
command logs start and finish with file/module/relationship counts.

The `typer.echo` call sites in `cli/main.py` were separately audited: none
echo an API key, `.env` content, or raw source text -- they print
`Answer`/`ChangePlan`/`ImpactReport` prose (AI-generated interpretation,
not repository secrets) and deterministic fact counts.
`ai/anthropic_provider.py` reads `ANTHROPIC_API_KEY` from the environment
and never logs or echoes it. Rule of thumb for future call sites: never
format a `FileRecord`'s raw bytes, a `.env*` file's content, or an
`AIProvider`'s API key into any echoed/logged string -- only paths,
hashes, counts, and AI-generated prose are safe to surface.

`domain.relationships`, `query.retrieval`, and `query.graph` now log too:
`build_relationships_with_unresolved` logs a per-kind relationship count
plus the unresolved-call count on every run, and DEBUG-logs each
unresolved call's source/expression/location; `retrieve_relevant` logs a
summary (lexical vs. expanded file/symbol counts, chain count) and
DEBUG-logs each entity graph expansion added, with the same `detail` text
`MatchSignal` already carries -- "why was this entity retrieved" now has a
log line, not just a queryable field on the result; `RelationshipGraph.
transitive_dependents_traversal` logs when it truncates. All of these
follow the same rule as everything else here: paths, identifiers, counts,
and match reasons only -- never file content.

## Verifying the boundary

`tests/integration/test_security_boundaries.py` is the end-to-end
acceptance test: a repository containing a `.env` file, an RSA private key,
and a `node_modules/` tree is scanned, analyzed, persisted to a real SQLite
file, and queried through `ai.qa.answer_question` with a recording stub
provider. It asserts the secret file is excluded from the scan, the `.env`
file is never content-hashed, the on-disk SQLite file's raw bytes never
contain the secret markers, and no AI prompt ever contains them either.
