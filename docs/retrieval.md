# Retrieval

`query.retrieval.retrieve_relevant` is deterministic keyword/graph
retrieval, not an LLM call and not embeddings. It narrows a natural-
language query down to a bounded, explainable set of files/symbols before
`ai.qa`/`ai.change_planner` ever build a prompt.

## Stages

1. **Lexical name match** -- a query term equals a symbol's/file's bare
   name exactly (`MatchReason.NAME_EXACT`, weight 5), or matches a
   component of its qualified name/path (`MatchReason.QUALIFIED_NAME`,
   weight 4).
2. **Docstring match** -- a query term appears in the docstring
   (`MatchReason.DOCSTRING`, weight 3).
3. **Relationship expansion** -- entities directly connected (one hop, via
   `CALLS`/`IMPORTS`/`INHERITS`/`TESTS`, in either direction) to whatever
   matched in stages 1-2 are added, even if they didn't match lexically
   themselves (`MatchReason.RELATIONSHIP`, weight 2).
4. **Directory proximity** -- files in the same directory as a lexically
   matched file are added (`MatchReason.DIRECTORY_PROXIMITY`, weight 1).

Stages 3-4 only ever add to what stages 1-2 already found -- they never
run when nothing matched lexically (`RetrievalResult.is_empty` stays a
hard "no evidence," not a fallback into fabrication).

A candidate's `score` is the sum of the weights of every signal that
applied to it (a file can accumulate more than one -- e.g. a docstring
match *and* being a directory neighbor of the top match). This is the
explainable weighting table from the project brief:

| Signal | Weight |
|---|---|
| Name exact match | 5 |
| Qualified name / path component match | 4 |
| Docstring match | 3 |
| Relationship (CALLS/IMPORTS/INHERITS/TESTS) | 2 |
| Directory proximity | 1 |

Every `RetrievedFile`/`RetrievedSymbol` exposes `signals: list[MatchSignal]`
-- each with a human-readable `detail` -- so any retrieved item can answer
"why was this retrieved," not just "how relevant is this."

## Relationship chains

For architectural questions ("how does authentication flow?"), isolated
file/symbol facts force the model to reconstruct the call graph itself --
which it cannot be trusted to do accurately. `retrieve_relevant` also
returns `relationship_chains: list[RelationshipChain]`: short, deduplicated
forward `CALLS` paths (up to `chain_depth` hops, default 3) starting from
the top-ranked matched functions, e.g.:

```
AuthRouter.login --calls--> AuthService.authenticate --calls--> UserRepository.find_user
```

`ai.qa` and `ai.change_planner` include these chains verbatim in the
prompt context and instruct the model to use them directly for "how does
X work" questions, rather than guessing at architecture from filenames and
docstrings alone.

## What this deliberately does not do

No semantic/embedding retrieval stage exists. The project brief is
explicit that embeddings should not be added "merely because AI is
involved" -- if lexical + relationship + directory signals prove
insufficient in practice, a semantic stage would be added as an
additional, clearly-labeled signal (its own `MatchReason`, its own
weight), not a replacement for the deterministic ones.
