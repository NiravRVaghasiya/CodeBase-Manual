"""Deterministic keyword-overlap retrieval over a repository snapshot.

No embeddings, no LLM calls: this narrows a natural-language query down to
a bounded set of files/symbols before any AI synthesis happens, so a whole
repository is never placed into a model's context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from codebase_manual.domain.models import ClassSymbol, FunctionSymbol, PythonModule
from codebase_manual.persistence.snapshot import RepositorySnapshot

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


def _overlap(query_terms: set[str], candidate_terms: set[str]) -> tuple[int, set[str]]:
    matched = query_terms & candidate_terms
    return len(matched), matched


@dataclass
class RetrievedFile:
    module: PythonModule
    score: int
    matched_terms: set[str]


@dataclass
class RetrievedSymbol:
    identifier: str
    kind: str
    module_path: str
    score: int
    matched_terms: set[str]


@dataclass
class RetrievalResult:
    query_terms: set[str]
    files: list[RetrievedFile] = field(default_factory=list)
    symbols: list[RetrievedSymbol] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.files and not self.symbols


def _score_symbol(
    query_terms: set[str],
    symbol: FunctionSymbol | ClassSymbol,
    kind: str,
    module_path: str,
) -> RetrievedSymbol | None:
    terms = _split_identifier(symbol.name)
    if symbol.docstring:
        terms |= _tokenize(symbol.docstring)
    score, matched = _overlap(query_terms, terms)
    if score == 0:
        return None
    return RetrievedSymbol(
        identifier=symbol.qualified_name,
        kind=kind,
        module_path=module_path,
        score=score,
        matched_terms=matched,
    )


def retrieve_relevant(
    query: str,
    snapshot: RepositorySnapshot,
    *,
    top_k_files: int = 8,
    top_k_symbols: int = 12,
) -> RetrievalResult:
    """Rank files and symbols by keyword overlap with `query`.

    Returns an empty result (not a guess) when nothing overlaps -- callers
    must treat that as "no evidence found," not fall back to fabrication.
    """
    query_terms = _tokenize(query)
    files: list[RetrievedFile] = []
    symbols: list[RetrievedSymbol] = []

    for module in snapshot.modules:
        file_terms = _split_identifier(module.path)
        if module.docstring:
            file_terms |= _tokenize(module.docstring)
        score, matched = _overlap(query_terms, file_terms)
        if score > 0:
            files.append(RetrievedFile(module=module, score=score, matched_terms=matched))

        for function in module.functions:
            symbol = _score_symbol(query_terms, function, "function", module.path)
            if symbol is not None:
                symbols.append(symbol)
        for klass in module.classes:
            symbol = _score_symbol(query_terms, klass, "class", module.path)
            if symbol is not None:
                symbols.append(symbol)
            for method in klass.methods:
                symbol = _score_symbol(query_terms, method, "function", module.path)
                if symbol is not None:
                    symbols.append(symbol)

    files.sort(key=lambda item: item.score, reverse=True)
    symbols.sort(key=lambda item: item.score, reverse=True)

    return RetrievalResult(
        query_terms=query_terms,
        files=files[:top_k_files],
        symbols=symbols[:top_k_symbols],
    )
