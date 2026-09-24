"""Core entities of the code intelligence model.

These models represent deterministically extracted facts about a repository
(files, Git metadata, Python structure). They must never carry AI-inferred
interpretation -- that belongs to a separate layer added in later phases.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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


class CallSite(BaseModel):
    """A single call expression found directly in a function's own scope.

    Never includes calls made inside a nested function/lambda/class defined
    within this function's body -- those belong to that nested scope, not
    this one. `expression` is the raw, unresolved callee text (e.g.
    "self.foo", "Bar"); resolution against known symbols happens in
    `domain.relationships`.
    """

    model_config = {"frozen": True}

    expression: str
    line: int
    column: int | None = None
    containing_symbol_id: str


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
    """Structural facts extracted from a single Python source file."""

    path: str
    module_name: str | None = None
    docstring: str | None = None
    imports: list[ImportedName] = Field(default_factory=list)
    functions: list[FunctionSymbol] = Field(default_factory=list)
    classes: list[ClassSymbol] = Field(default_factory=list)
    variables: list[Variable] = Field(default_factory=list)
    parse_error: str | None = None


class FileLanguage(StrEnum):
    PYTHON = "python"
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
