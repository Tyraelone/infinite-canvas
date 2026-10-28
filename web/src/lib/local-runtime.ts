export const LOCAL_CHANNEL_ID = "this-machine";
export const LOCAL_H3_DURATIONS = [5, 10, 15] as const;
export const LOCAL_H3_MAX_REFERENCES = 9;

export function resolveLocalCodexSelection<T extends { model: string; isDefault: boolean; defaultReasoningEffort: string; supportedReasoningEfforts: Array<{ reasoningEffort: string }> }>(models: T[], savedModel?: string, savedEffort?: string) {
    const model = savedModel ? models.find((item) => item.model === savedModel) : models.find((item) => item.isDefault) || models[0];
    const effort = savedEffort || model?.defaultReasoningEffort;
    const error = !model ? "所选 Codex 模型当前不可用，请重新选择"
        : !model.supportedReasoningEfforts.some((item) => item.reasoningEffort === effort) ? "所选模型不支持该推理强度，请重新选择" : "";
    return { model, effort, error };
}

export function localH3DurationError(seconds: number) {
    return LOCAL_H3_DURATIONS.some((value) => value === seconds) ? "" : "当前本机 H3 仅支持 5、10 或 15 秒";
}

export async function stopLocalVideoAfterSubmission(
    submission: { id: string; status?: string } | Promise<{ id: string; status?: string }> | undefined,
    cancel: (id: string) => Promise<unknown>,
    persistedTaskId?: string,
) {
    const task = await Promise.resolve(submission).catch(() => undefined);
    if (task?.id && task.status !== "failed") await cancel(task.id);
    else if (!task && /^[a-f0-9]{32}$/.test(persistedTaskId || "")) await cancel(persistedTaskId!);
}

export function shouldSelectLocalDefaults(channels: Array<{ id: string; models: string[]; apiKey: string }>, imageModel: string) {
    return !channels.some((channel) => channel.id !== LOCAL_CHANNEL_ID && (channel.models.length || channel.apiKey.trim()))
        && !["qwen-image-2.1", "codex-image"].includes(imageModel);
}

export function isLocalH3Model(model: string) {
    return model === "local-h3-turbo" || model === "local-h3-quality";
}

export function localH3Size(resolution: string, ratio: string) {
    const sizes: Record<string, Record<string, string>> = {
        "480": { "16:9": "864x480", "9:16": "480x864", "1:1": "640x640" },
        "720": { "16:9": "1280x720", "9:16": "720x1280", "1:1": "960x960" },
        "768": { "16:9": "1344x768", "9:16": "768x1344", "1:1": "992x992" },
    };
    const aspect = Object.values(sizes).flatMap((options) => Object.entries(options))
        .find(([, pixels]) => pixels === ratio || pixels.split("x").map((value) => Number(value) * 2).join("x") === ratio)?.[0] || ratio;
    const value = sizes[resolution.replace(/p$/, "")]?.[aspect];
    if (!value) throw new Error("本机 H3 支持 480p/720p/768p，画幅为 16:9、9:16 或 1:1");
    return value;
}

export function localH3VideoOptions(config: { vquality: string; size: string; videoSeconds: string; videoGenerateAudio: string; videoUpscale: string }) {
    return { size: localH3Size(config.vquality, config.size), seconds: config.videoSeconds,
        video_generate_audio: config.videoGenerateAudio === "true", upscale: config.videoUpscale === "true" };
}

export function canUpscaleLocalH3Video(metadata?: { model?: string; status?: string; content?: string; videoTaskId?: string; upscale?: string; canUpscale?: boolean }) {
    return Boolean(metadata && isLocalH3Model(metadata.model || "") && metadata.status === "success" && metadata.content
        && /^[a-f0-9]{32}$/.test(metadata.videoTaskId || "") && metadata.upscale !== "true" && metadata.canUpscale !== false);
}

export function mergeLocalChannel<T extends { localChannels: Array<{ id: string }> }>(config: T, channel: T["localChannels"][number]) {
    return { ...config, localChannels: [...config.localChannels.filter((item) => item.id !== LOCAL_CHANNEL_ID), channel] };
}
