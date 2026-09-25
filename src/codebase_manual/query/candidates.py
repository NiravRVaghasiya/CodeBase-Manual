"""Candidate ID tables: the only way an LLM may reference a repository entity.

Before any AI call that must produce structured references to specific
files, symbols, or tests, the caller builds a `CandidateSet` from
deterministic retrieval results and gives the model only these IDs -- never
raw paths or names. `CandidateSet.resolve` is the sole path back to a real
entity: an ID the model invents simply fails to resolve, rather than
silently becoming a fact (see `ai.grounding.GroundingValidator`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codebase_manual.domain.models import EntityKind, EntityRef
from codebase_manual.domain.relationships import is_test_path
from codebase_manual.query.retrieval import RetrievalResult


@dataclass(frozen=True)
class Candidate:
    id: str
    ref: EntityRef
    label: str


@dataclass
class CandidateSet:
    """A bounded set of entities an LLM may reference, addressed by opaque ID."""

    files: list[Candidate] = field(default_factory=list)
    symbols: list[Candidate] = field(default_factory=list)
    tests: list[Candidate] = field(default_factory=list)
    _by_id: dict[str, Candidate] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {c.id: c for c in (*self.files, *self.symbols, *self.tests)}

    def resolve(self, candidate_id: str) -> EntityRef | None:
        candidate = self._by_id.get(candidate_id)
        return candidate.ref if candidate else None

    def resolve_many(self, candidate_ids: list[str]) -> tuple[list[EntityRef], list[str]]:
        """Split `candidate_ids` into (resolved refs, unknown ids), preserving order."""
        resolved: list[EntityRef] = []
        unknown: list[str] = []
        for candidate_id in candidate_ids:
            ref = self.resolve(candidate_id)
            if ref is None:
                unknown.append(candidate_id)
            else:
                resolved.append(ref)
        return resolved, unknown

    @property
    def is_empty(self) -> bool:
        return not self.files and not self.symbols and not self.tests

    def prompt_block(self) -> str:
        """Render the candidate tables for inclusion in an LLM prompt."""
        lines: list[str] = []
        if self.files:
            lines.append("FILES")
            lines.extend(f"{c.id} = {c.label}" for c in self.files)
        if self.tests:
            lines.append("TESTS")
            lines.extend(f"{c.id} = {c.label}" for c in self.tests)
        if self.symbols:
            lines.append("SYMBOLS")
            lines.extend(f"{c.id} = {c.label}" for c in self.symbols)
        return "\n".join(lines)


def _looks_like_test(ref: EntityRef) -> bool:
    """Whether `ref` names a test entity, for a MODULE/FUNCTION identifier (dotted) or a
    FILE identifier (a repo-relative path) -- `is_test_path` expects path-shaped input,
    so a dotted name is converted to a path shape first (`pkg.test_x` -> `pkg/test_x`).
    """
    return is_test_path(ref.identifier.replace(".", "/"))


def build_candidate_set_from_refs(refs: list[EntityRef]) -> CandidateSet:
    """Assign candidate IDs directly to a fixed, already-computed list of entity refs.

    Unlike `build_candidate_set`, this has no retrieval result to draw
    labels/signals from -- it exists for callers whose candidates are a
    deterministic fact list rather than a retrieval result (e.g. impact
    analysis's dependents/tests), so free prose can still cite by ID instead
    of writing out raw identifiers a model could later invent variations on.
    Duplicate refs are assigned only one ID each, in first-seen order.
    """
    files: list[Candidate] = []
    symbols: list[Candidate] = []
    tests: list[Candidate] = []
    file_i = test_i = symbol_i = 1
    seen: set[EntityRef] = set()

    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)

        if ref.kind in (EntityKind.FILE, EntityKind.MODULE):
            if _looks_like_test(ref):
                tests.append(Candidate(id=f"TEST_{test_i:03d}", ref=ref, label=ref.identifier))
                test_i += 1
            else:
                files.append(Candidate(id=f"FILE_{file_i:03d}", ref=ref, label=ref.identifier))
                file_i += 1
        elif ref.kind in (EntityKind.FUNCTION, EntityKind.CLASS):
            if _looks_like_test(ref):
                tests.append(Candidate(id=f"TEST_{test_i:03d}", ref=ref, label=ref.identifier))
                test_i += 1
            else:
                symbols.append(
                    Candidate(id=f"SYMBOL_{symbol_i:03d}", ref=ref, label=ref.identifier)
                )
                symbol_i += 1

    return CandidateSet(files=files, symbols=symbols, tests=tests)


def build_candidate_set(retrieval: RetrievalResult) -> CandidateSet:
    """Assign stable candidate IDs to a retrieval result's files, tests, and symbols."""
    files: list[Candidate] = []
    tests: list[Candidate] = []
    file_i = 1
    test_i = 1
    for retrieved_file in retrieval.files:
        path = retrieved_file.module.path
        candidate = Candidate(
            id="",  # placeholder, set below once the right counter is known
            ref=EntityRef(kind=EntityKind.FILE, identifier=path),
            label=path,
        )
        if is_test_path(path):
            candidate = Candidate(id=f"TEST_{test_i:03d}", ref=candidate.ref, label=path)
            test_i += 1
            tests.append(candidate)
        else:
            candidate = Candidate(id=f"FILE_{file_i:03d}", ref=candidate.ref, label=path)
            file_i += 1
            files.append(candidate)

    symbols: list[Candidate] = []
    for symbol_i, retrieved_symbol in enumerate(retrieval.symbols, start=1):
        kind = EntityKind.CLASS if retrieved_symbol.kind == "class" else EntityKind.FUNCTION
        symbols.append(
            Candidate(
                id=f"SYMBOL_{symbol_i:03d}",
                ref=EntityRef(kind=kind, identifier=retrieved_symbol.identifier),
                label=(
                    f"{retrieved_symbol.identifier} "
                    f"({retrieved_symbol.kind}, in {retrieved_symbol.module_path})"
                ),
            )
        )

    return CandidateSet(files=files, symbols=symbols, tests=tests)
