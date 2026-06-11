# N.E.K.O. Testbench 架构概览 (ARCHITECTURE_OVERVIEW)

> 面向**二次开发者 / 新接手 agent / 代码审查者**的架构导航文档.
>
> - 读者假设: 已经**跑过一次** testbench, 大致知道 Setup / Chat / Evaluation
>   / Diagnostics / Settings 几个 workspace 是干什么的; 想动代码或要做
>   PR review, 但没时间啃 2400 行 `P24_BLUEPRINT.md` + 900 行
>   `LESSONS_LEARNED.md` + 4000+ 行 `AGENT_NOTES.md`.
> - 本文目标: **15-30 分钟读完**即可定位 "这个功能的代码在哪" + "改这里
>   要小心什么" + "写新功能应该把边界落在哪里".
> - 本文**不**重复: 历史阶段的实施细节 (留在 `PROGRESS.md`) / 踩坑复盘
>   (留在 `LESSONS_LEARNED.md`) / 具体用法操作 (留在 `testbench_USER_MANUAL.md`
>   / `external_events_guide.md`).
>
> **版本**: 对齐 `config.py::TESTBENCH_VERSION = 1.1.0` (P25 外部事件注入
> sign-off 基线). 后续版本若修改**模块拓扑 / 顶层事件总线 / 边界契约**,
> 必须同步更新本文.

---

## 目录

