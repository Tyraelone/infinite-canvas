import assert from "node:assert/strict";
import test from "node:test";
import * as localRuntime from "../../web/src/lib/local-runtime.ts";
import { supportsVideoAudioGeneration } from "../../web/src/lib/video-model-capabilities.ts";
const { canUpscaleLocalH3Video, isLocalH3Model, localH3Size, localH3VideoOptions, mergeLocalChannel, shouldSelectLocalDefaults, stopLocalVideoAfterSubmission } = localRuntime;

test("the canvas Agent sees local H3 audio and the real 5/10/15 second contract", () => {
    for (const model of ["local-h3-turbo", "local-h3-quality"]) assert.equal(supportsVideoAudioGeneration(model), true);
    assert.deepEqual(localRuntime.LOCAL_H3_DURATIONS, [5, 10, 15]);
    assert.equal(localRuntime.localH3DurationError(15), "");
    assert.match(localRuntime.localH3DurationError(16), /5.*10.*15/);
    assert.notEqual(localRuntime.localH3DurationError(12), "");
});

test("local H3 converts selected resolution and ratio without changing aspect", () => {
    assert.equal(localH3Size("480", "16:9"), "864x480");
    assert.equal(localH3Size("720", "9:16"), "720x1280");
    assert.equal(localH3Size("768", "1:1"), "992x992");
    assert.equal(localH3Size("480", "864x480"), "864x480");
    assert.equal(localH3Size("720", "864x480"), "1280x720");
    assert.equal(localH3Size("480", "1728x960"), "864x480");
    assert.throws(() => localH3Size("4k", "16:9"));
    assert.throws(() => localH3Size("720", "21:9"));
    assert.equal(isLocalH3Model("MiniMax-H3"), false);
});

test("local H3 request options preserve duration and send real boolean upscale", () => {
    assert.equal(typeof localH3VideoOptions, "function");
    assert.deepEqual(localH3VideoOptions({ vquality: "720", size: "9:16", videoSeconds: "10", videoGenerateAudio: "false", videoUpscale: "true" }),
        { size: "720x1280", seconds: "10", video_generate_audio: false, upscale: true });
    assert.equal(localH3VideoOptions({ vquality: "480", size: "1:1", videoSeconds: "15", videoGenerateAudio: "true", videoUpscale: "false" }).upscale, false);
});

test("2x action accepts only completed local H3 originals with an owned task identifier", () => {
    assert.equal(typeof canUpscaleLocalH3Video, "function");
    const metadata = { model: "local-h3-turbo", status: "success", content: "/api/local-runtime/media/video/" + "a".repeat(32), videoTaskId: "a".repeat(32), upscale: "false" };
    assert.equal(canUpscaleLocalH3Video(metadata), true);
    for (const patch of [{ status: "loading" }, { model: "cloud-h3" }, { videoTaskId: "client_video_task_123" }, { content: "" }, { upscale: "true" }]) {
        assert.equal(canUpscaleLocalH3Video({ ...metadata, ...patch }), false);
    }
});

test("stopping during submission waits for actual remote task ID before cancelling", async () => {
    const pending = Promise.withResolvers();
    const cancelled = [];
    const stopping = stopLocalVideoAfterSubmission(pending.promise, async (id) => { cancelled.push(id); });
    assert.deepEqual(cancelled, []);
    pending.resolve({ id: "real-studio-job-id", status: "queued" });
    await stopping;
    assert.deepEqual(cancelled, ["real-studio-job-id"]);
});

test("stopping a restored video task uses its persisted real task ID", async () => {
    const cancelled = [];
    await stopLocalVideoAfterSubmission(undefined, async (id) => { cancelled.push(id); }, "a".repeat(32));
    assert.deepEqual(cancelled, ["a".repeat(32)]);
});

test("an empty default channel does not prevent local defaults, configured user channels do", () => {
    assert.equal(shouldSelectLocalDefaults([{ id: "local-default", models: [], apiKey: "" }], "gpt-image-2"), true);
    assert.equal(shouldSelectLocalDefaults([{ id: "custom", models: ["custom-image"], apiKey: "secret" }], "custom-image"), false);
    assert.equal(shouldSelectLocalDefaults([{ id: "this-machine", models: ["codex-image"], apiKey: "local" }], "codex-image"), false);
});

test("local profile refresh preserves other channels and replaces only its own channel", () => {
    const config = { localChannels: [{ id: "custom", name: "mine" }, { id: "this-machine", name: "old" }], imageModel: "custom-image" };
    const merged = mergeLocalChannel(config, { id: "this-machine", name: "new" });
    assert.deepEqual(merged.localChannels, [{ id: "custom", name: "mine" }, { id: "this-machine", name: "new" }]);
    assert.equal(merged.imageModel, "custom-image");
});
