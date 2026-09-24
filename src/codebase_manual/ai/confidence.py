"""Deterministic confidence: never trusted from the model, always computed here.

The LLM is never asked for a `confidence` field -- every prompt in `ai/`
requests only prose/reasoning. Confidence is instead derived from the
strength of the deterministic evidence that grounded the result, per the
"LLM confidence is not authoritative" rule. This keeps the mapping
documented, testable, and calibrated in one place instead of scattered
per-call trust in provider self-reports.
"""

from __future__ import annotations

from codebase_manual.ai.models import Confidence
from codebase_manual.domain.evidence import EvidenceStrength

# Higher rank = stronger evidence. RESOLVED and DIRECT are treated as equally
# strong: both are facts read/derived with no ambiguity, they just differ in
# whether a resolution step was involved.
_RANK: dict[EvidenceStrength, int] = {
    EvidenceStrength.DIRECT: 2,
    EvidenceStrength.RESOLVED: 2,
    EvidenceStrength.INFERRED: 1,
    EvidenceStrength.UNKNOWN: 0,
}


def confidence_from_strengths(strengths: list[EvidenceStrength]) -> Confidence:
    """Map the evidence strengths behind a result to a `Confidence` level.

    - No evidence at all, or only UNKNOWN evidence -> LOW.
    - Every piece of evidence is DIRECT/RESOLVED -> HIGH.
    - Anything else (a mix, or purely INFERRED evidence such as
      keyword-overlap retrieval) -> MEDIUM.
    """
    if not strengths:
        return Confidence.LOW
    ranks = [_RANK[s] for s in strengths]
    if all(rank == 2 for rank in ranks):
        return Confidence.HIGH
    if all(rank == 0 for rank in ranks):
        return Confidence.LOW
    return Confidence.MEDIUM


def confidence_for_summary(*, has_docstring: bool, has_structural_facts: bool) -> Confidence:
    """Confidence for a file/function summary, from the facts it was paraphrased from.

    An AI summary restates facts already read from source (see
    `ai.summarizer`); it never resolves or infers a relationship. So its
    confidence reflects how much explicit, author-stated intent (a
    docstring) versus bare structure the model had to paraphrase from:

    - A docstring is present -> HIGH: the model paraphrased stated intent.
    - No docstring, but there is other structure (functions, classes,
      parameters, calls) -> MEDIUM: the model inferred purpose from shape
      alone.
    - Neither -> LOW: there was almost nothing to summarize.
    """
    if has_docstring:
        return Confidence.HIGH
    if has_structural_facts:
        return Confidence.MEDIUM
    return Confidence.LOW
