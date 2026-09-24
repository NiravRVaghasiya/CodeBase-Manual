"""Change planning: the system's core differentiating capability.

Deterministic retrieval finds candidate files/symbols and sibling files in
the same directory (a common "find the existing pattern" signal); the
model only synthesizes reasoning and proposes file actions grounded in
that evidence. When no convention is evident, the model is instructed to
say so in its reasoning rather than invent a plausible-sounding path.
"""

from __future__ import annotations

from typing import Any

from codebase_manual.ai.models import (
    ChangePlan,
    Confidence,
    EvidenceItem,
    FileAction,
    FileRecommendation,
)
from codebase_manual.ai.provider import AIProvider, AISynthesisError, complete_json
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.retrieval import RetrievalResult, retrieve_relevant

_SYSTEM_PROMPT = (
    "You are a precise software change-planning assistant. You are given "
    "deterministic facts about a repository: retrieved files/symbols "
    "relevant to a change request, and sibling files in the same directory "
    "as the closest match (if any were found). Propose which files to "
    "modify, which files to create, relevant symbols, and tests to update "
    "-- but ONLY based on the given facts. If no clear convention exists "
    "for a new file's location, say so in `reasoning` and omit it from "
    "`files_to_create` rather than inventing a plausible-sounding path. "
    "Respond with a single JSON object: "
    '{"goal": "...", "subsystem": "..." or null, '
    '"implementation_pattern": "..." or null, '
    '"files_to_modify": [{"path": "...", "reasoning": "...", '
    '"confidence": "high|medium|low"}], '
    '"files_to_create": [{"path": "...", "reasoning": "...", '
    '"confidence": "high|medium|low"}], '
    '"relevant_symbols": ["..."], "tests_to_update": ["..."], '
    '"potential_impact": ["..."], "reasoning": "...", '
    '"confidence": "high|medium|low"}'
)

_NO_EVIDENCE_REASONING = (
    "No files or symbols in the indexed repository matched this request's terms, "
    "so no subsystem, pattern, or file recommendations can be grounded in evidence."
)


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _sibling_files(retrieval: RetrievalResult, snapshot: RepositorySnapshot) -> list[str]:
    if not retrieval.files:
        return []
    top_path = retrieval.files[0].module.path
    top_dir = _dirname(top_path)
    return sorted(
        {
            module.path
            for module in snapshot.modules
            if _dirname(module.path) == top_dir and module.path != top_path
        }
    )


def _context_block(retrieval: RetrievalResult, siblings: list[str]) -> str:
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
    if siblings:
        lines.append("Sibling files in the same directory as the closest match:")
        lines.extend(f"  - {path}" for path in siblings)
    for symbol in retrieval.symbols:
        lines.append(f"- {symbol.kind} `{symbol.identifier}` in {symbol.module_path}")
    return "\n".join(lines)


def _retrieval_evidence(retrieval: RetrievalResult) -> list[EvidenceItem]:
    return [
        EvidenceItem(
            description=f"module matched terms {sorted(f.matched_terms)}",
            file_path=f.module.path,
        )
        for f in retrieval.files
    ]


def _recommendation(item: dict[str, Any], action: FileAction) -> FileRecommendation:
    return FileRecommendation(
        path=item["path"],
        action=action,
        reasoning=item.get("reasoning", ""),
        confidence=Confidence(item.get("confidence", "low")),
        evidence=[
            EvidenceItem(description="proposed from retrieved context", file_path=item["path"])
        ],
    )


def plan_change(request: str, snapshot: RepositorySnapshot, provider: AIProvider) -> ChangePlan:
    retrieval = retrieve_relevant(request, snapshot)

    if retrieval.is_empty:
        return ChangePlan(
            request=request,
            goal=request,
            reasoning=_NO_EVIDENCE_REASONING,
            confidence=Confidence.LOW,
        )

    siblings = _sibling_files(retrieval, snapshot)
    context = _context_block(retrieval, siblings)
    prompt = f"Change request: {request}\n\nRetrieved context:\n{context}"
    payload = complete_json(provider, system=_SYSTEM_PROMPT, prompt=prompt)

    try:
        return ChangePlan(
            request=request,
            goal=payload["goal"],
            subsystem=payload.get("subsystem"),
            implementation_pattern=payload.get("implementation_pattern"),
            files_to_modify=[
                _recommendation(item, FileAction.MODIFY)
                for item in payload.get("files_to_modify", [])
            ],
            files_to_create=[
                _recommendation(item, FileAction.CREATE)
                for item in payload.get("files_to_create", [])
            ],
            relevant_symbols=list(payload.get("relevant_symbols", [])),
            tests_to_update=list(payload.get("tests_to_update", [])),
            potential_impact=list(payload.get("potential_impact", [])),
            reasoning=payload["reasoning"],
            confidence=Confidence(payload["confidence"]),
            evidence=_retrieval_evidence(retrieval),
        )
    except (KeyError, ValueError) as exc:
        raise AISynthesisError(
            f"Provider response did not match the change plan schema: {payload}"
        ) from exc
