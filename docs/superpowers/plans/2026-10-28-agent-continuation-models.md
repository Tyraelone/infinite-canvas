# Agent continuation and model selection implementation plan

**Goal:** Fix repeated unchanged node reads consuming every Agent turn, and let the local creative Agent select its Codex model and reasoning effort.

**Design:** Keep the current shared canvas and local gateway. Detect unchanged read loops, give the model corrective feedback, and finish with a truthful summary if it keeps looping. Allow up to 48 useful model rounds. Keep real tool results and cancellation intact. Read available models and supported efforts from the local Codex account; save the selections in the existing canvas Agent configuration and pass them through the gateway.

**Constraints:** Preserve all existing uncommitted work and user canvas data. No new dependencies, model generation or edits to user artwork during verification. Existing remote channels retain their reasoning behavior. Use the existing Chinese UI and theme.

## Tasks

- [x] Add failing runtime tests for repeated reads, recovery into a write, changing task progress, more than 12 useful steps, cancellation, and bounded finalization.
- [x] Add failing gateway/RPC tests for actual catalog metadata, selected model/effort forwarding, defaults and unsupported selections.
- [x] Implement runtime correction and a bounded final summary; clarify the existing tool-result prompt and pair history calls with results.
- [x] Add local Codex catalog endpoint, validate selections, and reuse existing model/effort controls for creative Agent mode.
- [x] Run targeted regressions, inspect UI using an isolated test canvas, verify actual Codex request settings, and update pending-test documentation.

## Verification

49 Node and 44 Python tests pass. Production build passes; the existing nine optional-token type errors in canvas-client-page.tsx remain. Independent review found and verified fixes for initial-request/catalog races and same-model cross-channel routing. Live gateway selected GPT-6-Luna with medium effort. Live UI selected GPT-6.1-Sol with xhigh, retained selections after refresh, and performed create_text_node then get_node/update_text_node exactly once on the isolated test canvas. Evidence: data/local-runtime/agent-fix-acceptance. TODO reviewed: no new outstanding feature task.

## Review focus

- Unchanged reads across different call IDs must be recognized; changed task progress and reads after writes must remain valid.
- Loop correction must preserve full returned node text and original tool-call pairing.
- Tool calls during forced finalization must never execute.
- A saved model/effort that is no longer supported must not silently use another model or effort.
- Stop, refresh persistence, remote providers and media auto-submit behavior must remain unchanged.

## Execution notes

Work in the current checkout because the local runtime and relevant features are uncommitted here; a baseline copy of all edited existing files is saved under data/local-runtime/agent-fix-baseline-*. Do not reset, commit or move unrelated work. Execute inline and validate with focused tests and UI evidence.
