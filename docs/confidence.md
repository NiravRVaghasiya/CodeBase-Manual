# Confidence

Confidence is never read from a model's response. Every `confidence` field
on an `ai.models` result is computed by `codebase_manual.ai.confidence`
from the strength of the deterministic evidence that grounded the result.
As of this change, none of the LLM-facing JSON schemas in `ai.qa`,
`ai.change_planner`, `ai.impact`, or `ai.summarizer` include a `confidence`
field at all -- it is structurally impossible for the model to set it.

## `confidence_from_strengths(strengths)`

Used by `ai.qa`, `ai.change_planner`, and `ai.impact`, whose evidence comes
from retrieval matches or relationship-graph edges (see
`docs/evidence-model.md` for what strength each relationship kind carries).

| Input | Result | Why |
|---|---|---|
| No evidence, or only `UNKNOWN` | `LOW` | Nothing, or only an explicit "could not determine", backs the result. |
| Every piece of evidence is `DIRECT`/`RESOLVED` | `HIGH` | Everything backing the result is a hard fact. |
| Anything else (a mix, or purely `INFERRED`) | `MEDIUM` | At least one heuristic (e.g. keyword-overlap retrieval) is load-bearing. |

In practice, `ai.qa` and `ai.change_planner` ground their results in
keyword-overlap retrieval, which is `INFERRED` -- so those results cap out
at `MEDIUM` even when the model is very fluent, because the retrieval
signal itself is a heuristic. `ai.impact` grounds its facts in the
relationship graph -- `IMPORTS`/`CALLS`/`INHERITS`/`TESTS` edges are all
`RESOLVED`, so a target with only those kinds of dependents can reach
`HIGH`.

## `confidence_for_summary(has_docstring, has_structural_facts)`

Used by `ai.summarizer`, whose job is to paraphrase facts already read from
source rather than resolve a relationship:

| Docstring present | Other structure (functions/classes/params) | Result |
|---|---|---|
| Yes | -- | `HIGH` -- the model paraphrased stated intent. |
| No | Yes | `MEDIUM` -- the model inferred purpose from shape alone. |
| No | No | `LOW` -- there was almost nothing to summarize. |

## Rationale

This satisfies the project's non-negotiable rule that "LLM confidence is
not authoritative": the model may still explain its own uncertainty in
prose (via `reasoning`/`explanation` fields), but the number/label attached
to a result is deterministic, documented here, and covered by
`tests/unit/test_confidence.py`.
