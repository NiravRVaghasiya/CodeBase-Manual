"""Impact analysis: deterministic dependency facts, with an AI explanation layered on top.

`compute_impact_facts` computes direct/indirect dependents, affected
tests, and affected API endpoints purely from the relationship graph --
the model never invents an edge. It is only ever asked to explain the
likely consequences of a fixed, already-computed set of facts, so it
cannot fabricate a dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from codebase_manual.ai.models import Confidence, ImpactReport
from codebase_manual.ai.provider import AIProvider, AISynthesisError, complete_json
from codebase_manual.domain.models import EntityRef
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.api_endpoints import detect_api_endpoints
from codebase_manual.query.graph import RelationshipGraph

_SYSTEM_PROMPT = (
    "You explain the likely consequences of changing a piece of code, given "
    "a fixed, already-computed list of dependents, tests, and API "
    "endpoints. Do not invent any dependency beyond what is listed. "
    "Respond with a single JSON object: "
    '{"explanation": "...", "confidence": "high|medium|low"}.'
)


@dataclass
class ImpactFacts:
    target: EntityRef
    direct_dependents: list[EntityRef]
    indirect_dependents: list[EntityRef]
    affected_tests: list[EntityRef]
    affected_apis: list[str]


def _by_identifier(refs: set[EntityRef]) -> list[EntityRef]:
    return sorted(refs, key=lambda ref: ref.identifier)


def compute_impact_facts(target: EntityRef, snapshot: RepositorySnapshot) -> ImpactFacts:
    graph = RelationshipGraph(snapshot.relationships)

    direct = {edge.source for edge in graph.dependents_of(target)}
    transitive = set(graph.transitive_dependents(target))
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
    )


def _bullets(items: list[str]) -> list[str]:
    return [f"  - {item}" for item in items] if items else ["  (none)"]


def _facts_block(facts: ImpactFacts) -> str:
    lines = [f"Target: {facts.target.kind.value} `{facts.target.identifier}`", "Direct dependents:"]
    lines.extend(_bullets([ref.identifier for ref in facts.direct_dependents]))
    lines.append("Indirect dependents:")
    lines.extend(_bullets([ref.identifier for ref in facts.indirect_dependents]))
    lines.append("Affected tests:")
    lines.extend(_bullets([ref.identifier for ref in facts.affected_tests]))
    lines.append("Affected API endpoints:")
    lines.extend(_bullets(facts.affected_apis))
    return "\n".join(lines)


def analyze_impact(
    target: EntityRef, snapshot: RepositorySnapshot, provider: AIProvider
) -> ImpactReport:
    facts = compute_impact_facts(target, snapshot)
    payload = complete_json(provider, system=_SYSTEM_PROMPT, prompt=_facts_block(facts))

    try:
        explanation = payload["explanation"]
        confidence = Confidence(payload["confidence"])
    except (KeyError, ValueError) as exc:
        raise AISynthesisError(
            f"Provider response did not match the impact schema: {payload}"
        ) from exc

    return ImpactReport(
        target_identifier=target.identifier,
        direct_dependents=[ref.identifier for ref in facts.direct_dependents],
        indirect_dependents=[ref.identifier for ref in facts.indirect_dependents],
        affected_tests=[ref.identifier for ref in facts.affected_tests],
        affected_apis=facts.affected_apis,
        explanation=explanation,
        confidence=confidence,
    )
