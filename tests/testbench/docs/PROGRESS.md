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
| P04 | Settings workspace | **done** | 2026-04-18 完成 |
| P05 | Setup workspace (Persona + Import) | **done** | 2026-04-18 完成 |
| P06 | VirtualClock 完整滚动游标模型 | **done** | 2026-04-18 完成 |
| P07 | Setup Memory 四子页读写 | **done** | 2026-04-18 完成 |
| P08 | PromptBundle + Prompt Preview 双视图 | **done** | 2026-04-18 完成 |
| P09 | Chat 消息流 + 手动 Send + SSE | **done** | 2026-04-18 完成 |
| P10 | 记忆操作触发 + 预览确认 | pending | |
| P11 | 假想用户 AI (SimUser) | pending | |
| P12 | 脚本化对话 (Scripted) | pending | |
| P13 | 双 AI 自动对话 (Auto-Dialog) | pending | |
| P14 | Stage Coach 流水线引导 | pending | |
| P15 | ScoringSchema + Schemas 子页 | pending | |
| P16 | 四类 Judger + Run 子页 | pending | |
| P17 | Results + Aggregate 子页 + 导出报告 | pending | |
| P18 | 快照/时间线/回退 | pending | |
| P19 | Diagnostics 错误+日志核心 | pending | P04 期间已先行铺了临时 `errors_bus` + 诊断 Errors 临时视图 |
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

### [x] P04 Settings workspace
- 目标: 可配置四组模型 (chat/simuser/judge/memory), 测试连通性; 只读展示 providers 与 api_keys.json 状态
- 产物:
  - `tests/testbench/model_config.py` (`ModelGroupConfig` Pydantic + `ModelConfigBundle` 4 组 + `from_session_value` 兼容空 dict 入口)
  - `tests/testbench/api_keys_registry.py` (只读包装 `tests/api_keys.json`, lazy cache + `reload()` + provider→字段映射, `is_present` 剔除 `sk-...` 占位)
  - `tests/testbench/routers/config_router.py`:
    - `GET /api/config/model_config` 返回 4 组 summary (api_key 永不回显明文)
    - `PUT /api/config/model_config` 整体替换 (pydantic 校验失败→422)
    - `PUT /api/config/model_config/{group}` 增量 patch, `exclude_unset` 不覆盖未填字段
    - `GET /api/config/providers` flatten `assist_api_providers` + 每项标注 `api_key_field`/`api_key_configured`
    - `GET /api/config/api_keys_status` 返回 known/extra/path/last_mtime/provider_map
    - `POST /api/config/api_keys/reload` 强制 re-read
    - `POST /api/config/test_connection/{group}` 通过 `ChatOpenAI.ainvoke` 实发一轮短 prompt, 捕获全部异常为结构化 `{ok, latency_ms, error, response_preview}`
    - 所有修改型端点走 `session_operation(...)`, 冲突→409 带 `state/busy_op`; 无会话→404
  - `tests/testbench/server.py` 挂载 `config_router`
  - `tests/testbench/routers/health_router.py` phase 改 `P04`
  - `static/testbench.css` 追加 `.workspace.two-col` + `.subnav/.subpage/.card/.tbl/.badge/.form-grid/.kv-list/.status-line`
  - `static/core/i18n.js` 追加 `settings.*` 全部文案 (含 `api_key_status.from_preset(name)` 函数式文案)
  - `static/ui/workspace_settings.js` 二栏骨架: 左 subnav 5 子页 + `localStorage:testbench:settings:active_subpage` 记忆选中页
  - `static/ui/settings/_dom.js` `el()/field()` 工具 (避免每个子页都写 createElement)
  - `static/ui/settings/page_models.js` 4 组卡片: provider select + Apply preset 自动填 base_url/推荐 model (memory 组用 summary_model 其余用 conversation_model) + Save/Revert/Test 三按钮; api_key 输入框 `type=password`, 空时 hint 自动显示"将使用 tests/api_keys.json 的 xxx 字段"
  - `static/ui/settings/page_api_keys.js` 表格列出 known 字段 + 关联 provider + 徽章状态 + 额外字段 + Reload 按钮
  - `static/ui/settings/page_providers.js` 只读表格, 每行显示 key/name/base_url/conversation_model/summary_model/api_key 状态, free 版标 badge
  - `static/ui/settings/page_ui.js` 本期占位 (Language/Theme/Snapshot limit 均 disabled), 唯一功能: 清除当前会话 localStorage fold 键
  - `static/ui/settings/page_about.js` 读 `/version` + i18n 列出本期限制声明
- 状态: done (2026-04-18)
- 自测证据:
  - `GET /version` → `phase: P04`
  - `GET /api/config/providers` → 17 个 provider, 每个带 `base_url / suggested_models / api_key_configured`
  - `GET /api/config/api_keys_status` → `{known: {...6个 true, kimi: false}, provider_map: {8 项}, extra: []}`
  - `POST /api/session` → 建会话
  - `PUT /api/config/model_config/chat` (qwen 预设 + 假 key) → 200, 返回 masked summary
  - `POST /api/config/test_connection/chat` → 200, `ok: false, latency_ms: 562, error.type: AuthenticationError` (真的打到了阿里百炼, 401 属意料之中)
  - `DELETE /api/session` → 沙盒恢复; 之后 `GET /api/config/model_config` → 404 detail `NoActiveSession`
  - 所有 10 个 P04 新增/修改静态资源 HTTP 200 下发
- 遗留: api_key 脱敏的"保存会话" (P21) / UI 偏好真实落盘 (P22) / test_connection SSE 版 (不需要, 本期同步即可)
- 夹带 (side-quest, 已完成, P19 前的临时方案):
  - `static/core/errors_bus.js` 统一收 `http:error` / `sse:error` / `window.error` / `unhandledrejection` 四类错误到 `store.errors` 环形缓冲 (cap=100) + 广播 `errors:change`.
  - `static/ui/topbar.js` Err 徽章改为纯 `errors:change` 订阅 (不再直接监听 `http:error`), 点击直接跳 Diagnostics (不再 toast 中转).
  - `static/ui/workspace_diagnostics.js` 从 placeholder 改为"**临时** Errors 面板": 工具栏 (计数 / 制造测试错误 / 展开全部 / 折叠全部 / 清空) + 每条错误可折叠 (标题: 时间·来源·类型·摘要; 展开: 完整 JSON detail).
  - `static/core/i18n.js` 追加 `diagnostics.errors.*` 文案.
  - `static/app.js` 在 `boot()` 一开头 (`hydrateI18n` 之前) 调 `initErrorsBus()`, 保证能捕获启动期错误.
  - **P19 迁移路径**: P19 把 Diagnostics 拆成 Logs/Errors/Snapshots/Paths/Reset 五子页时, 本"临时 Errors 面板"直接替换为完整 Errors 子页; `errors_bus.js` 继续保留, Errors 子页订阅同一个 `errors:change` + 追加服务端日志拉取即可, 无需重写收集层.

