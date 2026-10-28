"use client";

import localforage from "localforage";
import { collectWorkspaceReferences, emptyLegacyWorkspace, rewriteWorkspaceMedia, sharedStateSlot, workspaceFileUrl } from "@/lib/local-workspace";
import { useLocalWorkspaceStore } from "@/stores/use-local-workspace-store";

const base = "/api/local-runtime/workspace";
const rawState = localforage.createInstance({ name: "infinite-canvas", storeName: "app_state" });
const imageFiles = localforage.createInstance({ name: "infinite-canvas", storeName: "image_files" });
const mediaFiles = localforage.createInstance({ name: "infinite-canvas", storeName: "media_files" });
const names = { canvas: "infinite-canvas:canvas_store", assets: "infinite-canvas:asset_store" } as const;
const markerKey = "infinite-canvas:disk-workspace-migration-v1";
const clientId = (() => {
    if (typeof window === "undefined") return "server";
    try {
        const key = "infinite-canvas:workspace-tab-id";
        const previous = window.sessionStorage.getItem(key);
        if (previous && /^[a-f0-9-]{36}$/.test(previous)) return previous;
        const id = crypto.randomUUID(); window.sessionStorage.setItem(key, id); return id;
    } catch { return crypto.randomUUID(); }
})();
let modePromise: Promise<boolean> | undefined;
let migrationPromise: Promise<void> | undefined;
let enabled = false;
let pendingWrites = 0;
let leaseHeartbeat: number | undefined;
const leaseProjects = new Map<string, { runs: number; baseline: Map<string, string> }>();
const versions = new Map<string, number>();
const availableKeys = new Set<string>();
const fileHashes = new Map<string, string>();
const replacements = new Map<string, string>();
const queues = new Map<string, Promise<void>>();
const blockedSlots = new Set<string>();
const latestDrafts = new Map<string, { value: string }>();
const snapshots = new Map<string, string>();

class WorkspaceError extends Error {
    constructor(message: string, readonly status: number) { super(message); }
}

function failure(error: unknown) {
    const conflict = error instanceof WorkspaceError && error.status === 409;
    if (error instanceof WorkspaceError && error.status === 423) {
        useLocalWorkspaceStore.setState({ enabled: true, phase: "conflict", detail: error.message });
        return;
    }
    useLocalWorkspaceStore.setState({ enabled: true, phase: conflict ? "conflict" : "error", detail: conflict
        ? "其他浏览器已更新数据，当前修改已保留为浏览器草稿；请刷新后再编辑。"
        : `本机保存失败，原数据已保留：${error instanceof Error ? error.message : String(error)}` });
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers);
    headers.set("X-Workspace-Client", clientId);
    const response = await fetch(base + path, { ...options, headers, cache: "no-store" });
    const payload = await response.json().catch(() => null) as { code?: number; msg?: string; data?: T; error?: { message?: string } } | null;
    if (!response.ok || payload?.code !== 0) throw new WorkspaceError(payload?.msg || payload?.error?.message || `本机存储接口失败（${response.status}）`, response.status);
    return payload.data as T;
}

export function localWorkspaceActive() { return enabled; }

function hasPendingGeneration() {
    const snapshot = latestDrafts.get("canvas")?.value || snapshots.get("canvas");
    if (!snapshot) return false;
    const projects = JSON.parse(snapshot)?.state?.projects || [];
    return projects.some((project: any) => {
        const scope = leaseProjects.get(project.id);
        return scope && project.nodes?.some((node: any) => {
            const task = node.metadata?.compositionTaskId || node.metadata?.clientTaskId || node.metadata?.videoTaskId || node.metadata?.imageTaskId || node.metadata?.audioTaskId;
            return task && ["loading", "stopping"].includes(node.metadata?.status) && scope.baseline.get(node.id) !== task;
        });
    });
}

export function releaseWorkspaceProject(projectId: string) { leaseProjects.delete(projectId); }

