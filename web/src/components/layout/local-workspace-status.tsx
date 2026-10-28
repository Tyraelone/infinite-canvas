"use client";

import { useLocalWorkspaceStore } from "@/stores/use-local-workspace-store";
import { downloadWorkspaceDrafts } from "@/services/local-workspace";

export function LocalWorkspaceStatus() {
    const { enabled, phase, detail, storageRoot } = useLocalWorkspaceStore();
    if (!enabled) return null;
    const failed = phase === "error" || phase === "conflict";
    return <div className="fixed right-4 top-16 z-[120] max-w-[min(90vw,520px)] rounded-lg px-3 py-2 text-xs"
        role={failed ? "alert" : "status"} title={storageRoot}
        style={{ color: "var(--foreground)", background: "var(--background)", border: failed ? "1px solid var(--border)" : undefined }}>
        <span>{phase === "ready" ? "本机共享 · 已保存到 D 盘" : detail || "正在连接本机共享存储…"}</span>
        {failed ? <>
            <button type="button" className="ml-3 cursor-pointer underline" onClick={() => void downloadWorkspaceDrafts()}>下载修改草稿</button>
            <button type="button" className="ml-3 cursor-pointer underline" onClick={() => window.location.reload()}>刷新重试</button>
        </> : null}
    </div>;
}