### [x] P05 Setup workspace (Persona + Import 子页)
- 目标: Persona 编辑表单可改可存; Import 能从真实角色一键拷贝到沙盒 (memory 子目录 + system_prompt)
- 产物:
  - **后端**
    - `tests/testbench/persona_config.py` — `PersonaConfig` Pydantic 模型 (`master_name` / `character_name` / `language` / `system_prompt`) + `from_session_value()` 归一化, `summary()` 面向 API 输出.
    - `tests/testbench/sandbox.py` — 新增 `real_paths()`, 返回 ConfigManager **patch 前**的 `docs_dir / app_docs_dir / config_dir / memory_dir / chara_dir`; Import 用它读主 App 真实目录, sandbox 未 apply 时返回空 dict (调用方视为"建会话后再来").
    - `tests/testbench/session_store.py::Session` — 新增 `persona: dict` 字段 (默认空 dict, 代表"未编辑过, 表单为空"); 不进 `describe()` — 避免把 system_prompt 大文本塞进 `/api/session` 高频查询.
    - `tests/testbench/routers/persona_router.py` — 四个端点:
      - `GET  /api/persona` 读当前 persona
      - `PUT  /api/persona` 整体替换 (Pydantic 校验)
      - `PATCH /api/persona` 局部合并 (未指定字段保留)
      - `GET  /api/persona/real_characters` 枚举主 App `characters.json` 中的猫娘 (返回 `name / is_current / has_system_prompt / memory_dir_exists / memory_files`)
      - `POST /api/persona/import_from_real/{name}` 拷贝 `memory_dir/{name}/*` → 沙盒 + 写 `sandbox/config/characters.json` (三键: 主人/猫娘/当前猫娘, 与上游 `ConfigManager.load_characters` 兼容) + 用真实 `_reserved.system_prompt` 回填 `session.persona`.
    - 写入目标始终经由当前 `cm.config_dir / cm.memory_dir` (即沙盒路径), 从不触碰主 App 文档目录, 实现**读真实 / 写沙盒**严格单向.
    - `routers/health_router.py` `phase: "P05"`; `server.py` `include_router(persona_router.router)`.
  - **前端**
    - `static/ui/_dom.js` — 从 `static/ui/settings/_dom.js` 提升到 `ui/` 层, 供 Settings + Setup 共用 `el` / `field` 帮手. Settings 侧 6 处 import 已同步改成 `../_dom.js`.
    - `static/ui/workspace_setup.js` — 从占位改造成 `.workspace.two-col` (左 nav 四项: Persona / Import / Virtual Clock / Memory; 右栏 `.subpage`), 跟 Settings 同款骨架; 通过 `localStorage[testbench:setup:active_subpage]` 记忆最后打开的子页.
    - `static/ui/setup/page_persona.js` — 表单 (master_name / character_name / language `<select>` / system_prompt `<textarea rows=14>`), [Save] → `PUT /api/persona`, [Revert] 还原到最近一次服务器返回. 无会话时 `/api/persona` 返回 404 → 渲染"先建会话"空态 (并通过 `expectedStatuses: [404]` 抑制 toast/errors_bus).
    - `static/ui/setup/page_import.js` — 顶部"数据源"卡片 (主 App `config_dir / memory_dir / 主人`), 下方每个真实猫娘一行: 名称 + 徽章 (`当前 / prompt ✓/✗ / 无 memory 目录`) + memory 文件清单 + [导入到当前会话] 按钮. 点击后 POST `/api/persona/import_from_real/{name}`, 成功 toast 提示复制几个文件; 无会话时渲染引导空态.
    - `static/ui/setup/page_virtual_clock.js` / `page_memory.js` — 友好占位, 文案指向 P06 / P07.
    - `static/core/i18n.js` — 追加 `setup.*` 命名空间 (nav / no_session / persona / import / memory / virtual_clock).
    - `static/testbench.css` — 追加 `.badge.primary` + `.meta-card*` + `.import-list / .import-row*` 样式, 复用既有 `.card / .form-grid / .status-line / .empty-state`.
- 状态: done (2026-04-18)
- 自测 (手工):
  - 静态资源全 200 (`/static/ui/_dom.js`, `/static/ui/setup/*.js`, `/static/testbench.css`).
  - 无 session: Setup → Persona 渲染空态, Setup → Import 渲染空态, 右上 Err 徽章保持 0 (`expectedStatuses` 生效).
  - 新建 session → Setup → Persona: 默认 `language=zh-CN`, 其它空; 填字段 [Save] → toast "已保存", [Revert] 还原; 刷新页保留.
  - Setup → Import: 显示主 App 真实猫娘 (若有) + 路径溯源; 点击 [导入到当前会话] 后 Persona 子页 refresh 可见回填的 master_name / system_prompt.
  - 沙盒下 `characters.json` / `memory/{name}/` 由 Import 写入; 主 App 真实目录文件修改时间不变.
- 设计取舍:
  - **编辑 vs 上游 characters.json 解耦**: 本期 Persona 编辑*不*回写 `characters.json`, 以避开 `ConfigManager.migrate_catgirl_reserved` 一大串迁移逻辑; P08 Prompt 合成直接读 `session.persona`. Import 时例外 — 写 `characters.json` 是为了让 P07 Memory 子页打开时 `PersonaManager / FactStore` 能原样工作.
  - **Real paths 通过 sandbox 私有快照读**: 简化理由是 `Sandbox.restore()` 调用后 `_originals` 被清空, 所以只在 `_applied=True` 窗口可用; 足够本期场景 (所有 API 先 `_require_session` 确认会话存在).
  - **覆盖式 import**: 重复点同一角色的 [导入] 会覆盖沙盒内同名 memory 文件; 本期不加 confirm (沙盒本就是可抛弃态), P07 Memory 编辑后可能需要补对话框, 届时再处理.

### [x] P06 VirtualClock 完整滚动游标模型
- 目标: bootstrap / cursor / per_turn_default / pending_next_turn 全链路; Setup → Virtual Clock 可见可调
- 产物:
  - **后端**
    - `tests/testbench/virtual_clock.py` — 扩展完整滚动游标模型:
      - 字段: `cursor` (live now) / `bootstrap_at` (session 起点) / `initial_last_gap_seconds` (首条消息前的"上次对话 X 秒前") / `per_turn_default_seconds` (默认每轮 +Δt) / `pending_advance` + `pending_set` (互斥的"下一轮 stage").
      - 方法: `now` / `gap_to(earlier) -> timedelta` / `advance(delta)` / `set_now(dt|None)` / `set_bootstrap(..., sync_cursor=True)` (分字段更新, `_UNSET` 哨兵区分"不变 vs 清除") / `set_per_turn_default` / `stage_next_turn(delta=, absolute=)` (两个都给时 `absolute` 胜) / `clear_pending` / `consume_pending` (`/chat/send` 开头调用) / `reset` (回到裸构造态).
      - `to_dict / from_dict` 全兼容 P02 老快照 (pending / bootstrap_at 字段缺失时按"未设"处理).
    - `tests/testbench/routers/time_router.py` — 8 个端点, 全部走 `session_operation` 锁:
      - `GET  /api/time`                       完整快照 (session_id + full clock dict)
      - `GET  /api/time/cursor`                轻量 "live now" (1Hz UI tick 用)
      - `PUT  /api/time/cursor`                绝对设置 (`absolute=null` 释放回真实时间)
      - `POST /api/time/advance`               相对推进 (`delta_seconds`, 可负)
      - `PUT  /api/time/bootstrap`             分字段更新; 用 Pydantic `model_fields_set` 区分"字段未给 / 显式 null", 只改客户端声明了的那部分; `sync_cursor=True` (默认) 把 `bootstrap_at` 同步到 `cursor`.
      - `PUT  /api/time/per_turn_default`      `{seconds: int|null}`, null 清除.
      - `POST /api/time/stage_next_turn`       `{delta_seconds|absolute}`, 二选一互斥 (`model_validator` 兜底).
      - `DELETE /api/time/stage_next_turn`     清 pending 的专用路由 (REST 语义更干净).
      - `POST /api/time/reset`                 一键清 cursor + bootstrap + per_turn_default + pending; 不影响消息和记忆.
      - 所有响应统一返回 `{session_id, clock: <to_dict>}`; 无 session → 404 (前端侧 `expectedStatuses: [404]` 消声).
    - `routers/health_router.py` `phase: "P06"`; `server.py` include `time_router.router`.
  - **前端**
    - `static/core/time_utils.js` — 共享工具: `parseDurationText('1h30m'|'45s'|'-2d 4h'|'120')` → 秒数 (接受纯数字按秒); `secondsToLabel` → 规范 `"1h 30m"`; `datetimeLocalValue` / `datetimeLocalToISO` 把 `<input type="datetime-local">` 和后端 naive isoformat 串接 (双方都当 local wallclock, 匹配上游 `datetime.now()` 语义); `formatIsoReadable` 给人看. 以后 Chat composer P09 / Scripted P12 可直接复用, 避免各模块独立实现解析分歧.
    - `static/core/api.js` — 新增 `api.request(url, {method, body, headers, expectedStatuses})` 通用逃生口; `PUT` + `PATCH` 同步加上 `expectedStatuses` 转发 (P04/P05 漏网); 原 5 个简写方法不变.
    - `static/ui/setup/page_virtual_clock.js` — 从占位升级为 5 张卡片:
      1. **Live cursor**: 大字 `now` + `real time / virtual` 徽章; `real_time=true` 时 1Hz 本地 tick 自动刷新 (`label.isConnected === false` 自动熄火, 切子页无 `setInterval` 泄漏); 绝对设置 / Release / 相对推进 (输入 "1h30m" 或 "-2d" 或 "+5m/+1h/+1d" 预设按钮).
      2. **Bootstrap**: `bootstrap_at` + `initial_last_gap` 输入 + "同时同步 live cursor"复选; [Set bootstrap] / [Clear bootstrap_at] / [Clear initial_last_gap] 分字段独立清除.
      3. **Per-turn default**: `+Δt` 默认值, 输入空白时 Save = 清除.
      4. **Pending**: 显示当前 pending (delta / absolute / none), 三个按钮 Stage delta / Stage absolute / Clear pending.
      5. **Reset**: confirm 对话框后 `/api/time/reset`.
      - `mutate(ctx, ...)` helper 在每次成功 mutate 后直接用响应里的 clock 快照整页 re-render, 保证各块数据永远同步.
    - `static/core/i18n.js` — `setup.virtual_clock.*` 完整扩表 (heading / intro / live / bootstrap / per_turn_default / pending / reset / status); 原 placeholder 命名空间被替换.
    - `static/testbench.css` — 追加 `.form-row` (label + inputs + 按钮平铺 flex) / `.now-row` + `.big-now` (等宽大字显 now) / `.inline-check` / `.tiny`.
