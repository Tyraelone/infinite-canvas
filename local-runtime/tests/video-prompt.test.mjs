import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import vm from "node:vm";
import ts from "../../web/node_modules/typescript/lib/typescript.js";

const root = "web/src/app/(user)/canvas/";
function sourceTree(path) {
    const source = fs.readFileSync(new URL("../../" + path, import.meta.url), "utf8");
    return ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}
const page = sourceTree(root + "[id]/canvas-client-page.tsx");
function callback(name) {
    let result;
    function visit(node) {
        if (ts.isVariableDeclaration(node) && node.name.getText(page) === name) result = node.initializer.arguments[0].getText(page);
        ts.forEachChild(node, visit);
    }
    visit(page);
    assert.ok(result, name);
    return result;
}
function declarations(path) {
    const tree = sourceTree(path);
    return tree.statements.filter((node) => !ts.isImportDeclaration(node)).map((node) => node.getText(tree)).join("\n");
}

const oldScript = "不应重新追加的完整分镜。".repeat(500);
const editedPrompt = "<Picture 1> 中的猫轻轻转头，固定镜头，五秒。";
const image = { id: "image", type: "image", title: "角色", metadata: { content: "data:image/png;base64,eA==" } };
const script = { id: "script", type: "text", title: "完整分镜", metadata: { content: oldScript } };
const video = { id: "video", type: "video", title: "试片", width: 320, height: 180, metadata: { prompt: "旧描述", status: "error", model: "local-h3-quality", mode: "ref2va" } };
const link = (fromNodeId, toNodeId) => ({ id: fromNodeId + "-" + toNodeId, fromNodeId, toNodeId });

function harness(nodes, connections) {
    const requests = [], notices = [];
    const state = {
        exports: {},
        CanvasNodeType: Object.fromEntries(["Image", "Panorama", "Video", "Audio", "Text", "Config"].map((name) => [name, name.toLowerCase()])),
        isCanvasImageNodeType: (type) => type === "image" || type === "panorama",
        isPanoramaNodeType: (type) => type === "panorama",
        imageReferenceLabel: (index) => "图片" + (index + 1), seedanceReferenceLabel: (type, index) => type + (index + 1),
        nodesRef: { current: structuredClone(nodes) }, connectionsRef: { current: connections },
        effectiveConfig: { model: "local-h3-quality", videoModel: "local-h3-quality" },
        buildGenerationConfig: (config, node) => ({ ...config, model: node?.metadata?.model || config.model }),
        isAiConfigReady: () => true, isLocalH3Model: (model) => model?.startsWith("local-h3-"),
        isAutoDLConfig: () => false, applyCameraPrompt: (prompt) => prompt,
        runningRequestRef: {}, setRunningNodeId: () => {}, nanoid: () => "retry-test",
        NODE_STATUS_LOADING: "loading", NODE_STATUS_ERROR: "error",
        message: { warning: (text) => notices.push(text), error: (text) => notices.push(text) },
        withCanvasVideoAdvancedConfig: (config) => config,
        channelProtocolForConfig: () => "openai", supportsVideoFrameReferences: () => true,
        isNodeTaskActive: () => true,
        submitNodeTask: async (_node, _task, submit) => submit(undefined, () => {}),
        createVideoGenerationTask: async (_config, prompt, references) => {
            requests.push({ prompt, references });
            return { task: { status: "queued" } };
        },
        applyCanvasVideoTaskUpdate: (current) => current,
    };
    state.setNodes = (update) => { state.nodesRef.current = update(state.nodesRef.current); };
    vm.createContext(state);
    const helpers = ["findRetrySourceNode", "sourceNodeReferenceImages"].map((name) => page.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === name).getText(page)).join("\n");
    const code = declarations(root + "utils/canvas-resource-references.ts") + "\n"
        + declarations(root + "components/canvas-node-generation.ts") + "\n" + helpers
        + "\nconst editPrompt = " + callback("handleNodePromptChange")
        + ";\nconst retryVideo = " + callback("handleRetryNode") + ";";
    vm.runInContext(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, state);
    // Hydration only loads media bytes; these fixtures already contain data URLs.
    vm.runInContext("hydrateNodeGenerationContext = async (context) => context;", state);
    return {
        state, requests, notices,
        edit: (prompt) => { state.nextPrompt = prompt; vm.runInContext('editPrompt("video", nextPrompt)', state); },
        context: () => vm.runInContext('buildNodeGenerationContext("video", nodesRef.current, connectionsRef.current, nodesRef.current.find(node => node.id === "video").metadata.prompt)', state),
        retry: async (snapshot = state.nodesRef.current.find((node) => node.id === "video")) => { state.snapshot = snapshot; await vm.runInContext("retryVideo(snapshot)", state); },
    };
}

test("editing a video prompt excludes connected full-story text while retaining the image reference", () => {
    const h = harness([script, image, video], [link("script", "video"), link("image", "video")]);
    h.edit(editedPrompt);
    const context = h.context();
    assert.equal(context.prompt, editedPrompt);
    assert.equal(context.referenceImages[0].id, "image");
});

test("retry uses the latest edited video text even when called with the old rendered node", async () => {
    const h = harness([script, image, video], [link("script", "video"), link("image", "video")]);
    h.edit(editedPrompt);
    await h.retry(video);
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].prompt, editedPrompt);
    assert.equal(h.requests[0].references.references[0].id, "image");
});

test("retry uses the edited child prompt and retains references collected from its source config", async () => {
    const config = { id: "config", type: "config", metadata: { prompt: "旧配置描述", model: "local-h3-quality" } };
    const h = harness([script, image, config, video], [link("script", "config"), link("image", "config"), link("config", "video")]);
    h.edit(editedPrompt);
    await h.retry();
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].prompt, editedPrompt);
    assert.equal(h.requests[0].references.references[0].id, "image");
});

test("clearing a video prompt does not restore the old script on retry", async () => {
    const h = harness([script, video], [link("script", "video")]);
    h.edit("");
    await h.retry();
    assert.equal(h.requests.length, 0);
    assert.equal(h.notices.length, 1);
});

test("unchanged text-generation inputs still append their connected context", () => {
    const h = harness([script, { ...video, type: "text" }], [link("script", "video")]);
    assert.equal(h.context().prompt, "旧描述\n\n" + oldScript);
});

test("a generated video's saved prompt is not expanded again on its next generation", () => {
    let metadata;
    function visit(node) {
        if (ts.isVariableDeclaration(node) && node.name.getText(page) === "videoNode") {
            metadata = node.initializer.properties.find((property) => property.name?.getText(page) === "metadata").initializer.getText(page);
        }
        ts.forEachChild(node, visit);
    }
    visit(page);
    const h = harness([script, image, video], [link("script", "video"), link("image", "video")]);
    Object.assign(h.state, {
        effectivePrompt: editedPrompt, sourceNode: video,
        videoGenerationConfig: { model: "local-h3-quality", videoMode: "ref2va" },
        workflowRef: undefined, videoReferenceImages: [], firstFrame: null, lastFrame: null,
        generationContext: {}, generationReferenceUrls: () => [], generationStartedAt: 1, clientTaskId: "test",
    });
    vm.runInContext(ts.transpileModule("nodesRef.current.find(node => node.id === 'video').metadata = " + metadata, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, h.state);
    assert.equal(h.context().prompt, editedPrompt);
    assert.equal(h.context().referenceImages[0].id, "image");
});
