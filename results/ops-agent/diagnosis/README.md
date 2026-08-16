# Phase 3 Diagnosis Results

`golden-eval-v1.json` is a deterministic offline evaluation summary produced
with scripted model responses. It validates the Agent loop, allowlisted tool
selection, evidence citations, abstention, schema, budget, and stop contracts
without calling OpenAI.

Live `ops.diagnosis.v1` files in this directory remain local and Git-ignored.
They may contain runtime topology, pod identifiers, revisions, evidence IDs,
and token usage. They must never contain `OPENAI_API_KEY` or raw source bodies.

The 2026-08-16 positive run-01 Luna rerun selected four normalized tools. Its
initial structured result failed stop-consistency validation, then passed one
tool-free output repair. `20260816-positive-run-01-luna.json` is the completed
local artifact; its final stop is `insufficient_evidence`. Invalid intermediate
output is represented only by validation-attempt metadata and was not promoted
to a completed diagnosis.
