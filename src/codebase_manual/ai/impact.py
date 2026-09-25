"""Impact analysis: deterministic dependency facts, with an AI explanation layered on top.

`compute_impact_facts` computes direct/indirect dependents, affected
tests, and affected API endpoints purely from the relationship graph --
the model never invents an edge. It is only ever asked to explain the
likely consequences of a fixed, already-computed set of facts, so it
cannot fabricate a dependency. Confidence is derived from the strength of
those facts (never asked of the model), capped at MEDIUM when the
dependent traversal was truncated at its depth limit -- see
`query.graph.RelationshipGraph.transitive_dependents_traversal`.

The free-prose `explanation` is grounded the same way `ai.qa`/
`ai.change_planner` ground their prose: the target/dependents/tests are
given to the model as opaque candidate IDs (via `build_candidate_set_from_refs`,
since these come from a fixed fact list, not a retrieval result), the model
must cite which IDs its explanation actually relies on, and
`GroundingValidator` resolves those citations before they become
`ImpactReport.evidence` -- an invented ID is rejected, not trusted. This
closes what was previously an unmeasurable gap in the trust boundary: prose
with no citation mechanism at all can't be checked for unsupported claims.
"""

from __future__ import annotations

from dataclasses import dataclass

from codebase_manual.ai.confidence import confidence_from_strengths
from codebase_manual.ai.grounding import GroundingValidator
from codebase_manual.ai.models import Confidence, EvidenceItem, ImpactReport
from codebase_manual.ai.provider import AIProvider, AISynthesisError, complete_json
from codebase_manual.domain.evidence import EvidenceStrength
from codebase_manual.domain.models import EntityRef
from codebase_manual.logging_config import get_logger
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.api_endpoints import detect_api_endpoints
from codebase_manual.query.candidates import CandidateSet, build_candidate_set_from_refs
from codebase_manual.query.graph import RelationshipGraph

_logger = get_logger("ai.impact")

_SYSTEM_PROMPT = (
    "You explain the likely consequences of changing a piece of code, given "
    "a fixed, already-computed list of dependents, tests, and API "
    "endpoints, each dependent/test labeled with an opaque ID (FILE_xxx, "
    "SYMBOL_xxx, TEST_xxx). Do not invent any dependency beyond what is "
    "listed. List the IDs your explanation actually relies on in "
    "`cited_ids`; do not invent an ID. Respond with a single JSON object: "
    '{"explanation": "...", "cited_ids": ["SYMBOL_xxx", "TEST_xxx"]}.'
)


@dataclass
class ImpactFacts:
    target: EntityRef
    direct_dependents: list[EntityRef]
    indirect_dependents: list[EntityRef]
    affected_tests: list[EntityRef]
    affected_apis: list[str]
    truncated: bool


def _by_identifier(refs: set[EntityRef]) -> list[EntityRef]:
    return sorted(refs, key=lambda ref: ref.identifier)


def compute_impact_facts(target: EntityRef, snapshot: RepositorySnapshot) -> ImpactFacts:
    graph = RelationshipGraph(snapshot.relationships)

    direct = {edge.source for edge in graph.dependents_of(target)}
    traversal = graph.transitive_dependents_traversal(target)
    transitive = set(traversal.entities)
    indirect = transitive - direct

    affected_tests: set[EntityRef] = set()
    for entity in {target, *transitive}:
        affected_tests.update(edge.source for edge in graph.tests_for(entity))

    affected_identifiers = {ref.identifier for ref in {target, *transitive}}
    affected_apis = [
        f"{endpoint.http_method} {endpoint.path or '(dynamic path)'}"
        for endpoint in detect_api_endpoints(snapshot.modules)
        if endpoint.function_qualified_name in affected_identifiers
    ]

    return ImpactFacts(
        target=target,
        direct_dependents=_by_identifier(direct),
        indirect_dependents=_by_identifier(indirect),
        affected_tests=_by_identifier(affected_tests),
        affected_apis=sorted(affected_apis),
        truncated=traversal.truncated,
    )


