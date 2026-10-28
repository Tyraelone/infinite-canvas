export type CompositionClip = { nodeId: string; startSeconds: number; durationSeconds: number };
export type CompositionOptions = { title: string; clips: CompositionClip[]; keepAudio: boolean; openingText: string; endingText: string; score: boolean };
export type CompositionRequest = Omit<CompositionOptions, "clips"> & { requestId: string; clips: { storageKey: string; startSeconds: number; durationSeconds: number }[] };
export type CompositionTask = { id: string; status: "queued" | "processing" | "completed" | "failed" | "cancelled"; progress: number; stage?: string; error?: string; storageKey?: string; url?: string; width?: number; height?: number; durationSeconds?: number; frames?: number; bytes?: number; sha256?: string };

const base = "/api/local-runtime/v1/compositions";
export class CompositionError extends Error { constructor(message: string, readonly status: number) { super(message); } }
async function request(path: string, options: RequestInit = {}): Promise<CompositionTask> {
    const response = await fetch(base + path, { ...options, cache: "no-store" });
    const value = await response.json().catch(() => null);
    if (!response.ok) throw new CompositionError(value?.error?.message || value?.msg || "本机视频合成接口不可用", response.status);
    return value as CompositionTask;
}
export const getComposition = (id: string, signal?: AbortSignal) => request("/" + encodeURIComponent(id), { signal });
export const cancelComposition = (id: string) => request("/" + encodeURIComponent(id), { method: "DELETE" });
export async function createCompositionAfterSave(body: CompositionRequest, savePending: () => Promise<void>) {
    await savePending();
    return createComposition(body);
}
export async function createComposition(body: CompositionRequest) {
    try { return await request("", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
    catch (error) {
        if (error instanceof CompositionError) throw error;
        return getComposition(body.requestId).catch(() => { throw error; });
    }
}
