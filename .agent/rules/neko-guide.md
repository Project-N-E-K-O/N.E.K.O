---
trigger: always_on
---

# N.E.K.O 开发规范

## 基本规则

- 使用 i18n 支持国际化，目前支持 en.json、ja.json、ko.json、zh-CN.json、zh-TW.json、ru.json 六种。每次改 i18n 字符串时必须同步更新全部 6 个 locale 文件，只改部分会被打回。
- **后端 Python 多语言字符串一律落在 `config/prompts_*.py`**：无论是平铺 `dict[str, str]` 还是嵌套 `dict[str, dict[K, str]]`，凡键里出现 `'zh' / 'en' / 'ja' / 'ko' / 'ru'` 的语言映射，都必须放在 `config/prompts_*` 下。`scripts/check_prompt_hygiene.py` 只抓平铺结构，但规范是"加新语言时一次扫 `config/` 即可补全"——嵌套 dict 即使 lint 没抓也算技术债，需自觉搬迁。新增后端模块若有翻译需求，直接在 `config/prompts_<topic>.py` 加新模块或复用已有模块（如 `prompts_activity.py`、`prompts_proactive.py`、`prompts_memory.py`）。
- 使用 `uv run` 来运行本项目的任何 Python 程序（pytest、脚本等），不要直接用系统 Python。原因：pyproject.toml 限制了 Python 版本（<3.13），uv 会自动选择合适版本并管理虚拟环境。
- 任何涉及用户隐私（原始对话）的 log 只能用 `print` 输出，不得使用 `logger`。
- 翻译 system prompt 时，即使出于其他原因也应当保留 `======以上为`，这是一个水印。
- **辅助 LLM 调用约定（memory/ + utils/）**：
  - **不下发 `temperature`**：所有 `utils.llm_client.create_chat_llm` / `ChatOpenAI` 及其包装 helper 一律不传 `temperature=...`，默认 `None` 表示"不写进请求体"。理由：(1) 兼容 o1/o3/gpt-5-thinking/Claude extended-thinking；(2) 各 task 自定温度会引入难复现的回归。守门：`scripts/check_no_temperature.py`（CI 见 `.github/workflows/analyze.yml`）。
  - **模型从 tier 拿，不 hardcoded fallback**：每个 LLM 调用都通过 `config_manager.get_model_api_config(<tier>)` 拿 model/base_url/api_key 三件套。不要再写 `api_config.get('model', SETTING_PROPOSER_MODEL)` 这类 fallback——`SETTING_PROPOSER_MODEL` / `SETTING_VERIFIER_MODEL` 已于 2026-04 退环境。tier 未配好时让 API 直接拒绝，比静默回退到 qwen-max 更安全。
  - **memory 子模块按职责选 tier**：fact extraction / signal detection / reflection synthesis / fact dedup / recall rerank 走 `summary`；recent.review + persona.correction + promotion merge 走 `correction`。不要为单点新增 hardcoded 模型名。

## API URL 末尾不带斜杠（前后端硬性要求）

后端 route 声明、前端调用都必须用**不带末尾斜杠**的形式：

- ✅ `/api/characters`、`/api/live2d/models`、`/api/memory/funnel/{lanlan_name}`
- ❌ `/api/characters/`、`/api/live2d/models/`

理由：
1. **跟主流 REST API 一致**：Stripe / GitHub / Google / AWS / Microsoft REST API Guidelines 全都禁止末尾斜杠。
2. **反代场景下不会炸**：FastAPI/Starlette 默认 `redirect_slashes=True`，把 `/foo/` 307 重定向到 `/foo`，但 `Location` 是用 request `Host` 拼出来的**绝对 URL**。如果反代没透传 `Host`、或 `proxy_headers` 没开（`NEKO_BEHIND_PROXY=1`），重定向就指向上游 `127.0.0.1:<内网端口>`，浏览器从局域网跟过去拿到 `ERR_CONNECTION_REFUSED`。PR #938 引发的角色卡管理回归就是这个 bug，根因不在反代而在我们前端写了带斜杠的 URL 把锅推给了 starlette 的脆弱重定向。**不带斜杠 = 永远不触发 307 = 整类问题消失。**

