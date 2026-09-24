"""Deterministic, multi-signal retrieval over a repository snapshot.

No embeddings, no LLM calls: this narrows a natural-language query down to
a bounded set of files/symbols -- and the graph relationships directly
connecting them -- before any AI synthesis happens, so a whole repository
is never placed into a model's context.

Retrieval runs in stages, each contributing an explainable, weighted
`MatchSignal` to whatever it touches:

1. Lexical name match (exact bare name, or a component of the qualified
   name/path).
2. Docstring match.
3. Relationship expansion: entities directly connected (via CALLS/IMPORTS/
   INHERITS/TESTS) to whatever matched lexically.
4. Directory proximity: sibling files of a lexically matched file.

Every retrieved item exposes exactly which signals caused it to be
retrieved -- retrieval is never a black box the AI (or a person) has to
trust blindly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from codebase_manual.domain.models import (
    ClassSymbol,
    EntityKind,
    EntityRef,
    FunctionSymbol,
    PythonModule,
    RelationshipKind,
)
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.graph import RelationshipGraph

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "for",
    "and",
    "or",
    "is",
    "are",
    "how",
    "what",
    "does",
    "do",
    "i",
    "want",
    "add",
    "change",
    "with",
    "should",
    "work",
    "works",
    "this",
    "that",
    "it",
    "be",
    "can",
    "new",
    "where",
}


def _tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in _STOPWORDS
    }


def _split_identifier(name: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    spaced = re.sub(r"[_./]", " ", spaced)
    return _tokenize(spaced)


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


class MatchReason(StrEnum):
    NAME_EXACT = "name_exact"
    QUALIFIED_NAME = "qualified_name"
    DOCSTRING = "docstring"
    RELATIONSHIP = "relationship"
    DIRECTORY_PROXIMITY = "directory_proximity"


# Explainable, deterministic weights per signal kind -- never inferred from
# an LLM, and documented here as the single source of truth for scoring.
_REASON_WEIGHT: dict[MatchReason, int] = {
    MatchReason.NAME_EXACT: 5,
    MatchReason.QUALIFIED_NAME: 4,
    MatchReason.DOCSTRING: 3,
    MatchReason.RELATIONSHIP: 2,
    MatchReason.DIRECTORY_PROXIMITY: 1,
}


@dataclass(frozen=True)
class MatchSignal:
    """One concrete reason a candidate was retrieved."""

    reason: MatchReason
    detail: str

    @property
    def weight(self) -> int:
        return _REASON_WEIGHT[self.reason]


@dataclass
class RetrievedFile:
    module: PythonModule
    signals: list[MatchSignal] = field(default_factory=list)

    @property
    def score(self) -> int:
        return sum(signal.weight for signal in self.signals)


@dataclass
class RetrievedSymbol:
    identifier: str
    kind: str
    module_path: str
    signals: list[MatchSignal] = field(default_factory=list)

    @property
    def score(self) -> int:
        return sum(signal.weight for signal in self.signals)


@dataclass(frozen=True)
class RelationshipStep:
    source_identifier: str
    kind: str
    target_identifier: str
    evidence: str


@dataclass
class RelationshipChain:
    """A short forward chain of CALLS edges connecting retrieved symbols.

    Gives the AI a call-flow answer to "how does X work" questions directly,
    rather than expecting it to reconstruct one from isolated file/symbol
    facts.
    """

    steps: list[RelationshipStep]

    @property
    def description(self) -> str:
        if not self.steps:
            return ""
        parts = [self.steps[0].source_identifier]
        for step in self.steps:
            parts.append(f"--{step.kind}-->")
            parts.append(step.target_identifier)
        return " ".join(parts)


@dataclass
class RetrievalResult:
    query_terms: set[str]
    files: list[RetrievedFile] = field(default_factory=list)
    symbols: list[RetrievedSymbol] = field(default_factory=list)
    relationship_chains: list[RelationshipChain] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.files and not self.symbols


def _name_signal(
    query_terms: set[str], bare_name: str, extra_terms: set[str]
) -> MatchSignal | None:
    if bare_name.lower() in query_terms:
        return MatchSignal(MatchReason.NAME_EXACT, f"name exactly matches `{bare_name}`")
    matched = query_terms & extra_terms
    if matched:
        return MatchSignal(MatchReason.QUALIFIED_NAME, f"name matches {sorted(matched)}")
    return None


def _docstring_signal(query_terms: set[str], docstring: str | None) -> MatchSignal | None:
    if not docstring:
        return None
    matched = query_terms & _tokenize(docstring)
    if not matched:
        return None
    return MatchSignal(MatchReason.DOCSTRING, f"docstring matches {sorted(matched)}")


def _file_signals(query_terms: set[str], module: PythonModule) -> list[MatchSignal]:
    filename = module.path.rsplit("/", 1)[-1]
    bare_name = filename[:-3] if filename.endswith(".py") else filename
    signals = []
    name_signal = _name_signal(query_terms, bare_name, _split_identifier(module.path))
    if name_signal:
        signals.append(name_signal)
    docstring_signal = _docstring_signal(query_terms, module.docstring)
    if docstring_signal:
        signals.append(docstring_signal)
    return signals


def _symbol_signals(
    query_terms: set[str], symbol: FunctionSymbol | ClassSymbol
) -> list[MatchSignal]:
    signals = []
    name_signal = _name_signal(query_terms, symbol.name, _split_identifier(symbol.qualified_name))
    if name_signal:
        signals.append(name_signal)
    docstring_signal = _docstring_signal(query_terms, symbol.docstring)
    if docstring_signal:
        signals.append(docstring_signal)
    return signals


@dataclass
class _SymbolIndex:
    """Lookup tables from an `EntityRef` back to the file/symbol it addresses."""

    modules_by_name: dict[str, PythonModule] = field(default_factory=dict)
    modules_by_path: dict[str, PythonModule] = field(default_factory=dict)
    symbol_kind_and_module: dict[str, tuple[str, str]] = field(default_factory=dict)

    @classmethod
    def build(cls, snapshot: RepositorySnapshot) -> _SymbolIndex:
        index = cls()
        for module in snapshot.modules:
            index.modules_by_path[module.path] = module
            if module.module_name:
                index.modules_by_name[module.module_name] = module
            for function in module.functions:
                index.symbol_kind_and_module[function.qualified_name] = ("function", module.path)
            for klass in module.classes:
                index.symbol_kind_and_module[klass.qualified_name] = ("class", module.path)
                for method in klass.methods:
                    index.symbol_kind_and_module[method.qualified_name] = ("function", module.path)
        return index

    def file_for_ref(self, ref: EntityRef) -> PythonModule | None:
        if ref.kind is EntityKind.FILE:
            return self.modules_by_path.get(ref.identifier)
        if ref.kind is EntityKind.MODULE:
            return self.modules_by_name.get(ref.identifier)
        return None

    def symbol_for_ref(self, ref: EntityRef) -> tuple[str, str] | None:
        if ref.kind in (EntityKind.CLASS, EntityKind.FUNCTION):
            return self.symbol_kind_and_module.get(ref.identifier)
        return None


_EXPANSION_KINDS = (
    RelationshipKind.CALLS,
    RelationshipKind.IMPORTS,
    RelationshipKind.INHERITS,
    RelationshipKind.TESTS,
)


def _seed_refs(
    files: dict[str, RetrievedFile], symbols: dict[str, RetrievedSymbol]
) -> list[EntityRef]:
    refs: list[EntityRef] = []
    for retrieved_file in files.values():
        module_name = retrieved_file.module.module_name
        if module_name:
            refs.append(EntityRef(kind=EntityKind.MODULE, identifier=module_name))
    for identifier, retrieved_symbol in symbols.items():
        kind = EntityKind.CLASS if retrieved_symbol.kind == "class" else EntityKind.FUNCTION
        refs.append(EntityRef(kind=kind, identifier=identifier))
    return refs


def _add_relationship_signal(
    files: dict[str, RetrievedFile],
    symbols: dict[str, RetrievedSymbol],
    index: _SymbolIndex,
    ref: EntityRef,
    detail: str,
) -> None:
    signal = MatchSignal(MatchReason.RELATIONSHIP, detail)

    module = index.file_for_ref(ref)
    if module is not None:
        if module.path in files:
            files[module.path].signals.append(signal)
        else:
            files[module.path] = RetrievedFile(module=module, signals=[signal])
        return

    resolved = index.symbol_for_ref(ref)
    if resolved is not None:
        kind, module_path = resolved
        if ref.identifier in symbols:
            symbols[ref.identifier].signals.append(signal)
        else:
            symbols[ref.identifier] = RetrievedSymbol(
                identifier=ref.identifier, kind=kind, module_path=module_path, signals=[signal]
            )


def _expand_via_relationships(
    files: dict[str, RetrievedFile],
    symbols: dict[str, RetrievedSymbol],
    graph: RelationshipGraph,
    index: _SymbolIndex,
) -> None:
    for seed in _seed_refs(files, symbols):
        edges = [*graph.outgoing(seed, _EXPANSION_KINDS), *graph.incoming(seed, _EXPANSION_KINDS)]
        for edge in edges:
            other = edge.target if edge.source == seed else edge.source
            detail = f"connected via {edge.kind.value} to `{seed.identifier}`"
            _add_relationship_signal(files, symbols, index, other, detail)


def _expand_via_directory_proximity(
    files: dict[str, RetrievedFile], snapshot: RepositorySnapshot, seed_paths: set[str]
) -> None:
    seed_dirs = {_dirname(path) for path in seed_paths if _dirname(path)}
    if not seed_dirs:
        return
    for module in snapshot.modules:
        if module.path in files:
            continue
        directory = _dirname(module.path)
        if directory in seed_dirs:
            files[module.path] = RetrievedFile(
                module=module,
                signals=[
                    MatchSignal(
                        MatchReason.DIRECTORY_PROXIMITY,
                        f"same directory as a matched file ({directory})",
                    )
                ],
            )


def _call_chains_from(
    seed: EntityRef, graph: RelationshipGraph, *, max_depth: int
) -> list[list[RelationshipStep]]:
    """All simple forward CALLS paths from `seed`, up to `max_depth` hops."""
    chains: list[list[RelationshipStep]] = []
    stack: list[tuple[EntityRef, list[RelationshipStep], frozenset[EntityRef]]] = [
        (seed, [], frozenset({seed}))
    ]
    while stack:
        current, steps, visited = stack.pop()
        edges = graph.outgoing(current, (RelationshipKind.CALLS,)) if len(steps) < max_depth else []
        unvisited = [edge for edge in edges if edge.target not in visited]
        if not unvisited:
            if steps:
                chains.append(steps)
            continue
        for edge in unvisited:
            step = RelationshipStep(
                source_identifier=edge.source.identifier,
                kind=edge.kind.value,
                target_identifier=edge.target.identifier,
                evidence=edge.evidence,
            )
            stack.append((edge.target, [*steps, step], visited | {edge.target}))
    return chains


def _relationship_chains(
    seed_identifiers: list[str], graph: RelationshipGraph, *, max_depth: int, max_chains: int
) -> list[RelationshipChain]:
    chains: list[RelationshipChain] = []
    seen_signatures: set[tuple[str, ...]] = set()

    for identifier in seed_identifiers:
        seed = EntityRef(kind=EntityKind.FUNCTION, identifier=identifier)
        for steps in _call_chains_from(seed, graph, max_depth=max_depth):
            signature = tuple(step.target_identifier for step in steps)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            chains.append(RelationshipChain(steps=steps))
            if len(chains) >= max_chains:
                return chains

    return chains


def retrieve_relevant(
    query: str,
    snapshot: RepositorySnapshot,
    *,
    top_k_files: int = 8,
    top_k_symbols: int = 12,
    max_chains: int = 6,
    chain_depth: int = 3,
) -> RetrievalResult:
    """Rank files/symbols relevant to `query`, plus the relationship chains connecting them.

    Returns an empty result (not a guess) when nothing overlaps lexically --
    callers must treat that as "no evidence found," not fall back to
    fabrication. Relationship expansion and directory proximity only ever
    add to what lexical matching already found; they never run on their own.
    """
    query_terms = _tokenize(query)
    files: dict[str, RetrievedFile] = {}
    symbols: dict[str, RetrievedSymbol] = {}

    for module in snapshot.modules:
        file_signals = _file_signals(query_terms, module)
        if file_signals:
            files[module.path] = RetrievedFile(module=module, signals=file_signals)

        for function in module.functions:
            signals = _symbol_signals(query_terms, function)
            if signals:
                symbols[function.qualified_name] = RetrievedSymbol(
                    identifier=function.qualified_name,
                    kind="function",
                    module_path=module.path,
                    signals=signals,
                )
        for klass in module.classes:
            class_signals = _symbol_signals(query_terms, klass)
            if class_signals:
                symbols[klass.qualified_name] = RetrievedSymbol(
                    identifier=klass.qualified_name,
                    kind="class",
                    module_path=module.path,
                    signals=class_signals,
                )
            for method in klass.methods:
                method_signals = _symbol_signals(query_terms, method)
                if method_signals:
                    symbols[method.qualified_name] = RetrievedSymbol(
                        identifier=method.qualified_name,
                        kind="function",
                        module_path=module.path,
                        signals=method_signals,
                    )

    if not files and not symbols:
        return RetrievalResult(query_terms=query_terms)

    seed_file_paths = set(files)
    index = _SymbolIndex.build(snapshot)
    graph = RelationshipGraph(snapshot.relationships)

    _expand_via_relationships(files, symbols, graph, index)
    _expand_via_directory_proximity(files, snapshot, seed_file_paths)

    ranked_files = sorted(files.values(), key=lambda f: f.score, reverse=True)[:top_k_files]
    ranked_symbols = sorted(symbols.values(), key=lambda s: s.score, reverse=True)[:top_k_symbols]

    seed_function_ids = [s.identifier for s in ranked_symbols if s.kind == "function"]
    chains = _relationship_chains(
        seed_function_ids, graph, max_depth=chain_depth, max_chains=max_chains
    )

    return RetrievalResult(
        query_terms=query_terms,
        files=ranked_files,
        symbols=ranked_symbols,
        relationship_chains=chains,
    )
