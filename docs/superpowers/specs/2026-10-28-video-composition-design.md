# 本机画布视频合成

用户明确要求直接实现并实际验证：选视频、排序、设置时长、一键合成，Agent能调用，成片存D盘并新增画布节点。沿用当前未提交的本机接入和已迁移工作区，不覆盖既有成果，不提交或推送Git。

## 方案

采用现有Python网关的持久化任务和PyAV编解码能力。浏览器内编码会受关闭页面、内存和大文件影响；新增Go编解码进程管理会重复网关职责。UI和Agent使用同一POST/GET/DELETE合成API；Go共享文件接口继续负责权威媒体存储。此取舍由实现者决定，用户已要求直接实施。

## 行为

- 工具栏“合成视频”打开列表，预选当前选中的完成视频；可从当前画布添加、移除、上下移动，每段设置起点和使用秒数，显示总时长。
- 输入1至50段，总时长最多1800秒，秒数按24fps取最近帧。输出以第一段画幅统一缩放并留边、不拉伸；常见帧率统一24fps。缺帧、损坏、越界或不存在的来源报错，禁止超出源片冻结/循环补时。
- 输出名、保留原音可配置；可选片头、片尾文字和轻柔配乐，默认不额外添加。无音轨来源以静音段衔接。
- POST /v1/compositions 接收requestId、title、clips[{storageKey,startSeconds,durationSeconds}]、keepAudio、openingText、endingText、score。GET/DELETE /v1/compositions/{id}读取或取消。
- 来源只能是Go共享存储中的媒体键，不接受文件路径、任意URL或用户指定输出路径。网关带现有本机凭据读取/发布，浏览器不接触凭据。
- 任务以gateway.sqlite的composition类别保存，单独CPU队列不占用GPU队列。异步子进程编码，进度可查，取消/服务退出只终止本任务子进程。重启将未完成任务标为中断失败，可显式重试。requestId幂等，避免网络重试重复编码。
- 中间文件在data/local-runtime/compositions/{id}，完成文件发布为video:composition:{id}到data/workspace/files；发布成功前不得报告完成。原片不变。
- 前端先保存含compositionTaskId和参数的独立结果节点；轮询填入真实共享URL/尺寸/时长。刷新可恢复轮询，失败可重试，停止可取消；提交和媒体期间沿用制作租约。
- Agent新增compose_videos工具，clips使用真实nodeId并保留数组顺序；与UI共用提交函数。get_generation_task/get_media_task_status能读取合成节点的真实状态。不能将已提交说成已完成。

## 验收

真实编码测试覆盖裁剪/顺序/不等时长、混合帧率尺寸、静音、越界和原片保护。HTTP任务测试覆盖持久化、幂等、发布失败、中断、路径/URL拒绝。前端契约测试覆盖Agent参数与来源解析。实际UI用现有H3素材排序裁剪合成、播放至末尾；再由实际画布Agent调用合成，校验D盘文件、哈希、帧数、时长和刷新恢复。