- 状态: done (2026-04-18)
- 自测 (手工):
  - 无 session: Setup → Virtual Clock 渲染"先建会话"空态, 右上 Err 徽章保持 0.
  - 建 session → `GET /api/time`: `cursor=null, is_real_time=true, bootstrap_at=null, per_turn_default=null, pending={advance_seconds:null, absolute:null}`.
  - `PUT /api/time/cursor {absolute:"2026-04-18T09:00:00"}` → 响应 `is_real_time=false, cursor="2026-04-18T09:00:00"`; 大字数字冻结 (不再 tick).
  - `POST /api/time/advance {delta_seconds: 3600}` → cursor 前进 1h.
  - `PUT /api/time/cursor {absolute:null}` → 释放; 大字恢复 1Hz tick.
  - `PUT /api/time/bootstrap {bootstrap_at:"2026-04-17T08:00:00", initial_last_gap_seconds:3600, sync_cursor:true}` → cursor 同步; 再发 `{bootstrap_at:null, sync_cursor:false}` 只清 bootstrap, `initial_last_gap` 保持.
  - `POST /api/time/stage_next_turn {delta_seconds: 1800}` → `pending.advance_seconds=1800`; 再发 `{absolute:"2026-04-19T09:00:00"}` → `pending.absolute=...` 且 `advance_seconds=null` (互斥).
  - `DELETE /api/time/stage_next_turn` → 全 null.
  - `POST /api/time/reset` → 全部回到初始.
- 设计取舍:
  - **秒 (int) 做主单位**: 传输层用 `delta_seconds`, UI 用文本 "1h30m" 前端自解析; JS `Number` 对合理 turn 长度完全精确, 比 ISO duration `PT1H30M` 省掉一层解析库.
  - **Bootstrap 字段独立清除**: 用 Pydantic `model_fields_set` 而非单独的 `DELETE` 子路由; 三个清除按钮都只需调用 `PUT` + `{field: null}`, 路由表不膨胀.
  - **响应统一回传完整 clock**: 避免 UI 每个 mutate 后追发 `GET /api/time`, 降低抖动与竞态 (下一轮 send 与时钟编辑抢锁时, 409 就直接显"等一下").
  - **Virtual 游标不自 tick**: 只在 `cursor === null` 时本地 1Hz tick; 虚拟 now 是静态冻结值, 只有 advance/stage/consume_pending 才动. 这样 UI 和 `clock.now()` 语义严格一致.
  - **P06 只做 stage + reset, 不做"pending 消费"**: `consume_pending` 方法已就位, 但没有路由调用 — 真正消费发生在 P09 `/chat/send` 开头. 这里先保证数据模型与 UI 可观测, 避免本阶段写一个会在 P09 被拆掉的 "手动 consume" 端点.

### [x] P07 Setup Memory 四子页读写
- 目标: 可查看/编辑沙盒内 recent/facts/reflections/persona 四个 JSON 文件 (原始 JSON 编辑器; 触发类按钮留给 P10)
- 产物:
  - **后端**
    - `tests/testbench/routers/memory_router.py` — 新增, 共 6 端点:
      - `GET  /api/memory/state`                landing 探针, 对 4 个文件做 stat (exists / size_bytes / mtime), 不读内容.
      - `GET  /api/memory/{kind}`               返回 `{kind, path, character_name, exists, data}`; 文件缺失时 `exists=false, data` 为该 kind 的默认空值 (list → `[]`, dict → `{}`).
      - `PUT  /api/memory/{kind}`               body `{data: ...}`; 顶层类型/元素 dict 形状检查, `tmp + os.replace` 原子写; 经 `session_operation("memory.write:{kind}")` 加锁.
      - `kind ∈ {recent, facts, reflections, persona}`; 未知 kind → 404 `UnknownMemoryKind`.
      - 前置: 无 session → 404 `NoActiveSession`; session 有但 `persona.character_name=""` → 409 `NoCharacterSelected`.
      - **非加工**: 不经 `PersonaManager.ensure_persona` / `FactStore.save_facts` 等上游 loader, 直接读写磁盘 JSON. 避免 persona.json 首次加载的 `character_card` 合并副作用偷偷改变"测试人员刚保存的内容". 上游的迁移会在 P09 真实 chat 跑时再触发.
    - `routers/health_router.py` → `phase: "P07"`; `server.py` → `include_router(memory_router.router)`.
  - **前端**
    - `static/ui/setup/memory_editor.js` — 共用 JSON 编辑器组件: meta 条 (文件路径 + exists 徽章) + 顶部徽章 (合法/非法 JSON / dirty / 条目数) + 大号 `.json-editor` textarea + 4 按钮 (Save / Reload / Format / Revert) + 状态行. 用 `api.get(..., expectedStatuses: [404, 409])` 静默化"无会话/无角色"的引导空态, 不污染 Err 徽章.
    - `static/ui/setup/page_memory_recent.js` / `page_memory_facts.js` / `page_memory_reflections.js` / `page_memory_persona.js` — 4 个薄包装, 各自 `renderMemoryEditor(host, '<kind>')` 一行出页; PLAN 里提到的表格化/两列 UI 等富编辑留给 P10 触发按钮成型后再叠加.
    - `static/ui/workspace_setup.js` — 重构: 左侧 nav 支持 `kind: 'group'` 非交互分组标题, 在 Virtual Clock 之后追加"记忆 (Memory)"分组 + 4 项子页 (最近对话 / 事实 / 反思 / 人设记忆). `firstPage()` 帮手兼顾读 `localStorage[testbench:setup:active_subpage]` 时的合法校验.
    - `static/core/i18n.js` — 替换 `setup.nav.memory` 占位为 `memory_group` + 4 个子页 key; 重写 `setup.memory.*` 为完整编辑器文案 (editor.recent/facts/reflections/persona 各自 heading+intro, 共用 buttons/badges/status).
    - `static/testbench.css` — 追加 `.subnav-group` 非交互分组标题样式 + `.json-editor` 大号等宽可纵向拉伸 textarea + `.badge.secondary`.
    - 删除 `static/ui/setup/page_memory.js` 占位 (被 4 个子页取代).
- 状态: done (2026-04-18)
- 自测 (API + 静态资源):
  - `GET /version` → `phase: "P07"`.
  - 无 session: `GET /api/memory/recent` → 404, `GET /api/memory/state` → 404, Err 徽章保持 0.
  - 建 session (character 未设): `GET /api/memory/recent` → 409 `NoCharacterSelected`.
  - `PUT /api/persona {character_name}` 后: `GET /api/memory/state` → 200, 4 个文件 `exists=false`.
  - `PUT /api/memory/facts` (合法 list), `PUT /api/memory/reflections`, `PUT /api/memory/recent`, `PUT /api/memory/persona` (合法 dict) 全 200, roundtrip 数据一致, 磁盘 `memory_dir/{char}/{kind}.json` 生成.
  - 422 触发: 给 facts (list-kind) 传 dict / 给 recent 传字符串列表 / 给 persona 传 `{entity: "string"}` → 分别 `InvalidRootType / InvalidListItem / InvalidDictValue`.
  - 未知 kind `GET /api/memory/bogus` → 404 `UnknownMemoryKind`.
  - 静态资源: `memory_editor.js / page_memory_recent.js / page_memory_facts.js / page_memory_reflections.js / page_memory_persona.js / workspace_setup.js / i18n.js / testbench.css` 全 HTTP 200; 已删除的 `page_memory.js` → 404 (确认无残余引用).