具体规则：
- **后端**：`APIRouter(prefix="/api/foo")` + `@router.get('')`（不是 `@router.get('/')`）。唯一例外是 `pages_router.py` 里 `@router.get("/")` —— 那是根页面 `index.html`，本来就该是 `/`。
- **前端**：`fetch('/api/foo')`，不是 `fetch('/api/foo/')`。**前缀拼接**（例如 `` `/api/foo/${id}` `` 或 `` '/api/foo/' + encodeURIComponent(name) + '/sub' ``）里的中间斜杠是路径分隔符，不算违反——最终 URL 不带末尾斜杠就行。

CI 守门：
- `scripts/check_api_trailing_slash.py` —— AST 扫 `main_routers/*.py` 和 `*_server.py` 里的 `@router.get/post/...` 装饰器
- `scripts/check_frontend_api_trailing_slash.py` —— 正则扫 `static/` / `frontend/` / `templates/` 里以 `/'` 或 `/"` 或 `` /` `` 结尾的 `/api/...` 字面量（前缀拼接形式自动豁免）

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

## 架构：跨模块 prompt 不能写死特定游戏 / 功能

系统级 / 跨模块的 LLM prompt（archive label、history review、postgame realtime context、memory highlight selector、context organizer 等）只能用通用层概念，**不能**出现"足球""比分""射门""乌龙""抢断""进球""防守"等特定游戏术语。具体游戏术语只能出现在 module-bound 的 helper 内（函数名带 module 名的，如 `_format_soccer_pregame_context_for_prompt`、`_build_soccer_balance_hint`），或者 `config/prompts_game.py` 里 `SOCCER_*_PROMPT` 这种 specific-by-design 的常量里。

边界判定：
- **prompt 指令**：`_build_game_archive_memory_text` / `_build_postgame_realtime_context_text` / `_select_game_archive_memory_highlights` / `_run_game_context_organizer_ai` 这种函数名是 generic（没有 module 名）的，里面所有字符串都必须 module-agnostic。
- **module-bound prompt**：函数名 `_*_soccer_*` 或 `prompts_game.py` 里的 `SOCCER_*_PROMPT`，用 `if game_type == "soccer":` gate 进入，里面提足球/比分天然合理。
- **data 不算 prompt**：score 数值、`_GAME_EVENT_MEMORY_LABELS` 这种 event-kind→中文 label 表、`game_type: "soccer"` 字段值——是数据不是指令，不受此规则约束。
- **写入侧也要查**：写进 ordinary memory 的字符串会被后续 `HISTORY_REVIEW_PROMPT` 读到，等同于 prompt，必须 generic；不要靠 review prompt 兜底（`config/prompts_memory.py` 里的"游戏模块归档"豁免规则是兜底，不是借口）。

理由：
1. 跨模块 prompt 会反复进入 review LLM 视野，review LLM 不知道当前是哪个游戏；硬编码具体游戏名会让其他游戏的 archive 在 review 阶段被错判。
2. 加第二个游戏（chess、racing 等）时不必双写所有 cross-module prompt。
3. system-level 不应该泄漏 module-internal 概念，否则就是抽象层错位。

应避免 → 应使用：
- `"[足球小游戏记忆记录]"` → `"[游戏模块记忆记录]"`
- `"官方比分"` / `"比分规则"` / `"比分说明"` / `"最终/最近比分"` → `"官方结果"` / `"结果规则"` / `"结果说明"` / `"最终/最近结果"`
- `"比赛事件"` / `"比赛事实"`（信号组键）→ `"本局事件"` / `"本局事实"`
- `"刚才足球小游戏已经结束"` → `"刚才这局游戏已经结束"`
- `"不要再说站好、射门、继续进攻等比赛中指令"` → `"不要再说任何只在游戏进行中才合理的指令或动作"`
- `"足球小游戏赛后记录"` / `"soccer minigame postgame record"`（5 locale 都要查）→ `"游戏模块归档"` / `"game module archive"`

检测方法：在 `main_routers/` 等跨模块代码里 `grep -nE "足球|比分|射门|乌龙|抢断|进球"`，结果应该全部落在带 module 名的函数（`_*_soccer_*`）或 module-specific 路径里。

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
