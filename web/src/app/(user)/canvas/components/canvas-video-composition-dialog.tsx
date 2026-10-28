"use client";

import { useState } from "react";
import { Alert, Button, Input, InputNumber, Modal, Select, Switch } from "antd";
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";
import { canvasThemes } from "@/lib/canvas-theme";
import { useThemeStore } from "@/stores/use-theme-store";
import type { CompositionClip, CompositionOptions } from "@/services/api/video-composition";
import type { CanvasNodeData } from "../types";

function defaultSeconds(node: CanvasNodeData) {
    return node.metadata?.compositionDurationSeconds || Number(node.metadata?.seconds) || (!node.metadata?.startedAt ? (node.metadata?.durationMs || 0) / 1000 : 0);
}

export function CanvasVideoCompositionDialog({ nodes, selectedNodeIds, onClose, onSubmit }: { nodes: CanvasNodeData[]; selectedNodeIds: Set<string>; onClose: () => void; onSubmit: (options: CompositionOptions) => Promise<void> }) {
    const theme = canvasThemes[useThemeStore((state) => state.theme)];
    const available = nodes.filter((node) => node.type === "video" && node.metadata?.content && !["loading", "stopping", "error", "stopped"].includes(node.metadata.status || ""));
    const [clips, setClips] = useState<CompositionClip[]>(() => available.filter((node) => selectedNodeIds.has(node.id)).map((node) => ({ nodeId: node.id, startSeconds: 0, durationSeconds: defaultSeconds(node) })));
    const [durations, setDurations] = useState<Record<string, number>>({});
    const [title, setTitle] = useState("合成视频");
    const [keepAudio, setKeepAudio] = useState(true);
    const [score, setScore] = useState(false);
    const [openingText, setOpeningText] = useState("");
    const [endingText, setEndingText] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const total = clips.reduce((sum, clip) => sum + Math.round(clip.durationSeconds * 24) / 24, 0);
    const invalid = clips.some((clip) => clip.durationSeconds < 1 / 24 || clip.startSeconds < 0 || (durations[clip.nodeId] && clip.startSeconds + clip.durationSeconds > durations[clip.nodeId] + .001));
    const patch = (index: number, value: Partial<CompositionClip>) => setClips((items) => items.map((item, i) => i === index ? { ...item, ...value } : item));
    const move = (index: number, offset: number) => setClips((items) => { const next = [...items]; [next[index], next[index + offset]] = [next[index + offset], next[index]]; return next; });
    const submit = async () => {
        setError(""); setBusy(true);
        try { await onSubmit({ title: title.trim() || "合成视频", clips, keepAudio, score, openingText, endingText }); }
        catch (error) { setError(error instanceof Error ? error.message : "提交合成失败"); }
        finally { setBusy(false); }
    };
    return <Modal title="合成视频" open onCancel={onClose} footer={null} width={860} centered destroyOnHidden closable={!busy} maskClosable={!busy} keyboard={!busy}>
        <div className="space-y-4" style={{ color: theme.node.text }}>
            <p className="text-sm" style={{ color: theme.node.muted }}>按列表顺序拼接，成片保存到 D 盘并生成新视频节点。原片保留，关闭面板不影响后台合成。</p>
            <label className="flex items-center gap-3 text-sm">成片名称<Input aria-label="成片名称" value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} className="flex-1" /></label>
            <Select<string | null> aria-label="从画布添加视频" className="w-full" placeholder="从画布添加视频" showSearch optionFilterProp="label" value={null} disabled={busy || clips.length >= 50}
                options={available.map((node) => ({ value: node.id, label: node.title }))}
                onSelect={(id) => { if (!id) return; const node = available.find((item) => item.id === id)!; setClips((items) => [...items, { nodeId: id, startSeconds: 0, durationSeconds: durations[id] || defaultSeconds(node) }]); }} />
            <div className="max-h-[45vh] space-y-2 overflow-auto rounded-xl p-2" style={{ background: theme.node.fill }}>
                {!clips.length && <p className="py-8 text-center text-sm" style={{ color: theme.node.muted }}>添加视频，或先在画布多选视频再打开合成。</p>}
                {clips.map((clip, index) => {
                    const node = available.find((item) => item.id === clip.nodeId);
                    if (!node) return <div key={index}>源视频已不存在，请移除此片段<Button onClick={() => setClips((items) => items.filter((_, i) => i !== index))}>移除片段 {index + 1}</Button></div>;
                    const duration = durations[node.id] || defaultSeconds(node);
                    return <div key={index + ":" + node.id} className="flex flex-wrap items-center gap-3 rounded-lg border p-2" style={{ borderColor: theme.node.stroke }}>
                        <span className="w-5 text-center text-sm opacity-60">{index + 1}</span>
                        <video className="h-14 w-24 rounded object-contain" src={node.metadata!.content} preload="metadata" muted onLoadedMetadata={(event) => {
                            const value = event.currentTarget.duration;
                            if (!Number.isFinite(value) || value <= 0) return;
                            setDurations((previous) => previous[node.id] === value ? previous : { ...previous, [node.id]: value });
                            setClips((items) => items.map((item) => item.nodeId === node.id && !item.durationSeconds ? { ...item, durationSeconds: Math.floor(value * 24) / 24 } : item));
                        }} />
                        <div className="min-w-28 flex-1"><div className="line-clamp-2 text-sm">{node.title}</div><div className="mt-1 text-xs opacity-60">{duration ? `原片 ${duration.toFixed(2)} 秒` : "正在读取原片时长"}</div></div>
                        <label className="grid gap-1 text-xs opacity-80">起点（秒）<InputNumber aria-label={`片段${index + 1}起点（秒）`} min={0} max={duration || 1800} step={.1} precision={3} value={clip.startSeconds} className="!w-24" disabled={busy} onChange={(value) => patch(index, { startSeconds: Number(value || 0), ...(duration ? { durationSeconds: Math.min(clip.durationSeconds, Math.floor(Math.max(0, duration - Number(value || 0)) * 24) / 24) } : {}) })} /></label>
                        <label className="grid gap-1 text-xs opacity-80">使用时长（秒）<InputNumber aria-label={`片段${index + 1}时长（秒）`} min={1 / 24} max={duration ? duration - clip.startSeconds : 1800} step={.1} precision={3} value={clip.durationSeconds} className="!w-24" disabled={busy} onChange={(value) => patch(index, { durationSeconds: Number(value || 0) })} /></label>
                        <div className="flex gap-1">
                            <Button type="text" size="small" aria-label={`上移片段${index + 1}`} disabled={!index || busy} icon={<ArrowUp className="size-4" />} onClick={() => move(index, -1)} />
                            <Button type="text" size="small" aria-label={`下移片段${index + 1}`} disabled={index === clips.length - 1 || busy} icon={<ArrowDown className="size-4" />} onClick={() => move(index, 1)} />
                            <Button type="text" size="small" aria-label={`移除片段${index + 1}`} disabled={busy} icon={<Trash2 className="size-4" />} onClick={() => setClips((items) => items.filter((_, i) => i !== index))} />
                        </div>
                    </div>;
                })}
            </div>
            <div className="flex items-center justify-between text-sm"><label className="flex items-center gap-2"><Switch aria-label="保留原音" size="small" checked={keepAudio} onChange={setKeepAudio} disabled={busy} />保留原音</label><span>{clips.length} 段 · 24fps · 总时长 {total.toFixed(2)} 秒</span></div>
            <details className="text-sm"><summary className="cursor-pointer opacity-70">片头、片尾与配乐（可选）</summary><div className="mt-3 grid gap-3">
                <Input aria-label="片头文字" placeholder="片头文字，留空不添加" value={openingText} maxLength={60} onChange={(event) => setOpeningText(event.target.value)} />
                <Input aria-label="片尾文字" placeholder="片尾文字，留空不添加" value={endingText} maxLength={60} onChange={(event) => setEndingText(event.target.value)} />
                <label className="flex items-center gap-2"><Switch aria-label="添加轻柔配乐" size="small" checked={score} onChange={setScore} disabled={busy} />添加轻柔原创配乐</label>
            </div></details>
            {error && <Alert type="error" title={error} showIcon />}
            {invalid && <p className="text-sm" style={{ color: theme.node.muted }}>请确认使用时长大于零，并且起点加使用时长不超过原片。</p>}
            <div className="flex items-center justify-end gap-3"><span className="mr-auto text-xs opacity-60">按 1/24 秒取整 · 最多 50 段 / 30 分钟</span><Button onClick={onClose} disabled={busy}>取消</Button><Button type="primary" loading={busy} disabled={!clips.length || invalid || total > 1800} onClick={() => void submit()}>一键合成</Button></div>
        </div>
    </Modal>;
}
