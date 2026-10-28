# Shared Local Workspace Implementation Plan

> **For agentic workers:** Use independent parallel tasks with a final independent review; parent integrates and performs real migration and UI acceptance.

**Goal:** 本机画布/素材统一保存到 D 盘，迁移现有内容，跨浏览器可用，画布可直接视频超分。

**Architecture:** Go handles revisioned shared state and content-addressed media. Existing Next local proxy handles loopback/Token boundary. Browser persistence adapter imports legacy browser state before returning shared state. Python gateway reuses the current studio upscale APIs.

**Tech Stack:** Go/Gin/GORM/SQLite, Next.js/TypeScript/localforage, existing aiohttp inference gateway.

**Spec:** `docs/superpowers/specs/2026-10-27-shared-workspace-design.md`

## Global Constraints

- 不删除或覆盖现有浏览器数据；迁移先备份、校验、事务发布。
- 项目及共享文件均位于 D 盘；只在本机运行模式开放，无需登录。
- 存储和超分代理分工互不修改对方拥有的文件；不自行停止共享 GPU 服务或提交重复推理。
- 用户已明确批准直接实施、迁移及验收。

## Review Focus

- 确认新浏览器没有本地 Blob 也能显示和播放全部媒体。
- 迁移失败和过期版本不能覆盖已保存数据。
- 另一浏览器删除/清理缓存不能物理删除共享媒体。
- localhost 与 127.0.0.1 映射到同一份后端内容；同源边界仍有效。
- 已完成视频超分保留原片、音频和帧数，不静默截断。

### Task 1: Go 共享磁盘后端

**Owner:** storage backend agent.
**Files:** `model/local_workspace.go`, `repository/local_workspace.go`, `service/local_workspace.go`, `handler/local_workspace.go`, their tests, repository migration registration and database documentation.
**Interface:** `handler.RegisterLocalWorkspaceRoutes(r gin.IRouter)` registers `/api/local-workspace/state/{canvas|assets}`, `/files/{key}`, `/import`, `/status`. Uses existing `{code,data,msg}` envelope and `CANVAS_LOCAL_MODE`/`CANVAS_LOCAL_TOKEN` boundary. Parent adds its router invocation.

- [x] 写先失败的持久化、版本冲突、导入回滚、幂等、文件哈希和 Range 测试。
- [x] 实现并通过相关 Go 测试。

### Task 2: H3 超分入口

**Owner:** video upscale agent.
**Files:** Existing Python gateway/video tests and video-specific TS/UI/config metadata files only.
**Interface:** `videoUpscale` option in config, node metadata and request; POST `/v1/videos/{id}/upscale` for completed owned H3 video; response compatible with existing local video poll/cancel.

- [x] 实现生成后 2× 超分和原片后处理入口，沿用现有工作流限制。
- [x] 回归测试请求与超分任务映射，不启动实际 GPU 推理或服务。

### Task 3: 浏览器迁移与共享适配

**Owner:** parent.
**Files:** `web/src/services/local-workspace.ts`, `web/src/lib/localforage-storage.ts`, `web/src/services/image-storage.ts`, `web/src/services/file-storage.ts`, canvas/asset persistence integration, local proxy and migration UI status.

- [x] 接通同源 Go 代理；实现自动备份、媒体上传校验、事务导入。
- [x] 原状态键/媒体键保持不变，读写走共享后端；失败给出可见提示。
- [x] 测试原始 Blob URL 替换、过期版本和服务器故障处理。

### Task 4: 真实迁移与跨浏览器/超分验收

**Owner:** parent plus final independent reviewer.

- [x] 迁移现有内置浏览器状态，并核对 ID、计数、媒体哈希。
- [x] 从另一实际浏览器读取、播放、编辑；重启验证持久化。
- [x] 实际超分现有 5 秒视频，验证翻倍分辨率、完整帧数和非零音频。
- [x] 更新使用说明、pending-test、数据库说明和验收记录；处理审查发现。
