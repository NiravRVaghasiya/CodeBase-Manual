"""Derives relationship facts from analyzed Python modules.

Every relationship produced here is backed by concrete syntactic evidence
(an import statement, a base-class declaration, a resolvable call
expression). Ambiguous references -- calls through arbitrary local
variables, unresolved base classes, dotted bases -- are dropped rather than
guessed, per the "never fabricate a relationship" rule.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from codebase_manual.domain.models import (
    CallSite,
    ClassSymbol,
    EntityKind,
    EntityRef,
    FunctionSymbol,
    ImportedName,
    PythonModule,
    Relationship,
    RelationshipKind,
    SourceLocation,
    UnresolvedCall,
)
from codebase_manual.domain.type_inference import (
    build_attribute_types,
    build_local_var_types,
    iter_function_callers,
    resolve_attribute_type,
    resolve_method_on_class,
)
from codebase_manual.logging_config import get_logger

_logger = get_logger("domain.relationships")


def _module_ref(name: str) -> EntityRef:
    return EntityRef(kind=EntityKind.MODULE, identifier=name)


def _file_ref(path: str) -> EntityRef:
    return EntityRef(kind=EntityKind.FILE, identifier=path)


def _class_ref(qualified_name: str) -> EntityRef:
    return EntityRef(kind=EntityKind.CLASS, identifier=qualified_name)


def _function_ref(qualified_name: str) -> EntityRef:
    return EntityRef(kind=EntityKind.FUNCTION, identifier=qualified_name)


def is_test_path(path: str) -> bool:
    """Whether a repo-relative path looks like a test file, by location or filename."""
    posix_path = path.replace("\\", "/")
    filename = posix_path.rsplit("/", 1)[-1]
    return f"/{posix_path}/".count("/tests/") > 0 or filename.startswith("test_")


def _is_test_module(module: PythonModule) -> bool:
    return is_test_path(module.path)


def _import_statement(imp: ImportedName) -> str:
    if imp.name is not None:
        return f"from {imp.module or '.'} import {imp.name}"
    return f"import {imp.module}"


@dataclass
class _RepositoryIndex:
    """Lookup tables over all analyzed modules, used to resolve references."""

    modules_by_name: dict[str, PythonModule] = field(default_factory=dict)
    classes_by_qname: dict[str, ClassSymbol] = field(default_factory=dict)
    functions_by_qname: dict[str, FunctionSymbol] = field(default_factory=dict)
    # Qualified name (of a module, class, or function/method) -> the module
    # that defines it. Used to find which module a resolved call/construction
    # belongs to, e.g. for TESTS evidence.
    owning_module: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, modules: list[PythonModule]) -> _RepositoryIndex:
        index = cls()
        for module in modules:
            if not module.module_name:
                continue
            index.modules_by_name[module.module_name] = module
            index.owning_module[module.module_name] = module.module_name
            for function in module.functions:
                index.functions_by_qname[function.qualified_name] = function
                index.owning_module[function.qualified_name] = module.module_name
            for klass in module.classes:
                index.classes_by_qname[klass.qualified_name] = klass
                index.owning_module[klass.qualified_name] = module.module_name
                for method in klass.methods:
                    index.functions_by_qname[method.qualified_name] = method
                    index.owning_module[method.qualified_name] = module.module_name
        return index

    def resolve_module(self, dotted: str) -> str | None:
        return dotted if dotted in self.modules_by_name else None

    def module_owning(self, ref: EntityRef) -> str | None:
        return self.owning_module.get(ref.identifier)


def _relative_package(module: PythonModule, level: int) -> str | None:
    if not module.module_name:
        return None
    is_package = module.path.replace("\\", "/").endswith("__init__.py")
    parts = module.module_name.split(".")
    drop = level - 1 if is_package else level
    if drop > len(parts):
        return None
    remaining = parts[: len(parts) - drop] if drop else parts
    return ".".join(remaining) if remaining else None


def _resolve_import_module(
    module: PythonModule, imp: ImportedName, index: _RepositoryIndex
) -> str | None:
    if imp.is_relative:
        base = _relative_package(module, imp.relative_level)
        if base is None:
            return None
        dotted = f"{base}.{imp.module}" if imp.module else base
    else:
        dotted = imp.module

    if imp.name is not None:
        nested = f"{dotted}.{imp.name}"
        if index.resolve_module(nested):
            return nested

    return index.resolve_module(dotted)


def _local_name_map(module: PythonModule, index: _RepositoryIndex) -> dict[str, str]:
    """Map names visible in `module`'s namespace to a resolved qualified name.

    The resolved value is a module name, a class qualified name, or a
    function qualified name -- callers decide which of those they accept.
    """
    local_map: dict[str, str] = {}

    for imp in module.imports:
        target_module = _resolve_import_module(module, imp, index)
        local_name = imp.alias or imp.name or imp.module.split(".")[-1]
        if imp.name is not None and target_module is not None:
            candidate = f"{target_module}.{imp.name}"
            if candidate in index.classes_by_qname or candidate in index.functions_by_qname:
                local_map[local_name] = candidate
                continue
        if target_module is not None:
            local_map[local_name] = target_module

    for function in module.functions:
        local_map.setdefault(function.name, function.qualified_name)
    for klass in module.classes:
        local_map.setdefault(klass.name, klass.qualified_name)

    return local_map


def _contains_relationships(module: PythonModule) -> list[Relationship]:
    if not module.module_name:
        return []

    relationships: list[Relationship] = []
    module_ref = _module_ref(module.module_name)

    relationships.append(
        Relationship(
            kind=RelationshipKind.CONTAINS,
            source=_file_ref(module.path),
            target=module_ref,
            evidence=f"{module.path} defines module `{module.module_name}`",
        )
    )

    for function in module.functions:
        relationships.append(
            Relationship(
                kind=RelationshipKind.CONTAINS,
                source=module_ref,
                target=_function_ref(function.qualified_name),
                evidence=(
                    f"`{function.qualified_name}` is defined at "
                    f"{module.path}:{function.location.line_start}"
                ),
                location=function.location,
            )
        )

    for klass in module.classes:
        class_ref = _class_ref(klass.qualified_name)
        relationships.append(
            Relationship(
                kind=RelationshipKind.CONTAINS,
                source=module_ref,
                target=class_ref,
                evidence=(
                    f"`{klass.qualified_name}` is defined at "
                    f"{module.path}:{klass.location.line_start}"
                ),
                location=klass.location,
            )
        )
        for method in klass.methods:
            relationships.append(
                Relationship(
                    kind=RelationshipKind.CONTAINS,
                    source=class_ref,
                    target=_function_ref(method.qualified_name),
                    evidence=(
                        f"`{method.qualified_name}` is defined at "
                        f"{module.path}:{method.location.line_start}"
                    ),
                    location=method.location,
                )
            )

    return relationships


def _import_relationships(module: PythonModule, index: _RepositoryIndex) -> list[Relationship]:
    if not module.module_name:
        return []

    relationships: list[Relationship] = []
    source_ref = _module_ref(module.module_name)
    seen: set[str] = set()

    for imp in module.imports:
        target_module = _resolve_import_module(module, imp, index)
        if target_module is None or target_module == module.module_name:
            continue
        if target_module in seen:
            continue
        seen.add(target_module)
        relationships.append(
            Relationship(
                kind=RelationshipKind.IMPORTS,
                source=source_ref,
                target=_module_ref(target_module),
                evidence=f"{module.path}:{imp.location.line_start} -- `{_import_statement(imp)}`",
                location=imp.location,
            )
        )

    return relationships


def _inherits_relationships(
    modules: list[PythonModule],
    local_maps: dict[str, dict[str, str]],
    index: _RepositoryIndex,
) -> list[Relationship]:
    relationships: list[Relationship] = []

    for module in modules:
        if not module.module_name:
            continue
        local_map = local_maps.get(module.module_name, {})

        for klass in module.classes:
            for base in klass.bases:
                resolved = local_map.get(base)
                if resolved is None or resolved not in index.classes_by_qname:
                    continue
                relationships.append(
                    Relationship(
                        kind=RelationshipKind.INHERITS,
                        source=_class_ref(klass.qualified_name),
                        target=_class_ref(resolved),
                        evidence=(
                            f"`{klass.qualified_name}` declares base `{base}` at "
                            f"{module.path}:{klass.location.line_start}"
                        ),
                        location=klass.location,
                    )
                )

    return relationships


def _resolve_call(
    owning_class: ClassSymbol | None,
    expression: str,
    local_map: dict[str, str],
    index: _RepositoryIndex,
    *,
    attribute_types: dict[str, dict[str, str]],
    local_var_types: dict[str, str],
    local_maps: dict[str, dict[str, str]],
) -> EntityRef | None:
    """Resolve a call expression's callee to the entity it invokes, or None if unresolvable.

    Levels of resolution, from most to least direct:
      1. `self.method()` / `cls.method()` (`this.method()` in a TypeScript
         source's facts -- the domain model doesn't distinguish which
         analyzer produced a `PythonModule`, so the same self-reference
         resolution serves both) -- resolved on the owning class, walking
         resolvable base classes (inheritance-aware dispatch).
      2. `self.attr.method()` / `cls.attr.method()` -- `attr`'s type is
         looked up (constructor-injected dependency, direct construction,
         dataclass-style annotation, or factory return type -- see
         `domain.type_inference`), then the method is resolved on that type.
      3. `Name()` -- a bare call: direct construction/factory/local function
         call, resolved via this module's import/definition namespace.
      4. `Name.method()` -- `Name` resolved as a locally-known class (calls
         the method, inheritance-aware) or as a local variable of known type
         (constructor-injected parameter, direct construction, factory
         return, or simple name propagation from another typed variable).
    Anything else -- a deeper attribute chain, a call through an untyped
    parameter, a dynamically computed callee -- resolves to None: dropped,
    never guessed.
    """
    parts = expression.split(".")

    if parts[0] in ("self", "cls", "this") and owning_class is not None:
        if len(parts) == 2:
            return resolve_method_on_class(owning_class.qualified_name, parts[1], index, local_maps)
        if len(parts) == 3:
            target_class = resolve_attribute_type(
                owning_class.qualified_name, parts[1], attribute_types, index, local_maps
            )
            if target_class is None:
                return None
            return resolve_method_on_class(target_class, parts[2], index, local_maps)
        return None

    root = parts[0]
    if len(parts) == 1:
        resolved = local_map.get(root)
        if resolved is None:
            return None
        if resolved in index.functions_by_qname:
            return _function_ref(resolved)
        if resolved in index.classes_by_qname:
            return _class_ref(resolved)
        return None

    if len(parts) == 2:
        method_name = parts[1]
        resolved = local_map.get(root)
        if resolved is not None and resolved in index.classes_by_qname:
            return resolve_method_on_class(resolved, method_name, index, local_maps)
        var_type = local_var_types.get(root)
        if var_type is not None:
            return resolve_method_on_class(var_type, method_name, index, local_maps)
        return None

    return None


def _calls_relationships(
    modules: list[PythonModule],
    local_maps: dict[str, dict[str, str]],
    index: _RepositoryIndex,
    attribute_types: dict[str, dict[str, str]],
    local_var_types: dict[str, dict[str, str]],
) -> tuple[list[Relationship], list[UnresolvedCall]]:
    relationships: list[Relationship] = []
    unresolved: list[UnresolvedCall] = []

    for module in modules:
        if not module.module_name:
            continue
        local_map = local_maps.get(module.module_name, {})

        module_relationships, module_unresolved = _calls_for_call_sites(
            module.calls,
            _module_ref(module.module_name),
            None,
            module,
            local_map,
            index,
            attribute_types=attribute_types,
            local_var_types={},
            local_maps=local_maps,
        )
        relationships.extend(module_relationships)
        unresolved.extend(module_unresolved)

        for function, owning_class in iter_function_callers(module):
            function_relationships, function_unresolved = _calls_for_call_sites(
                function.calls,
                _function_ref(function.qualified_name),
                owning_class,
                module,
                local_map,
                index,
                attribute_types=attribute_types,
                local_var_types=local_var_types.get(function.qualified_name, {}),
                local_maps=local_maps,
            )
            relationships.extend(function_relationships)
            unresolved.extend(function_unresolved)

    return relationships, unresolved


def _call_site_location(call_site: CallSite) -> SourceLocation:
    return SourceLocation(
        line_start=call_site.line,
        line_end=call_site.line,
        col_start=call_site.column,
        col_end=None,
    )


def _calls_for_call_sites(
    call_sites: list[CallSite],
    source_ref: EntityRef,
    owning_class: ClassSymbol | None,
    module: PythonModule,
    local_map: dict[str, str],
    index: _RepositoryIndex,
    *,
    attribute_types: dict[str, dict[str, str]],
    local_var_types: dict[str, str],
    local_maps: dict[str, dict[str, str]],
) -> tuple[list[Relationship], list[UnresolvedCall]]:
    """Resolve every call site belonging to one scope (a function, or a module's
    own top-level statements) into `CALLS` relationships plus `UnresolvedCall`
    facts for whatever didn't resolve. `source_ref` is that scope's own entity --
    a `FunctionSymbol`'s qualified name, or the module itself for module-level
    calls.
    """
    relationships: list[Relationship] = []
    unresolved: list[UnresolvedCall] = []
    seen: set[str] = set()

    for call_site in call_sites:
        target_ref = _resolve_call(
            owning_class,
            call_site.expression,
            local_map,
            index,
            attribute_types=attribute_types,
            local_var_types=local_var_types,
            local_maps=local_maps,
        )
        if target_ref is None:
            unresolved.append(
                UnresolvedCall(
                    source=source_ref,
                    expression=call_site.expression,
                    location=_call_site_location(call_site),
                )
            )
            continue
        if target_ref.identifier == source_ref.identifier:
            continue
        if target_ref.identifier in seen:
            continue
        seen.add(target_ref.identifier)
        relationships.append(
            Relationship(
                kind=RelationshipKind.CALLS,
                source=source_ref,
                target=target_ref,
                evidence=(
                    f"`{source_ref.identifier}` calls `{call_site.expression}` at "
                    f"{module.path}:{call_site.line}"
                ),
                location=_call_site_location(call_site),
            )
        )

    return relationships, unresolved


def _tests_relationships(
    modules: list[PythonModule],
    local_maps: dict[str, dict[str, str]],
    index: _RepositoryIndex,
    attribute_types: dict[str, dict[str, str]],
    local_var_types: dict[str, dict[str, str]],
) -> list[Relationship]:
    """TESTS is asserted only from a resolved call, not from an import alone.

    A test module importing a module is not itself evidence that it tests
    it; a test function *calling* (or constructing) something defined in
    that module is. This also covers "imports X and directly constructs X"
    (a constructor call `X(...)` is a call like any other).

    Two granularities are asserted from the same resolved call: a
    module-level edge (test module -> target module, deduplicated per
    target module -- unchanged from before) and a symbol-level edge (test
    function -> the specific function/class/method it resolved to,
    deduplicated per (test function, target)). The symbol-level edge only
    exists when the call resolution in `_resolve_call` actually reached a
    specific function or class -- e.g. through a constructor-injected
    fixture attribute -- so `impact <function>` can find tests that exercise
    it directly instead of only tests of its whole module.
    """
    relationships: list[Relationship] = []

    for module in modules:
        if not module.module_name or not _is_test_module(module):
            continue
        source_ref = _module_ref(module.module_name)
        local_map = local_maps.get(module.module_name, {})
        seen_modules: set[str] = set()

        for function, owning_class in iter_function_callers(module):
            function_local_var_types = local_var_types.get(function.qualified_name, {})
            seen_symbols: set[str] = set()

            for call_site in function.calls:
                target_ref = _resolve_call(
                    owning_class,
                    call_site.expression,
                    local_map,
                    index,
                    attribute_types=attribute_types,
                    local_var_types=function_local_var_types,
                    local_maps=local_maps,
                )
                if target_ref is None:
                    continue
                target_module = index.module_owning(target_ref)
                if target_module is None or target_module == module.module_name:
                    continue
                target_module_obj = index.modules_by_name.get(target_module)
                if target_module_obj is not None and _is_test_module(target_module_obj):
                    continue

                evidence = (
                    f"`{function.qualified_name}` calls `{call_site.expression}` "
                    f"(resolved to `{target_ref.identifier}`) at "
                    f"{module.path}:{call_site.line}"
                )

                if (
                    target_ref.identifier != function.qualified_name
                    and target_ref.identifier not in seen_symbols
                ):
                    seen_symbols.add(target_ref.identifier)
                    relationships.append(
                        Relationship(
                            kind=RelationshipKind.TESTS,
                            source=_function_ref(function.qualified_name),
                            target=target_ref,
                            evidence=evidence,
                            location=_call_site_location(call_site),
                        )
                    )

                if target_module not in seen_modules:
                    seen_modules.add(target_module)
                    relationships.append(
                        Relationship(
                            kind=RelationshipKind.TESTS,
                            source=source_ref,
                            target=_module_ref(target_module),
                            evidence=evidence,
                            location=_call_site_location(call_site),
                        )
                    )

    return relationships


@dataclass
class RelationshipBuildResult:
    """Both outputs of one `build_relationships_with_unresolved` run.

    Kept as one call rather than two separate functions re-deriving the same
    resolution context (`_RepositoryIndex`, local name maps, attribute/local-
    variable types) twice.
    """

    relationships: list[Relationship] = field(default_factory=list)
    unresolved_calls: list[UnresolvedCall] = field(default_factory=list)


def build_relationships_with_unresolved(modules: list[PythonModule]) -> RelationshipBuildResult:
    """Derive all deterministic relationship facts, plus every call that didn't resolve.

    See `build_relationships` for the relationships-only convenience wrapper
    most callers want.
    """
    index = _RepositoryIndex.build(modules)

    relationships: list[Relationship] = []
    for module in modules:
        relationships.extend(_contains_relationships(module))
        relationships.extend(_import_relationships(module, index))

    local_maps = {
        module.module_name: _local_name_map(module, index)
        for module in modules
        if module.module_name
    }
    attribute_types = build_attribute_types(modules, index, local_maps)
    local_var_types = build_local_var_types(modules, index, local_maps, attribute_types)

    relationships.extend(_inherits_relationships(modules, local_maps, index))
    call_relationships, unresolved_calls = _calls_relationships(
        modules, local_maps, index, attribute_types, local_var_types
    )
    relationships.extend(call_relationships)
    relationships.extend(
        _tests_relationships(modules, local_maps, index, attribute_types, local_var_types)
    )

    kind_counts = Counter(rel.kind.value for rel in relationships)
    _logger.info(
        "build_relationships modules=%d relationships=%d kinds=%s unresolved_calls=%d",
        len(modules),
        len(relationships),
        dict(kind_counts),
        len(unresolved_calls),
    )
    for call in unresolved_calls:
        owning_module = index.modules_by_name.get(index.module_owning(call.source) or "")
        _logger.debug(
            "unresolved call source=%s expression=%r at %s:%d",
            call.source.identifier,
            call.expression,
            owning_module.path if owning_module else "?",
            call.location.line_start,
        )

    return RelationshipBuildResult(relationships=relationships, unresolved_calls=unresolved_calls)


def build_relationships(modules: list[PythonModule]) -> list[Relationship]:
    """Derive all deterministic relationship facts for a set of analyzed modules.

    A thin wrapper over `build_relationships_with_unresolved` for the (common)
    case where the caller doesn't need the unresolved-call facts too.
    """
    return build_relationships_with_unresolved(modules).relationships
