"""Resolves a free-text identifier to an indexed entity.

Used by both the CLI and the web UI, which each decide separately how to
report "no match" (exit code vs. an HTTP error page).
"""

from __future__ import annotations

from codebase_manual.domain.models import EntityKind, EntityRef
from codebase_manual.persistence.snapshot import RepositorySnapshot


def find_entity_ref(target: str, snapshot: RepositorySnapshot) -> EntityRef | None:
    """Find `target` among indexed files, modules, classes, and functions, in that order."""
    if any(f.path == target for f in snapshot.files):
        return EntityRef(kind=EntityKind.FILE, identifier=target)

    for module in snapshot.modules:
        if module.module_name == target:
            return EntityRef(kind=EntityKind.MODULE, identifier=target)
        for klass in module.classes:
            if klass.qualified_name == target:
                return EntityRef(kind=EntityKind.CLASS, identifier=target)
            for method in klass.methods:
                if method.qualified_name == target:
                    return EntityRef(kind=EntityKind.FUNCTION, identifier=target)
        for function in module.functions:
            if function.qualified_name == target:
                return EntityRef(kind=EntityKind.FUNCTION, identifier=target)

    return None
