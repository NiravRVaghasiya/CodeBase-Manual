"""Repository retrieval and traversal over persisted facts."""

from codebase_manual.query.api_endpoints import ApiEndpoint, detect_api_endpoints
from codebase_manual.query.candidates import Candidate, CandidateSet, build_candidate_set
from codebase_manual.query.drift import IndexDriftReport, detect_index_drift
from codebase_manual.query.entity_resolution import find_entity_ref
from codebase_manual.query.graph import RelationshipGraph, Traversal
from codebase_manual.query.retrieval import (
    MatchReason,
    MatchSignal,
    RelationshipChain,
    RelationshipStep,
    RetrievalResult,
    RetrievedFile,
    RetrievedSymbol,
    retrieve_relevant,
)

__all__ = [
    "ApiEndpoint",
    "Candidate",
    "CandidateSet",
    "IndexDriftReport",
    "MatchReason",
    "MatchSignal",
    "RelationshipChain",
    "RelationshipGraph",
    "RelationshipStep",
    "RetrievalResult",
    "RetrievedFile",
    "RetrievedSymbol",
    "Traversal",
    "build_candidate_set",
    "detect_api_endpoints",
    "detect_index_drift",
    "find_entity_ref",
    "retrieve_relevant",
]
