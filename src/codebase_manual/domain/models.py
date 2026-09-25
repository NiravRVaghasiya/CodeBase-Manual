"""Core entities of the code intelligence model.

These models represent deterministically extracted facts about a repository
(files, Git metadata, Python structure). They must never carry AI-inferred
interpretation -- that belongs to a separate layer added in later phases.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# Bump when `PythonModule`'s shape changes in a way that would make an old,
# persisted instance unsafe to reuse without re-analysis -- consumed by
# `analyzer.registry`'s incremental reuse decision and `ai.summary_cache`'s
# cache keys, both of which need "is this fact shape still what the current
# analyzer would produce" answered the same way, from one place.
PYTHON_MODULE_SCHEMA_VERSION = "1"


class SourceLocation(BaseModel):
    """A location within a source file, using 1-indexed line numbers."""

    line_start: int
    line_end: int
    col_start: int | None = None
    col_end: int | None = None


class ParameterKind(StrEnum):
    POSITIONAL = "positional"
    VAR_POSITIONAL = "var_positional"
    KEYWORD_ONLY = "keyword_only"
    VAR_KEYWORD = "var_keyword"


class Parameter(BaseModel):
    name: str
    annotation: str | None = None
    default: str | None = None
    kind: ParameterKind


class ImportedName(BaseModel):
    """A single imported name, e.g. one alias in `import foo, bar` or `from x import a, b`."""

    module: str
    name: str | None = None
    alias: str | None = None
    is_relative: bool = False
    relative_level: int = 0
    location: SourceLocation


class Decorator(BaseModel):
    expression: str
    location: SourceLocation


class Variable(BaseModel):
    name: str
    annotation: str | None = None
    scope: str
    is_constant: bool = False
    location: SourceLocation


class AssignedValueKind(StrEnum):
    """The shape of an assignment's right-hand side, as far as type inference can use it.

    CALL      -- `x = Foo(...)` / `x = get_repo(...)`: `value_expression` is the
                 unparsed callee, resolved as a construction or factory call.
    NAME      -- `x = other_name`: `value_expression` is the bare name, resolved
                 by propagating whatever type (if any) `other_name` already has.
    ATTRIBUTE -- `x = self.other` / `x = cls.other`: `value_expression` is the
                 unparsed dotted text, resolved via the owning class's own
                 attribute types.
    """

    CALL = "call"
    NAME = "name"
    ATTRIBUTE = "attribute"


class Assignment(BaseModel):
    """A single `target = value` assignment found directly in a function's own scope.

    Only assignments whose target is a plain local name or a `self.`/`cls.`
    attribute, and whose value is a call, a bare name, or a dotted attribute
    access, are recorded -- anything else (tuple unpacking, subscripts,
    literals, binary expressions) carries no usable type information and is
    not recorded at all. See `domain.type_inference` for how these are turned
    into best-effort attribute/local-variable types.
    """

    model_config = {"frozen": True}

    target: str
    is_attribute: bool
    value_kind: AssignedValueKind
    value_expression: str
    line: int


class CallArgument(BaseModel):
    """One argument of a call expression, unparsed.

    `keyword` is the parameter name for `f(x=1)`, `None` for a positional
    argument (including a `*args`-unpacked one -- `value` already contains
    the leading `*` from `ast.unparse`) or a `**kwargs`-style unpacking
    (`value` contains the leading `**`).
    """

    model_config = {"frozen": True}

    value: str
    keyword: str | None = None


class CallSite(BaseModel):
    """A single call expression found directly in a function's own scope.

    Never includes calls made inside a nested function/lambda/class defined
    within this function's body -- those belong to that nested scope, not
    this one. `expression` is the raw, unresolved callee text (e.g.
    "self.foo", "Bar"); resolution against known symbols happens in
    `domain.relationships`. `arguments` are captured unparsed, unresolved --
    consumers that need to resolve an argument to a known symbol (e.g.
    `query.api_endpoints`'s `add_api_route` handler) do that resolution
    themselves.
    """

    model_config = {"frozen": True}

    expression: str
    line: int
    column: int | None = None
    containing_symbol_id: str
    arguments: list[CallArgument] = Field(default_factory=list)


class FunctionSymbol(BaseModel):
    name: str
    qualified_name: str
    parameters: list[Parameter] = Field(default_factory=list)
    return_annotation: str | None = None
    decorators: list[Decorator] = Field(default_factory=list)
    is_async: bool = False
    is_method: bool = False
    docstring: str | None = None
    location: SourceLocation
    calls: list[CallSite] = Field(default_factory=list)
    assignments: list[Assignment] = Field(default_factory=list)


class ClassSymbol(BaseModel):
    name: str
    qualified_name: str
    bases: list[str] = Field(default_factory=list)
    decorators: list[Decorator] = Field(default_factory=list)
    docstring: str | None = None
    methods: list[FunctionSymbol] = Field(default_factory=list)
    class_variables: list[Variable] = Field(default_factory=list)
    location: SourceLocation


class PythonModule(BaseModel):
    """Structural facts extracted from a single source file.

    Despite the name, nothing here requires the source to actually be
    Python -- it is the fact *shape* every `LanguageAnalyzer` must produce
    (see `analyzer.registry.LanguageAnalyzer`, `docs/analyzers.md`).
    `analyzer.typescript_analyzer` produces the same shape for TypeScript.
    """

    path: str
    module_name: str | None = None
    docstring: str | None = None
    imports: list[ImportedName] = Field(default_factory=list)
    functions: list[FunctionSymbol] = Field(default_factory=list)
    classes: list[ClassSymbol] = Field(default_factory=list)
    variables: list[Variable] = Field(default_factory=list)
    calls: list[CallSite] = Field(default_factory=list)
    parse_error: str | None = None


class FileLanguage(StrEnum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    TOML = "toml"
    YAML = "yaml"
    JSON = "json"
    MARKDOWN = "markdown"
    SQL = "sql"
    ENV = "env"
    UNKNOWN = "unknown"


class FileHashStrategy(StrEnum):
    """How `FileRecord.content_hash` (if any) was computed.

    FULL_HASH      -- a hash of the file's entire content; drift detection
                       can compare hashes directly.
    METADATA_ONLY  -- no content hash (the file is binary, or larger than
                       the hashing threshold); drift detection must fall
                       back to comparing `size_bytes`/`mtime` instead of
                       treating the file as unconditionally changed.
    """

    FULL_HASH = "full_hash"
    METADATA_ONLY = "metadata_only"


class FileRecord(BaseModel):
    path: str
    size_bytes: int
    extension: str
    language: FileLanguage
    is_binary: bool = False
    content_hash: str | None = None
    hash_strategy: FileHashStrategy = FileHashStrategy.METADATA_ONLY
    mtime: float | None = None


def file_fingerprint_matches(previous: FileRecord, current: FileRecord) -> bool:
    """Whether `current` is the same file `previous` was, by content hash or,
    for a file with no full hash (binary, oversized, `.env*`), by size/mtime.

    The single source of truth for "this file hasn't changed" -- used by
    `query.drift.detect_index_drift` (has it changed since the last index)
    and `analyzer.registry.analyze_repository_incremental` (can its prior
    analysis be reused instead of re-parsed). Lives in `domain.models`,
    not either of those modules, so neither has to import the other for it.
    """
    if (
        previous.hash_strategy is FileHashStrategy.FULL_HASH
        and current.hash_strategy is FileHashStrategy.FULL_HASH
        and previous.content_hash is not None
        and current.content_hash is not None
    ):
        return previous.content_hash == current.content_hash

    return (
        previous.mtime is not None
        and current.mtime is not None
        and previous.size_bytes == current.size_bytes
        and previous.mtime == current.mtime
    )


class DirectoryRecord(BaseModel):
    path: str


class GitMetadata(BaseModel):
    is_git_repository: bool
    commit_sha: str | None = None
    branch: str | None = None
    is_dirty: bool | None = None
    remote_url: str | None = None


class RepositoryRecord(BaseModel):
    root: str
    git: GitMetadata
    indexed_at: datetime


class ScanResult(BaseModel):
    """Result of scanning a repository: its structure plus Git identity."""

    repository: RepositoryRecord
    directories: list[DirectoryRecord]
    files: list[FileRecord]
    ignored_file_paths: list[str] = Field(default_factory=list)

    @property
    def python_files(self) -> list[FileRecord]:
        return [f for f in self.files if f.language is FileLanguage.PYTHON and not f.is_binary]


class EntityKind(StrEnum):
    """The kind of thing an `EntityRef` addresses."""

    REPOSITORY = "repository"
    FILE = "file"
    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"


class EntityRef(BaseModel):
    """A stable, addressable reference to a repository entity.

    `identifier` is the repo-relative path for FILE, the dotted module name
    for MODULE, or the qualified name for FUNCTION/CLASS.
    """

    model_config = {"frozen": True}

    kind: EntityKind
    identifier: str


class UnresolvedCall(BaseModel):
    """A call expression that was found but could not be resolved to a known symbol.

    Recorded explicitly rather than silently dropped -- its presence *is*
    the evidence of "unknown" (see `domain.evidence.EvidenceStrength.UNKNOWN`).
    This is deliberately not a `Relationship`: a `Relationship`'s `target`
    must be a real, already-resolved entity, and an unresolved call has none
    -- forcing it into the `Relationship` shape would mean either fabricating
    a target or silently dropping the fact, both of which this exists to
    avoid. Produced by `domain.relationships.build_relationships_with_unresolved`.
    """

    model_config = {"frozen": True}

    source: EntityRef
    expression: str
    location: SourceLocation


class RelationshipKind(StrEnum):
    """Relationship kinds with sufficient deterministic evidence to assert.

    `depends_on` is intentionally not a stored kind: it is a derived view
    (any of CONTAINS/IMPORTS/CALLS/INHERITS) computed by the query layer,
    not a separately observed fact. `tested_by` is the inverse of TESTS and
    is likewise derived rather than duplicated in storage.
    """

    CONTAINS = "contains"
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    TESTS = "tests"


class Relationship(BaseModel):
    """A deterministic fact linking two entities, with evidence for why it was asserted."""

    kind: RelationshipKind
    source: EntityRef
    target: EntityRef
    evidence: str
    location: SourceLocation | None = None
