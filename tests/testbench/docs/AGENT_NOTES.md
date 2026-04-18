# AGENT_NOTES — 给未来 Agent 的恢复指南

> **本项目是 N.E.K.O. Testbench Web UI 的独立实施工程。**
> 如果你是刚介入这个项目的 Agent, 请先读本文档、再读 [PROGRESS.md](./PROGRESS.md), 最后按需查阅 [PLAN.md](./PLAN.md)。

---

## 1. 快速定位当前断点 (最重要)

1. 打开 [PROGRESS.md](./PROGRESS.md)
2. 从上往下找第一个状态为 `in_progress` 的条目 → 那就是**上次被打断的阶段**
3. 若没有 `in_progress`, 找第一个 `pending` → 那就是**下一阶段起点**
4. 若有 `blocked` 条目, 先读"遗留"字段看原因, 决定是否绕过或用户决策
5. 展开该条目的"子任务" checklist, 打勾的已完成, 未打勾的继续做

**对应的 `.cursor/plans/n.e.k.o._testbench_web_ui_787681c0.plan.md` 也应维护同样的状态**, 因为 todos 字段被 Cursor 用于渲染任务列表。两处需保持同步。

---

## 2. 环境检查清单 (每次会话开始都先过一遍)

- [ ] 当前工作目录是 `E:\NEKO\NEKO dev\project`
- [ ] Python 使用 `uv run`, 不是系统 Python
- [ ] `tests/testbench/` 目录存在 (代码目录, 入库 git)
- [ ] `tests/testbench_data/` 目录首次启动时会自动创建 (不入库)
- [ ] `.gitignore` 包含 `tests/testbench_data/`
- [ ] `tests/api_keys.json` 已存在 (可能有测试 API key)

如果 testbench 服务在别的会话里还在跑, 需要先 kill 再重启 (常见: 端口 48920 被占用)。

---

## 3. 关键决策摘要 (理解代码的底层逻辑)

### 3.1 代码与数据严格分离
- `tests/testbench/` = **代码 + 内置预设** (`scoring_schemas/builtin_*.json`, `dialog_templates/sample_*.json`), 入库 git
- `tests/testbench_data/` = **所有运行时数据**, 整体 gitignore
  - `sandboxes/<session_id>/` 会话沙盒 (memory, 配置)
  - `logs/` 会话 JSONL 日志
  - `saved_sessions/` + `_autosave/` 存档
  - `scoring_schemas/` 用户自定义 schema (合并 builtin 加载)
  - `dialog_templates/` 用户自定义模板
  - `exports/` 手动导出

### 3.2 单活跃会话 (ConfigManager 单例限制)
- `utils/config_manager.py` 是全局单例; 为在沙盒和真实目录间切换, 需用 `sandbox.py` 替换 `cm.docs_dir / app_docs_dir / memory_dir / ...` 等字段
- **单活跃会话**: 同时只能有一个沙盒激活; 切换会话前优雅关闭旧会话
- 用 `asyncio.Lock` + 状态枚举 (`idle / busy:<op> / loading / saving / rewinding / resetting`) 保护切换过程
- 参考 [tests/conftest.py](../../conftest.py) `clean_user_data_dir` fixture 的补丁模式

### 3.3 Prompt: structured vs wire
- **Structured dict** 是给 UI 看的拆分 (`session_init / character_prompt / persona / inner_thoughts / recent_history / time_context / holiday / closing`), **AI 模型永远看不到**
- **Wire messages** 是真正发给模型的 `[{role, content}, ...]`, 其中 system 消息是上述分区**首尾相接的一整段扁平字符串**
- `pipeline/prompt_builder.py` 的 `PromptBundle` 同时产出两者, `chat_runner / judge_runner / simulated_user` 只消费 `wire_messages`
- 参考 [tests/dump_llm_input.py](../../dump_llm_input.py) 和 [tests/unit/run_prompt_test_eval.py:154-182](../../unit/run_prompt_test_eval.py) 的装配逻辑

