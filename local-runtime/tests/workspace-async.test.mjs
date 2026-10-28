import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import vm from "node:vm";
import ts from "../../web/node_modules/typescript/lib/typescript.js";

const source = ts.transpileModule(fs.readFileSync(new URL("../../web/src/services/local-workspace.ts", import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const tick = () => new Promise((resolve) => setImmediate(resolve));
const reply = (data, status = 200) => new Response(JSON.stringify({ code: status === 200 ? 0 : status, data, msg: "test failure" }), { status });

function workspace(onPut, onGet, normalize = (value) => value, sessionStorage) {
    let heartbeat, cleared = 0;
    const leases = [];
    const values = new Map([["infinite-canvas:disk-workspace-migration-v1", { complete: true }]]);
    const storage = {
        getItem: async (key) => values.get(key) ?? null,
        setItem: async (key, value) => { values.set(key, value); },
        removeItem: async (key) => { values.delete(key); },
    };
    const status = {};
    const module = { exports: {} };
    const requests = [];
    const clients = [];
    vm.runInNewContext(source, {
        module, exports: module.exports, Blob, Headers, crypto,
        window: { sessionStorage, setInterval: (callback) => { heartbeat = callback; return 1; }, clearInterval: () => { heartbeat = undefined; cleared += 1; } },
        fetch: async (path, options = {}) => {
            if (options.headers?.get("X-Workspace-Client")) clients.push(options.headers.get("X-Workspace-Client"));
            if (path.endsWith("/profile")) return new Response(JSON.stringify({ enabled: true }));
            if (path.endsWith("/status")) return reply({ storageRoot: "D:/workspace" });
            if (path.endsWith("/lease")) { leases.push(JSON.parse(options.body)); return reply({ ttlSeconds: 45 }); }
            if (options.method === "PUT") { const body = JSON.parse(options.body); requests.push(body); return onPut(body, requests.length); }
            return onGet ? onGet() : reply({ value: '{"state":{"projects":[]}}', revision: 1 });
        },
        require: (name) => name === "localforage" ? { default: { createInstance: () => storage } }
            : name.includes("use-local-workspace-store") ? { useLocalWorkspaceStore: { setState: (value) => Object.assign(status, value) } }
            : { sharedStateSlot: () => "canvas", collectWorkspaceReferences: () => ({ keys: new Set(), urls: new Set(), byUrl: new Map() }), rewriteWorkspaceMedia: normalize },
    });
    return { api: module.exports, values, status, requests, leases, clients, heartbeat: () => heartbeat?.(), cleared: () => cleared };
}

test("reload retains this tab's workspace writer identity", async () => {
    const data=new Map();const session={getItem:key=>data.get(key),setItem:(key,value)=>data.set(key,value)};
    const first=workspace(()=>reply({revision:2}),undefined,undefined,session);await first.api.readWorkspaceState('canvas');
    const second=workspace(()=>reply({revision:2}),undefined,undefined,session);await second.api.readWorkspaceState('canvas');
    assert.equal(first.clients[0],second.clients[0]);
});
test("required persistence rejects failed disk writes and a lease conflict does not permanently block later saves", async()=>{
    const w=workspace((_body,count)=>count===1?reply({},423):reply({revision:2}));await w.api.readWorkspaceState('canvas');
    await assert.rejects(()=>w.api.writeWorkspaceState('canvas','{"state":{"projects":[]}}',true));
    await w.api.writeWorkspaceState('canvas','{"state":{"projects":[]}}',true);
    assert.equal(w.requests.length,2);assert.equal(w.status.phase,'ready');
});

test("Agent writer lease renews while submitted media runs and stops after completion", async () => {
    let revision = 1;
    const w = workspace(() => reply({ revision: ++revision }));
    await w.api.readWorkspaceState("canvas");
    const finish = await w.api.beginWorkspaceGeneration("film");
    assert.equal(w.leases[0].revision, 1);
    await w.api.writeWorkspaceState("canvas", JSON.stringify({ state: { projects: [{ id: "film", nodes: [{ id: "shot", metadata: { status: "loading", clientTaskId: "task-new" } }] }] } }));
    finish();
    w.heartbeat();
    await tick();
    assert.equal(w.leases.length, 2);
    await w.api.writeWorkspaceState("canvas", JSON.stringify({ state: { projects: [{ id: "film", nodes: [{ id: "shot", metadata: { status: "success" } }] }] } }));
    w.heartbeat();
    assert.equal(w.cleared(), 1);
});

test("old unfinished records cannot hold a new project's writer lease forever", async () => {
    let revision = 1;
    const w = workspace(() => reply({ revision: ++revision }));
    await w.api.readWorkspaceState("canvas");
    await w.api.writeWorkspaceState("canvas", JSON.stringify({ state: { projects: [
        { id: "film", nodes: [{ id: "old", metadata: { status: "loading", clientTaskId: "old-task" } }] },
        { id: "unrelated", nodes: [{ id: "other", metadata: { status: "loading", videoTaskId: "other-task" } }] },
    ] } }));
    const finish = await w.api.beginWorkspaceGeneration("film");
    finish();
    w.heartbeat();
    assert.equal(w.cleared(), 1);
    assert.equal(w.leases.length, 1);
});

test("cold-start history is included in the lease baseline before the first edit", async () => {
    let revision = 1;
    const value = JSON.stringify({ state: { projects: [{ id: "film", nodes: [{ id: "old", metadata: { status: "loading", clientTaskId: "old-task" } }] }] } });
    const w = workspace(() => reply({ revision: ++revision }), () => reply({ value, revision }));
    await w.api.readWorkspaceState("canvas");
    const finish = await w.api.beginWorkspaceGeneration("film");
    await w.api.writeWorkspaceState("canvas", value);
    finish();
    w.heartbeat();
    assert.equal(w.cleared(), 1);
    assert.equal(w.leases.length, 1);
});

test("unmounting the project stops renewal even with a pending owned job", async () => {
    let revision = 1;
    const w = workspace(() => reply({ revision: ++revision }));
    await w.api.readWorkspaceState("canvas");
    const finish = await w.api.beginWorkspaceGeneration("film");
    await w.api.writeWorkspaceState("canvas", JSON.stringify({ state: { projects: [{ id: "film", nodes: [{ id: "shot", metadata: { status: "loading", clientTaskId: "new" } }] }] } }));
    w.api.releaseWorkspaceProject("film");
    w.heartbeat();
    finish();
    assert.equal(w.cleared(), 1);
});

test("a viewer retains fresh shared state when optional normalization is lease-locked", async () => {
    const w = workspace(() => reply(null, 423), undefined, (value) => ({ ...value, normalized: true }));
    const value = await w.api.readWorkspaceState("canvas");
    assert.equal(value, '{"state":{"projects":[]}}');
    assert.equal(w.values.get("canvas"), value);
    assert.equal(w.status.phase, "ready");
});

for (const statusCode of [409, 502]) test(`failed ${statusCode} write blocks queued snapshots and preserves later draft`, async () => {
    let finish;
    const w = workspace(() => new Promise((resolve) => { finish = resolve; }));
    await w.api.readWorkspaceState("canvas");
    const a = w.api.writeWorkspaceState("canvas", '{"edit":"A"}');
    const b = w.api.writeWorkspaceState("canvas", '{"edit":"B"}');
    await tick();
    assert.equal(w.requests.length, 1);
    finish(reply(null, statusCode));
    await Promise.all([a, b]);
    await w.api.writeWorkspaceState("canvas", '{"edit":"C"}');
    assert.equal(w.requests.length, 1);
    assert.equal(w.values.get("workspace-draft:canvas"), '{"edit":"C"}');
    assert.equal(w.status.phase, statusCode === 409 ? "conflict" : "error");
});

test("a slow state read cannot race a successful write and rewind its revision", async () => {
    let revision = 1, reads = 0, finishRead;
    const value = '{"state":{"projects":[]}}';
    const w = workspace((body) => body.revision === revision ? reply({ revision: ++revision }) : reply(null, 409), () => {
        if (++reads === 2) return new Promise((resolve) => { finishRead = resolve; });
        return reply({ value, revision });
    });
    await w.api.readWorkspaceState("canvas");
    const read = w.api.readWorkspaceState("canvas");
    await tick();
    const first = w.api.writeWorkspaceState("canvas", '{"edit":"A"}');
    await tick();
    const writesDuringRead = w.requests.length;
    finishRead(reply({ value, revision: 1 }));
    await Promise.all([read, first]);
    await w.api.writeWorkspaceState("canvas", '{"edit":"B"}');
    assert.equal(writesDuringRead, 0);
    assert.deepEqual(w.requests.map((body) => body.revision), [1, 2]);
    assert.equal(w.status.phase, "ready");
});

test("acknowledging an older write keeps the pending latest draft and uses sequential revisions", async () => {
    const pending = [];
    const w = workspace(() => new Promise((resolve) => pending.push(resolve)));
    await w.api.readWorkspaceState("canvas");
    const a = w.api.writeWorkspaceState("canvas", '{"edit":"A"}');
    const b = w.api.writeWorkspaceState("canvas", '{"edit":"B"}');
    await tick();
    pending[0](reply({ revision: 2 }));
    await a;
    await tick();
    assert.equal(w.values.get("workspace-draft:canvas"), '{"edit":"B"}');
    assert.equal(w.requests[1].revision, 2);
    pending[1](reply({ revision: 3 }));
    await b;
    assert.equal(w.values.has("workspace-draft:canvas"), false);
    assert.equal(w.status.phase, "ready");
});
