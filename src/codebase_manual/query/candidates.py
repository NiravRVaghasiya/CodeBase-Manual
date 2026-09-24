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
