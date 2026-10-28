# Video Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在画布和Agent中完成可排序、裁剪、落盘并自动生成节点的视频合成。
**Architecture:** Python网关管理持久任务并启动PyAV子进程，Go现有共享文件API读写媒体；前端共用提交/轮询Hook，工具栏弹窗和Agent共用接口。
**Tech Stack:** Python/PyAV/aiohttp/SQLite；Next.js/React/TypeScript/Ant Design。
**Spec:** docs/superpowers/specs/2026-10-28-video-composition-design.md

## Global Constraints

- 当前工作区包含必要未提交集成，原地追加；保留原片、原画布与既有代码，不提交/推送。
- 复用已安装依赖；服务只监听回环；保持共享ComfyUI、影格和Steam进程不变。
- 1—50段、总计最多1800秒、24fps、首片画幅、无越界补帧；仅媒体键、不接受文件路径/任意URL。
- UI中文并沿用主题；同一接口供UI/Agent调用，任务完成必须经过共享发布。

## Review Focus

- 页面关闭、刷新和网关重启：任务不伪完成，已保存节点可恢复查询，中断可重试。
- 单段无音轨、混合帧率/尺寸和非零起点：输出帧序、裁剪范围和音画时长正确。
- 双击、重复requestId、发布失败：幂等且失败不暴露成片。
- 取消与服务退出：只停止自有编码子进程，不触碰共享GPU。
- 恶意storageKey、任意路径/URL、过长时长：入口拒绝，不能读取工作区外任意文件。

### Task 1: 编码与持久任务接口

**Files:** Create local-runtime/composition.py, local-runtime/composition_jobs.py, local-runtime/tests/test_composition.py; Modify local-runtime/gateway.py.
**Interfaces:** render_composition(manifest, progress)；POST/GET/DELETE /v1/compositions；完成返回storageKey/url/width/height/durationSeconds/frames/bytes/sha256。

- [x] 写真实视频夹具测试：顺序、非等长裁剪、静音、混合规格、越界拒绝；先观察失败。
- [x] 实现PyAV滤镜裁剪/帧率转换、缩放留边、AAC音轨、原子输出和进度。
- [x] 写异步HTTP任务测试：幂等、发布失败、恢复、非法来源；先观察失败。
- [x] 实现任务队列、子进程生命周期、共享文件读写、取消与重启中断。
- [x] 跑Python完整测试，Expected: 全部通过，记录命令/数量。

### Task 2: 共用前端流程与Agent工具

**Files:** Create web/src/services/api/video-composition.ts, web/src/app/(user)/canvas/hooks/use-video-composition.ts, web/src/app/(user)/canvas/utils/canvas-video-composition.ts; Modify canvas/types.ts, agent/canvas-agent-tools.ts, agent/skills/core.ts, [id]/canvas-client-page.tsx, services/local-workspace.ts.
**Interfaces:** CompositionOptions clips[{nodeId,startSeconds,durationSeconds}]；submitComposition(options,retryNodeId?)返回真实结果节点；compositionTaskId独立于H3视频ID。

- [x] 写Agent规范化、来源校验、顺序/裁剪参数测试并观察失败。
- [x] 实现共用提交/轮询、独立结果节点、刷新恢复、重试/取消、制作租约任务签名。
- [x] 注册compose_videos及任务查询；返回真实提交状态，保留所有源节点。
- [x] 跑前端完整本机测试，Expected: 全部通过。

### Task 3: 合成列表与真实验收

**Files:** Create canvas/components/canvas-video-composition-dialog.tsx; Modify canvas/components/canvas-toolbar.tsx, canvas/[id]/canvas-client-page.tsx, docs/progress/pending-test.md, 本机运行说明.md, docs/backend/backend-database.md.

- [x] 工具栏入口和弹窗：从画布选择，预选当前视频，移动顺序、移除、设置起点/时长、总时长、输出名及声音/片头片尾选项。
- [x] 构建前端并只重启画布自有web/gateway，保留共享GPU/影格；Expected: 页面新入口可见。
- [x] UI用真实H3素材调整顺序、裁剪并提交，保存结果节点；检查播放至末尾、D盘文件、哈希、时长/帧数及原片未变。
- [x] 实际画布Agent调用compose_videos再次合成，刷新恢复节点和媒体。
- [x] 独立完整改动审查，修复重要问题并补回归；更新文档与账本。Expected: 实际UI/Agent输出均可播放，未遗留伪完成状态。

