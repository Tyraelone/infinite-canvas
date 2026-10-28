import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import vm from "node:vm";
import ts from "../../web/node_modules/typescript/lib/typescript.js";

const source = fs.readFileSync("D:/AI/MiniMax-H3/studio/static/app.js", "utf8");
const tree = ts.createSourceFile("studio.js", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const names = new Set(["referenceLimit", "refreshUploads", "acceptFiles", "payload"]);
const code = tree.statements.filter((node) => ts.isFunctionDeclaration(node) && names.has(node.name?.text)).map((node) => node.getText(tree)).join("\n");
function harness(mode = "native") {
    const elements = {};
    const uploaded = [], notices = [];
    const state = { mode, first: null, last: null, references: [], capabilities: { native_reference_max_images: 9 } };
    const scope = {
        state, $: (id) => elements[id] ||= { textContent: "", hidden: false, value: "", replaceChildren() {} },
        uploadPreview: () => {}, updateButtons: () => {}, toast: (...args) => notices.push(args),
        uploadFile: async (file) => { uploaded.push(file.name); return { id: file.name, url: "local-test" }; },
    };
    vm.runInNewContext(code, scope);
    return { scope, state, elements, uploaded, notices };
}

test("studio Ref2VA count and drop-zone use nine while Qwen uses three", () => {
    for (const [mode, expected] of [["native", "0 / 9"], ["reference", "0 / 3"]]) {
        const h = harness(mode);
        h.scope.refreshUploads();
        assert.equal(h.elements.referenceCount.textContent, expected);
    }
});

test("studio accepts all nine selected native images in input order", async () => {
    const h = harness();
    h.scope.refreshUploads = () => {};
    const files = Array.from({ length: 9 }, (_, i) => ({ name: "ref-" + i }));
    await h.scope.acceptFiles(files, "references");
    assert.deepEqual(h.uploaded, files.map((file) => file.name));
    assert.deepEqual(Array.from(h.state.references, (ref) => ref.id), files.map((file) => file.name));
});

test("studio rejects an oversized native selection without silently using only a prefix", async () => {
    const h = harness();
    h.scope.refreshUploads = () => {};
    await h.scope.acceptFiles(Array.from({ length: 10 }, (_, i) => ({ name: "ref-" + i })), "references");
    assert.equal(h.uploaded.length, 0);
    assert.equal(h.state.references.length, 0);
    assert.ok(h.notices.some(([message]) => message.includes("9")));
});

test("switching nine native references to Qwen preserves them but blocks invalid submission", () => {
    const h = harness("reference");
    h.state.references = Array.from({ length: 9 }, (_, i) => ({ id: "ref-" + i }));
    h.scope.$("prompt").value = "Test";
    assert.throws(() => h.scope.payload(), /3/);
    assert.equal(h.state.references.length, 9);
});
