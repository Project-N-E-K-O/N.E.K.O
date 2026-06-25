# 智能体系统

智能体系统使 N.E.K.O. 角色能够执行后台任务 —— 浏览网页、控制计算机、委派给独立的智能体通道以及调用外部工具 —— 这些任务由对话上下文触发。

## 架构

```
主服务器                              智能体服务器
┌────────────────┐                  ┌────────────────────┐
│ LLMSession     │                  │ TaskExecutor        │
│ Manager        │  ZeroMQ          │   ├── Planner       │
│   │            │ ──────────────>  │   ├── Processor     │
│   │ agent_flags│  PUB/SUB         │   ├── Analyzer      │
│   │            │                  │   └── Deduper        │
│   │ callbacks  │ <──────────────  │                      │
│   │            │  PUSH/PULL       │ 适配器:              │
└────────────────┘                  │   ├── Computer Use   │
                                    │   ├── Browser Use    │
                                    │   ├── OpenClaw       │
                                    │   ├── OpenFang       │
                                    │   └── User Plugin    │
                                    └────────────────────┘
```

## 能力标志

智能体能力通过 `/api/agent/flags` 端点管理的标志进行切换：

| 标志 | 默认值 | 说明 |
|------|--------|------|
| `agent_enabled` | false | 智能体系统主开关 |
| `computer_use_enabled` | false | 截图分析、鼠标/键盘操作 |
| `browser_use_enabled` | false | 网页浏览自动化 |
| `user_plugin_enabled` | false | 插件 / Model Context Protocol 工具调用 |
| `openclaw_enabled` | false | OpenClaw 独立智能体通道 |
| `openfang_enabled` | false | OpenFang 独立智能体通道 |

## 任务执行流水线

1. **触发**：主服务器在对话中检测到可执行的请求，通过 ZeroMQ 发布分析请求。

2. **规划**：`Planner` 将请求分解为有序步骤的任务计划。

3. **执行**：`Processor` 通过相应的适配器运行每个步骤：
   - **Computer Use** —— 截取屏幕截图，使用视觉模型分析，执行鼠标/键盘操作
   - **Browser Use** —— 导航网页、提取内容、填写表单
   - **OpenClaw / OpenFang** —— 将任务委派给独立的智能体通道
   - **User Plugin** —— 通过用户安装的插件（Model Context Protocol）调用外部工具

4. **分析**：`Analyzer` 评估任务目标是否已达成。

5. **去重**：`Deduper` 防止发送重复结果。

6. **返回**：结果通过 ZeroMQ PUSH/PULL 流式返回主服务器。

## ZeroMQ 套接字映射

| 地址 | 类型 | 方向 | 用途 |
|------|------|------|------|
| `tcp://127.0.0.1:48961` | PUB/SUB | 主服务器 → 智能体 | 会话事件、任务请求 |
| `tcp://127.0.0.1:48962` | PUSH/PULL | 智能体 → 主服务器 | 任务结果、状态更新 |
| `tcp://127.0.0.1:48963` | PUSH/PULL | 主服务器 → 智能体 | 分析请求队列 |

## Computer Use

Computer Use 适配器（`brain/computer_use.py`）支持基于视觉的计算机交互：

1. 捕获桌面截图
2. 发送给视觉模型（如 `qwen3-vl-plus`）进行分析
3. 根据视觉理解规划鼠标/键盘操作
4. 通过 `pyautogui` 执行操作

Computer Use 的模型配置请参阅[模型配置](/zh-CN/config/model-config)参考文档。

## Browser Use

Browser Use 适配器（`brain/browser_use_adapter.py`）封装了 `browser-use` 库用于网页自动化：

- 导航至 URL
- 提取页面内容
- 填写表单
- 点击元素
- 截取页面截图

## OpenClaw

OpenClaw 适配器（`brain/openclaw_adapter.py`）将可执行的任务委派给 OpenClaw 独立智能体通道（在内部以 `qwenpaw` 引用）。

## OpenFang

OpenFang 适配器（`brain/openfang_adapter.py`）将可执行的任务委派给 OpenFang 独立智能体通道。

通道选择优先级在 `brain/task_executor.py` 中定义为 `_CHANNEL_PRIORITY = ["qwenpaw", "openfang", "browser_use", "computer_use"]`。插件 / MCP 工具调用（`user_plugin_enabled`）走独立的分发路径，**不**属于 `_CHANNEL_PRIORITY`。

## API 端点

完整的端点参考请参阅[智能体 REST API](/zh-CN/api/rest/agent)。
