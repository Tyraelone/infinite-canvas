# Local Runtime Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. The user requested direct completion and verification.

**Goal:** 本机视频、生图和 Codex 文本能力在无限画布中实际可用。

**Architecture:** aiohttp 本机网关复用现有 H3 工作室与 Qwen 工作流；本机 Codex app-server 负责文本及图像生成。Next.js 同源代理与默认本机配置对接原有模型选择器。

**Tech Stack:** Python aiohttp/Pillow、Codex JSON RPC、Next.js/React/TypeScript、现有 Go 后端。

**Spec:** `docs/superpowers/specs/2026-10-27-local-runtime-design.md`

## Global Constraints

- 使用现有 embedded Python 和模型，不复制模型，不中断已有任务。
- 本项目新增服务只监听回环地址；后端 8081，前端 3000，网关 8191。
- 页面文案中文；沿用模型选择器与画布主题。
- 用户已授权修改、依赖、启动及实测；直接在指定本地目录完成，保留可审查 diff，不发布。

## Review Focus

- 错误模型、非法尺寸和时长必须得到明确错误。
- 图片参考输入不能被丢弃，路径不能逃出媒体目录。
- Codex 未登录、推理失败和无图像输出不能误报成功。
- 关闭或取消请求不得终止其他服务或其他任务。
- 自动配置不能覆盖已有用户渠道，非本机模式不改变行为。

### Task 1: 本机网关

**Files:** `local-runtime/gateway.py`, `local-runtime/codex_rpc.py`, `local-runtime/tests/test_gateway.py`

**Interfaces:** `create_app()` 提供 OpenAI 兼容媒体/文本接口；`CodexSession.run()` 返回文本、工具调用和生成图像路径。

- [x] 写并运行契约测试，确认当前缺少实现。
- [x] 实现模型目录、视频任务映射、Qwen 排队、结果文件与请求边界。
- [x] 实现 Codex JSON RPC、文本流、图像结果与错误处理。
- [x] 运行网关测试并记录结果。

### Task 2: 画布与启动

**Files:** `web/src/app/api/local-runtime/[...path]/route.ts`, `web/src/services/api/local-runtime.ts`, `web/src/components/layout/client-root-init.tsx`, `Start-Local.ps1`, `Stop-Local.ps1`, 中文 CMD 入口及本地使用说明。

**Interfaces:** 本机 profile 描述模型和能力；自动配置使用既有 `useConfigStore`；启动脚本写 `data/local-runtime/processes.json` 并检查进程归属。

- [x] 补齐前后端运行依赖，使用 8081 避开 Steam。
- [x] 实现同源代理与仅本机启用的默认渠道。
- [x] 实现可重复运行的启动、状态与按身份停止脚本。
- [x] 构建并通过浏览器核对模型选择和对话入口。

### Task 3: 真实验收

**Files:** `local-runtime/verify_live.py`, `docs/progress/pending-test.md`, `data/local-runtime/verification.json`

- [x] 实际调用 Codex 文本、Qwen 生图、Codex 生图和 H3 视频。
- [x] 从画布提交并检查结果节点，图片尺寸与视频解码。
- [x] 保存验收记录、更新文档，完成独立代码审查并修复重要问题。