## Ledger

- Ruling: 原地追加已有本机集成，不新建不含这些改动的worktree，也不提交用户已有改动；否则当前运行环境无法验证这次功能。
- Ruling: 用户已明确“直接接进、做完实际验证”，无需再次确认设计或编辑授权；实现者选择上述轻量合成列表和CPU任务方案。

- Task 1 complete: Python 36/36通过；真实编码与HTTP任务 RED→GREEN。命令：python -m unittest discover -s local-runtime/tests -p test_*.py。
- Task 2 complete: 前端31/31通过；Agent参数与顺序、来源校验、旧任务保护 RED→GREEN。tsc只剩原有9处上传targetToken类型错误，新增代码无类型错误。

- Final review: 独立只读审查发现4项Important；已在同一修复批次处理，未遗漏Minor。
- Final fixed: 跨任务类别requestId覆盖 — 原H3登记保护测试202→400 RED→GREEN；Python37/37。
- Final fixed: 覆盖生成保留合成身份 — 清除身份后旧任务不能恢复旧片 RED→GREEN；实际生成路径在loading前及新videoNode合并时均清除。
- Final fixed: 刷新失去租约 — 每标签页sessionStorage保留客户端ID，恢复轮询先取得并持有租约；423保留草稿但不永久锁死。身份及423恢复测试 RED→GREEN。
- Final fixed: 提交早于节点持久化 — POST前必须等待pending节点的共享PUT确认，失败不提交后台任务；保存确认与失败保护 RED→GREEN。前端35/35。
- Ruling: sessionStorage仅保存极小标签页身份；不同浏览器仍有独立身份，CAS继续拒绝过期写入。若浏览器禁止sessionStorage，回退临时身份，可能需要等旧租约过期再刷新。
- Ruling: 画布源目录、素材与H3任务登记保留；新增-SkipSharedEngines仅为CPU合成独立启动，当前不启动已关闭的共享GPU服务。
- Final review set-asides: 实际UI/Agent播放、主观音画、进程取消与刷新验收由主代理执行；不将旧集成和9处既存类型错误归入本次新增缺陷。未证明的外部资源容器问题不作为已验证漏洞；API仍只接受共享媒体键，不接受用户路径/URL。

- Task 3 complete: 按钮真实重排02/01/12，裁剪1+3、2+4、0+2，结果9秒216帧；POST成功后立即刷新仍自动完成并共享落盘。实际Agent调用compose_videos并查询完成，07/12结果4秒96帧。两份浏览器均ended=true、readyState=4，完整解码、源帧像素比对与Range206通过。
- 真实取消：观察编码PID6404，DELETE后自有PID消失，任务cancelled且不暴露URL；原片与原180秒影片哈希未变。
- 重启验收：只重启自有gateway，两个完成任务与33个制作节点、原画布5节点、既有H3登记保持；D盘共享文件33个。
- Ruling: 非已证明的外部引用容器不报告漏洞；为履行固定共享来源边界，解码只开放MP4/MOV、MKV/WebM、AVI、TS和file协议。伪装MP4的有效GIF拒绝测试RED→GREEN，Python38/38；代价是不接受其他容器。
- Final verification: 前端35/35、Python38/38；生产构建成功；tsc仅既有9项targetToken可选字符串错误，未声称全量类型检查通过。未修改Go业务代码，todo无对应未完成事项，已更新pending-test与运行/存储说明。
- Delivery: 保持当前目录中的改动及全部原有未提交工作，不创建提交、不合并、不推送。独立审查的重要问题均已修复，无延期Minor。

- 实际收尾发现两次自动合成同坐标重叠，新增自动避让其他节点，保留重试节点原位置。位置回归RED→GREEN，前端36/36；正在验证部署后的自动放置。

- 自动避让实测通过：新2秒48帧结果无任何节点碰撞，旧两份结果已单独排开；制作画布34节点、D盘共享34文件。额外真实WebM30fps无音轨输入转换为24fps MP4通过。最终证据：data/local-runtime/composition-acceptance/verification.json。
