# Task HUD System

Task HUD (Head-Up Display) 是一个悬浮在屏幕上的实时任务监控面板，用于显示 Agent 任务（包括插件任务）的执行状态和进度。

## 整体架构

```
Backend                              Frontend
┌────────────────┐                  ┌────────────────────┐
│ TaskExecutor   │                  │ app.js             │
│   │            │  WebSocket       │   │                │
│   │ task events│ ──────────────>  │   │ _agentTaskMap  │
│   │            │  JSON messages   │   │                │
│                │                  │   └─> AgentHUD     │
└────────────────┘                  │        │           │
                                    │        ▼           │
                                    │   Task HUD Panel   │
                                    └────────────────────┘
```

## 数据流转

### 1. 任务创建

当用户触发一个 Agent 任务（包括插件任务）时，后端 Agent Server 会创建任务对象并分配唯一 ID。任务初始状态为 `queued`（队列中）。

### 2. 状态推送

后端通过 WebSocket 连接向前端推送任务状态消息，主要有两种类型：

| 消息类型 | 用途 |
|---------|------|
| `agent_task_snapshot` | 批量任务快照，初始化或重连时发送 |
| `agent_task_update` | 单个任务状态更新，实时推送 |

### 3. 前端接收

前端 `app.js` 中的 WebSocket 处理器接收消息后：

1. 将任务数据存入 `window._agentTaskMap`（Map 结构）
2. 计算各状态任务的数量统计
3. 调用 `AgentHUD.updateAgentTaskHUD()` 更新界面

### 4. HUD 更新

`updateAgentTaskHUD()` 方法负责：

- 更新标题栏的统计数字（运行中、队列中）
- 渲染或更新任务卡片
- 控制空状态提示的显示/隐藏
- 管理已完成/失败任务的短暂停留时间

## 任务状态

| 状态 | 显示文本 | 颜色 | 说明 |
|------|---------|------|------|
| `queued` | 队列中 | 灰色 | 任务已创建，等待执行 |
| `running` | 运行中 | 蓝色 | 任务正在执行 |
| `completed` | 已完成 | 绿色 | 任务执行成功 |
| `failed` | 失败 | 红色 | 任务执行失败 |
| `cancelled` | 已取消 | - | 任务被用户终止 |

## 任务数据结构

后端推送的任务对象包含以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 任务唯一标识 |
| `type` | string | 任务类型（`user_plugin`、`plugin_direct`、`computer_use` 等） |
| `status` | string | 任务状态 |
| `start_time` | string | ISO 格式的开始时间 |
| `params` | object | 任务参数 |

### params 字段

对于插件任务，`params` 包含：

| 字段 | 说明 |
|------|------|
| `plugin_id` | 插件 ID（内部标识） |
| `plugin_name` | 插件友好名称（用于显示） |
| `entry_id` | 入口点 ID |
| `description` | 任务描述（用户原话，如"三分钟后提醒我吃饭"） |

对于 Computer Use 任务，`params` 包含：

| 字段 | 说明 |
|------|------|
| `instruction` | 任务指令（用户原话） |

## 任务卡片布局

采用**紧凑多行**布局：

```
┌──────────────────────────────────┐
│ 🧩 提醒插件        ▏运行中▕   ✕ │  ← 第一行：图标 + 名称 + 状态 + 取消
│ 三分钟后提醒我吃饭               │  ← 描述行：任务具体内容（可选）
│ ⏱️ 1:23  ━━━━━━━━━━━━━━━━  2/3  │  ← 进度行：倒计时 + 进度条 + 步数
└──────────────────────────────────┘
```

| 元素 | 位置 | 说明 |
|------|------|------|
| 类型图标 | 第一行左 | 插件 🧩 / Computer Use 🖱️ / 其他 ⚙️ |
| 名称 | 第一行左 | 插件任务优先用 `plugin_name`，否则用 `plugin_id`，最后用类型翻译名 |
| 状态徽章 | 第一行中 | 带背景色的状态文字 |
| 取消按钮 | 第一行右 | 点击可终止单个任务 |
| 任务描述 | 描述行 | 显示 `params.description` 或 `params.instruction`（用户原话），单行省略 |
| 运行时间 | 进度行左 | 仅运行中任务显示，格式 `⏱️ 分:秒` |
| 进度条 | 进度行中 | 运行中任务显示，支持确定性和动画两种模式 |
| 步数 | 进度行右 | 当 `step` 和 `step_total` 存在时显示，如 `2/3` |

> 已完成/失败的任务停留 10 秒后移除（透明度 0.6），只显示第一行和描述行。

### 任务类型

