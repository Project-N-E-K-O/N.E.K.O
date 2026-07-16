# N.E.K.O. 与 QwenPaw 集成

## 名称与边界

当前代码中，`OpenClaw` 是 N.E.K.O. 内部保留的兼容名称，实际连接的外部服务是 QwenPaw。适配器位于 `brain/openclaw_adapter.py`，Agent 服务器通道位于 `app/agent_server/channels/openclaw.py`。

N.E.K.O. 不在本地实现 QwenPaw 的工具执行环境，也不管理其服务进程。它负责能力探测、任务路由、请求格式转换、会话标识持久化、结果清洗与向前端发送任务事件；QwenPaw 负责远端 Agent 执行。服务的安装与启动请使用 UI 中的 OpenClaw 接入教程。

## 运行链路

```text
用户消息
  -> Agent 分析器评估 qwenpaw 通道
  -> TaskExecutor 选择 execution_method=openclaw
  -> app/agent_server/channels/openclaw.py 注册后台任务
  -> brain/openclaw_adapter.py 调用 QwenPaw HTTP API
  -> 清洗最终回复并更新 task_registry
  -> task_result / task_update / proactive_message 事件返回主界面
```

非插件通道的选择优先级是 `qwenpaw`、`openfang`、`browser_use`、`computer_use`。只有 `openclaw_enabled` 已开启且能力探测返回 ready 时，QwenPaw 才会进入可选通道。开启开关后，Agent 服务器会进行有界重试；探测失败会撤销开关并向 UI 返回原因，而不是把不可用服务当作可执行能力。

普通任务在 `task_registry` 中以 `type: "openclaw"` 注册，并通过独立 `asyncio` 后台任务执行。成功、失败与取消都会写回任务状态并发送统一 Agent 事件。

## 服务地址与配置

默认服务根地址为：

```text
http://127.0.0.1:8088
```

适配器从核心配置读取以下首选键，同时兼容旧 `OPENCLAW_*`/camelCase 名称：

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `QWENPAW_URL` | `http://127.0.0.1:8088` | 服务根地址；也可传入受支持的完整端点，适配器会还原根地址 |
| `QWENPAW_TIMEOUT` | `300` 秒 | 单次任务超时 |
| `QWENPAW_AUTH_TOKEN` | 空 | 同时发送为 `Authorization: Bearer ...` 与 `x-openclaw-token` |
| `QWENPAW_DEFAULT_SENDER_ID` | `neko_user` | 无法从消息解析用户时的发送者 |
| `QWENPAW_CHANNEL` | `console` | 下游 channel 字段 |

HTTP 客户端明确禁用环境代理（`trust_env=False`），避免本机回环请求被系统代理劫持。

## 协议探测与降级

适配器同时支持 QwenPaw v2 控制台接口和旧兼容接口。

| 用途 | 端点 | 格式 |
| --- | --- | --- |
| v2 能力探测 | `GET /api/version` | 必须返回带 `version` 的 JSON 对象 |
| 旧能力探测 | `GET /api/agent/health` | 成功状态即可 |
| v2 任务 | `POST /api/console/chat` | SSE |
| 旧 Responses 兼容任务 | `POST /api/agent/compatible-mode/v1/responses` | JSON |
| 旧 process 任务 | `POST /api/agent/process` | SSE |

能力探测优先 `/api/version`，失败后尝试旧 health；一旦识别为旧服务，后续探测会优先旧端点。任务发送顺序也根据已识别版本调整：v2 优先 console，旧服务优先 Responses/process。传输错误、5xx、404 或 405 可以继续尝试兼容端点；认证错误等其他客户端错误会直接报告。

SSE 解析器保留最后一个实际 response/message 事件，不会让尾部 usage 事件覆盖最终回复。适配器从多种兼容响应结构提取 assistant 文本，并移除 `<think>`、ReAct `Thought:`/`Action:` 等推理痕迹；没有最终文本时任务视为失败。

