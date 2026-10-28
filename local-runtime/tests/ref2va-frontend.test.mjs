import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import vm from "node:vm";
import ts from "../../web/node_modules/typescript/lib/typescript.js";
import { isLocalH3Model, localH3VideoOptions } from "../../web/src/lib/local-runtime.ts";

function sourceTree(path) {
    const source = fs.readFileSync(new URL("../../" + path, import.meta.url), "utf8");
    return ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}
const tree = sourceTree("web/src/services/api/video.ts");
const requestFunction = tree.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "createVideoRequestBody").getText(tree);
const scope = { isLocalH3Model, localH3VideoOptions, LOCAL_H3_MAX_REFERENCES: 9, VideoRequestError: Error, imageToDataUrl: async (image) => image.dataUrl };
vm.runInNewContext(ts.transpileModule(requestFunction, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, scope);
const config = { model: "local-h3-quality", videoMode: "ref2va", vquality: "480", size: "16:9", videoSeconds: "5", videoGenerateAudio: "true", videoUpscale: "false" };
const images = [{ id: "character", dataUrl: "character-data" }, { id: "robot", dataUrl: "robot-data" }, { id: "scene", dataUrl: "scene-data" }];
const input = (references, extra = {}) => ({ references, firstFrame: null, lastFrame: null, videoReferences: [], audioReferences: [], ...extra });
const prompt = "<Picture 1> runs with <Picture 2> in <Picture 3>.";

test("Ref2VA forwards all three ordered references without treating any as a first frame", async () => {
    const body = await scope.createVideoRequestBody(config, config.model, prompt, input(images));
    assert.equal(body.reference_mode, "ref2va");
    assert.deepEqual(Array.from(body.input_reference), images.map((i) => i.dataUrl));
    assert.equal(body.first_frame_url, undefined);
    assert.equal(body.prompt, prompt);
});

test("quality multi-image requests infer native mode and canvas image labels become Picture tags", async () => {
    const body = await scope.createVideoRequestBody({ ...config, videoMode: "normal" }, config.model, "图片1 和 图片2 在 图片3 中奔跑", input(images));
    assert.equal(body.reference_mode, "ref2va");
    for (const n of [1, 2, 3]) assert.ok(body.prompt.includes("<Picture " + n + ">"));
});

test("one reference can explicitly use Ref2VA", async () => {
    const body = await scope.createVideoRequestBody(config, config.model, "Keep <Picture 1>.", input(images.slice(0, 1)));
    assert.equal(body.reference_mode, "ref2va");
    assert.deepEqual(Array.from(body.input_reference), ["character-data"]);
});

test("four through nine image references preserve order and all numbered labels", async () => {
    for (let count = 4; count <= 9; count++) {
        const refs = Array.from({ length: count }, (_, i) => ({ id: "ref-" + i, dataUrl: "image-data-" + i }));
        const text = refs.map((_, i) => "图片" + (i + 1)).join("，");
        const request = await scope.createVideoRequestBody(config, config.model, text, input(refs));
        assert.equal(request.reference_mode, "ref2va");
        assert.deepEqual(Array.from(request.input_reference), refs.map((i) => i.dataUrl));
        for (let i = 1; i <= count; i++) assert.ok(request.prompt.includes("<Picture " + i + ">"));
    }
});

test("ten images and a missing ninth label are rejected without silent truncation", async () => {
    const refs = Array.from({ length: 10 }, (_, i) => ({ id: "ref-" + i, dataUrl: "data-" + i }));
    await assert.rejects(scope.createVideoRequestBody(config, config.model,
        refs.map((_, i) => "<Picture " + (i + 1) + ">").join(" "), input(refs)), /9/);
    await assert.rejects(scope.createVideoRequestBody(config, config.model,
        refs.slice(0, 8).map((_, i) => "<Picture " + (i + 1) + ">").join(" "), input(refs.slice(0, 9))));
});

test("invalid native combinations are rejected before serializing images", async () => {
    for (const [settings, text, refs] of [
        [{ ...config, model: "local-h3-turbo" }, prompt, input(images)],
        [config, "missing labels", input(images)],
        [config, prompt, input([...images, images[0]])],
        [config, prompt, input(images, { firstFrame: images[0] })],
        [config, prompt, input(images, { lastFrame: images[0] })],
        [config, prompt, input(images, { videoReferences: [{}] })],
        [config, prompt, input(images, { audioReferences: [{}] })],
        [config, "only <Picture 2>", input(images.slice(0, 1))],
        [config, prompt, input([])],
    ]) await assert.rejects(scope.createVideoRequestBody(settings, settings.model, text, refs));
});

test("existing text and single first-frame requests retain their original shape", async () => {
    const settings = { ...config, videoMode: "normal", model: "local-h3-turbo" };
    const text = await scope.createVideoRequestBody(settings, settings.model, "landscape", input([]));
    assert.equal(text.reference_mode, undefined);
    const first = await scope.createVideoRequestBody(settings, settings.model, "move", input([], { firstFrame: images[0] }));
    assert.equal(first.first_frame_url, "character-data");
    assert.equal(first.input_reference, undefined);
});

const toolsTree = sourceTree("web/src/app/(user)/canvas/agent/canvas-agent-tools.ts");
const toolsScope = { exports: {}, nanoid: () => "test-call", normalizeCompositionOptions: () => ({}) };
vm.runInNewContext(ts.transpileModule(toolsTree.statements.filter((node) => !ts.isImportDeclaration(node)).map((node) => node.getText(toolsTree)).join("\n"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, toolsScope);

test("Agent video tools accept explicit Ref2VA mode and reject invented modes", () => {
    const action = toolsScope.exports.normalizeCanvasAgentAction("generate_video", { prompt, sourceNodeIds: ["a", "b", "c"], referenceMode: "ref2va" });
    assert.equal(action.arguments.referenceMode, "ref2va");
    assert.deepEqual(Array.from(action.arguments.sourceNodeIds), ["a", "b", "c"]);
    assert.throws(() => toolsScope.exports.normalizeCanvasAgentAction("generate_video", { prompt, sourceNodeIds: ["a"], referenceMode: "invented" }));
});

test("manual and retry canvas paths retain bound-frame roles for Ref2VA validation", () => {
    const canvasTree = sourceTree("web/src/app/(user)/canvas/[id]/canvas-client-page.tsx");
    const blocks = [];
    function visit(node) {
        if (ts.isVariableDeclaration(node) && node.name.getText(canvasTree) === "frameReferencesEnabled") {
            const statement = node.parent.parent;
            if (ts.isVariableStatement(statement) && ts.isBlock(statement.parent)) {
                const index = statement.parent.statements.indexOf(statement);
                blocks.push(statement.parent.statements.slice(index, index + 4).map((item) => item.getText(canvasTree)).join("\n"));
            }
        }
        ts.forEachChild(node, visit);
    }
    visit(canvasTree);
    assert.equal(blocks.length, 2);
    for (const block of blocks) {
        const state = {
            isLocalH3Model, supportsVideoFrameReferences: () => false, isAutoDLConfig: () => false,
            channelProtocolForConfig: () => "openai", workflowRef: "", retryWorkflowRef: "",
            videoGenerationConfig: { model: "local-h3-quality", videoMode: "ref2va" },
            generationContext: { firstFrame: images[0], lastFrame: images[1], referenceImages: [images[2]] },
            context: { firstFrame: images[0], lastFrame: images[1] }, retryImages: [images[2]],
        };
        vm.runInNewContext(ts.transpileModule(block + "\nglobalThis.result = { firstFrame, lastFrame };", {
            compilerOptions: { target: ts.ScriptTarget.ES2022 },
        }).outputText, state);
        assert.equal(state.result.firstFrame?.id, "character");
        assert.equal(state.result.lastFrame?.id, "robot");
    }
});

test("a native video's retry retains its saved model and mode when the upstream config changes", async () => {
    const canvasTree = sourceTree("web/src/app/(user)/canvas/[id]/canvas-client-page.tsx");
    const declarations = [];
    function visit(node) {
        if (ts.isVariableDeclaration(node) && (node.name.getText(canvasTree) === "retryConfigNode"
            || (node.name.getText(canvasTree) === "generationConfig" && node.initializer?.getText(canvasTree).includes("hasSavedImageMetadata")))) declarations.push("const " + node.getText(canvasTree) + ";");
        ts.forEachChild(node, visit);
    }
    visit(canvasTree);
    const helper = canvasTree.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "buildGenerationConfig").getText(canvasTree);
    const state = {
        isLocalH3Model, isPanoramaNodeType: () => false, defaultConfig: {},
        CanvasNodeType: { Video: "video", Text: "text", Audio: "audio" },
        node: { type: "video", metadata: { model: "local-h3-quality", mode: "ref2va", channelId: "this-machine" } },
        sourceNode: { type: "config", metadata: { model: "local-h3-turbo", mode: "auto" } },
        effectiveConfig: { ...config, videoSize: "16:9", model: "local-h3-turbo", videoModel: "local-h3-turbo", videoMode: "auto" },
        hasSavedImageMetadata: false, savedImageMetadata: undefined,
    };
    vm.runInNewContext(ts.transpileModule(helper + "\n" + declarations.join("\n") + "\nglobalThis.result = generationConfig;", {
        compilerOptions: { target: ts.ScriptTarget.ES2022 },
    }).outputText, state);
    assert.equal(state.result.model, "local-h3-quality");
    assert.equal(state.result.videoMode, "ref2va");
    const request = await scope.createVideoRequestBody(state.result, state.result.model, "图片1 waves", input(images.slice(0, 1)));
    assert.equal(request.reference_mode, "ref2va");
    assert.equal(request.first_frame_url, undefined);
});

test("an inherited Agent Ref2VA setting does not block another model, but an explicit request is rejected", () => {
    const canvasTree = sourceTree("web/src/app/(user)/canvas/[id]/canvas-client-page.tsx");
    let block;
    function visit(node) {
        if (ts.isIfStatement(node) && node.expression.getText(canvasTree) === 'mode === "video"'
            && node.thenStatement.getText(canvasTree).includes("metadata.vquality = generationConfig.vquality")) block = node.getText(canvasTree);
        ts.forEachChild(node, visit);
    }
    visit(canvasTree);
    for (const requestedMode of [undefined, "ref2va"]) {
        const state = {
            mode: "video", metadata: {}, args: requestedMode ? { referenceMode: requestedMode } : {},
            generationConfig: { model: "seedance-2", videoMode: "ref2va", videoSeconds: "5" },
            agentWorkflowRef: "", validateCanvasAgentVideoSeconds: () => "",
            supportsVideoAudioGeneration: () => true,
            stringValue: (name) => name === "referenceMode" ? requestedMode || "" : "",
        };
        vm.runInNewContext(ts.transpileModule("globalThis.result = (() => {" + block + ";return metadata;})();", {
            compilerOptions: { target: ts.ScriptTarget.ES2022 },
        }).outputText, state);
        if (requestedMode) assert.equal(state.result.code, "ref2va_requires_quality");
        else {
            assert.equal(state.result.code, undefined);
            assert.notEqual(state.result.mode, "ref2va");
        }
    }
});