export async function beginWorkspaceGeneration(projectId: string): Promise<() => void> {
    if (!await ensureLocalWorkspace()) return () => {};
    await queues.get("canvas");
    if (blockedSlots.has("canvas") || !versions.has("canvas")) throw new Error("请刷新读取最新画布后继续制作；当前修改可先下载草稿");
    const renew = () => request("/lease", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ revision: versions.get("canvas") }) });
    try { await renew(); } catch (error) { failure(error); throw error; }
    const snapshot = JSON.parse(snapshots.get("canvas") || latestDrafts.get("canvas")?.value || "null");
    const project = snapshot?.state?.projects?.find((item: any) => item.id === projectId);
    const scope = leaseProjects.get(projectId) || { runs: 0, baseline: new Map<string, string>(project?.nodes?.map((node: any) =>
        [node.id, node.metadata?.compositionTaskId || node.metadata?.clientTaskId || node.metadata?.videoTaskId || node.metadata?.imageTaskId || node.metadata?.audioTaskId || ""])) };
    scope.runs += 1;
    leaseProjects.set(projectId, scope);
    if (leaseHeartbeat === undefined) leaseHeartbeat = window.setInterval(() => {
        if (![...leaseProjects.values()].some((scope) => scope.runs > 0) && !hasPendingGeneration()) {
            window.clearInterval(leaseHeartbeat);
            leaseHeartbeat = undefined;
            leaseProjects.clear();
            return;
        }
        void renew().catch(failure);
    }, 15000);
    let ended = false;
    return () => { if (!ended) { ended = true; scope.runs -= 1; } };
}

export async function localWorkspaceEnabled() {
    if (typeof window === "undefined") return false;
    if (!modePromise) modePromise = fetch("/api/local-runtime/profile", { cache: "no-store" }).then(async (response) => {
        if (response.status === 404) return false;
        if (!response.ok) throw new Error("无法确认本机存储状态，请检查本机服务");
        enabled = (await response.json()).enabled === true;
        useLocalWorkspaceStore.setState({ enabled });
        return enabled;
    });
    return modePromise;
}

async function sha256(blob: Blob) {
    return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer())))
        .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function uploadBlob(key: string, blob: Blob) {
    const hash = await sha256(blob);
    const saved = await request<{ key: string; sha256: string; bytes: number }>(`/files/${encodeURIComponent(key)}`, {
        method: "POST", headers: { "Content-Type": blob.type || "application/octet-stream" }, body: blob,
    });
    if (saved.sha256 !== hash || saved.bytes !== blob.size) throw new Error(`媒体完整性校验失败：${key}`);
    availableKeys.add(key);
    fileHashes.set(key, hash);
    return { key, sha256: hash };
}

async function existingBlob(key: string) {
    return (await imageFiles.getItem<Blob>(key)) || (await mediaFiles.getItem<Blob>(key));
}

async function ensureFile(key: string, fallback?: string) {
    if (availableKeys.has(key)) return;
    const response = await fetch(workspaceFileUrl(key), { method: "HEAD", cache: "no-store" });
    if (response.ok) { availableKeys.add(key); return; }
    const blob = await existingBlob(key);
    if (blob instanceof Blob) { await uploadBlob(key, blob); return; }
    if (fallback) {
        const source = await fetch(fallback);
        if (source.ok) { await uploadBlob(key, await source.blob()); return; }
    }
    throw new Error(`缺少媒体 ${key}，迁移未发布；原始浏览器数据仍保留`);
}

