# N.E.K.O. Testbench 更新记录 (CHANGELOG)

本文档只记录**对测试用户可见**的版本变更. 内部的重构 / 文档调整 / smoke
结构变化不在本记录范围内, 详见 `PROGRESS.md` 与 `AGENT_NOTES.md`.

---

## 版本号约定

- **MAJOR** bumps (X.y.z) — 某个 Phase 签收, 且带来**外部可见契约变更**
  (沙盒目录结构 / 导出格式 / 持久化 schema / HTTP 端点重命名等).
- **MINOR** bumps (x.Y.z) — 新增特性, 但不破坏现有测试者的使用流程
  (新面板, 新按钮, 新 Op, 新诊断页面).
- **PATCH** bumps (x.y.Z) — 纯 bugfix / UI 打磨, 不新增功能.

目前版本号统一由 `tests/testbench/config.py` 里的 `TESTBENCH_VERSION`
常量控制, `/version` 端点和 Settings → About 页都从此读取.

---

## v1.1.0 (2026-04-23) — P25 外部事件注入

### 新增功能

- **外部事件注入模拟面板** (Chat workspace 左侧栏, 默认折叠)
  模拟主程序运行时会把外部事件注入到 prompt 的行为, 给测试人员提供
  端到端闭环测试手段. 包含 3 个 tab:
  - **Avatar 道具交互** — 模拟虚拟主播形象上的道具 / 动作 /
    触碰事件 (部位 / 动作 / 强度 / 文本上下文 / 附带奖励).
  - **Agent 回调后台任务** — 模拟后台计划任务 / 异步任务完成后
    回调主程序时投递的事件 (业务类型 / 结果 payload / reason_code).
  - **Proactive 主动搭话** — 模拟 AI 在空闲时间 "自己开口" 的场景
    (触发类型 / 主动对话话题 / 对话历史自动补全).

  每个事件的完整字段说明见 `external_events_guide.md`.

- **Prompt 预览按钮** (不调 LLM, 可反复点)
  - **Memory workspace** — 每个 memory op 的参数 drawer 内, 紧邻
    `[执行 (Dry-run)]` 按钮的 `[预览 prompt]`. 让测试人员先看到
    即将发送给 LLM 的完整 wire, 确认参数无误后再花 token 真的跑.
  - **Evaluation workspace** — Run 页面的 `[运行评分]` 按钮旁
    `[预览 prompt]`. 每条待评对象的 judge prompt 都会列出来.

- **[保存到最近对话] 快捷键** (Chat workspace 底部工具栏)
  一键把当前 Chat session 的对话内容写入 `memory/recent.json` (LangChain
  canonical 格式), 省去"要先结束会话再手动复制粘贴"的繁琐.

- **非对话事件的系统提示气泡** (Chat UI 消息流内联)
  对于那些**不产生 assistant 回复**但又真实发生过的操作 (例如 Agent
  Callback), Chat 消息流内会有一条 `XX 分钟后 · 测试用户发送了一次
  XXX 事件` 的 system 气泡, 让测试人员看到"事件真的发生过但没回复".

- **Tester 文档**
  - `external_events_guide.md` — 外部事件详细字段表 + 常见问答.
  - `testbench_USER_MANUAL.md` — 面向测试人员的中文使用手册
    (v1.1 发布 · 待配图).
  - `CHANGELOG.md` (本文件) — 版本更新记录.

### 改进 (UI / UX)

- **对话区 Prompt Preview 面板按消费域分区** — Chat 右侧 Prompt
  Preview 现在只显示 "对话 AI" 的 wire, 而记忆 / 评分 / 假想用户的
  wire 会在各自页面的按钮旁边看. 消除 "测试记忆时发现对话 preview
  也跟着变了" 的跨域污染.

- **外部事件日志中文化** — Log 页面的 `external_event.*` 条目全部
  有中文说明 (例如 `session.create` → "会话新建", `external_event
  avatar.interact` → "Avatar 道具交互"), 不再只有英文 op_id.

- **错误徽章时区显示修复** — 错误列表 / 日志页面的时间从 UTC 改为
  用户本地时区 (之前下午 1:37 显示成 05:37).

- **Avatar 道具选项布局修正** — 部位 / 动作 / 强度 / 附带奖励
  等下拉框不再横跨整个容器, 改为 label 和 select 横向并列.

- **Prompt Preview modal 宽度 + 自动换行** — Modal 加宽到
  1100px (大屏), 长文本自动换行, 不再从右边缘溢出.

- **空消息发送的软警告** — 空白或只含空格的消息点发送, 改为 toast
  警告 + 日志记录 `chat_send_empty_ignored`, 不再硬报
  `InvalidSendState`.

### 修复

- **`role=system` 导致 AI 回复空** — Chat UI 的 "系统" 角色消息
  会被主程序 prompt 契约拒收 (只接受 initial system_prompt + runtime
  role=user). 现在 `pipeline/prompt_builder::build_prompt_bundle` 统一
  重写 runtime role=system 为 role=user + `[system note]` 前缀,
  并打 info 日志 `CHAT_SEND_SYSTEM_REWRITTEN` 透明化.

