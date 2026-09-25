"""Deterministic structural analysis of TypeScript source files.

This is the second `LanguageAnalyzer` (see `analyzer.registry`), added as a
proof that the domain model/relationship/retrieval/AI pipeline is genuinely
language-agnostic, not merely designed to look that way (`docs/analyzers.md`
flagged this as the point at which `PythonModule` should eventually be
renamed -- not done in this change; see that doc for why).

Unlike `analyzer.python_analyzer`, which builds on the standard library's
real `ast` parser, there is no bundled TypeScript grammar available here
(no `tree-sitter`/compiler-API dependency was added -- see the project's
dependency-minimalism precedent, and this environment had no way to install
one safely). This module is therefore a deliberately conservative
brace-depth scanner over regex-recognized declaration shapes, not a real
parser. It follows the same "fail closed" principle as the Python analyzer:
a construct it doesn't confidently recognize is skipped entirely, never
guessed at. Concretely, it extracts:

- `import` statements (named, default, namespace, and side-effect imports;
  `import type` is treated the same as a value import).
- Top-level and nested `function` declarations (not arrow functions or
  function expressions assigned to a variable -- those have no reliable
  brace-scoped signature to anchor on without a real parser).
- `class` declarations, with an `extends` base and method declarations
  inside them (constructors and getters/setters included; object-literal
  shorthand methods and arrow-function class fields are not detected).
- Call expressions (`name(...)`, `a.b.c(...)`, `new Name(...)`), scoped the
  same way the Python analyzer scopes them: a call inside a nested
  function/method belongs to that scope, not its enclosing one; a call
  directly in a class body (not inside a method) is not recorded at all --
  the same "no confident scope to attribute it to" reasoning the Python
  analyzer applies to a lambda body.

No `Assignment` facts (see `domain.models.Assignment`) are extracted for
TypeScript at all, so `domain.type_inference`'s attribute/local-variable
typing never has anything to resolve for a TS class -- `this.method()`
resolves (same-class dispatch, inheritance-aware, via `domain.relationships`
recognizing `this` as a self-reference alongside Python's `self`/`cls`),
but `this.someInjectedField.method()` does not, unlike the equivalent
Python `self.attr.method()` shape. This is a real, disclosed gap in parity
with the Python analyzer, not an oversight -- extending assignment
extraction to TypeScript's `this.x = y` constructor-parameter-property
shorthand and class-field initializers would be the natural next step if
TypeScript support needs to grow past this proof-of-architecture stage.

Known, deliberate gaps (fail closed, not fabricated): arrow functions and
function expressions are not extracted as symbols, so calls inside them are
attributed to their *enclosing* named scope rather than a scope of their
own. Expressions inside a template literal's `${...}` interpolation are not
scanned (the whole template literal is treated as an opaque string).
Comments and string/template contents are blanked out before scanning, but
regular-expression literals are not specially recognized -- a `//` inside a
regex literal can be misread as a line-comment start. Cross-file relative
imports (`./service`) are extracted as facts but are not resolved by
`domain.relationships` the way a Python package-relative import is --
that resolver's relative-import logic assumes Python package semantics
(`docs/relationship-model.md`); same-file resolution (local functions/
classes, `CALLS`/`INHERITS` within one file) works identically to Python,
since that's the part of `domain.relationships` this analyzer's facts
actually exercise. None of this differs in kind from the Python analyzer's
own documented gaps -- it is simply a larger set, proportionate to having
no real grammar to lean on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from codebase_manual.analyzer.config import AnalysisContext
from codebase_manual.domain.models import (
    CallArgument,
    CallSite,
    ClassSymbol,
    FunctionSymbol,
    ImportedName,
    PythonModule,
    SourceLocation,
)

_CALL_EXCLUSION_KEYWORDS = frozenset(
    {
        # Control-flow/statement keywords that can be immediately followed by
        # `(` in valid syntax without being a call -- `if (x)`, `return (x)`,
        # `function(x) { ... }` (an anonymous function expression). This is
        # deliberately narrow: `this`/`super` are common, entirely legitimate
        # roots of a real method-call chain (`this.method()`), not excluded.
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "throw",
        "typeof",
        "delete",
        "void",
        "await",
        "yield",
        "do",
        "function",
    }
)

_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_STRING_OR_COMMENT = re.compile(
    r"/\*.*?\*/"  # block comment
    r"|//[^\n]*"  # line comment
    r"|`(?:\\.|[^`\\])*`"  # template literal (interpolation is not scanned)
    r'|"(?:\\.|[^"\\])*"'  # double-quoted string
    r"|'(?:\\.|[^'\\])*'",  # single-quoted string
    re.DOTALL,
)


def _blank(match: re.Match[str]) -> str:
    """Replace matched text with same-length whitespace, preserving line numbers."""
    return "".join(ch if ch == "\n" else " " for ch in match.group(0))


def _strip_comments(source: str) -> str:
    """Blank comments only, keeping string content -- used for import-path
    extraction, since an import's path is itself a quoted string that a
    full string-blanking sanitize would erase."""
    return _COMMENT.sub(_blank, source)


def _sanitize(source: str) -> str:
    """Blank comments *and* string/template content -- used for the brace-depth/
    call-scanning walk, where a brace or paren inside a string must not be
    mistaken for real structure."""
    return _STRING_OR_COMMENT.sub(_blank, source)


_IMPORT_STATEMENT = re.compile(
    r"import\s+(?:type\s+)?"
    r"(?:"
    r"(?P<namespace>\*\s*as\s+[A-Za-z_$][\w$]*)"
    r"|(?P<named>\{[^}]*\})"
    r"|(?P<default>[A-Za-z_$][\w$]*)"
    r")?"
    r"(?:\s*,\s*(?:\{[^}]*\}|\*\s*as\s+[A-Za-z_$][\w$]*))?"
    r"\s*(?:from\s*)?"
    r"['\"](?P<path>[^'\"]+)['\"]"
)
_NAMED_IMPORT_NAME = re.compile(r"([A-Za-z_$][\w$]*)(?:\s+as\s+([A-Za-z_$][\w$]*))?")

_FUNCTION_HEADER = re.compile(
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^>{(]*>)?\([^)]*\)\s*(?::[^{;]+?)?\s*\{"
)
_CLASS_HEADER = re.compile(
    r"(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)"
    r"(?:\s*<[^>{]*>)?"
    r"(?:\s+extends\s+(?P<base>[A-Za-z_$][\w$.]*)(?:\s*<[^>{]*>)?)?"
    r"(?:\s+implements\s+[^{]+)?\s*\{"
)
_METHOD_HEADER = re.compile(
    r"(?:(?:public|private|protected|static|readonly|abstract|override|async)\s+)*"
    r"(?:(?:get|set)\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^>{(]*>)?\([^)]*\)\s*(?::[^{;]+?)?\s*\{"
)
_CALL_EXPRESSION = re.compile(
    r"(?:new\s+)?(?P<callee>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\("
)


def _line_at(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _qualify(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


@dataclass
class _Frame:
    """One open `{...}` scope during the brace-depth walk."""

    kind: str  # "module" | "function" | "method" | "class" | "block"
    name: str
    qualified_name: str
    header_start: int
    calls: list[CallSite] = field(default_factory=list)
    methods: list[FunctionSymbol] = field(default_factory=list)
    base: str | None = None


def _owning_scope(stack: list[_Frame]) -> _Frame | None:
    """The nearest enclosing frame a call should attach to, skipping `block` frames.

    Returns `None` if the nearest non-block frame is a `class` -- a call
    directly in a class body (not inside a method) has no scope this
    analyzer is confident attributing it to, the same reasoning the Python
    analyzer applies to a lambda body.
    """
    for frame in reversed(stack):
        if frame.kind == "block":
            continue
        return None if frame.kind == "class" else frame
    return None


def _enclosing_qualified_name(stack: list[_Frame]) -> str:
    for frame in reversed(stack):
        if frame.kind in ("function", "method", "class", "module"):
            return frame.qualified_name
    return ""


def _call_arguments(call_text: str) -> list[CallArgument]:
    """A shallow, depth-0 split of a call's argument text -- not a real parser.

    Splits only on top-level commas (tracking bracket/paren/brace depth so a
    nested call or object/array literal argument isn't split apart), keeping
    each argument as opaque unparsed text -- there is no keyword-argument
    concept in TypeScript's call syntax, so every `CallArgument` here has
    `keyword=None`.
    """
    depth = 0
    current: list[str] = []
    arguments: list[str] = []
    for char in call_text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            arguments.append("".join(current))
            current = []
        else:
            current.append(char)
    tail = "".join(current).strip()
    if tail:
        arguments.append(tail)
    return [CallArgument(value=arg.strip()) for arg in arguments if arg.strip()]


def _extract_call_text(sanitized: str, open_paren: int) -> str:
    """The text between a call's `(` and its matching `)`, tracking nesting depth."""
    depth = 1
    i = open_paren + 1
    while i < len(sanitized) and depth > 0:
        if sanitized[i] == "(":
            depth += 1
        elif sanitized[i] == ")":
            depth -= 1
        i += 1
    return sanitized[open_paren + 1 : i - 1]


def _extract_imports(comments_stripped: str) -> list[ImportedName]:
    imports: list[ImportedName] = []
    for match in _IMPORT_STATEMENT.finditer(comments_stripped):
        path = match.group("path")
        location = SourceLocation(
            line_start=_line_at(comments_stripped, match.start()),
            line_end=_line_at(comments_stripped, match.end()),
        )
        is_relative = path.startswith(".")

        named = match.group("named")
        if named:
            for name_match in _NAMED_IMPORT_NAME.finditer(named):
                name, alias = name_match.group(1), name_match.group(2)
                imports.append(
                    ImportedName(
                        module=path,
                        name=name,
                        alias=alias,
                        is_relative=is_relative,
                        location=location,
                    )
                )
            continue

        namespace = match.group("namespace")
        if namespace:
            alias = namespace.rsplit(None, 1)[-1]
            imports.append(
                ImportedName(module=path, alias=alias, is_relative=is_relative, location=location)
            )
            continue

        default = match.group("default")
        if default:
            imports.append(
                ImportedName(module=path, alias=default, is_relative=is_relative, location=location)
            )
            continue

        # A side-effect import (`import "./setup"`) -- the module is still a
        # real dependency fact even with nothing bound to a local name.
        imports.append(ImportedName(module=path, is_relative=is_relative, location=location))
    return imports


def _finish_function(frame: _Frame, sanitized: str, end_pos: int) -> FunctionSymbol:
    return FunctionSymbol(
        name=frame.name,
        qualified_name=frame.qualified_name,
        is_method=frame.kind == "method",
        location=SourceLocation(
            line_start=_line_at(sanitized, frame.header_start),
            line_end=_line_at(sanitized, end_pos),
        ),
        calls=frame.calls,
    )


def _walk(
    sanitized: str, module_name: str
) -> tuple[list[CallSite], list[FunctionSymbol], list[ClassSymbol]]:
    """One left-to-right scan, brace-depth-tracked, classifying each `{` and
    attributing each call to the innermost scope that owns it.
    """
    module_frame = _Frame(kind="module", name="", qualified_name=module_name, header_start=0)
    stack: list[_Frame] = [module_frame]
    functions: list[FunctionSymbol] = []
    classes: list[ClassSymbol] = []

    function_ends = {m.end(): m for m in _FUNCTION_HEADER.finditer(sanitized)}
    class_ends = {m.end(): m for m in _CLASS_HEADER.finditer(sanitized)}
    method_ends = {m.end(): m for m in _METHOD_HEADER.finditer(sanitized)}
    header_spans = sorted(
        (m.start(), m.end())
        for m in (*function_ends.values(), *class_ends.values(), *method_ends.values())
    )

    def _inside_a_header(start: int) -> bool:
        return any(hs <= start < he for hs, he in header_spans)

    brace_events: list[tuple[int, str, re.Match[str] | None]] = [
        (m.start(), "{", None) for m in re.finditer(r"\{", sanitized)
    ]
    brace_events += [(m.start(), "}", None) for m in re.finditer(r"\}", sanitized)]
    call_events: list[tuple[int, str, re.Match[str] | None]] = [
        (m.start(), "call", m)
        for m in _CALL_EXPRESSION.finditer(sanitized)
        # Only the *first* dotted segment risks being a control-flow keyword
        # (`if (`, `return (`, `catch (`) -- the last segment is checked
        # nowhere, since `.get(`/`.set(`/`.of(`/`.from(` etc. are common,
        # entirely legitimate method names.
        if m.group("callee").split(".")[0] not in _CALL_EXCLUSION_KEYWORDS
        and not _inside_a_header(m.start())
    ]
    all_events = sorted(brace_events + call_events, key=lambda event: event[0])

    for pos, kind, match in all_events:
        if kind == "{":
            top = stack[-1]
            end = pos + 1
            if top.kind == "class" and end in method_ends:
                header = method_ends[end]
                name = header.group("name")
                stack.append(
                    _Frame(
                        kind="method",
                        name=name,
                        qualified_name=_qualify(top.qualified_name, name),
                        header_start=header.start(),
                    )
                )
            elif end in class_ends:
                header = class_ends[end]
                name = header.group("name")
                stack.append(
                    _Frame(
                        kind="class",
                        name=name,
                        qualified_name=_qualify(_enclosing_qualified_name(stack), name),
                        header_start=header.start(),
                        base=header.group("base"),
                    )
                )
            elif end in function_ends:
                header = function_ends[end]
                name = header.group("name")
                stack.append(
                    _Frame(
                        kind="function",
                        name=name,
                        qualified_name=_qualify(_enclosing_qualified_name(stack), name),
                        header_start=header.start(),
                    )
                )
            else:
                stack.append(_Frame(kind="block", name="", qualified_name="", header_start=end))
            continue

        if kind == "}":
            if len(stack) <= 1:
                continue  # unmatched brace (sanitizer artifact, malformed input) -- ignore
            frame = stack.pop()
            if frame.kind == "function":
                functions.append(_finish_function(frame, sanitized, pos))
            elif frame.kind == "method":
                stack[-1].methods.append(_finish_function(frame, sanitized, pos))
            elif frame.kind == "class":
                classes.append(
                    ClassSymbol(
                        name=frame.name,
                        qualified_name=frame.qualified_name,
                        bases=[frame.base] if frame.base else [],
                        methods=frame.methods,
                        location=SourceLocation(
                            line_start=_line_at(sanitized, frame.header_start),
                            line_end=_line_at(sanitized, pos),
                        ),
                    )
                )
            continue

        # kind == "call"
        assert match is not None
        owner = _owning_scope(stack)
        if owner is None:
            continue
        call_text = _extract_call_text(sanitized, match.end() - 1)
        owner.calls.append(
            CallSite(
                expression=match.group("callee"),
                line=_line_at(sanitized, match.start()),
                containing_symbol_id=owner.qualified_name,
                arguments=_call_arguments(call_text),
            )
        )

    return module_frame.calls, functions, classes


def infer_module_name(relative_path: str) -> str | None:
    """A dotted pseudo-module-name for a TypeScript file, for consistency with
    `EntityRef.MODULE` identifiers elsewhere in the domain model -- TypeScript
    has no real dotted-module convention; this just gives each file a stable,
    readable identifier (`app/services/auth.ts` -> `app.services.auth`)."""
    path = PurePosixPath(relative_path)
    if path.suffix not in (".ts", ".tsx"):
        return None
    parts = list(path.with_suffix("").parts)
    return ".".join(parts) if parts else None


def analyze_module(
    *, file_path: Path, repo_relative_path: str, context: AnalysisContext
) -> PythonModule:
    """Parse a single TypeScript file and extract its structural facts.

    Matches `analyzer.registry.LanguageAnalyzer`'s protocol exactly -- the
    same signature the Python analyzer's entry point has -- so registering
    it is the only change `analyzer.registry` needs. `context.source_roots`
    (a Python-specific concept) is unused here.
    """
    module_name = infer_module_name(repo_relative_path)

    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return PythonModule(path=repo_relative_path, module_name=module_name, parse_error=str(exc))

    imports = _extract_imports(_strip_comments(source))
    sanitized = _sanitize(source)
    module_calls, functions, classes = _walk(sanitized, module_name or repo_relative_path)

    return PythonModule(
        path=repo_relative_path,
        module_name=module_name,
        imports=imports,
        functions=functions,
        classes=classes,
        calls=module_calls,
    )
