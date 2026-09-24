"""Codebase Q&A: retrieval-grounded question answering.

Retrieval supplies the facts; the model only synthesizes prose over what
was retrieved -- it never sees the whole repository, and questions with no
matching evidence are answered by saying so, not by guessing.
"""

from __future__ import annotations

from codebase_manual.ai.models import Answer, Confidence, EvidenceItem
from codebase_manual.ai.provider import AIProvider, AISynthesisError, complete_json
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.retrieval import RetrievalResult, retrieve_relevant

_SYSTEM_PROMPT = (
    "You are a precise assistant answering questions about a specific "
    "software repository. You are given a bounded set of retrieved facts "
    "(file paths, docstrings, function/class signatures) -- answer using "
    "only those facts. If they don't answer the question, say so instead "
    "of guessing. Respond with a single JSON object: "
    '{"answer": "...", "confidence": "high|medium|low"}.'
)

_NO_EVIDENCE_ANSWER = (
    "No files or symbols in the indexed repository matched this question's terms. "
    "The repository may not implement this, or different terminology is used -- "
    "try rephrasing with names that appear in the codebase."
)


def _context_block(retrieval: RetrievalResult) -> str:
    lines: list[str] = []
    for retrieved_file in retrieval.files:
        module = retrieved_file.module
        lines.append(f"### {module.path} (module: {module.module_name or '?'})")
        if module.docstring:
            lines.append(f"Docstring: {module.docstring}")
        for klass in module.classes:
            suffix = f": {klass.docstring}" if klass.docstring else ""
            lines.append(f"class {klass.name}(bases={klass.bases}){suffix}")
        for function in module.functions:
            suffix = f": {function.docstring}" if function.docstring else ""
            lines.append(f"def {function.name}(...){suffix}")
    for symbol in retrieval.symbols:
        lines.append(f"- {symbol.kind} `{symbol.identifier}` in {symbol.module_path}")
    return "\n".join(lines)


def _retrieval_evidence(retrieval: RetrievalResult) -> list[EvidenceItem]:
    evidence = [
        EvidenceItem(
            description=f"module matched terms {sorted(f.matched_terms)}",
            file_path=f.module.path,
        )
        for f in retrieval.files
    ]
    evidence.extend(
        EvidenceItem(
            description=f"{s.kind} `{s.identifier}` matched terms {sorted(s.matched_terms)}",
            file_path=s.module_path,
        )
        for s in retrieval.symbols
    )
    return evidence


def answer_question(question: str, snapshot: RepositorySnapshot, provider: AIProvider) -> Answer:
    retrieval = retrieve_relevant(question, snapshot)

    if retrieval.is_empty:
        return Answer(
            question=question, text=_NO_EVIDENCE_ANSWER, confidence=Confidence.LOW, evidence=[]
        )

    prompt = f"Question: {question}\n\nRetrieved context:\n{_context_block(retrieval)}"
    payload = complete_json(provider, system=_SYSTEM_PROMPT, prompt=prompt)

    try:
        return Answer(
            question=question,
            text=payload["answer"],
            confidence=Confidence(payload["confidence"]),
            evidence=_retrieval_evidence(retrieval),
        )
    except (KeyError, ValueError) as exc:
        raise AISynthesisError(
            f"Provider response did not match the answer schema: {payload}"
        ) from exc
