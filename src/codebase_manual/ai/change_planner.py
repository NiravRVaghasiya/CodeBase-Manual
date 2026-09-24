"""Change planning: the system's core differentiating capability.

Deterministic retrieval (`query.retrieval`) finds candidate files/symbols/
tests -- including directory siblings of the closest match and entities
connected to it via the relationship graph. Those candidates are given to
the model as opaque IDs (`FILE_001`, `SYMBOL_002`, ...) -- never as raw
paths the model could instead invent a plausible-sounding substitute for.
The model proposes reasoning and selects among those IDs;
`GroundingValidator` resolves every ID back to a real entity before it can
appear in the returned `ChangePlan`, and confidence is computed from the
strength of the evidence behind whatever survives validation, never taken
from the model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codebase_manual.ai.confidence import confidence_from_strengths
from codebase_manual.ai.context_limits import truncate_context
from codebase_manual.ai.grounding import GroundingValidator, ValidationVerdict, combine_verdicts
from codebase_manual.ai.models import (
    ChangePlan,
    Confidence,
    EvidenceItem,
    FileAction,
    FileRecommendation,
)
from codebase_manual.ai.provider import AIProvider, AISynthesisError, complete_json
from codebase_manual.analyzer.config import load_security_config
from codebase_manual.domain.evidence import EvidenceStrength
from codebase_manual.logging_config import get_logger
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.candidates import CandidateSet, build_candidate_set
from codebase_manual.query.retrieval import MatchReason, RetrievalResult, retrieve_relevant

_logger = get_logger("ai.change_planner")

_SYSTEM_PROMPT = (
    "You are a precise software change-planning assistant. You are given "
    "candidate files, tests, and symbols from the repository, each labeled "
    "with an opaque ID (FILE_xxx, TEST_xxx, SYMBOL_xxx) and the reason it "
    "was retrieved (a name/docstring match, a directory sibling of a "
    "match, or a relationship connecting it to a match), plus any call "
    "chains found between candidates. Reference existing repository "
    "entities ONLY by these IDs -- never by writing out a path or symbol "
    "name yourself for files_to_modify, tests_to_update, or "
    "relevant_symbols; an ID you invent will be rejected. For "
    "files_to_create, propose a real new file path following a directory-"
    "sibling convention shown among the candidates, or omit it if no clear "
    "convention exists rather than inventing a plausible-sounding path. "
    "Respond with a single JSON object: "
    '{"goal": "...", "subsystem": "..." or null, '
    '"implementation_pattern": "..." or null, '
    '"files_to_modify": [{"id": "FILE_xxx", "reasoning": "..."}], '
    '"files_to_create": [{"path": "...", "reasoning": "..."}], '
    '"relevant_symbols": ["SYMBOL_xxx"], "tests_to_update": ["TEST_xxx"], '
    '"potential_impact": ["..."], "reasoning": "..."}'
)

_NO_EVIDENCE_REASONING = (
    "No files or symbols in the indexed repository matched this request's terms, "
    "so no subsystem, pattern, or file recommendations can be grounded in evidence."
)


def _has_directory_convention_evidence(retrieval: RetrievalResult) -> bool:
    return any(
        signal.reason is MatchReason.DIRECTORY_PROXIMITY
        for retrieved_file in retrieval.files
        for signal in retrieved_file.signals
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
    return [
        EvidenceItem(
            description="; ".join(signal.detail for signal in f.signals),
            file_path=f.module.path,
        )
        for f in retrieval.files
    ]


def _resolved_file_recommendations(
    items: list[dict[str, Any]],
    action: FileAction,
    validator: GroundingValidator,
) -> tuple[list[FileRecommendation], ValidationVerdict]:
    ids = [item["id"] for item in items]
    verdict = validator.resolve_candidate_ids(ids).verdict

    recommendations: list[FileRecommendation] = []
    for item in items:
        ref = validator.resolve_candidate_id(item["id"])
        if ref is None:
            continue
        recommendations.append(
            FileRecommendation(
                path=ref.identifier,
                action=action,
                reasoning=item.get("reasoning", ""),
                confidence=confidence_from_strengths([EvidenceStrength.INFERRED]),
                evidence=[
                    EvidenceItem(
                        description="matched retrieved candidate", file_path=ref.identifier
                    )
                ],
            )
        )
    return recommendations, verdict


def _proposed_file_recommendations(
    items: list[dict[str, Any]],
    validator: GroundingValidator,
    *,
    has_convention_evidence: bool,
) -> tuple[list[FileRecommendation], ValidationVerdict]:
    strength = EvidenceStrength.INFERRED if has_convention_evidence else EvidenceStrength.UNKNOWN
    recommendations: list[FileRecommendation] = []
    verdicts: list[ValidationVerdict] = []

    for item in items:
        path = item["path"]
        verdict = validator.validate_proposed_path(path)
        verdicts.append(verdict)
        if verdict is ValidationVerdict.INVALID:
            continue
        recommendations.append(
            FileRecommendation(
                path=path,
                action=FileAction.CREATE,
                reasoning=item.get("reasoning", ""),
                confidence=confidence_from_strengths([strength]),
                evidence=[
                    EvidenceItem(
                        description="proposed new file, no existing file at this path",
                    )
                ],
            )
        )

    return recommendations, combine_verdicts(verdicts)


def _resolved_identifiers(
    candidate_ids: list[str], validator: GroundingValidator
) -> tuple[list[str], ValidationVerdict]:
    result = validator.resolve_candidate_ids(candidate_ids)
    return [ref.identifier for ref in result.resolved_refs], result.verdict


def plan_change(request: str, snapshot: RepositorySnapshot, provider: AIProvider) -> ChangePlan:
    retrieval = retrieve_relevant(request, snapshot)
    _logger.info(
        "retrieval found files=%d symbols=%d chains=%d",
        len(retrieval.files),
        len(retrieval.symbols),
        len(retrieval.relationship_chains),
    )

    if retrieval.is_empty:
        return ChangePlan(
            request=request,
            goal=request,
            reasoning=_NO_EVIDENCE_REASONING,
            confidence=Confidence.LOW,
        )

    candidates = build_candidate_set(retrieval)
    validator = GroundingValidator(candidates, snapshot)
    security_config = load_security_config(Path(snapshot.repository_root))
    context = truncate_context(
        _context_block(retrieval, candidates), security_config.max_context_size
    )
    prompt = f"Change request: {request}\n\nCandidates and context:\n{context}"
    payload = complete_json(provider, system=_SYSTEM_PROMPT, prompt=prompt)
    has_convention_evidence = _has_directory_convention_evidence(retrieval)

    try:
        files_to_modify, modify_verdict = _resolved_file_recommendations(
            list(payload.get("files_to_modify", [])), FileAction.MODIFY, validator
        )
        files_to_create, create_verdict = _proposed_file_recommendations(
            list(payload.get("files_to_create", [])),
            validator,
            has_convention_evidence=has_convention_evidence,
        )
        relevant_symbols, symbols_verdict = _resolved_identifiers(
            list(payload.get("relevant_symbols", [])), validator
        )
        tests_to_update, tests_verdict = _resolved_identifiers(
            list(payload.get("tests_to_update", [])), validator
        )

        strengths = [
            EvidenceStrength.INFERRED
            for _ in (*files_to_modify, *relevant_symbols, *tests_to_update)
        ]
        strengths.extend(
            EvidenceStrength.INFERRED if has_convention_evidence else EvidenceStrength.UNKNOWN
            for _ in files_to_create
        )

        # Only fields the model actually attempted to populate contribute to
        # the overall verdict -- an empty field is trivially valid and must
        # not dilute a genuine rejection found in another field.
        attempted_verdicts = [
            verdict
            for attempted, verdict in (
                (payload.get("files_to_modify"), modify_verdict),
                (payload.get("files_to_create"), create_verdict),
                (payload.get("relevant_symbols"), symbols_verdict),
                (payload.get("tests_to_update"), tests_verdict),
            )
            if attempted
        ]
        overall_verdict = combine_verdicts(attempted_verdicts)
        if overall_verdict is not ValidationVerdict.VALID:
            _logger.warning(
                "grounding verdict=%s across %d attempted fields",
                overall_verdict.value,
                len(attempted_verdicts),
            )

        return ChangePlan(
            request=request,
            goal=payload["goal"],
            subsystem=payload.get("subsystem"),
            implementation_pattern=payload.get("implementation_pattern"),
            files_to_modify=files_to_modify,
            files_to_create=files_to_create,
            relevant_symbols=relevant_symbols,
            tests_to_update=tests_to_update,
            potential_impact=list(payload.get("potential_impact", [])),
            reasoning=payload["reasoning"],
            confidence=confidence_from_strengths(strengths),
            evidence=_retrieval_evidence(retrieval),
            grounding=overall_verdict,
        )
    except (KeyError, ValueError) as exc:
        raise AISynthesisError(
            f"Provider response did not match the change plan schema: {payload}"
        ) from exc
