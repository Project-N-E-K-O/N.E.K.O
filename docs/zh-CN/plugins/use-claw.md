# Agent 自动化渠道、QwenPaw 与纠正反馈

> 本页同时维护[英文版](/plugins/use-claw)与简体中文版，暂无日文镜像。它描述的是 N.E.K.O 当前仓库中的 Agent 路由实现，不是插件 SDK 教程，也不是 QwenPaw/OpenClaw 的安装手册。

N.E.K.O 的 Agent 可以把任务交给用户插件、浏览器自动化、桌面自动化或外部 Agent 服务。这里最容易混淆的三件事是：**渠道选择**、**任务执行**和**纠正反馈**。它们分别由不同模块负责。

## 当前实现概览

| 能力 | 当前状态 | 代码归属 |
|---|---|---|
| 用户插件选择 | 已实现 | `brain/plugin_filter.py`、`brain/task_executor.py` |
| Browser Use / Computer Use 统一评估 | 已实现 | `brain/task_executor.py` |
| QwenPaw（兼容名 OpenClaw）接入 | 已实现适配层；服务本体是外部依赖 | `brain/openclaw_adapter.py`、`app/agent_server/channels/openclaw.py` |
| 任务登记、状态与取消 | 已实现 | `app/agent_server/registry.py`、`app/agent_server/api_runtime.py` |
| Browser Use / Computer Use 纠正反馈 | 已实现 | `brain/task_executor.py`、`app/agent_server/api_runtime.py` |
| 将纠正自动扩展到 QwenPaw、OpenFang 或用户插件 | 未实现 | 若要支持，需要新增产品和数据边界设计 |

Agent Server 当前实现为 `app/agent_server/` 包，不是单文件模块。源码引用应使用上表中的实际包模块。

## 渠道选择

### 用户插件是独立的两阶段路径

用户插件不会与 Browser Use、Computer Use、QwenPaw 共用同一套 entry 选择逻辑：

1. 插件描述较短时，直接把所有具备 Agent 可见 entry 的插件交给第二阶段。
2. 超过配置的 token 阈值时，第一阶段并行执行 BM25、LLM 粗筛，并合并 manifest `keywords` 命中项。
3. 第二阶段 LLM 读取候选插件的完整描述，返回 `plugin_id` 和运行时 `entry_id`。
4. Host 严格校验这两个 ID 是否来自本次候选集。第一次不合法时会带纠正提示重试一次；仍不合法就强制 `can_execute = false`。

这里的运行时 `entry_id` 来自 `@plugin_entry(id=...)` 或动态 entry。它与 `plugin.toml` 中用于导入 Python 类的 `[plugin].entry = "module.path:ClassName"` 完全不同。

### 非插件渠道共用一次评估

`TaskExecutor` 会根据实际可用性动态组装 QwenPaw、OpenFang、Browser Use 和 Computer Use 的候选描述，并用一次 LLM 调用评估这些渠道。若结果同时选择多个渠道，当前固定优先级是：

```text
QwenPaw > OpenFang > Browser Use > Computer Use
```

QwenPaw 的 `/clear`、`/new`、`/stop`、`/daemon approve` 还具有独立的魔法命令识别路径。普通自然语言任务仍走可用性检查和统一渠道评估。

## QwenPaw / OpenClaw 的边界

代码中的 `OpenClawAdapter` 是兼容接口名称；它当前连接的是**外部 QwenPaw 服务**。N.E.K.O 仓库负责：

- 读取 QwenPaw/OpenClaw 兼容配置；
- 探测健康状态；
- 适配 legacy Responses-compatible API 与 v2 console streaming API；
- 维护 sender 到外部 session 的本地映射；
- 转发任务、流式状态、停止和魔法命令。

N.E.K.O 仓库不提供 QwenPaw 服务进程本身，也不保证外部服务已经安装、已启动或具有所需模型和工具权限。只有健康探测返回 ready 时，QwenPaw 才会进入统一渠道候选集。默认探测地址由适配器配置决定；不要把某个本地端口当成内置服务承诺。

