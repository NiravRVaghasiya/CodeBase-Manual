"""Codebase Q&A: retrieval-grounded question answering.

Retrieval supplies a bounded set of candidate files/symbols, each given an
opaque ID; the model synthesizes prose over what was retrieved and must
cite which candidate IDs it actually relied on. Citations are resolved
through `GroundingValidator` -- an invented ID is dropped rather than
trusted -- and confidence is computed from the strength of whatever
citations survive, never read from the model.
"""

from __future__ import annotations

from pathlib import Path

from codebase_manual.ai.confidence import confidence_from_strengths
from codebase_manual.ai.context_limits import truncate_context
from codebase_manual.ai.grounding import GroundingValidator
from codebase_manual.ai.models import Answer, Confidence, EvidenceItem
from codebase_manual.ai.provider import AIProvider, AISynthesisError, complete_json
from codebase_manual.analyzer.config import load_security_config
from codebase_manual.domain.evidence import EvidenceStrength
from codebase_manual.logging_config import get_logger
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.candidates import CandidateSet, build_candidate_set
from codebase_manual.query.retrieval import RetrievalResult, retrieve_relevant

_logger = get_logger("ai.qa")

_SYSTEM_PROMPT = (
    "You are a precise assistant answering questions about a specific "
    "software repository. You are given a bounded set of retrieved "
    "candidates (files, symbols), each labeled with an opaque ID "
    "(FILE_xxx, TEST_xxx, SYMBOL_xxx) and the reason it was retrieved, "
    "along with their docstrings/signatures and any call chains found "
    "between them -- for questions about how something works or flows, "
    "use those call chains directly rather than guessing at the "
    "architecture from isolated files. Answer using only these facts; if "
    "they don't answer the question, say so instead of guessing. List the "
    "IDs of every candidate you actually relied on in `cited_ids`; do not "
    "invent an ID. Respond with a single JSON object: "
    '{"answer": "...", "cited_ids": ["FILE_xxx", "SYMBOL_xxx"]}.'
)

_NO_EVIDENCE_ANSWER = (
    "No files or symbols in the indexed repository matched this question's terms. "
    "The repository may not implement this, or different terminology is used -- "
    "try rephrasing with names that appear in the codebase."
)


def _context_block(retrieval: RetrievalResult, candidates: CandidateSet) -> str:
    lines = [candidates.prompt_block()]
    by_path = {rf.module.path: rf for rf in retrieval.files}
    for candidate in (*candidates.files, *candidates.tests):
        rf = by_path.get(candidate.ref.identifier)
        if rf is None:
            continue
        module = rf.module
        reasons = ", ".join(signal.detail for signal in rf.signals)
        lines.append(f"### {candidate.id} details ({module.module_name or '?'}) -- {reasons}")
        if module.docstring:
            lines.append(f"Docstring: {module.docstring}")
        for klass in module.classes:
            suffix = f": {klass.docstring}" if klass.docstring else ""
            lines.append(f"class {klass.name}(bases={klass.bases}){suffix}")
        for function in module.functions:
            suffix = f": {function.docstring}" if function.docstring else ""
            lines.append(f"def {function.name}(...){suffix}")
    if retrieval.relationship_chains:
        lines.append("Call chains found between candidates:")
        lines.extend(f"  - {chain.description}" for chain in retrieval.relationship_chains)
    return "\n".join(lines)


def _retrieval_evidence(retrieval: RetrievalResult) -> list[EvidenceItem]:
    evidence = [
        EvidenceItem(
            description="; ".join(signal.detail for signal in f.signals),
            file_path=f.module.path,
        )
        for f in retrieval.files
    ]
    evidence.extend(
        EvidenceItem(
            description=f"{s.kind} `{s.identifier}`: " + "; ".join(sig.detail for sig in s.signals),
            file_path=s.module_path,
        )
        for s in retrieval.symbols
    )
    return evidence


def answer_question(question: str, snapshot: RepositorySnapshot, provider: AIProvider) -> Answer:
    retrieval = retrieve_relevant(question, snapshot)
    _logger.info(
        "retrieval found files=%d symbols=%d chains=%d",
        len(retrieval.files),
        len(retrieval.symbols),
        len(retrieval.relationship_chains),
    )

    if retrieval.is_empty:
        return Answer(
            question=question, text=_NO_EVIDENCE_ANSWER, confidence=Confidence.LOW, evidence=[]
        )

    candidates = build_candidate_set(retrieval)
    validator = GroundingValidator(candidates, snapshot)
    security_config = load_security_config(Path(snapshot.repository_root))
    context = truncate_context(
        _context_block(retrieval, candidates), security_config.max_context_size
    )
    prompt = f"Question: {question}\n\nCandidates and context:\n{context}"
    payload = complete_json(provider, system=_SYSTEM_PROMPT, prompt=prompt)

    try:
        answer_text = payload["answer"]
        cited_ids = list(payload.get("cited_ids", []))
    except KeyError as exc:
        raise AISynthesisError(
            f"Provider response did not match the answer schema: {payload}"
        ) from exc

    result = validator.resolve_candidate_ids(cited_ids)
    if result.rejected_ids:
        _logger.warning(
            "grounding rejected %d/%d cited ids verdict=%s",
            len(result.rejected_ids),
            len(cited_ids),
            result.verdict.value,
        )

    if cited_ids:
        # The model cited specific candidates -- ground evidence/confidence in
        # exactly those (rejected IDs contribute neither evidence nor strength).
        evidence = [
            EvidenceItem(
                description=f"cited candidate `{ref.identifier}`", file_path=ref.identifier
            )
            for ref in result.resolved_refs
        ]
        strengths = [EvidenceStrength.INFERRED for _ in result.resolved_refs]
    else:
        # No citations given -- fall back to the full bounded retrieval set.
        evidence = _retrieval_evidence(retrieval)
        strengths = [EvidenceStrength.INFERRED for _ in evidence]

    return Answer(
        question=question,
        text=answer_text,
        confidence=confidence_from_strengths(strengths),
        evidence=evidence,
        grounding=result.verdict,
    )