- **`mirror_to_recent` shape 不匹配** — 之前写入 `memory/recent.json`
  的形状和主程序 canonical LangChain 格式不一致, 会导致主程序启动时
  读不回来. 已对齐.

- **外部事件触发后对话区不自动刷新** — 已接上 `session:change`
  事件总线, 事件成功后 Chat 会自动拉取最新 session.messages 重绘.

- **Avatar 道具的 text_context / reward 字段不进 instruction** — 已
  修复并增加静态 smoke 保证字段能被注入检测扫到.

- **Memory op 参数 drawer 内可反复预览 prompt** — 点击 `[预览 prompt]`
  不会清空已填参数, 也不关闭 drawer, 让"预览 → 微调参数 → 再预览"
  流畅循环.

### 兼容性

- 持久化 schema 未变 — v1.0 保存的会话可以在 v1.1 下原样加载.
- 导出格式未变 — v1.0 导出的报告可以被 v1.1 再导入重跑.
- 默认 HTTP 端口未变 (`127.0.0.1:48920`).
- `tests/testbench_data/` 运行时数据目录结构未变.

---

## v1.1.0 hotfix (2026-04-24) — 文档渲染 / 手册事实对齐 / 图片 pipeline

v1.1.0 发布当日的用户手测反馈收治, 仍属 v1.1 同一版本号 (未 bump) 的维护性
更新, 仅对 `/docs` 端点行为 / UI 文案 / 内置手册内容做 tester-visible 修正.

### 修复

- **`/docs/<name>` 渲染的 markdown 链接跳转** — 之前:
  - 内部 `[§X.Y](#xxx)` 点击无反应 (heading 没 `id` 属性).
  - 跨文档 `[arch](testbench_ARCHITECTURE_OVERVIEW.md)` 报 404
    `unknown_doc` (白名单 key 无 `.md` 后缀, 浏览器把 `.md` 原样带进 URL).
  - 现在 heading 自动生成 GitHub 风 slug id (保留 CJK 字符, 标点 drop,
    空白 → hyphen); 白名单文档的 `.md` 后缀自动剥; 指向内部开发文档
    (LESSONS_LEARNED / PROGRESS / AGENT_NOTES / PLAN / P*_BLUEPRINT)
    的链接降级为灰色 dotted 不可点提示.

- **测试用户使用手册大面积与实际 UI 不符** — 手册内容 4 轮手测后深度
  对齐实装代码, 典型修正点:
  - 启动命令 `python -m ...` → **`uv run python -m tests.testbench.server`**.
  - 数据目录 `~/.testbench` → **`tests/testbench_data/`** (项目内相对路径).
  - 删掉 "Welcome Banner 首次打开引导" 描述 (当前 UI 无该组件).
  - Setup 子页 5 个 → **8 个** (persona / chat_import / memory_import /
    scripts / recent / facts / reflections / persona_memory).
  - Stage 7 id → **6 id**. Composer 3 模式 → **4 模式** (含 system).
  - Evaluation Run **启动后不可暂停/停止** (之前手册误写 "可暂停",
    实际只有 Auto-Dialog 能暂停/停止).
  - Settings → UI 的 "Language" + "Theme" select 标注为**当前版本
    未实装** (disabled 占位符).
  - 反馈渠道改为 "截图当前子页 + `tests/testbench_data/` 下相关 json
    发给开发者" (本地环境无在线反馈按钮).
  - 清理手册中所有 `P19 之后可能微调` / `P25 之后独立立项` / `详见蓝图`
    等内部开发术语 — tester 不应读到项目的内部 phase 编号.

- **手册 13 张配图现在真的能看到** — 用户手动截屏放入
  `tests/testbench/docs/images/` 后, 手册从 HTML 注释占位
  `<!-- IMG: ... -->` 替换为标准 markdown `![描述](images/01_xxx.png)`.
  新增 `/docs/images/{filename}` 端点 (basename + 扩展名白名单 +
  路径边界校验), 响应式 CSS `max-width: 100%; height: auto` 防溢出,
  点击图片弹出 lightbox 看原图.

- **ARCHITECTURE_OVERVIEW 部分宽表格右侧溢出** — 表格改 `display: block;
  overflow-x: auto`, 长单元格改 `word-break: break-word`, 双保险.

- **ARCHITECTURE_OVERVIEW 二审事实偏差** (开发者向但同样是可见内容):
  - Logs 子页描述 "实时 tail" → "5 秒轮询 auto-refresh + 无自动滚动".
  - Paths 子页描述补 "列孤儿(沙盒外)高亮 + 每行可复制".
  - i18n 描述 "多语言 zh-CN/en/ja/ko/es/pt" → "当前仅 zh-CN 实装,
    其它 locale 静默回退" (主程序才有 es/pt 英文回退契约).