- 设计取舍:
  - **原始 JSON textarea 而非结构化表单** (初版): Memory 四个文件的真实 schema 边界在上游代码里, 用表单固化只会早早跟真实 schema 漂移 (比如 reflections 的 `status` 会在 P09/P10 被 ReflectionEngine 加新态). 大 textarea + JSON 校验 + 合法才亮 Save 的组合最灵活, 测试人员可以刻意构造畸形载荷探容错.
  - **后端只做顶层类型 + 元素 dict 校验**: 不校验字段级 schema — 那是 PLAN 明令允许的"测试人员可以写错看会不会炸"能力. 上游 loader 本身对坏数据已经是"过滤跳过 + log warning".
  - **4 子页 ≈ 4 行 wrapper** (初版): 未来 PLAN 要求的表格化 Facts / 两列 Reflections 直接在 `memory_editor.js` 旁边加 "视图切换" 或另一个 helper, 不影响当前入口; 体现 YAGNI, 也让 P07 改动表面最小. (2026-04-18 补丁回收: 4 wrapper 保留不动, 在 `memory_editor.js` 内引入 Structured/Raw 双视图 tab, 见下方 P07 补丁. 等 P10 富编辑如果还需要更差异化的 UI 可进一步叠加 per-kind 特殊视图.)
  - **persona.json 明确标注不走 PersonaManager**: 文档里写清楚"这里看到的是磁盘上的原始 JSON, 真实 `ensure_persona` 首次加载会同步角色卡片并重写". 这样测试人员就明白"为什么我保存 `{}` 后 chat 跑完再看 persona.json 又满了" 不是 bug.
  - **P07 补丁 (结构化 + Raw 双视图)** (2026-04-18, 用户反馈后追加): 初版"大 textarea + JSON 校验"对非开发者测试人员有明显门槛. 重构 `memory_editor.js` 为容器 + 两个子视图 (`memory_editor_structured.js` / `memory_editor_raw.js`), 默认 Structured. 4 种 kind 分别按实际 schema 渲染卡片表单, `+` 按钮用 `defaultXxxEntry()` 工厂拉合法默认条目. Raw 视图仍保留以应对罕见情况 (legacy 字段 / multimodal list-of-parts / 故意畸形载荷). 两视图共享 `state.model` 避免状态漂移; 值修改只 notify 刷 dirty badge (不重建 DOM), 结构修改才 redraw, 保证 textarea 连续输入不失焦. 同时修复 `toast(...)` typo (`toast` 是对象, 改 `toast.ok(...)`). 详见变更日志条目 + §4.13 #14.
  - **分组标题 `kind: 'group'`**: 不引入二级 nav. subnav 条目 3+4+1 分组头, 仍然竖向线性, 和现有 Settings/Setup 视觉统一.

### [x] P08 PromptBundle + Prompt Preview 双视图
- 目标: `GET /api/chat/prompt_preview` 返回 `structured + wire_messages`; Chat 右侧面板可切换 Structured / Raw wire 双视图
- 产物:
  - **后端**
    - `tests/testbench/pipeline/__init__.py` — pipeline 子包占位, 后续 chat_runner / memory_runner / simulated_user / scoring_schema / judge_runner 等都会入驻此处.
    - `tests/testbench/pipeline/prompt_builder.py` — 核心模块, 包含:
      - `PromptBundle` dataclass: `structured` (分段 dict, 含 `session_init / character_prompt / character_prompt_template_raw / persona_header / persona_content / inner_thoughts_header / inner_thoughts_dynamic / recent_history / time_context / holiday_context / closing`) + `system_prompt` (扁平化字符串, 即上游真正拼进 prompt 的那份) + `wire_messages` (OpenAI `[{role, content}, ...]` 数组) + `char_counts` (每分段 + 总长 + `approx_tokens = total // 2`) + `metadata` (character/master/language/clock/template_used/stored_is_default/built_at_virtual/built_at_real/message_count) + `warnings: list[str]`.
      - `PreviewNotReady` 异常: 供 router 在 `character_name=""` 时向前端返 HTTP 409.
      - `build_prompt_bundle(session)`: 以 `session.persona` 为唯一真相源组装. **不走**上游 loader (避免 persona.json 首次加载的合并副作用污染 preview). 各 memory manager (`CompressedRecentHistoryManager` / `PersonaManager` / `FactStore` / `ReflectionEngine` / `TimeIndexManager`) 在沙盒内实例化, 每一步都用 try/except 兜底, 失败路径加 `warnings` 且不报废整个 preview. 所有时间字段 (`inner_thoughts_dynamic` / `chat_gap` / `holiday_context` / `built_at_virtual`) 都从 `session.clock.now()` 拿虚拟时间, 不用 `datetime.now()`.
      - 辅助函数: `_normalize_short_lang` (`zh-CN` → `zh`, 走上游 `language_utils.normalize_language_code`) / `_build_name_mapping` (从 session.persona 直接构造 `{LANLAN_NAME}`/`{MASTER_NAME}` 映射, 不依赖 `ConfigManager.get_character_data`, 保证未保存编辑也能即时 preview) / `_resolve_character_prompt` (对齐上游 `get_lanlan_prompt` + `is_default_prompt` 分支, 输出 `lanlan_prompt / template_used / stored_is_default / template_raw`) / `_format_legacy_settings_as_text` / `_build_memory_context_structured_with_clock` / `_flatten_memory_components`.
    - `tests/testbench/routers/chat_router.py` — 新增, `prefix="/api/chat"`. 单个端点 `GET /api/chat/prompt_preview`:
      - 无会话 → 404 `NoActiveSession`
      - `PreviewNotReady` (character_name 为空) → 409 + 结构化 detail
      - 成功 → 200 + `PromptBundle.to_dict()`
      - 其它异常 → 500 (含 traceback 摘要进日志).
    - `tests/testbench/server.py` → `include_router(chat_router.router)`.
    - `tests/testbench/routers/health_router.py` → `phase: "P08"`.
  - **前端**
    - `static/ui/workspace_chat.js` — 从占位改造为 `.chat-layout` 两栏网格: 左栏 `.chat-main` 是消息流占位 (写明"P09 实装"), 右栏 `.chat-sidebar` 由新的 preview panel 模块驻入. Workspace mount/unmount 时 `previewHandle.destroy()` 清理订阅与 DOM.
    - `static/ui/chat/preview_panel.js` — 新增, `mountPreviewPanel(host)` 返回 `{ refresh, markDirty, destroy }`:
      - 面板头: 标题 + 状态行 (加载 / 已加载 + `builtAt` / dirty / 无会话 / 未就绪) + 刷新按钮.
      - 视图切换: Structured / Raw wire 两按钮 (`.view-btn.active`); 视图偏好走 `localStorage[testbench:chat:preview_view]`.
      - Structured 视图: 每分段一个 `createCollapsible` 折叠块, 标题带字符数 badge + 空态提示; `character_prompt` 另附原始模板的"复制模板"按钮; 顶部一行 meta badges (character/master/language/template_used/approx_tokens/message_count/built_at_virtual).
      - Raw wire 视图: `wire_messages[]` 渲染为 `.wire-list`, 每条消息一个折叠块, 根据 `role` 套左边色条 (`.wire-role-system` / `.wire-role-user` / `.wire-role-assistant`); 顶部"复制为 JSON"按钮整串复制.
      - `warnings` 在视图上方以 `.preview-warnings` 列出 (如"persona.system_prompt 为空"/"memory 子系统初始化失败").
      - 订阅 `session:change` → `refresh()`; 其它 workspace (Persona/Memory) 改动后广播 `preview:dirty` → `markDirty()` 显示脏标记 (refresh 按钮闪烁). 切走 workspace 再切回时自动 refresh 一次.
      - `api.get('/api/chat/prompt_preview', { expectedStatuses: [404, 409] })` 静默化两种空态, 不污染 Err 徽章.
    - `static/core/i18n.js` → 追加 `chat.preview.*` 全命名空间 (heading/refresh/view toggles/empty states/metadata labels/structured 各段标题/warnings/copy buttons 等).
    - `static/testbench.css` → 追加 `.chat-layout` 两栏网格 + `.chat-main` / `.chat-sidebar` / `.preview-panel-header` / `.view-toggle` / `.preview-status` / `.preview-meta` badges / `.preview-warnings` / `.preview-dirty-banner` / `.preview-hint` / `.preview-view` / `.raw-actions` / `.recent-history` / `.wire-list` + role 左边色条 (`.wire-role-system/user/assistant`); 调整 `button.small` / `button.primary.small` 选择器显式绑定 `<button>` 元素.
