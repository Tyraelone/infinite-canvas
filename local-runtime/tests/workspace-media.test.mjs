import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import vm from "node:vm";
import ts from "../../web/node_modules/typescript/lib/typescript.js";

test("concurrent completed-video saves reuse the owned task key", async () => {
    const source = ts.transpileModule(fs.readFileSync(new URL("../../web/src/services/file-storage.ts", import.meta.url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const keys = [];
    const module = { exports: {} };
    let id = 0;
    vm.runInNewContext(source, {
        module, exports: module.exports, Blob,
        require: (name) => name === "localforage" ? { default: { createInstance: () => ({}) } }
            : name === "nanoid" ? { nanoid: () => `upload-${++id}` }
            : name.endsWith("/local-workspace") ? {
                localWorkspaceEnabled: async () => true,
                saveWorkspaceBlob: async (key) => { keys.push(key); return `/files/${key}`; },
            } : {},
    });
    const blob = new Blob(["media"], { type: "audio/wav" });
    const taskKey = "video:local:" + "a".repeat(32);
    await Promise.all([module.exports.uploadMediaFile(blob, "generated-video", taskKey), module.exports.uploadMediaFile(blob, "generated-video", taskKey)]);
    assert.deepEqual(keys, [taskKey, taskKey]);
    await module.exports.uploadMediaFile(blob, "file", "untrusted-or-changing-sync-id");
    assert.equal(keys[2], "file:upload-1");
});
