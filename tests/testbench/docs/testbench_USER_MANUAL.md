# Testbench 测试员使用手册

> **受众**: 测试员 / 中级用户 (不需要读 Python 源码). 本手册覆盖**怎么用**, 不讲**为什么这样设计**. 想了解架构 / 设计权衡, 见 [代码与设计总体概述](testbench_ARCHITECTURE_OVERVIEW.md).
>
> **版本**: 版本号见 Settings → About (来自 `/version` 端点 + `config.py::TESTBENCH_VERSION`). 修订日期见同一页面的 **最后更新日期** 字段.

本手册按你**实际打开 testbench 后看到的界面**自上而下组织:

1. [准备事项](#1-准备事项-启动-配置-首次打开) — 启动命令 / 端口 / 数据目录 / api_keys.json
2. [Workspace 导航](#2-workspace-导航-顶栏-stage-chip-timeline-chip) — 5 个 workspace 切换 + 顶栏两个 chip
3. [Chat 对话区](#3-chat-对话区-四种模式-外部事件模拟) — Manual / SimUser / Script / Auto 四模式 + 外部事件 (avatar / agent_callback / proactive)
4. [Memory 记忆编辑](#4-memory-记忆编辑-setup-workspace-memory-子组) — recent / facts / reflections / persona + 5 ops + 预览
5. [Evaluation 评分](#5-evaluation-评分-schemas-run-results-aggregate) — 四子页完整工作流
6. [Session 管理](#6-session-管理-保存-加载-自动保存-快照-rewind) — 顶栏入口 + Autosave 策略
7. [Diagnostics 诊断](#7-diagnostics-诊断-errors-logs-snapshots-paths-reset) — 5 子页 + 错误排查
8. [Settings 设置](#8-settings-设置-models-api_keys-providers-autosave-ui-about) — 6 子页
9. [FAQ 与已知限制](#9-faq-与已知限制)
10. [进阶操作](#10-进阶操作-给深度用户)

---

## 1. 准备事项 (启动 / 配置 / 首次打开)

### 1.1 启动命令

所有命令都应通过 `uv` 运行 (项目 Python 环境统一走 `uv`, 避免系统 Python 版本不一致导致依赖解析错). 在项目根目录执行:

```bash
uv run python tests/testbench/run_testbench.py --port 48920
```

CLI 参数:

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--port` | `48920` | 监听端口. 被占用时改别的, e.g. `--port 48921`. |
| `--host` | `127.0.0.1` | 绑定地址. **默认只听 loopback**, 不暴露公网. 需要局域网访问才传 `--host 0.0.0.0`. |

启动成功后控制台会打印监听地址 (e.g. `http://127.0.0.1:48920`). 浏览器访问该 URL 即可.

### 1.2 数据目录

所有运行期数据都在仓库内 **`tests/testbench_data/`** (不跟主程序 `~/.neko/` 互通). 首次启动会自动创建目录树:

| 子目录 | 作用 |
|---|---|
| `sandboxes/` | 每个会话一个沙箱目录, 内含 `memory/` (recent / facts / reflections / persona) |
| `saved_sessions/` | 手动保存过的会话 JSON |
| `saved_sessions/_autosave/` | 自动保存滚动文件 (默认保留 3 份) |
| `logs/` | 诊断日志 JSONL, 按日期 + session_id 拆分, 默认保留 14 天 |
| `scoring_schemas/` | 用户自建评分 schema |
| `dialog_templates/` | 用户自建脚本模板 |
| `exports/` | Session 导出包 (zip) 落地位置 |

API key 存在**另一处**: `tests/api_keys.json` (整仓共享, 主程序单元测试和 testbench 都会读; **明文**, 请自行保护).

### 1.3 首次打开该做什么

1. **填 API Keys**: Settings → API Keys, 按 provider (openai / anthropic / deepseek / ...) 填 key 保存. 保存后立刻生效, 无需重启.
2. **配 Providers**: 如果你用的是 Azure / Ollama / 其它自建网关, 去 Settings → Providers 补 `base_url`.
3. **配 Models 四组**: Settings → Models 为 `chat / memory / judge / simuser` 四组分别选 provider + model + 参数, 点 **[测试连接]** 验证通. 只用哪组功能就配哪组, 不强制全填.
4. **创建会话**: 顶栏左上角 **会话下拉** → **[新建会话]**, 输入名字 (e.g. `smoke_01`). 未建会话前大多数 `/api/session/*` 端点返 404 (特例: `/api/session/state` 永远返 200, 用 `has_session:false` 指示).
5. **填 Persona** (角色卡): Setup → Persona, 至少填 `character_name` (必填). 不填触发 `reason=persona_not_ready`, Chat 不能发送.

完成后就能切 Chat workspace 发消息了.

![testbench 首次启动空界面 + 顶栏](images/01_first_launch.png)

### 1.4 可公开访问的 md 文档

下列 md 通过 `/docs/{name}` 对外暴露, 其它 md 均不可通过该端点下载:

- `testbench_USER_MANUAL` (本手册)
- `testbench_ARCHITECTURE_OVERVIEW` (代码与设计总体概述)
- `external_events_guide` (外部事件测试指南)
- `CHANGELOG` (版本更新记录)

浏览器直接访问 `http://<host>:<port>/docs/testbench_USER_MANUAL` 即可看到渲染后的 HTML. 端点行为见 §9.Q1.

---

## 2. Workspace 导航 + 顶栏 Stage chip / Timeline chip

### 2.1 5 个 Workspace

![顶栏 5 个 workspace 切换按钮 + chip](images/02_workspace_topbar.png)

| Workspace | 主要用途 |
|---|---|
| **Setup** | 8 个子页: Persona / Import (Persona 导入) / Virtual Clock / Scripts + Memory 组下 Recent / Facts / Reflections / Persona |
| **Chat** | 和 AI 对话, 触发外部事件, 右侧 Prompt Preview |
| **Evaluation** | Schemas / Run / Results / Aggregate 四子页 |
| **Diagnostics** | Errors / Logs / Snapshots / Paths / Reset 五子页 |
| **Settings** | Models / API Keys / Providers / Autosave / UI / About 六子页 |

切 workspace 时**顶栏 Stage chip 的折叠状态**会自动变:

- **setup / chat / evaluation**: chip 展开为完整行动栏 `[去 Chat 发送消息] [预览] [执行并推进] [跳过] [回退] [⋯ 展开面板]`
- **diagnostics / settings**: chip 折叠为小徽章 `Stage: <当前阶段> ▾`

这是刻意设计: 诊断和设置是"元操作"环境, 不应被 action-oriented 的 stage 控件干扰.

**顶栏右上角 `⋯` 菜单** (5 条快捷项, 与 workspace 导航互补):

| 菜单项 | 作用 |
|---|---|
| **导出…** | 打开统一的 Session 导出对话框 (选 scope + 格式) |
| **重置…** | 跳转到 Diagnostics → Reset 子页 |
| **关于** | 跳转到 Settings → About 子页 |
| **打开诊断** | 等价于点 workspace 切换到 Diagnostics |
| **打开设置** | 等价于点 workspace 切换到 Settings |

### 2.2 顶栏 Stage chip (阶段指示 + 快速跳转)

Stage 表示当前流水线走到哪一步. 6 个阶段 (按主干顺序):

| Stage id | 含义 |
|---|---|
| `persona_setup` | 填角色卡 |
| `memory_build` | 造初始记忆 (facts / reflections 预填) |
| `prompt_assembly` | 组装 prompt (一般不停留, 透传) |
| `chat_turn` | 对话中 |
| `post_turn_memory_update` | 对话后写入 recent, 可选触发 memory ops |
| `evaluation` | 评分; `advance()` 会回到 `chat_turn` 形成循环 |

> Stage **不会**自动 advance — 所有 stage 切换**都由测试员显式点按钮**. 这是避免"测了一半 stage 偷偷跳了 tester 以为还在原 stage"的 footgun (契约见 `/api/stage` 端点).

### 2.3 顶栏 Timeline chip (快照 + 消息计数)

![timeline chip 展开面板](images/03_timeline_chip.png)

- 展示 `消息计数 / 最近快照 / 最近用户发言`.
- 展开面板后有 **[去快照]** 按钮直跳 Diagnostics → Snapshots.
- 每条消息会带 `source` 标签, 取值闭集:
  - `manual` — 测试员手打
  - `simuser` — SimUser 模式生成的 user 草稿
  - `script` — Script 模式驱动的 user turn
  - `auto` — Auto-Dialog 自动对话产出的消息
  - `llm` — Chat / Auto 模式下 Target AI 的 assistant 回复
  - `inject` — 通过 [注入 sys] 按钮插入的 system 消息
  - `external_event_banner` — 外部事件模拟生成的"提示条"消息

> `source` 标记的是**消息**的来源; 预览右栏的 "wire source" (chat.send / avatar_event / agent_callback / proactive_chat / auto_dialog_target / memory.llm / judge.llm / ...) 是**构造给 LLM 的 prompt 请求**的来源, 二者不是一套命名. 见 §3.5 Prompt Preview.

### 2.4 关于语言与主题切换

**当前版本 UI 仅支持简体中文, 主题仅支持深色.** Settings → UI 下这两个选择器**存在但 disabled**, 是 i18n 框架的占位 — 等翻译 / 浅色主题补完后再启用. 见 §8.5.

> Persona 的 `language` 字段是另一回事, 那影响 LLM 回复语种, 与 UI 文案无关.

---

## 3. Chat 对话区 — 四种模式 + 外部事件模拟

### 3.1 界面概览

![Chat workspace 主界面 (左对话流 + 中 composer + 右 Prompt Preview)](images/04_chat_overview.png)

Chat workspace 三栏布局:

- **左栏上**: 对话流 (`user` / `assistant` / `system` 气泡 + memory note 小斜体).
- **左栏下**: Composer — 上一行是元信息 (时钟 + Next turn 推进 + **Role 下拉 user/system** + **Mode 下拉** Manual/SimUser/Script/Auto); 下一行是 textarea + **[发送]** + **[注入 sys]** + **[⋯ more]**. 外部事件模拟面板默认折叠在 composer 下方.
- **右栏**: Prompt Preview — **Structured** 视图 (按来源 tag 分节, 每段带字符数徽章) + **Raw wire** 视图 (真正送到 LLM 的 `messages: [{role, content}, ...]`). 两种是切换关系, 不是 tab.

> **Role 下拉**: 选 `user` 时 [发送] 按普通对话走 LLM; 选 `system` 时 [发送] 语义变成"以 system 身份追加一条消息"(主要用于插入上下文). [注入 sys] 按钮是 Role 无关的独立入口, **不走 LLM**, 直接把 textarea 内容以 `role=system / source=inject` 落盘, 用于"往对话中塞一条 system 备注/规则", 典型用于测试 AI 对 system 提示的响应性.

### 3.2 四种输入模式 (composer 顶部 Mode 下拉)

| Mode | 含义 | 典型用途 |
|---|---|---|
| `Manual` | 自己打字发送 | 探索性对话 |
| `SimUser` | 调 SimUser LLM 生成一条"用户可能会说的话"草稿 | 压测 AI 对不同风格用户的回复 |
| `Script` | 从 Setup → Scripts 加载预置剧本, 按 [下一轮] / [跑完] 前进 | 可重放的 regression case |
| `Auto` | 启动后双 AI 自动对话 (SimUser × Target) | 无人值守批量造对话数据 |

**关键语义**:

- **SimUser**: 切到该模式 → 展开 Style 下拉 + [生成] 按钮 → 点 [生成] 得到一条草稿填入 textarea, 源 tag = `simuser`. tester 修改过草稿后 tag 自动退回 `manual`.
- **Script**: 切到该模式 → 加载一个剧本 → [下一轮] 走一步 SSE / [跑完] 跑到尾 / [卸载] 清空 script_state.
- **Auto**: 配置 simuser & target + 轮数 → 点 [启动 Auto] → Chat 顶部出现 **auto_banner**, 上面有 **[暂停] [停止]** + 速度拨盘. **这是 testbench 里唯一可以边跑边停/暂停的 LLM 操作**.

### 3.3 外部事件模拟 (Avatar / Agent Callback / Proactive)

外部事件模拟是**独立于 Chat 消息输入**的另一条触发路径, 复现主程序**非用户发起**的 3 种 LLM 入口:

| 入口 | Kind | 典型场景 |
|---|---|---|
| Avatar Interaction | `avatar` | 主人用棒棒糖 / 拳头 / 锤子碰 AI |
| Agent Callback | `agent_callback` | 后台任务 (绘图 / 搜索) 完成后通知 AI |
| Proactive Chat | `proactive` | 定时 / 空闲主动搭话 |

![外部事件模拟面板展开 + 3 个子 tab](images/05_external_event_panel.png)

**详细测试手册**见 [external_events_guide.md](external_events_guide.md), 那里覆盖 payload 字段枚举 / 去重窗口 / `reason` 代码闭集 / mirror_to_recent 三态 / PASS 信号等.

**手册用户必读的 3 条**:

1. **预览 ≠ 触发**. 预览只构造 instruction wire, **不调 LLM, 不写 session.messages, 不消耗去重缓存**. 触发才真发.
2. **去重窗口**: 同 `(interaction_id, tool, action)` 在 8000ms 内二次触发会得 `reason=dedupe_window_hit`, 对话区不新增消息. 连点没反应是去重, 不是 bug.
3. **`reason` 字段闭集**: 成功为 `null`, 失败只会是预定义代码 (`invalid_payload / dedupe_window_hit / empty_callbacks / pass_signaled / llm_failed / persona_not_ready / chat_not_configured / unsupported_event`). 出现其它值是上游契约漂移, 请报告.

**不可中断**: Invoke event 是 mutation 性操作, **严禁 AbortController**. 点触发后按钮 disabled + 文案换 spinner 直到 api.js 90s 超时或返回. 中途刷新会留下部分副作用, 别这样做.

### 3.4 Auto-Dialog (双 AI 自动对话)

![auto_banner + 暂停 + 停止按钮](images/06_auto_dialog_banner.png)

启动流程:

1. Composer Mode 切到 **Auto** → 配置以下字段, 然后点 **[启动 Auto]**:
   - **Style** 下拉 — 选 SimUser 的说话风格预设 (和 SimUser 模式共用一套 style 列表; 也有 [自定义 persona] 折叠区可临时覆盖).
   - **总轮数** input (1-50, 默认 5).
   - **Step mode** 下拉 — `off` (连轴跑, 默认无间隔) 或 `fixed` (固定间隔).
   - **Step 秒数** input (1-604800 秒, 默认 300 = 5 分钟; 仅 `fixed` 模式生效).
2. Chat 顶部出现 **auto_banner**: `进行中 轮 N/M · [暂停] [继续] [停止]`.
3. 每轮内部: SimUser LLM 生成一条"用户"消息 → Target (chat) LLM 回复 → 分别写入 `session.messages`.
4. **暂停期间可以正常触发外部事件** — 事件会插在当前轮和下一轮之间.
5. **[停止]**: 后续不再新生成, 已 inflight 的那轮走完就结束 (不硬断); 点完按钮通过后端 POST 控制面, 真实状态迁移靠 SSE 的 `stopped` 事件回传.

> banner 上**没有"速度拨盘"**. 节奏在启动时就通过 step mode + step 秒数决定好了; 要改节奏得 [停止] 后以新参数重启.

### 3.5 Prompt Preview 右栏

两种视图:

| 视图 | 内容 | 用途 |
|---|---|---|
| **Structured** | 按来源分节 (`session_init / character_prompt / memory_flat / closing / recent_history`), 每节带 tag 和字符数 | 人类视角检查哪一段从哪来 |
| **Raw wire** | `messages: [{role, content}, ...]` 真实数组 | 审计送到 LLM 的最终内容 |

**来源过滤**: Chat 页面的 preview **只显示对话域** wire (`chat.send / auto_dialog_target / avatar_event / agent_callback / proactive_chat`). 记忆合成 (`memory.llm`) / 评分 (`judge.llm`) / SimUser 草稿生成 (`simulated_user`) 的 wire 走各自入口的 **[预览 prompt]** 按钮显示, 不污染这里.

**刷新**: 第一次 mount 不自动拉, 需要点面板内 **[刷新]** 按钮. 每次 Chat send / 外部事件成功后, 对应 wire 会覆盖此处.

---

## 4. Memory 记忆编辑 — Setup Workspace Memory 子组

Testbench 把主程序的 4 层记忆**完全暴露**给测试员编辑 + 触发 LLM 操作, 方便压测各种记忆状态对对话的影响.

### 4.1 四子页速览

![Setup workspace 左侧子导航 + 4 个 memory 子页](images/07_memory_four_subpages.png)

Setup 左侧导航分两组:

- **顶部 4 页**: Persona 角色卡编辑 / **Import Persona** (内置预设 + 从主程序 `~/Documents/N.E.K.O/.../characters.json` 导入) / Virtual Clock 虚拟时钟 / Scripts 脚本管理.
- **Memory 组 4 子页**: Recent 最近对话 / Facts 事实 / Reflections 反思 / Persona 角色卡 (memory 版).

> **Import 不是导入聊天历史**. 它只灌 Persona (角色卡 + memory 同名 JSON), 对 `session.messages` 不动. 当前手册版本没有"一键把 session.messages 回灌到 recent"的按钮 — 如需压测"某段对话作为 recent"场景, 请直接在 Recent 子页用 raw JSON 视图粘贴, 或通过 `POST /api/memory/recent` 由 API 写入.

Memory 4 子页对应 4 个 JSON 文件 (在 `sandboxes/<session_id>/memory/<character>/`, `<character>` 一般是当前 persona 的 `character_name`):

| 子页 | 文件 | 实际结构 (以真实代码为准) |
|---|---|---|
| **Recent** | `recent.json` | 压缩后的近期对话段, 含候选事实标记 |
| **Facts** | `facts.json` | `list[{id, text, importance (1-10), entity (master/neko/relationship/world), tags, hash, created_at, absorbed}]` |
| **Reflections** | `reflections.json` | 由 `reflect` op 从未 absorbed 的 facts 合成 |
| **Persona** (memory 版) | `persona.json` | 顶层 `dict[entity -> {facts: [...]}]`, 记录每个实体的结构化事实集 |

> Setup → Persona (顶部 4 页之一) 和 Setup → Memory → Persona 入的是**同一份 `persona.json`**, 只是编辑器视图不同 (顶部 Persona 页是结构化表单, memory 版是 raw JSON 编辑器). 改任一个会立即反映到另一个.

### 4.2 5 个 LLM Op (触发操作面板)

每个 memory 子页底部都挂一个 **"触发操作"面板**. 实际 op 表:

| Kind | 可触发的 op | 说明 |
|---|---|---|
| `recent` | `recent.compress` | 压缩 `recent.pairs` 尾部, 抽出可提事实的 candidate |
| `facts` | `facts.extract` | 从会话抽新事实 |
| `reflections` | `reflect` | 从事实合成反思 |
| `persona` | `persona.add_fact` | 向 persona.facts 加一条, 含矛盾检测 |
| `persona` | `persona.resolve_corrections` | 批量裁决 persona 的 corrections 队列 |

每个 op 有两个按钮:

- **[预览 prompt]** — 只构 wire 不调 LLM, 可反复点不花钱. 用来确认 op 的 instruction 结构.
- **[触发执行]** — Dry-run → 弹预览 drawer → tester 可编辑 payload → [Accept] commit / [Cancel] discard.

> **预览按钮位置**: 与触发按钮紧邻, 在触发面板底部, 不放在页顶行动区 — 预览是触发前的 dry-run, 不是独立页面级动作.

### 4.3 Recent 子页 (对话最近条目)

![Recent 子页完整截图](images/08_memory_recent.png)

所有 4 个 memory 子页走同一个 **memory_editor** 容器, 顶部有两个视图:

- **Structured (默认)** — 按 kind 渲染表单卡片, 点 `+` 加条目, 常见字段直接 input, 低频字段折叠在 `[高级 ▾]` 里.
- **Raw JSON** — 原生大 textarea, 用于改 structured 视图兜不住的罕见 payload. 切回 structured 时会 parse 一次灌回; parse 失败会拒绝切换, 要求先修好 Raw.

顶部工具栏: **[保存]** (按当前视图的 model) / **[重新加载]** (丢弃 draft 回服务器状态) / dirty 徽章.

底部挂**触发操作面板** — 对 Recent 子页来说是 `recent.compress` (见 §4.2).

> 没有 "[Import from session]" 按钮, 见 §4.1 说明.

### 4.4 Facts / Reflections / Persona 子页差异

三子页复用同一个 memory_editor 容器 (§4.3 的结构), 差异只在字段集:

- **Facts**: structured 视图按 entity (`master / neko / relationship / world`) 分卡片; 每条 fact 可编辑 `text / importance (1-10) / entity / tags`, `id / hash / created_at / absorbed` 在 `[高级 ▾]` 里保留可读. `facts.extract` op 输出是新增候选, 经触发面板的 drawer 由 tester 逐条 Accept 才落盘. 未 `absorbed` 且 `importance ≥ 5` 的 facts 是 reflection 合成时的素材源.
- **Reflections**: 每条 reflection 的核心字段为 `text / source_fact_ids / created_at`. `reflect` op 会从未 absorbed 的 facts 合成一条新 reflection 挂到列表尾.
- **Persona (memory 版)**: 顶层为 `dict[entity -> {facts: [...]}]`, 与 Setup → Persona (角色卡) 共享同一份 `persona.json` (见 §4.1). 角色卡字段 (character_name / master_name / language / backstory / ...) 在 Setup → Persona 顶部表单页编辑, memory 版更适合直接查看/改结构化的 entity → facts 映射.

### 4.5 记忆文件的原子保证

所有 memory 文件 (`recent.json` / `facts.json` / `reflections.json` / `persona.json`) 的写入都遵循 **tmp → fsync → rename 覆盖**: 先写到 `<file>.tmp`, `fsync` 刷盘后原子 rename 到最终文件名. 所以任何时候读到的 4 件套都是**完整旧版**或**完整新版**, 不会是半截. 这意味着 tester 在编辑过程中进程被 kill / 机器断电, 最多丢掉当前未保存的改动, 不会把 JSON 写坏. 如果同时编辑多个 memory 文件, 写入顺序在代码里有固定约定, 不会交叉半旧半新.

---

## 5. Evaluation 评分 — Schemas / Run / Results / Aggregate

Evaluation workspace 让测试员用一份 **schema** (评分维度定义) 跑 judge LLM, 给对话 / 单条消息打分.

### 5.1 四子页工作流

![Evaluation workspace + 4 个子页](images/09_eval_workflow.png)

典型顺序:

1. **Schemas** — 定义 / 导入评分维度.
2. **Run** — 选 schema + 选评分目标 (整段对话 / 某条消息 / 多条消息批量) + 可选 judge 模型覆盖 → 运行评分.
3. **Results** — 看单条评分结果明细.
4. **Aggregate** — 跨 run 聚合统计 + 导出.

### 5.2 Schemas 子页

Schema 结构 (JSON 最小例):

```json
{
  "id": "schema_basic_v1",
  "name": "基础对话质量 v1",
  "mode": "absolute",
  "granularity": "single",
  "dimensions": [
    { "key": "character_consistency", "label": "人设一致性", "scale": [1, 5] }
  ],
  "judge_instruction": "你是严格但公正的评分员..."
}
```

关键字段:

- `mode`: `absolute` (打绝对分) / `comparative` (对比参照) .
- `granularity`: `single` (单条消息) / `conversation` (整段对话).
- `dimensions`: 评分维度列表, 每维有 `scale` 数值上下限.

操作: **[新建] / [从 JSON 导入] / [复制] / [删除]**. 删除不可恢复; 被 run 引用过的 schema 会在删除时警告.

### 5.3 Run 子页

![Run 子页 + 进度条](images/10_eval_run.png)

1. 顶部选 **Schema** — 选完之后 mode / granularity 等参数自动锁定, 下面的控件按 schema 自适应显隐.
2. 选评分目标:
   - `scope=messages + granularity=single` — 可多选消息, 批量评 (后端硬限 50 条, 超出截断 + toast 警告).
   - `scope=conversation + granularity=conversation` — 消息选择器隐藏, 就是整段.
   - `scope=messages + granularity=conversation` — 见 Schema 具体允许组合.
3. (仅 comparative 模式) 三选一填 reference: 内联文本 / 消息自带 reference_content / (暂不支持) script 导入.
4. (可选) 展开 **高级: judge 模型覆盖** `<details>` 填临时 provider / model / temperature, 不填就用 Settings → Models → judge 组.
5. 按钮: **[运行评分]** 与 **[预览 prompt]** (dry-run, 不调 LLM).

**运行中**:

- 后端是**一次 POST 跑完返回**, 前端按钮 disabled + 文案 "运行中..." 直到返回. 客户端不做逐条 SSE 流式进度.
- **没有 [暂停] / [取消] 按钮**. batch=10 × 每条 5-15s 可能耗时 1 分钟以上, 大规模 run 前看一眼成本估算.
- 返回后底部的 **"本次结果"** 区渲染 per-result 卡片: verdict 徽章 + overall 分 / gap + analysis. 单条失败 (LLM 超时等) 那张卡片标红 + [重试], 重试只跑那一条.

### 5.4 Results 子页

本会话历史评分结果的消费界面 (数据源 `GET /api/judge/results`, 读 `session.eval_results`; 跨 session 不保留).

**布局** (单列, 右侧抽屉):

1. Header + 说明.
2. **Filter bar** (顶部 sticky) — schema / mode (absolute / comparative) / granularity / scope / verdict / judge_model / passed / errored / message_id / min_overall 等. 过滤状态持久化到 `localStorage` (跨 session 保留, 方便反复查相同问题).
3. **工具栏** — 选中计数 + 批量操作 (删除) + 统一导出按钮 (走 session_export_modal) + 刷新.
4. **表格** — 可多选 / 排序 / 点行打开详情抽屉.
5. **Drawer (右侧滑入)** — 按 Collapsible 区块展示: 分数细节 / analysis / 原始 messages 快照 / prompt messages / reference (仅 comparative) / errors.

> **没有独立的 "[看原始 wire]" 按钮** — 真实送给 judge LLM 的 messages 作为 drawer 的一个 Collapsible 区块直接展示, 不单独跳页.

**跨 workspace 联动**: 在 Chat 点某条消息的评分徽章会把 `filter.message_id` 预置到本页, 切过来即显示这条消息的所有评分.

### 5.5 Aggregate 子页

跨 run 聚合视图, 数学 + 分组由后端 `judge_export.aggregate_results` 负责, 本页只画图:

- 按 schema / dimension / judge_model 等聚合, 显示 mean / median / std / min / max 及分布.
- **[导出汇总报告]** — 单按钮, 调统一的 session 导出 modal, 预设 scope=`aggregate_report`. 不是独立的 CSV/JSON 双按钮.

---

## 6. Session 管理 — 保存 / 加载 / 自动保存 / 快照 / Rewind

Session 不是独立 workspace, 所有操作入口在**顶栏 Session 下拉**和 **Settings → Autosave**.

### 6.1 操作入口一览

| 操作 | 入口 | 说明 |
|---|---|---|
| **新建会话** | 顶栏 Session 下拉 → [新建会话] | 空白会话, 自动 autosave |
| **销毁当前会话** | 顶栏 Session 下拉 → [销毁当前会话] | 删当前 sandbox, 保留 saved_sessions |
| **保存到存档** | 顶栏 Session 下拉 → [保存到存档] / [另存为…] | 写 `saved_sessions/<name>.json` |
| **加载存档** | 顶栏 Session 下拉 → [加载存档 / 导入 JSON…] | 同一入口支持从已保存列表选, 或从外部 JSON 文件导入 |
| **恢复自动保存** | 顶栏 Session 下拉 → [恢复自动保存…] | 列最近滚动 autosave, 崩溃恢复用 |
| **导出 (zip / json)** | 右上角 **⋯ 菜单 → [导出…]** | 统一导出 modal, 选 scope (会话 / 角色+记忆 / 对话 / 对话+评分 / 仅评分 / 剧本模板) 和格式 (zip / json) |
| **自动保存配置** | Settings → Autosave | debounce / force / rolling / keep window |
| **手动建快照** | Diagnostics → Snapshots → [+ 手动建快照] | 即时建一个 `trigger=manual` 快照 |
| **Rewind 快照** | Diagnostics → Snapshots → 选快照 → [回退] | 回档前会**隐式先打一次 `pre_rewind_backup`** |

![顶栏 Session 下拉菜单](images/11_session_chip_menu.png)

### 6.2 保存 / 加载 / 自动保存 组合场景

| 场景 | 期望行为 |
|---|---|
| 新建 + 不保存 + 关浏览器 | 下次开看不到 (autosave 要时间积攒, 未达 debounce/force 阈值的丢失) |
| 新建 + autosave 开 + 达到 debounce 阈值 + 关浏览器 | 下次开 [恢复自动保存…] 可恢复 |
| 新建 + 手动保存 + 关浏览器 | 下次开 [加载存档 / 导入 JSON…] 看得到 |
| 加载 + 编辑 + [保存到存档] | 覆盖旧文件 (atomic, 不会丢) |
| 加载 + 编辑 + [另存为…] | 写新文件, 旧文件不动 |
| Rewind 到旧快照 | 先隐式 `pre_rewind_backup` 快照, 再回档 |
| 导出 zip + 另一侧 [Load → Import JSON] | 完整恢复 |
| 同一会话双 tab | 仅最早 tab 可写, 后开 tab 进 BUSY lock 只读态 |

### 6.3 Rewind 的隐式快照保护

**用户痛点**: tester 点 Rewind 想"看看 3 轮前的状态", 但忘了保存现场, 一点就丢当前改动.

**Testbench 防御**: 每次 Rewind **先隐式打一次 `pre_rewind_backup` 快照**, 再执行回档. 所以:

- Rewind 不会真丢数据 — 隐式快照永远可再回退回来.
- 快照不是纯内存 — 最近 `max_hot` (默认 30) 条热快照留在内存; 更早的自动压缩到 `<sandbox>/.snapshots/<id>.json.gz` 变**冷快照**, Snapshots 表格里会标 `cold` 徽章. 冷快照仍可 Rewind / 查看 / 导出, 只是每次读需解压一下.
- 同 trigger 的 chat send / memory op / stage advance 等在 5 秒内多次触发会**合并**到同一条快照 (只保留最新状态, 保持时间线可读); `init` / `manual` / `pre_rewind_backup` / `pre_reset_backup` 这 4 类永不合并.

### 6.4 Autosave 配置 (Settings → Autosave)

页面分两张卡:

**Status 卡** — 展示当前 scheduler 状态: `enabled / dirty / last_flush_at / last_source / last_error / stats (notifies / flushes / errors / skipped_disabled / skipped_lock_busy)`; 底部有 **[立即保存一次]** 按钮, 调 `POST /api/session/autosave/flush`.

**Config 卡**:

| 字段 | 默认 | 范围 | 说明 |
|---|---|---|---|
| `enabled` | `true` | — | 勾上才滚动落盘 |
| `debounce_seconds` | `5` | 0.5-300 | 最近一次改动后静默多久才写 (防抖) |
| `force_seconds` | `60` | 0.5-3600 | 无论静默与否, 最多多久必须强制写一次 (防丢) |
| `rolling_count` | `3` | 1-3 | 保留最近 N 份 autosave, 超过 LRU 删 |
| `keep_window_hours` | `24` | 1-720 | 滚动保留窗口 (小时), 超窗的 autosave 会被清理 |

**没有** "on_idle_only" / "interval_sec" 字段 — 节奏由 debounce + force 两轴控制, 过期清理由 rolling_count + keep_window_hours 控制.

页面底部还挂一个 **[打开自动保存管理面板]** 按钮, 点了弹出 session_restore_modal (和顶栏 Session 下拉的"恢复自动保存…"是同一个 modal).

---

## 7. Diagnostics 诊断 — Errors / Logs / Snapshots / Paths / Reset

Diagnostics 是**元操作**环境, 提供**事后**查看 / 归档 / 重置能力. 5 子页.

### 7.1 Errors 子页

![Errors 子页 + 一条展开的 stack trace](images/12_diag_errors.png)

数据源是**后端进程级 ring buffer** (200 条上限, server 重启清空) + 前端 errors_bus 本地条目合并去重. 主要用来回看最近哪里出了错, **不是用来提交反馈** (testbench 是本地工具, 没有反馈提交按钮 — 要反馈请截图/复制 JSON 自行发给维护者).

**Toolbar 控件**:

- **source 下拉**: 按来源筛 (middleware / http / sse / js / promise / resource / pipeline / synthetic / 全部).
- **level 下拉**: 按严重度筛 (error / warning / info / fatal / 全部).
- **搜索框**: 全文搜 type / message / detail, 300ms 防抖.
- **"包含 info 级"复选框**: 默认不勾 (屏蔽 info 级审计噪声); 勾上后才把 info 纳入结果.
- **"自动刷新"复选框**: 默认勾, 每 5 秒自动拉一次.
- **[刷新]**: 手动立即拉一次.
- **[制造测试错误]**: 主动触发一条合成错误 (走 errors_bus 本地+后端双写), 用来验证错误管道是否通.
- **[清空]**: 清**后端 ring buffer + 本地 store.errors** (不动磁盘上的 JSONL 日志).
- **Security quick-filter chips**: 一行底栏按钮 (`integrity_check` / `judge_extra_context_override` / `prompt_injection_suspected` / `timestamp_coerced` / 全部), 点一个按 `op_type` 过滤, 再点同一个清除.

**条目展示**: 每条是一个 `<details>` 折叠块, 折叠态显示 `时间 · 来源徽章 · level 徽章 · type · 摘要`; 展开态打印完整 `id / source / level / type / message / method / url / status / session_id / user_agent` 元信息, 以及 `trace_digest` 和 `detail` 两个嵌套子 `<details>`.

**跨 workspace 跳转**: 其它页面 (例如 Evaluation Results 的错误徽章) 可以通过 `ui_prefs.diagnostics_errors_filter` 预填 filter 再跳这里. 这是代码级机制, 日常使用无需关心.

### 7.2 Logs 子页

结构化 JSONL 日志查看器. 数据源是磁盘上的 `tests/testbench_data/logs/<session_id>-<YYYYMMDD>.jsonl` (per-session per-date), 服务重启**不丢** (与 Errors 的内存 ring buffer 相反).

**选择条**:

- **Session 下拉**: 从 `/api/diagnostics/logs/sessions` 列当前所有写过日志的 session. 也支持**全部会话** (把当天所有 session 的文件并集展示).
- **Date 下拉**: 该 session 写过日志的日期列表, 默认选最新的那天. 选择会持久化到 localStorage.
- **保留天数条**: 顶部显示当前 `LOG_RETENTION_DAYS` (默认 14) + 总文件数 + 总体积; 旁边有 **"启用 DEBUG 日志"** 开关 (默认关, 勾上后后端开始把 DEBUG 级落盘), 以及 **[立即清理过期]** 按钮.

**过滤工具栏**:

- **level 下拉**: error / warning / info / debug / 全部.
- **op 下拉**: 按事件类型 (chat_send / memory.compress / judge.run / external_event / ...) 筛, 选项来自后端 facets 统计.
- **关键字搜索**: 全文搜 payload / error JSON 串.
- **"自动刷新"复选框**: 默认勾, 每 5 秒拉一次.
- **[导出 JSONL]**: 导出当前 session + date + filter 命中的日志 (全部会话模式下该按钮 disabled).

**条目展示**: 折叠态 `时间 · level · op · 摘要`, 展开态显示完整 payload + error + "原始 JSON" 子 `<details>`. 级别为 WARNING / ERROR 的条目默认展开.

> **没有 follow mode (`tail -f`) 按钮**; "自动刷新"是 5 秒轮询, 不会自动滚到底, 需要的话手动滚动.

### 7.3 Snapshots 子页

快照列表 + 管理. 表格列: `时间 (真实 + 虚拟) / 标签 / trigger / 消息数 / 内存大小 / stage / 存储 (hot/cold) / 操作`.

**Trigger 取值闭集**:

| Trigger | 由什么触发 |
|---|---|
| `init` | 新建 / 加载会话自动打一条 |
| `manual` | 点 [+ 手动建快照] 按钮手工打 |
| `send` | chat send / auto turn / script next 等消息产生时 |
| `memory_op` | memory 组任一 op commit 后 |
| `stage_advance` | stage 切换后 |
| `persona_update` | persona 编辑保存后 |
| `script_load` | 加载剧本时 |
| `auto_dialog_start` | 启动 Auto 模式时 |
| `pre_rewind_backup` | Rewind 前自动兜底 (§6.3) |
| `pre_reset_backup` | Reset 前自动兜底 (§7.5) |

**工具栏按钮**: `[+ 手动建快照]` / `[刷新]` / `[清空 (保留备份)]`.

**行级操作**: `[查看 payload]` / `[重命名 label]` / `[回退]` / `[删除]`. 备份类快照 (`pre_rewind_backup` / `pre_reset_backup`) 会标"备份"徽章, 清空操作也会保留这些.

### 7.4 Paths 子页

**系统健康卡片 (顶部)**: 状态总结 (healthy / warning / critical), 下面 5 个指标行:

- `autosave_scheduler` — 当前会话 autosave 线程是否活.
- `orphan_sandboxes` — 发现几个"有 sandbox 但无匹配 session 元信息"的孤儿目录.
- `diagnostics_errors` — 当前 Errors 条目数.
- `disk_free_gb` — 数据盘剩余空间.
- `log_dir_size_mb` — 日志目录体积.

非 healthy 指标会在卡片顶部额外列出原因和建议动作 (例如"孤儿沙盒过多, 去下方清理"/"日志占用过大, 建议降低保留天数").

**数据目录大卡**: 突出显示 `data_root` (`tests/testbench_data/`) 路径 + 总体积 + 文件数 + [复制] / [打开] 按钮.

**按组分段的条目表**: 每组一张表格, 列含 `名称 / 路径 / 大小 / 文件数 / 存在 (✓/✗) / 操作`:

| 分组 | 典型条目 |
|---|---|
| **session (仅当前会话)** | `current_sandbox` (当前会话沙盒) / `current_session_log` (当前会话日志文件) |
| **shared** | `sandboxes_all` / `logs_all` / `saved_sessions` / `autosave` / `exports` / `user_schemas` / `user_dialog_templates` |
| **code (只读)** | `code_dir` / `builtin_schemas` / `builtin_dialog_templates` / `docs` |

**行级操作**:

- **[复制路径]** — 复制到剪贴板 (`navigator.clipboard.writeText`).
- **[打开]** — 调 `POST /system/open_path` 用系统默认文件管理器打开. **仅对 `testbench_data/` 子路径启用**, code-side 条目 disabled (防止误操作 repo 源码); 不存在的路径也 disabled.

**孤儿沙盒清理段**: 列出"目录存在但没有 saved_sessions 元信息能追上的"孤儿沙盒 (session 被销毁或意外崩溃后遗留). 提供:

- **[清除所有空孤儿 (0B)]** — 一键批量删除完全空的遗留 session 目录.
- 单行 **[删除]** — 逐条删, 先 `window.confirm`.

**顶部工具栏**: **[刷新]** / **[导出沙盒快照]** (调统一导出 modal, 预设 scope=full + json + include_memory) / 平台徽章 (Windows / Linux / macOS).

> **不是一个固定 9 条的路径清单**, 实际条目取决于后端 `/system/paths` 返回 (目前典型 15 条左右, 随 feature 增加而动). 具体以 UI 为准.

### 7.5 Reset 子页 (⚠️ 破坏性操作)

三级 Reset, 并排三个卡片 (`Soft` / `Medium` / `Hard`). 每级都列出**会清什么 / 会保什么**, 点 [执行 Reset] 后会弹**二次确认 modal**, 再次列细节 — **不需要输入会话名或关键字**, 只需在 modal 里点 [确认执行 XXX Reset].

| 级别 | 会清 | 会保留 |
|---|---|---|
| **Soft** | messages + eval results | persona / memory / virtual clock / model config / schemas / timeline / stage |
| **Medium** | messages + eval results + **memory** | persona / virtual clock / model config / schemas / timeline / stage |
| **Hard** | messages + eval results + memory + persona + virtual clock + stage + 非备份快照 | model config / schemas / 备份类快照 (`pre_reset_backup` / `pre_rewind_backup`) |

**兜底机制**: 所有三级 Reset 在执行前**都会先打一个 `pre_reset_backup` 快照**挂到时间线上. 不慎点了想回滚, 去 Diagnostics → Snapshots 找最近的 `pre_reset_backup_<level>_<ts>` 条目 Rewind 即可. 所以 Reset **并非完全不可撤销**, 但 Hard Reset 会清很多状态, 请在二次确认 modal 里仔细看清列表.

**Medium / Hard 额外行为**: 执行成功后浏览器**会自动 reload**. 这是刻意设计 — 清 memory / persona 后很多挂载组件的"有数据"渲染路径不再安全, 直接整页刷新避免脏状态. Soft Reset 不刷新, 仅 emit 局部刷新事件.

---

## 8. Settings 设置 — Models / API_keys / Providers / Autosave / UI / About

### 8.1 Models 子页

按**组**配置. 四组互相独立:

| 组 | 用途 | 必填吗 |
|---|---|---|
| `chat` | Chat workspace 的主对话 LLM | 要发消息就必填 |
| `memory` | recent.compress / facts.extract / reflect 的 LLM | 要触发 memory op 才必填 |
| `judge` | Evaluation 的评分 LLM | 要跑 Evaluation 才必填 |
| `simuser` | Chat composer 的 SimUser 模式生成 | 要用 simuser / auto 才必填 |

每组字段:

- `provider` (下拉, 从 Providers 子页列表选)
- `model` (文本, e.g. `gpt-4o-mini / claude-3-5-sonnet-latest`)
- `base_url` / `api_key` (从 provider 继承, 可本组覆盖)
- `temperature` / `max_tokens` / `top_p` / `timeout` 等

每组底部 **[测试连接]** 发一个最小 chat completion 验证通. 测试结果只 toast, 不写持久日志.

### 8.2 API Keys 子页

全局 key 表 (落盘在 `tests/api_keys.json`):

| provider | key (mask) | 状态 |
|---|---|---|
| openai | `sk-...xxx` | ✅ 有效 |
| anthropic | `sk-ant-...yyy` | ⚠️ 未验证 |

- **[编辑]** → 明文显示, 改后保存.
- **[批量 Test]** → 对所有 provider 发最小请求更新状态列.

### 8.3 Providers 子页

定义可用的 provider **模板** (base_url / auth 方式 / 默认 model 列表):

```json
{
  "name": "openai",
  "base_url": "https://api.openai.com/v1",
  "auth": "bearer",
  "models_default": ["gpt-4o", "gpt-4o-mini", "o1-preview"]
}
```

新加 provider (e.g. Azure / 本地 Ollama) 从这里开始.

### 8.4 Autosave 子页

见 §6.4.

### 8.5 UI 子页

| 字段 | 当前状态 | 说明 |
|---|---|---|
| **语言** | disabled, 固定 `简体中文` | i18n 框架完整但未翻译其它语种 |
| **主题** | disabled, 固定 `深色` | light palette CSS 待补 |
| **Snapshot 配置** | 可用 | `max_hot` (默认 30, 热缓存上限) + `debounce_seconds` (默认 5) |
| **默认折叠策略** | 可用 | 5 类 CollapsibleBlock (chat_message / log_entry / error_entry / preview_panel / eval_drawer) 各自的 `mode` (auto/open/closed) + `threshold` |
| **重置当前会话的 fold keys** | 按钮 | 清 LocalStorage 里本会话的折叠状态 |

### 8.6 About 子页

![About 页显示版本 + 最后更新日期 + 相关文档链接](images/13_about_page.png)

实际展示的字段 (按页面自上而下):

| 区块 | 字段 | 说明 |
|---|---|---|
| 基本信息卡片 | **版本** | `testbench <TESTBENCH_VERSION>` (semver) |
| 基本信息卡片 | **最后更新日期** | 版本最后修订日期 (ISO) |
| 基本信息卡片 | **Host:Port** | testbench 当前监听地址, 方便复制 |
| 已知限制 | 列表 | 与本手册 §9.已知限制 基本一致的精简版 |
| 相关文档 | 4 条链接 | USER_MANUAL / ARCHITECTURE_OVERVIEW / external_events_guide / CHANGELOG, 点开在新 tab 渲染 HTML |
| 页脚 | 内部文档提示 | 开发者内部文档 (LESSONS / PLAN / PROGRESS 等) 不对外公开 |

> About 页**不显示**内部阶段代号 — 阶段信息属于开发内字段, 用户不需要感知. 需要版本追溯请看"**最后更新日期**"或 CHANGELOG.

---

## 9. FAQ 与已知限制

### Q1: `/docs/<name>` 访问返 404, 为什么

Testbench 的公开文档端点有**双 404 语义**:

| 情况 | HTTP | detail.error_type |
|---|---|---|
| 请求的 `<name>` 不在白名单 | 404 | `unknown_doc` |
| 在白名单, 但对应 md 文件**还没落盘** | 404 | `file_missing` |
| 在白名单且文件存在 | 200 | HTML 渲染 |

两种 404 区别: 前者是"根本没打算公开", 后者是"公开了但未写". 后者常见于"文档还在路上"阶段.

另: md 里写的 `[xxx](yyy.md)` 会在渲染时**自动剥 `.md` 后缀** (白名单命中才剥); 非白名单的内部 md (LESSONS / PROGRESS 等) 链接会降级为灰色不可点的文本 — 不会带你撞 404.

### Q2: 端口和 host 可以改吗

可以. 见 §1.1 `--port` / `--host` CLI 参数. 默认 `127.0.0.1:48920`.

**被占用**: `netstat -ano | findstr 48920` (Windows) / `lsof -i :48920` (Linux/macOS) 找占用进程, 杀掉, 或换别的端口.

### Q3: 关浏览器后会话丢了

看 autosave 配置 (§6.4). 默认开, 最多丢 `force_seconds` (60s 默认) 内的未写改动. 完全防丢: 每次关前手动 Save.

### Q4: `reason=chat_not_configured` 但我配了

常见坑:

1. 配了但没点 **[测试连接]** 通过.
2. 只配了 memory / judge / simuser 组, **chat 组没配**.
3. api_key 对但 `base_url` 填错 (e.g. 漏了 `/v1`, 或 Azure 缺 `/deployments/<name>/`).

### Q5: 同一会话在两个浏览器 tab 同时编辑会怎样

**只有最早 tab 可写**, 后开的进只读态 + `SessionConflict` 警示 + `busy_op` 告知谁在占用. 刷最早 tab 或关掉就释放 lock.

### Q6: 顶栏 Stage chip 会自己跳吗

**不会**. 所有 stage 切换都由测试员显式点按钮. 看到"自己跳"通常是因为你在另一 tab 点了 advance, 本 tab 同步过来. 真·单 tab 内自己跳是 bug, 请报告.

### Q7: LLM 调用扣费了但没看到回复

1. Diagnostics → Logs → 搜最近 `llm_call` 事件, 查 `response` 字段.
2. response 里有内容但 UI 没显示 → 前端 render bug, 报告.
3. response 空或带 error → 看 `error_detail`, 常见: timeout / context_length_exceeded / rate_limit.

### Q8: Evaluation 跑到一半想中止

**做不到**. `/api/judge/run` 是一次性 POST, 后端跑完才返回, 前端按钮只是 disabled 等待. 唯一办法: 关窗口或刷新页面 (后端那次 run 会继续跑到它自己结束, 不会因为前端离开而中断). 因此**大规模 run 前请看 token/成本估算**, 别盲目启动 50 条 × 5 维度的 batch.

### Q9: Auto-Dialog 的 [暂停] [停止] 为什么是真暂停/真停止

Auto 是 SSE 长连接 + 后端 per-round 事件驱动, 每轮结束会检查 pause/stop flag. 所以:

- **暂停**: 当前 inflight 那轮走完, 下一轮不起, 直到 [继续].
- **停止**: 当前 inflight 那轮走完, 之后完全退出 SSE. 这和 §Q8 Evaluation 的"一次 POST 跑完"不同.

### Q10: Prompt injection 检测把我合法测试输入当攻击了

见 `external_events_guide.md` 相关 FAQ. 简短版: 这是 positive signal (主程序检测太严), 事件仍会触发, 不阻塞测试, 但请记录并反馈.

### 已知限制

1. **大 session 性能**: `session.messages` 超过 ~5000 条时 Chat 滚动会卡, 建议定期归档.
2. **Memory LLM 成本**: `recent.compress / facts.extract / reflect` 每次都消费全量 context, 大 recent 烧 token. 用 **[预览 prompt]** 先看成本.
3. **UI 语言 / 主题**: 当前版本仅简体中文 + 深色, 见 §2.4 / §8.5. i18n / 浅色主题已预留, 未启用.
4. **Evaluation 不可中断**: 见 §Q8. 大 batch 之前务必确认.
5. **不支持多用户**: testbench 是**单人 localhost 工具**, 无权限系统. 默认绑 `127.0.0.1`, 不要改 `--host 0.0.0.0` 暴露公网 (会把你的 api_keys 和对话内容一起暴露).
6. **浏览器兼容**: Chrome / Edge 测过. Firefox 大致能用. Safari 有零星 CSS bug.

---

## 10. 进阶操作 (给深度用户)

### 10.1 对话剧本模板 (Setup → Scripts)

**Setup → Scripts 子页**是对话剧本模板的管理器 (浏览/复制/编辑/新建/删除/导出), 不是跑剧本的地方. 跑剧本在 **Chat composer** 切到 `Script` 模式后, 用模板下拉选一个再按 [加载] / [下一轮] / [跑完] / [停止] 驱动 (§3.1).

**左右分栏**: 左列按 "用户模板 + 内置模板" 两组列出, 右列是编辑器 (空态 / 只读 builtin / 可编辑 user).

**规则**:

1. **内置模板不能原地改**, 只能点 **[复制为可编辑]** 生成 user 副本再编辑.
2. **user 模板 name 与 builtin 重名** = 覆盖 builtin, UI 会标 "覆盖中" 徽章.
3. **name 即文件名** (`dialog_templates/<name>.json`). 改 name = Save As (走 "save 新 + delete 旧" 两步).
4. **校验隐式内嵌在 Save 里** (打字不打扰): POST 失败 422 时带 `detail.errors`, UI 按路径红框高亮. 没有独立的 [Validate] 按钮.
5. **本页不做"试跑"** — 回 Chat 用 composer.

**模板 JSON 结构** (以内置模板为参考; 具体字段随版本演进, 以编辑器结构化视图为准):

- 顶层含 `name` / 描述 / entries 列表等元信息.
- 每个 entry 描述一个 `user` 或 `assistant` 角色的条目, 支持 text + 可选 notes.

需要精确 schema 请在本页 **[新建]** 一份看结构化视图的字段布局, 或用 **[导出]** 拿一份内置模板 JSON 反推.

### 10.2 Eval Schema 共享

Schemas 支持 JSON 导入导出, 团队协作:

1. Tester A 在 Schemas 子页 **[新建]** 维度定义.
2. **[导出 JSON]** 发给 Tester B.
3. Tester B **[从 JSON 导入]** → 一模一样的 schema, 可对比评分一致性.

### 10.3 API 直连 (curl 示例)

部分关键端点 (完整列表见代码与设计总体概述):

```bash
# 1) 版本探活
curl http://127.0.0.1:48920/version

# 2) 新建会话 (body 字段 name, 可选)
curl -X POST http://127.0.0.1:48920/api/session \
  -H "Content-Type: application/json" \
  -d '{"name":"my_session"}'

# 3) Chat send (SSE, 需要 -N; body 用 content 不是 text)
curl -N -X POST http://127.0.0.1:48920/api/chat/send \
  -H "Content-Type: application/json" \
  -d '{"content":"你好","role":"user","source":"manual"}'

# 4) Memory 触发 (op 是 URL 路径参数, 不是 body 字段)
curl -X POST http://127.0.0.1:48920/api/memory/trigger/recent.compress \
  -H "Content-Type: application/json" \
  -d '{"params":{"keep_last_pairs":2}}'

# 5) 外部事件 (avatar payload 需要 interaction_id / tool_id / action_id / target)
curl -X POST http://127.0.0.1:48920/api/session/external-event \
  -H "Content-Type: application/json" \
  -d '{
    "kind":"avatar",
    "payload":{
      "interaction_id":"demo-001",
      "tool_id":"lollipop",
      "action_id":"offer",
      "target":"avatar",
      "intensity":"normal"
    }
  }'
```

注意:
- `/api/chat/send` 走 SSE, 需要 `-N` 或其它流式客户端.
- 外部事件完整字段表 (含 agent_callback / proactive 的 payload shape) 见 `external_events_guide.md`.

### 10.4 本手册的上游知识

本手册是**操作手册**, 不含架构 / 设计理由. 深入了解推荐顺序:

1. 本手册 (USER_MANUAL) — 你在读.
2. [external_events_guide.md](external_events_guide.md) — 外部事件专项测试手册.
3. [testbench_ARCHITECTURE_OVERVIEW.md](testbench_ARCHITECTURE_OVERVIEW.md) — 代码与设计总体概述, 给开发者 / 深度用户.
4. [CHANGELOG.md](CHANGELOG.md) — 版本变更记录.

开发者内部文档 (LESSONS_LEARNED / PROGRESS / AGENT_NOTES / PLAN) 不对测试员公开, 请联系维护者获取.

---

## 汇报问题时请附带

1. **版本**: Settings → About 页截图 (含**版本**号 + **最后更新日期**).
2. **复现步骤**: 操作顺序 (从"启动 → 新建会话 → ...").
3. **相关日志条目**: Diagnostics → Logs 搜关键 event 导出 JSON.
4. **错误 toast 截图** (如有).
5. **session 导出 zip**: 右上角 `⋯` 菜单 → [导出…], 如果问题和数据状态相关.

> 这五样打包发给开发者基本能直接定位问题.

---

*手册版本对齐 `TESTBENCH_VERSION` 与**最后更新日期** (见 Settings → About).*

*Testbench 是本地工具, **没有应用内反馈按钮**. 发现手册错漏或想提建议, 请自行截图 / 复制相关 JSON (Diagnostics → Errors / Logs 条目可直接展开复制), 通过你与维护者之间约定的渠道 (邮件 / 群聊 / 代码仓库 issue 等) 提交.*
