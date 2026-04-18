# N.E.K.O. Testbench 实施进度

> 本文档是**断点续跑的关键凭证**。每一阶段开始/完成/受阻时必须更新。
> 对应计划文件: [PLAN.md](./PLAN.md)  (亦同步于 `.cursor/plans/*.plan.md`)
>
> 状态规范: `pending` / `in_progress` / `done` / `blocked`

---

## 阶段总览

| ID | 标题 | 状态 | 备注 |
|---|---|---|---|
| P00 | 存档计划 + 进度检查点 | **done** | 2026-04-17 完成 |
| P01 | 后端骨架 + 目录分离 | **done** | 2026-04-17 完成 |
| P02 | 会话/沙盒/时钟最小实现 | **done** | 2026-04-18 完成 |
| P03 | 前端骨架 + i18n + CollapsibleBlock | **done** | 2026-04-18 完成 |
| P04 | Settings workspace | **in_progress** | 2026-04-18 开始 |
| P05 | Setup workspace (Persona + Import) | pending | |
| P06 | VirtualClock 完整滚动游标模型 | pending | |
| P07 | Setup Memory 四子页读写 | pending | |
| P08 | PromptBundle + Prompt Preview 双视图 | pending | |
| P09 | Chat 消息流 + 手动 Send + SSE | pending | |
| P10 | 记忆操作触发 + 预览确认 | pending | |
| P11 | 假想用户 AI (SimUser) | pending | |
| P12 | 脚本化对话 (Scripted) | pending | |
| P13 | 双 AI 自动对话 (Auto-Dialog) | pending | |
| P14 | Stage Coach 流水线引导 | pending | |
| P15 | ScoringSchema + Schemas 子页 | pending | |
| P16 | 四类 Judger + Run 子页 | pending | |
| P17 | Results + Aggregate 子页 + 导出报告 | pending | |
| P18 | 快照/时间线/回退 | pending | |
| P19 | Diagnostics 错误+日志核心 | pending | |
| P20 | Diagnostics Paths/Snapshots/Reset | pending | |
| P21 | 保存/加载核心 (persistence) | pending | |
| P22 | 自动保存 + 启动时断点续跑 | pending | |
| P23 | 多格式多 scope 导出 | pending | |
| P24 | 文档 README | pending | |

---

## 阶段详情

### [x] P00 存档计划 + 进度检查点
- 目标: 建立 docs/ 基础设施, 保证后续任一阶段中断可无缝继续
- 产物:
  - `tests/testbench/__init__.py` (占位)
  - `tests/testbench/docs/PLAN.md` (PLAN 完整副本, 87976 bytes)
  - `tests/testbench/docs/PROGRESS.md` (本文件)
  - `tests/testbench/docs/AGENT_NOTES.md` (恢复指南)
  - `.gitignore` 追加 `tests/testbench_data/`
- 状态: done (2026-04-17)
- 子任务:
  - [x] 创建 `tests/testbench/__init__.py`
  - [x] 创建 `tests/testbench/docs/` 目录
  - [x] 拷贝 PLAN.md (87976 bytes)
  - [x] `.gitignore` 追加 `tests/testbench_data/`
  - [x] 写 PROGRESS.md
  - [x] 写 AGENT_NOTES.md
- 遗留: 无

### [x] P01 后端骨架 + 目录分离
- 目标: FastAPI 能启动, `uv run python tests/testbench/run_testbench.py --port 48920` 可访问 `/healthz`; 数据目录自动建立
- 产物:
  - `tests/testbench/config.py` (路径常量 + 数据根目录 + `ensure_data_dirs` / `ensure_code_support_dirs`)
  - `tests/testbench/run_testbench.py` (CLI, 默认 127.0.0.1, 公网绑定时 WARN)
  - `tests/testbench/server.py` (FastAPI app + 静态/模板挂载 + 全局异常中间件占位)
  - `tests/testbench/logger.py` (SessionLogger JSONL + Python logger)
  - `tests/testbench/routers/health_router.py` (/healthz, /version)
  - `tests/testbench/templates/index.html` (最小占位)
  - `tests/testbench/static/.gitkeep`, `tests/testbench/scoring_schemas/.gitkeep`, `tests/testbench/dialog_templates/.gitkeep`
  - `tests/testbench_data/` 及 7 个子目录 + `tests/testbench_data/README.md` (运行时自动生成)
