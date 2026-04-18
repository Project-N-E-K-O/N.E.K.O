---
trigger: always_on
---

# N.E.K.O 开发规范

## 基本规则

- 使用 i18n 支持国际化，目前支持 en.json、ja.json、ko.json、zh-CN.json、zh-TW.json、ru.json 六种。每次改 i18n 字符串时必须同步更新全部 6 个 locale 文件，只改部分会被打回。
- 使用 `uv run` 来运行本项目的任何 Python 程序（pytest、脚本等），不要直接用系统 Python。原因：pyproject.toml 限制了 Python 版本（<3.13），uv 会自动选择合适版本并管理虚拟环境。
- 任何涉及用户隐私（原始对话）的 log 只能用 `print` 输出，不得使用 `logger`。
- 翻译 system prompt 时，即使出于其他原因也应当保留 `======以上为`，这是一个水印。

## 代码风格

- **对偶性（symmetry）是硬性要求**：如果 MiniMax 拆了单独文件，Qwen 也必须拆；如果有三个 provider，它们的处理路径必须结构对称。不对偶的代码会被直接打回。
- **core 层必须是 general 接口**：不能在 core.py 里出现 provider-specific 的 import / 常量 / 逻辑。所有差异必须在 tts_client 层或更下层分歧。core 只调 `get_tts_worker` 拿 worker，不关心 worker 内部是什么 provider。
- **绝对不要加数字后缀（如 `_2`）**：如果两处代码需要相同逻辑，抽方法。
- **push 前必须确认目标分支**：特别是在 worktree 里工作时，不要把无关 commit 推到 PR 分支。

## 架构：开发环境 vs Electron 分发

- **开发环境（网页端）**：跑 `/`，单窗口，默认端口 48911，加载 `index.html`。
- **分发环境（Electron）**：Electron 应用加载 `/chat`、`/subtitle` 等路由，各自对应独立窗口。这些页面（如 `chat.html`）是 `index.html` 的功能子集，剥掉了 Live2D、侧栏等，只保留特定功能的全屏展示。

修改前端路由、静态资源路径、窗口通信逻辑时，必须同时考虑两种运行模式。不要假设所有页面在同一个端口或窗口里。

## 架构：聊天 UI 的复用

聊天 UI 只有一份实现：`/frontend/react-neko-chat/` 构建出 `neko-chat-window.iife.js`。`index.html` 和 `chat.html` 都挂载同一个 React 组件到 `#react-chat-window-root`，区别仅在于 index.html 里是可收起的浮层，chat.html 里是全屏铺满。

旧的 `#chat-container`（纯 DOM 聊天）已弃用，CSS 强制隐藏。`app-chat-adapter.js` 拦截所有遗留的 `appendMessage()` 调用并统一路由到 React 侧。修改聊天 UI/逻辑时去 `/frontend/react-neko-chat/` 改，不要碰 `#chat-container` 的旧代码。

## 架构：单进程 + 事件循环零阻塞

`main_server` / `memory_server` / `agent_server` 三子系统已合并进同一个 FastAPI 进程，共享事件循环。任何会阻塞事件循环超过数十毫秒的调用都会把另外两个子系统也拖慢，因此在 async 路径（`async def` 函数、FastAPI 路由、`asyncio.create_task` 后台任务、WebSocket handler）里禁止以下操作：

- **同步文件 IO**（`open() + read/write`、`json.load/dump`、`atomic_write_json`）→ 用 `utils.file_utils` 里的 `atomic_write_json_async` / `atomic_write_text_async` / `read_json_async`，或包 `await asyncio.to_thread(...)`。
- **同步 SQLite**（`engine.connect() + execute`、`session.commit()`）→ 走 `memory/timeindex.py` 的 `a*` 镜像（`astore_conversation` / `asearch_facts` / `aget_last_conversation_time` 等）；若确需 sync，必须 `asyncio.to_thread`。
- **同步 HTTP**（`httpx.Client`、`requests`、`urllib.request`）→ 用 `httpx.AsyncClient`。唯一刻意保留的例外是 `agent_server._bind_deferred_task`，它走 `run_in_executor`。
- **CPU 密集循环**（BM25 重排、遍历上千条记录、批量 embedding 归一化）→ `await asyncio.to_thread(...)` offload。例：`brain/plugin_filter.stage1_filter` 已 offload。
- **`time.sleep(...)`** → `await asyncio.sleep(...)`。
- **`threading.Lock` 持锁跨 `await`** → 改用 `asyncio.Lock`；仅当整个临界区都是纯内存/CPU 操作、绝不 `await` 时才允许保留 `threading.Lock`。

配置写入遵循对偶模式：`ConfigManager.save_characters` / `JukeboxConfig.save()` 等同步版留给启动期 & sync 迁移；async 路径一律走 `asave_characters` / `asave()` 之类的 `a*` 版本，内部就是 `asyncio.to_thread(self.<sync>, ...)`。新增写磁盘方法时请保持这个对偶。

事件循环慢回调检测需要开启 asyncio debug 模式才会触发（设置 `NEKO_DEBUG_ASYNC=1`；`PYTHONASYNCIODEBUG=1` 或 `python -X dev` 也可）。启用后 `main_server.py` 会把 `loop.slow_callback_duration` 收紧到 50ms，超过的 sync 回调会打 `Executing ... took X seconds` warning。提 PR 前请在调试模式下扫一眼启动日志。