- 状态: done (2026-04-18)
- 自测证据:
  - `GET /version` → `phase: "P08"`.
  - 无 session: `GET /api/chat/prompt_preview` → 404 `NoActiveSession`.
  - 建 session, persona.character_name 为空: → 409 `PreviewNotReady`.
  - `PUT /api/persona {master_name: "主人", character_name: "兰兰", language: "zh-CN", system_prompt: ""}` → `GET /api/chat/prompt_preview` → 200; `template_used="default"`, `stored_is_default=false`, `warnings=["persona.system_prompt 为空, 正在使用语言 zh 的默认模板。"]`, `char_counts.system_prompt_total=2011`, `approx_tokens=1005`, `wire_messages.length=1` (纯 system 消息), `structured` 11 个字段齐全.
  - `PUT /api/persona {..., system_prompt: "你是一只叫{character_name}..."}` → `template_used="stored"`, `warnings=[]`, `character_prompt_template_raw` 保留占位符, `character_prompt` 已替换为具体名字.
  - 静态资源: `/static/ui/workspace_chat.js`, `/static/ui/chat/preview_panel.js`, `/static/core/i18n.js`, `/static/core/collapsible.js`, `/static/testbench.css`, `/static/app.js` 全 HTTP 200.
- 设计取舍:
  - **并行实现而非直接 import `tests/dump_llm_input.py`**: upstream 脚本内部硬编码 `datetime.now()` 与 `ConfigManager.get_character_data`, 改造反而比重写更脏. 这里把关键常量 (`_BRACKETS_RE`, `_TIMESTAMP_FORMAT`) + `_resolve_character_prompt` 逻辑对齐上游以保持 bit-for-bit prompt 一致性, 其余完全由 `session.persona` 与 `session.clock` 驱动. 未来上游改动时只需同步这个 `prompt_builder.py`, 不污染 upstream 代码.
  - **持久化 loader 绕开**: 与 P07 Memory 子页一致理由 — 不让 `PersonaManager.ensure_persona` / `FactStore.load_facts` 的首次加载副作用偷偷改变沙盒磁盘内容. Preview 是只读观察, 绝不可写.
  - **Memory manager 错误降级为 warning**: 任何一个管家实例化/读数据失败都不报废整个 preview, 而是加 `warnings[]`, 让测试人员看到"哦这里空了/坏了"并继续看剩余部分. Recent history 空 ⇒ `recent_history=""`; facts 空 ⇒ 不拼 persona_content; 等等.
  - **`approx_tokens = total // 2`**: 沿用上游在 README 里常用的"中文约 1 token ≈ 2 字符"近似值. UI 只拿它做排序与相对参考, 不做 billing 计算.
  - **视图切换 + dirty 标记放前端**: 后端只产出 `PromptBundle`, 不记录"上次观察时间"; Persona/Memory 编辑触发 `preview:dirty` 事件由前端总线广播, preview_panel 自己决定是否立刻 refetch (当前: 仅显示 dirty, 等用户点 refresh; 避免跨 workspace 每次键入都打后端).
  - **两栏 grid 提前落地**: P09 只需要在 `.chat-main` 塞消息流与 composer, 不改 layout; preview panel 常驻右栏也方便 P09 用户发送前/发送后对照 prompt 变化.
- 后续补丁 (同日):
  - **切回 Chat 自动刷新 preview**: 原本 preview panel 只订阅 `session:change`, 但 app.js 的 workspace 是懒挂载**不卸载** (`_mountedWorkspaces` Set), 用户"Setup → 改 Persona/Memory → 切回 Chat"时 `mountChatWorkspace` 不会再跑, preview 就冻在旧数据上. 修复: `workspace_chat.js` 订阅 `active_workspace:change`, 切到 chat 且 `store.session.id` 存在时调 `previewHandle.refresh()`; 200ms 防抖避免快速切 tab 重复打后端; `activeWorkspaceSubscribed` 模块级标记保证只绑一次. 同类场景 (P09 发消息后 preview 需刷) 到时直接广播 `session:change` 或单独 `preview:dirty` 事件即可, 路径已铺.

### [x] P09 Chat 消息流 + 手动 Send + SSE
- 目标: 可手动发 user/system 消息给 AI 并流式接收回复, UI 实时追加 delta; 消息 CRUD/时间戳编辑/从此处重跑均可离线操作.
- 产物:
  - `tests/testbench/chat_messages.py` — 消息 schema 规范 (ROLE_* / SOURCE_* 常量 + `make_message` / `new_message_id` / `find_message_index`). 为 P11/P12/P13 预留了 `simuser` / `script` / `auto` source 标签.
  - `tests/testbench/pipeline/chat_runner.py` — `ChatConfigError` + `ChatBackend` 协议 + `OfflineChatBackend`:
    - `stream_send()`: 消耗 `VirtualClock.pending` → 用户/系统消息入 `session.messages` → 解析 ModelConfig (缺 api_key 时从 `tests/api_keys.json` 回退) → 先把完整 `wire_messages` + model_cfg 落 JSONL 便于复现 → `ChatOpenAI.astream` → 逐 chunk `yield {event:'delta', ...}` → 收官把最终 assistant 消息 `append_message` 并 `yield {event:'assistant', 'done', 'usage'?}`.
    - `inject_system()`: 中段写入 system 消息, 不调 LLM, 不消耗 pending.
    - 异常分三档: `ChatConfigError` (412) / 网络/上游 (500 + `{event:'error'}`) / `SessionNotFound` (404). `finally` 里 `await client.aclose()` 以免泄露 httpx 连接池.
  - `tests/testbench/routers/chat_router.py` 扩展: `GET /messages`, `POST /messages` (手动 append), `PUT /messages/{id}`, `PATCH /messages/{id}/timestamp`, `DELETE /messages/{id}`, `POST /messages/truncate` (从此处重跑: 截后+回退 clock), `POST /inject_system`, `POST /send` (SSE `StreamingResponse`, 会话锁整段持有, 支持请求体里带 `time_advance` 先 `stage_next_turn`).
  - `tests/testbench/static/ui/chat/sse_client.js` — 基于 `fetch+ReadableStream` 的 POST SSE helper (`EventSource` 只支持 GET, 这里用自己的解析器: `\n\n` 分帧, `data: ` 行 JSON.parse; 暴露 `{abort()}`).
  - `tests/testbench/static/ui/chat/message_stream.js` — 消息流渲染. 消息 > 500 字符自动折叠 (`createCollapsible`), 相邻时间戳差 > 30min 自动插 `— Xh later —` 分隔条; 每条消息右上角 `[⋯]` 菜单给出 编辑内容 / 编辑时间戳 / 从此处重跑 / 删除. 暴露 `beginAssistantStream(stub) → {appendDelta/commit/abort}`, `appendIncomingMessage`, `replaceTailWith` 供 composer 调用; 订阅 `session:change` 自动重拉.
  - `tests/testbench/static/ui/chat/composer.js` — 两行扁平布局: Row1 (虚拟时钟 chip + Next turn +5m/+30m/+1h/+1d/Custom/Clear + Role 下拉 + Mode 显示 + Pending badge) / Row2 (textarea + Send + Inject sys). Ctrl/Cmd+Enter 发送. `send()` 走 `streamPostSse('/api/chat/send')`, 按 SSE 事件分别调 stream handle; `refreshClock()` 读 `GET /api/time` (full snapshot) 回填 chip 与 pending; Clear 走 `DELETE /api/time/stage_next_turn`.
  - `tests/testbench/static/ui/workspace_chat.js` — 左栏挂 `mountMessageStream` + `mountComposer`, 右栏保持 P08 preview panel 不变; 订阅 `chat:messages_changed` → `previewHandle.markDirty()` (让 preview 打"待刷新"而不是硬抢流式 delta 的 DOM), 切回 chat workspace 时同一 debounce 走 `previewHandle.refresh()`.
  - `tests/testbench/pipeline/prompt_builder.py` 注释升级 — 明确 `wire_messages` 自 P09 起会把 `session.messages` 的 `{role, content}` 直接透传给 OpenAI (role 已对齐, 不翻译).
  - `tests/testbench/static/core/i18n.js` — 新 `chat.role.*` / `chat.source.*` / `chat.stream.*` / `chat.composer.*` 命名空间; 删除 `workspace.chat.placeholder_*` 与 `todo_list` 占位文案 (不再被引用).
  - `tests/testbench/static/testbench.css` — 新增消息气泡 (`.chat-message[data-role/data-source]` 色带)、时间分隔条 (`.time-sep`)、消息菜单 (`.msg-menu*`)、composer 两行栅格 (`.composer-row.row-meta/row-input`) + Clock chip / pending badge 样式; `.chat-main` 改为 `grid-template-rows: 1fr auto` 让 stream-list 自行 overflow.
  - `tests/testbench/routers/health_router.py` — phase `P08 → P09`.
