import { create } from "zustand";

export const useLocalWorkspaceStore = create<{
    enabled: boolean;
    phase: "idle" | "migrating" | "ready" | "saving" | "error" | "conflict";
    detail: string;
    storageRoot: string;
}>(() => ({ enabled: false, phase: "idle", detail: "", storageRoot: "" }));
