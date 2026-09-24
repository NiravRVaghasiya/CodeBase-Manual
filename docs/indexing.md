# Indexing: identity, concurrency, fingerprinting, caching

## Repository / working copy / index run identity

- **`RepositoryORM`** identifies a *logical* repository: its Git remote URL,
  or its filesystem root when there is none (`persistence.store.repository_identity`).
- **`WorkingCopyORM`** identifies one checkout of that repository on disk
  (`repository_id`, `root`). Two clones of the same remote -- on different
  machines, or at different local paths -- are the same `Repository` but
  different `WorkingCopy` rows.
- **`IndexRunORM`** is one indexing run, produced by exactly one working
  copy at exactly one commit (or no commit, if the tree has no Git history
  or is dirty). Re-indexing the same `(repository, commit)` pair replaces
  the prior run for that commit rather than accumulating; runs with no
  commit are never deduplicated (there's no stable key to dedupe against).

`IndexStore.latest_snapshot(identity, working_copy_root=...)` prefers the
requested working copy's own most recent run, falling back to the global
most recent run across all working copies only if that working copy has
never indexed. Without this, two working copies of the same remote would
silently overwrite each other's notion of "latest" purely based on
wall-clock indexing order -- confusing when, say, two team members index
the same remote from different local paths with different uncommitted
state. The CLI and web API both pass their resolved repository path as
`working_copy_root`.

## Concurrency

`IndexStore.save` get-or-creates the `Repository` and `WorkingCopy` rows,
then dedupes-and-inserts the `IndexRun` for a given commit. Both steps are
check-then-act and thus racy under concurrent writers targeting the same
identity/commit for the first time. Each get-or-create step catches
`IntegrityError` from the underlying unique constraint, rolls back, and
re-queries for the row the concurrent writer just committed, rather than
crashing. `save` as a whole retries once on `IntegrityError` (covering the
commit-dedup delete-then-insert race), converging on "last writer for a
given commit wins" -- the same idempotency guarantee `save` already
documents, just made safe under concurrent callers. See
`tests/unit/test_persistence.py`'s `test_get_or_create_*_recovers_from_a_concurrent_insert_race`
tests, which deterministically simulate the race window (rather than
relying on real thread timing, which SQLite's file-level locking makes
hard to trigger reliably in a test).

Not implemented: PostgreSQL advisory locking to *prevent* the race rather
than recover from it. The catch-and-retry approach above is portable
across SQLite and PostgreSQL and is sufficient at this project's scale;
advisory locking would be a targeted addition if concurrent-write volume
against a shared PostgreSQL instance ever made retries themselves
contended.

## Large-file / binary fingerprinting

`FileRecord.hash_strategy` (`FileHashStrategy.FULL_HASH` or
`METADATA_ONLY`) records how -- or whether -- a file was content-hashed.
Binary files, `.env*` files, and files over the hashing threshold
(`SecurityConfig.max_file_size`, 5 MB by default, configurable via
`[tool.codebase-manual] max-file-size` -- see `docs/security.md`) get
`METADATA_ONLY`: no content hash, but
`size_bytes`/`mtime` are still recorded. `query.drift.detect_index_drift`
compares `METADATA_ONLY` files by size+mtime instead of treating a missing
hash as unconditional change -- the original bug made every binary/large
file show as "changed" on every single `check` run, forever.

The 5 MB threshold is a module constant, not yet exposed as configuration
-- a reasonable next step if a repository's specific mix of large files
makes it worth tuning.

## Index drift (not "documentation drift")

`query.drift.detect_index_drift` (CLI: `codebase-manual check`) compares
file fingerprints between the last index and the current tree. It does
not compare generated documentation against code semantics -- so it's
named for what it actually does. A true documentation-drift feature (code
change -> affected graph entities -> affected manual sections -> is that
section stale) would be a distinct, larger feature built on top of this,
not a renaming of it.

## AI summary caching

`ai.summary_cache` keys a cached `FileSummary` on `content_hash` +
`SUMMARY_PROMPT_VERSION` + `provider.model_identifier` + `ANALYSIS_VERSION`.
`ai.manual.generate_manual` accepts an optional `cache` (anything
implementing `get_cached_value`/`set_cached_value` -- `IndexStore` does,
backed by the generic `ai_cache` table) and reuses a summary instead of
re-requesting one on every `manual` invocation, as long as the file's
content hash, the summarizer's prompt version, the model, and the
analyzer's output version are all unchanged. A file with no full content
hash (`METADATA_ONLY`) is never cached -- there's nothing stable to key it
on, so it degrades to "always regenerate," never to "silently invalidated."