async function prepareValue(value: unknown) {
    const references = collectWorkspaceReferences(value);
    for (const key of references.keys) {
        const fallback = [...references.byUrl].find(([, found]) => found === key)?.[0];
        await ensureFile(key, fallback);
    }
    for (const url of references.urls) {
        if (replacements.has(url) || (references.byUrl.has(url) && availableKeys.has(references.byUrl.get(url)!))) continue;
        const response = await fetch(url);
        if (!response.ok) throw new Error("存在无法读取的媒体引用，未切换原始数据");
        const blob = await response.blob();
        const key = "inline:" + await sha256(blob);
        await uploadBlob(key, blob);
        replacements.set(url, workspaceFileUrl(key));
    }
    return rewriteWorkspaceMedia(value, availableKeys, replacements);
}

export async function ensureLocalWorkspace() {
    if (!await localWorkspaceEnabled()) return false;
    if (!migrationPromise) migrationPromise = (async () => {
        const status = await request<{ storageRoot: string; projectCount: number; assetCount: number; fileCount: number }>("/status");
        useLocalWorkspaceStore.setState({ phase: "migrating", detail: "正在检查并迁移原浏览器画布和素材…", storageRoot: status.storageRoot });
        const marker = await rawState.getItem<{ migrationId: string; complete: boolean }>(markerKey);
        if (!marker?.complete) {
            const migrationId = marker?.migrationId || crypto.randomUUID();
            await rawState.setItem(markerKey, { migrationId, complete: false });
            const rawStates: Record<string, string | null> = {};
            for (const [slot, key] of Object.entries(names)) {
                const value = await rawState.getItem<string | object>(key);
                rawStates[slot] = typeof value === "string" ? value : value ? JSON.stringify(value) : window.localStorage.getItem(key);
            }
            const files: Array<{ key: string; sha256: string }> = [];
            for (const storage of [imageFiles, mediaFiles]) {
                for (const key of await storage.keys()) {
                    const blob = await storage.getItem<Blob>(key);
                    if (!(blob instanceof Blob)) throw new Error(`媒体格式无法迁移：${key}`);
                    files.push(await uploadBlob(key, blob));
                    useLocalWorkspaceStore.setState({ detail: `正在校验并迁移媒体：${files.length} 个文件` });
                }
            }
            const states: Record<string, string | null> = {};
            for (const [slot, value] of Object.entries(rawStates)) states[slot] = value ? JSON.stringify(await prepareValue(JSON.parse(value))) : null;
            const byKey = new Map(files.map((file) => [file.key, file]));
            for (const [key, hash] of fileHashes) byKey.set(key, { key, sha256: hash });
            const report = !byKey.size && emptyLegacyWorkspace(rawStates) ? { ...status, revisions: {} } : await request<{ revisions: Record<string, number>; projectCount: number; assetCount: number; fileCount: number }>("/import", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ migrationId, sourceOrigin: window.location.origin, rawStates, states, files: [...byKey.values()] }),
            });
            Object.entries(report.revisions).forEach(([slot, revision]) => versions.set(slot, revision));
            await rawState.setItem(markerKey, { migrationId, complete: true, report });
        }
        useLocalWorkspaceStore.setState({ phase: "ready", detail: "画布和素材已保存到本机，各浏览器共享" });
    })().catch((error) => { failure(error); throw error; });
    await migrationPromise;
    return true;
}

export async function readWorkspaceState(name: string): Promise<string | null | undefined> {
    const slot = sharedStateSlot(name);
    if (!slot) return undefined;
    try {
        if (!await ensureLocalWorkspace()) return undefined;
        const task = (queues.get(slot) || Promise.resolve()).then(async () => {
            const state = await request<{ value: string | null; revision: number }>(`/state/${slot}`);
            if (state.value !== null) {
                const normalized = JSON.stringify(await prepareValue(JSON.parse(state.value)));
                if (normalized !== state.value) {
                    try {
                        const saved = await request<{ revision: number }>(`/state/${slot}`, {
                            method: "PUT", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ value: normalized, revision: state.revision }),
                        });
                        state.value = normalized;
                        state.revision = saved.revision;
                    } catch (error) {
                        if (!(error instanceof WorkspaceError) || error.status !== 423) throw error;
                    }
                }
            }
            versions.set(slot, state.revision);
            if (state.value !== null) snapshots.set(slot, state.value);
            if (state.value !== null) await rawState.setItem(name, state.value);
            return state.value;
        });
        queues.set(slot, task.then(() => undefined, () => undefined));
        return await task;
    } catch (error) {
        blockedSlots.add(slot);
        failure(error);
        return await rawState.getItem<string>(name);
    }
}