| 类型 ID | 显示名称 | 图标 |
|---------|---------|------|
| `user_plugin` | 用户插件 | 🧩 |
| `plugin_direct` | 用户插件 | 🧩 |
| `computer_use` | 电脑控制 | 🖱️ |
| `mcp` | MCP工具 | ⚙️ |

## 交互功能

### 拖拽定位

- HUD 整体可拖拽，支持鼠标和触摸操作
- 拖拽时自动进行边界检测，确保不超出屏幕
- 位置保存到 `localStorage`，下次打开时恢复

### 折叠/展开

- 点击标题栏的最小化按钮（▼）可折叠为紧凑模式
- 折叠状态下只显示统计数字，隐藏任务列表
- 折叠状态保存到 `localStorage`

### 批量终止

- 标题栏右侧显示终止按钮（✕）
- 点击后弹出确认对话框
- 确认后调用 `/api/agent/admin/control` 接口终止所有任务

### 显示策略

- HUD 主要显示**运行中**和**队列中**的任务
- 任务完成或失败后，卡片会**停留 10 秒**再从 HUD 移除（透明度降为 0.6），防止快速任务一闪而过
- 停留期间使用 `setTimeout` 定时触发 re-render 清理过期卡片
- 当没有活跃任务时，显示空状态提示

## 视觉设计

### 样式特点

- **毛玻璃效果**：`backdrop-filter: blur(20px)` + 半透明背景
- **圆角设计**：8px 圆角，卡片内部元素使用更小的圆角
- **状态颜色**：
  - 运行中：`#2a7bc4`（蓝色）
  - 已完成：`#16a34a`（绿色）
  - 失败：`#dc2626`（红色）
  - 队列中：`#666`（灰色）
- **平滑动画**：状态变化和布局变化使用 CSS transition

### 尺寸限制

- 宽度：320px
- 最大高度：60vh
- 内容超出时内部滚动

## 技术细节

### 跨进程插件名称获取

由于 N.E.K.O 采用多进程架构，Agent Server（端口 48915）和 Main Server 是独立进程。插件状态（`plugin.core.state.state.plugins`）维护在 Main Server 进程中，Agent Server 无法直接访问。

**解决方案：**

Agent Server 通过 HTTP 调用嵌入式插件服务（端口 48916）的 `/plugins` 端点获取插件元数据：

```
Agent Server (48915)
    │
    │ HTTP GET /plugins
    ▼
User Plugin Server (48916) ──> 返回插件列表（含 name 字段）
```

**缓存机制：**

为避免频繁 HTTP 请求，插件名称缓存 30 秒（`PLUGIN_NAME_CACHE_TTL`）。实现位于 `agent_server.py` 的 `_get_plugin_friendly_name()` 函数。

**数据流：**

1. 任务创建时，调用 `_get_plugin_friendly_name(plugin_id)` 获取友好名称
2. 将 `plugin_name` 添加到 `task_params` 中
3. 通过 `task_update` WebSocket 消息推送到前端
4. 前端从 `params.plugin_name` 读取并显示

### 前端显示逻辑

对于插件任务（`user_plugin`、`plugin_direct`），HUD 显示逻辑：

1. **名称行**：优先显示 `params.plugin_name`，回退到 `params.plugin_id`，最后显示翻译后的"用户插件"
2. **描述行**：显示 `params.description`（插件任务）或 `params.instruction`（CU 任务），即用户原话
3. **进度行**：倒计时 + 进度条（仅运行中任务）

### 任务停留与清理

**前端停留**：已完成/失败的任务在 HUD 中停留 10 秒后移除（`MIN_DISPLAY_MS = 10000`），通过 `_taskTerminalAt` 记录进入终态的时间戳，`setTimeout` 延迟触发 re-render 清理。

**后端内存清理**：`task_registry` 中已完成/失败/取消的任务在 5 分钟后自动清理（`TASK_REGISTRY_CLEANUP_TTL = 300`），通过 `_cleanup_task_registry()` 在每次生成快照时触发（最多每 60 秒一次）。

### 样式主题适配

卡片文字颜色和背景全部使用 CSS 变量，自动适配暗色模式：

| 用途 | CSS 变量 | 亮色回退值 |
|------|----------|-----------|
| 名称文字 | `--neko-popup-text-sub` | `#666` |
| 倒计时文字 | `--neko-popup-text-sub` | `#888` |
| 取消按钮 | `--neko-popup-text-sub` | `#999` |
| 运行中背景 | `--neko-popup-accent-bg` | `rgba(42, 123, 196, 0.08)` |
| 进度条底色 | `--neko-popup-accent-bg` | `rgba(42, 123, 196, 0.15)` |
| 进度条填充 | `--neko-popup-accent` | `#2a7bc4` |

## 相关文件

