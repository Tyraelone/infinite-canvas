"use client";

import { useCallback, useEffect, useRef, type Dispatch, type RefObject, type SetStateAction } from "react";
import { CompositionError, cancelComposition, createCompositionAfterSave, getComposition, type CompositionOptions } from "@/services/api/video-composition";
import { beginWorkspaceGeneration, localWorkspaceEnabled } from "@/services/local-workspace";
import { uploadMediaFile } from "@/services/file-storage";
import { CanvasNodeType, type CanvasNodeData } from "../types";
import { saveCanvasNodesNow } from "../stores/use-canvas-store";
import { applyCompositionTaskUpdate, compositionPosition, compositionRequest, compositionSources, normalizeCompositionOptions } from "../utils/canvas-video-composition";

export function useVideoComposition(projectId: string, loaded: boolean, nodes: RefObject<CanvasNodeData[]>, setNodes: Dispatch<SetStateAction<CanvasNodeData[]>>) {
    const submitting = useRef(false);
    const commit = useCallback((update: (items: CanvasNodeData[]) => CanvasNodeData[]) => setNodes((previous) => {
        const next = update(previous); nodes.current = next; return next;
    }), [nodes, setNodes]);

    useEffect(() => {
        if (!loaded) return;
        const active = new Map<string, AbortController>();
        let release: (() => void) | undefined;
        let acquiring = false;
        let disposed = false;
        const poll = async () => {
            const pending = nodes.current.filter((node) => node.metadata?.compositionTaskId && ["loading", "stopping"].includes(node.metadata.status || ""));
            if (!pending.length) { release?.(); release = undefined; return; }
            if (!release) {
                if (acquiring) return;
                acquiring = true;
                try {
                    const end = await beginWorkspaceGeneration(projectId);
                    if (disposed) { end(); return; }
                    release = end;
                } catch { return; }
                finally { acquiring = false; }
            }
            pending.forEach((node) => {
            const id = node.metadata!.compositionTaskId!;
            if (active.has(id)) return;
            const controller = new AbortController(); active.set(id, controller);
            void getComposition(id, controller.signal).then((task) => {
                if (!controller.signal.aborted) commit((items) => applyCompositionTaskUpdate(items, node.id, task));
            }).catch((error) => {
                if (!controller.signal.aborted && error instanceof CompositionError && error.status === 404 && Date.now() - (node.metadata?.startedAt || 0) > 60000) {
                    commit((items) => applyCompositionTaskUpdate(items, node.id, { id, status: "failed", progress: 0, error: "合成任务未提交成功，请重试" }));
                }
            }).finally(() => active.delete(id));
            });
        };
        void poll(); const timer = window.setInterval(() => void poll(), 2000);
        return () => { disposed = true; release?.(); window.clearInterval(timer); active.forEach((controller) => controller.abort()); };
    }, [loaded, nodes, commit, projectId]);

    const submit = useCallback(async (input: CompositionOptions | Record<string, unknown>, retryNodeId?: string) => {
        if (submitting.current) throw new Error("正在提交合成，请勿重复点击");
        submitting.current = true;
        let release = () => {};
        let outputId = retryNodeId;
        let requestId = "";
        try {
            if (!await localWorkspaceEnabled()) throw new Error("视频合成需要本机运行模式，请使用本机启动入口");
            release = await beginWorkspaceGeneration(projectId);
            const options = normalizeCompositionOptions(input);
            const previous = nodes.current.find((node) => node.id === retryNodeId);
            if (previous?.metadata?.compositionTaskId) {
                const task = await getComposition(previous.metadata.compositionTaskId).catch(() => null);
                if (task && ["queued", "processing", "completed"].includes(task.status)) {
                    commit((items) => applyCompositionTaskUpdate(items.map((item) => item.id === previous.id ? { ...item, metadata: { ...item.metadata, status: "loading" } } : item), previous.id, task));
                    return { node: previous, task };
                }
            }
            let prepared = [...nodes.current];
            for (const source of compositionSources(options, prepared)) {
                if (source.metadata?.content?.startsWith("/api/local-runtime/workspace/files/")) continue;
                const saved = await uploadMediaFile(source.metadata!.content!, "composition-source");
                prepared = prepared.map((item) => item.id === source.id ? { ...item, metadata: { ...item.metadata, content: saved.url, storageKey: saved.storageKey } } : item);
                commit((items) => items.map((item) => item.id === source.id ? { ...item, metadata: { ...item.metadata, content: saved.url, storageKey: saved.storageKey } } : item));
            }
            requestId = crypto.randomUUID().replaceAll("-", "");
            outputId ||= "video-composition-" + requestId;
            const sources = compositionSources(options, nodes.current);
            const total = options.clips.reduce((sum, clip) => sum + clip.durationSeconds, 0);
            const node: CanvasNodeData = { id: outputId, type: CanvasNodeType.Video, title: options.title, position: previous?.position || compositionPosition(sources, nodes.current), width: 420, height: 236,
                metadata: { status: "loading", compositionTaskId: requestId, compositionOptions: options, progress: 0, startedAt: Date.now(), seconds: String(total), compositionStage: "正在提交合成" } };
            const pendingNodes = await new Promise<CanvasNodeData[]>((resolve) => setNodes((items) => {
                const next = previous ? items.map((item) => item.id === node.id ? node : item) : [...items, node];
                nodes.current = next; resolve(next); return next;
            }));
            const task = await createCompositionAfterSave(compositionRequest(options, prepared, requestId), () => saveCanvasNodesNow(projectId, pendingNodes));
            commit((items) => applyCompositionTaskUpdate(items, node.id, task));
            return { node, task };
        } catch (error) {
            if (outputId && requestId) commit((items) => applyCompositionTaskUpdate(items, outputId!, { id: requestId, status: "failed", progress: 0, error: error instanceof Error ? error.message : "合成提交失败" }));
            throw error;
        } finally { release(); submitting.current = false; }
    }, [commit, nodes, projectId, setNodes]);

    const stop = useCallback(async (nodeId: string) => {
        const node = nodes.current.find((item) => item.id === nodeId);
        const id = node?.metadata?.compositionTaskId;
        if (!id) return;
        commit((items) => items.map((item) => item.id === nodeId ? { ...item, metadata: { ...item.metadata, status: "stopping", errorDetails: undefined } } : item));
        try { const task = await cancelComposition(id); commit((items) => applyCompositionTaskUpdate(items, nodeId, task)); }
        catch (error) { commit((items) => items.map((item) => item.id === nodeId ? { ...item, metadata: { ...item.metadata, errorDetails: "取消失败，请重试停止" } } : item)); throw error; }
    }, [commit, nodes]);
    return { submit, stop };
}