### 3.4 虚拟时钟是滚动游标 (不是静态值)
- `VirtualClock.cursor` 随对话前进
- `bootstrap` (首条消息前的会话起点) vs `live cursor` (当前游标) vs `per_turn_default` (默认每轮推进) 三概念分开
- `stage_next_turn(delta=... or absolute=...)` 声明下一轮时间, `consume_pending()` 在 /chat/send 开头消费
- 消息 `timestamp` 是**权威时间记录**, gap 计算优先 `messages[-1].timestamp`, 退化到 TimeIndexedMemory.get_last_conversation_time, 再退化到 bootstrap

### 3.5 目标模型无状态 (ChatCompletion)
- 每次 `/chat/send` 都从 `session.messages` 重新拼 wire_messages; 编辑历史立刻生效, 无端上残留上下文
- `Re-run from here` = 截断 + 重发, 时钟回退到该消息 timestamp
- 如未来加 Realtime 支持, `ChatBackend` 接口需实现 `reset()` / `replay()`; 当前 `OfflineChatBackend` 两者均为 no-op

### 3.6 评分 ScoringSchema 一等公民
- `pipeline/scoring_schema.py` 的 `ScoringSchema` 含 dimensions + anchors + formula + prompt_template + version
- 三套内置预设 JSON 复刻自现有 [tests/utils/human_like_judger.py](../../utils/human_like_judger.py) 与 [tests/utils/prompt_test_judger.py](../../utils/prompt_test_judger.py) 的常量
- 四类 judger (AbsoluteSingle/AbsoluteConversation/ComparativeSingle/ComparativeConversation) 全部 schema-driven
- EvalResult 内嵌 `schema_snapshot`, schema 改了不影响历史结果重现

### 3.7 折叠规范
- 所有长内容用 `CollapsibleBlock` (在 `static/core/collapsible.js`) 包裹
- localStorage key = `fold:<session_id>:<block_id>`; 会话切换不互相污染
- Settings → UI 可调各类内容的默认折叠策略

### 3.8 多客户端并发约束 (README 声明)
- 同一测试人员建议只开一个浏览器标签; 多标签可能互相踩状态
- testbench 默认绑 127.0.0.1, 不监听公网

---

## 4. 常见陷阱

### 4.1 不要用 `datetime.now()` 直接装 prompt
务必走 `virtual_clock.now()` / `virtual_clock.gap_to(...)`。`pipeline/prompt_builder.py` 是唯一合法入口。

### 4.2 不要把 `structured` dict 发给 LLM
只能发 `wire_messages`。违反此规则 = 测试结果不可信。

### 4.3 不要同步调用长耗时操作
记忆 LLM / judge LLM / 目标 AI 都可能耗时数秒到数十秒, 全走 async + `asyncio.to_thread` 包裹 sync 库调用。

### 4.4 ConfigManager 属性替换不可遗漏
`cm.docs_dir` 改了, 别忘了 `cm.app_docs_dir / config_dir / memory_dir / chara_dir / live2d_dir / vrm_dir / ...`。参考 [conftest.py](../../conftest.py) `clean_user_data_dir` 的完整补丁列表。

### 4.5 CollapsibleBlock 的 localStorage 清理
会话删除后 stale key 不自动清理; 暂时接受, 后期可加定期 cleanup。

### 4.6 api_key 默认脱敏
`persistence.py` 导出/保存时, `model_config` 中的 api_key 默认替换为 `"<redacted>"`; Save 对话框 checkbox 勾选才包含明文。

### 4.7 uvicorn 绑定
默认 `127.0.0.1`, 不要误改成 `0.0.0.0`。

### 4.8 `config` 命名冲突 (已修复, 不要回滚!)

`tests/testbench/config.py` 与项目根 `config/` 包同名. 如果直接执行
`python tests/testbench/run_testbench.py`, Python 会把 `tests/testbench/` 注入
`sys.path[0]`, 然后任何地方的 `from config import X` 都会解析到 testbench
的 `config.py` 而不是项目根的 `config/` 包, 导致 `utils.config_manager` 等
依赖 `from config import APP_NAME` 的模块炸掉.

