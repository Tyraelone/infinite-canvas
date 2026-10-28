import { LOCAL_CHANNEL_ID, mergeLocalChannel, shouldSelectLocalDefaults } from "@/lib/local-runtime";
import { useConfigStore, type LocalModelChannel } from "@/stores/use-config-store";
import type { CodexModel } from "./codex-agent";

export async function fetchLocalCodexModels(signal?: AbortSignal): Promise<CodexModel[]> {
    const response = await fetch("/api/local-runtime/v1/codex/models", { signal });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error?.message || "读取本机 Codex 模型失败");
    return payload.data;
}

export async function initializeLocalRuntime() {
    const response = await fetch("/api/local-runtime/profile");
    if (!response.ok) return;
    const profile = await response.json() as { enabled: boolean; baseUrl: string };
    if (!profile.enabled) return;
    const baseUrl = `${window.location.origin}/api/local-runtime/v1`;
    const models = ["qwen-image-2.1", "codex-image", "local-h3-turbo", "local-h3-quality", "codex-local"];
    const channel: LocalModelChannel = {
        id: LOCAL_CHANNEL_ID, name: "本机 AI · H3 / Qwen / Codex", protocol: "openai",
        baseUrl, apiKey: "local", models,
        modelCapabilities: { "qwen-image-2.1": "image", "codex-image": "image", "local-h3-turbo": "video", "local-h3-quality": "video", "codex-local": "text" },
    };
    useConfigStore.setState((state) => {
        const selectDefaults = shouldSelectLocalDefaults(state.config.localChannels, state.config.imageModel);
        const config = mergeLocalChannel(state.config, channel);
        return { config: { ...config, ...(selectDefaults ? {
            channelMode: "local" as const, baseUrl, apiKey: "local", model: "codex-local",
            imageModel: "qwen-image-2.1", videoModel: "local-h3-turbo", textModel: "codex-local",
            imageModels: ["qwen-image-2.1", "codex-image"], videoModels: ["local-h3-turbo", "local-h3-quality"], textModels: ["codex-local"], models,
            imageChannelId: LOCAL_CHANNEL_ID, videoChannelId: LOCAL_CHANNEL_ID, textChannelId: LOCAL_CHANNEL_ID,
            videoSeconds: "5", vquality: "480", videoSize: "16:9", size: "1:1", apiMode: "images",
        } : {}) } };
    });
}
