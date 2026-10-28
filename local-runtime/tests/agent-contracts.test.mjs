import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import vm from "node:vm";
import ts from "../../web/node_modules/typescript/lib/typescript.js";
import * as local from "../../web/src/lib/local-runtime.ts";
import { modelKey } from "../../web/src/lib/video-model-capabilities.ts";

const source = fs.readFileSync(new URL("../../web/src/app/(user)/canvas/[id]/canvas-client-page.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("canvas.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = ["canvasAgentNodeSummary", "canvasAgentVideoDurationHint", "validateCanvasAgentVideoSeconds"];
const functions = tree.statements.filter((node) => ts.isFunctionDeclaration(node) && names.includes(node.name?.text)).map((node) => node.getText(tree)).join("\n");
const scope = { ...local, modelKey, CanvasNodeType: { Text: "text" }, canvasRecoverableTaskId: () => "", isCogVideoX3Model: () => false };
vm.createContext(scope);
vm.runInContext(ts.transpileModule(functions, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, scope);

test("explicit Agent node reads retain the full twelve-shot script", () => {
    const text = "完整镜头".repeat(2500);
    const node = { id: "script", type: "text", metadata: { content: text } };
    assert.equal(scope.canvasAgentNodeSummary(node).text.length, 4000);
    assert.equal(scope.canvasAgentNodeSummary(node, true).text.length, text.length);
});

test("Agent duration hints and validation agree with the local H3 service", () => {
    assert.deepEqual(Array.from(scope.canvasAgentVideoDurationHint("local-h3-turbo").values), [5, 10, 15]);
    assert.equal(scope.validateCanvasAgentVideoSeconds("local-h3-turbo", 15), "");
    assert.notEqual(scope.validateCanvasAgentVideoSeconds("local-h3-turbo", 16), "");
});
