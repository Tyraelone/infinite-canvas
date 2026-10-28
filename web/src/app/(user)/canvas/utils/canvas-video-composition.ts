import type { CanvasNodeData, CanvasNodeMetadata } from "../types";
import type { CompositionOptions, CompositionRequest, CompositionTask } from "@/services/api/video-composition";

export function normalizeCompositionOptions(input: Record<string, unknown>): CompositionOptions {
    if (!Array.isArray(input.clips) || !input.clips.length || input.clips.length > 50) throw new Error("请选择1至50段视频");
    const seconds = (value: unknown, zero = false) => {
        if (typeof value !== "number" || !Number.isFinite(value) || value < (zero ? 0 : 1 / 24)) throw new Error("起点和使用时长必须是有效秒数");
        return Math.round(value * 24) / 24;
    };
    const clips = input.clips.map((item) => {
        if (!item || typeof item !== "object") throw new Error("合成片段无效");
        const clip = item as Record<string, unknown>;
        if (typeof clip.nodeId !== "string" || !clip.nodeId.trim()) throw new Error("合成片段缺少节点ID");
        return { nodeId: clip.nodeId.trim(), startSeconds: seconds(clip.startSeconds ?? 0, true), durationSeconds: seconds(clip.durationSeconds) };
    });
    if (clips.reduce((sum, clip) => sum + clip.durationSeconds, 0) > 1800) throw new Error("合成总时长不能超过1800秒");
    const text = (key: string, fallback = "") => {
        if (input[key] !== undefined && (typeof input[key] !== "string" || (input[key] as string).length > (key === "title" ? 120 : 60))) throw new Error("标题或片头片尾文字过长");
        return typeof input[key] === "string" ? (input[key] as string).trim() : fallback;
    };
    for (const key of ["keepAudio", "score"]) if (input[key] !== undefined && typeof input[key] !== "boolean") throw new Error(key + "必须是布尔值");
    return { clips, title: text("title", "合成视频") || "合成视频", keepAudio: input.keepAudio !== false, score: input.score === true, openingText: text("openingText"), endingText: text("endingText") };
}

export function compositionSources(options: CompositionOptions, nodes: CanvasNodeData[]) {
    return options.clips.map((clip) => {
        const node = nodes.find((item) => item.id === clip.nodeId);
        if (!node) throw new Error("找不到源节点 " + clip.nodeId);
        if (node.type !== "video" || !node.metadata?.content || ["loading", "stopping", "stopped", "error"].includes(node.metadata.status || "")) throw new Error("请选择已完成的视频：" + node.title);
        return node;
    });
}

export function compositionRequest(input: Record<string, unknown> | CompositionOptions, nodes: CanvasNodeData[], requestId: string): CompositionRequest {
    const options = normalizeCompositionOptions(input);
    const sources = compositionSources(options, nodes);
    return { ...options, requestId, clips: options.clips.map((clip, index) => {
        const source = sources[index];
        const storageKey = source.metadata?.storageKey || (source.metadata?.content?.startsWith("/api/local-runtime/workspace/files/") ? decodeURIComponent(source.metadata.content.split("/files/")[1]) : "");
        if (!storageKey) throw new Error("源视频尚未保存到本机共享存储");
        return { storageKey, startSeconds: clip.startSeconds, durationSeconds: clip.durationSeconds };
    }) };
}

export function applyCompositionTaskUpdate(nodes: CanvasNodeData[], nodeId: string, task: CompositionTask) {
    return nodes.map((node) => {
        if (node.id !== nodeId || node.metadata?.compositionTaskId !== task.id || !["loading", "stopping"].includes(node.metadata.status || "")) return node;
        const complete = task.status === "completed" && Boolean(task.url && task.storageKey);
        const status = complete ? "success" : task.status === "failed" ? "error" : task.status === "cancelled" ? "stopped" : node.metadata.status;
        const scale = task.width && task.height ? Math.min(420 / task.width, 420 / task.height, 1) : 0;
        return { ...node, ...(complete && scale ? { width: Math.round(task.width! * scale), height: Math.round(task.height! * scale) } : {}), metadata: { ...node.metadata, status,
            progress: task.progress, compositionStage: task.stage, errorDetails: task.status === "failed" ? task.error || "视频合成失败" : undefined,
            ...(complete ? { content: task.url, storageKey: task.storageKey, naturalWidth: task.width, naturalHeight: task.height, bytes: task.bytes, mimeType: "video/mp4", seconds: String(task.durationSeconds), compositionDurationSeconds: task.durationSeconds } : {}) } } as CanvasNodeData;
    });
}

export function clearCompositionMetadata(metadata?: CanvasNodeMetadata): CanvasNodeMetadata {
    const { compositionTaskId, compositionOptions, compositionStage, compositionDurationSeconds, ...rest } = metadata || {};
    void compositionTaskId; void compositionOptions; void compositionStage; void compositionDurationSeconds;
    return rest;
}

export function compositionPosition(sources: CanvasNodeData[], nodes: CanvasNodeData[]) {
    const source = sources.reduce((a, b) => a.position.x + a.width > b.position.x + b.width ? a : b);
    const position = { x: source.position.x + source.width + 96, y: source.position.y };
    for (let row = 0; row <= nodes.length; row++) {
        const collisions = nodes.filter((node) => node.type !== "group" && position.x < node.position.x + node.width + 72 && position.x + 420 + 72 > node.position.x && position.y < node.position.y + node.height + 72 && position.y + 420 + 72 > node.position.y);
        if (!collisions.length) return position;
        position.y = Math.max(...collisions.map((node) => node.position.y + node.height)) + 72;
    }
    return position;
}
