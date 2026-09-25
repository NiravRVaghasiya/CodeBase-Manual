"""Best-effort, fail-closed type inference for attribute/local-variable call resolution.

Python's dynamic typing means most instance-attribute and local-variable
types cannot be recovered without full type inference, which this project
deliberately does not attempt -- that would make the analyzer a compiler,
not a fact extractor (see `docs/analyzers.md`). This module resolves only a
handful of concrete, low-risk shapes:

- a constructor-injected parameter assigned straight to `self.<attr>`
  (`self.repository = repository` where `repository: UserRepository`)
- a dataclass/pydantic-style annotated class attribute (`repository:
  UserRepository` at class scope, no `__init__` needed)
- a direct construction (`self.repository = UserRepository()`, or
  `repo = UserRepository()` as a local variable)
- a basic factory call whose target function has a resolvable return
  annotation (`repo = get_repository()`, `repo = repositories.get_repository()`)
- simple name propagation (`repo = repository` for an already-typed
  parameter or local; `self.repo = other_repo`; `repo = self.repository`)

Anything that doesn't match one of these shapes -- an arbitrary expression,
a reassignment to a conflicting type, an unannotated dynamic factory --
resolves to `None` (unknown), never a guess. `domain.relationships` treats
`None` exactly like "no type information available": the call is dropped,
not fabricated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codebase_manual.domain.models import (
    AssignedValueKind,
    Assignment,
    ClassSymbol,
    EntityKind,
    EntityRef,
    FunctionSymbol,
    PythonModule,
)

if TYPE_CHECKING:
    from codebase_manual.domain.relationships import _RepositoryIndex


def iter_function_callers(module: PythonModule) -> list[tuple[FunctionSymbol, ClassSymbol | None]]:
    """Every function/method in `module`, paired with its owning class (if any)."""
    callers: list[tuple[FunctionSymbol, ClassSymbol | None]] = [
        (function, None) for function in module.functions
    ]
    for klass in module.classes:
        callers.extend((method, klass) for method in klass.methods)
    return callers


def resolve_type_name(
    annotation: str, local_map: dict[str, str], index: _RepositoryIndex
) -> str | None:
    """Resolve a type annotation string to a known class's qualified name, or None.

    Handles a bare name, `Optional[X]`, and `X | None` (or `None | X`) -- a
    union of more than one non-`None` alternative is genuinely ambiguous and
    resolves to `None` rather than guessing which branch applies.
    """
    text = annotation.strip()
    if text.startswith("Optional[") and text.endswith("]"):
        return resolve_type_name(text[len("Optional[") : -1], local_map, index)
    if "|" in text:
        alternatives = [part.strip() for part in text.split("|")]
        non_none = [part for part in alternatives if part != "None"]
        if len(non_none) != 1:
            return None
        return resolve_type_name(non_none[0], local_map, index)
    if not text.isidentifier():
        return None
    resolved = local_map.get(text)
    return resolved if resolved is not None and resolved in index.classes_by_qname else None


def resolve_method_on_class(
    class_qname: str,
    method_name: str,
    index: _RepositoryIndex,
    local_maps: dict[str, dict[str, str]],
    visited: set[str] | None = None,
) -> EntityRef | None:
    """Resolve `method_name` on `class_qname`, walking resolvable bases if not found directly."""
    visited = visited if visited is not None else set()
    if class_qname in visited:
        return None
    visited.add(class_qname)

    candidate = f"{class_qname}.{method_name}"
    if candidate in index.functions_by_qname:
        return EntityRef(kind=EntityKind.FUNCTION, identifier=candidate)

    klass = index.classes_by_qname.get(class_qname)
    if klass is None:
        return None
    owning_module = index.owning_module.get(class_qname)
    base_local_map = local_maps.get(owning_module, {}) if owning_module else {}
    for base in klass.bases:
        resolved_base = base_local_map.get(base)
        if resolved_base is None or resolved_base not in index.classes_by_qname:
            continue
        found = resolve_method_on_class(resolved_base, method_name, index, local_maps, visited)
        if found is not None:
            return found
    return None


def resolve_attribute_type(
    class_qname: str,
    attr_name: str,
    attribute_types: dict[str, dict[str, str]],
    index: _RepositoryIndex,
    local_maps: dict[str, dict[str, str]],
    visited: set[str] | None = None,
) -> str | None:
    """Resolve the type of `class_qname`'s `attr_name`, walking resolvable bases if needed."""
    visited = visited if visited is not None else set()
    if class_qname in visited:
        return None
    visited.add(class_qname)

    resolved = attribute_types.get(class_qname, {}).get(attr_name)
    if resolved is not None:
        return resolved

    klass = index.classes_by_qname.get(class_qname)
    if klass is None:
        return None
    owning_module = index.owning_module.get(class_qname)
    base_local_map = local_maps.get(owning_module, {}) if owning_module else {}
    for base in klass.bases:
        resolved_base = base_local_map.get(base)
        if resolved_base is None or resolved_base not in index.classes_by_qname:
            continue
        found = resolve_attribute_type(
            resolved_base, attr_name, attribute_types, index, local_maps, visited
        )
        if found is not None:
            return found
    return None