**防御**:
1. `run_testbench.py` 启动时会把 `tests/testbench/` 从 `sys.path` 过滤掉
2. testbench 内部模块避免直接 `from config import ...`; 通过 `get_config_manager()` 或其他间接方式取
3. 如果未来必须在 testbench 代码中引用根 `config` 包, 先确认 sys.path 已清理, 并在 import 失败时给清晰错误

### 4.9 `_characters_cache` 现在有锁 (上游 2026-04 新增)

`utils/config_manager.py` 的 `_characters_cache / _mtime / _path / _dirty` 读写已统一走 `self._characters_cache_lock: threading.Lock()`. testbench 的 `sandbox.apply/restore` 目前直接赋值这 4 个字段 (**没拿锁**), 依赖"沙盒切换窗口内不会有别的线程调 `load_characters_config`"这个事实.

- 单会话 asyncio 下成立 → 目前无 bug
- 如果未来引入后台线程 (例如 P18 快照异步导出里意外触发 `load_characters_config`), 需要在 sandbox 里包 `with cm._characters_cache_lock: ...`
- 如果上游哪天把这 4 个字段改成必须通过 method 访问, `_PATCHED_ATTRS` 策略会失效, 需要重写 sandbox

### 4.11 临时 Errors 面板 (P04 夹带, P19 替换)

当前 `static/ui/workspace_diagnostics.js` 是**简化临时版**, **不是** PLAN 中 P19 的完整交付:

- 只渲染 Errors 列表 (折叠条目 + JSON detail), 没有 Logs / Snapshots / Paths / Reset 四子页
- 没有后端 JSONL 日志拉取, 只有前端运行时错误
- 错误收集层 `static/core/errors_bus.js` 是面向 P19 设计的, **P19 实施时不要重写它**, 直接让正式 Errors 子页继续订阅 `errors:change` 即可
- P19 正式实施时的动作:
  1. 拆分 `workspace_diagnostics.js` 为左 subnav + 5 子页 (参考 Settings 的 `ui/settings/page_*.js` 组织方式)
  2. 实现 `static/ui/diagnostics/page_errors.js` 替换当前视图 (复用 `errors_bus.js`, 追加"按来源过滤"等高级功能)
  3. 新增 Logs 子页 + 后端 `logger.py` 暴露的 tail/filter 端点
  4. 全局 FastAPI 异常中间件把后端异常也 push 到前端 (HTTP 响应 + 前端 api.js 已经通过 `http:error` 转发到 errors_bus)
- PLAN.md P19 条目末尾已留迁移说明, PROGRESS.md P04 条目的"夹带"段列出了所有涉及文件

**不要**把这个临时面板当作 P19 已部分完成. P19 状态仍是 `pending`.

### 4.10 mermaid 图语法
如果修改 PLAN.md 里的 mermaid 图, 注意:
- subgraph id 不能有空格
- 节点 label 里的特殊字符 (括号/冒号) 要加双引号
- 不要在 subgraph 内部使用 subgraph id 作为边端点
- 不要用 HTML 实体 (`&lt;` 等)
- `<br/>` 在 label 里合法, 用于换行

---

## 5. 每阶段完成的操作流程

完成一个阶段的 **所有子任务** + **启动自测** 后, 严格按顺序执行:

1. **自检**:
   - `ReadLints` 检查新增/修改文件无 lint 错误
   - 启动 `uv run python tests/testbench/run_testbench.py --port 48920`, 访问 UI, 肉眼验证该阶段产物
   - 不同阶段的自测点见 PROGRESS.md 条目"预期产物"

2. **更新 PROGRESS.md**:
   - 把当前阶段状态从 `in_progress` 改为 `done`
   - 填完成时间戳 `(YYYY-MM-DD HH:MM)`
   - 如有遗留问题, 记入"遗留"字段
   - 把下一阶段状态改为 `in_progress` 并填开始时间戳

3. **同步 PLAN.md 与 `.cursor/plans/*.plan.md`**:
   - 如本阶段引出新决策或调整, 同步更新两处对应章节
   - 若只是实施细节, 只更新 PROGRESS.md 即可