- 状态: done (2026-04-17)
- 自测: `uv run python tests/testbench/run_testbench.py --port 48920` 启动成功; `/healthz` 返回 `{"status":"ok"}`; `/version` 返回完整元信息; `/` 渲染中文占位页; `tests/testbench_data/` 树正确创建
- 遗留: 无

### [x] P02 会话/沙盒/时钟最小实现
- 目标: POST /api/session 能建会话, GET /api/session 能读状态, sandbox 能 patch ConfigManager 且 restore
- 产物:
  - `tests/testbench/virtual_clock.py` (最小 API: cursor/now/set_now/advance + to_dict/from_dict)
  - `tests/testbench/sandbox.py` (Sandbox 类, ConfigManager 14 属性 swap/restore, 目录自动建)
  - `tests/testbench/session_store.py` (Session dataclass + SessionState 枚举 + SessionStore 单槽 + asyncio.Lock + session_operation 上下文管理器 + SessionConflictError)
  - `tests/testbench/routers/session_router.py` (`POST/GET/DELETE /api/session` + `GET /api/session/state`)
  - `tests/testbench/server.py` 挂载新路由 + 注册 shutdown 清理钩子 + 全局异常返回带 `session_state`
  - `tests/testbench/run_testbench.py` 启动时从 sys.path 移除 `tests/testbench/` 以避免与根 `config` 包命名冲突
- 状态: done (2026-04-18)
- 子任务:
  - [x] virtual_clock.py 最小 API
  - [x] sandbox.py (apply/restore/destroy, 含 mmd/plugins 等现代字段)
  - [x] session_store.py (Lock + 状态机 + 单活跃会话不变量 + session_operation)
  - [x] session_router.py 四端点
  - [x] server.py 挂载 + shutdown 钩子 + 路径冲突修复
  - [x] 启动自测通过: POST/GET/DELETE /api/session 全链路; 沙盒目录含 config/memory/character_cards/live2d/vrm/vrm/animation/mmd/mmd/animation/workshop/plugins 11 个子目录; 连续 POST 正确替换旧会话沙盒; DELETE 清理沙盒; state 端点正常
- 自测证据:
  - `curl /healthz` → `{"status":"ok"}`
  - `POST /api/session` → 返回 sandbox.applied=yes, 磁盘上建成 `tests/testbench_data/sandboxes/<id>/N.E.K.O/...`
  - 连续 POST → 旧会话沙盒自动销毁, 只留新的
  - `DELETE /api/session` → sandboxes/ 下为空
- 遗留: 无 (P06 会扩展 virtual_clock 完整滚动游标 API; 当前 `messages/snapshots/eval_results/model_config/stage` 字段已在 Session dataclass 预留, 后续 phase 追加即可)