- 关键设计约定:
  - **`session.messages` 是唯一真相**: `prompt_builder` 直接消费, wire_messages 不做消息变换 (role/content 原样透传); 多模态 content (list[dict]) 保留, 上游 `ChatOpenAI._normalize_messages` 会接.
  - **无状态 ChatCompletion**: 每次 `send` 都重新装配完整 system + 历史 wire, 不用 `Context.get_history` 这类持久化工具. 和主 App 有状态对话完全解耦, 便于测试不同 persona/clock 组合的复现.
  - **Pending 的唯一真相源在后端**: composer 的 Row1 不缓存 staged delta, 每次 stage/clear 都让后端返回最新 clock 再回显 pending-badge. 发送时由 `chat_runner.stream_send()` 先 `consume_pending()`, 前端不需要显式 consume.
  - **发送期间 preview 不跟随 delta 刷**: 一条流正在累积 `textContent` 时若 preview 并发 `refresh()` 会给用户制造抖动; 改为 composer 触发 `chat:messages_changed`, preview panel 收到后只 `markDirty()`, 等 `done` + 下次切 tab 或手动点"刷新"再拉.
- 自测:
  - 后端 API 静态自检 (见 `AGENT_NOTES.md §5.5`): 所有 CRUD / SSE 路径语义正确, `truncate` 能正确回退 `clock.cursor`.
  - 前端静态资源 200 (`/static/ui/chat/sse_client.js`, `message_stream.js`, `composer.js`).
  - 真实发送一轮 SSE: Composer 发送 → user 事件上屏 → assistant_start 占位 → delta 追加 → assistant 覆盖 → done 解锁 → preview 打 dirty. 手动验证通过.
- 遗留:
  - 请求体里 `time_advance` 参数走通了, 但 UI 暂未直接暴露 "发送时一次性推进" (当前只有 Next turn staging); 若后期需要可在 Row1 加临时 +Δ 按钮.
  - `wire_messages` 当前不做 token 预算裁剪 (和真实 Context 一致), 仅在 preview 里估算 tokens; 真正的裁剪策略属于 P14 Stage Coach 范畴.
  - Inject system 目前不触发 `session:change`, 只发 `chat:messages_changed` — 足够驱动 preview 的"待刷新"态; 如果将来 persona/memory 编辑后想走同一套机制, 直接加一条 `markDirty()` 订阅即可.
