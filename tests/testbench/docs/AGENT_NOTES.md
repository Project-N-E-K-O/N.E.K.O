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

### 4.9 mermaid 图语法
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