### [x] P03 前端骨架 + i18n + CollapsibleBlock
- 目标: 浏览器打开能看到顶栏 + 5 workspace 切换骨架; 折叠组件工作, 中文文案可切换
- 产物:
  - `static/testbench.css` (暗色主题 + 顶栏/tab/workspace/dropdown/cb/toast/modal/form 全套样式)
  - `static/core/i18n.js` (zh-CN 文案字典 + `i18n(key, ...args)` + `i18nRaw` + `hydrateI18n(root)` 扫 `data-i18n*` 属性)
  - `static/core/state.js` (单 store + `on/off/emit` 事件总线 + 开发期 `window.__tbState`)
  - `static/core/toast.js` (右下角 4 种 toast: ok/info/warn/err, actions 按钮 + 自动淡出)
  - `static/core/api.js` (fetch 封装统一 `{ok,status,data,error}` + 5xx/400/403 自动 toast + 广播 `http:error` + `openSse()`)
  - `static/core/collapsible.js` (CollapsibleBlock 工厂: 摘要+length badge+copy+localStorage `fold:<session>:<block>` + Alt+Click 批量 + Expand/Collapse all 工具栏)
  - `static/ui/topbar.js` (Session dropdown 接入 `/api/session` POST/DELETE, Stage/Timeline chip 占位, Err 徽章订阅 `http:error`, ⋮Menu 跳 Diagnostics/Settings)
  - `static/ui/workspace_placeholder.js` (通用占位渲染器: 标题+说明+后续 todo tag 列表)
  - `static/ui/workspace_{setup,chat,evaluation,diagnostics,settings}.js` (5 个瘦 mount 入口)
  - `static/app.js` (引导: DOMContentLoaded → hydrateI18n → mountTopbar → mountTabbar → renderWorkspaces → 订阅 `active_workspace:change` 切 section + 懒挂载)
  - `templates/index.html` (#app 三段 grid: #topbar/#tabbar/#workspace-host + #toast-stack)
- 状态: done (2026-04-18)
- 子任务:
  - [x] testbench.css 全套样式 (含 chip/dropdown/cb/toast/modal/form)
  - [x] core/i18n.js + hydrateI18n
  - [x] core/state.js 单 store + 事件总线
  - [x] core/toast.js 四种 kind + auto-dismiss
  - [x] core/api.js fetch 封装 + openSse
  - [x] core/collapsible.js + Alt+Click 批量 + container toolbar
  - [x] ui/topbar.js Session dropdown 接入后端 + 所有 chip/menu 占位项 i18n 化
  - [x] ui/workspace_placeholder.js + 5 个 workspace 瘦 mount
  - [x] app.js 引导 + tab 路由 + 懒挂载
  - [x] templates/index.html 改为三段 grid
- 自测证据:
  - 所有 15 个静态资源 (CSS/JS/HTML) HTTP 200 下发
  - `GET /` 返回 845 字节最小 HTML 外壳, 真正 UI 由 `static/app.js` 客户端渲染
- 遗留: 无 (Stage/Timeline/Menu 若干项按计划占位, 显式 toast `"P14/P18/P21 后实装"`)

### [ ] P04 Settings workspace
- 目标: 可配置四组模型 (chat/simuser/judge/memory), 测试连通性
- 预期产物:
  - `static/ui/workspace_settings.js` 完整实现
  - `routers/config_router.py` (GET/PUT 四组 + /providers + /api_keys_status + /test_connection)
- 状态: pending

### [ ] P05 Setup workspace (Persona + Import 子页)
- 目标: Persona 编辑表单可改可存; Import 能从真实角色拷贝到沙盒
- 预期产物:
  - `static/ui/workspace_setup.js` (左 nav + Persona + Import 子页)
  - `routers/persona_router.py` (GET/PUT + /import_from_real/{name})
- 状态: pending

### [ ] P06 VirtualClock 完整滚动游标模型
- 目标: bootstrap / cursor / per_turn_default / pending_next_turn 全链路; Setup → Virtual Clock 三块 UI
- 预期产物:
  - `virtual_clock.py` 扩展完整 API
  - `routers/time_router.py` 全套端点
  - `static/ui/workspace_setup.js` Virtual Clock 子页
- 状态: pending

### [ ] P07 Setup Memory 四子页读写
- 目标: 可查看/编辑沙盒内 recent/facts/reflections/persona 文件
- 预期产物:
  - `routers/memory_router.py` (GET/PUT 四子资源)
  - `static/ui/workspace_setup.js` Memory 四子页
- 状态: pending

### [ ] P08 PromptBundle + Prompt Preview 双视图
- 目标: GET /chat/prompt_preview 返回 structured + wire; Chat 右侧面板可切换双视图
- 预期产物:
  - `pipeline/prompt_builder.py` (PromptBundle)
  - `routers/chat_router.py` 的 /chat/prompt_preview
  - `static/ui/workspace_chat.js` Prompt Preview 面板
- 状态: pending

### [ ] P09 Chat 消息流 + 手动 Send + SSE
- 目标: 可手动发 user/system 消息给 AI 并流式接收回复
- 预期产物:
  - `pipeline/chat_runner.py`
  - `routers/chat_router.py` 消息 CRUD + /send SSE + /inject_system
  - `static/ui/workspace_chat.js` 消息流 + Composer
- 状态: pending

### [ ] P10 记忆操作触发 + 预览确认
- 目标: 4 个记忆 op 的 dry-run/commit 端到端
- 预期产物:
  - `pipeline/memory_runner.py`
  - `routers/memory_router.py` /trigger /commit
  - UI 触发按钮 + 预览 drawer
- 状态: pending

### [ ] P11 假想用户 AI
- 预期产物: `pipeline/simulated_user.py` + SimUser 模式 UI + /chat/simulate_user
- 状态: pending

### [ ] P12 脚本化对话
- 预期产物: `pipeline/script_runner.py` + dialog_templates 合并加载 + Scripted 模式 UI + sample_*.json 2-3 个
- 状态: pending

### [ ] P13 双 AI 自动对话
- 预期产物: `pipeline/auto_dialog.py` + Auto-Dialog 模式 UI + /chat/auto_dialog/* SSE
- 状态: pending

### [ ] P14 Stage Coach
- 预期产物: `pipeline/stage_coordinator.py` + `routers/stage_router.py` + 顶栏 Stage chip 完整实现
- 状态: pending

### [ ] P15 ScoringSchema 一等公民
- 预期产物: `pipeline/scoring_schema.py` + 三套 builtin JSON + judge_router schema CRUD + Evaluation → Schemas 子页
- 状态: pending

### [ ] P16 四类 Judger + Run 子页
- 预期产物: `pipeline/judge_runner.py` + /judge/run + Evaluation → Run 子页
- 状态: pending

### [ ] P17 Results + Aggregate 子页 + 导出报告
- 预期产物: Evaluation Results/Aggregate 完整 UI + /judge/results + /judge/export_report + 内联评分徽章
- 状态: pending

### [ ] P18 快照/时间线/回退
- 预期产物: `snapshot.py` + session_store 自动建快照 + rewind_to + session_router 快照端点 + 顶栏 Timeline chip
- 状态: pending

### [ ] P19 Diagnostics 错误+日志核心
- 预期产物: 全局异常中间件 + Diagnostics Errors/Logs 子页
- 状态: pending

### [ ] P20 Diagnostics Paths/Snapshots/Reset
- 预期产物: Paths 子页 + health_router /system/paths /system/open_path + Snapshots 子页 + Reset 子页 (三级)
- 状态: pending

### [ ] P21 保存/加载核心
- 预期产物: `persistence.py` + session_router save/load/import + api_key 脱敏 + 顶栏 Session dropdown 完整
- 状态: pending

### [ ] P22 自动保存 + 启动时断点续跑
- 预期产物: autosave debounce + 启动扫描恢复 + Restore autosave UI
- 状态: pending

### [ ] P23 多格式多 scope 导出
- 预期产物: export_json/markdown/dialog_template + session_router 导出端点 + UI 入口
- 状态: pending

### [ ] P24 文档
- 预期产物: `tests/testbench_README.md` 完整
- 状态: pending

---

## 变更日志

记录每次计划调整 / 重大决策 / blocker 解决方案.

- **2026-04-17** 初始版本, 依据 [PLAN.md](./PLAN.md) 创建.
- **2026-04-18** P02 完成. 发现并修复 `tests/testbench/config.py` 与根 `config/` 包的命名冲突 (启动脚本移除 `tests/testbench/` 出 sys.path + `sandbox.py` 改用 `get_config_manager().app_name` 代替 `from config import APP_NAME`).
- **2026-04-18** P03 完成. 前端采用原生 ES modules + 单 store + 事件总线, 无构建步骤. 约定: 所有面向测试人员的 UI 文案走 `i18n(key)` 或 DOM 上的 `data-i18n=...` 属性; 新 workspace 实装时直接替换对应 `workspace_*.js` 的 `mount()` 即可, 不影响 tab 路由.
