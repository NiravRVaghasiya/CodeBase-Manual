"""Semantic analysis and AI orchestration, grounded in deterministic facts."""

from codebase_manual.ai.anthropic_provider import AnthropicProvider
from codebase_manual.ai.change_planner import plan_change
from codebase_manual.ai.impact import ImpactFacts, analyze_impact, compute_impact_facts
from codebase_manual.ai.manual import generate_manual
from codebase_manual.ai.models import (
    Answer,
    ChangePlan,
    Confidence,
    EvidenceItem,
    FileAction,
    FileRecommendation,
    FileSummary,
    FunctionSummary,
    ImpactReport,
)
from codebase_manual.ai.provider import AIProvider, AIProviderNotConfiguredError, AISynthesisError
from codebase_manual.ai.qa import answer_question
from codebase_manual.ai.summarizer import summarize_file, summarize_function

__all__ = [
    "AIProvider",
    "AIProviderNotConfiguredError",
    "AISynthesisError",
    "AnthropicProvider",
    "Answer",
    "ChangePlan",
    "Confidence",
    "EvidenceItem",
    "FileAction",
    "FileRecommendation",
    "FileSummary",
    "FunctionSummary",
    "ImpactFacts",
    "ImpactReport",
    "analyze_impact",
    "answer_question",
    "compute_impact_facts",
    "generate_manual",
    "plan_change",
    "summarize_file",
    "summarize_function",
]
