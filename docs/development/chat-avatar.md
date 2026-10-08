# 自定义聊天头像：实现与验收记录

对应 [issue #2810](https://github.com/Project-N-E-K-O/N.E.K.O/issues/2810)。实现基于 main `1d814cf32fba8c02b5727d2890710e4e7b8ece24`，开发分支为 `codex/issue-2810-chat-avatar`。验收日期：2026-10-07 至 2026-10-08。

## 功能与数据边界

网页浮层与完整聊天窗复用原头像预览弹窗，提供选择图片、裁剪、确认保存、取消及恢复模型头像。上传不依赖模型就绪。选图与裁剪仅产生候选图，服务端落盘成功后才更新正式头像；失败保留此前已确认的头像。

自定义头像由当前连接的 NEKO 后端保存，按稳定的 `character_uid` 归属。角色改名保留头像；删除后同名重建不会继承。连接同一后端的网页和 Electron 客户端共享记录。远程连接时数据存于远端，后端之间不自动同步。

显示优先级为 **引导临时头像 → 当前 UID 的自定义头像 → 原模型头像与缓存回退链**。`getCurrentAvatarDataUrl()` 提供最终显示头像；`getCachedPreview()` 只提供模型截图。恢复模型头像仅清除覆盖记录，保留角色模型和原截图。

| 数据或事件 | 职责与消费者 |
|---|---|
| `chat_avatars/<uid>.json` | 后端持久化的显示资源，含图片、版本与操作 ID |
| `chat-avatar-preview-updated` | 保留原模型截图、IPC 和 card-drop 链路 |
| `chat-avatar-display-updated` | 刷新 React 聊天中现有助手消息的显示头像 |
| `chat_avatar_changed` | 后端仅发送 UID、revision，客户端重新 GET |
| 模型缓存、角色卡封面、头像道具 | 继续使用原模型资源，不写入上传图片 |
| 聊天图片导出 | 沿用当前显示头像，历史消息不逐条冻结头像 |

不新增云同步、角色卡携带头像、社区发布或动图头像；不修改 `main_logic/`、`memory/`、根目录 `app/` 或 PC 仓库运行时代码。

## 接口合同

统一地址：`/api/characters/by-uid/{uid}/chat-avatar`，无尾斜杠。

| 方法 | 请求 | 成功结果 |
|---|---|---|
| GET | 路径中的角色 UID | 当前记录，未设置时仍为 200 |
| PUT | multipart：`image`、`base_revision`、`operation_id` | 已落盘的新记录 |
| DELETE | JSON：`base_revision`、`operation_id` | 图片为 null 的新版本记录 |

成功记录包含 `schema_version`、`character_uid`、`revision`、`data_url`、`last_operation_id`。响应另附 `limits`，供前端读取统一限制。初始版本为字符串 `"0"`；首次未设置时 `data_url` 与 `last_operation_id` 为 null。清除操作写入新版本，不退回初始版本。

前端接受静态 PNG、JPEG、WebP，原文件上限 10 MiB、2000 万像素，实际解码后裁剪为保留透明度的 320×320 PNG。PUT 只接受该规范化 PNG，提交上限 1 MiB。服务端重新完整解码、核对格式与尺寸并去除元数据。目录最终 JSON 记录合计配额为 64 MiB，计算包含 base64 和记录字段。

| 状态码 | 稳定错误码或既有行为 |
|---|---|
| 400 | `chat_avatar_invalid_request` |
| 401 / 403 | 沿用应用鉴权和既有写请求校验 |
| 404 | `chat_avatar_character_not_found` |
| 409 | `chat_avatar_conflict`、`chat_avatar_storage_changed`，或既有维护错误 |
| 413 | `chat_avatar_too_large` |
| 422 | `chat_avatar_invalid_image` |
| 500 | `chat_avatar_record_corrupt` |
| 503 | `chat_avatar_read_failed`、`chat_avatar_write_failed`、`chat_avatar_character_read_failed` |
| 507 | `chat_avatar_quota_exceeded` |

普通 404、接口不可用、角色不存在与未设置头像分开处理。读取或记录损坏不能触发自动覆盖。PUT、DELETE 复用既有 CSRF/Origin 校验；接口继承应用级鉴权，不额外限制为本机访问。

## 提交、身份与恢复

图片解析、实际解码在提交锁外完成。提交复用 `character_config_mutation_lock` 与 `cloudsave_writable_transaction()`，进入提交阶段重新核对数据根、角色 UID、维护状态和 `base_revision`，以原子文件工具一次替换 JSON。图片和文件 I/O 均在工作线程执行。请求取消后，提交线程物理结束才释放锁。

同一版本并发修改只有一个成功。当前记录仍属于同一 `operation_id` 且内容相同时，重复请求返回既有结果；后续操作已接管则冲突。保存超时先补读确认结果，无法确认时保留不确定状态，避免盲目重传。落盘成功后通知失败不回滚数据。

前端将已确认记录与候选编辑分开，每次编辑绑定 UID、身份代次、基准版本和操作 ID。异步读取、解码、裁剪、编码和保存结果都校验归属。角色切换开始清除当前显示归属，成功提交新 UID，失败或超时恢复原角色状态；相同模型不影响 UID 切换。

关闭、Esc、切换角色或重新选图取消未提交编辑。裁剪会话各自拥有清理函数；小图和极端长宽比的裁剪框与实际源像素均受限。上传编码保留透明度。Blob URL 在最后一次解码、编码结束后释放。连续选择相同文件也可重新处理。

跨窗口仅传失效通知，初始化、重连、角色切换、窗口激活和重新可见时补读，不增加高频轮询。Electron 使用现有原始 WebSocket 转发及聊天窗口就绪重检机制。

## 用户数据生命周期

`chat_avatars` 接入运行时目录迁移、本地用户内容识别和迁移源备份保留；不纳入云存档清单。迁移目标仅含头像数据时，也识别为已有用户数据。

角色普通删除、异常角色名救援删除和 Workshop 退订级联删除均在角色配置提交后清理对应 UID 记录。删除事务回滚保留头像；已提交后的请求取消仍完成清理。清理失败返回既有部分成功语义或清理错误摘要，不反转角色删除结果。不依据失败的角色列表请求清理“孤儿头像”。

回滚应用版本时保留新增目录，旧版本可忽略这些资源。前后端应配套发布；接口不可用时展示不可用状态，不降级写入模型缓存。

## 回归报告 / Regression Report

### 已实跑

| 验证层 | 结果与覆盖 |
|---|---|
| 定向 Python 回归 | 前端构建后提交前复验 268 通过、无跳过；覆盖新接口/存储、UID、迁移、PNGTuber、card-drop、聊天导出、React 适配及产物、locale、Workshop 删除和关闭云存档路径 |
| 新后端测试 | 63 个用例；新路由/存储/通知模块合计覆盖率 92.52% |
| 前端行为测试 | 52 个独立状态、图片、编辑和裁剪行为用例，新增模块行覆盖率均超过 96%；另有 13 个真实前端模块的 VM 集成场景，由 pytest 包装接入现有单测入口 |
| 静态检查 | 新 Python 文件 Ruff、API 尾斜杠、异步阻塞、日志规则及 `git diff --check` 通过 |
| Windows 文件系统 | 实际使用 Windows 文件占用阻止原子替换，确认旧记录保持可读；配额、权限和写失败另有故障注入 |
| 真实 Electron | Electron 41.2.0 / Chromium 146.0.7680.179，真实 renderer、三个 PC preload、实际 IPC router、生产 WebSocket 分发/重连、独立会话分区及真实 HTTP/WS 后端 |
| 正式开发环境人工测试 | 2026-10-08 完整执行 `build_frontend.bat`，以正式 launcher 启动后端；用户启动 Electron 实测并确认功能未发现明显问题 |

后端测试实跑：伪造图片头、损坏或动画 PNG、错误尺寸和请求字段、CSRF/Origin、并发版本冲突、幂等重试、记录损坏不覆盖、写失败保留旧记录、配额、角色删除/数据根切换与解码交错、取消持锁、通知失败、改名/同名重建、角色事务回滚/提交后取消、Workshop 退订和迁移备份。

前端测试使用受控 Promise/计时器验证迟到读取和响应、角色切换失败和 watchdog、编辑接管、取消和资源释放、超时补读、冲突与不确定结果恢复。模块集成场景覆盖真实角色切换函数、消息头像刷新、模型截图兜底、WebSocket 通知和重连。截图复核发现并修正了旧模型失败说明残留：自定义/候选图片隐藏模型说明，恢复默认或取消时恢复模型标题与说明，真实保存错误仍由独立状态区展示。

真实 Electron 验收覆盖：

1. 未加载模型时普通网页弹窗仍可上传。
2. 32px 透明图片裁剪、保存为 320×320 PNG，并通过真实 IPC 更新隐藏 full 窗口。
3. 上传头像不进入模型缓存和原模型广播；模型更新保留自定义显示，引导覆盖清除后恢复自定义显示。
4. 结束并重启后端进程后，读取同一磁盘记录及版本；聊天窗口重新加载后恢复记录并继续同步。
5. 恢复默认写入新版本，持有旧版本的窗口不能重新覆盖。
6. 4000×100 图片裁剪框不越界，关闭弹窗取消候选编辑。
7. 相同模型的不同 UID 切换与失败回滚保持归属。
8. 无 PC preload 的普通网页实际上传带 EXIF6 的 JPEG，方向为 40×80，保存并同步到原生聊天窗口；损坏 PNG 和真实键盘 Esc 均保留已确认头像。

### 证据边界

268 项为定向回归，不代表仓库全部测试或远端 CI 已通过。早期 267 通过、1 跳过来自未构建的 React bundle；完成正式前端构建后该产物断言已通过。人工测试记录来自用户实测反馈，不将未逐项报告的场景推定为已覆盖。

原生验收使用隔离的轻量 HTTP/WS 宿主，加载生产角色路由、ConfigManager、磁盘存储、写事务和通知注册表，前端通知及重连通过生产 `app-websocket.js`，省略模型与 LLM 引擎。PC 的 preload 与 IPC router 直接读取现有 PC 仓库；测试宿主按生产窗口管理器触发已有聊天窗口就绪重检。此证据验证原生窗口、真实网络、磁盘持久化和 IPC，不等同完整安装包启动或跨机器远程部署验收。

磁盘满、权限失败、通知故障、乱序/丢失和各异步交错主要使用故障注入与受控调度；Windows 文件占用、后端重启、页面重载、隐藏窗口及 EXIF 方向使用真实平台。尚未实跑断电、物理磁盘耗尽、触屏或其他操作系统的安装包。

GitNexus 已对改动的既有符号执行影响分析；为避免扩散，没有修改高风险 `StorageRootsMixin` 类，目录解析采用新增独立 helper。最终变更检查识别到预期的聊天连接流程，风险为 MEDIUM。图谱对属性赋值定义的部分 JS 函数及新增未索引文件存在覆盖限制，相应调用方另以源码和行为测试核对。

## 复现原生验收

需要已安装项目 Python 依赖以及含 Electron 的 PC 仓库。所有应用数据在系统临时目录隔离，现有后端和 PC 用户数据不参与；测试结束后保留日志、截图和后端记录以便审查。

```powershell
# 在本仓库根目录运行；将路径替换为本机 PC 仓库。
$env:NEKO_RUN_CHAT_AVATAR_ELECTRON = '1'
$env:NEKO_PC_ROOT = 'C:\path\to\N.E.K.O.-PC'
node --test tests/electron/chat-avatar-electron-acceptance.test.cjs
```

原生测试内部通过 `uv run --no-sync` 启动隔离宿主；若当前工作树没有虚拟环境，可显式设置 `UV_PROJECT_ENVIRONMENT` 为已安装同版依赖的环境。运行结束输出 `Native acceptance artifacts` 路径，包含 `acceptance.log`、`profile/saved-avatar.png` 和隔离后端数据。
