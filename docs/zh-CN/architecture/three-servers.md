# 服务架构

## 主服务器（`main_server.py`，端口 48911）

主服务器是一个 FastAPI 应用，作为所有交互的用户侧入口。

### 启动流程

1. **加载配置** —— 加载 `config_manager`，初始化角色数据，检测文档目录
2. **创建会话** —— 为每个已定义的角色创建 `LLMSessionManager`
3. **挂载静态文件** —— 挂载 `/static`、`/user_live2d`、`/user_vrm`、`/workshop`
4. **注册路由** —— 引入全部 11 个 API 路由（agent、characters、config、cookies_login、live2d、memory、music、pages、system、vrm、websocket、workshop）
5. **事件处理器** —— 初始化 Steamworks、启动 ZeroMQ 桥、预加载音频模块、检测语言、启动同步连接器线程
6. **启动 Uvicorn** —— 绑定 `127.0.0.1:48911`

### 处理内容

- 所有 REST API 端点（11 个路由）
- 用于实时聊天的 WebSocket 连接（`/ws/{lanlan_name}`）
- TTS 合成（线程工作器，带请求/响应队列）
- 音频重采样（24kHz -> 48kHz，通过 soxr 状态化 `ResampleStream`）
- 静态文件服务（模型、CSS、JS、多语言文件）
- HTML 页面渲染（Jinja2 模板）
- 智能体事件桥（ZeroMQ PUB/SUB + PUSH/PULL）
- 跨服务器同步连接器守护线程（记忆分析、监控同步）

## 记忆服务器（`memory_server.py`，端口 48912）

记忆服务器管理持久化的对话历史、语义召回和角色设置提取。

### 存储层级

| 层级 | 用途 | 后端 |
|------|------|------|
| 近期记忆 | 每个角色最近 N 条消息（含 LLM 压缩） | JSON 文件（`recent_*.json`） |
| 时间索引原文 | 完整对话历史 | SQLite 表 |
| 时间索引压缩 | 摘要历史 | SQLite 表 |
| 语义记忆 | 基于嵌入向量的召回 | 向量存储 |
| 重要设置 | 从对话中提取的角色偏好和知识 | JSON 文件 |

### 关键操作

- **缓存**：轻量追加新对话轮次（无 LLM 处理）
- **处理**：完整处理流水线 —— 近期记忆更新、时间索引存储、语义索引和审阅
- **续期**：热切换续期 —— 归档当前上下文用于会话过渡
- **搜索**：跨所有历史的语义相似度搜索（`hybrid_search`，带 LLM 重排序）
- **设置**：从对话中提取并验证角色偏好（`ImportantSettingsManager`）
- **审阅**：异步 LLM 审计以检测矛盾和逻辑错误（`review_history`）
- **新对话**：为新轮次准备上下文（带括号清理）

### REST 端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `/health` | GET | 服务健康检查（N.E.K.O. 签名） |
| `/cache/{lanlan_name}` | POST | 轻量追加（无 LLM） |
| `/process/{lanlan_name}` | POST | 完整处理（近期 + 时间 + 语义 + 审阅） |
| `/renew/{lanlan_name}` | POST | 热切换续期 |
| `/get_recent_history/{lanlan_name}` | GET | 格式化的 LLM 提示词上下文 |
| `/search_for_memory/{lanlan_name}/{query}` | GET | 语义搜索 |
| `/get_settings/{lanlan_name}` | GET | 角色设置 JSON |
| `/new_dialog/{lanlan_name}` | GET | 新对话上下文（清理括号） |
| `/reload` | POST | 重新加载所有组件 |
| `/cancel_correction/{lanlan_name}` | POST | 中止审阅任务 |

## 智能体服务器（`agent_server.py`，端口 48915）

智能体服务器处理由对话上下文触发的后台任务执行，使用 `DirectTaskExecutor` 进行并行能力评估。

### ZeroMQ 地址映射

| 套接字 | 地址 | 方向 | 用途 |
|--------|------|------|------|
| PUB/SUB | `tcp://127.0.0.1:48961` | 主服务器 -> 智能体 | 会话事件 |
| PUSH/PULL | `tcp://127.0.0.1:48962` | 智能体 -> 主服务器 | 任务结果 |
| PUSH/PULL | `tcp://127.0.0.1:48963` | 主服务器 -> 智能体 | 分析请求 |

### REST 端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `/health` | GET | 服务健康检查（N.E.K.O. 签名） |
| `/status` | GET | 智能体状态快照（任务、标志、能力） |
| `/flag_set/{flag_name}/{value}` | POST | 控制智能体功能标志 |
| `/capabilities` | GET | 可用的智能体能力 |
| `/task/spawn_computer_use` | POST | 排队 Computer Use 任务 |
| `/task/{task_id}` | GET | 获取任务状态和结果 |
| `/task/{task_id}` | DELETE | 取消/删除任务 |
| `/reload_config` | POST | 重新加载 API 配置 |

### 任务执行流水线

1. 主服务器通过 ZeroMQ 发布分析请求
2. `DirectTaskExecutor` 对所有可用方法进行**并行评估**：
   - `_assess_mcp()` —— MCP 工具能否处理？
   - `_assess_browser_use()` —— 浏览器自动化能否处理？
   - `_assess_computer_use()` —— 桌面 GUI 自动化能否处理？
   - `_assess_user_plugin()` —— 本地插件能否处理？
3. 基于优先级选择：**MCP > Browser Use > Computer Use > 用户插件**
4. 选定的适配器执行任务
5. 结果经过分析（`analyzer.py`）和去重（`deduper.py`）
6. 最终结果通过 ZeroMQ 流式返回（`task_result`、`proactive_message`）

## 监控服务器（`monitor.py`，端口 48913）

监控服务器是一个轻量级 FastAPI 服务，为启动器和进程间协调提供健康检查和配置端点。

### 用途

- **健康监控**：提供带有 N.E.K.O. 签名的健康检查端点，使启动器能够验证服务状态
- **配置中继**：为其他进程暴露当前配置状态
- **启动器协调**：使启动器能够管理启动顺序、端口回退和优雅关闭

### 启动锁

启动器使用平台特定的机制来防止重复实例：
- **Windows**：命名互斥体 `Global\NEKO_LAUNCHER_STARTUP_LOCK`
- **POSIX**：系统临时目录中的文件锁