## 请求结构与附件

适配器内部统一接收：

```python
await adapter.run_instruction(
    instruction,
    attachments=attachments,
    sender_id=sender_id,
    session_id=session_id,
    role_name=lanlan_name,
)
```

不要在业务代码中直接拼接某个版本的 HTTP payload。适配器会为 Responses 兼容接口使用 `input_text`/`input_image`，为 console/process 使用 `text`/`image`，并传入 `session_id`、`channel` 与需要时的 `user_id`。

附件当前按图片 URL 处理。每项可以是 URL 字符串，也可以是含 `url`、`image_url` 或 `data_url` 的对象。没有文字、只有图片时，适配器会补充一条要求分析图片的文字指令。未经适配器支持的任意文件类型不应伪装成图片附件。

## 会话与多用户隔离

会话映射由适配器持久化到配置目录中的 `openclaw_sessions.json`，当前键格式是：

```text
user::{sender_id} -> session_id
```

同一 `sender_id` 会持续复用同一 QwenPaw 会话，不再按角色名拆分。旧版 `{role}::{sender}` 键会在首次读取时迁移到新格式。没有可用 sender 时使用 `neko_user`；主动触发的任务也固定使用默认 sender，避免误用消息窗口中上一个用户的会话。

`/new` 会先为当前 sender 生成并保存新的会话 ID，再把命令发送到 QwenPaw。不要在其他模块维护第二份 user-to-session 字典。

## 魔法命令与取消

支持的命令为：

- `/clear`：清除当前 QwenPaw 上下文；
- `/new`：切换到新的持久会话；
- `/stop`：取消当前用户的 N.E.K.O. 后台任务，并通知 QwenPaw；
- `/daemon approve`：批准当前 QwenPaw 高风险动作。

`TaskExecutor` 会先执行零 LLM 规则识别，覆盖精确命令和少量明确自然语言表达；在合适的用户文本路径上，适配器还可以使用小模型进行保守分类。带附件的请求不会进入魔法命令前置拦截，以免把多模态任务误判成控制命令。

`/stop` 的即时效果是取消匹配 sender 的本地 `asyncio` 任务并把任务状态写为 cancelled。适配器的 `stop_running()` 记录的是客户端侧取消语义；不能据此假设任意版本的 QwenPaw 都提供独立的远端停止端点。

## 前端与可观测性

主界面通过 `openclaw_enabled` 开关启用该能力，并通过能力快照显示 pending、ready 或不可用原因。通道会发送：

- `proactive_message`：任务接受提示；
- `task_update`：running/completed/failed/cancelled 生命周期；
- `task_result`：经过清洗的最终回复或错误摘要。

不要绕过这些事件直接向旧聊天 DOM 写消息。聊天 UI 的唯一实现是 React，遗留消息 API 由 `app-chat-adapter.js` 桥接。

日志与错误中应保留 HTTP 状态、端点版本和任务 ID 等诊断信息，但不要记录认证 token 或未经处理的敏感附件内容。

## 修改检查清单

1. 协议变更集中在 `brain/openclaw_adapter.py`，不要在通道层复制 payload 构造。
2. 路由、任务登记、取消与 UI 事件变更集中在 `app/agent_server/channels/openclaw.py`。
3. 新增协议版本时保留 v2 与旧接口的对偶探测/降级，并补充 `tests/unit/test_openclaw_adapter_protocol.py`。
4. 会话变更必须验证同用户复用、不同用户隔离、旧键迁移、`/new` 与主动任务。
5. 魔法命令必须同时验证误触发与漏触发；涉及产品语义扩大时再增加短语。
6. 前端用户可见文案变更必须同步 8 个 `static/locales/*.json`。
7. 不要把示例人格 prompt、第三方部署脚本或未经源码实现的 endpoint 写成 N.E.K.O. 公共契约。
