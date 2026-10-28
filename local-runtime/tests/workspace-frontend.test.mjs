import assert from "node:assert/strict";
import test from "node:test";
import { sharedStateSlot, workspaceFileUrl, rewriteWorkspaceMedia, collectWorkspaceReferences, emptyLegacyWorkspace } from "../../web/src/lib/local-workspace.ts";

test("a fresh viewer skips empty legacy import without discarding real or invalid legacy data", () => {
    assert.equal(emptyLegacyWorkspace({ canvas: null, assets: null }), true);
    assert.equal(emptyLegacyWorkspace({ canvas: '{"state":{"projects":[]}}', assets: '{"state":{"assets":[]}}' }), true);
    assert.equal(emptyLegacyWorkspace({ canvas: '{"state":{"projects":[{"id":"empty-project","nodes":[]}]}}' }), false);
    assert.equal(emptyLegacyWorkspace({ canvas: "broken" }), false);
});

test("only canvas and asset business state is shared, API keys and login config stay private", () => {
    assert.equal(sharedStateSlot("infinite-canvas:canvas_store"), "canvas");
    assert.equal(sharedStateSlot("infinite-canvas:asset_store"), "assets");
    assert.equal(sharedStateSlot("infinite-canvas:ai_config_store"), undefined);
    assert.equal(sharedStateSlot("infinite-canvas:user_store"), undefined);
});

test("file URLs keep storage keys without embedding browser origin", () => {
    assert.equal(workspaceFileUrl("image:abc_123"), "/api/local-runtime/workspace/files/image%3Aabc_123");
    assert.equal(workspaceFileUrl("video:a/b"), "/api/local-runtime/workspace/files/video%3Aa%2Fb");
});

test("migration replaces expired Blob URLs using storage keys and preserves IDs and links", () => {
    const source = { id: "canvas-one", nodes: [{ id: "node-one", metadata: { storageKey: "image:photo", content: "blob:expired-image" } },
        { id: "node-two", metadata: { storageKey: "file:clip", content: "blob:expired-video", title: "same title" } }],
        connections: [{ fromNodeId: "node-one", toNodeId: "node-two" }] };
    const result = rewriteWorkspaceMedia(source, new Set(["image:photo", "file:clip"]));
    assert.equal(result.id, "canvas-one");
    assert.equal(result.nodes[0].metadata.content, "/api/local-runtime/workspace/files/image%3Aphoto");
    assert.equal(result.nodes[1].metadata.content, "/api/local-runtime/workspace/files/file%3Aclip");
    assert.deepEqual(result.connections, [{ fromNodeId: "node-one", toNodeId: "node-two" }]);
    assert.equal(source.nodes[0].metadata.content, "blob:expired-image");
});

test("asset covers share their media mapping and inline attachments use uploaded replacements", () => {
    const source = { assets: [{ id: "asset-one", coverUrl: "blob:image", data: { storageKey: "image:p", dataUrl: "blob:image" } }],
        chat: [{ content: "data:image/png;base64,AAA=" }] };
    const result = rewriteWorkspaceMedia(source, new Set(["image:p"]), new Map([["data:image/png;base64,AAA=", "/api/local-runtime/workspace/files/inline%3Ax"]]));
    assert.equal(result.assets[0].coverUrl, "/api/local-runtime/workspace/files/image%3Ap");
    assert.equal(result.chat[0].content, "/api/local-runtime/workspace/files/inline%3Ax");
});

test("unresolved media stops migration instead of silently publishing broken nodes", () => {
    assert.throws(() => rewriteWorkspaceMedia({ metadata: { storageKey: "image:missing", content: "blob:gone" } }, new Set()), /未迁移|缺少/);
    assert.throws(() => rewriteWorkspaceMedia({ dataUrl: "data:image/png;base64,AAAA" }, new Set()), /未迁移/);
});

test("reference collection distinguishes media, ordinary text and reusable stable URLs", () => {
    const result = collectWorkspaceReferences({ metadata: { storageKey: "image:p", content: "blob:image" },
        items: [{ dataUrl: "data:image/png;base64,AAA=" }], text: "normal text", url: "/api/local-runtime/media/video/abc" });
    assert.deepEqual([...result.keys], ["image:p"]);
    assert.equal(result.urls.has("blob:image"), true);
    assert.equal(result.urls.has("data:image/png;base64,AAA="), true);
    assert.equal(result.urls.has("normal text"), false);
    assert.equal(result.urls.has("/api/local-runtime/media/video/abc"), true);
});

test("owned runtime media is adopted into shared disk while external links stay unchanged", () => {
    const original = { metadata: { content: "/api/local-runtime/media/video/owned" }, external: "https://example.com/clip.mp4" };
    const result = rewriteWorkspaceMedia(original, new Set(["inline:clip"]), new Map([[original.metadata.content, workspaceFileUrl("inline:clip")]]));
    assert.equal(result.metadata.content, workspaceFileUrl("inline:clip"));
    assert.equal(result.external, original.external);
});
