# Ref2VA maximum reference count

**Goal:** Raise native H3 Ref2VA image references from the previous interface cap of three to the supported maximum of nine.

**Evidence:** Official MiniMax H3 model card specifies at most nine reference images; the installed ComfyUI MiniMaxH3ReferenceToVideo schema also sets the image autogrow maximum to nine. Do not modify the model node or inference behavior.

**Design:** Share a nine-image native limit between studio validation/status and the gateway. Keep Qwen/image editing at three and preserve Turbo/first-frame behavior. Update the canvas request bound, UI hints, Agent capability and templates, studio upload UI, and request byte ceiling. Preserve all nine images and Picture indices; reject ten before uploading or submitting.

- [x] Add failing 4–9 image, tenth-image rejection, order/tag, byte-bound and unchanged Qwen tests.
- [x] Update native limits and all user/Agent capability descriptions.
- [x] Verify studio nine-image upload UI without silently truncating overflow.
- [x] Run regressions and independent review; rebuild and reload only affected services while idle.
- [x] Verify a real nine-slot native request and decoded video, retain evidence, and update pending-test notes.

Work in the current checkout and existing studio. Scoped backups are under data/local-runtime/ref2va-nine-baseline-*. No new model files, drivers or ComfyUI changes. No original canvas edits or automatic memory updates.

## Verification result

Canvas Node: 64 passed. Canvas Python: 57 passed. Studio Python: 46 passed with workflows imported before discovery; an existing short HTTP timeout test was intermittent in ordinary discovery, while isolated and old baseline checks passed. Production build passed. Independent review found no new issue.

Real job `7eb9ec86723c47cea69b41bc8cce57df` passed through the canvas proxy and produced a fully decoded 864×480, 124-frame, 24fps, 5.17-second video with nonzero audio. All nine graph inputs matched the expected pixel hashes. The three existing fixtures were each repeated three times to test the count boundary; this does not establish nine distinct-subject consistency. Evidence is in `data/local-runtime/ref2va-nine-acceptance`.

FlClash occupied the previous ports after service reload. The user explicitly selected preserving FlClash and moving the affected services. Studio now uses 18290 and gateway 18291; canvas remains 3000. Launchers, CLI defaults and the production proxy were updated and all three local endpoints verified. No firewall rule was changed; LAN reachability of the new studio port remains unverified.