def _value_type_from_call(
    callee: str, local_map: dict[str, str], index: _RepositoryIndex
) -> str | None:
    """The type constructed/returned by a call expression's callee, if determinable.

    Handles a bare-name callee (`Foo()`, `get_foo()`) and a `module.func()`
    factory where `module` resolves to a known module -- anything else
    (`self.factory()`, `obj.create()`, a chained/dynamic callee) is left
    unresolved rather than guessed.
    """
    parts = callee.split(".")
    if len(parts) == 1:
        resolved = local_map.get(parts[0])
    elif len(parts) == 2:
        module_resolved = local_map.get(parts[0])
        resolved = (
            f"{module_resolved}.{parts[1]}" if module_resolved in index.modules_by_name else None
        )
    else:
        resolved = None

    if resolved is None:
        return None
    if resolved in index.classes_by_qname:
        return resolved
    function = index.functions_by_qname.get(resolved)
    if function is not None and function.return_annotation:
        return resolve_type_name(function.return_annotation, local_map, index)
    return None


def _resolve_attribute_assignment_type(
    assignment: Assignment,
    param_types: dict[str, str | None],
    local_map: dict[str, str],
    index: _RepositoryIndex,
) -> str | None:
    if assignment.value_kind is AssignedValueKind.CALL:
        return _value_type_from_call(assignment.value_expression, local_map, index)
    if assignment.value_kind is AssignedValueKind.NAME:
        return param_types.get(assignment.value_expression)
    # ATTRIBUTE (`self.x = self.y`) would need this class's own attribute
    # types while they're still being built -- left unresolved rather than
    # attempting a fragile same-pass fixed point.
    return None


def build_attribute_types(
    modules: list[PythonModule],
    index: _RepositoryIndex,
    local_maps: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Best-effort instance-attribute types per class, keyed by class qualified name."""
    attribute_types: dict[str, dict[str, str]] = {}

    for module in modules:
        if not module.module_name:
            continue
        local_map = local_maps.get(module.module_name, {})

        for klass in module.classes:
            types: dict[str, str] = {}

            for variable in klass.class_variables:
                if variable.annotation is None:
                    continue
                resolved = resolve_type_name(variable.annotation, local_map, index)
                if resolved is not None:
                    types.setdefault(variable.name, resolved)

            for method in klass.methods:
                param_types: dict[str, str | None] = {
                    parameter.name: resolve_type_name(parameter.annotation, local_map, index)
                    for parameter in method.parameters
                    if parameter.annotation
                }
                for assignment in method.assignments:
                    if not assignment.is_attribute or assignment.target in types:
                        continue
                    resolved = _resolve_attribute_assignment_type(
                        assignment, param_types, local_map, index
                    )
                    if resolved is not None:
                        types[assignment.target] = resolved

            if types:
                attribute_types[klass.qualified_name] = types

    return attribute_types


def _resolve_local_assignment_type(
    assignment: Assignment,
    known: dict[str, str | None],
    owning_class: ClassSymbol | None,
    attribute_types: dict[str, dict[str, str]],
    index: _RepositoryIndex,
    local_maps: dict[str, dict[str, str]],
    local_map: dict[str, str],
) -> str | None:
    if assignment.value_kind is AssignedValueKind.CALL:
        return _value_type_from_call(assignment.value_expression, local_map, index)
    if assignment.value_kind is AssignedValueKind.NAME:
        return known.get(assignment.value_expression)
    if assignment.value_kind is AssignedValueKind.ATTRIBUTE:
        parts = assignment.value_expression.split(".")
        if len(parts) == 2 and parts[0] in ("self", "cls") and owning_class is not None:
            return resolve_attribute_type(
                owning_class.qualified_name, parts[1], attribute_types, index, local_maps
            )
        return None
    return None


def build_local_var_types(
    modules: list[PythonModule],
    index: _RepositoryIndex,
    local_maps: dict[str, dict[str, str]],
    attribute_types: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Best-effort local-variable types per function, keyed by function qualified name.

    A variable reassigned to a different (or unresolvable) type anywhere in
    the function is treated as ambiguous and dropped entirely, rather than
    resolved to whichever assignment happened to run last in this pass.
    """
    local_var_types: dict[str, dict[str, str]] = {}

    for module in modules:
        if not module.module_name:
            continue
        local_map = local_maps.get(module.module_name, {})

        for function, owning_class in iter_function_callers(module):
            types: dict[str, str | None] = {
                parameter.name: resolve_type_name(parameter.annotation, local_map, index)
                for parameter in function.parameters
                if parameter.annotation
            }

            for assignment in function.assignments:
                if assignment.is_attribute:
                    continue
                resolved = _resolve_local_assignment_type(
                    assignment, types, owning_class, attribute_types, index, local_maps, local_map
                )
                target = assignment.target
                if resolved is None:
                    types[target] = None
                elif target not in types:
                    types[target] = resolved
                elif types[target] != resolved:
                    types[target] = None

            resolved_types = {name: t for name, t in types.items() if t is not None}
            if resolved_types:
                local_var_types[function.qualified_name] = resolved_types

    return local_var_types