| 文件 | 说明 |
|------|------|
| `static/common-ui-hud.js` | HUD 组件核心实现 |
| `static/app.js` | WebSocket 消息处理，调用 HUD 更新 |
| `static/locales/*.json` | i18n 翻译文件 |
| `agent_server.py` | `_get_plugin_friendly_name()` 跨进程获取插件名称 |

## 延迟完成机制（Deferred Completion）

部分插件任务在"调度成功"时并未真正完成，而是需要等待未来某个时间点才算真正结束（例如备忘提醒插件在提醒触发后才算完成）。

### 问题背景

默认流程下，`add_reminder()` 调度成功即返回 `ok()` → 任务立即标记为 `completed` → HUD 显示"已完成"。但用户期望在提醒实际触发后才看到完成状态。

### 实现流程

```
add_reminder() → ok(data={deferred: true, reminder_id: "abc"})
  ↓
agent_server 检测 deferred=true → 任务保持 "running"，不发 completed 事件
  ↓
HUD：🧩 备忘提醒 ▏运行中▕ / 15分钟后 起来活动 / ⏱️ 倒计时
  ↓
agent_server 调用 bind_task 入口 → reminder 记录写入 agent_task_id
  ↓
... 15分钟后 daemon 触发提醒 ...
  ↓
_push_reminder() → HTTP POST /api/agent/tasks/{id}/complete
  ↓
agent_server 标记 "completed" → 推送 task_update 事件 → HUD 10秒后消失
```

### 插件侧实现

插件的 `add_reminder()` 在返回值中设置 `deferred: True`：

```python
return ok(data={
    "status": "scheduled",
    "deferred": True,       # 通知 agent_server 任务尚未完成
    "reminder_id": rid,
    ...
})
```

提醒记录中预留 `agent_task_id` 字段（由 `_bind_deferred_task` 回写）：

```python
reminder = {"id": rid, ..., "agent_task_id": None}
```

`_push_reminder()` 在推送消息后调用回调端点：

```python
agent_task_id = r.get("agent_task_id")
if agent_task_id:
    httpx.post(f"http://127.0.0.1:48915/api/agent/tasks/{agent_task_id}/complete")
```

插件还提供内部入口 `bind_task`，用于接收 agent_task_id 的绑定：

```python
@plugin_entry(id="bind_task", description="内部接口：将 agent_task_id 关联到提醒记录")
async def bind_task(self, reminder_id: str, agent_task_id: str, **kwargs): ...
```

### Agent Server 侧实现

`_run_user_plugin_dispatch()` 检测 deferred 标志，跳过终态更新和事件推送：

```python
is_deferred = isinstance(run_data, dict) and run_data.get("deferred") is True
if up_result.success and is_deferred:
    # 不更新 registry 状态，不推 task_update(completed)
    loop.run_in_executor(None, _bind_deferred_task, plugin_id, reminder_id, task_id)
    return
```

`_bind_deferred_task()` 在线程池中异步调用插件的 `bind_task` 入口（避免阻塞事件循环）：

```python
def _bind_deferred_task(plugin_id, reminder_id, agent_task_id):
    # 通过 /runs 接口调用 bind_task 入口，轮询等待完成
    ...
```

### 回调端点

daemon 触发提醒后，调用此端点标记任务完成并推送前端事件：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/agent/tasks/{task_id}/complete` | POST | 将 deferred 任务标记为已完成并推送 HUD 更新 |

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/agent/tasks/{id}/cancel` | POST | 取消单个任务 |
| `/api/agent/tasks/{id}/complete` | POST | 标记 deferred 任务为已完成（daemon 回调） |
| `/api/agent/admin/control` | POST | 批量控制（`action: end_all`） |

## 改进记录

### 2026-03-09：简洁双行布局 + 视觉统一

**背景**：原有卡片信息过多（描述行、阶段消息、独立进度条），任务完成后一闪即逝（5 秒）。

**改动**：

1. **布局重构**：从多行布局精简为紧凑布局——第一行（图标+名称+状态+取消），描述行（任务具体内容），进度行（倒计时+进度条+步数）
2. **任务描述**：后端将 `task_description` 存入插件任务的 `params.description`，前端显示用户原话（如"三分钟后提醒我吃饭"）
3. **停留策略**：已完成/失败的任务停留 10 秒后移除（`MIN_DISPLAY_MS = 10000`），防止快速任务一闪而过
4. **内存清理**：`task_registry` 自动清理 5 分钟前的已完成任务（`_cleanup_task_registry()`），防止长时间运行时内存堆积
5. **视觉统一**：所有硬编码颜色（`#444`、`#666`、`#888`、`#999`）替换为 CSS 变量，支持暗色模式自动适配
6. **透明度调整**：已完成任务透明度从 0.75 降至 0.6，视觉区分更明显