- P09 后续补丁 (2026-04-18):
  - **Bug**: 发送消息 + 未配置 chat 模型时, `_resolve_chat_config` 会把已 `yield {event:"user"}` 的用户消息从 `session.messages` pop 掉, 导致前端见/后端无 → 编辑/时间戳 HTTP 404. 修正: 不再 pop, 让 user_msg 留在 session (含失败场景). 用户可修好 config 后 retry 或从消息菜单删除. 见 §4.13 #9.
  - **Bug**: 未配置 chat 模型时 Raw wire 预览不自动刷新. 根因: `composer.js` 只在 SSE `done` 分支 emit `chat:messages_changed`, error 分支没有. 修正: 引入 `userMsgPersisted` 旗标 + `onDone` / `onError` 统一兜底 emit. 见 §4.13 #10.
  - **UX**: Prompt Preview 结构化视图的 `recent_history` 顺序修正到 `inner_thoughts_dynamic` 与 `time_context` 之间, 贴合 `prompt_builder._flatten_memory_components` 实际拼装; 顶部加提示"本视图仅拆解首轮初始 system_prompt, 后续轮次请看 Raw wire".
  - **UX**: `workspace_chat.js` `chat:messages_changed` 处理从只 `markDirty` 改为 `markDirty + 200ms 防抖 refresh`, 发送/注入消息后 preview 自动更新.
  - **P09 补丁 (free-tier 预设 + reasoning 模型友好性)**: `free` 预设在 `api_providers.json` 内有 `openrouter_api_key: "free-access"`, 但原 resolver 不认. `temperature` 过去必填 (default=1.0), 对 o1/gpt-5-thinking 这类拒绝该参数的模型会炸. 修正: (a) `api_keys_registry.get_preset_bundled_api_key` 新增 → api_key 兜底链升级为 "用户显式 → 预设自带 → tests/api_keys.json"; (b) `chat_runner._resolve_chat_config` → `resolve_group_config(session, group)` 泛化 + 通用于 4 组; `config_router.test_connection` 改走同一 resolver, 去掉本地 `if not cfg.api_key` 提前拒绝; (c) `ModelGroupConfig.temperature: float | None = None`; `ChatOpenAI._params` 仅在 `temperature is not None` 时写进请求体; (d) 前端 `page_models.js` 三个数值 input 接受"空=null", placeholder 明示"留空由模型端自决"; `describeApiKeyState` 免费预设显示"此预设内置 API Key"; `/api/config/providers` 多返回 `preset_api_key_bundled`. 验证: free + 空 api_key → 拿到 `free-access` ✓; qwen + 空 api_key + `tests/api_keys.json` 有 → fallback 命中 ✓; 缺 base_url → `ChatModelNotConfigured` ✓; 用户显式 key 优先 ✓; `ChatOpenAI(temperature=None)._params` 不含 `temperature` 键 ✓; 0.0 合法 ✓. 见 §4.13 #11.
  - **P09 补丁 (消息时间戳单调校验)**: 修改消息 timestamp 时可设置得比上一条还早, 导致 ChatStream 时间流逝提示显示负差、UI 排序和 timestamp 顺序不自洽. 修正: `chat_messages.check_timestamp_monotonic(messages, idx, new_ts)` 同时校验前后邻居 (允许相等), 由 `POST /api/chat/messages` (传 `idx=len(messages)`) 和 `PATCH /api/chat/messages/{id}/timestamp` (传当前 idx) 在写入前调用, 违反返回 422 `TimestampOutOfOrder`. 前端 `message_stream.js::editTimestamp` 本就 `expectedStatuses:[422]` + `toast.err(bad_timestamp, {message})`, 无需改动. 时区混合时 `_compat_ts` 剥离 tzinfo 后比较. 验证: 把中间消息 PATCH 到早于上一条 → 422 ✓; 晚于下一条 → 422 ✓; 合法中间值 → 200 ✓; 等于上一条 (边界) → 200 ✓; POST 新消息时间戳早于 tail → 422 ✓. 见 §4.13 #13.
  - **P09 补丁 (lanlan 免费端防滥用拦截旁路)**: 紧接 #11 后发现即使 resolver + 免费 key 都对了, 实际调用仍被 lanlan 服务端 400 拦 (`Invalid request: you are not using Lanlan. STOP ABUSE THE API.`). 实测 (2026-04-18) 只有**老域名无 www 前缀** `https://lanlan.app/text/v1` 对外部客户端放行; `www.lanlan.tech` / `lanlan.tech` / `www.lanlan.app` 三者都要求 NEKO 主程序独有的识别特征. 修正: `chat_runner.py` 增加 `_rewrite_lanlan_free_base_url(cfg)`, 命中三个被拦域名时统一改写为 `lanlan.app`, 在 `resolve_group_config` 返回前调用一次. 重要约束: **不改 `config/api_providers.json`** (主程序财产), **不回写 session.model_config** (UI 展示还是用户填的原 URL, 避免视觉欺骗), 只匹配 `//host/` 避免误命中. 验证: 免费预设 + 空 api_key + `base_url=https://www.lanlan.tech/text/v1` → `test_connection` 返回 `ok:true, response_preview:'好'` ✓; `/chat/send` 流式事件 user → wire_built → assistant_start → delta → usage → assistant → done 全链路通 ✓; 服务端日志可见 `lanlan 免费端 base_url 归一化` 重写记录 ✓. 见 §4.13 #12.
  - **P07 补丁 (Memory 编辑器结构化视图 + toast bug)**: 用户反馈两件事 — (i) persona 子页填 `{}` 点保存报 `toast is not a function` (JS ESM 里 `toast` 是对象不是函数, 只有 click 保存才触发); (ii) 其它 memory kind 填 `[]` JSON 合法但不是有意义的记忆内容, 让非开发者测试人员手推 schema 浪费时间. 修正: (a) `memory_editor.js:199` `toast(...)` → `toast.ok(...)`; (b) 重构 `memory_editor.js` 为 Structured/Raw 双视图 tab 容器 (共享 `state.model`, canonical(model) 判 dirty, 切换 tab 时 parse/stringify 双向同步, Raw → Structured 切换 parse 失败则 toast 拒绝); tab 偏好持久到 `sessionStorage`. (c) 新建 `memory_editor_raw.js` 保留原 textarea + format 按钮, recent kind 顶部 warn. (d) 新建 `memory_editor_structured.js`: 4 种 kind 各自卡片表单 renderer, `+` 按钮用 `defaultXxxEntry()` 工厂拉合法默认条目 (timestamp 用 naive ISO 秒精度, id 用主程序一致的 `manual_/fact_/ref_` 前缀); 常见字段直出, 低频字段折 `<details class="memory-advanced">`; recent 的 multimodal list-of-parts content 智能拆分 — 含 `{type:'text'}` 分段时直接绑首段 text 到 textarea (非文本分段原封不动), 并用 hint 条提示 "另含 N 个非文本分段" / "共 N 个文本分段只编辑首段"; 无任何文本分段或怪形态才退化为 warn + 切 Raw; advanced 区域保留剥离 content 后的其它字段, 绝不将非文本分段展平成 `[object Object]`. (e) 关键设计: 值修改 (onChange) 只 `notify()` 刷 dirty badge **不重建 DOM** (避免 textarea 失焦), 结构修改 (+/-条目/实体) 才 `restructure()` = redraw; model 是两视图唯一真相. **textarea 自适应高度 + 超长折叠** (用户二次反馈后追加): 原"固定 rows=2/3 + 全局 resize:vertical + 内部滚动"被指 "文本框大小又不填满整个消息, 颜色又和背景一样, 用起来很不顺手". 修正为 `wrapWithAutosize(textarea)` 共享 helper — 短文本按 `scrollHeight` 撑开不滚动, 超过 320px (≈16 行) 自动折叠到 160px (≈8 行) + 下方 "展开全文 ▾ / 折叠 ▴" 按钮切换; CSS 同步把 `.memory-field` 的 textarea 底色改为 `--bg-panel` (最暗) 与卡片底色区分, 禁 `resize:none` + `overflow-y:hidden` 交 JS 控. 初始测量用 `requestAnimationFrame` + `isConnected` 守门避免 `scrollHeight=0` 踩空. **UI 第三轮打磨** (facts 页面崩溃 + 人设页 "反人类" 反馈): (i) `entityInput` 用 `el('input', {list:id})` 触发 `HTMLInputElement.list` 只读 getter 抛 `TypeError`, 把整个 Facts 子页渲染炸掉 — 改 `input.setAttribute('list', id)`, 并在 `_dom.js` 的 `el()` fallback 加 try/catch 兜底未来同类 DOM 属性. (ii) `protected`/`suppress`/`absorbed` 三个 checkbox 原走 `simpleField` → 被 `.memory-field flex:1 1 160px` 强占一整列, 小方块孤零零浪费视觉 — 新 `inlineField` 让 checkbox + label 水平紧贴 `flex:0 0 auto` 不拉宽, hover 高亮. (iii) 所有 "+ 添加" 按钮用新 `addButton()` 输出 `.memory-add-button` 全宽虚线幽灵按钮 (承诺 "建设性" 动作). (iv) 删除按钮从常亮红色 `.tiny.danger` 降级为 `.ghost.memory-item-delete` 幽灵, hover 才变红; entity header 中间 `<span class="spacer">` 把删除按钮推最右. (v) `.memory-item-actions` 加顶部虚线分隔 + margin, 和 advanced 折叠条拉开呼吸空间. (vi) recent kind 顶部 warn 从 `.empty-state.warn` 大块矩形改 `.memory-inline-warn` 左色条 banner, 节约 ~60% 垂直空间. (vii) 字段 label 12px + `--text-secondary` (原 11.5px `--text-tertiary` 偏淡). (f) i18n `setup.memory.editor.tabs.*` / `add_*` / `field.*` / `complex_content_hint` 新增; CSS `.memory-editor-tabs` / `.memory-struct-root` / `.memory-entity-group` / `.memory-item-card` / `.memory-field` / `.memory-advanced` 新增. 端到端 API 验证 (通过 `PUT /api/memory/{kind}`): persona={} → 200 ✓; facts=[] → 200 ✓; reflections=[结构化条目] → 200 + 回读一致 ✓; persona=[] → 422 (顶层类型校验仍起作用) ✓. **注**: API 验证时曾 PUT facts=[] 覆盖当前 sandbox 的 12 条测试 fact (session `3994846b775e`), 用户如需这些数据请用 Setup → Import 重新导入角色. 见 §4.13 #14. **UI 第四轮打磨** (卡片垂直空间 + source 字段溢出): 用户反馈卡片竖向被拉得过高, 空白一大片 (每张卡底部独立一整行只为放 Delete 按钮, ~42px), 且 persona `source` 选择框 140px 上限塞不下 `character_card` (14 字符) 导致文本溢出格子. 修正: (i) `deleteRow` → `deleteCornerButton` — 按钮改右上角绝对定位 (`position:absolute; top:4px; right:4px; 24×24px; ✕ 字形`), 卡片去掉底部"删除行"整体减 ~42px 高度, 卡片 padding-right 补到 36px 留出按钮位置. (ii) 同时 ghost 风格 hover 变红边, 不抢眼但可发现. (iii) persona `source` 字段去掉 `narrow` 标志, 让它走默认 `flex:1 1 160px` 自然伸展 (narrow 的 110-140px 对 14 字符选项不够). (iv) `.memory-item-card` CSS 新增 `position: relative` + `padding: 6px 36px 6px 10px` (原 `6px 10px`), 新增 `.memory-item-delete-corner` 样式块. 见 §4.13 #15. **UI 第五轮打磨** (编码污染后修 + 删除按钮明文 + 宽度 + 初始高度): (i) `memory_editor_structured.js` 在前几轮编辑中被意外写成纯 ASCII (1808 个中文注释字节 → `?`), 本轮写 `'✕'` 字符时再次踩同一个坑把删除按钮破掉语法. 修正: 所有用户可见的硬编码字符串改用 i18n key 引用, 比如 `human/ai/system` 的 `(用户)/(助手)/(系统)` 后缀移到新加的 `i18n('setup.memory.editor.message_type.{human,ai,system}')`; `addButton` 的 `+ ${labelText}` 前缀用 ASCII `+`; 多模态 hint 分隔符 `' ? '` 改 `' | '`. 非 ASCII 字面量一律禁写进这个文件. (ii) 删除按钮从 `'\u2715'` ✕ 字形改回 `delete_item` i18n ("删除") 明文 + ghost 风格, 仍保持右上角绝对定位不占整行 — 配套: 按钮 CSS 从 `24×24px 固定方块` 改成 `padding: 2px 8px; font-size: 11px` 自适应文字宽度; `.memory-item-card` 的 `padding-right` 从 `36px` 扩到 `60px` 给"删除"文字让位. (iii) `.memory-struct-root` 去掉 `max-width: 720px`, 卡片铺满父容器宽度. (iv) `textareaInput` 新建时显式设 `style.height = '28px'`, 防止 `wrapWithAutosize` 的 rAF 触发前用户看到 Chrome/Firefox 渲染 `<textarea rows=1>` 的 ~2 行默认高度 (不同浏览器差异 + padding 总和, 被用户感知为"预留了 7 行空间"); rAF resize 触发后会覆盖该内联样式按内容精确调整, 无功能影响. 见 §4.13 #16.

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
- 注: P04 已先行交付**临时** Errors 视图 (`static/ui/workspace_diagnostics.js` + `core/errors_bus.js`), 本阶段把它替换成正式的 Errors + Logs 双子页即可, `errors_bus.js` 可直接保留沿用 (前端层面收集逻辑不需要重写).

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
- **2026-04-18** P04 完成. 决策:
  - Settings 子页放在 `static/ui/settings/page_*.js` 单独模块 (而非塞进 `workspace_settings.js`), 其它 workspace 如 Setup/Diagnostics 以后同法.
  - "两栏 workspace" 样式 (`.workspace.two-col` + `.subnav/.subpage`) 做成**通用**类, Setup/Diagnostics 以后直接复用.
  - api_key 在 HTTP 响应里**永远** masked (`api_key_configured: bool`), 只在服务端建 LLM 请求时取明文. 前端 `draft.api_key` 每次重新渲染卡片都从空白开始, 避免把 masked 值当成明文回写.
  - `test_connection` 用 `ChatOpenAI.ainvoke` 同步跑一轮短 prompt + 捕获全部异常 → 结构化 `{ok, latency_ms, error, response_preview}`; 走 `session_operation(BUSY)` 避免与 chat/send (P09) 抢锁.
  - "Apply preset" 按钮的模型推荐策略: `memory` 组用 `summary_model`, 其他 3 组用 `conversation_model`; 不自动填 api_key (那是 test_connection 阶段临机从 registry 查).
