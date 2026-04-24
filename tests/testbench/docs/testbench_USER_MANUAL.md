# Testbench 测试用户使用手册

> **受众**: 测试员 / 中级用户 (不需要读 Python 源码). 本手册覆盖**怎么用**, 不讲**为什么这样设计**. 想了解架构 / 设计权衡, 见 [`testbench_ARCHITECTURE_OVERVIEW.md`](testbench_ARCHITECTURE_OVERVIEW.md).
>
> **版本**: 对齐 `TESTBENCH_VERSION` (semver, `/api/version` 端点) + `TESTBENCH_PHASE=P26` (开发阶段内部标识, 用户侧可忽略).

本手册按你**实际打开 testbench 后看到的界面**自上而下组织:

1. [准备事项](#1-准备事项-启动--配置--首次打开) — 启动命令 / 端口 / 数据目录 / api_keys.json
2. [Workspace 导航](#2-workspace-导航---顶栏-stage-chip--timeline-chip) — 5 个 workspace 切换 + 顶栏两个 chip
3. [Chat 对话区](#3-chat-对话区--三模式--外部事件模拟--auto-dialog) — manual / SimUser / Scripted + 外部事件 (avatar / agent_callback / proactive) + Dual-AI Auto
4. [Memory 记忆编辑](#4-memory-记忆编辑--setup-workspace-四子页) — recent / facts / reflections / persona + 5 ops + 预览 prompt
5. [Evaluation 评分](#5-evaluation-评分--schemas--run--results--aggregate) — 四子页完整工作流
6. [Session 管理](#6-session-管理--保存--加载--自动保存--快照--rewind) — 11 组合全覆盖
7. [Diagnostics 诊断](#7-diagnostics-诊断--errors--logs--snapshots--paths--reset) — 5 子页 + 错误排查
8. [Settings 设置](#8-settings-设置--models--api_keys--providers--autosave--ui--about) — 6 子页
9. [FAQ 与已知限制](#9-faq-与已知限制)
10. [扩展点](#10-扩展点-给深度用户)

> **配图占位约定**: 本手册使用 `<!-- IMG: 文件名 | 描述 | 分辨率建议 | 高亮区域 -->` 内联注释标注**应拍图**位置. 注释不参与 Markdown 渲染, Ctrl+F 搜 `IMG:` 可定位所有待拍点. 图片实际由测试员拍摄, 统一放 `tests/testbench/docs/images/`, 引用时改成 `![描述](images/文件名)` 即可.

---

## 1. 准备事项 (启动 / 配置 / 首次打开)

### 1.1 启动命令

```bash
# 在项目根目录
python -m tests.testbench.server
```

默认监听 `http://localhost:48920`. 端口固定 (不支持 `--port`, 见 §9.Q2). 启动后浏览器**自动弹开** (若不弹, 手动访问该 URL).

### 1.2 数据目录

启动时控制台会打印:

```text
[testbench] data_root = C:\Users\<user>\.testbench   # Windows
[testbench] data_root = ~/.testbench                 # Linux/macOS
```

| 子目录 | 作用 |
|---|---|
| `sessions/` | 保存过的会话 JSON, 每个会话 1 个目录 |
| `autosave/` | 自动保存(每 60s 非阻塞落盘)的会话, 崩溃恢复源 |
| `snapshots/` | 快照(手动 + rewind 隐式触发)的时点回档副本 |
| `logs/` | 诊断日志 JSONL, 按日轮转 |
| `api_keys.json` | 全局 API key 存储 (**明文**, 自己保护好这个文件) |

> **注意**: `data_root` 不同于主程序 (桌宠)**的 `~/.neko/`, 两者不互通. testbench 改出来的东西**不会**污染主程序真实数据.

### 1.3 首次打开该做什么

<!-- IMG: 01_first_launch.png | 首次启动空界面 + 顶栏 "新建会话" 按钮 + Welcome Banner | 1920x1080 截全屏 | 高亮顶栏 + Welcome Banner -->

1. **处理 Welcome Banner** (如果是首次启动 / 服务端 restart 后的首次访问): 右上角会出现一次性提示条, 点 **[去配置]** 跳 Settings → API Keys, 或点 **[关闭]** 稍后配.
2. **填 API Keys**: Settings → API Keys, 按 provider (openai / anthropic / deepseek / ...) 填写 key, 点 **[保存]**. 保存后立刻生效, 无需重启.
3. **创建会话**: 顶栏点 **[+ 新建会话]**, 输入名字 (如 `smoke_test_01`). 没有会话时所有 `/api/session/*` 端点都返 `404 NoActiveSession`.
4. **配 Chat 模型**: Settings → Models → chat 组, 选 provider / base_url / model, 点 **[Test Connection]** 验证通. Memory / Eval / SimUser 等组也都要配 (可以复用同一 provider, 只是 role 不同).
5. **填 Persona** (角色卡): Setup → Persona, 至少填 `character_name` (必填, 其他可选). 不填会触发 `reason=persona_not_ready`.

这 5 步做完就进入**可用状态**, 可以切 Chat workspace 发消息了.

### 1.4 重要文件白名单 (开发者侧感知)

下面这些 md 通过 `/docs/{name}` 公开, 非白名单的 md 不可公网下载 (防敏感文档泄漏):

- `testbench_USER_MANUAL` (本手册)
- `testbench_ARCHITECTURE_OVERVIEW` (架构总览)
- `external_events_guide` (外部事件测试手册)
- `CHANGELOG` (版本更新记录)

访问 `http://localhost:48920/docs/testbench_USER_MANUAL` 可直接在浏览器看本手册. 端点行为见 §9.Q1 的 **404 双语义**说明.

---

## 2. Workspace 导航 + 顶栏 Stage chip / Timeline chip

### 2.1 5 个 Workspace

<!-- IMG: 02_workspace_topbar.png | 顶栏展开状态 + 5 个 workspace 切换按钮 + stage chip + timeline chip | 1920x120 截顶栏 | 高亮 5 个 workspace 按钮 -->

| Workspace | 快捷键 | 主要用途 |
|---|---|---|
| **Setup** | — | 填角色卡 / 导入聊天历史 / 调试虚拟时钟 / 管脚本 / 编辑 4 层记忆 |
| **Chat** | — | 和桌宠对话, 触发外部事件, 预览 prompt |
| **Evaluation** | — | 评测 schema 配置 + 批量 run + 看结果 + 聚合导出 |
| **Diagnostics** | — | 看错误 / 日志 / 快照 / 路径 / Reset 工具 |
| **Settings** | — | 全局配置: Models / API Keys / Providers / Autosave / UI / About |

切 workspace 时**顶栏折叠状态**可能变化:

- **setup / chat / evaluation**: 顶栏 Stage chip **展开**为行动栏 `[去 Chat 发送消息] [预览] [执行并推进] [跳过] [回退] [⋯ 展开面板]`
- **diagnostics / settings**: Stage chip **折叠**为小徽章 `Stage: 对话 ▾`

这是刻意设计: 诊断和设置是"元操作"环境, 不应该被 action-oriented 的 stage 控件干扰.

### 2.2 顶栏 Stage chip (阶段指示 + 快速跳转)

Stage 表示当前测试场景走到了**哪一步**. 7 个阶段:

| Stage | 展开时按钮 (常见) |
|---|---|
| 对话 (dialog) | 预览 / 发送 / 跳过 / 回退 |
| 记忆触发 (memory_trigger) | 去 Memory / 预览 / 执行 |
| 外部事件 (external_event) | 去 Chat / 预览 / 触发 |
| SimUser (simuser) | 生成 SimUser 草稿 / 使用 / 丢弃 |
| Auto-Dialog (auto) | 开始 / 暂停 / 继续 / 停止 |
| 评分 (evaluation) | 去 Evaluation Run / Results |
| 完成 (done) | 新建会话 / 导出 |

> Stage **不会**自动 advance — 所有 stage 切换**都由测试员显式点按钮**. 这是避免"测了一半 stage 偷偷跳了 tester 以为还在原 stage"的 footgun.

### 2.3 顶栏 Timeline chip (快照 + 消息计数)

<!-- IMG: 03_timeline_chip.png | timeline chip 展开面板 | 1920x200 截顶栏+面板 | 高亮 chip + "最近 N 条消息" + "去快照" 按钮 -->

- 展示 `消息计数 / 最近快照 / 最近用户发言`.
- 点展开看 timeline 面板, 有 **[去快照]** 按钮直跳 Diagnostics → Snapshots.
- Timeline 的 source 标签会显示消息来自 `chat.send / avatar_event / agent_callback / proactive_chat / auto_dialog_target / auto_dialog_simuser / simulated_user / memory.llm / judge.llm` — 测试自动对话混合场景时这个标签特别有用.

### 2.4 语言切换

Settings → UI → 语言, 支持 `中文简体 / English / 日本語 / 한국어 / Русский / 中文繁體`. 切换后**所有 UI 文案**立刻翻译, 无需刷新. (Persona 的 `language` 字段是另一回事, 那影响的是 LLM 回复的语种, 见 §4.4.)

---

## 3. Chat 对话区 — 三模式 + 外部事件模拟 + Auto-Dialog

### 3.1 界面概览

<!-- IMG: 04_chat_overview.png | Chat workspace 主界面 (左对话流 + 中 composer + 右 Prompt Preview) | 1920x1080 | 高亮 composer 的 "mode" dropdown + 折叠的外部事件面板 -->

Chat workspace 三栏:

- **左栏 对话流**: `user` / `assistant` / `system` 消息气泡 + memory note (小斜体).
- **中下 Composer**: 文本框 + **模式下拉** + 附件按钮 + Send. **外部事件面板**默认折叠在 composer 下方.
- **右栏 Prompt Preview**: 双 tab.
  - **当前 wire** — 每次发消息/触发事件后**立刻覆盖**, 看 LLM 实际收到什么.
  - **下次 /send 预估** — 尝试性渲染, 不触发 LLM.

### 3.2 三种输入模式 (composer mode dropdown)

| Mode | 含义 | 典型用途 |
|---|---|---|
| `manual` (手动) | tester 自己在文本框打字 | 探索性对话, 触发特定桌宠行为 |
| `simuser` (SimUser 模拟用户) | 调 SimUser LLM 生成一条"用户可能会说的话"草稿 | 压测桌宠对不同风格用户的回复 |
| `script` (脚本) | 从 Setup → Scripts 选一条预置脚本填入 | 可重放的 regression case |

**模式切换规则**:

- 切到 `simuser` → 点 **[生成草稿]** 按钮 (不是 Send) → LLM 返回一条草稿填入 textarea, 背景色变浅蓝标记"SimUser 原产".
- 若 tester 改过草稿 → 背景色退回白色, `source` tag 退回 `manual`.
- 点 **[Send]** → 发 LLM 得回复, textarea 清空, mode **保留**, 下次还是这个模式.

### 3.3 外部事件模拟 (Avatar / Agent Callback / Proactive)

外部事件模拟是**独立于 Chat 消息输入**的另一条触发路径, 复现主程序**非用户发起**的 4 种 LLM 入口:

| 入口 | Kind | 典型场景 |
|---|---|---|
| Avatar Interaction | `avatar` | 主人用棒棒糖/猫爪/锤子碰桌宠 |
| Agent Callback | `agent_callback` | 后台任务(绘图/搜索)完成后通知桌宠 |
| Proactive Chat | `proactive` | 定时/空闲主动搭话 |

**详细测试手册**: [`external_events_guide.md`](external_events_guide.md) — 约 250 行, 覆盖 payload 字段 / 去重窗口 / `reason` 代码闭集 / mirror_to_recent 三态 / PASS 信号等细节.

**关键点 (手册用户必读)**:

1. **预览 ≠ 触发**. `预览 prompt` 按钮只构造 wire 不调 LLM, **不动** `session.messages` / 去重缓存 / `session.last_llm_wire`. `触发事件` 才真发 LLM.
2. **去重窗口**: 同 `(interaction_id, tool, action)` 在 8000ms 内二次触发会得 `reason=dedupe_window_hit`, **对话区不新增消息**. 连点没反应是去重不是 bug.
3. **`reason` 字段闭集**: 成功为 `null`, 失败只会是 8 种预定义代码 (`invalid_payload` / `dedupe_window_hit` / `empty_callbacks` / `pass_signaled` / `llm_failed` / `persona_not_ready` / `chat_not_configured` / `unsupported_event`). 出现其他值是上游契约漂移, 请报告.

<!-- IMG: 05_external_event_panel.png | 外部事件模拟面板展开 + 3 个子 tab | 1920x400 截 composer 下方 | 高亮 tab + [预览] [触发] 双按钮 -->

### 3.4 Dual-AI Auto-Dialog (双 AI 自动对话)

<!-- IMG: 06_auto_dialog_banner.png | Chat 页面顶部的 auto_banner + 暂停 + 速度拨盘 | 1920x100 截 auto banner | 高亮 [暂停] [停止] [速度] 三控件 -->

启动 Auto-Dialog:

1. 顶栏 Stage chip → 切 **auto** → **[开始]** → 选"谁当 simuser, 谁当 target" + 轮数.
2. 启动后 Chat 页面顶部出现 **auto_banner**, 显示 `进行中 轮 N/M · [暂停] [停止] · 速度 [x1]`.
3. 每轮: SimUser LLM 生成 → Target LLM 回复 → 各自写 `session.messages`, source tag 分别为 `auto_dialog_simuser` / `auto_dialog_target`.

**暂停期间可以正常触发外部事件** — 事件会**插入**当前轮和下一轮之间. 暂停是真暂停, Auto-Dialog 的 BUSY lock 释放给外部事件用.

**停止**: 点 [停止] 后不会再新生成, 已经 inflight 的那一轮走完就结束.

### 3.5 Prompt Preview 右栏

两个 tab 的语义区别很重要:

| Tab | 触发刷新时机 | 是否真调 LLM |
|---|---|---|
| **当前 wire** | 每次 chat.send / external_event / memory.llm / judge.llm / auto_dialog_* 成功后**立刻覆盖** | 是(是刚才那次的) |
| **下次 /send 预估** | 手动点 **[刷新]** 按钮 | **否**(纯 build_prompt_bundle, 不写任何状态) |

**拿不准看哪个 tab**? 看 source tag. "当前 wire" 会显示刚才那次的具体 source (`chat.send` / `avatar_event` / ...); "预估" 只会显示 `chat.send` 猜测.

---

## 4. Memory 记忆编辑 — Setup Workspace 四子页

Testbench 把主程序的 4 层记忆**完全暴露**出来让测试员编辑 + 触发 LLM 操作, 方便压测各种记忆状态对对话的影响.

### 4.1 四子页速览

<!-- IMG: 07_memory_four_subpages.png | Setup workspace 左侧子导航 + 4 个 memory 子页标签 | 600x800 截左侧 nav | 高亮 memory group + 4 个子页 -->

| 子页 | 文件 | 结构 | LLM op |
|---|---|---|---|
| **Recent** (最近对话) | `memory/recent.json` | `pairs: [{user, assistant, ts}]` | `recent.compress` — 压缩老 pair |
| **Facts** (事实) | `memory/facts.json` | `facts: [{id, text, category, source, ts}]` | `facts.extract` — 从 recent 抽事实 |
| **Reflections** (反思) | `memory/reflections.json` | `reflections: [{id, text, trigger, ts}]` | `reflect` — 对整体对话做反思 |
| **Persona** (角色卡) | `memory/persona.json` | 扁平字段: character_name / language / backstory / ... | (无 LLM op, 纯表单) |

### 4.2 5 个 LLM Op 入口 (trigger panel)

每个 memory 子页 (Recent / Facts / Reflections) 底部有 **"记忆触发面板"**, 折叠默认. 展开后:

| Op | 输入 | 输出 | 消耗 |
|---|---|---|---|
| `recent.compress` | (自动) 全量 recent.pairs | 压缩后的 pairs + 被抽出的 facts candidate | memory LLM |
| `facts.extract` | (自动) 全量 recent + 已有 facts | 新增 facts list | memory LLM |
| `reflect` | (自动) recent + facts | 新增 reflection | memory LLM |
| `persona.auto` | (暂未开放) — | — | — |
| `memory.llm` (通用 dry-run) | JSON schema 自定义 | 任意结构 | memory LLM |

**两种按钮**每个 op 都有:

- **[预览 prompt]** — 同 §3.5 Prompt Preview, 只构 wire 不调 LLM. 可**反复点不花钱**, 用于确认 op 的 instruction 结构.
- **[触发执行]** — 真的调 LLM, 真写 memory 文件 (atomic write, 不会把原文件改坏).

> **预览按钮放置位置**: P25 r6 之后, 预览按钮**和触发按钮紧邻**, 在触发面板底部, **不**放在页顶行动区. 这是对齐"预览是触发前的 dry-run, 不是独立页面级动作"的语义, 见 [`LESSONS_LEARNED.md`](LESSONS_LEARNED.md) §7.27.

### 4.3 Recent 子页 (对话最近条目)

<!-- IMG: 08_memory_recent.png | Recent 子页完整截图 | 1920x1080 | 高亮 5 个按钮 + 触发面板 -->

核心操作:

- **编辑**: 直接点 pair 的 user / assistant 文本 → 就地编辑 → Enter 保存. 写入 `recent.json` 是 atomic (L07 约定, 不会把文件改半截).
- **删**: pair 右上角 `×` — 删除单对.
- **插入**: pair 之间 `+` 按钮 → 选 `user:` / `assistant:` / `both:` 插入空白或模板条目.
- **Import from session**: 把当前 `session.messages` 复制到 `recent.json` (谨慎! 会覆盖 recent).
- **记忆触发**: 底部 `recent.compress` op (§4.2).

**常见测试步骤**:

1. 手动塞 5-10 对 pair (模拟"老用户").
2. 点 **[预览 prompt]** 看 `recent.compress` 的 instruction 结构.
3. 点 **[触发执行]** → 右栏 Prompt Preview 看 wire + 新的 `recent.json` 结构被渲染.
4. 切 Chat workspace 发条消息 → 看 assistant 回复是否体现了压缩结果.

### 4.4 Facts / Reflections / Persona 子页

三子页操作模式相同 (CRUD + LLM op + 预览), 本节列差异点:

- **Facts**: 每条 fact 有 `category` 字段 (e.g. `preference` / `trait` / `relationship`), 过滤器可按 category 筛. `facts.extract` op 输入是整个 recent + 现有 facts, 输出是 **新增候选** (不自动写, 要 tester 逐条确认或批量 approve).
- **Reflections**: reflection 有 `trigger` 字段 (`periodic` / `manual` / `key_moment`). `reflect` op 生成一条新 reflection 挂到 list 末尾.
- **Persona**: 纯表单, 无 LLM op. 字段:
  - `character_name` (必填, 否则 `persona_not_ready`)
  - `master_name` (默认 `主人`)
  - `language` (默认 `zh-CN`, 支持 6+ 种 + 静默回退 `es` / `pt` → en, 见 `external_events_guide.md` §4)
  - `backstory` / `personality` / `speech_style` (自由文本, 拼进 system prompt)
  - `initial_greeting` (新会话第一句话)

### 4.5 记忆操作的原子保证

所有 memory 文件写都是**先写 tmp → fsync → rename 覆盖**, 任何时候读到的 `recent.json` / `facts.json` / `reflections.json` / `persona.json` 要么是**完整旧版**要么是**完整新版**, **不会**是半截写过的坏文件. 这是 L07 "atomic I/O only" 铁律.

---

## 5. Evaluation 评分 — Schemas / Run / Results / Aggregate

Evaluation workspace 支持**批量评测**: 用一个 schema (评分维度定义) + 一堆 session 作为被评对象, 跑 judge LLM 给出每维分数, 再聚合输出.

### 5.1 四子页工作流

<!-- IMG: 09_eval_workflow.png | Evaluation workspace + 4 个子页 tab | 1920x1080 | 高亮子页切换 nav -->

典型工作流**必须按顺序**:

1. **Schemas** — 定义 / 导入评分维度 (schema)
2. **Run** — 选 schema + 选 sessions → 批量跑 judge LLM
3. **Results** — 看单条评分结果 / JSON 明细
4. **Aggregate** — 跨 session 聚合统计 + 导出 CSV / JSON

### 5.2 Schemas 子页

**Schema 结构**:

```json
{
  "id": "schema_basic_v1",
  "name": "基础对话质量 v1",
  "dimensions": [
    { "key": "character_consistency", "label": "人设一致性", "scale": [1, 5], "instruction": "5=完美一致, 1=严重偏离" },
    { "key": "response_quality", "label": "回复质量", "scale": [1, 5], "instruction": "..." }
  ],
  "judge_instruction": "你是一个严格但公正的评分员..."
}
```

操作:

- **[新建]** → 填字段 → 保存.
- **[从 JSON 导入]** → 粘贴已有 schema JSON.
- **[复制]** → 基于旧 schema 改维度.
- **[删除]** → 不可恢复. 已有 run 引用的 schema 不能删 (会警告).

### 5.3 Run 子页

1. 左上选 **Schema**.
2. 左下选 **被评 sessions** (多选, 支持按日期 / 名字过滤).
3. (可选) 勾 **[只评最近 N 对 message pair]** — 限定评分窗口, 默认全量.
4. 点 **[开始 Run]**. Run ID 生成, 进度条实时刷.
5. Run 中可 **[暂停]** / **[取消]** (取消是真取消, 已 inflight 的 judge 调用走完就停, 不硬断).

<!-- IMG: 10_eval_run.png | Run 子页 + 进度条 + 正在评的 session 高亮 | 1920x600 | 高亮 [开始] [暂停] [取消] 三按钮 + 进度条 -->

**LLM 成本提示**: 每个 session 对每个 dimension 调一次 judge LLM. 10 sessions × 5 dimensions = 50 次调用. 大规模 run 前看一眼 token/花费估算 (进度条旁显示).

### 5.4 Results 子页

展示**单条** run 结果:

- 左侧: run 列表 (按时间倒序, 标 schema / session / 得分平均).
- 右侧: 选中 run 的**每维评分 + judge 的解释文本 + 原始 messages 快照**.

**验证 judge 是否瞎评**: 点 run 右上角 **[看原始 wire]** → 展开 judge LLM 实际收到的 prompt (含 messages + schema instruction), 核对 judge 的 reasoning 是否基于这段 context.

### 5.5 Aggregate 子页

跨 run / 跨 session 统计:

- 按 schema 聚合, 每 dimension 的 mean / median / std / min / max.
- 按 session 聚合, 对比不同 schema 给同一 session 的评分.
- **导出**: **[导出 CSV]** / **[导出 JSON]** 按钮, 下载到浏览器.

> **筛选 hint**: 从 Results 的单条 run 点 "跨 session 聚合当前 schema" → 自动跳 Aggregate 并预填 filter. 这是 P17 之后的 one-shot hint 导航模式 (见 ARCHITECTURE_OVERVIEW §5.3).

---

## 6. Session 管理 — 保存 / 加载 / 自动保存 / 快照 / Rewind

Session 不是独立 workspace, 操作入口在**顶栏** (session chip + timeline chip) 和 **Settings → Autosave**.

### 6.1 操作入口一览

| 操作 | 入口 | 说明 |
|---|---|---|
| **新建会话** | 顶栏 session chip → [+] | 空白会话, 自动 autosave |
| **切换会话** | 顶栏 session chip → 下拉列表 | 未保存的草稿会警示 |
| **手动保存** | 顶栏 session chip → [保存] | 写 `sessions/<name>.json` |
| **加载已保存** | 顶栏 session chip → [加载] → 选文件 | 覆盖当前 session state |
| **自动保存** | Settings → Autosave → [启用] | 每 60s (默认) 非阻塞写 `autosave/` |
| **打快照** | Diagnostics → Snapshots → [打快照] | 即时拍照归档 |
| **Rewind** | Diagnostics → Snapshots → 选快照 → [Rewind] | **会先隐式打快照**再回档(不丢数据) |
| **导出** | 顶栏 session chip → [导出] | zip 包含 session + memory 四件套 |
| **导入** | 顶栏 session chip → [导入] | 从 zip 恢复 |

<!-- IMG: 11_session_chip_menu.png | 顶栏 session chip 展开菜单 | 600x500 | 高亮 [新建] [保存] [加载] [导出] [导入] -->

### 6.2 11 种 save/load/autosave 组合

| # | 场景 | 期望行为 |
|---|---|---|
| 1 | 新建 + 不保存 + 关浏览器 | 下次开看不到 (autosave 即使开了, 未落盘的也丢) |
| 2 | 新建 + autosave 开 + 关浏览器 | 下次开 autosave 恢复 |
| 3 | 新建 + 手动保存 + 关浏览器 | 下次开 session chip 看到, 可加载 |
| 4 | 编辑中 + 切会话 | 警示"有未保存改动, 丢弃?" |
| 5 | 编辑中 + 新建会话 | 同上警示 |
| 6 | 加载 + 立刻切回 | session state 切走再切回, autosave 最新版回来 |
| 7 | 加载 + 编辑 + 保存 | 覆盖旧文件 (atomic write, 不会丢) |
| 8 | 加载 + 编辑 + 另存为 | 写新文件, 旧文件不动 |
| 9 | Rewind 到旧快照 | **先隐式打一次"rewind 前"快照**再回档 (§6.3) |
| 10 | 导出 zip + 另一个 testbench 导入 | 应完全一致 (memory + session + 配置) |
| 11 | 同 session 多 tab 打开 | 仅**最早 tab**可写, 后开 tab 进只读警示 (BUSY lock) |

### 6.3 Rewind 的隐式快照保护

**用户痛点**: tester A 点 Rewind 想"看看 3 轮前的状态", 但忘了保存现场, 一点就丢掉**当前**改动.

**Testbench 防御**: 每次 Rewind **先隐式打一次快照** 命名 `before_rewind_<timestamp>`, 再执行回档. 所以:

- Rewind 不会真丢数据 — 隐式快照永远可以再 Rewind 回来.
- 频繁 Rewind 会堆快照 — 定期清一下 Diagnostics → Snapshots (超过 `snapshot_limit` 设置会自动 LRU 删).

### 6.4 Autosave 配置 (Settings → Autosave)

| 配置 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 新用户默认开, 减少"忘了保存"损失 |
| `interval_sec` | `60` | 60s 一次; 最低 10s, 最高 600s |
| `on_idle_only` | `false` | 开后仅在 tester 停止操作 10s+ 才写, 不打断正在打字 |
| `retention_count` | `10` | 保留最近 N 份 autosave, 超过 LRU 删 |

### 6.5 常见 session 问题

参见 §9.Q3-Q5.

---

## 7. Diagnostics 诊断 — Errors / Logs / Snapshots / Paths / Reset

Diagnostics 是**元操作**环境, 提供**事后**查看 / 归档 / 重置能力. 5 子页:

### 7.1 Errors 子页

最近错误队列 (from `errors_bus`, 由全局 http:error / sse:error / chat:error 等汇聚).

<!-- IMG: 12_diag_errors.png | Errors 子页 + 一条展开的 stack trace | 1920x800 | 高亮 filter + 单条 error 的 JSON detail 展开 -->

- **filter**: 按 category (http / sse / chat / memory / eval) / 严重度 筛.
- **单条展开**: 看 stack trace + 请求 payload + server-side exception_type.
- **[清空]**: 清内存里的错误队列 (不影响日志文件).
- **One-shot filter**: 其他 workspace (如 Chat 的 error toast) 点 "查看详情" 会自动跳 Diagnostics → Errors 并预填 filter.

### 7.2 Logs 子页

结构化日志 JSONL 查看器. Logs 分两类:

- **运行时日志** (`logs/runtime.jsonl`) — 每条有 `ts / level / event / detail`.
- **LLM 调用日志** (`logs/llm_calls.jsonl`) — 每条记录一次 chat/memory/judge LLM 的 wire + response + 耗时.

操作:

- 顶部 **search box**: 全文搜索 event 名 / detail JSON 子串. 支持 `event:chat_send` 这种字段筛.
- **follow mode**: 右上角 ☁ 图标 → 开启后新日志自动拉到视图底 (类似 `tail -f`).
- **[导出]**: 导出当前 filter 下的 JSONL.
- **源文件** 点一条日志 → 右栏显示完整 JSON + [在文件夹打开] (本地跳 Explorer / Finder).

### 7.3 Snapshots 子页

快照列表 + 管理:

- 快照分三种: `manual` (tester 主动打) / `before_rewind_*` (Rewind 前隐式) / `periodic_*` (配置了定期打快照的话).
- 每条快照显示 `时间 / 名字 / session_id / size / 消息数`.
- **[Rewind]** — 见 §6.3.
- **[差异对比]** — 选 2 个快照 diff JSON (哪些字段变了, 可视化 tree).
- **[导出]** — 单个快照导出 zip.

### 7.4 Paths 子页

显示当前 testbench 运行时**所有关键路径**, 方便 tester 去 Explorer 找文件:

```text
data_root         C:\Users\<user>\.testbench
sessions_dir      C:\Users\<user>\.testbench\sessions
autosave_dir      C:\Users\<user>\.testbench\autosave
snapshots_dir     C:\Users\<user>\.testbench\snapshots
logs_dir          C:\Users\<user>\.testbench\logs
current_session   C:\Users\<user>\.testbench\sessions\smoke_test_01.json
active_memory_dir C:\Users\<user>\.testbench\sessions\smoke_test_01\memory
```

每条右侧 **[复制]** / **[在文件夹打开]** 按钮.

### 7.5 Reset 子页 (⚠️ 危险区)

核选项, 不可撤销:

| 操作 | 删什么 | 保留什么 |
|---|---|---|
| **清当前会话的记忆** | `memory/*.json` 4 件套 | session.messages 保留 |
| **清当前会话全部** | 当前 session 所有文件 | 其他 session + 全局配置 |
| **清所有 autosave** | `autosave/` 全部 | `sessions/` 已保存的不动 |
| **清所有日志** | `logs/*.jsonl` | 其他 |
| **清所有快照** | `snapshots/` | 其他 |
| **工厂重置** (核按钮) | 整个 `data_root/` | 只保留 `api_keys.json` |

**双重确认**: 每个 reset 按钮都需要:

1. 点按钮 → 弹 modal.
2. 输入**当前会话名字**作为二次确认 (防误点).
3. 才执行.

---

## 8. Settings 设置 — Models / API_keys / Providers / Autosave / UI / About

### 8.1 Models 子页

按 **组** 配置:

| 组 | 用途 | 必填 |
|---|---|---|
| `chat` | Chat workspace 的主对话 LLM | 是 |
| `memory` | recent.compress / facts.extract / reflect 的 LLM | 用哪个 op 才必填 |
| `judge` | Evaluation 的评分 LLM | 用 Eval 才必填 |
| `simuser` | Chat composer 的 SimUser 模拟生成 | 用 simuser 模式才必填 |

每组字段:

- `provider` (下拉, 从 Providers 子页定义的列表选)
- `model` (文本, 如 `gpt-4o-mini` / `claude-3.5-sonnet`)
- `base_url` / `api_key` (从对应 provider 继承, 可本组覆盖)
- `temperature` / `max_tokens` / `top_p` / ...

每组底部 **[Test Connection]** — 发一个最小 chat completion, 验证配置通. **测试结果只打到 toast, 不写日志**.

### 8.2 API Keys 子页

全局 key 表:

| provider | key (mask) | 状态 |
|---|---|---|
| openai | `sk-...xxx` | ✅ 有效 (上次 Test 通过) |
| anthropic | `sk-ant-...yyy` | ⚠️ 未验证 |

- **[编辑]** → 明文显示, 改后保存. 保存到 `api_keys.json` (明文, 自己保管).
- **[批量 Test]** → 对所有 provider 发最小请求, 状态列批量更新.

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

新加 provider (e.g. azure / 本地 ollama) 在这里加.

### 8.4 Autosave 子页

见 §6.4.

### 8.5 UI 子页

| 配置 | 默认 | 说明 |
|---|---|---|
| **语言** | 跟随浏览器 | 6+ 种见 §2.4 |
| **主题** | 深色 | light / dark / auto (跟系统) |
| **Snapshot limit** | 50 | 超出 LRU 删老快照 |
| **Fold defaults** | `{"external_event": true, "memory_trigger": false, ...}` | 各折叠面板默认展开/折叠 |
| **显示 debug info** | `false` | 开后每个气泡右上角有小图标显示 message.id / source tag |

### 8.6 About 子页

<!-- IMG: 13_about_page.png | About 页显示版本 + 相关文档链接 | 1200x800 | 高亮 4 条文档链接 -->

- 显示 `TESTBENCH_VERSION` (semver) + `TESTBENCH_PHASE` (开发阶段, 测试员可忽略).
- **相关文档**: 4 条链接 (USER_MANUAL / ARCHITECTURE_OVERVIEW / external_events_guide / CHANGELOG), 点开在浏览器渲染 Markdown.
- **系统信息**: Python 版本 / OS / 启动时间 / uptime.
- **server_boot_id**: 用于前端判断"服务端重启过"的随机 ID, 每次 server 启动会变. 开发侧 feature, 测试员可忽略.

---

## 9. FAQ 与已知限制

### Q1: `/docs/<name>` 访问有时候返 404, 为什么?

testbench 的公开文档端点有 **双 404 语义**:

| 情况 | HTTP | body |
|---|---|---|
| 请求的 `<name>` 不在白名单 (`_PUBLIC_DOCS`) | 404 | `{"detail":"not_in_whitelist"}` |
| 在白名单, 但对应 md 文件**还没落盘** | 404 | `{"detail":"file_missing"}` |
| 在白名单且文件存在 | 200 | HTML 渲染 |

两种 404 语义区别: **前者** 是"这个文档根本不公开", **后者** 是"公开了但还没写". 后者常见于"文档还在路上" 阶段. 见 [`LESSONS_LEARNED.md`](LESSONS_LEARNED.md) L46.

### Q2: 端口可以改吗?

不可以. 端口固定 `48920`. 原因: 前端静态资源 hardcoded 了 `ws://localhost:48920/...`, 换端口要改一堆 import. 后续版本可能会抽到 config.

**被占用怎么办**: 先 `netstat -ano | findstr 48920` (Windows) / `lsof -i :48920` (Linux/macOS) 找占用进程, 杀掉再启.

### Q3: 关浏览器后会话丢了?

看 autosave 设置 (§6.4). 默认开, 最多丢最近 60s 改动. 完全防丢: 每次关前手动保存.

### Q4: `reason=chat_not_configured` 但我配了?

几个常见坑:

1. 配了但没点 **[Test Connection]** 通过. Test 通过才算"有效配置".
2. 配的是 memory / judge 组, **chat 组没配**.
3. api_key 对但 `base_url` 填错 (e.g. 漏了 `/v1`).

### Q5: 同一 session 在两个浏览器标签页同时编辑?

**只有最早打开的那个标签页可写**, 后开的标签页会看到 `SessionConflict` 警示 + `busy_op` 字段告知谁在占用. 刷一下最早那个 tab 或关掉能释放 lock.

### Q6: 顶栏 Stage chip 有时候自己变了?

**不会**. Stage 切换**永远**由测试员显式点按钮. 看到 Stage 无故跳了, 是:

- (最可能) 你在另一个 tab 点了 stage 按钮, 本 tab 同步过来.
- 真出现**一个 tab 内自己跳**的情况, 请报告 — 这违反 P14 契约.

### Q7: LLM 调用扣费了但没看到回复?

1. 查 Diagnostics → Logs → `llm_calls.jsonl` → 搜刚才时间. 看 response 字段.
2. 若 response 里有内容但 UI 没显示 → 前端 render bug, 报告.
3. 若 response 空或 error → 看 `error_detail`, 常见: timeout / context_length_exceeded / rate_limit.

### Q8: Prompt injection 把我合法的测试输入当攻击了?

见 `external_events_guide.md` Q5. 简短版: 这是 positive signal (主程序的检测太严了), 事件仍会触发, 不阻塞测试, 但请记录下来.

### Q9: 为什么 Diagnostics 没有 Security 子页?

之前 PLAN 里写过要加, **最终没加**. 原因: Security (prompt injection 检测 / API key mask / path traversal 防御) 是**横切关注**, 分散在各处 op 里, 没有独立 "安全仪表盘" 需求. 相关告警在 Errors 子页以 category=security 出现.

### 已知限制

1. **大 session 性能**: `session.messages` 超过 ~5000 条时 Chat 滚动会卡, 建议定期归档.
2. **Memory LLM 成本**: recent.compress / facts.extract / reflect 每次 op 调用全量 context, 大 recent 很烧 token. 用 **[预览 prompt]** 先看成本.
3. **不支持多用户**: testbench 是**单人 localhost 工具**, 没有权限系统. 别暴露到公网.
4. **浏览器兼容**: Chrome / Edge 测过. Firefox 大致能用. Safari 有零星 CSS bug.

---

## 10. 扩展点 (给深度用户)

### 10.1 Script 文件格式

Setup → Scripts 支持从 JSON 文件批量导入预置对话:

```json
{
  "id": "greeting_test",
  "name": "问候场景 × 10 变体",
  "entries": [
    { "text": "你好", "notes": "标准问候" },
    { "text": "早上好呀", "notes": "时段问候" },
    { "text": "喂,在吗?", "notes": "寻求注意" }
  ]
}
```

Chat composer mode 切到 `script` 后可从下拉选条目直接填 textarea.

### 10.2 Eval Schema 共享

Schemas 支持 JSON 导入导出, 团队协作时:

1. Tester A 在 Schemas 子页 **[新建]** 一个维度定义.
2. **[导出 JSON]** → 发给 Tester B.
3. Tester B **[从 JSON 导入]** → 一模一样的 schema, 可以比对评分一致性.

### 10.3 API 直连 (curl)

关键端点, 详见 ARCHITECTURE_OVERVIEW §4:

```bash
# 版本
curl http://localhost:48920/api/version

# 创建会话
curl -X POST http://localhost:48920/api/session \
  -H "Content-Type: application/json" \
  -d '{"name":"my_session"}'

# 发消息
curl -X POST http://localhost:48920/api/chat/send \
  -H "Content-Type: application/json" \
  -d '{"text":"你好"}'

# 触发记忆 op
curl -X POST http://localhost:48920/api/memory/compress-recent

# 触发外部事件 (详见 external_events_guide.md §9)
curl -X POST http://localhost:48920/api/session/external-event \
  -H "Content-Type: application/json" \
  -d '{"kind":"avatar","payload":{...}}'
```

### 10.4 本手册的上游知识

本手册是**操作手册**, 不含架构 / 设计理由. 深入了解推荐顺序:

1. 本手册 (USER_MANUAL) — 你现在在读.
2. [`external_events_guide.md`](external_events_guide.md) — 外部事件专项, 250 行.
3. [`testbench_ARCHITECTURE_OVERVIEW.md`](testbench_ARCHITECTURE_OVERVIEW.md) — 架构总览, 给开发者 / 深度用户.
4. [`LESSONS_LEARNED.md`](LESSONS_LEARNED.md) — 设计教训, 给 AI agent / 跨项目参考.
5. [`CHANGELOG.md`](CHANGELOG.md) — 每版变更记录.
6. [`PROGRESS.md`](PROGRESS.md) — 逐 commit 开发日志 (最细粒度).

---

## 汇报问题时请附带

1. **版本**: Settings → About 页截图 (含 `TESTBENCH_VERSION` + `TESTBENCH_PHASE`).
2. **复现步骤**: 操作顺序 (从"启动 → 新建会话 → ...").
3. **相关日志条目**: Diagnostics → Logs → 搜关键 event → 右栏 JSON detail 导出.
4. **错误 toast 截图** (如有).
5. **session 导出 zip** (Settings → Autosave → [导出当前会话]), 如果问题和数据状态相关.

> 把这五样打包发给开发者基本能直接定位问题. 主程序 (桌宠) 的行为问题也在 testbench 复现时同样适用.

---

*手册版本对齐 `TESTBENCH_VERSION` (见 Settings → About). 如有错漏, 请在 Diagnostics → Errors → [报告问题] 提交.*