export async function writeWorkspaceState(name: string, value: string, requireAck = false): Promise<boolean> {
    const slot = sharedStateSlot(name);
    if (!slot) return false;
    const draft = { value };
    latestDrafts.set(slot, draft);
    try { if (!await ensureLocalWorkspace()) return false; }
    catch (error) {
        blockedSlots.add(slot);
        await rawState.setItem(`workspace-draft:${slot}`, latestDrafts.get(slot)!.value);
        failure(error);
        if (requireAck) throw error;
        return true;
    }
    await rawState.setItem(`workspace-draft:${slot}`, latestDrafts.get(slot)!.value);
    if (blockedSlots.has(slot)) {
        if (requireAck) throw new Error("共享保存已阻止，请下载草稿并刷新后重试");
        return true;
    }
    pendingWrites += 1;
    if (!blockedSlots.size) useLocalWorkspaceStore.setState({ phase: "saving", detail: "正在保存到本机…" });
    const task = (queues.get(slot) || Promise.resolve()).then(async () => {
        if (blockedSlots.has(slot)) {
            if (requireAck) throw new Error("共享保存已阻止，请刷新后重试");
            return;
        }
        const normalized = JSON.stringify(await prepareValue(JSON.parse(value)));
        if (!versions.has(slot)) {
            const state = await request<{ revision: number }>(`/state/${slot}`);
            versions.set(slot, state.revision);
        }
        const saved = await request<{ revision: number }>(`/state/${slot}`, {
            method: "PUT", headers: { "Content-Type": "application/json", "X-Workspace-Revision": String(versions.get(slot)) },
            body: JSON.stringify({ value: normalized, revision: versions.get(slot) }),
        });
        versions.set(slot, saved.revision);
        snapshots.set(slot, normalized);
        await rawState.setItem(name, normalized);
        if (latestDrafts.get(slot) === draft) await rawState.removeItem(`workspace-draft:${slot}`);
    });
    queues.set(slot, task.catch(() => undefined));
    try {
        await task;
        pendingWrites -= 1;
        if (!pendingWrites && !blockedSlots.size) {
            useLocalWorkspaceStore.setState({ phase: "ready", detail: "画布和素材已保存到本机，各浏览器共享" });
        }
    } catch (error) {
        pendingWrites -= 1;
        if (!(error instanceof WorkspaceError && error.status === 423)) blockedSlots.add(slot);
        failure(error);
        if (requireAck) throw error;
    }
    return true;
}

export async function saveWorkspaceBlob(key: string, blob: Blob): Promise<string | undefined> {
    if (!await ensureLocalWorkspace()) return undefined;
    try { await uploadBlob(key, blob); return workspaceFileUrl(key); }
    catch (error) { failure(error); throw error; }
}

export async function loadWorkspaceBlob(key: string): Promise<Blob | null | undefined> {
    if (!await ensureLocalWorkspace()) return undefined;
    const response = await fetch(workspaceFileUrl(key));
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`读取本机媒体失败（${response.status}）`);
    return response.blob();
}

export async function downloadWorkspaceDrafts() {
    const drafts: Record<string, string> = {};
    for (const slot of Object.keys(names)) {
        const value = await rawState.getItem<string>(`workspace-draft:${slot}`);
        if (value) drafts[slot] = value;
    }
    const url = URL.createObjectURL(new Blob([JSON.stringify({ exportedAt: new Date().toISOString(), drafts }, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `canvas-drafts-${Date.now()}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