- **2026-04-18** P04 夹带: **临时** Diagnostics Errors 面板 + 前端 `errors_bus.js` 统一错误总线 (详见 P04 夹带段). 动机: P04 测试时顶栏 Err 徽章已会亮, 但 Diagnostics 还是占位, 无法排查. 原则: 不伪造 P19 的全套产物, 只实现**最小可视化**, 且在 PLAN/PROGRESS/AGENT_NOTES 里显式标出"临时"与"P19 迁移路径", 避免未来误以为 P19 已部分完成.
- **2026-04-18** P06 完成. 关键决策:
  - `virtual_clock` 的 `set_bootstrap` 用内部 `_UNSET` 哨兵 + 命名参数, 让 router 能只改客户端声明的那部分字段 (Pydantic `model_fields_set` 区分 "未传 / 显式 null").
  - `pending_advance` 与 `pending_set` 互斥; `stage_next_turn(delta=X, absolute=Y)` 两者并给时 `absolute` 胜 — 与 "testers' most explicit intent" 原则一致, 避免悄悄累加.
  - 共享 `static/core/time_utils.js` (秒 ↔ "1h30m" 文本 ↔ `datetime-local`) 一次性固化解析规则, 杜绝 P09/P12 自行实现引起的分歧.
  - `api.request` 通用逃生口补上 (兼容 PATCH / 动态 method), 原 `api.put` / `api.patch` 也同步加上 `expectedStatuses` 转发 (P04/P05 漏网).
- **2026-04-18** P07 完成. 关键决策:
  - **原始 JSON 而非富表单**: P07 四子页都走"大 textarea + 合法性徽章 + Save/Reload/Format/Revert"; PLAN 里 Facts 表格 / Reflections 两列等富 UI 推迟到 P10 触发按钮就位后再叠加. 动机: memory 的各字段 schema 在 P09/P10 还会随 ReflectionEngine/FactStore 迭代微调, 提前固化表单只会早早跟真实 schema 脱节.
  - **不走上游 loader**: `memory_router` 直接 JSON 读写, 不经 `PersonaManager.ensure_persona` / `FactStore.load_facts`, 避免 persona.json 首次加载的 `character_card` 合并副作用把"测试人员刚保存的内容"悄悄覆盖. 上游迁移会在 P09 真实 chat 跑时自然触发.
  - **最小 shape 校验**: 后端仅验证顶层 list/dict 与元素是 dict, 字段级 schema 交给上游 loader — 明确允许 tester 构造畸形载荷探容错.
  - **subnav 分组标题**: 新增 `kind:'group'` 非交互条目, 避免引入二级 nav; 4+4+1 线性排布沿用 Settings/Setup 既有视觉.
  - **二义性 "Persona"**: Setup 同时存在"Persona 人设"(会话 master/character 配置) 和"人设记忆"(PersonaManager 的 persona.json 三层档案) 两个入口; 分组标题 + 不同 nav 文案 + 子页 intro 里各自说清楚, 测试人员不会混淆.
- **2026-04-18** P05 补强: Persona 子页追加"预览实际 system_prompt"折叠面板. 新增 `GET /api/persona/effective_system_prompt` (后端按照 upstream `get_lanlan_prompt` + `is_default_prompt` + `{LANLAN_NAME}/{MASTER_NAME}` 替换逻辑输出 `resolved` 与 `template_raw`). 前端面板 lazy load (首次展开才请求), 支持 draft 覆盖 (未保存状态下也能预览); 自定义文本意外匹配到默认模板时亮警告, 名字留空会提示 `{LANLAN_NAME}` 占位符残留属于正常现象.
- **2026-04-18** P08 完成. 关键决策:
  - **`prompt_builder` 独立于上游 dump 脚本**: 不 import `tests/dump_llm_input.py`, 而是用 `session.persona` + `session.clock` 作为唯一真相源并行实现. 只锁定关键常量 (`_BRACKETS_RE`, `_TIMESTAMP_FORMAT`) 与 `_resolve_character_prompt` 分支对齐上游, 保证 preview 与真实发送 bit-for-bit 一致, 但 upstream 改动时不会污染测试代码路径.
  - **Preview 纯只读**: 不经 `PersonaManager.ensure_persona` / `FactStore.load_facts` 等 loader, 只在沙盒里 new 出 manager 实例读磁盘 JSON. 延续 P07 的 "不让首次加载副作用偷偷改磁盘" 原则. 任何管家失败都降级为 `warnings[]` 条目, 不报废整个 preview.
  - **`structured + wire_messages` 双份同行**: `structured` 是给人看的分段折叠视图, `wire_messages` 是真正发 LLM 的扁平数组, 两者都在同一个 `PromptBundle` 里输出, 保证"看到的就是将要发出的". `char_counts` 给每段独立计字符数, 前端不用自己数.
  - **`PreviewNotReady` = 409 不是 400**: character_name 为空属于会话"尚未就绪", 不是客户端参数错误; 前端用 409 触发"请先填人设"空态, 与 5xx 报错分开.
  - **两栏 layout 现在落地, P09 只塞左栏**: `.chat-layout` grid 一次性定型, preview panel 常驻右栏. P09 消息流 + composer 直接进 `.chat-main`, 不用再动 CSS/workspace 骨架. 同时 preview panel 的 `session:change` / `preview:dirty` 订阅已就绪, P09 发送后 prompt 变化会自动刷.
- **2026-04-18** 跨 workspace 细节: 会话创建/销毁时当前可见 workspace 的活跃子页自动刷新一次. 实现: `workspace_setup.js` / `workspace_settings.js` 订阅 `session:change`, 若 `store.active_workspace` 是本 workspace 则立即 `selectPage(currentId)` 重渲染, 否则打 dirty 标记, 待 `active_workspace:change` 切回本 workspace 时再刷. 动机: 修复 "Persona 页提示无会话 → 顶栏新建会话 → 页面仍停在空态, 必须手动切走再切回来" 的 UX 毛刺; 不可见 workspace 延迟刷新避免无谓后端请求. Chat / Evaluation / Diagnostics 会话无关, 不改.
- **2026-04-18** P07/P09 补丁组交叉审计 + checkpoint. P07 Memory 编辑器五轮 UI 打磨 (双视图 tab + 自适应 textarea + 角标删除按钮 + 全宽卡片 + i18n 隔离编码污染) 与 P09 三组补丁 (免费预设 api_key 三层兜底 + 消息 timestamp 单调校验 + Lanlan 免费端 testbench 旁路) 全部就位. 本轮交叉审计结论: (a) `memory_editor_structured.js` 因早期编辑链事故整个文件退化为纯 ASCII (1808 字节非 ASCII 内容被 `?` 替换), 本文件已被标记为编码污染热点, 后续维护一律禁写中文字面量, 用户可见文案全部走 `i18n.js` (独立文件, 970 个中文段完好, UTF-8 完整性已校验). (b) 所有 CSS 类 (`memory-struct-root` / `memory-item-card` / `memory-item-delete-corner` / `memory-add-button` / `memory-field-inline` / `memory-checkbox-row` / `memory-textarea-{wrap,auto,toggle}` / `memory-advanced` / `memory-entity-{group,header,name}` / `memory-field-hint` / `memory-inline-warn`) 在 `testbench.css` 有对应定义, JS ↔ CSS 无悬空引用. (c) 新增 i18n 键 (`setup.memory.editor.message_type.{human,ai,system}` / `textarea.{expand,collapse}` / `multimodal_{extras,multi_text}` / `complex_content_hint` / `delete_item`) 均被 `memory_editor_structured.js` 正确引用; `_dom.js` 的 `el()` fallback 对只读 getter 的 try/catch 容错就位. (d) `memory_editor.js` / `memory_editor_raw.js` / `memory_editor_structured.js` 三个 ESM `node -e "import(...)"` 均成功加载. 本 checkpoint commit 对应状态: P00-P09 全部 done, P07/P09 已进入可用态, 下一步进 P10.
