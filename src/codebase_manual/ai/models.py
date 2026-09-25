"""Semantic interpretation models.

Everything here is AI-generated interpretation, never a deterministic fact
-- keep it out of `domain.models`. Every result carries `confidence` and
`evidence` tying it back to the facts (file paths, symbols, relationship
evidence strings) it was grounded in.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from codebase_manual.ai.grounding import ValidationVerdict


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FileAction(StrEnum):
    MODIFY = "modify"
    CREATE = "create"


class EvidenceItem(BaseModel):
    description: str
    file_path: str | None = None
    line: int | None = None


class FileSummary(BaseModel):
    file_path: str
    purpose: str
    responsibilities: list[str] = Field(default_factory=list)
    important_symbols: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    confidence: Confidence
    evidence: list[EvidenceItem] = Field(default_factory=list)


class FunctionSummary(BaseModel):
    qualified_name: str
    purpose: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    confidence: Confidence
    evidence: list[EvidenceItem] = Field(default_factory=list)


class Answer(BaseModel):
    question: str
    text: str
    confidence: Confidence
    evidence: list[EvidenceItem] = Field(default_factory=list)
    grounding: ValidationVerdict = ValidationVerdict.VALID


class FileRecommendation(BaseModel):
    path: str
    action: FileAction
    reasoning: str
    confidence: Confidence
    evidence: list[EvidenceItem] = Field(default_factory=list)


class ChangePlan(BaseModel):
    request: str
    goal: str
    subsystem: str | None = None
    implementation_pattern: str | None = None
    files_to_modify: list[FileRecommendation] = Field(default_factory=list)
    files_to_create: list[FileRecommendation] = Field(default_factory=list)
    relevant_symbols: list[str] = Field(default_factory=list)
    tests_to_update: list[str] = Field(default_factory=list)
    potential_impact: list[str] = Field(default_factory=list)
    reasoning: str
    confidence: Confidence
    evidence: list[EvidenceItem] = Field(default_factory=list)
    grounding: ValidationVerdict = ValidationVerdict.VALID


class ImpactReport(BaseModel):
    target_identifier: str
    direct_dependents: list[str] = Field(default_factory=list)
    indirect_dependents: list[str] = Field(default_factory=list)
    affected_tests: list[str] = Field(default_factory=list)
    affected_apis: list[str] = Field(default_factory=list)
    explanation: str
    confidence: Confidence
    truncated: bool = False
    evidence: list[EvidenceItem] = Field(default_factory=list)
    grounding: ValidationVerdict = ValidationVerdict.VALID