“OpenClaw”在当前代码中是兼容名，不代表另有一套由本仓库实现的 OpenClaw Agent。描述部署或能力时，应明确区分 N.E.K.O 适配层与外部 QwenPaw 服务。

## 任务状态与取消

Agent Server 的任务注册表保存在进程内。`app/agent_server/api_runtime.py` 提供任务查询和：

```text
POST /tasks/{task_id}/cancel
```

取消行为按任务类型分派：Computer Use 设置取消信号，Browser Use 调用自身取消接口，OpenFang 和 QwenPaw/OpenClaw 转发远端停止请求。任务进入终态后，注册表会按 TTL 清理；它不是永久任务历史。

取消是尽力而为的协作式停止。外部服务是否已经执行某个不可逆动作，取决于取消抵达前的实际状态，不能只根据本地 `cancelled` 标签推断。

## 已实现的纠正反馈

纠正反馈当前仅支持 **Browser Use 与 Computer Use 互相纠正**。终态任务可以调用：

```text
POST /api/agent/tasks/{task_id}/correction
```

请求体：

```json
{
  "correct_tool": "browser_use",
  "correct_instruction": "这个任务只需要操作网页，不要控制整个桌面",
  "user_note": "可选补充"
}
```

接口约束来自当前实现：

- 原任务类型必须是 `computer_use` 或 `browser_use`；
- 任务必须处于 `completed`、`failed` 或 `cancelled`；
- `correct_tool` 只能是另一个渠道，不能与原渠道相同；
- 创建任务时必须已经保存内部纠正上下文；
- `correct_instruction` 不能为空。

### 写入内容与隐私处理

`TaskExecutor.record_tool_correction()` 会写入当前配置目录下的 `correction_memory.json`。每个事件包含经过裁剪和脱敏的用户请求、归一化意图、近期上下文、错误渠道、正确渠道及说明。密码、token、Cookie、邮箱、验证码、身份证号和常见电话号码等模式会在写入前替换；文件使用原子写入，并在支持的系统上收紧目录和文件权限。

这是独立的 Agent 路由纠正文件，不是角色记忆系统，也不是插件 SDK 的 `bus.memory`。当前最多保留 300 条事件；同一 `task_id` 的再次提交会更新原事件。

### 后续评估如何使用

统一渠道评估前，代码会用当前请求、归一化意图和近期上下文做轻量关键词匹配，最多选取 3 条相关纠正事件，并只注入：

- 归一化意图；
- 上次错误选择；
- 用户确认的正确渠道。

这不是向量检索，也不会把完整历史或原始敏感文本无条件塞进 prompt。相关性不足时不会注入任何纠正。

## 尚未实现的能力

以下内容目前只能作为建议，不能写成现状：

- 对 QwenPaw、OpenFang、用户插件或具体 plugin `entry_id` 提交同类纠正；
- 自动从自然语言“你用错工具了”推断并提交纠正；
- 提供纠正记录的管理 UI、导出、删除或多用户隔离策略；
- 使用 embedding、向量数据库或角色持久记忆来检索纠正；
- 将本地纠正记录同步给外部 QwenPaw/OpenClaw 服务。

若要扩展这些能力，至少需要先定义用户确认、身份隔离、保留周期、删除入口和外部同步边界，不能直接复用角色记忆或插件 `bus.memory`。

## 排查顺序

渠道选择异常时，按下面顺序判断，不要先改 prompt：

1. 检查对应能力开关和运行时可用性；不可用渠道不会进入候选集。
2. 区分用户插件的两阶段选择与非插件渠道的统一评估。
3. 检查任务记录中的 `type`、`status` 和内部纠正上下文是否存在。
4. 若是 QwenPaw，分别检查 N.E.K.O 适配层配置和外部服务健康状态。
5. 若纠正没有生效，确认事件已写入 `correction_memory.json`，且新请求与旧事件有可匹配的关键词。

这套边界能解释当前实现；任何超出上述范围的设计都应明确标为提案。
