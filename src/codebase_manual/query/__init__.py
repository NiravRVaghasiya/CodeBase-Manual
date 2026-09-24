"""Repository retrieval and traversal over persisted facts."""

from codebase_manual.query.api_endpoints import ApiEndpoint, detect_api_endpoints
from codebase_manual.query.drift import DriftReport, detect_drift
from codebase_manual.query.entity_resolution import find_entity_ref
from codebase_manual.query.graph import RelationshipGraph
from codebase_manual.query.retrieval import RetrievalResult, retrieve_relevant

__all__ = [
    "ApiEndpoint",
    "DriftReport",
    "RelationshipGraph",
    "RetrievalResult",
    "detect_api_endpoints",
    "detect_drift",
    "find_entity_ref",
    "retrieve_relevant",
]
