# 自定义聊天头像

对应 [issue #2810](https://github.com/Project-N-E-K-O/N.E.K.O/issues/2810)。

## 功能与数据归属

网页浮层与完整聊天窗复用头像预览弹窗，支持选图、裁剪、保存、取消及恢复模型头像，无需等待模型就绪。选图和裁剪只产生候选图，服务端落盘成功后才更新正式头像；失败保留此前已确认的头像。

记录由当前连接的 NEKO 后端按稳定的 `character_uid` 保存。角色改名保留头像，删除后同名重建不会继承。连接同一后端的网页与 Electron 客户端共享记录；远程连接的数据存于远端，不在后端之间自动同步。

显示优先级为 **引导临时头像 → 当前 UID 的自定义头像 → 原模型头像与缓存回退链**。恢复模型头像写入图片为空的新版本记录，保留角色模型与原截图。

| 资源或事件 | 职责与消费者 |
|---|---|
| `chat_avatars/<uid>.json` | 后端持久化记录，含图片、版本与操作 ID；头像接口是唯一权威 |
| `getCurrentAvatarDataUrl()` | 提供最终显示头像，供 React 消息与聊天图片导出使用 |
| `getCachedPreview()`、`chat-avatar-preview-updated` | 保留模型截图、IPC 与 card-drop 链路，不写入上传图片 |
| `chat-avatar-display-updated` | 刷新现有助手消息的显示头像；历史消息不逐条冻结头像 |
| `chat_avatar_changed` | 后端仅发送 UID、revision，客户端重新 GET |

自定义头像不进入模型缓存、角色卡封面或头像道具，也不纳入云存档、角色卡导出或社区发布。不支持动图。

## 接口与限制

统一地址：`/api/characters/by-uid/{uid}/chat-avatar`，无尾斜杠。接口继承应用鉴权，PUT、DELETE 复用既有 CSRF/Origin 校验。

| 方法 | 请求 | 成功结果 |
|---|---|---|
| GET | 路径中的角色 UID | 当前记录，未设置时仍为 200 |
| PUT | multipart：`image`、`base_revision`、`operation_id` | 已落盘的新记录 |
| DELETE | JSON：`base_revision`、`operation_id` | 图片为 null 的新版本记录 |

成功记录包含 `schema_version`、`character_uid`、`revision`、`data_url`、`last_operation_id`，响应另附统一的 `limits`。初始版本为字符串 `"0"`；首次未设置时，`data_url` 与 `last_operation_id` 为 null。清除操作递增版本，不退回初始版本。

前端接受静态 PNG、JPEG、WebP，原文件上限 10 MiB、2000 万像素。解码、处理 EXIF 方向后，裁剪为保留透明度的 320×320 PNG。PUT 只接受该规范化 PNG，提交上限 1 MiB；服务端完整解码、核对格式与尺寸并去除元数据。目录内最终 JSON 记录合计配额为 64 MiB，包含 base64 和记录字段。

| 状态码 | 错误码或既有行为 |
|---|---|
| 400 | `chat_avatar_invalid_request` |
| 401 / 403 | 应用鉴权和写请求校验 |
| 404 | `chat_avatar_character_not_found` |
| 409 | `chat_avatar_conflict`、`chat_avatar_storage_changed`，或既有维护错误 |
| 413 | `chat_avatar_too_large` |
| 422 | `chat_avatar_invalid_image` |
| 500 | `chat_avatar_record_corrupt` |
| 503 | `chat_avatar_read_failed`、`chat_avatar_write_failed`、`chat_avatar_character_read_failed` |
| 507 | `chat_avatar_quota_exceeded` |

接口不可用、角色不存在与未设置头像分别处理；读取失败或记录损坏不能触发自动覆盖。

## 提交、取消与同步

图片解码在提交锁外完成。提交复用 `character_config_mutation_lock` 与 `cloudsave_writable_transaction()`，重新核对数据根、角色 UID、维护状态和 `base_revision`，再原子替换 JSON。图片与文件 I/O 在线程执行；请求取消后，提交线程结束才释放锁。

同一版本的并发修改只有一个成功。同一 `operation_id` 且内容相同的重试返回既有结果，后续操作已接管则冲突。保存超时先补读确认，无法确认时保留不确定状态，避免盲目重传。落盘后的通知失败不回滚数据。

前端将已确认记录与候选编辑分开，每次编辑绑定 UID、身份代次、基准版本和操作 ID；异步结果提交前校验归属。角色切换成功后使用新 UID，失败或超时恢复原角色状态，相同模型也需切换 UID。

关闭弹窗、Esc、切换角色或重新选图取消未提交编辑；已发送或结果不确定的操作继续确认。裁剪会话负责自身资源清理，Blob URL 在最后一次解码、编码结束后释放，小图和极端长宽比的裁剪范围均受限。

初始化、重连、角色切换、窗口激活和重新可见时补读，不增加高频轮询。头像模块首次角色列表请求使用 15 秒期限，超时明确报错，激活或重连可重试。Electron 复用现有 WebSocket 转发与聊天窗口就绪重检机制。

## 删除、迁移与部署

普通角色删除、异常名称救援删除和 Workshop 退订级联删除，均在角色配置确认提交后清理对应 UID 记录。事务回滚保留头像，提交后的请求取消仍完成清理。清理失败按既有部分成功语义或清理错误摘要报告，不反转角色删除结果；角色列表请求失败不能作为清理孤儿记录的依据。

`chat_avatars` 属于本地用户数据，参与运行时目录迁移、用户内容识别和迁移源备份保留。迁移目标仅含头像数据时，也视为已有用户数据。回滚应用版本时保留此目录，旧版本可忽略它。

网页浮层与完整聊天窗加载同一套前端模块，前后端需配套发布；接口不可用时展示不可用状态，不降级写入模型缓存。历史异常名称删除事务、全局 `pageConfigReady` 协议和 PC preload/IPC 沿用既有实现。

## 验证入口

后端回归见 `tests/unit/test_chat_avatar_*.py`，前端行为与集成测试见 `tests/unit/chat_avatar_*.test.cjs` 和 `tests/frontend/chat_avatar_integration.test.cjs`。重点覆盖版本冲突、幂等、取消持锁、角色切换归属、写入失败、删除回滚、迁移及窗口重连。

原生验收需要已安装项目 Python 依赖，以及含 Electron 的 PC 仓库。在本仓库根目录运行：

```powershell
$env:NEKO_RUN_CHAT_AVATAR_ELECTRON = '1'
$env:NEKO_PC_ROOT = 'C:\path\to\N.E.K.O.-PC'
node --test tests/electron/chat-avatar-electron-acceptance.test.cjs
```

测试通过 `uv run --no-sync` 启动隔离后端，使用生产角色路由、存储、通知和 PC preload/IPC，覆盖上传、透明度、EXIF、隐藏窗口同步、后端重启、恢复默认及取消。数据存于系统临时目录，结束时输出 `Native acceptance artifacts` 路径，保留日志、截图和后端记录。工作树没有虚拟环境时，可通过 `UV_PROJECT_ENVIRONMENT` 指定已安装同版依赖的环境。

隔离后端不加载模型与 LLM 引擎；这些测试不替代完整安装包、跨机器部署、触屏或断电验收。CI 结果以具体提交为准。
