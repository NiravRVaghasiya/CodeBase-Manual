"""Grounded AI summaries of files and functions.

The model only ever sees one module's/function's own deterministic facts,
never the whole repository. Purpose/responsibilities come from the model;
the evidence that grounds them is attached deterministically by this
module, not requested from the model.
"""

from __future__ import annotations

from pydantic import ValidationError

from codebase_manual.ai.models import Confidence, EvidenceItem, FileSummary, FunctionSummary
from codebase_manual.ai.provider import AIProvider, AISynthesisError, complete_json
from codebase_manual.domain.models import FunctionSymbol, PythonModule

_SYSTEM_PROMPT = (
    "You are a precise code-analysis assistant. You are given deterministic "
    "facts about Python source code -- never assume anything beyond them. "
    "Respond with a single JSON object only, no prose, no markdown fences, "
    "matching exactly the fields requested. If you are not confident, say "
    "so via the `confidence` field ('high', 'medium', or 'low') rather than "
    "guessing."
)

_FILE_SCHEMA = (
    '{"purpose": "one sentence", "responsibilities": ["..."], '
    '"important_symbols": ["..."], "side_effects": ["..."], '
    '"confidence": "high|medium|low"}'
)

_FUNCTION_SCHEMA = (
    '{"purpose": "one sentence", "inputs": ["..."], "outputs": ["..."], '
    '"side_effects": ["..."], "confidence": "high|medium|low"}'
)


def _module_facts(module: PythonModule) -> str:
    lines = [f"File: {module.path}", f"Module: {module.module_name or '(unresolved)'}"]
    if module.docstring:
        lines.append(f"Docstring: {module.docstring}")
    if module.imports:
        lines.append("Imports:")
        for imp in module.imports:
            target = f"{imp.module}.{imp.name}" if imp.name else imp.module
            lines.append(f"  - {target}")
    if module.classes:
        lines.append("Classes:")
        for klass in module.classes:
            suffix = f": {klass.docstring}" if klass.docstring else ""
            lines.append(f"  - {klass.name}(bases={klass.bases}){suffix}")
    if module.functions:
        lines.append("Functions:")
        for function in module.functions:
            params = ", ".join(p.name for p in function.parameters)
            suffix = f": {function.docstring}" if function.docstring else ""
            lines.append(f"  - {function.name}({params}){suffix}")
    return "\n".join(lines)


def _function_facts(function: FunctionSymbol, module_path: str) -> str:
    params = ", ".join(
        f"{p.name}: {p.annotation}" if p.annotation else p.name for p in function.parameters
    )
    lines = [
        f"File: {module_path}",
        f"Function: {function.qualified_name}({params})",
        f"Returns: {function.return_annotation or '(unannotated)'}",
        f"Async: {function.is_async}",
    ]
    if function.docstring:
        lines.append(f"Docstring: {function.docstring}")
    if function.calls:
        lines.append(f"Calls: {', '.join(function.calls)}")
    return "\n".join(lines)


def summarize_file(module: PythonModule, provider: AIProvider) -> FileSummary:
    prompt = f"{_module_facts(module)}\n\nRespond with JSON matching: {_FILE_SCHEMA}"
    payload = complete_json(provider, system=_SYSTEM_PROMPT, prompt=prompt)

    try:
        return FileSummary(
            file_path=module.path,
            purpose=payload["purpose"],
            responsibilities=list(payload.get("responsibilities", [])),
            important_symbols=list(payload.get("important_symbols", [])),
            side_effects=list(payload.get("side_effects", [])),
            confidence=Confidence(payload["confidence"]),
            evidence=[
                EvidenceItem(
                    description=f"facts extracted from {module.path}", file_path=module.path
                )
            ],
        )
    except (KeyError, ValueError, ValidationError) as exc:
        raise AISynthesisError(
            f"Provider response did not match the file summary schema: {payload}"
        ) from exc


def summarize_function(
    function: FunctionSymbol, module_path: str, provider: AIProvider
) -> FunctionSummary:
    facts = _function_facts(function, module_path)
    prompt = f"{facts}\n\nRespond with JSON matching: {_FUNCTION_SCHEMA}"
    payload = complete_json(provider, system=_SYSTEM_PROMPT, prompt=prompt)

    try:
        return FunctionSummary(
            qualified_name=function.qualified_name,
            purpose=payload["purpose"],
            inputs=list(payload.get("inputs", [])),
            outputs=list(payload.get("outputs", [])),
            side_effects=list(payload.get("side_effects", [])),
            confidence=Confidence(payload["confidence"]),
            evidence=[
                EvidenceItem(
                    description=f"facts extracted from {function.qualified_name}",
                    file_path=module_path,
                    line=function.location.line_start,
                )
            ],
        )
    except (KeyError, ValueError, ValidationError) as exc:
        raise AISynthesisError(
            f"Provider response did not match the function summary schema: {payload}"
        ) from exc
