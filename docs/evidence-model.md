# Evidence model

`codebase_manual.domain.evidence` defines the only shape an AI-facing claim
is allowed to point to. It is built on top of the pre-existing deterministic
`Relationship` facts in `domain.relationships` -- it does not replace them.

## Types

- `EvidenceType` -- what kind of fact this is (`FILE`, `SYMBOL`, `IMPORT`,
  `CALL`, `INHERITANCE`, `TEST`, `API_ENDPOINT`, `SOURCE_LOCATION`,
  `CONFIGURATION`, `GRAPH_PATH`, `RETRIEVAL_MATCH`).
- `EvidenceStrength` -- how directly the fact was derived:
  - `DIRECT` -- read straight off the AST/filesystem, no resolution step.
  - `RESOLVED` -- required a deterministic resolution step that can fail
    (an import resolved to a module, a call resolved to a callee).
  - `INFERRED` -- a deterministic heuristic with acknowledged
    false-positive risk (keyword-overlap retrieval, an import-only test
    link).
  - `UNKNOWN` -- the fact could not be determined. Its presence is the
    evidence of "unknown" -- never treat it as a negative fact.
- `Evidence` -- a frozen, typed record: `id`, `type`, `source_entity_id`,
  `target_entity_id`, `file_path`, `line_start`/`line_end`, `description`,
  `strength`.

## How it's built

`evidence_from_relationship(relationship, id=..., file_path=...)` is the
only constructor. It takes an already-derived `Relationship` (from
`domain.relationships.build_relationships`, which only asserts facts backed
by concrete syntax) and turns it into a structured `Evidence` record. There
is no path from an LLM string to an `Evidence` record -- evidence is always
built from a fact the deterministic analyzer already produced.

`strength_for_relationship_kind(kind)` fixes the strength per relationship
kind, as a property of how that kind is derived, not a per-instance
judgment call:

| Kind | Strength | Why |
|---|---|---|
| `CONTAINS` | DIRECT | Read straight off the AST (a symbol's own definition). |
| `IMPORTS` | RESOLVED | An import statement resolved to a known module. |
| `CALLS` | RESOLVED | A call expression resolved to a known callee. |
| `INHERITS` | RESOLVED | A base-class name resolved to a known class. |
| `TESTS` | RESOLVED | Asserted only from a resolved call/construction made by a test function against a symbol in the target module -- the same resolution mechanism as `CALLS`, not a bare import (see `domain.relationships._tests_relationships`). |

## Where it's used

- `ai.confidence.confidence_from_strengths` maps a list of evidence
  strengths to a `Confidence` level (see `docs/confidence.md`).
- `ai.grounding.GroundingValidator` resolves candidate IDs (see
  `docs/ai-grounding.md`) back to real entities before they can appear in
  an AI result.