- [第一部分: 概念图谱](#第一部分-概念图谱) — 设计哲学 + 关键术语 + 跨组件数据流
  - [§1.1 Testbench 是什么, 不是什么](#11-testbench-是什么-不是什么)
  - [§1.2 五大设计哲学](#12-五大设计哲学)
  - [§1.3 关键术语速查表](#13-关键术语速查表)
  - [§1.4 顶层数据流 (一张图看懂)](#14-顶层数据流-一张图看懂)
- [第二部分: 后端模块拓扑](#第二部分-后端模块拓扑) — Python 侧
  - [§2.1 目录速查](#21-目录速查)
  - [§2.2 启动路径 (`run_testbench.py` → `server.py` → 各 router)](#22-启动路径)
  - [§2.3 Session / Sandbox / ConfigManager 三位一体](#23-session-sandbox-configmanager-三位一体)
  - [§2.4 Pipeline 层 (`pipeline/`)](#24-pipeline-层-pipeline)
  - [§2.5 Router 层 (`routers/`)](#25-router-层-routers)
  - [§2.6 持久化 / 自动保存 / 快照](#26-持久化-自动保存-快照)
- [第三部分: 前端模块拓扑](#第三部分-前端模块拓扑) — JS 侧
  - [§3.1 目录速查](#31-目录速查)
  - [§3.2 boot 流程 (`app.js`)](#32-boot-流程-appjs)
  - [§3.3 事件总线 (`state.js` + `errors_bus.js` + `sse_events.js`)](#33-事件总线)
  - [§3.4 渲染哲学: `renderXxx()` + 不可变 state snapshot](#34-渲染哲学)
  - [§3.5 `renderDriftDetector` (dev 模式断言)](#35-renderdriftdetector)
- [第四部分: 关键子系统](#第四部分-关键子系统)
  - [§4.1 Chat 对话四模式](#41-chat-对话四模式)
  - [§4.2 Memory 三层 + 5 Op + Preview/Commit](#42-memory-三层-5-op-previewcommit)
  - [§4.3 Stage Coach (六阶段引导)](#43-stage-coach-六阶段引导)
  - [§4.4 Evaluation (Schema + Run + Results + Aggregate + Export)](#44-evaluation)
  - [§4.5 External Event Injection (P25)](#45-external-event-injection-p25)
  - [§4.6 Diagnostics 五子页](#46-diagnostics-五子页)
  - [§4.7 Virtual Clock](#47-virtual-clock)
- [第五部分: 横切关注点](#第五部分-横切关注点)
  - [§5.1 原子写入 (`atomic_io`)](#51-原子写入)
  - [§5.2 LLM wire stamp chokepoint (`wire_tracker`)](#52-llm-wire-stamp-chokepoint)
  - [§5.3 Injection audit chokepoint (`injection_audit`)](#53-injection-audit-chokepoint)
  - [§5.4 SSE 协议 (`sse_events.py`)](#54-sse-协议)
  - [§5.5 i18n (`static/core/i18n.js`)](#55-i18n)
- [第六部分: 开发者上手 cheatsheet](#第六部分-开发者上手-cheatsheet)
  - [§6.1 "我想加一个 X" 该从哪里下手](#61-我想加一个-x-该从哪里下手)
  - [§6.2 常见陷阱速查](#62-常见陷阱速查)
  - [§6.3 smoke 套件导航](#63-smoke-套件导航)

---

## 第一部分: 概念图谱

### §1.1 Testbench 是什么, 不是什么

**是什么**: 主程序 N.E.K.O. 的 **"测试员门面"** (tester-facing harness).
主程序是一只带 2D 虚拟形象 + 三层记忆 (recent / facts / reflections) +
persona + 多外部触发源 (avatar 道具 / agent 回调 / proactive 主动搭话)
的 AI 桌宠. Testbench 剥离所有实时渲染 / 多进程 IPC / 硬件交互,
**只保留一套可手动驱动的 web UI**, 让非程序员测试员可以:

1. 准备一个 persona + 三层记忆的初始状态;
2. 用四种方式**对该 AI 发消息** (手动 / 假想用户 / 脚本回放 / 双 AI 对打);
3. 手动触发**记忆合成** (压缩 / 事实抽取 / 反思);
4. 用四类评分器**打分比较**不同 persona × 不同 memory 设置 × 不同
   模型下的回复质量;
5. 把完整过程**导出**成 Markdown / JSON / CSV 报告.

**不是什么**:

- ❌ **不是主程序的真实运行时**. Testbench 没有 avatar 渲染, 没有硬件
  交互, 没有 async task callback, 没有真实 cooldown 冷却窗口. 它只
  **复现主程序的 "语义契约"** (prompt 如何拼 / memory 如何存 / role 如
  何分), 不复现 "运行时机制" (帧合并 / 多进程 queue / 实时冷却). 这个
  边界是**冻结设计**, 见 `LESSONS_LEARNED §1.6 语义契约 vs 运行时机制`
  + `semantic-contract-vs-runtime-mechanism` skill.
- ❌ **不是面向终端用户的产品**. 所有 UI 文案 / 错误 / 操作路径都按
  "测试员理解 + 开发者审查" 优化, 不管 "用户体验". 术语直接用
  `persona.resolve_corrections` / `dedupe_window_hit` / `last_llm_wire`
  这种实装名词.
- ❌ **不是通用 benchmark 平台**. 四类 Judger (pairwise / head-to-head /
  single-turn / full-session) 只是 "够用", 不和 MMLU / HellaSwag 这类
  基准对标.

### §1.2 五大设计哲学

这五条是 P00-P24 开发过程中**反复验证**且已**冻结**的设计原则.
修改模块或加新功能前**必读**.

1. **单活跃会话 + asyncio.Lock + 状态机 (`session_store.py`)**.
   整个进程**只有一个 active session**. 切换会话 (new / switch / load)
   都走**单一 `asyncio.Lock`**, 配合状态机 (Idle / Loading / Ready /
   Destroying) 防止"创建还没走完就有请求打进来" 的竞态. 这条来自 P11-P13
   的血泪教训 (见 `AGENT_NOTES §4.7`), 导致项目绝对不支持**并发多会话**
   (想测多会话请开多个 testbench 实例, 用不同端口).

2. **代码 / 数据严格分离 (`config.py::CODE_DIR` vs `DATA_DIR`)**.
   `tests/testbench/` 下**一切 git-tracked**, 只能读; `tests/testbench_data/`
   下**整体 .gitignore**, 运行时才创建. 测试员保存的会话 / 自定义 schema
   / 自定义 dialog template / 导出报告全部落在 DATA_DIR.

3. **Preview / Commit 两阶段 (Memory Op 全部适用)**.
   所有 memory 操作 (recent.compress / facts.extract / reflect /
   persona.resolve_corrections / persona.add_fact) 都**先 preview**
   返回 "将要写入什么", tester 审核后**才 commit**. 这让 "误跑一次
   LLM 花费 token" 和 "污染 memory 状态" 两件事解耦: preview 只跑 LLM
   不写盘, commit 只写盘不跑 LLM. UI 在 `[Dry-run]` 按钮下对 tester
   暴露这个分阶段, 是 testbench 的**核心交互惯用法**.

4. **事件总线驱动 `renderXxx()` 全量重绘 (前端)**.
   前端**不做**"partial DOM patch". 所有状态变更 (session 变 / messages 变 /
   errors 变) emit 事件到 `state.js` / `errors_bus.js` / `sse_events.js`
   的总线, 订阅者的回调只做一件事: 调 `renderXxx()` 重新渲染**对应
   section 的全量 DOM**. 这规避了 "DOM 状态和 JS 状态漂移" 这种前端最
   常见的 bug. 代价是对中到大列表 (messages > 200 条) 有性能问题, 但
   testbench 是测试工具, UI 流畅度不是首要目标.

5. **Single-writer chokepoint + 纸面原则的静态守护**.
   任何 "X 必须统一走 Y" 的不变量都落在一个 chokepoint helper 上
   (例子: `pipeline/messages_writer.append_message` 唯一写入
   `session.messages`; `pipeline/wire_tracker.record_last_llm_wire` 唯一
   stamp `session.last_llm_wire`; `pipeline/injection_audit.scan_and_record`
   唯一记录 prompt injection). 更进一步, 用 smoke 套件
   (`p24_lint_drift_smoke.py`, `p25_llm_call_site_stamp_coverage_smoke.py`)
   **静态**扫代码, 发现绕过 chokepoint 的调用就 FAIL, 让"纸面原则"
   有机械保证. 详见 `LESSONS_LEARNED §7.6 / §7.25 / L43`.

### §1.3 关键术语速查表

| 术语 | 含义 | 权威源 |
|---|---|---|
| **Session** | 一次测试会话. 包含 persona / memory / chat messages / 快照 / 虚拟时钟 cursor. 唯一 active. | `session_store.py::Session` |
| **Sandbox** | 主程序 `ConfigManager` 的本地目录隔离区. 每 Session 一个独立目录, 在 `DATA_DIR/sandboxes/<session_id>/`. | `sandbox.py` |
| **ConfigManager** | 主程序的**进程单例**对象, 管理 persona / memory 的加载与存储. Testbench 通过 sandbox 替换其 root path 实现会话级隔离, **但不替换 API 配置** (API 配置全 testbench 共享, session 切换时**必须重载**). | `main_logic.config_manager` (主程序) |
| **PromptBundle** | 发给 LLM 的 prompt 的**两种表达**: `structured` (给代码处理) + `wire` (给 LLM 看的 messages 列表). **session.messages 是唯一事实源**, wire 按 messages 构造. | `pipeline/prompt_builder.py` |
| **Session messages** | `session.messages` 列表, 唯一真相. 所有 chat turn / external event / role=system reply 最终都经 `messages_writer.append_message` 写入. | `pipeline/messages_writer.py` |
| **three-tier memory** | recent (短期对话缓冲) / facts (中期事实) / reflections (长期反思) 三层, 再加 persona (角色卡). 分别对应 `memory/recent.json` / `memory/facts.json` / `memory/reflections.json` / `persona.json` 四个文件. | `main_logic.memory.*` (主程序) |
| **Stage Coach** | 六阶段引导: `persona_setup` → `memory_build` → `prompt_assembly` → `chat_turn` → `post_turn_memory_update` → `evaluation` (chat_turn ↔ post_turn_memory_update 循环). 状态机只 suggest + advance, 不阻塞. | `pipeline/stage_coordinator.py` |
| **Virtual Clock** | 可手动拖拽的时间游标. 只影响 "XXX ago" 类相对时间渲染, 不影响真实墙钟. 解决 "测试员不等 30 分钟就要看 30 分钟后的记忆效果" 的刚需. | `virtual_clock.py` + `pipeline/prompt_builder.py::time_manager` |
| **Judger** | 评分器. 四类: pairwise (两两比较) / head-to-head (每对 N 轮对决) / single-turn (单条打分) / full-session (全轮评分). 都接受 `ScoringSchema` 定义的评分维度. | `pipeline/judge_runner.py` + `pipeline/scoring_schema.py` |
| **Preview / Commit (Memory Op)** | memory op 的**两阶段 API**. preview 调 LLM + 返回 "将要写入什么", commit 只写盘不跑 LLM. | `routers/memory_router.py` + `pipeline/memory_runner.py` |
| **External Event** | P25 引入的三类 "运行时 prompt 注入 + 写 memory" 事件: avatar interaction / agent callback / proactive chat. | `pipeline/external_events.py` |
| **last_llm_wire stamp** | `Session.last_llm_wire` 字段. 每次 LLM 调用走 `wire_tracker.record_last_llm_wire` 留痕, 给 UI `Prompt Preview` 面板显示 "最近一次 LLM 实际发了什么". | `pipeline/wire_tracker.py` |
| **Diagnostics op** | `diagnostics_store` 的 ring buffer 一条条目. 每条 op 有 id (`session.create`, `memory.recent.compress_preview`, ...) + level + payload + ts. | `pipeline/diagnostics_store.py` + `pipeline/diagnostics_ops.py` |
| **Snapshot** | 对某次 chat commit 后的 session 状态快照. 可在快照上 "编辑历史" / "re-run" / "rewind". 最多 30 份 (`SNAPSHOT_MAX_IN_MEMORY`). | `pipeline/snapshot_store.py` |
| **Autosave** | 每会话滚动 3 份的自动存档, 默认 24h 保留. 崩溃重启后 boot 时提示恢复. | `pipeline/autosave.py` |
| **Sandbox orphan** | 没有对应 active session 的沙盒目录. boot_self_check 扫, Diagnostics → Paths 子页 triage. | `pipeline/boot_self_check.py` |

### §1.4 顶层数据流 (一张图看懂)

```
 ┌──────────── 浏览器 ─────────────┐
 │  app.js (boot)                  │
 │    → state.js (session:change)  │      SSE stream
 │    → workspace_xxx.js           │ ◄──────────────┐
 │      ├─ workspace_setup         │                │
 │      ├─ workspace_chat          │                │
 │      ├─ workspace_evaluation    │                │
 │      ├─ workspace_diagnostics   │                │
 │      └─ workspace_settings      │                │
 │                                  │                │
 │  api.js (fetch)  ───────────────┼────── HTTP ───►┤
 └──────────────────────────────────┘                │
                                                     ▼
                   ┌─────────── FastAPI ───────────────┐
                   │  server.py (create_app)            │
                   │    → routers/                      │
                   │       ├─ session_router  ──────────┤
                   │       ├─ config_router            │
                   │       ├─ persona_router           │
                   │       ├─ memory_router            │
                   │       ├─ chat_router      (SSE)   │
                   │       ├─ judge_router     (SSE)   │
                   │       ├─ stage_router             │
                   │       ├─ snapshot_router          │
                   │       ├─ diagnostics_router       │
                   │       ├─ security_router          │
                   │       ├─ external_event_router    │
                   │       ├─ time_router              │
                   │       └─ health_router            │
                   │                                    │
                   │    → pipeline/  (业务逻辑)         │
                   │       ├─ prompt_builder           │
                   │       ├─ messages_writer  (chkpt) │
                   │       ├─ chat_runner              │
                   │       ├─ memory_runner            │
                   │       ├─ judge_runner             │
                   │       ├─ external_events          │
                   │       ├─ wire_tracker     (chkpt) │
                   │       ├─ injection_audit  (chkpt) │
                   │       ├─ snapshot_store           │
                   │       ├─ autosave                 │
                   │       ├─ session_export           │
                   │       └─ diagnostics_{ops,store}  │
                   │                                    │
                   │    → session_store (单活跃会话)     │
                   │       └─ Session (sandbox + clock) │
                   └────┬───────────────────────────────┘
                        │
                        ▼
                ┌─── 主程序调用 ───┐
                │ config_manager   │  (ConfigManager, 进程单例)
                │ memory.*          │
                │ llm_client       │
                │ prompts_*        │  (pure helper)
                └──────────────────┘
                        │
                        ▼
             ┌── DATA_DIR (gitignore) ──┐
             │ sandboxes/<sid>/         │  (persona/memory)
             │ saved_sessions/           │  (人工存档)
             │   + _autosave/            │  (滚动 3)
             │ logs/<sid>-YYYYMMDD.jsonl │
             │ exports/                  │
             │ scoring_schemas/          │
             │ dialog_templates/         │
             └──────────────────────────┘
```

---

## 第二部分: 后端模块拓扑

### §2.1 目录速查

```
tests/testbench/
├── run_testbench.py       # CLI 入口 (argparse + uvicorn.run)
├── server.py              # FastAPI app factory (create_app)
├── config.py              # 版本号 / 路径常量 / 保留策略
├── session_store.py       # 单活跃会话 + asyncio.Lock + 状态机
├── sandbox.py             # ConfigManager 的 sandbox 生命周期
├── chat_messages.py       # session.messages 列表的 shape helper
├── persona_config.py      # persona schema + 校验
├── model_config.py        # LLM 模型配置
├── api_keys_registry.py   # api_key 存取 + 脱敏
├── virtual_clock.py       # 虚拟时钟 + time_manager
├── logger.py              # JSONL session logger + anon logger
├── pipeline/              # 业务逻辑 (~30 个文件, 见 §2.4)
├── routers/               # HTTP 端点 (13 个, 见 §2.5)
├── static/                # 前端: core/ + ui/ (见第三部分)
├── templates/             # Jinja2 (只有 index.html 壳子)
├── dialog_templates/      # 内置脚本模板 (.json)
├── scoring_schemas/       # 内置评分 schema (.json)
├── presets/               # persona 内置预设
├── smoke/                 # 18 份 Python smoke + 1 份 Node.js UI smoke (见 §6.3)
├── docs/                  # 本文所在目录, 全项目文档入口
└── _subagent_handoff/     # 运行时 subagent 交付目录 (见 L33.x)
```

### §2.2 启动路径

```
uv run python tests/testbench/run_testbench.py [--port 48920] [--host 127.0.0.1]
  │
  ▼
run_testbench.py::main()            # argparse + log level + live_runtime_log 安装
  │
  ▼
uvicorn.run("tests.testbench.server:app", ...)
  │
  ▼
server.py::create_app()             # FastAPI 工厂 (模块级 app = create_app())
  │
  ├── config.ensure_code_support_dirs()  # 保证 docs/ static/ templates/ 存在
  ├── config.ensure_data_dirs()          # 保证 tests/testbench_data/ 存在, 写入首次 README
  ├── mount /static  (NoCacheStaticFiles)
  ├── mount Jinja2 templates
  ├── register 全局 exception handler   # 三路下发: python_logger + SessionLogger + diagnostics_store
  ├── include_router × 14
  │
  ├── @on_event("startup")
  │     ├── cleanup_old_logs()           # P19 日志保留
  │     ├── autosave.cleanup_old_autosaves()   # P22 自动保存保留
  │     ├── boot_cleanup.run_boot_cleanup()    # P-B: 清 .tmp / 老 .locked_/ 孤儿 SQLite 旁车
  │     └── create_task(_periodic_log_cleanup)  # 12h 循环 cleanup
  │
  └── @on_event("shutdown")
        └── session_store.destroy(purge_sandbox=False)  # 释放 ConfigManager + sandbox
```

> **关键**: 单进程启动 → 注册所有 router → 启动后跑一次 cleanup → 进入
> serve 循环. 没有多进程 / 多 worker; uvicorn 建议以 `--workers 1` 跑
> (默认就是 1).

### §2.3 Session / Sandbox / ConfigManager 三位一体

这是 testbench 最容易被新接手者误解的一块. 三者各自的职责:

1. **`Session` (`session_store.py::Session`)** — 单个测试会话的**内存
   级状态容器**. 包含: `session_id` / `persona` / `messages` / `stage`
   / `virtual_clock` / `last_llm_wire` / 快照 id 列表 / logger 句柄.

2. **`Sandbox` (`sandbox.py::SessionSandbox`)** — 该 Session 的**磁盘级
   隔离区**. 路径 = `DATA_DIR/sandboxes/<session_id>/`. 主程序
   `ConfigManager` 启动时会读该目录下的 `persona.json` / `memory/*.json`
   / `config.yaml` 等文件, 写也写这里. testbench **永远通过替换
   ConfigManager 的 root path 来实现隔离**, 而非改 ConfigManager 内部.

3. **`ConfigManager` (主程序 singleton)** — 主程序对 persona + memory 的
   **进程级单例**. Testbench **一个进程只能有一个 ConfigManager 绑定到
   一个 sandbox**. Session 切换时, 必须先 `destroy()` 释放 ConfigManager
   的资源 (SQLAlchemy engine / SQLite connection / cached objects), 再
   用新 sandbox 启一个新 ConfigManager. **这个 "绑定约束" 是单活跃会话
   的根因**.

> **规则**: `session_store.get_session_store().session_operation()` 异步
> 上下文管理器是获取当前 active session 的**唯一正规路径**. 任何代码不
> 得自己 `SessionStore._active` 拿引用, 否则可能拿到正在被 destroy 的
> 僵尸 session.

### §2.4 Pipeline 层 (`pipeline/`)

业务逻辑按 "动词" 分. 每个模块对应一类操作:

| 模块 | 职责 | 关键类 / 函数 | 备注 |
|---|---|---|---|
| `atomic_io.py` | 原子写 JSON (`tmp + os.replace`) | `atomic_write_json(path, data)` | 所有关键持久化的底层. 崩溃只丢 `.tmp` 不破坏原文件. |
| `messages_writer.py` | **Chokepoint**: 唯一写入 `session.messages` | `append_message(session, role, content, **meta)` | 所有 chat turn / external event / role=system 重写都走这里. |
| `wire_tracker.py` | **Chokepoint**: 唯一 stamp `session.last_llm_wire` | `record_last_llm_wire(session, wire, source, note)` | P25 Day 3 引入. smoke `p25_llm_call_site_stamp_coverage_smoke.py` AST 扫保证所有 LLM 调用点都调. |
| `injection_audit.py` | **Chokepoint**: prompt injection 检测 + 记录 | `scan_and_record(text, source, ...)` | 5 个调用点 collapse 到这里 (P25 polish r5). |
| `prompt_builder.py` | `session.messages` → `PromptBundle` | `build_prompt_bundle(session, ...)` | 唯一 wire 构造入口. 外部事件也走这里 (L36 chokepoint 下沉). |
| `prompt_injection_detect.py` | injection 模式库 + 扫描引擎 | 被 `injection_audit` 调用 | 扩模式在 Diagnostics → Security. |
| `chat_runner.py` | 接收 user 消息 → 跑 LLM → 写 assistant reply | `stream_send(session, user_content, source, ...)` | SSE 流式. 四种 source: `chat.send` / `auto_dialog_target` / `script.playback` / 外部事件. |
| `simulated_user.py` | "假想用户" 自动产生下一句 | `generate_simuser_message(...)` | Chat 四模式里的 "自动对话" 模式. NOSTAMP (不被测对象). |
| `script_runner.py` | 脚本化回放 (.json dialog template) | `run_script(...)` | 四模式之一. |
| `auto_dialog.py` | 双 AI 对打 | `AutoDialogController` | 四模式之一. |
| `memory_runner.py` | Memory op 的 preview + commit 实现 | 4 个 `_preview_*` + 4 个 `_commit_*` + `build_memory_prompt_preview` (pure, 不调 LLM) | 对应 5 个 memory op. |
| `avatar_dedupe.py` | Avatar 8000ms 去重窗口 LRU cache | `_AvatarDedupeCache` | copy from 主程序 `cross_server._should_persist_avatar_interaction_memory` + drift smoke `p25_avatar_dedupe_drift_smoke.py` byte-hash 等价 (L30). |
| `external_events.py` | Avatar / Agent Callback / Proactive 三类 handler | 3 个 `simulate_*` async def + `SimulationResult` dataclass | 统一出口 `_record_and_return` 写 diagnostics. |
| `judge_runner.py` | 四类 Judger 的评分流程 | `JudgeRunner` (pairwise/head-to-head/single-turn/full-session) | SSE 流式进度. |
| `judge_export.py` | Run 结果导出为可比较的 report 格式 | `export_run(run_id)` | |
| `scoring_schema.py` | ScoringSchema 加载 / 校验 / 合并 (内置 + 用户) | `ScoringSchemaStore` | 3 内置 + 用户上传. |
| `diagnostics_store.py` | 进程级 errors / ops ring buffer | `record(...)` / `list_recent(...)` | bounded (默认 500 条). |
| `diagnostics_ops.py` | op id 常量 + 中文化描述 | 每个 op 的 `{id, level, i18n_key}` | Logs 页 / Errors 页都读这里. |
| `live_runtime_log.py` | UI 实时日志转存 (live tail) | `LiveRuntimeLog` | dev mode 用. |
| `stage_coordinator.py` | Stage Coach 六阶段状态机 | `StageCoordinator.suggest() / advance()` | 只 suggest + advance, 不阻塞. |
| `snapshot_store.py` | 对 session 状态快照 (记忆 + 消息 + 时间 cursor) | `SnapshotStore.create() / rewind() / rerun() / edit_message()` | SQLAlchemy, 最多 30 份. |
| `autosave.py` | 滚动 3 份自动保存 | `AutosaveScheduler` | 5s debounce + 60s force. |
| `persistence.py` | 人工保存 / 加载 (单个 .json + memory.tar.gz) | `save_session_to_file(...)` / `load_session_from_file(...)` | 也做 import roundtrip. |
| `session_export.py` | 导出报告 (4 scope × 3 format = 11 组合) | `export_session(scope, format, ...)` | api_key 自动脱敏. |
| `reset_runner.py` | Diagnostics → Reset 页的硬重置 | `reset_runtime_state(...)` | 清沙盒 + 清持久化 + 清日志. |
| `boot_cleanup.py` | 启动时清临时文件 (`.tmp`, `.locked_*`, 孤儿 SQLite 旁车) | `run_boot_cleanup()` | P-B 延期加固. |
| `boot_self_check.py` | 启动时扫孤儿沙盒 | `scan_orphan_sandboxes()` | P-A 延期加固. 只扫不删. |
| `redact.py` | 脱敏工具 (api_key / 长文) | `redact_dict(...)` | export 路径 + diagnostics 展示都用. |
| `request_helpers.py` | FastAPI 请求处理共享 | 当前 session id 等 | |
| `sse_events.py` | SSE 帧格式 helper | `sse_error_frame(...)` | 顶层必须先 yield 一条 error 帧再 raise. |

**设计原则**: pipeline/ 下所有模块**尽可能 session-agnostic 纯函数**.
接收 session 作显式参数 (而非从全局拿), 方便 smoke 套件 mock. 这是 P21
`session_export` / P22 `autosave` / P23 导出 session-agnostic 化的关键
教训.

### §2.5 Router 层 (`routers/`)

HTTP 端点按**业务域**分, 13 个 router:

| Router | 路径前缀 | 职责 |
|---|---|---|
| `health_router` | `/health`, `/version`, `/docs/{name}` (P26 新增) | 健康检查 + 版本元数据 + 公共 markdown 文档渲染 |
| `session_router` | `/api/session/*` | New / Switch / Load / Save / Export / List |
| `config_router` | `/api/config/*` | 模型配置 / provider / api_key |
| `persona_router` | `/api/persona/*` | Persona CRUD + 导入 (真实角色 / 内置 preset) |
| `memory_router` | `/api/memory/*` | 三层 memory 的 CRUD + 5 op preview/commit + `/api/memory/recent/import_from_session` (P25 polish r6) + `/api/memory/prompt_preview/{op}` (P25 polish r7 pure preview) |
| `chat_router` | `/api/chat/*` | 四模式 chat (send / auto_dialog / script playback / dual_ai) + SSE |
| `judge_router` | `/api/judge/*` | Evaluation Run + SSE + 结果查询 + Aggregate + `/api/judge/run_prompt_preview` (P25 polish r7 pure preview) |
| `stage_router` | `/api/stage/*` | Stage Coach suggest / advance |
| `snapshot_router` | `/api/snapshot/*` | 快照 create / list / edit / rewind / rerun |
| `diagnostics_router` | `/api/diagnostics/*` | Errors / Logs / Paths / Snapshots list / Reset 子页后端 |
| `security_router` | `/api/security/*` | Injection 审计视图 + 模式库编辑 |
| `external_event_router` | `/api/session/external-event` | P25 统一外部事件入口 |
| `time_router` | `/api/time/*` | 虚拟时钟 cursor 操作 |

**原则**:
- 每个 router 只做 "HTTP 层适配" — 解析 request, 调 session_store 拿
  session, 调 pipeline/ 做业务, 组装 response.
- **不在 router 里写业务逻辑**. 业务逻辑一律在 pipeline/.
- SSE 端点 (`chat_router`, `judge_router`) 顶层 `try/except` 必须先
  yield 一条 error 帧再 raise (§5.4).

### §2.6 持久化 / 自动保存 / 快照

三者层次不同, 容易混:

| 概念 | 频率 | 保留 | 文件 | 触发 |
|---|---|---|---|---|
| **人工保存** | 按需 (测试员点 `[Save]`) | 不自动删 | `DATA_DIR/saved_sessions/<name>.json` + `<name>.memory.tar.gz` | 点按钮 / `POST /api/session/save` |
| **自动保存** | 每次变更 debounce 5s / force 60s | 滚动 3 份, 默认 24h 保留 | `DATA_DIR/saved_sessions/_autosave/<session_id>_<slot>.json(+memory.tar.gz)` | `autosave.AutosaveScheduler` 后台线程 |
| **快照** | 每次 chat commit 后 / 按需 | 内存中最多 30 份 (`SNAPSHOT_MAX_IN_MEMORY`), 不落盘 (live-only) | 内存 + SQLAlchemy (snapshot_store) | `snapshot_store.create()` |

**灾难恢复路径**:
1. 程序崩溃 → 重启 → boot 时 `autosave.check_orphans()` 发现上次 session
   还有未落盘的 autosave → 前端顶部弹 Restore banner → 用户选择 Restore
   or Discard.
2. 编辑记录错了 → 从 Snapshots 列表里选历史快照 → Rewind (抛弃之后所有
   turn) / Re-run (从该点重跑后续 turn) / Edit message (修改某条消息).

---

## 第三部分: 前端模块拓扑

### §3.1 目录速查

```
tests/testbench/static/
├── app.js                  # 单页 boot + workspace 切换
├── testbench.css           # 全部样式 (单文件 ~173KB, 按 workspace 分块)
├── core/
│   ├── api.js              # fetch 封装 + AbortController (last-click-wins)
│   ├── state.js            # 全局事件总线 (session:change / messages_changed / ...)
│   ├── errors_bus.js       # 错误 toast + Errors 子页数据源
│   ├── sse_events.js       # SSE 帧解析 (chat / judge)
│   ├── i18n.js             # 国际化字典 (zh-CN 主 / en/ja 回退) ~165KB
│   ├── collapsible.js      # <details>/<summary> 默认折叠策略
│   ├── time_utils.js       # 虚拟时钟 "XXX ago" 渲染
│   ├── toast.js            # toast + warning 轻量提示
│   └── render_drift_detector.js  # dev-mode renderXxx() 后置断言
│
└── ui/
    ├── topbar*.js          # 顶栏: session chip / time chip / stage chip
    ├── workspace_*.js      # 各 workspace 入口 (setup / chat / eval / diag / settings)
    ├── session_*.js        # Save/Load/Export/Restore 共享 modal
    ├── model_config_reminder.js  # 模型配置提示条
    ├── _*.js               # 共享 UI helper (_dom.js / _open_folder_btn.js / _prompt_preview_modal.js)
    │
    ├── setup/              # Setup workspace 的各子页 (persona / scripts / virtual_clock / memory_*)
    ├── chat/               # Chat workspace (composer / message_stream / preview_panel / external_events_panel / sse_client)
    ├── evaluation/         # Evaluation workspace (schemas / run / results / aggregate)
    ├── diagnostics/        # Diagnostics workspace (errors / logs / paths / snapshots / reset)
    └── settings/           # Settings workspace (api_keys / models / providers / autosave / ui / about)
```

### §3.2 boot 流程 (`app.js`)

```
app.js::boot()                          # DOMContentLoaded 后执行
  │
  ├── state.js::init()                  # 取 localStorage 最后一次活跃 workspace
  ├── i18n.js::init(lang)               # zh-CN/en/ja 字典选择
  ├── errors_bus.js::init()             # 订阅 SSE error 帧 + HTTP 4xx/5xx
  ├── sse_events.js::init()             # 订阅 diagnostics: ops
  │
  ├── renderTopbar()                    # 顶栏三个 chip
  ├── renderDriftDetector.init()        # dev-mode only
  │
  └── workspace_<active>.mount()        # 按当前 active 选择入口
         ├── workspace_setup.mount()
         ├── workspace_chat.mount()
         ├── workspace_evaluation.mount()
         ├── workspace_diagnostics.mount()
         └── workspace_settings.mount()
```

每个 `workspace_*.mount()` 大致格式:

```javascript
export function mount() {
  const host = document.querySelector('#workspace-xxx-host');
  function renderAll() {
    host.replaceChildren(...renderXxxSection(store.get()));
  }
  const offSession = state.onSession(renderAll);
  const offMessages = state.onMessagesChanged(renderAll);
  renderAll();  // first paint
  return { destroy() { offSession(); offMessages(); } };
}
```

**关键**: `destroy()` 必须 off 所有 on, 否则 workspace 切换后订阅泄漏
(`LESSONS §7.11`).

### §3.3 事件总线

**三路总线**:

1. **`state.js`** — 主业务事件. 最重要的两个:
   - `session:change` — session 切换 / new / load / reset 完毕.
   - `messages_changed` — `session.messages` 被追加 / 修改 / 清空.
     **所有**修改必须 emit 这个事件, 任何 UI 片段的增量都通过它驱动.

2. **`errors_bus.js`** — 错误事件. 订阅者:
   - 全局 toast (红色浮条).
   - Diagnostics → Errors 子页列表.
   - 过滤规则: 只拉 `level in ('error', 'warning')`, `info` 不进 toast.

3. **`sse_events.js`** — SSE 流事件. 订阅者:
   - Chat SSE (`/api/chat/send` 流式进度).
   - Judge SSE (`/api/judge/run` 流式进度).
   - Diagnostics ops `diagnostics:op_pushed` (ring buffer 新增).

**emit × listener matrix audit**: 任何新 emit 都必须有至少一个 listener,
任何 listener 都必须有对应的 emit 入口, 否则 `event-bus-emit-on-matrix-audit`
skill 会 FAIL. 详见 `LESSONS §2.5`.

### §3.4 渲染哲学

前端**不做** partial DOM patch. 所有状态变更都走 **"事件触发 → 调
renderXxx() → 用新 state 替换整个 section DOM"**. 具体做法:

- 每个 `renderXxx(state)` 是**纯函数**, 接收 state snapshot, 返回 DOM
  Node 列表.
- 挂载时 `host.replaceChildren(...renderXxx(state))` 一次性替换.
- 不在 render 内做副作用 (不 fetch, 不 setState), 只读 state + 生成 DOM.

**代价**: message 列表 > 200 条时性能差. 测试场景下用户不太会单会话跑
超过 100 轮, 所以可接受.

**好处**: 永远不会 "state 和 DOM 漂移". 想 debug "为什么 UI 还是老数据"
只需打印 state, 不用看 DOM.

### §3.5 renderDriftDetector

Dev-mode only (`?dev=1` 或 `window.__DEBUG_RENDER_DRIFT__=true`) 的**断言
框架**. API:

```javascript
import { registerChecker, initRenderDriftDetector } from '/static/core/render_drift_detector.js';

registerChecker({
  name: 'page_snapshots.row_count',
  event: 'messages_changed',
  check(state) {
    const domCount = document.querySelectorAll('.snapshots-row').length;
    if (domCount !== state.items.length) {
      return { ok: false, detail: `DOM=${domCount} state=${state.items.length}` };
    }
    return { ok: true };
  },
});
initRenderDriftDetector();
```

在 microtask 调度 check, per-(event, name) dedupe. `window.__renderDrift`
三件套: `listCheckers()` / `runNow()` / `getLog()`.

已注册的 checker (骨架 + 3 个), 由 P24 Day 12 欠账清返引入
(`AGENT_NOTES §4.27 #118`).

---

## 第四部分: 关键子系统

### §4.1 Chat 对话四模式

Chat composer 顶部的 **Mode 切换器** 让测试员切入下面四类驱动:

| 模式 | 入口 | pipeline 模块 |
|---|---|---|
| **Manual (手动单发)** | composer Mode = manual, 点发送按钮 | `chat_runner.stream_send(source="chat.send")` |
| **SimUser (假想用户自动续写)** | composer Mode = simuser | `simulated_user.generate_simuser_message` (NOSTAMP) → `chat_runner.stream_send(source="chat.send")` |
| **Script (脚本化回放)** | composer Mode = script, 选 dialog template (Setup → Scripts 里维护) | `script_runner.run_script` → `chat_runner.stream_send(source="chat.send")` |
| **Auto (双 AI 自动对话)** | composer Mode = auto | `auto_dialog.AutoDialogController` → `chat_runner.stream_send(source="auto_dialog_target")` 交替 |

**一条 chat turn 的完整路径**:

```
[前端 composer.js]
  ↓ fetch /api/chat/send (SSE stream)
[后端 chat_router.py::POST /send]
  ↓
[chat_runner.stream_send]
  ├── 1. 检查 session ready (raise InvalidSendState 若未 ready)
  ├── 2. 跑 injection_audit.scan_and_record(user_content)
  ├── 3. prompt_builder.build_prompt_bundle() → wire
  ├── 4. wire_tracker.record_last_llm_wire(session, wire, source)   ← stamp
  ├── 5. messages_writer.append_message(role=user, content=user_content)
  │       → emit messages_changed
  ├── 6. llm.astream(wire) → chunks
  │       SSE yield 每个 chunk
  ├── 7. messages_writer.append_message(role=assistant, content=final_reply)
  │       → emit messages_changed
  ├── 8. diagnostics_store.record(source="chat.send", ...)
  └── 9. snapshot_store.create(session)  [条件性: Stage advance 后]
```

### §4.2 Memory 三层 + 5 Op + Preview/Commit

**三层**: recent (`memory/recent.json`) + facts (`memory/facts.json`) +
reflections (`memory/reflections.json`) + persona (`persona.json`).

**5 op 对照**:

| Op id | 作用 | Preview 输出 |
|---|---|---|
| `recent.compress` | 取 recent 最后 N 条 → LLM 压成摘要 → 插入 facts | 压缩摘要 + 将影响的 recent 条数 |
| `facts.extract` | 从 recent 全部 → LLM 抽事实 → 追加 facts | 抽取到的事实列表 (草稿) |
| `reflect` | 从 facts 全部 → LLM 反思 → 插入 reflections | 反思文本 |
| `persona.resolve_corrections` | 消化 facts.extract 产生的 "待确认 persona 修正" | persona diff |
| `persona.add_fact` | 把当前对话某条信息手动加入 persona | persona diff (无 prompt, 无 preview) |

**Preview / Commit 两阶段**:

```
POST /api/memory/{op}/preview   # 跑 LLM, 返草稿, 不写盘
      │
      ▼ (tester 审核草稿)
      │
POST /api/memory/{op}/commit    # 用 preview 返的草稿写盘, 不跑 LLM
```

**Pure preview 端点** (P25 polish r7 新增):
```
POST /api/memory/prompt_preview/{op}   # 只返 "将要发给 LLM 的 wire", 不跑 LLM 不写盘
```
这条给 tester "我想看 prompt 但不付 2-10s LLM 费用" 的需求用.

### §4.3 Stage Coach (六阶段引导)

`pipeline/stage_coordinator.py`. 状态 (`Stage` Literal):

```
persona_setup → memory_build → prompt_assembly → chat_turn ⇄ post_turn_memory_update → evaluation
                                                   ↑___________________________|
                                                   (chat_turn 和 post_turn_memory_update 循环)
```

每状态有 `suggest()` 给 UI 提示下一步 + `advance()` 推进条件校验.
**只 suggest + advance**, 不阻塞. 测试员可以跳阶段 (比如跳过 memory 直
接 chat), 顶栏 chip 显示当前阶段.

### §4.4 Evaluation

Evaluation 链路:

```
[Schemas 子页]  ←── 定义 / 上传 / 编辑 ScoringSchema (JSON)
       ↓ 引用
[Run 子页]       ←── 选 schema + target (messages subset / 对比对) → 跑 Judger
       ↓ 产出 Run id
[Results 子页]   ←── 查看某次 Run 的逐项打分
[Aggregate 子页] ←── 多 Run 聚合 (同 schema 下 对 messages 子集 N 次独立评分)
       ↓
[Export]        ←── 输出 Markdown / JSON / CSV (走 session_export / judge_export)
```

**四类 Judger** (`pipeline/judge_runner.py::JudgeKind`):

| Kind | 用途 | Wire 构造特点 |
|---|---|---|
| `pairwise` | 两个候选 (如同一提问两个回复) 两两比较 | A/B prompt + 评分维度 |
| `head_to_head` | N 轮 A vs B 对决 | 多轮 A/B, 统计胜率 |
| `single_turn` | 单条回复打分 | 单条 + schema 维度 |
| `full_session` | 全 session 打分 | 整个 messages 做 input |

### §4.5 External Event Injection (P25)

三类外部事件, 统一入口:

```
POST /api/session/external-event
{
  "kind": "avatar" | "agent_callback" | "proactive",   # kind 字符串值, 不带后缀
  "payload": {...},          # kind-specific (见 external_events_guide.md)
  "mirror_to_recent": bool   # 可选, default false
}
```

后端分发 (`routers/external_event_router.py::POST`):

```
  kind = request.kind
  if kind == "avatar":
      result = await external_events.simulate_avatar_interaction(session, payload, mirror_to_recent)
  elif kind == "agent_callback":
      result = await external_events.simulate_agent_callback(...)
  elif kind == "proactive":
      result = await external_events.simulate_proactive(...)
  return result.to_dict()

> ℹ️ 注意 ``wire_tracker`` 给这些事件打的 source 字符串是 ``avatar_event`` /
> ``agent_callback`` / ``proactive_chat`` — 即 wire 层 slug 和 API kind 不
> 同名 (历史原因). 做 Preview Panel 过滤时用 wire slug.
```

每个 handler 内部:

```
  1. payload validation (allowlist, 组合约束)
  2. dedupe check (仅 avatar, 8000ms LRU cache)
  3. _InstructionBundle: 共享 "真实 run" + "dry-run preview" 的构造 helper
  4. prompt_builder.build_prompt_bundle(session, instruction=...)
  5. wire_tracker.record_last_llm_wire(..., source=<kind_slug>)
  6. llm.ainvoke(wire) → assistant reply
  7. messages_writer.append_message × 2 (user-role memory_note + assistant reply)
     * mirror_to_recent 开关下: 也 mirror 到 memory/recent.json (LangChain canonical shape)
  8. _record_and_return(SimulationResult)   ← 统一 diagnostics 出口
```

**`SimulationResult.reason` 是闭集** (8 个值: `null`, `invalid_payload`,
`dedupe_window_hit`, `empty_callbacks`, `pass_signaled`, `llm_failed`,
`persona_not_ready`, `chat_not_configured`). 见 `external_events_guide.md`.

### §4.6 Diagnostics 五子页

| 子页 | 数据源 | 用途 |
|---|---|---|
| **Errors** | `diagnostics_store` (filter level=error/warning) | 错误排查; 内置 "Security 视图" filter (切到 prompt_injection / security 相关 op) |
| **Logs** | `logger.py` JSONL (per session per date) | session + date 选择, level / op / keyword 过滤, 5s 轮询 auto-refresh, 导出 JSONL. **无 follow/tail 自动滚动**. |
| **Paths** | `/system/paths` + `/system/health` + `/system/orphans` | 数据/代码路径一览 (按 session/shared/code 三组) + 系统健康卡片 (5 项指标) + 孤儿沙盒 triage (批量清 0B + 逐条删) + [复制路径] / [在文件管理器中打开] (仅限 DATA_DIR 子路径) + [导出沙盒快照] |
| **Snapshots** | `snapshot_store` | list / edit / rewind / rerun |
| **Reset** | `reset_runner` | 硬重置 (清沙盒 + 清持久化 + 清日志) |

> Injection 审计的**模式库编辑**不在 Diagnostics, 而是独立 `security_router`
> 下的后端端点 (当前版本无独立子页, 由 agent / 二开通过 HTTP 直调).

### §4.7 Virtual Clock

`virtual_clock.py::VirtualClock` + `prompt_builder.py::time_manager`.

**设计**: 一个可手动拖拽的 **Unix timestamp cursor**. 不影响真实墙钟,
不影响 `time.time()` / `datetime.now()`. 只影响:

- `time_manager.format_ago(ts)` — 所有 "X 分钟前 / 昨天" 渲染.
- prompt 里的 "Current time" 字段 (给 LLM 的时间参照).
- memory 里时间戳的相对显示.

三层防线 (P24 Day 6 补齐):
1. 默认墙钟 (cursor 未设置时).
2. Cursor 设置后**冻结**, 所有相对时间以 cursor 为锚.
3. **时钟回流保护**: 新 commit 的消息时间不得早于 session 最后一条消息
   时间 (`chat_messages.py::assert_monotonic_or_fail`), 否则 raise.

---

## 第五部分: 横切关注点

### §5.1 原子写入

`pipeline/atomic_io.py::atomic_write_json(path, data)`:

```python
tmp = path.with_suffix(path.suffix + '.tmp')
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.replace(tmp, path)   # POSIX rename 原子性
```

**覆盖面**: `memory_router` / `memory_runner` / `script_runner` /
`scoring_schema` / `session_export` / `autosave` / `persona_router` 所
有关键写入都走. 崩溃只丢 `.tmp`, 永远不破坏原文件.

**cursor rule**: `.cursor/rules/atomic-io-only.mdc` (agent 不得直接
`open(path, 'w')` 写 JSON).

### §5.2 LLM wire stamp chokepoint

`pipeline/wire_tracker.py::record_last_llm_wire(session, wire, source, note)`:

- 唯一 stamp `session.last_llm_wire` 入口.
- `source` ∈ `KNOWN_SOURCES` = `{chat.send, auto_dialog_target, avatar_event, agent_callback, proactive_chat, memory.llm, judge.llm}` (**7 项**, P25 r7 移除了 simuser — 参见 `LESSONS_LEARNED §L44`). unknown 抛 `ValueError`.
- smoke `p25_llm_call_site_stamp_coverage_smoke.py` AST 扫所有
  `<xxx>.ainvoke/astream/invoke(...)` 调用, 确保同 body 内有
  `record_last_llm_wire`, 否则 FAIL; 合法不 stamp 的用代码内 `# NOSTAMP(wire_tracker): <理由>` sentinel (10 行 lookback).

**Chat 页 Preview Panel 白名单** (`preview_panel.js::CHAT_VISIBLE_SOURCES`):
只渲 `chat.send / auto_dialog_target / avatar_event / agent_callback /
proactive_chat` 五个; `memory.llm` / `judge.llm` 来的 stamp 存在但**不
渲染**, 回退预估 wire + hint 引导去对应页面点 `[预览 prompt]`.

### §5.3 Injection audit chokepoint

`pipeline/injection_audit.py::scan_and_record(text, source, ...)`:
- 唯一 injection 扫描 + 记录入口 (P25 polish r5 single-writer
  chokepoint 重构, 5 个调用点 collapse 到这里).
- 匹配模式在 `prompt_injection_detect.py::BUILTIN_PATTERNS` + 用户扩展
  `DATA_DIR/security_patterns.json`.
- 命中写 `diagnostics_store` level=warning + 可选抛异常 (默认不抛, 仅
  审计).

### §5.4 SSE 协议

Chat / Judge 的流式端点走 SSE. 约束 (`pipeline/sse_events.py`):

1. **顶层 `try / except` 必须先 yield 一条 error 帧再 raise** — 否则
   前端 EventSource 看到的是连接中断, 拿不到 error_type 和 message.
2. 每个帧格式: `event: <name>\ndata: <json>\n\n`.
3. 前端 `sse_events.js::parseFrames()` 按 event 名分发.

### §5.5 i18n

`static/core/i18n.js` 一个大字典. **当前只定义 `zh-CN`**; `setLocale()`
仅接受 `zh-CN`, Settings → UI 的语言切换下拉也 disabled, 只有 "简体中文" 一项.
缺 key 时调用点显式展示 `<missing:key>` 方便审计. en / ja 字典尚未落地
(P04 预留位).

**命名约定** (见 `.cursor/rules/i18n-fmt-naming.mdc`):
- `<workspace>.<section>.<key>` 层级.
- 格式参数名用 `{nameInCamelCase}` (JS 端) / `{name_in_snake}` (Py 端)
  — 即 placeholder 命名随消费方风格.

**硬规**: 不得直接在 JS 写中文字符串 (violates
`.cursor/rules/no-hardcoded-chinese-in-ui.mdc`), 必须走 i18n.

---

## 第六部分: 开发者上手 cheatsheet

### §6.1 "我想加一个 X" 该从哪里下手

| 需求 | 切入点 | 必读 |
|---|---|---|
| 加一个新 memory op | `pipeline/memory_runner.py` + `routers/memory_router.py` + `static/ui/setup/memory_trigger_panel.js` | `AGENT_NOTES §4.7` P17 交付案 |
| 加一个新 Judger kind | `pipeline/judge_runner.py::JudgeKind` + schema + `static/ui/evaluation/page_run.js` | `P24_BLUEPRINT §4` |
| 加一个新外部事件类型 | `pipeline/external_events.py` + `routers/external_event_router.py` + `static/ui/chat/external_events_panel.js` + tester 手册 | `P25_BLUEPRINT` |
| 加一个新 Diagnostics op id | `pipeline/diagnostics_ops.py` + i18n 中英双语 | `.cursor/rules/emit-grep-listener.mdc` |
| 加一个设置项 | `config.py` 常量 + `static/ui/settings/page_*.js` + i18n + (如需) `/api/config/*` 端点 | — |
| 加一个 Chat 模式 | 新建 `pipeline/<mode>_runner.py` + `chat_router` 新端点 + `static/ui/chat/` 新组件 + 与 `composer.js` 对接 | `pipeline/script_runner.py` / `auto_dialog.py` 参考 |
| 加一个新 persistent 字段到 Session | `session_store.py::Session` + `persistence.py::save_session_to_file / load_session_from_file` + `smoke/p24_session_fields_audit_smoke.py` 补断言 | `LESSONS §7.25` shape drift 必 rg 消费方 |

### §6.2 常见陷阱速查

> 每条都有详细论述在 `LESSONS_LEARNED.md`, 这里只做索引.

| 症状 | 根因 | 查 |
|---|---|---|
| 新加写入路径但 UI 不刷新 | 忘 emit `messages_changed` 或对应事件 | `LESSONS §7.25 L39` |
| `session.messages` 混入脏数据 | 绕过 `messages_writer.append_message` | `LESSONS §7.6` |
| LLM Preview Panel 显示的不是刚跑的 prompt | 新 LLM 调用点没走 `wire_tracker` | `LESSONS L43` + `p25_llm_call_site_stamp_coverage_smoke.py` |
| 同族原则漏守 (一处 chokepoint N 处绕过) | 纸面原则没机械保证 | `LESSONS §7.6` + `audit-chokepoint-invariant` skill |
| `Node.append(null)` 静默变 "null" 字符串节点 | DOM 原生行为 | `.cursor/rules/dom-append-null-gotcha` / skill |
| 加新 grid child 导致布局错位 | grid-template-rows 写死 | `.cursor/rules/css-grid-template-child-sync` / skill |
| UTF-8 CJK 文件被 PowerShell 搞坏 | `Set-Content` 默认 CP936 | `LESSONS L32` |
| Subagent 派完忘了收结果 | 没 DONE 标志 | `LESSONS L33.x` |
| 跨阶段 "推迟至 PX" 漏回填 | 没双向回扫 | `LESSONS L28` |
| Shape drift (后端加字段前端漏改) | 没 rg 消费方 | `LESSONS §7.25 L36` + `ui-wire-field-rg-backend-first` skill |
| `role=system` 消息 LLM 返空回复 | 主程序 prompt 契约不接受 runtime system | `chat_router` 的 rewrite chokepoint |
| Dropdown/select 只在某些导航路径下为空 | lazy init 的 boolean flag race | `async-lazy-init-promise-cache` skill |
| generator finally 清了 yield 依赖的 state | finally 早于最后 yield | `python-generator-finally-snapshot` skill |
| 流式系统 Preview 显示陈旧数据 | chokepoint 覆盖率漏 + 展示域没分区 | `LESSONS L43 + L44` |

### §6.3 smoke 套件导航

18 份 Python smoke + 1 份 Node.js UI smoke, 按阶段归属:

```
p21_1_reliability_smoke.py            # 生命周期 / lock / atomic write
p21_persistence_smoke.py              # 保存加载 roundtrip
p21_3_prompt_injection_smoke.py       # injection 模式库
p21_ui_smoke.mjs                      # (Node.js) 前端 UI smoke — 单独跑
p22_hardening_smoke.py                # P22 加固 (sha256 / boot cleanup / judger extra_context)
p23_exports_smoke.py                  # 导出 11 组合 + 脱敏 + 导入往返
p24_integration_smoke.py              # P24 端到端联调
p24_lint_drift_smoke.py               # 代码静态 lint 漂移
p24_sandbox_attrs_sync_smoke.py       # sandbox 属性同步 (主程序 ConfigManager 对齐)
p24_session_fields_audit_smoke.py     # Session 字段持久化白名单
p25_avatar_dedupe_drift_smoke.py      # avatar_dedupe vs 主程序 byte-hash 等价
p25_external_events_smoke.py          # 3 handler × 9 组契约
p25_llm_call_site_stamp_coverage_smoke.py  # AST 扫 wire_tracker 覆盖率
p25_prompt_preview_truth_smoke.py     # preview wire = real wire 契约
p25_r5_polish_smoke.py                # polish r5 7 契约
p25_r6_import_recent_smoke.py         # recent.json import shape
p25_r7_wire_partition_smoke.py        # Preview Panel 域分区
p25_wire_role_chokepoint_smoke.py     # role=system rewrite chokepoint
p26_docs_endpoint_smoke.py            # /docs/{name} 公开白名单 + heading id 锚点 + .md 后缀改写
```

**跑全量**: `uv run python tests/testbench/smoke/_run_all.py` (cross-platform).
当前基线: 18/18 Python smoke 全绿 (`p21_ui_smoke.mjs` 需 Node.js 单独跑).

---

## 修改本文档的守则

- 本文**只描述稳定已冻结的架构**. 加新阶段时**不在这里记实施细节** —
  那些去 `PROGRESS.md` + `AGENT_NOTES.md §4.27`.
- 若本文描述的模块拓扑 / 事件总线 / 边界契约发生**不向后兼容**变更
  (新增字段 / 新 chokepoint 不算), 必须同步更新本文 + bump
  `TESTBENCH_VERSION` MAJOR.
- 对**新接手 agent 和二次开发者**的可读性优先于完整性: 发现某小节超
  过 150 行就拆. 需要深入的 "为什么" 用链接 (引 `LESSONS` / `AGENT_NOTES`)
  而不是原地铺开.

---

*本文档是 N.E.K.O. Testbench 项目架构概览, 与 `docs/` 下其它文档的关系:*

| 文档 | 时效 | 受众 | 侧重 |
|---|---|---|---|
| **本文 `ARCHITECTURE_OVERVIEW.md`** | 长期 | 二次开发者 / 接手 agent | 架构 / 模块拓扑 / 上手 |
| `testbench_USER_MANUAL.md` | 版本 | 测试员 | 操作流程 |
| `external_events_guide.md` | P25+ | 测试员 | 外部事件具体用法 |
| `CHANGELOG.md` | 版本 | 用户 | 版本更新 |
| `P24_BLUEPRINT.md` / `P25_BLUEPRINT.md` | 历史档 | 项目开发 | 阶段蓝图 |
| `PROGRESS.md` | 历史档 | 项目开发 | 阶段交付日志 |
| `AGENT_NOTES.md` | 历史档 | AI agent | 踩坑复盘 |
| `LESSONS_LEARNED.md` | 跨项目 | 任何 AI 辅助开发项目 | 经验沉淀 |
| `PLAN.md` | 活动 | 项目开发 | 未决 / 待办 |
