const slots: Record<string, "canvas" | "assets"> = {
    "infinite-canvas:canvas_store": "canvas",
    "infinite-canvas:asset_store": "assets",
};
const urlFields = new Set(["content", "url", "dataUrl", "coverUrl", "previewUrl", "posterUrl"]);

export function sharedStateSlot(name: string) { return slots[name]; }
export function workspaceFileUrl(key: string) { return `/api/local-runtime/workspace/files/${encodeURIComponent(key)}`; }
export function isTemporaryMedia(value: string) { return value.startsWith("blob:") || /^data:(image|video|audio)\//.test(value); }
export function isRuntimeMedia(value: string) { return /^(?:https?:\/\/(?:127\.0\.0\.1|localhost):\d+)?\/api\/local-runtime\/media\//.test(value); }
export function emptyLegacyWorkspace(states: Record<string, string | null>) {
    try {
        return Object.entries(states).every(([slot, value]) => {
            if (value === null) return true;
            const list = JSON.parse(value)?.state?.[slot === "canvas" ? "projects" : "assets"];
            return Array.isArray(list) && list.length === 0;
        });
    } catch { return false; }
}

export function collectWorkspaceReferences(value: unknown, result = { keys: new Set<string>(), urls: new Set<string>(), byUrl: new Map<string, string>() }) {
    if (typeof value === "string") {
        if (isTemporaryMedia(value) || isRuntimeMedia(value)) result.urls.add(value);
        return result;
    }
    if (!value || typeof value !== "object") return result;
    const object = value as Record<string, unknown>;
    if (typeof object.storageKey === "string" && object.storageKey) {
        result.keys.add(object.storageKey);
        for (const field of urlFields) {
            const url = object[field];
            if (typeof url === "string" && url) result.byUrl.set(url, object.storageKey);
        }
    }
    Object.values(object).forEach((child) => collectWorkspaceReferences(child, result));
    return result;
}

export function rewriteWorkspaceMedia<T>(source: T, availableKeys: Set<string>, replacements = new Map<string, string>()): T {
    const references = collectWorkspaceReferences(source);
    const urls = new Map(replacements);
    for (const [url, key] of references.byUrl) if (availableKeys.has(key)) urls.set(url, workspaceFileUrl(key));
    function visit(value: unknown): unknown {
        if (typeof value === "string") {
            if (urls.has(value)) return urls.get(value)!;
            if (isTemporaryMedia(value)) throw new Error("存在未迁移的媒体引用，已保留原始浏览器数据");
            return value;
        }
        if (Array.isArray(value)) return value.map(visit);
        if (!value || typeof value !== "object") return value;
        const object = value as Record<string, unknown>;
        const key = typeof object.storageKey === "string" ? object.storageKey : "";
        return Object.fromEntries(Object.entries(object).map(([field, child]) => {
            if (key && availableKeys.has(key) && urlFields.has(field) && typeof child === "string" && child && /^(blob:|data:|https?:|\/)/.test(child)) {
                return [field, workspaceFileUrl(key)];
            }
            return [field, visit(child)];
        }));
    }
    return visit(source) as T;
}
