# AI grounding: candidate IDs and the validator

## The problem this closes

Before this change, `ai.change_planner` took `files_to_modify`,
`files_to_create`, `relevant_symbols`, and `tests_to_update` directly from
the model's JSON response as raw strings, with no check that they referred
to anything real. `ai.qa` had the same gap implicitly (the model's prose
answer was never checked against what was retrieved). An LLM could invent
a plausible-sounding path or symbol and it would flow straight into a
returned `ChangePlan`/`Answer`.

## Candidate IDs (`query.candidates`)

Before a prompt is sent, `build_candidate_set(retrieval)` assigns opaque
IDs (`FILE_001`, `TEST_001`, `SYMBOL_001`, ...) to the bounded set of
files/symbols that deterministic retrieval already found. The prompt gives
the model only these IDs -- via `CandidateSet.prompt_block()` -- never raw
paths or symbol names for it to reference. `CandidateSet.resolve(id)` is
the only way back to a real `EntityRef`; an ID the model didn't receive
simply resolves to `None`.

Files under a `tests/` directory or named `test_*` are grouped separately
as `TEST_xxx` candidates rather than `FILE_xxx` (see
`domain.relationships.is_test_path`).

## `GroundingValidator` (`ai.grounding`)

- `resolve_candidate_ids(ids)` resolves a list of IDs the model returned
  for one field, and returns a `ValidationResult` with a `ValidationVerdict`
  (`VALID` / `PARTIALLY_VALID` / `INVALID`), the resolved `EntityRef`s, and
  the rejected IDs. An empty input list is trivially `VALID` -- that is not
  the same as every ID failing to resolve.
- `validate_proposed_path(path)` checks a `files_to_create` proposal isn't
  actually an existing file (existing paths come from both
  `snapshot.files` and `snapshot.modules`, since many snapshots only
  populate one of the two).
- `combine_verdicts(verdicts)` combines several per-field verdicts into one
  overall verdict for a result: `VALID` only if every field was `VALID`,
  `INVALID` only if every field was `INVALID`, otherwise
  `PARTIALLY_VALID`. Callers only pass verdicts for fields the model
  actually attempted to populate -- an empty, unattempted field must not
  dilute a genuine rejection found elsewhere.

Rejected IDs are dropped from the result, not silently kept -- they never
become a `FileRecommendation`, a `relevant_symbols` entry, or cited
evidence. `ChangePlan.grounding` and `Answer.grounding` expose the overall
verdict so a caller can tell "this result had something quarantined" from
"this result is fully backed by evidence".

## Where this shows up

- `ai.change_planner.plan_change`: `files_to_modify` items reference
  `FILE_xxx` IDs; `relevant_symbols`/`tests_to_update` reference
  `SYMBOL_xxx`/`TEST_xxx` IDs. `files_to_create` paths can't have an ID
  (they don't exist yet) -- they're validated as *not already existing*
  instead, and are represented as proposals, never as if they were an
  indexed fact.
- `ai.qa.answer_question`: the model must list `cited_ids` for the
  candidates its answer actually relied on; unresolved citations are
  dropped from `Answer.evidence` and don't contribute to `Answer.confidence`.

See `tests/unit/test_grounding.py`, `tests/unit/test_change_planner.py`,
and `tests/unit/test_qa.py` for the hallucination-quarantine cases this
covers (a valid ID, an invented ID, and a mix of both).
