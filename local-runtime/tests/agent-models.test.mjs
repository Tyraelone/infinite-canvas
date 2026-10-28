import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import vm from "node:vm";
import ts from "../../web/node_modules/typescript/lib/typescript.js";
import * as local from "../../web/src/lib/local-runtime.ts";

const catalog = [
    { model: "sol", displayName: "Sol", isDefault: true, defaultReasoningEffort: "low", supportedReasoningEfforts: [{ reasoningEffort: "low" }, { reasoningEffort: "xhigh" }] },
    { model: "luna", displayName: "Luna", isDefault: false, defaultReasoningEffort: "medium", supportedReasoningEfforts: [{ reasoningEffort: "low" }, { reasoningEffort: "medium" }] },
];

test("local model choices use the actual default and validate saved effort against the chosen model", () => {
    assert.equal(typeof local.resolveLocalCodexSelection, "function");
    const defaults = local.resolveLocalCodexSelection(catalog);
    assert.equal(defaults.model.model, "sol");
    assert.equal(defaults.effort, "low");
    const saved = local.resolveLocalCodexSelection(catalog, "sol", "xhigh");
    assert.equal(saved.effort, "xhigh");
    assert.equal(saved.error, "");
    assert.match(local.resolveLocalCodexSelection(catalog, "luna", "xhigh").error, /不支持/);
    assert.match(local.resolveLocalCodexSelection(catalog, "missing", "low").error, /不可用/);
});

const source = fs.readFileSync(new URL("../../web/src/services/api/canvas-agent.ts", import.meta.url), "utf8");
const tree = ts.createSourceFile("api.ts", source, ts.ScriptTarget.Latest, true);
const reasoning = tree.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "applyCanvasAgentReasoning").getText(tree);
const scope = { channelProtocolForConfig: () => "openai" };
vm.runInNewContext(ts.transpileModule(reasoning, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, scope);

test("explicit selected reasoning effort reaches Chat and Responses without the old high override", () => {
    const chat = {}, responses = {};
    scope.applyCanvasAgentReasoning(chat, { textReasoningEffort: "xhigh" }, "chat");
    scope.applyCanvasAgentReasoning(responses, { textReasoningEffort: "low" }, "responses");
    assert.equal(chat.reasoning_effort, "xhigh");
    assert.equal(responses.reasoning.effort, "low");
});

test("remote channel reasoning switches keep their prior behavior", () => {
    const enabled = {}, disabled = {};
    scope.applyCanvasAgentReasoning(enabled, { textReasoningEnabled: true }, "chat");
    scope.applyCanvasAgentReasoning(disabled, { textReasoningEnabled: false }, "chat");
    assert.equal(enabled.reasoning_effort, "high");
    assert.equal(disabled.reasoning_effort, undefined);
});

const panel = fs.readFileSync(new URL("../../web/src/app/(user)/canvas/components/canvas-assistant-panel.tsx", import.meta.url), "utf8");
const panelTree = ts.createSourceFile("panel.tsx", panel, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let requestDeclaration;
let initialRequestEffect;
function walk(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(panelTree) === "requestConfig") requestDeclaration = node.getText(panelTree);
    if (ts.isCallExpression(node) && node.expression.getText(panelTree) === "useEffect" && node.arguments[0]?.getText(panelTree).includes("consumedInitialRequestRef.current === initialRequest")) initialRequestEffect = node.arguments[0].getText(panelTree);
    ts.forEachChild(node, walk);
}
walk(panelTree);

test("creative Agent sends the chosen local model and effort rather than the codex-local alias", () => {
    const state = {
        effectiveConfig: { textModel: "codex-local", model: "codex-local", textChannelId: "this-machine", localChannels: [] },
        agentConfig: { textApiMode: "chat" }, isLocalCodex: true,
        codexModel: catalog[0], codexEffort: "xhigh", LOCAL_CHANNEL_ID: "this-machine",
    };
    vm.runInNewContext(ts.transpileModule("globalThis.result = (() => { const " + requestDeclaration + "; return requestConfig; })();", { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, state);
    assert.equal(state.result.model, "sol");
    assert.equal(state.result.textModel, "sol");
    assert.equal(state.result.textReasoningEffort, "xhigh");
});

test("initial creative request stays pending while the local catalog loads and dispatches once ready", () => {
    const sent = [], consumed = [];
    const state = {
        initialRequest: { prompt: "制作分镜", references: [], skills: [] },
        consumedInitialRequestRef: { current: null }, isLocalCodex: true,
        localModelsLoading: true, localModelsError: "", codexSelection: { error: "暂无模型" },
        onInitialRequestConsumed: () => consumed.push(true), sendMessage: (...args) => sent.push(args),
    };
    const effect = ts.transpileModule("(" + initialRequestEffect + ")();", { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
    vm.runInNewContext(effect, state);
    assert.equal(sent.length, 0);
    assert.equal(consumed.length, 0);
    state.localModelsLoading = false;
    state.codexSelection.error = "";
    vm.runInNewContext(effect, state);
    vm.runInNewContext(effect, state);
    assert.equal(sent.length, 1);
    assert.equal(consumed.length, 1);
});

test("local model selection stays on the local channel when another channel lists the same model", () => {
    const state = {
        effectiveConfig: { textModel: "codex-local", model: "codex-local", textChannelId: "this-machine", localChannels: [
            { id: "this-machine", models: ["codex-local"], baseUrl: "http://127.0.0.1:3000/api/local-runtime/v1" },
            { id: "custom", models: ["sol"], baseUrl: "https://other-provider.invalid" },
        ] },
        agentConfig: { textApiMode: "chat" }, isLocalCodex: true, LOCAL_CHANNEL_ID: "this-machine",
        codexModel: catalog[0], codexEffort: "xhigh",
        normalizeLocalChannels: (config) => config.localChannels,
        isWorkflowProtocol: () => false,
        channelIdForActiveModel: (config) => config.textChannelId,
    };
    const storeSource = fs.readFileSync(new URL("../../web/src/stores/use-config-store.ts", import.meta.url), "utf8");
    const storeTree = ts.createSourceFile("store.ts", storeSource, ts.ScriptTarget.Latest, true);
    const route = storeTree.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "localChannelForActiveModel").getText(storeTree).replace("export ", "");
    vm.runInNewContext(ts.transpileModule(route + "\n const " + requestDeclaration + "; globalThis.route = localChannelForActiveModel(requestConfig);", { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, state);
    assert.equal(state.route.id, "this-machine");
});
