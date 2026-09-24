# Codebase Manual

A living manual for software repositories.

Codebase Manual analyzes a repository deterministically (file structure, Git
metadata, Python AST, import/call/inheritance/test relationships) and builds
a code intelligence model that answers two kinds of questions:

- **Understanding**: What does this project/file/function do? How does a
  feature flow through the system? What depends on this component?
- **Change planning**: I want to add/change X — where should I work, what
  files should I modify or create, what tests should change, what could be
  affected?

Deterministic analysis establishes facts (`domain`, `repository`,
`analyzer`, persisted by `persistence`). An AI layer (`ai`) interprets those
facts — every recommendation carries a confidence level and evidence tying
it back to the facts. AI never invents a relationship or a file path.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy src
```

## CLI

```bash
# Index a repository (scan + analyze + derive relationships + persist).
uv run codebase-manual index /path/to/repository

# Ask a question, grounded in retrieved evidence.
uv run codebase-manual ask /path/to/repository "How does authentication work?"

# Plan a change: files to modify/create, tests, potential impact.
uv run codebase-manual change /path/to/repository "Add Google OAuth"

# See what depends on a file/module/class/function, and why.
uv run codebase-manual impact /path/to/repository app/auth/service.py

# Generate the living manual (Markdown).
uv run codebase-manual manual /path/to/repository --out MANUAL.md

# Compare the working tree against the last index (documentation drift).
uv run codebase-manual check /path/to/repository

# Serve the web UI over an already-indexed repository.
uv run codebase-manual serve /path/to/repository
```

`ask`, `change`, `impact`, and `manual` read from the persisted index, so
run `index` first. `impact` and `manual` still work without an AI provider
configured (they report deterministic facts and say so honestly); `ask` and
`change` require one.

## Configuration

- `CODEBASE_MANUAL_DATABASE_URL` — any SQLAlchemy URL. Defaults to a SQLite
  file at `<repository>/.codebase_manual/index.db`. Point this at PostgreSQL
  in production (install the `postgres` extra: `uv sync --extra postgres`).
- `ANTHROPIC_API_KEY` — required for `ask`/`change`, and for AI summaries in
  `manual`/`impact`. Without it, those commands report the deterministic
  facts they have and say plainly that AI synthesis is unavailable.
- `CODEBASE_MANUAL_AI_MODEL` — overrides the default Anthropic model.

## Architecture

```text
src/codebase_manual/
    cli/            command-line interface (Typer)
    domain/         core entities, relationships, facts vs. interpretation
    repository/     repository scanning, Git metadata, Git history
    analyzer/       Python AST analysis, dispatched by a language registry
    persistence/    SQLAlchemy schema, idempotent indexing, snapshot reads
    query/          retrieval, relationship graph traversal, drift detection
    ai/             provider interface + grounded summaries/Q&A/change
                    planning/impact explanation (Confidence + evidence)
    api/            FastAPI web UI (server-rendered, Jinja2)
```

## Known scope limits

- Only Python is analyzed. The analyzer is behind a language registry
  (`analyzer/registry.py`) so adding a language later doesn't require
  restructuring the domain model, but no other language is implemented.
- External integrations (GitHub/GitLab/Jira/Slack/CI, per the plan's
  "post-MVP" phase) are not implemented — they need real credentials this
  environment doesn't have, and a non-functional stub would misrepresent
  what the tool can do.
- Relationships are deliberately conservative: `contains`, `imports`,
  `calls`, `inherits`, and `tests` are derived with concrete syntactic
  evidence; ambiguous references (calls through arbitrary local variables,
  unresolved base classes) are dropped rather than guessed.
