import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import vm from "node:vm";
import ts from "../../web/node_modules/typescript/lib/typescript.js";

const file = fs.readFileSync(new URL("../../web/src/app/(user)/canvas/agent/canvas-agent-runtime.ts", import.meta.url), "utf8");
const tree = ts.createSourceFile("runtime.ts", file, ts.ScriptTarget.Latest, true);
const code = ts.transpileModule(tree.statements.filter((node) => !ts.isImportDeclaration(node)).map((node) => node.getText(tree)).join("\n"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

function harness(respond, execute = async () => ({ ok: true, node: { id: "script", text: "完整分镜正文".repeat(1000) } })) {
    const requests = [], actions = [], exports = {};
    const scope = {
        exports, AbortController,
        CANVAS_AGENT_JSON_FALLBACK_SIGNAL: "__FALLBACK__",
        CANVAS_AGENT_TOOLS: [{ function: { name: "get_node" } }],
        buildCanvasAgentSkillPrompt: () => "当前真实画布",
        canvasAgentSystemPrompt: (_, prompt) => prompt,
        canvasAgentTokenCalibrationKey: () => "test",
        estimateCanvasAgentInputTokens: () => 0,
        MAX_AGENT_INPUT_TOKENS: 100000,
        parseCanvasAgentJson: async () => ({ parsed: false, actions: [], reply: "" }),
        normalizeCanvasAgentAction: (name, args, id) => ({ name, arguments: args, id }),
        canvasAgentActionLabel: (action) => action.name,
        isCanvasAgentMediaAction: () => false,
        userLikelyRequestedCanvasAction: () => true,
        requestCanvasAgentTurn: async (input) => {
            requests.push(input);
            return { content: "", toolCalls: [], toolMode: "native", ...await respond(input, requests.length) };
        },
    };
    vm.runInNewContext(code, scope);
    const input = {
        config: { model: "codex-local", textModel: "codex-local" },
        initialState: exports.createCanvasAgentState(), protocolMessages: [], userText: "继续修订分镜",
        references: [], getContext: () => ({}),
        executeAction: async (action) => { actions.push(action); return execute(action); },
    };
    return { requests, actions, input, run: () => exports.runCanvasAgent(input) };
}
const call = (name, args = { nodeId: "script" }) => ({ toolCalls: [{ id: Math.random().toString(), name, arguments: args }] });

test("unchanged reads receive correction and can proceed to the requested write", async () => {
    let wrote = false;
    const h = harness((input) => wrote ? { content: "分镜已修订" }
        : input.systemPrompt.includes("重复读取") ? call("update_text_node", { nodeId: "script", content: "修订后的完整分镜" }) : call("get_node"),
    async (action) => {
        if (action.name === "update_text_node") wrote = true;
        return { ok: true, node: { id: "script", text: "完整分镜正文".repeat(1000) } };
    });
    const result = await h.run();
    assert.equal(wrote, true);
    assert.equal(result.reply, "分镜已修订");
    assert.ok(h.actions.filter((a) => a.name === "get_node").length <= 3);
    assert.ok(h.requests.some((r) => r.messages.some((m) => m.role === "tool" && m.content.includes("完整分镜正文"))));
});

test("a stubborn unchanged read loop finalizes without executing unwanted final tool calls", async () => {
    const h = harness(() => call("get_node"));
    const result = await h.run();
    assert.ok(h.actions.length <= 3, "unchanged reads must stop before consuming the entire budget");
    assert.ok(h.requests.at(-1).tools.length === 0, "final summary cannot execute tools");
    assert.match(result.reply, /重复读取/);
    assert.doesNotMatch(result.reply, /你可以让我继续下一步/);
    assert.equal(result.protocolMessages.filter((m) => m.role === "tool").length, h.actions.length);
});

test("useful work can exceed twelve rounds without the old safety-limit message", async () => {
    const h = harness((_, count) => count <= 20 ? call("create_text_node", { title: "镜头" + count, content: "正文" }) : { content: "20 个镜头完成" },
        async () => ({ ok: true, nodeId: Math.random().toString() }));
    const result = await h.run();
    assert.equal(h.actions.length, 20);
    assert.equal(result.reply, "20 个镜头完成");
});

test("changing task progress is not treated as an unchanged read loop", async () => {
    let progress = 0;
    const h = harness((_, count) => count <= 15 ? call("get_media_task_status") : { content: "任务已完成" },
        async () => ({ ok: true, status: ++progress === 15 ? "success" : "loading", progress }));
    assert.equal((await h.run()).reply, "任务已完成");
    assert.equal(h.actions.length, 15);
});

test("a successful write permits a fresh read of the same node", async () => {
    const names = ["get_node", "get_node", "update_text_node", "get_node"];
    const h = harness((_, count) => count <= names.length ? call(names[count - 1]) : { content: "完成" });
    assert.equal((await h.run()).reply, "完成");
    assert.equal(h.actions.length, 4);
});

test("cancellation still prevents the next model request", async () => {
    const controller = new AbortController();
    const h = harness(() => call("get_node"), async () => { controller.abort(); return { ok: true }; });
    h.input.signal = controller.signal;
    await assert.rejects(h.run(), /停止/);
    assert.equal(h.requests.length, 1);
});

test("the overall budget requests a final summary without more writes", async () => {
    const h = harness((input, count) => input.tools.length ? call("create_text_node", { title: "镜头" + count, content: "正文" }) : { content: "已保存阶段成果，请确认下一阶段。" },
        async () => ({ ok: true }));
    const result = await h.run();
    assert.equal(h.actions.length, 48);
    assert.equal(result.reply, "已保存阶段成果，请确认下一阶段。");
});