### 改进

- **Settings → About 页** — "当前阶段" 字段去掉 (内部术语), 替换为
  "最后更新日期: 2026-04-24". 新增 `TESTBENCH_LAST_UPDATED` 常量维护.

- **右上角三点菜单的 "关于" 按钮** — 之前 hidden, 现在常显, 点击
  直接跳到 Settings → About 页, 不再需要测试员记路径.

- **全仓 UI 文案清理内部 phase/蓝图术语** — 外部事件面板 "详见 P25
  蓝图" → "详见外部事件使用手册"; 评分 Run 页 "P16 暂不支持" →
  "当前版本 UI 暂不支持, 请直接调 API"; 记忆页同类 2-3 处.

### 兼容性

- 持久化 / 导出 / HTTP 端口 / 数据目录结构, 全部未动.
- CHANGELOG 版本号仍为 v1.1.0, 本次 hotfix **不 bump 版本** (纯 tester
  可见文档/文案修复, 无新功能 / 无 schema 变更).
- 所有 `/docs/{name}` 端点 URL 未变, 响应 Content-Type 协商规则未变
  (`text/markdown` vs HTML).
- **post-push 文档整理期 (2026-04-24 晚)**: 完成 `LESSONS_LEARNED §7.28 / §7.29`
  升格 (L50 Server boot_id / L51 文档作者先 grep + 多轮 tester 手测回写), 以及
  `AGENT_NOTES §4.27 #121` 记账. 纯内部开发经验沉淀, 对测试用户无可见变化.
- **post-upgrade 机制固化补刀 (2026-04-24 晚, `AGENT_NOTES §4.27 #122`)**: 把
  L51 升格后的反漂移经验分三线沉淀 — 抽出 `~/.cursor/skills/docs-code-reality-grep-before-draft`
  跨项目 skill (四层防御 how-to) + 新增 `.cursor/rules/lessons-candidate-promote-on-threshold.mdc`
  / `lessons-main-entry-requires-skill.mdc` 两条 project rule (候选写入即判决升格 +
  主条目必映射 skill) + `p26_docs_endpoint_smoke.py` 新加 D14 契约锁 USER_MANUAL
  7 条高价值 tester-fact (workspace/子页/memory op 数 + DATA_DIR 路径). 全量
  18/18 smoke 仍全绿 (p26_docs_endpoint_smoke 内部 D1-D14 14 契约). 纯工具链
  / 元文档增强, 对测试用户无可见变化.

---

## v1.0.0 (2026-04-22) — 第一个完善版本 (P24 sign-off)

首个对外可用的稳定版本. 所有基线能力都已冻结:

### 基线能力

- **会话与沙盒** — 单活跃会话 · 每会话一个隔离沙盒 · 主程序 config
  manager 单例约束下的强制切换.
- **虚拟时钟** — 可手动拖拽时间游标 · 时间游标影响所有 "XXX ago"
  相对时间渲染 · 不影响真实墙钟.
- **Chat 对话四模式** — 手动单发 · 假想用户自动续写 · 脚本化回放
  · 双 AI 自动对话.
- **三层记忆** (Recent / Facts / Reflections / Persona) + 5 个
  手动 Op (压缩最旧消息 / 从对话抽取事实 / 合成反思 / 修正 persona
  事实 / 加 persona 事实), 全部两阶段 (Preview / Commit).
- **Stage Coach 6 阶段引导** — suggest → advance 状态机, 帮测试
  人员从 "创建会话" 到 "开测" 的最短路径.
- **Evaluation 四类 Judger** — Pairwise 比较 · Head-to-head 对决
  · Single-turn 单条打分 · Full-session 全轮评分, 带 ScoringSchema
  + 内置 3 套 + 自定义上传.
- **保存 / 加载 / 自动保存 / 断点续跑** — 手动保存 · 滚动自动保存
  · 崩溃后启动时恢复上次会话.
- **导出** — 4 种 scope × 3 种格式 (JSON / Markdown / CSV) =
  11 组合. 导出时 `api_key` 自动脱敏.
- **Diagnostics 六子页** — Errors / Logs / Paths / Snapshots /
  Reset / Safety Audit.
- **快照与回退** — 可对任意 commit 打快照 · 编辑消息 · Re-run
  · Rewind.

### v1.0 基线的 smoke baseline

- 17/17 smoke 全绿
- 总耗时 ~34.59s (Windows cmd + venv python 下)
- 零跨模块死引用 · 零 shape drift · 零未处理 task warning

---

## 问题反馈

- 开发阶段产出的所有文档都在 `tests/testbench/docs/` 下.
- 测试人员入门手册: [`testbench_USER_MANUAL.md`](./testbench_USER_MANUAL.md).
- 外部事件注入详细字段表: [`external_events_guide.md`](./external_events_guide.md).
- 代码架构与设计原则: [`testbench_ARCHITECTURE_OVERVIEW.md`](./testbench_ARCHITECTURE_OVERVIEW.md).