4. **commit**:
   - 只在用户明确要求时才 `git commit`
   - 不要主动提交

5. **切下一阶段**

---

## 6. 中断/受阻/需求变更场景

| 场景 | 处理 |
|---|---|
| 网络断/会话崩溃 | 新会话读本文档 → PROGRESS.md → 定位 in_progress → 按子任务继续 |
| 用户加新需求 | 切 plan 模式 → 与用户讨论 → 双写 PLAN.md + `.cursor/plans/*.plan.md` → 更新 todos → 切回 agent 继续 |
| 阶段 blocked | PROGRESS.md 状态改 blocked + 原因 + 已尝试方案 → 告知用户 → 等决策 |
| 用户要回退一个阶段 | PROGRESS.md 改回 pending 或 in_progress → 对应代码 revert (或告知用户手动 revert) |

---

## 7. 关键外部依赖 (复用现有代码)

| 依赖 | 用途 |
|---|---|
| [utils/config_manager.py](../../../utils/config_manager.py) | 全局配置单例, 沙盒 patch 目标 |
| [utils/llm_client.py](../../../utils/llm_client.py) | `create_chat_llm` / `ChatOpenAI` / 消息类 |
| [utils/file_utils.py](../../../utils/file_utils.py) | 原子写 `atomic_write_json` / `atomic_write_json_async` |
| [memory/*.py](../../../memory/) | 三层记忆 (Facts/Reflections/Persona) + TimeIndexedMemory + CompressedRecentHistoryManager |
| [config/prompts_*.py](../../../config/) | 角色/记忆/系统提示词模板 |
| [tests/dump_llm_input.py](../../dump_llm_input.py) | 结构化 prompt 装配逻辑 (会复用 `build_memory_context_structured` 等) |
| [tests/utils/llm_judger.py](../../utils/llm_judger.py) | LLM 调用/重试/JSON 解析骨架 |
| [tests/utils/prompt_test_judger.py](../../utils/prompt_test_judger.py) | 单条评分常量, 用于 `builtin_prompt_test.json` |
| [tests/utils/human_like_judger.py](../../utils/human_like_judger.py) | 整段评分常量, 用于 `builtin_human_like.json` |
| [tests/conftest.py](../../conftest.py) | `clean_user_data_dir` fixture 的 ConfigManager 补丁模式可直接借鉴 |

---

## 8. 启动命令

```bash
uv run python tests/testbench/run_testbench.py --port 48920
# 访问 http://127.0.0.1:48920
```

---

## 9. 变更日志

- **2026-04-17** 初始版本, 完成 P00.
- **2026-04-18** 完成 P02. 新增 §4.8 `config` 命名冲突说明. P02 产物: `virtual_clock.py` / `sandbox.py` / `session_store.py` / `routers/session_router.py`; 端到端自测 `POST/GET/DELETE /api/session` + 沙盒目录创建/销毁全部通过.
- **2026-04-18** 完成 P03. 前端骨架 (原生 ES modules, 无构建): `static/{testbench.css, app.js, core/{i18n,state,api,toast,collapsible}.js, ui/{topbar,workspace_placeholder,workspace_{setup,chat,evaluation,diagnostics,settings}}.js}` + `templates/index.html` 重构为三段 grid. 顶栏 Session dropdown 已接入 `/api/session`; Stage/Timeline/Menu 未实装项显式 toast 提示下一 phase. 所有 15 个静态资源 HTTP 200.
- **2026-04-18** 合并上游 `NEKO-dev/main` 12 个 commit. `utils/config_manager.py` 有 48 行变更 (qwen_intl/minimax fallback, assistApiKey 回退, 部分 perf 优化), sandbox 依赖的 14 个路径属性 + 4 个 characters cache 字段全部保留. 新增 §4.9 记录 `_characters_cache_lock` 的隐式约束.
- **2026-04-18** 完成 P04 Settings workspace. 新增 `model_config.py` (Pydantic ModelGroupConfig/Bundle) / `api_keys_registry.py` / `routers/config_router.py` (7 端点) / `static/ui/settings/*` (5 子页). 关键决策: api_key 在 HTTP 响应永远 masked, 前端 draft 每次重新渲染从空白开始; test_connection 走 `ChatOpenAI.ainvoke` + 捕获所有异常→结构化, 锁粒度 `session_operation(BUSY)` 与 chat/send 同. "两栏 workspace" CSS 做成通用 (Setup/Diagnostics 后续复用).
- **2026-04-18** P04 夹带 (side-quest): 临时前端错误总线 + 诊断 Errors 面板. 新增 `static/core/errors_bus.js` 统一收 `http:error` / `sse:error` / `window.error` / `unhandledrejection` → `store.errors` (ring buffer cap=100) + 广播 `errors:change`; `static/ui/topbar.js` Err 徽章改为纯 `errors:change` 订阅, 点击直跳 Diagnostics; `static/ui/workspace_diagnostics.js` 从 placeholder 升级为**临时** Errors 面板 (工具栏 + 可折叠条目 + 完整 JSON detail). **注: 这是 P19 之前的调试辅助, 不是 P19 的部分交付**; PLAN/PROGRESS 里 P19 状态仍为 pending, 本临时面板在 P19 到来时整体替换为正式 Errors 子页 + 新增 Logs 子页, `errors_bus.js` 保留沿用. 新增 §4.11 交代临时模块边界.
- **2026-04-18** 完成 P05 Setup workspace (Persona + Import 子页). 后端: `persona_config.py` (PersonaConfig Pydantic) / `sandbox.real_paths()` 暴露 patch 前路径 / `Session.persona: dict` / `routers/persona_router.py` 四端点 (GET/PUT/PATCH /api/persona + GET /real_characters + POST /import_from_real/{name}); 前端: `static/ui/_dom.js` 从 settings 目录提升共享 / `workspace_setup.js` 重构为 two-col + 4 子页 nav / `setup/page_persona.js` 表单 + Save/Revert / `setup/page_import.js` 真实角色列表 + 一键导入 / `setup/page_{memory,virtual_clock}.js` 占位; i18n `setup.*` + CSS `.badge.primary / .meta-card / .import-list / .import-row`. 关键设计: Import 读 `sandbox._originals` (即 patch 前的真实文档目录), 写 `cm.config_dir / cm.memory_dir` (即当前沙盒) — 单向 "读真实 / 写沙盒" 严格遵守; Persona 编辑仅存 session 内存, 与 `characters.json` 解耦以绕开上游 `migrate_catgirl_reserved` 迁移链, P08 Prompt 合成再整合; Import 时例外写 `sandbox/config/characters.json` (三键: 主人/猫娘/当前猫娘) 好让 P07 Memory 子页 + 上游 `PersonaManager / FactStore` 直接可用. 错误处理: Persona/Import 子页均为 `/api/...` 404 注入 `expectedStatuses: [404]`, 未建会话不误报.
- **2026-04-18** 完成 P07 Setup Memory 四子页. 后端: `routers/memory_router.py` 新增, 6 端点 (`GET /api/memory/state` 四文件 stat / `GET /api/memory/{kind}` 读原始 JSON + 空态 / `PUT /api/memory/{kind}` 原子写 + 顶层类型校验, kind ∈ recent|facts|reflections|persona); 前置: 无 session → 404, session.persona.character_name 空 → 409 `NoCharacterSelected`; 直接读写磁盘 JSON, 不经 `PersonaManager.ensure_persona`/`FactStore.save_facts` 等 loader 避免偷偷触发 character_card 合并副作用. 前端: 共用组件 `setup/memory_editor.js` (raw JSON textarea + 合法性徽章 + dirty 徽章 + 条目数 + Save/Reload/Format/Revert 四按钮) + 4 个薄 wrapper `page_memory_{recent,facts,reflections,persona}.js` + `workspace_setup.js` 重构左 nav 支持 `kind:'group'` 非交互分组标题, 加"记忆"分组 + 4 子项; i18n `setup.memory.*` 完整重写, CSS 追加 `.subnav-group` / `.json-editor` / `.badge.secondary`; 删除 `page_memory.js` 占位. 关键取舍: (1) 本期仅 raw JSON 编辑器, 不做 Facts 表格化 / Reflections 两列等富 UI — schema 还在 P09/P10 漂移期, 提前固化表单只会早早跟真实 schema 脱节; (2) 后端仅校验顶层 list/dict 与元素是 dict 的"最低骨架", 字段级 schema 交给上游 loader (故意允许 tester 写坏来探容错); (3) `PersonaManager` 的 character_card 同步是首次 `ensure_persona` 的副作用, 文档明确标注 P07 看到的是磁盘原始 JSON, chat 跑完 persona.json 被重写不是 bug. PLAN `p07_memory_rw` 状态同步改 done, content 字段补注"富表单推迟到 P10 叠加".
- **2026-04-18** 完成 P06 VirtualClock 完整滚动游标模型. `virtual_clock.py` 扩完整 API (cursor / bootstrap_at / initial_last_gap_seconds / per_turn_default_seconds / pending_advance / pending_set, 配套 now / gap_to / advance / set_bootstrap / set_per_turn_default / stage_next_turn / consume_pending / reset); `routers/time_router.py` 8 端点 (`/api/time` GET snapshot + `/cursor` GET/PUT + `/advance` + `/bootstrap` + `/per_turn_default` + `/stage_next_turn` POST/DELETE + `/reset`), 每个 mutate 都经 `session_operation` 锁并回传完整 clock; 前端新增共享工具 `static/core/time_utils.js` (秒 ↔ "1h30m" 文本 ↔ `<input type="datetime-local">` 本地 wallclock), `api.js` 补 `api.request` 通用逃生口, `setup/page_virtual_clock.js` 从占位升级为 5 张卡片 (Live cursor 含 1Hz 本地 tick 自熄火 / Bootstrap 分字段清除 / Per-turn default / Pending stage / Reset). 关键取舍: (1) pending.advance 与 pending.set 互斥, 都给时 absolute 胜; (2) `set_bootstrap` 用 `_UNSET` 哨兵 + Pydantic `model_fields_set` 区分 "未传 / 显式 null", 三个清除按钮都只发 `PUT + {field: null}` 而非单独 DELETE 子路由; (3) consume_pending 已就位但 P06 无 caller — 真正消费在 P09 /chat/send 开头, 避免本阶段写个会被拆掉的手动端点. 注: PLAN 里"消息 timestamp 迷你时间轴"推迟到 P09 (Chat 消息流就位) 再接入.
- **2026-04-18** P05 补强: Persona 子页新增"预览实际 system_prompt"折叠面板. `routers/persona_router.py` 增 `GET /api/persona/effective_system_prompt` (复用 upstream `config.prompts_chara.get_lanlan_prompt` + `is_default_prompt`, 并做 `{LANLAN_NAME}/{MASTER_NAME}` 替换), 支持 `lang / master_name / character_name / system_prompt` query 参数以便在未 Save 的 draft 上预览; 前端 `setup/page_persona.js` 追加 `renderPreviewCard(draft)` (折叠 details, lazy load on first open, [刷新] 按钮, 显示 source = default/stored + 两段 code block `resolved` / `template_raw`, 自定义文本意外匹配默认模板亮 warn, 名字留空警告占位符未替换). i18n `setup.persona.preview.*` + CSS `.preview-summary/.preview-details/.preview-body/.preview-code/.empty-state.warn/.button.tiny`.
- **2026-04-18** UX 细节: 会话创建/销毁自动刷新当前可见子页. `workspace_setup.js` / `workspace_settings.js` 订阅 `session:change`, 当前 workspace 可见则立即 `selectPage(currentId)` 重渲染, 否则打 `dirty` 标, 下次 `active_workspace:change` 切回本 workspace 时再刷. 动机: 修复 "Persona 子页提示无会话 → 顶栏新建会话 → 页面仍停留在空态, 必须手动切走再切回" 的 UX 毛刺. 延迟刷新避免不可见 workspace 产生无谓请求. Chat/Evaluation/Diagnostics 会话无关, 不加订阅.