def confidence_for_impact_facts(facts: ImpactFacts) -> Confidence:
    strengths = [
        EvidenceStrength.RESOLVED
        for _ in (*facts.direct_dependents, *facts.indirect_dependents, *facts.affected_tests)
    ]
    if not strengths:
        # A definitive "nothing depends on this" is itself a direct fact,
        # not an absence of evidence -- see the RelationshipKind semantics.
        strengths = [EvidenceStrength.DIRECT]
    confidence = confidence_from_strengths(strengths)
    if facts.truncated and confidence is Confidence.HIGH:
        # The traversal hit max_depth with more left to explore -- the
        # picture is incomplete, so it must not present as fully confident.
        return Confidence.MEDIUM
    return confidence


def _bullets(items: list[str]) -> list[str]:
    return [f"  - {item}" for item in items] if items else ["  (none)"]


def build_impact_candidates(facts: ImpactFacts) -> CandidateSet:
    """The bounded set of entities the model may cite in its explanation.

    Built directly from already-computed facts (not a retrieval result) --
    see `query.candidates.build_candidate_set_from_refs`.
    """
    return build_candidate_set_from_refs(
        [facts.target, *facts.direct_dependents, *facts.indirect_dependents, *facts.affected_tests]
    )


def _facts_block(facts: ImpactFacts, candidates: CandidateSet) -> str:
    target_candidate = next(
        (
            c.id
            for c in (*candidates.files, *candidates.symbols, *candidates.tests)
            if c.ref == facts.target
        ),
        None,
    )
    lines = [
        candidates.prompt_block(),
        "",
        f"Target: {facts.target.kind.value} `{facts.target.identifier}` "
        f"(candidate ID: {target_candidate or 'n/a'})",
        "Direct dependents:",
    ]
    lines.extend(_bullets([ref.identifier for ref in facts.direct_dependents]))
    lines.append("Indirect dependents:")
    lines.extend(_bullets([ref.identifier for ref in facts.indirect_dependents]))
    lines.append("Affected tests:")
    lines.extend(_bullets([ref.identifier for ref in facts.affected_tests]))
    lines.append("Affected API endpoints:")
    lines.extend(_bullets(facts.affected_apis))
    if facts.truncated:
        lines.append(
            "Note: dependent traversal was truncated at its depth limit -- "
            "there may be further indirect dependents beyond this list."
        )
    return "\n".join(lines)


def analyze_impact(
    target: EntityRef, snapshot: RepositorySnapshot, provider: AIProvider
) -> ImpactReport:
    facts = compute_impact_facts(target, snapshot)
    candidates = build_impact_candidates(facts)
    validator = GroundingValidator(candidates, snapshot)
    payload = complete_json(provider, system=_SYSTEM_PROMPT, prompt=_facts_block(facts, candidates))

    try:
        explanation = payload["explanation"]
    except KeyError as exc:
        raise AISynthesisError(
            f"Provider response did not match the impact schema: {payload}"
        ) from exc

    cited_ids = list(payload.get("cited_ids", []))
    result = validator.resolve_candidate_ids(cited_ids)
    if result.rejected_ids:
        _logger.warning(
            "grounding rejected %d/%d cited ids verdict=%s",
            len(result.rejected_ids),
            len(cited_ids),
            result.verdict.value,
        )
    evidence = [
        EvidenceItem(description=f"cited candidate `{ref.identifier}`", file_path=ref.identifier)
        for ref in result.resolved_refs
    ]

    return ImpactReport(
        target_identifier=target.identifier,
        direct_dependents=[ref.identifier for ref in facts.direct_dependents],
        indirect_dependents=[ref.identifier for ref in facts.indirect_dependents],
        affected_tests=[ref.identifier for ref in facts.affected_tests],
        affected_apis=facts.affected_apis,
        explanation=explanation,
        confidence=confidence_for_impact_facts(facts),
        truncated=facts.truncated,
        evidence=evidence,
        grounding=result.verdict,
    )
