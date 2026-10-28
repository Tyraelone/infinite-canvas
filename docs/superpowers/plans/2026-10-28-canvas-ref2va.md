# Canvas Ref2VA integration

**Goal:** Connect canvas manual and Agent video generation to the existing local H3 native reference workflow.

**Design:** Reuse the existing quality model, videoMode setting, reference node order, gateway uploads, studio queue, stop and persisted task polling. Expose Ref2VA in H3 settings. Pass 1–3 ordinary reference images in order, use Picture tags, require quality mode, and reject ambiguous frame/video/audio inputs before uploading. Quality multi-image requests infer Ref2VA; explicit single-image Ref2VA remains selectable. Keep existing text and first-frame behavior.

## Tasks

- [x] Add failing frontend, Agent-tool and gateway tests for order, labels, mode, bounds, profile and invalid inputs.
- [x] Connect request serialization, gateway mode validation and actual task metadata to the existing native workflow.
- [x] Add settings and Agent referenceMode support, persist mode and document capability.
- [x] Run regressions, inspect a real canvas three-image request and verify a real Ref2VA output without disturbing other jobs.
- [x] Review the scoped changes, deploy and update pending-test notes.

**Result:** 58 Node and 51 Python tests pass; production build passes. The existing nine optional-token type errors remain. Independent review findings were reproduced and fixed: bound frames retained through manual/retry paths, native retry retains saved settings, multipart frames retain their role, inherited Agent mode does not block other models. Live job 9650c4ba625442faa895bdee2a7050bc used three pixel-verified ordered references, native Ref2VA weights and 20 steps. MP4 decoded to 124 frames at 24fps with audio, and the canvas played to the end. Evidence: data/local-runtime/ref2va-acceptance. TODO reviewed: no remaining integration task; visual identity quality remains a model-output review concern.

**Verification:** Test text, one first frame, 1–3 native references, four-reference rejection, missing/excess tags, Turbo rejection, mixed frames and native inputs, cancellation and retry. Confirm the queued native graph uses the Ref2VA weights and three ordered LoadImage nodes. Inspect an actual decoded video and preserved audio, not just a submitted task.

Work in the existing checkout with a scoped baseline under data/local-runtime/ref2va-baseline-*. Preserve all earlier fixes and user changes. No new model installation or changes to the studio are needed.
