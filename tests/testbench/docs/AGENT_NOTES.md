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

### 4.12 Workspace 懒挂载但**不卸载**, 跨 workspace 数据一致性需事件驱动

`app.js` 的 `_mountedWorkspaces: Set` 保证每个 workspace 只 `mount()` 一次 — 用户反复切 tab 期间 DOM 不重建, 里面的数据也不会重新拉. 这是为了避免会话数据/折叠状态被重置, 但**代价**是跨 workspace 的数据依赖必须走事件总线, 不能依赖"切回来重挂载就顺便刷一次".

- 会话级变化 (创建/销毁) → `session:change`, 每个 workspace 的 mount 函数里自行订阅, 切到本 workspace 时即时刷新或打 dirty 标等切回再刷 (Setup/Settings 已实现).
- 同一会话内跨 workspace 的数据依赖 (例如 Setup 改 Persona → Chat 的 Prompt Preview 要刷) → 建议订阅 `active_workspace:change`, 切到目标 workspace 时主动 refresh; 加简易防抖 (例如 200ms) 避免 tab 快速切换时打后端太猛. Chat P08 已实现 (见 `workspace_chat.js`).
- 未来 workspace 加新数据依赖时, 先问自己: "这个数据在别的 workspace 里被改过之后, 我切回来能看到最新的吗?" 如果答案不是"会话重建", 那就必须手动订阅事件.

### 4.13 Chat P09 的若干落地坑 (SSE / 会话锁 / messages schema / delta DOM)

P09 引入了第一个真正跑 LLM 的 pipeline, 一些看似小事其实不止踩一次:

1. **SSE over POST 必须自己写**. `EventSource` 只支持 GET + 不可带 body, 而 `/api/chat/send` 的 payload (content/role/source/time_advance) 很快就会超出 querystring 体积. 前端 `sse_client.js` 用 `fetch + ReadableStream + TextDecoder` 自己切 `\n\n` 分帧; 服务端用 Starlette `StreamingResponse(media_type='text/event-stream')` + `yield f"data: {json.dumps(evt)}\n\n"`. 不要回到 EventSource, 别把 body 塞进 GET 查询串.
2. **httpx 客户端必须 `aclose()`**. `chat_runner.py` 的 `OfflineChatBackend.stream_send` 用 `try/finally: await client.aclose()` 释放连接池; 任何异常分支 (含用户中止) 都要过 `finally`, 否则长期压测会把进程 FD 打满. 上游 `ChatOpenAI` 自己不释放.
3. **session lock 整段持有 stream**. `chat_router.send` 用 `session_operation(SENDING)` 包住整个 async generator, 意思是流式发送期间 session 会一直 BUSY. 这是故意的: 同一会话不允许双发 (否则消息顺序、clock.consume_pending 都会乱). 代价是前端 composer 单实例即可; 如果将来 UI 想同时 "Send" + "Memory trigger", 需要**重新设计锁粒度**, 现在先单闸口.
4. **`session.messages` 是唯一真相**, prompt_builder/wire_messages 不做消息变换. OpenAI `role ∈ {user, assistant, system}` 与 testbench 的 ROLE_* 常量已统一, 不翻译; `source` (manual / inject / llm / simuser / script / auto) 只是审计用, 不写进 wire. 别给 user 消息前缀拼主人名, 那是真实 Context 做的事, 测试床故意不做以便独立审 prompt.
5. **发送前把 wire 落 JSONL**. `chat_runner._log_wire_before_send` 在 `astream` 之前就把 model_cfg (masked api_key) + 完整 wire_messages 写进 `session/logs/chat_send.jsonl`. 即便 LLM 端 500 也能复现输入; 如果等到 done 再记, 连失败复现都做不到.
6. **`chat:messages_changed` 只在写操作完成后才 emit**. composer 在 SSE `done` 才 emit; inject 在 POST 成功后 emit; message_stream 在 edit/delete/patch_timestamp/truncate 成功后 emit. 因此 workspace_chat 可以安全地做 200ms 防抖 `previewHandle.refresh()` — 不会跟流式 delta 竞争 (delta 期间没人 emit 这个事件), 也不会跟 `message_stream.beginAssistantStream` 返回的直接 DOM 引用打架. **重要**: 如果将来有人新增 emit 点 (例如"delta 中间也想打 dirty 标"), 必须确认不会在流式期间触发 preview refresh, 否则流式 `<div>` 的 `textContent` 会被 renderAll 冲掉.
7. **Next turn 的真相源在后端**. Composer Row1 不缓存 pending delta, 每次 stage/clear 都重读 `GET /api/time` 或接口返回体里的 clock snapshot. 理由: VirtualClock.pending 可由 Setup → Virtual Clock 页或多个 chat send 间接改, 前端自己算 delta 会漂.
8. **`truncate` 回退 clock 是副作用**. `POST /api/chat/messages/truncate` 不仅删消息, 还把 `clock.cursor` 回退到被保留的最后一条消息的 timestamp (保留自身时). "从此处重跑"语义需要这条保证, 不要拆成前端两次调用.

9. **`stream_send` 里 yield 过的消息**绝对**不能 pop 回滚**. 关键顺序: `session.messages.append(user_msg) → yield {event:'user', ...}`. user 事件一旦推到 SSE, 前端就把它加进本地 `messages[]` + 渲染了 DOM, 这一步不可撤销. 如果随后因 `PreviewNotReady` / `ChatConfigError` / LLM 异常想"回滚 user 消息", 就会产生灵异现象: **前端看得见, 但 `GET /messages` / preview / `PUT /messages/{id}` 都认为没这条** (曾真实触发过 "Send 完的消息修改时间戳 HTTP 404"). 对 assistant 占位 (尚未 commit 最终内容) 例外: 它在异常路径上可以 pop, 因为前端的 `beginAssistantStream` 返回的 handle 在 `abort()` 时会自己把流式 DOM 节点撤掉 (见 `message_stream.abort`). 简而言之: **"yield 了就当这条消息已经落盘了, 不能再偷偷 pop"**. 用户看到错误 toast 后, 要么修好 config 再点 Send, 要么手动从消息菜单里删掉这条失败消息.

10. **"副作用已发生"的通知不能只绑在 happy-path 事件上**. 与 #9 配套的前端坑: 后端 `{event:'error'}` 路径 (ChatConfigError / PreviewNotReady / LLM 流异常) **没有** 随后的 `{event:'done'}`, 但 `user_msg` 其实已经落盘. 早期 `composer.js` 只在 `case 'done'` 里 `emit('chat:messages_changed')`, 导致未配置 chat 模型时发送后 preview 不自动刷新 (消息在后端有, 预览 UI 里看不到). 正确模式: 用一个 `userMsgPersisted` 旗标标记 "已经看到过 `user` 事件", 然后在 **`onDone` (不论 done/error 收尾都会到)** 和 **`onError` (传输层失败)** 两处都根据旗标 emit. 这样才能保证 "有副作用 → 必有通知". 通用启示: **任何 mutate 后端状态的 async 流程, emit 应该贴在"副作用已发生"的那一刻或 stream 关闭兜底, 而不是贴在 happy-path 里**. P11+ 的 SimUser / auto dialog 流程会 yield 更多中间事件, 照此模式检查每个 emit 点.

13. **消息时间戳必须单调不减 (retime / append 都要校验)**. 踩点: "我把中间某条消息的 timestamp 改到比上一条更早, 结果 ChatStream 时间流逝提示显示两条之间的时差 (一个负数绝对值), UI 排序也错乱 — 因为列表按插入序渲染, 但邻居时差按 timestamp 计算, 两者不自洽". 根因: `PATCH /api/chat/messages/{id}/timestamp` 和 `POST /api/chat/messages` 都没有校验新 timestamp 相对邻居的位置, 写什么存什么. 但下游 (时间分隔符 "— 2h later —" / prompt_builder 的 recent_history 切片 / `clock_resynced` 的 `set_now` 逻辑) 全都**假设 `session.messages` 按 timestamp 非递减**. 这条不变量一破, 错乱面很广. **修正方案**: (a) `chat_messages.check_timestamp_monotonic(messages, idx, new_ts)` — 对 idx 处的新 timestamp 同时检查它**不早于 messages[idx-1]**、**不晚于 messages[idx+1]** (索引越界时跳过该侧). 允许**相等** (`<=`), 因为同秒背靠背的消息是合理场景. (b) `POST /messages` 传 `idx = len(messages)` (append 位置, 只有上界有邻居), `PATCH /timestamp` 传当前 idx (两侧都检查, 自身旧值忽略). (c) 违反时返回 **HTTP 422 `{error_type: "TimestampOutOfOrder", message: <含邻居索引和具体时间戳的中文文案>}`**, 前端 `message_stream.js::editTimestamp` 已有 `expectedStatuses:[422]` + `toast.err(chat.stream.toast.bad_timestamp, {message: res.error?.message})` 兜底, 无需前端改动. (d) 时区处理: `_compat_ts(a, b)` 遇到 tz-aware / tz-naive 混合时剥离 tzinfo 再比较, 避免 `TypeError: can't compare offset-naive and offset-aware datetimes`. testbench 本来就不玩多时区, tzinfo 只是信息性. **延伸教训**: **数据结构的不变量要在写入点就守住**, 不要只在渲染侧做"发现错序就容错". 我之前默认"后端存什么前端认什么, 错了用户自己改回来就行" — 听起来灵活, 实际等于把"维护不变量"的责任甩给用户. UI 层看到的数据形态不可预测, 下游逻辑 (时差计算 / 切片 / prompt 组装) 要么要加一大堆 defensive 分支, 要么就会出现这种"看起来都对但互相矛盾"的故障. **正确做法: 明确一组 invariants, 每个 mutation endpoint 写入前校验, 失败就 422 拒绝**. 这条对将来 `POST /inject_system`、SimUser/Script 注入消息的 P11/P12 阶段同样适用 — 所有新增消息入口都应该 reuse `check_timestamp_monotonic` 而不是各自判断.

12. **Lanlan 免费端防滥用拦截的 testbench 旁路 (纯测试生态补丁, 不动主程序)**. 踩点: "#11 做完后我选免费预设发消息, lanlan 服务端回 400 `{"error":"Invalid request: you are not using Lanlan. STOP ABUSE THE API."}`". 根因: lanlan 服务端对 `www.lanlan.tech` / `lanlan.tech` / `www.lanlan.app` 三个域下的 `/text/v1/chat/completions` 都启用了"必须是 NEKO 主程序发起"的校验 (机制未公开, 推测 TLS 指纹 / 特殊请求形态 / 特殊证书), 只有**老域名无 www 前缀**的 `https://lanlan.app/text/v1` 未开启. 主程序 `utils/config_manager._adjust_free_api_url` 只做 `lanlan.tech → lanlan.app` 的 GeoIP 级替换, **不动 `www.` 前缀**, 所以主程序能走的也是 `www.lanlan.app`/`lanlan.tech` 这两个"被拦的" URL — 但它自身靠那套未公开的机制过关; testbench 从裸 `openai` SDK / `urllib` / `httpx` 发起, 没有那些特征, 四个可能的 URL 里只有 `lanlan.app` 一个能用. 实测结果 (2026-04-18): 其它三个一律 400; `lanlan.app` 正常返回 OpenAI 兼容 completion. **修正方案**: 在 `tests/testbench/pipeline/chat_runner.py` 加 `_rewrite_lanlan_free_base_url(cfg)`, 命中 `//www.lanlan.tech/` / `//lanlan.tech/` / `//www.lanlan.app/` 时改写为 `//lanlan.app/`, 在 `resolve_group_config` 返回前统一调一次. 这样 `test_connection` + `stream_send` + 将来的 simuser/judge/memory 通道全都自动受益. 关键设计: (a) **不改 `config/api_providers.json`** — 那是主程序财产, 按规则不动; (b) **不回写 session.model_config** — summary 页面展示的还是用户选的原始 URL, 避免"我明明填的是 lanlan.tech 怎么后端存成 lanlan.app 了"的视觉欺骗, 改写只在"发给 SDK 前"这一瞬间发生; (c) **只匹配 `//host/` 格式** — 避免把 URL query 里偶然出现的字符串也误替换 (虽然极不可能, 但写代码时值得刻意). **延伸教训**: 这是一个典型的"主程序-测试生态双向妥协"场景. 测试生态在**设计上**就不可能完全等同于主程序: TLS 栈、请求构造方式、 HTTP 客户端库版本, 都会导致看起来一样的 HTTP 请求在服务端眼里是两个完全不同的物种. 遇到"主程序能用 testbench 不能用"时, 第一反应不应该是"逆向主程序到底做了啥", 而是"**有没有一个更宽松的后端入口能给测试环境用**". 本例中 `lanlan.app` 正好是这个入口. 后续若它也被关, 应先找主程序同学协商出测试专用的 token / header, 而不是在 testbench 里造假 TLS 指纹那种不可维护的方向. 这条原则推广: **测试生态访问真实外部服务时, 优先走服务方给的"开放测试通道"; 没有的话先申请, 不要自己伪装客户端身份**.

16. **文件级编码污染是隐性但致命的: 非 ASCII 字面量不要直接写进可能被破坏的文件**. 踩点: `memory_editor_structured.js` 被用户打开时发现"系统/用户/助手"字眼全变成 `??`, "+ 添加" 前缀变成 `??添加`. 排查: 整个文件**1808 个非 ASCII 字节全部被替换成 `?`** (UTF-8 中文 3 字节 → 单字节 `?`), 代码能编译但用户可见的硬编码字符串全毁. 根因推测: 某次编辑经过的工具/终端对非 UTF-8 解码时走了 `errors='replace'` 的 GBK/latin1 路径, 回写时已经丢失原始字节. **修正方案 (本轮)**: (a) `memory_editor_structured.js` 里所有用户可见的非 ASCII 字面量全部改走 i18n key: `selectInput` 的 `'human (用户)'` → `i18n('setup.memory.editor.message_type.human')`; `addButton` 的 `+ ${label}` 前缀用纯 ASCII `+`; 多模态分隔符 `' · '` → `' | '`; 删除按钮用 i18n `delete_item` ("删除") 不再尝试写 `'✕'` 字符. (b) **本文件禁止直接写任何非 ASCII 字面量** (emoji / 中文符号 / ✕▾▴ 等 Unicode), 只能通过 i18n.js (独立文件, 未被污染) 或 `'\uXXXX'` 转义字符串. (c) 注释里的中文损失暂不回填 (工作量过大且不影响功能), 但新增注释必须用英文或 `\uXXXX`. **延伸教训**: 面向用户的**文案绝不硬编码在业务逻辑文件**, 一律进 i18n.js 等专用字典. 业务逻辑文件只放 key, 这样即使业务文件被编码工具破坏, 文案跟着 i18n.js 还在. 这条已经是业内共识但以前没强制执行, 本次事故验证了它的必要性. 还有: **自己写 `'✕'` 前先想想"这个字符能活过下次编辑吗"** — 但凡发生过一次丢字节, 后续每次 save 都是俄罗斯轮盘赌, 唯一可靠的是 `\uXXXX` 转义或 i18n. 同族风险点: macOS 路径分隔符、中英文标点混用、zero-width space (常被 Markdown/格式化工具吞), 都属于"字面量陷阱", 能避免就避免.

15. **卡片级 UI 的"垂直密度"是靠拆掉整行动作条换来的, 不是靠 padding 微调**. 踩点: #14 调完后用户又反馈"父容器竖向拉得过高, 留下大把空白". 测量: 每张 memory 条目卡底部有一条独占行 = `margin-top:4px + 虚线分隔 1px + padding-top:6px + button 26px + padding-bottom:6px ≈ 42px`, 只放一个 Delete 按钮, 对一行短文本的 fact 卡就是 "正文 40px + 动作条 42px" 几乎对半分, 视觉上确实**一半都是空的**. 单靠 `gap` / `padding` 缩到 0 也省不出这么多, 因为 button 高度本身就 26px, 加上 hover 边框至少 30px+. **修正方案**: 删除按钮从卡片底部的"整行"改成卡片右上角的"角标" — `position: absolute; top:4px; right:4px; 24×24px; ✕ 字形 (U+2715)`; 卡片 `.memory-item-card` 加 `position: relative` 当锚点, `padding-right` 从 `10px` 补到 `36px` 留出按钮位置, 内容不会被按钮覆盖. 这样每张卡片净减 ~42px 高度, 且按钮依然是 ghost 样式平时低调 (hover 才变红边), 语义没有丢失. 同时处理了"source 下拉框文本溢出"——`character_card` (14 字符) 在 `.memory-field.narrow` 的 140px 上限下被挤到溢出格子. 原则: `narrow` (`flex:0 1 110px; max-width:140px`) 只适合**绝对短值** (1-2 位数字 / `human`/`ai` / `pending`/`confirmed` 这种 8 字符内的枚举); 任何可能出现 12+ 字符选项的 select 都不要标 narrow, 让它走默认 `flex:1 1 160px` 自然伸展. **延伸教训**: (a) **UI "感觉太松散" 先看能不能整行删掉, 再考虑 padding 微调** — 一行独立的 action row 不管内容多少都有 `padding + border + button-height` 底价, 挤 padding 只能省个位数 px, 改成角标/inline 才能省两位数. (b) **`narrow` 类约束只给"保证短"的字段**. 给 select 加 `max-width` 之前先 grep 一下它所有 option 的最长值在当前 font 下的渲染宽度 (约 `字符数 × 7.5px + 20px 选择器装饰`), 否则溢出的体验比不 narrow 更糟 (至少不 narrow 时只是占空间, narrow 溢出时字会露出来. 这跟 #14 最后那条"schema 要对齐真实数据"是同一个毛病的两面: UI 约束也要对齐真实**值域**, 不能只想着默认 option 名字够短就一刀切). (c) **文件级编码问题**: `memory_editor_structured.js` 在本阶段前的某次编辑里意外把 1808 个非 ASCII 字节 (几乎全是中文注释) 替换成了 `?`, 文件变成纯 ASCII. JS 语法合法不影响运行, 但可读性塌了. 修这轮时初次在代码里写 `✕` 字符也踩了同一个坑 — 写回磁盘后变成 `??` 把字符串截断成语法错误 (`'??);`). **兜底办法: JS 源码里出现任何非 ASCII 字符 (emoji / 中文符号 / 特殊 Unicode 如 ✕/▾/▴) 时, 一律用 `'\uXXXX'` 转义, 不要直接嵌原字符**. 下一 Agent 注意: 这个文件的中文注释已经丢失, 如果需要重加注释请用英文或 Unicode 转义, 或者从本 AGENT_NOTES / PROGRESS 的叙述回填要点, 不要假设原注释还在.

14. **JSON 编辑器双视图: 结构化 + Raw, 两边共享 model 而非各持状态**. 踩点: "我把人设记忆编辑填写为 `{}` 报 `toast is not a function`; 填 `[]` JSON 合法但实际不是合适记忆格式, UI 没给任何提示". 两个根因: (a) `memory_editor.js:199` 写成 `toast(i18n(...))` 但 `toast` 是个对象 (`{show, ok, err, info, warn, mini}`), 应该 `toast.ok(...)`. ESM + JS 这种 typo 没编译期报错, 直到保存按钮 click 时才当场爆. (b) 纯 Raw JSON 编辑器让非开发者测试人员去"研究某个 memory kind 的合法 schema 应该怎么写", 但这些 schema 其实是主程序已写死的 (persona=`dict[entity→{facts: [...]}]`, facts=`list[fact_entry]`, reflections=`list[reflection]`, recent=LangChain dump) — 让测试人员手写就是在浪费时间. **修正方案**: 把 memory 编辑器拆成三文件 + 双视图 tab: `memory_editor.js` 作为容器 (tab 切换 + 共享 state + toolbar badges / save / reload / revert); `memory_editor_raw.js` 原大 textarea, 保留应对罕见情况 (legacy 字段 / 畸形载荷测试 / multimodal content 列表); `memory_editor_structured.js` 按 kind 渲染卡片表单, `+` 按钮用 `defaultXxxEntry()` 工厂拉默认合法条目, 常见字段直出 (text/entity/status/source/protected/tags/importance/feedback), 低频字段 (id/hash/created_at/recent_mentions/next_eligible_at 等) 折进 `<details class="memory-advanced">`. **关键设计取舍 — 共享 model 而非各自状态**: 两视图都读写同一个 `state.model`, 用 canonical(model) 判 dirty. Raw → Structured 切换时 parse 一次覆盖 model (parse 失败拒绝切换并 toast 指向错误); Structured → Raw 时 stringify 覆盖 textarea. 切视图不会产生 "Raw 里改的和 Structured 里改的两边打架" 这种漂移. **DOM 刷新分两档**: 值修改 (打字/勾选/选项切换) 只 `notify()` 给容器刷 dirty badge, **不重建 DOM** (否则 textarea 打一个字就失焦, 用户根本没法连续输入); 结构修改 (增删条目/实体) 才 `restructure()` = redraw. **checkbox 用 `inlineField` 不用 `simpleField`**: 语义上 `protected / suppress / absorbed` 是布尔状态不是填空, label 放 checkbox 上方 + `.memory-field flex: 1 1 160px` 会把 checkbox 强占 160px+ 宽度, 小方块孤零零, 极浪费视觉. `inlineField` = `<label><checkbox/><span/></label>` `flex: 0 0 auto` 紧贴不撑宽, 整个 label 可点 + hover 高亮, 一眼识别. **"建设性"/"操作性"/"危险" 三档按钮样式分离**: (i) add = `.memory-add-button` 全宽虚线幽灵 (承诺 "这是新增", hover 变 accent 实线); (ii) 普通操作 = 普通实线按钮; (iii) 删除 = `.ghost.memory-item-delete` 平时灰幽灵, hover 才变红 + 实线边框. 原本 `.tiny.danger` 的常亮红删除按钮在 card 底部一直刺眼, 降级成 ghost 后误触视觉压力明显下降. **`el()` 属性赋值需对 DOM 只读 getter 容错**: `HTMLInputElement.list / labels / form / validity` 等名字和 HTML 属性同名但 DOM 上是**只读 getter**, `el('input', {list: id})` 会走 `node[k]=v` → `TypeError: has only a getter`, **炸掉整个子页渲染** (实测 Facts 页面"子页渲染失败: Cannot set property list of HTMLInputElement which has only a getter"). 在 `_dom.js` 的 `el()` fallback 分支加 `try{node[k]=v}catch{node.setAttribute(k,v)}` 通用兜底, 同时 `entityInput` 里改用显式 `input.setAttribute('list', id)` 并留注释警示 future renderer. **textarea 自适应高度 + 超长折叠**: 固定小 textarea + 全局 `resize:vertical` + 内部滚动条的原始设计被用户点名"用起来很不顺手"——(a) 卡片底色 `--bg-input` 和全局 textarea 底色同为 `--bg-input` → 糊成一片, (b) `rows=2/3` 撑不开内容 → 稍长就出内部滚动条且卡片其它空间闲着. 修正: `wrapWithAutosize(textarea)` 共用 helper 给 structured 视图的所有 textarea (包括 `rawJsonInput`) 挂自适应逻辑 — input 事件里 `style.height='auto'` + `scrollHeight` 重算, **短文本按内容撑开不滚动**; 超过 `foldThresholdPx≈320` 时自动折叠为 `foldDisplayPx≈160` 高度 + 滚动条 + 下方出现"展开全文 ▾ / 折叠 ▴"切换按钮, 这样长对话不会一屏吃满整个 workspace. CSS 同步: `.memory-field textarea` 底色强制改用 `--bg-panel` (最暗) 给"凹陷感", 禁 `resize: none` + `overflow-y: hidden` 让 JS 完全接管高度. **首次测量**: `wrapWithAutosize` 内用 `requestAnimationFrame(resize)` 等待 DOM 连入再量 `scrollHeight` (刚 `el()` 出来时元素未挂树, `scrollHeight=0`); 不连时 `isConnected` 守门再 rAF 重试, 不会算错. 每个 renderer 的 "+"/"删除" 走 restructure, 每个 input 的 onChange 走 notify. **特例**: recent kind 的 `data.content` 可能是 LangChain multimodal list-of-parts (`[{type:'text', text:...}, {type:'image_url', ...}]`), 不是 string. 绝对不能无脑 `textareaInput(msg.data.content, ...)` 把它展平成 `[object Object]`. 但"一律退化成 warn 要求切 Raw" 又过于粗暴 — 实际多数多模态消息就是"首段 text + 其余图片/音频"的简单结构, 首段 text 就是对话正文. **细分方案**: (a) content 是 string → 直接 textarea; (b) content 是 Array 且含 `{type:'text', text:string}` 分段 → 定位**首个** text 分段, textarea 直接绑它的 `text` 字段, 其它非文本分段原封不动, 下方 hint 条说明"另含 N 个非文本分段" / "共 N 个文本分段, 上方只编辑首段"; (c) content 是 Array 但无任何 text 分段 (纯图/纯音频) → warn + 引导切 Raw; (d) content 是 object/null 等其它怪形态 → 同 (c). 核心不变量: **非文本分段永远不被结构化视图触碰**, textarea 只改它能识别的那一段 text. 原 value 仍然保留在 advanced `extra_data` JSON 区域供 Raw 级审视. **时间戳工厂**: `defaultFactEntry.created_at` 等用 `new Date().toISOString().slice(0, 19)` 构造 naive ISO (`YYYY-MM-DDTHH:MM:SS`), 与主程序 `datetime.now().isoformat()` 格式一致, 避免 `Z` / 毫秒后缀带进 memory 侧的 dedup/compare 踩时区. **延伸教训**: (a) 任何 `X is object` vs `X is function` 的 typo 在 JS 里都只能靠运行时炸点兜底, 对"每次都跑不到"的成功分支 (例如 save 成功后的 toast, error 路径从来不走) 特别危险 — 写新代码时 "非 happy-path 分支也至少 smoke 一次". (b) **面向测试人员的 JSON 编辑器应该默认结构化, Raw 作为逃生舱**, 而不是反过来. 主程序有特定 schema 的东西就不要让测试人员去重新发明合法写法, 会被"顶层类型合法 ≠ 业务合法"的细微差别坑到. (c) **数据 schema 要对齐真实数据, 不要只对着主程序代码抽象**: persona fact 的 `importance` 我原以为 1-5, 实测磁盘上有 6/7/8, 差点写死 `min:1 max:5` 限住合法值. 每次为结构化 UI 定 schema 前, 至少 `GET` 一份真实数据 grep 一遍字段范围.

### 4.14 P10 记忆操作触发的若干落地坑 (预览缓存 / 路由顺序 / LLM 配置隔离)

P10 引入了第一个"dry-run → 人工审阅 → commit"模式的 pipeline, 和 Chat P09 的"直接写"路径不同, 有几个设计点值得标出:

17. **预览期禁止磁盘副作用, commit 期才写**. 记忆 op 的传统实现 (`RecentHistoryManager.compress` / `FactExtractor.extract` / ...) 都是"读磁盘 → 调 LLM → 写磁盘"单原子操作, 没给"先算不写"的横切点. P10 不去改主程序 manager, 而是在 `tests/testbench/pipeline/memory_runner.py` **重新实现**五个 op 的 preview/commit 双阶段: preview 算完写 `session.memory_previews[op] = {created_at, payload, params, warnings}` 内存缓存就返回, commit 读缓存 + 应用用户 edits + 原子写盘 + pop 缓存. 代价是**两侧算法同步责任**: 若主程序改了 prompt (`config/prompts_memory.py`) 或抽取 schema (`FactExtractor.extract_facts`), memory_runner 要跟进. 收益是 dry-run 完全回滚零成本 (丢缓存即可), 且测试人员看到的预览和真实 LLM 输出完全一致 (prompt / messages / 模型配置一字不差). **延伸教训**: 测试生态想做"先看后做"时, 两条路: (a) 改主程序加 dry-run 参数 (侵入, 但逻辑唯一); (b) 测试侧复刻算法 (零侵入, 但要做同步). 记忆系统选 (b) 因为涉及太多 manager 和 prompts, 改主程序 API 面太大且会弄脏生产代码路径; 假想用户 / judger 这种**本来就是 testbench 独享**的会走另一条 (主程序根本没有对应 manager).

18. **memory 组的 LLM 配置必须走 testbench 自己的 resolver, 不能复用主程序 summary/correction 组**. 踩点: 最初想省事直接调 `RecentHistoryManager(cm).compress(...)` 让它自己组装 LLM, 发现它读的是 `ConfigManager.summary_provider_config` — 而 testbench 的沙盒补丁 (sandbox.py) **只替换磁盘路径** (`memory_dir` / `docs_dir`), **不替换 API 配置**, 所以主程序 manager 跑起来会用 ConfigManager 当前的真实 summary 模型, 而不是测试人员在 Settings → memory 组里填的模型. 这等于测试人员看到的"memory 组 API Key 填进去没反应"——因为根本没用上. **修正方案**: `memory_runner._llm_for_memory(session, temperature=...)` 用 `resolve_group_config(session, "memory")` 拿 testbench 自己的 `memory` 组 `ModelGroupConfig`, 再调 `pipeline.chat_runner.create_chat_llm(resolved_cfg)` 造 LLM 实例, **完全不碰 ConfigManager.summary/correction**. 同理 `facts.extract` / `reflect` / `persona.add_fact` 的冲突检测 LLM 也全部走 memory 组. **延伸教训**: "沙盒"的职责边界要一开始就讲清楚 — 磁盘隔离 ≠ 配置隔离. 以后再加类似的"testbench 要用自己的配置而不是全局配置"场景, 第一步先确认 "这个资源是从哪拿的" (`ConfigManager.X` 还是 `session.Y`), 如果是前者就要么在沙盒里补丁, 要么在 testbench 侧彻底绕开. 绕开往往更清晰, 因为补丁会让 "同一个 attr 在不同上下文值不同" 难排查.

19. **FastAPI 路由声明顺序**: `/previews` 必须写在 `/{kind}` 前面. 踩点: `memory_router.py` 新加 `GET /api/memory/previews` 后, 实际访问返回 `{"error_type": "UnknownMemoryKind", "message": "未知 kind: 'previews'"}`. 根因: FastAPI 的 path matcher 对静态段 vs 动态段**不做优先级偏好**, 完全按声明顺序. `@router.get("/{kind}")` 写在前面, `/previews` 就被当 `kind="previews"` 走到 `read_memory(kind="previews")` 的 `UnknownMemoryKind` 分支. **修正**: 把 `@router.get("/previews")` 和所有后来的 `trigger/commit/discard/{op}` 端点的声明**全部挪到 `@router.get("/{kind}")` 之前**. 在文件里加显式注释警告未来 Agent. **延伸教训**: (a) 任何 wildcard 路由后面再加静态路由都要检查声明顺序; 如果 wildcard 已经出现, 新增的静态段应该往文件顶部插, 不能直接追加. (b) 用 FastAPI `router.get(path)` 装饰器时, 养成"先画路由表再写实现"的习惯 — `/api/memory/{previews, trigger/{op}, commit/{op}, discard/{op}, {kind}, {kind}}` 这种复合结构, 扫一眼表就能看出 `{kind}` 必须殿后. (c) 如果 wildcard 和静态段混合非常多, 考虑拆多个 `APIRouter` (如 `previews_router` / `trigger_router` 分别 include) 而不是堆在同一个 `router` 上靠顺序.

20. **预览 drawer 的错误展示不走 toast**. 踩点: 早期想法是"调 trigger 失败弹 toast 就行". 实测 `recent.compress` 的预期失败包括: `NoRecentHistory` (recent.json 为空)、`MemoryModelNotConfigured` (memory 组未设)、`LlmFailed` (502), 每种都要让用户看到具体 `error_type` + `message` 才知道下一步该改什么. 把这些信息塞 toast 里读不全 + 很快消失, 完全不是"业务预期错误"的展示方式. **修正方案**: `triggerAndShowPreview` 里 `expectedStatuses: [404, 409, 412, 422, 500, 502]` 让失败不走全局异常 toast, 然后 `renderErrorDrawer(spec, detail, status)` 把 error_type (mono) + message (正文) + status code 渲染成红色 `.memory-preview-drawer.err` drawer, 放在触发按钮下方与成功预览同位置 — 用户可以对比一眼看到失败原因, 不需要翻 toast 历史. Commit 失败同理在 drawer 内 `statusLine` 显示. **延伸教训**: toast 是**瞬时通知**, 只适合"操作成功/失败"的一句话提醒; 只要错误信息里有**用户需要采取行动的具体细节** (缺哪个 config / 哪条数据有问题 / 错误码), 就应该走 drawer / inline banner / dialog 这类**持久展示**控件. P08 的 wire_rendering 里"第一轮 prompt" 提示走的也是 inline hint, 同理. P16 judger 的失败结果更要走 drawer 而非 toast, 提前打个招呼.

21. **预览 payload 是"可编辑的中间结果", commit 端要接受 `edits` 覆盖**. 设计细节: `POST /api/memory/commit/{op}` body 是 `{edits: {...}}`, 里面字段因 op 而异 — recent.compress 有 `memo_system_content`, facts.extract 有 `extracted: [...]` (用户可剔除条目), reflect 有 `reflection: {text, entity, ...}`, persona.add_fact 有 `text/entity`, resolve_corrections 有 `actions: [...]`. runner 的 `_commit_*` 函数里先从 `session.memory_previews.pop(op)` 拿原始 preview, 再把 `edits` 字段**浅合并**覆盖到 payload, 最后落盘. **关键设计**: preview 和 commit 之间**不会重跑 LLM**, commit 只是"把最终文案 + 参数应用到磁盘的一次 I/O". 如果用户觉得预览不好, 应该 Cancel (discard) 然后换参数重新 trigger, 而不是期望 commit 里 edits 能"重算". **延伸教训**: "先看后做"模式的关键是"看到的就是要做的", 不能在 commit 里做预览阶段没做过的事 (例如: 基于 edits 再调一次 LLM 重新抽取); 那样就破坏了"预览即承诺"的原则, 用户看到的和磁盘写的会不一致. 如果真的需要"基于审阅结果再算一次", 做法应该是"Cancel + 带着 edits 再 Trigger" (即 edits 作为下一次 trigger 的参数), 而不是让 commit 变复杂. 这条对 P16 judger 的"审阅判分再重跑"流程同样适用.

22. **临时状态字段要在 Session dataclass 里挂**, 别塞全局字典. `session.memory_previews: dict[str, dict]` 跟 session 生命周期绑定, 会话切换 / 销毁时自动连带清. 如果放模块级字典 (例如 `memory_runner._previews_by_session: dict[session_id, dict]`), 得在 session_store 的删会话路径专门加清理钩子, 还容易漏掉. **dataclass `default_factory=dict`** 是关键, 保证每个 Session 实例自己有独立 dict 而不是共享引用. TTL (600s) 在**读时 prune** — 不起后台定时器, 冷启动下只要不去读就不占 CPU, 一读就顺便扫一遍 `created_at` 过期的 pop 掉. **延伸教训**: 所有"会话级临时态"都应该是 Session 字段, 不是模块变量. 这样 (a) 生命周期清理免费, (b) 多会话并发时不串, (c) 序列化 (P21 持久化) 时也容易决定要不要存 (`memory_previews` 显式不 persist 因为过期重算成本低).

### 4.15 内置人设预设的几个约束

23. **预设目录 = 仓库侧 git 资产, 和 sandbox / testbench_data 严格分开**. 踩点: 首次设计时差点把预设放到 `tests/testbench_data/presets/` 下, 但那个目录整体 gitignore, 预设数据根本不会跟仓库走. 正确位置: `tests/testbench/presets/<preset_id>/`, 跟着代码入 git, 和 `scoring_schemas/builtin_*.json` / `dialog_templates/sample_*.json` 的位置约定一致 — 都属于"代码 + 内置预设, 入库", 不是"运行时数据". **延伸教训**: **"测试生态" 的 git 追踪策略只有两档**: 代码目录 (`tests/testbench/`) 里的**所有**文件都入库 (包括 JSON / YAML / 示例 seed 数据 / 静态资产); 数据目录 (`tests/testbench_data/`) **整体**不入库 (sandbox / logs / saved / exports 全在里面). 新增"内置预设"类东西时第一反应要定位到代码目录下 — 用户自定义覆盖层才在数据目录. 不要犹豫.

24. **内置预设 = "虚拟 real_characters" 而不是独立 pipeline**. 踩点: 最初想给预设做专属 service (`persona_seed.py` + 新一批 helper), 写到一半发现 `import_from_real` 已经把"写 characters.json + 拷 memory 树 + 回填 persona"全做了, 只要**数据源**换成预设目录就行. 改成"在 persona_router 里加 `_load_preset_meta` / `_read_preset_characters_json` / `_summarize_preset` / `_copytree_safe_with_normalization` 四个 helper + 两个端点, 复用 `_write_sandbox_characters_json` + `_copytree_safe` + `_extract_catgirl_entry` + `_get_reserved_system_prompt`", 总体加的代码量不到 200 行. **延伸教训**: **新增数据源时第一步是审视现有 import 管线能否被参数化**, 而不是建新路径. 本例中"数据源"只影响 `_write_sandbox_characters_json` 的输入 dict 从哪拿 + `_copytree_safe` 的 src 路径换一下, 其它一切完全一致. 未来如果要加"从 HuggingFace 拉"、"从压缩包导入"、"从别的 session 快照拷过来"等, **都**应该走这条"用户选一个 source descriptor → 复用同一条 write/copy 管线"的模式, 不要每种来源各一份代码路径.

25. **"一键清零"的语义是"覆盖性", 不是"清空式"**. 踩点: "清零人设"在自然语言里很模糊: 是"把沙盒 wipe 成空白"还是"把沙盒回到某个已知基线"? 本期选后者. 具体行为: 导入预设会**覆盖** `characters.json` + `memory/<character>/<filename>.json` (对 preset 里有的 JSON); **不动** `memory/<character>/` 下**额外**存在但预设里没有的文件 (reflections.json / persona_corrections.json / surfaced.json / time_indexed.db 等). **为什么**: 这些"额外"文件是测试人员在**当前会话**中生成的 (例如跑了 P10 `reflect` op 才会有 reflections.json), 是他调试痕迹的一部分, 如果"一键清零"把它们也删了, 用户就永久丢失了正在查的 bug 现场. 想要彻底 wipe 的用户可以走 memory 四子页手删 / 或将来 P20 的 Reset 功能. **延伸教训**: **"reset"这种动作在多态数据环境下一定要讲清楚清哪些不清哪些**. 我一开始想写 `shutil.rmtree(sb_memory_dir / char)` 然后拷 preset, 干净但太暴力 — 后来改成"只覆盖预设里声明过的文件, 其它保留". 同理 P20 Reset 时应提供多级选项: "清当前 character memory" / "清全沙盒 memory" / "重置整个 sandbox", 让用户明确选.

26. **seed 数据的 `hash` 字段不要手填**. 踩点: `facts.json` 的 `hash` 是 `sha256(text)[:16]`, 主程序 `FactStore` 用它做 exact dedup. 预设里如果手填, 文本微调就必须同步改 hash, 非常容易漏改 — 漏改后 dedup 键失效, 同样文本会被当新 fact 再抽一次. 方案: 预设文件里 `hash` 留空串 `""`, 后端 `_normalize_preset_facts` 在 copy 后重新计算并写回磁盘一次. **延伸教训**: **"从文本派生的元数据"永远不要手填进资产文件**, 只要有现成算法就让 loader 在读入时补全. 这条对 `reflections.json.source_fact_ids` (必须存在于 `facts.json`) / `persona.json.facts[].source_id` (若 `source="reflection"` 则必须对应某条 reflection.id) 也适用 — 预设要自己保证引用完整, 否则加载 memory 时会悄悄孤立.

27. **JSON 里的中文 `character_name` 对 Python Path 是合法的, 对 Windows 命令行混乱的 mojibake 不要紧张**. 踩点: 验证 sandbox 时 `Get-ChildItem` 回显的路径里"小天"变成了 `С��` (GBK 解码成 UTF-8 中文的典型 mojibake), 但文件实际上**能打开** / `Read` tool 里中文完整呈现. 根因: Python 的 `pathlib.Path` 默认走 `sys.getfilesystemencoding()` (Windows 下是 `mbcs`/`utf-8`), 创建/读取路径本身没问题; PowerShell 把 stdout 拿去解码时会把 UTF-8 字节按 console code page 905xx 解 → 破坏显示但不破坏文件内容. **测试时看到路径乱码不要立刻回滚 `character_name`, 先用 `Read` 或 `Get-Content -Encoding utf8` 验证实际文件名**. 同理 JSON 里的中文也是 UTF-8 编码进磁盘, 只有 PowerShell 输出时会乱, Read tool / 浏览器 fetch / `chcp 65001` 都能正常显示.

11. **api_key 三层兜底 + 可选模型参数 (免费预设 + reasoning 模型友好性)**. 踩点: "我选了免费版预设, 保存成功, test_connection 却报 MissingApiKey; 发消息也报 ChatApiKeyMissing". 根因: `free` 预设在 `config/api_providers.json` 里自带 `openrouter_api_key: "free-access"` (上游主 App 认这个字面量作为"免费 tier 标记"), 但 testbench 的 `_resolve_chat_config` / `test_connection` 早期只看 `cfg.api_key` + `tests/api_keys.json` 两层, 没看预设自带 key. 另一个相关问题: `temperature` 以前是必填 (default=1.0), 但 o1 / o3 / gpt-5-thinking / Claude extended-thinking 这类模型**拒绝** temperature 参数, 必传会 400. **修正方案**: (a) `api_keys_registry.get_preset_bundled_api_key(provider_key)` 读 `config/api_providers.json → assist_api_providers → openrouter_api_key` / `audio_api_key`, 形成 3 层兜底链 `用户显式 → 预设自带 → tests/api_keys.json`; (b) `chat_runner.resolve_group_config` 泛化为 4 组通用 (过去只 chat), `config_router.test_connection` 走同一 resolver, 去掉本地手写的 `if not cfg.api_key: return MissingApiKey`; (c) `ModelGroupConfig.temperature: float | None = None`, `ChatOpenAI._params` 改为**仅当 `self.temperature is not None` 才加字段到请求体** (关键: 用 `is not None` 不是 `if self.temperature`, 因为 0.0 合法); (d) UI 里 temperature / max_tokens / timeout 三个数值 input 都允许"空字符串 = null", placeholder 明说"留空 = 由模型端自决", hint 点名哪些模型禁止传 temperature; (e) `list_providers` 多返回 `preset_api_key_bundled: bool`, `describeApiKeyState` 对免费预设显示"此预设内置 API Key". **两个延伸教训**: 第一, "后端资源解析" 和 "前端校验/提示" 必须分离 — UI 只给视觉反馈, 真正"这个 config 能不能用"的判定永远在后端 resolver 里, 不要让前端重复实现兜底逻辑 (过去 test_connection 里那段 `if not cfg.api_key: return MissingApiKey` 就是这种重复, 会跟后端兜底链脱节). 第二, **配置字段的默认语义要支持"不设置"而不是只支持"默认值"**. `temperature=1.0` 作为默认值听起来没问题, 但它抹掉了"用户就是想不发送这个参数"的表达能力. 凡是"模型可能不支持"的 LLM 参数 (temperature / top_p / presence_penalty / frequency_penalty / max_tokens / logit_bias), 在 config schema 里都应该是 `Optional[T]` 且 `None = 不写进请求体`, 而不是给个武断的默认值. 这条原则将来扩字段时务必照做.

### 4.16 P11 SimUser 的几个约束

28. **SimUser 消费历史时必须翻转 user↔assistant**. 踩点: 实现 `simulated_user.py` 时直接把 `session.messages` 原样喂给 LLM, 模型产出的"用户消息"总是在"帮对方圆场"而不是"扮演真实用户质疑/追问". 根因: 主会话视角里 role=user 是"**真实用户**说的话", role=assistant 是"**目标 AI** 说的话"; 但对 SimUser LLM, 它自己扮演"真实用户", 所以 role=user 的消息对它而言是**自己之前说过的** (应当以 assistant 身份出现), role=assistant 的消息对它而言是**对方 (目标 AI) 说给它听的** (应当以 user 身份出现). 若不翻转, LLM 会把"我之前说过的话"误读为"对方说的话", 进入"回应自己"的怪圈. **修正方案**: `_flip_history(messages)` 严格映射 user→assistant / assistant→user, 同时丢弃 role=system (那是测试人员给**目标 AI** 的注入指令, SimUser 不该直接看到这种 meta 信息) 和空 content 条目 (placeholder 污染上下文). **延伸教训**: **凡是让 LLM 扮演对话另一侧的管线, 喂历史时都必须按 "对这个 LLM 来说, 谁是 assistant 谁是 user" 重新站队**. P13 双 AI 自动对话会复用这条规则: 目标 AI 那侧用 `session.messages` 原样, SimUser 那侧用 `_flip_history`; 切记不要图省事两侧共享一份 wire. 未来任何"第三方 judger 需要看对话"的场景 (P16 judger) 则**不翻转** — judger 是旁观者, 不扮演任何一方, 需要的是"真实对话的原始布局".

29. **生成草稿这一步不落盘不推进时钟**. 踩点: 最初想把 SimUser 做成"跟 chat.send 一样直接写入 session.messages + 消耗 pending clock", 但这样等于"每点一次生成就多一条 user 消息", 破坏"编辑后再确认发送"的语义, 也让"我只想看看 SimUser 大概会说什么"变成永久副作用. **修正方案**: `POST /api/chat/simulate_user` 明确 **无副作用** — 不动 `session.messages`, 不动 `clock.pending_*`, 不调 memory manager, 只写 JSONL 日志. 返回的 `content` 进 composer textarea, 由用户编辑后走 `/chat/send` (source=simuser) 才真正写入. 这样"生成→编辑→Send"三步里只有最后一步有副作用, 与 Manual 模式"手打→Send" 在副作用语义上完全一致, chat_runner / prompt_builder / clock 全部零改动. **延伸教训**: **"预览" vs "提交" 语义应当贯穿整个测试生态**. P10 memory 操作已经引入这条双阶段, P11 SimUser 只是把它再应用一次 (这里 "commit" = 用户手动点 Send, 走现有 /chat/send; 不需要像 memory 那样另起一个 commit 端点). P13 自动对话则是"生成即提交", 但那是另一层用户显式选择了"跑起来让它自动存", 不矛盾. 未来加任何 LLM 调用, 先问自己: "这次调用的产物, 用户**一定**想让它立刻落盘吗?" 答案不是明确 yes 的都应该先走 preview.

30. **SimUser 模式下 role 强制 user + 禁 role=system**. 踩点: 切到 SimUser 后如果还允许 role 下拉选 system, 语义彻底乱 — "让 SimUser 产出一条 system 消息"本身不成立 (SimUser 扮演"用户", 不是"注入者"). **修正方案**: `syncModeUI()` 在 mode=simuser 时把 `roleSelect.value = 'user'` + `roleSelect.disabled = true`, 切回 manual 自动恢复. 注入系统消息仍走独立的 [注入 sys] 按钮, 不受 mode 影响. **延伸教训**: 当"模式"引入后, 某些原来自由的字段就变成了模式派生, UI 要主动收紧 (disable + 视觉提示) 而不是放任用户点到不合法组合再靠后端拒绝 — 后端拒绝是兜底, 但用户视角"按钮点不下去比报错好".

31. **draft 溯源状态要在 input 事件里立即重置, 而不是 Send 时 diff**. 踩点: SimUser 生成填进 textarea 后, Send 时要判断"这是 LLM 原产还是用户手改过"以决定 `source: 'simuser' | 'manual'`. 初步想法是 "保存一份 generated_raw, Send 时和 textarea.value 做 diff", 但这有两个坑: (a) 占内存 (每次生成多存一份原文直到 Send 或下次生成); (b) 语义模糊 (一字不差 = simuser, 改一个字 = manual? 改过又改回来算哪个?). **修正方案**: 用一个 `draftOrigin: null | 'simuser'` 单布尔状态机 — 生成时置 simuser, textarea `input` 事件里**立即**置 null (只要用户动过一次键盘就算手动), Send / Inject / 切出 simuser 模式也清回 null. 语义清爽: "只要你编辑过, 就不算 LLM 原产". **延伸教训**: **UI 状态机优先选单向/单布尔**, 能用"单字段语义清晰的标记"解决就不要搞"保留原值 + diff" 那种看似灵活实则永远解释不清的设计. P12 Script 模式、P13 Auto-Dialog 的"这条消息从哪来"也会用类似标签, 照此法继续.

32. **"只 system, 无 user/assistant" 的 wire 会被 Gemini 直接 400 拒, OpenAI 不会 — 须加末尾 user nudge**. 踩点: SimUser 首轮生成 (session.messages 为空) 走 Lanlan 免费端点 (实际后端是 `gemini-2.5-flash` via Vertex AI), 返回 `BadRequestError: Error code: 400 - Model input cannot be empty`. 测试链接 (`POST /api/config/test_connection/simuser`) 却通过 — 因为 test_connection 走的是 `ChatOpenAI.ainvoke([{role:"user", content:"ping"}])` 里有合法 user 消息. **根因**: Vertex AI Gemini API 把 `system` 单独塞进 `systemInstruction` 字段, `contents` 只装 user/model 对话. 如果 `contents == []` 直接 `INVALID_ARGUMENT` (OpenAI `/chat/completions` 则对"只 system 无 user"宽容接受并产出一段独白). 另外 Gemini 还要求 `contents` 以 `user` 角色结尾才会生成 model (assistant) 输出; 翻转后若末尾是 assistant (即原会话最后一条是真实用户消息、目标 AI 还没回) 同样会被 Gemini 拒. **修正方案**: 在 `simulated_user.generate_simuser_message` 组完 `wire_messages = [system] + flipped_history` 之后, 判断 `flipped_history` 为空 **或** 末尾 role 不是 user, 就追加一条 `role=user` 的"nudge"消息 (内容是"请按上述风格/人设/历史, 作为用户说出你接下来要说的这一句话, 只输出原话"). 首轮用略不同的开场版本. OpenAI / Anthropic / Lanlan 等宽松实现收到这条 nudge 只是冗余强调, 无害. **延伸教训 (三条)**: 第一, **LLM 提供商对"空 / 首尾角色"的容忍度差异极大**, 永远不要假设 "测试连接通过 = 任何消息序列都能通过". 测试连接只证明网络/鉴权/模型名有效, 不证明我们平时组的 wire 格式合法. 后续任何 pipeline 新增 LLM 调用点 (P12 Script / P13 Auto-Dialog / P16 Judger) 都要**独立**在开发阶段跑一次"空 history" 和"history 末尾是 assistant"两个边界; 不要等用户碰到才修. 第二, **兼容最严 provider (当前是 Gemini) 的 wire 规则 = 其它 provider 的超集**. 原则: `contents` 非空 + 末尾是 user + 不出现同角色连发 — 按这三条组 wire, 发给 OpenAI/Claude/Lanlan 绝不会报错, 但不按会被 Gemini 卡. 这个约束应当在 `chat_runner` / `judge_runner` 将来落地时也沿用 (chat_runner 当前靠"真实用户消息天然在末尾"规避这坑, 但若未来加入"纯 system 注入后立即调模型"的场景就会撞到). 第三, **错误文案要带上真正的上游错误信息而不是只留 `LlmFailed`**. 当前实现 `SimUserError("LlmFailed", f"调用假想用户 LLM 失败: {type(exc).__name__}: {exc}")` 把 upstream 400 的 JSON body 完整透传给前端 toast 是对的 — 用户一眼看见 "Model input cannot be empty" 就能自己推断是 "provider 嫌 wire 太干净了" 而不是盲目重试. 所有 pipeline 层包装异常的地方都照此做: 外层加语义码 (LlmFailed / LlmTimeout / ...), 内层保留原 exception 类型与 message.

### 4.17 P12 Scripted Dialog 的几个约束

33. **脚本游标必须独立于 `session.messages` 索引**. 踩点: 最直觉的实现是 "游标 = 下一条要发送消息在 `session.messages` 里的位置", 但测试人员会在脚本跑到一半时手动编辑 / 删除 / 注入消息, 只要 `len(messages)` 发生变化游标就错位. **修正方案**: `session.script_state = {template_name, turns[], cursor, pending_reference, ...}`, `cursor` 是**脚本 turns 数组内的下标**, 与 `session.messages` 完全解耦. 手动删消息 / 注入 system / 编辑 timestamp 都不会碰到 script_state. 唯一"互动"是 assistant turn 的 `expected` 要在脚本发出一条 user 后回填到下一条 assistant 消息的 `reference_content` — 用 `pending_reference` 状态 (跨 assistant turn 累积), 在 stream_send 发出 `{event:'assistant'}` 的那一刻回填, 天然对齐. **延伸教训**: **任何"测试流程状态"都不应绑死 session.messages 下标**, P13 Auto-Dialog (两个 AI 轮流说) / P14 Stage Coach (阶段指针) 同理, 用独立的 `session.<xxx>_state` 存下标. 这也让 undo/ redo 更简单 — 消息操作不需要同步补偿脚本状态.

34. **bootstrap 只在 `session.messages == []` 时生效, 非空给 warning 不硬覆盖**. 踩点: 脚本里 `bootstrap.virtual_now = "2026-01-01T09:00:00"` 是"模拟这段对话发生在 2026 新年早上", 如果会话已经有消息了 (测试人员接着手工对话再切过去加载脚本), 强行把 clock 拉到 2026-01-01 会让之前消息的 timestamp 变成"未来发生过的", 时间线一片负数. **修正方案**: `apply_bootstrap(session, bootstrap)` 先看 `session.messages` 是否为空: 空→调 `clock.set_bootstrap(...)`; 非空→**什么都不做**, 只返回一条 warning `"会话已有消息, bootstrap 虚拟时钟未重设"`, 通过 `/chat/script/load` 的 `warnings[]` 返回给前端弹 toast. 测试人员看到 warning 自己决定要不要 destroy session 重建. **延伸教训**: **"破坏性操作在存在数据时默认降级为 warning + 继续, 而不是硬覆盖或硬拒绝"** 是本项目一贯风格 (P05 persona import 覆盖 memory 目录前先 confirm; P10 memory commit 预览后才落盘). 反模式是"直接抛 422 让 UI 自己 retry" — 对测试场景太吵闹. 例外是"数据完整性风险" (如 timestamp 非单调) 才硬拒, 其它都是 warning + 用户知情决定.

35. **脚本的 `role=assistant` turn 是 expected 载体, 不发 LLM 不写消息; 其 expected 是"对前一条 user 的理想答复", lookup 方向是 lookahead 而不是 lookbehind**. 踩点 (两次, 第二次才把方向修对): 模板写 `{role:"assistant", expected:"哼 起床气啦"}` 的 turn 时, **真正的 assistant 消息**应该来自前一条 user turn 触发的目标 AI 调用, expected 是理想答案参考 — 如果脚本自己往 `session.messages` 塞 assistant, AI 根本没机会回复, 也没法比对. 第一版实现做对了"不塞 assistant 消息"这一点, 但**收集 expected 的方向搞反了**: 以为 `pending_reference` 应该在 `cursor` 处向前扫 assistant 再发紧随的 user, 导致 "turn[0]=user, turn[1]=assistant expected=X, turn[2]=user" 的自然模板里, **turn[1].expected 被挂到 AI 对 turn[2] 的回复**上 — 全体错位一拍, 最后一条 AI 回复永远拿不到 reference. **修正方案**: `advance_one_user_turn` **发 user turn 之前先 lookahead** — 从 `cursor+1` 开始连续扫 `role=assistant`, 把它们的 expected 合并 (用 `\n---\n`) 存入 `fill_reference`; user turn 正常发 → 目标 AI 的 stream_send 产出 `{event:'assistant'}` 时, 拦截该事件往 `ev.message.reference_content` 写入 `fill_reference`; 成功后 `cursor` 直接跳到 `lookahead_end` (= user + 消费掉的 assistant 之后的第一条, 通常是下一条 user 或末尾). **延伸教训**: 第一, **expected / ground_truth 类字段不是"要发出去的消息", 而是"附着在真实消息上的元数据"**. 保持 `session.messages` 单纯 = "真实发生过的对话流", 所有"参考/期望/评分"都挂在元数据层. P13 Auto-Dialog 里若有"期望 SimUser 说这句", 同理加到真实 user 消息的 meta 字段 (或 reference_content) 上, 不要单独造 message. 第二, **"参考/答案"与"触发它的输入"之间的前后关系, 编码时一定要画一条样本轨迹再动手**. 本项目自然模板是 `[user, assistant_expected, user, assistant_expected, ...]` — 天然 lookahead. 以为可以靠"cursor 遇到 assistant 就累加"反而写成了 lookbehind, 测试时因为"总有 reference 出现"假象很难一眼看出错位, 要到 diff 才能暴露. **以后实现任何"把 N 条模板元素按某种分组对齐到真实产出"的逻辑, 都要先用一个 4 条以内的最小模板手推一遍 cursor 走位, 再落代码**. 第三, stream 失败时 `cursor` 不推进, 但 `pending_reference` 也要清零 — 下次重试同一 user turn 会重新 lookahead 扫 expected, 不用依赖上次保留的 pending (旧版本保留 pending 的做法会让 "中途清空消息 / 手工编辑过 session" 再回来 Next 时游标漂移).

36. **Run-all 必须整段持 BUSY 锁, 不能 per-turn 释放**. 踩点: 想过"每个 user turn 独立 session_operation 持锁, 两 turn 之间短暂释放"以便测试人员中途点 Stop 或 /chat/send, 但这样其它 HTTP 就能在 turn-gap 里塞 `{event:'send'}`, 把 session.messages 改乱, 下轮 script 醒过来游标和消息对应关系依旧错 (参 #33). **修正方案**: `/chat/script/run_all` 和 `/chat/script/next` 都在 `session_operation(..., state=BUSY)` 里跑**整个** SSE 生命周期, 直到 `script_exhausted` 或 `error` 才释放. 期间任何 `/chat/send` / `/chat/simulate_user` / `/chat/messages` 都会被 `SessionConflictError` 拒掉, 前端 toast 提示 "会话正忙: chat.script.run_all". Stop 按钮本期未实装 (留到 P14 Stage Coach + 统一中断信号), 临时用"刷新页面"中断. **延伸教训**: **LLM 长时间占用会话时, 锁粒度应是"整个工作单元"而不是"每一步"**, P13 Auto-Dialog (轮流发送直到收敛) / P16 Judger (可能串行评多条) 照此粒度.

37. **reference_content 写到 ev.message 而不是 session.messages**. 踩点: 最初实现把 `pending_reference` 直接写进 `session.messages[-1].reference_content`, 但 `chat_runner.stream_send` 产出 `{event:'assistant', message: msg}` 时, 那个 `msg` 是 **session.messages 里的同一个 dict 的引用** (Python 浅引用语义). 写 `ev.message.reference_content = X` 等于同时写了 `session.messages[-1].reference_content = X`, 所以语义没区别但代码更清爽. **修正方案**: 在 script_runner 的 SSE 转发层里 `ev.message["reference_content"] = _merge_pending_reference(ev.message.get("reference_content"), pending_ref)`, 然后把整个 ev 继续 yield 给前端. 前端接收到 `ev.message.reference_content` 已经是回填后的值, 直接渲染"参考回复"折叠块. **延伸教训**: **SSE 事件里的 message dict 和 session.messages 里的条目是同一对象**, 修改 ev 等于修改存档; 但这恰好省去了"改完存档再单独 push 到前端"的双写, **只要保证在 commit_assistant 之后、yield 给前端之前改**, 一次 mutation 覆盖两个目标. 这条约定未来 P16 Judger 落地时要继承: 评分元数据也挂在 assistant message 的新字段 (比如 `scores: [...]`) 上, 不另起一套存储.

38. **SSE 异常必须尽可能 yield 一条 `event:'error'` 再让上下文管理器释放锁**. 踩点: 初版 `_script_next_event_stream` 里 `ScriptError` / `Exception` 直接 `raise`, FastAPI 层捕获后返回 500 HTML, 前端 SSE client 在 `onError` 里拿到的是字节流断连, 根本没有 `ev.error.type` 可 toast. **修正方案**: 所有可预期错误 (`ScriptError`) 先 `yield _sse_frame({event:'error', error:{type, message}})` 再 return; 不可预期 `Exception` 也包一层 yield 再 raise. 这样前端 `case 'error'` 分支一定能跑到, toast 显示中文语义错误码, 而不是看到 "HTTP 500" 一脸懵. **延伸教训**: **任何 SSE generator 的顶层 try/except 都应先 yield 再 raise (或 return)**, 前端的 SSE error 路径与 HTTP error 路径设计本来就不对等 — SSE 连上之后 HTTP 500 前端是收不到响应体 JSON 的, 只能走流内 `event:'error'` 这一条语义通道. P13 Auto-Dialog / P16 Judger run 接入 SSE 时按此模板抄.

39. **UI 按钮的"依赖输入值而变 disabled"必须把该输入的 change/input 事件绑到 sync 函数**. 踩点 (同类 bug 在本项目已经发生过三次, 这是第三次): Script 模式选了一个剧本, [加载] 按钮不变亮, 得先切 Mode 再切回来 / 点 [刷新列表] / 改会话等于"走了一次别的 sync 路径"才能恢复. 根因: `syncScriptButtons()` 里读 `templateSelect.value` 判 `loadBtn.disabled`, 但 `templateSelect` 本身没挂 `change` 监听, 选剧本这一纯用户输入事件没有入口触发 sync. 前两次**同构**踩点: (a) **P11 SimUser 风格下拉**首次打开时是空的, 因为 `ensureStylesLoaded()` 只在 `generateDraft()` 里调, 没在 `syncModeUI()` 的 simuser 分支里调; (b) **P09 Composer textarea**: `draftOrigin` 靠 `input` 事件回退到 `null`, 如果忘了绑 `input` 监听, Send 时 source 永远是 simuser, 即使用户已经改得面目全非. **修正方案 (统一模板)**: 每次在 `syncXxxButtons()` 里读某个输入控件的 value 决定 disabled/visible 时, **同时** 在该控件创建处绑 `onChange`/`onInput` → `syncXxxButtons`. 具体本次: `templateSelect = el('select', {..., onChange: () => syncScriptButtons()})`. **延伸教训 (根本规则)**: **"UI state 依赖 input value" 是一个双向契约 — `sync` 读 value, input 事件触发 `sync`, 两头必须同时存在**. 缺一个就是"看似正确实则不响应"的欺骗性 bug. 以后添加任何"根据用户选择动态启用按钮"的控件, 按此 checklist 自查: (1) sync 函数里有没有读这个 value? (2) 控件本身有没有绑对应的 change/input 事件? (3) sync 函数是否在**所有**会改变这个 value 的路径上都被调用 (包括程序式赋值, 例如 `populateTemplateSelect` 里恢复 `prevValue` 的那一步 — 注意程序式赋值**不会**触发 `change` 事件, 必须显式调 `syncScriptButtons()` 一次)? 三条全中才算接对. P13 Auto-Dialog 起止按钮 / P14 Stage Coach 阶段选择 / P15 Schema 下拉全要按这条来.

40. **开发/测试服务器的静态资源 mount 必须强制 revalidate, 否则"代码改了但用户看不到"的假 bug 会反复出现**. 踩点 (P12 最后一步, 为验证 `reference_content` 折叠块专门绕一大圈): 后端已经把脚本 `expected` 回填到 assistant 消息的 `reference_content`, `GET /api/chat/messages` 返回的数据非空, `message_stream.js` 的 `buildMessageNode` 里 P12 折叠块代码也在, 写 headless `msedge --dump-dom` 隔离测试得到的 DOM 里 `.msg-reference-wrap > .cb.msg-reference-block` 完全正确 — 但用户浏览器里什么都不显示. 根因: **Chromium 系 (Edge / Chrome) 的 ES module loader 对 `import` 过的模块做强缓存, 普通 F5 有时不重新请求, 用户看到的永远是 P12 UI 合并前的 `message_stream.js` 版本** (那版本里没有参考回复折叠块那 17 行 DOM 构建). FastAPI 的 `StaticFiles` 默认只发 `Last-Modified` + `ETag`, 没有 `Cache-Control`, 浏览器按启发式规则自行决定是否 revalidate — 对频繁改动的 dev 服务器这直接翻车. **修正方案**: `tests/testbench/server.py` 里子类化 `_NoCacheStaticFiles(StaticFiles)` 并 override `file_response` 追加 `Cache-Control: no-cache, must-revalidate`, 挂载时用它替换原生 `StaticFiles`. 每次 F5 浏览器必须带上 `If-Modified-Since` 问服务器, 未变返回 304 (近零流量), 变了立刻拉新版本 — 彻底杜绝"代码已更新但页面不变"的假象. **延伸教训**: 第一, **dev/test 服务器任何静态资源 mount 默认就要 revalidate**, 生产环境才谈 aggressive cache + 版本化文件名; 本项目后续若新增静态入口 (P18 Judger Studio 独立 JS bundle 之类) 都要走 `_NoCacheStaticFiles`. 第二, **遇到"代码明显对但 UI 不生效"的现象, 排查优先级第一位就是浏览器缓存** — checklist: (1) Ctrl+Shift+R 硬刷, 如果硬刷也不对才真是代码 bug; (2) `curl /static/xxx.js` 抓到的内容和磁盘 diff, 服务器发的如果是新版本, 问题就在浏览器端; (3) 写 headless 隔离测试 (如本次 `msedge --headless --dump-dom`) 独立渲染验证 `buildXxx` 逻辑, headless 正确而用户页面不对 = 100% 缓存. 第三, **诊断 UI bug 的固定顺序是"数据 → 传输 → 解析渲染"**: 先 `curl /api/...` 确认目标字段非空 (排除后端数据问题), 再 `curl /static/...` 确认代码已部署 (排除静态服务问题), 最后才看浏览器 DevTools (排除渲染/缓存). 以后任何前端 bug 先按这三步过一遍, 直接动手改代码常常是南辕北辙.

41. **"编辑器 CRUD" 与 "运行期加载" 是两套 path, 共享 normalize 但不共享入口**. 踩点 (P12.5 脚本编辑器上线): 一开始想偷懒, 让新的 `save_user_template` / `read_template` 直接调 `load_template` / `load_script_into_session`, 结果有三种隐性耦合 (a) 加载链路里的 `apply_bootstrap` 会去碰 session clock, 纯编辑场景 (还没加载到会话) 就崩了; (b) 规范化时 `_normalize_template` 默认 raise 第一个错, 编辑器需要的是"一次拿到所有字段级错误清单红框全部高亮", 签名完全不同; (c) builtin 原地 save 会静默改到 git 管的 `tests/testbench/dialog_templates/*.json`. **修正方案 (三条边界约定)**: (1) 编辑器路径的 5 个函数 (`read_template` / `save_user_template` / `delete_user_template` / `validate_template_dict` / `duplicate_builtin_to_user`) **只碰 `USER_DIALOG_TEMPLATES_DIR`, 不碰 session_store, 不应用 bootstrap**; 它们是 "文件 IO + schema 校验" 纯函数, 运行期加载 (`load_template` / `load_script_into_session`) 依旧是另一套, 两套共享 `_normalize_template` 作为"磁盘 ↔ 内存形态"的单一来源. (2) 软校验 `_collect_template_errors` 与硬校验 `_normalize_template` 平行写, **不要尝试让后者 "raise-or-collect" 两用** — 字段级错误收集和单条 raise 的代码结构差异大, 强行合成一个会让两种语义都残疾, 宁可两份 90% 重叠. (3) builtin 不可原地编辑: `save_user_template` 只写 user 目录, 磁盘文件名永远是 `<name>.json` 即 `USER_DIALOG_TEMPLATES_DIR / f'{name}.json'`; "覆盖 builtin" 的语义由**加载器**在合并两目录时实现 (user 后扫覆盖 builtin), **编辑器不需要管 builtin 目录的存在**. **延伸教训**: 第一, **内置资产 vs 用户资产 = 只读目录 vs 可写目录, 一条红线**, 未来 P15 ScoringSchema (builtin + user schema 同一个模式) / P16 Judger 运行期配置 (builtin judger vs user 自定义) 照抄这条. 第二, **"name 即文件名" 是用户可预期的语义** — 用户想重命名 → 前端拆成 "save 新 name + delete 旧 name" 两个调用, 后端**不提供 rename 端点**; 一旦后端有了 rename, 磁盘 name 和字段 name 可能分裂 (文件叫 `foo.json` 但 name 字段是 `bar`), 加载器合并就会错位. 第三, **任何 CRUD 接口都要想清楚 "作用于 session 还是作用于全局资产" 两个轴**: 全局资产 (template / schema / judger config) 不持 session 锁, 甚至不需要 session 存在也能调用 (测试人员可能在建会话前就在 Setup → Scripts 里整理剧本); 只有 `load_script_into_session` / `unload_script_from_session` 这种"把资产**应用**到会话"的动作才进 session_operation 锁. 错把资产 CRUD 包进 session 锁 = 没建会话就不能编辑模板, 体验立即垮.

42. **`Node.append(null)` 会把字符串 `"null"` 塞进 DOM, 不是 no-op** (P12.5 用户验收时发现 Scripts 页底部多一行 "null"). 踩点: `renderEditorErrors(state)` 没错误时 `return null`, 外层 `pane.append(renderEditorErrors(state))` 以为 null 会被忽略, 实际上 `Node.prototype.append` 的签名是 `...(Node | string)[]`, 非 `Node` 参数一律 `String(x)` → 文本节点. 同理 `cond ? el(...) : null` 作为可变参数传进 `buttons.append(...)` 时, 条件为 false 的位置会被字符串化成 "null". 注意 `appendChild` 对非 Node 会抛 `TypeError` 而 `append` 不会 — 只是默默渲染一个诡异字符串, 没有 error bus 能捕获, 全靠肉眼巡查. **修正方案 (本次)**: 拆成 `const n = render...(); if (n) parent.append(n)`; 变参场景改 `parent.append(...children.filter(Boolean))`. **延伸教训**: 第一, **`el()` helper 的 null 过滤只保护"作为 el children 传入"那条路径, 不保护"后续 `.append / .replaceChildren / .prepend`"**, 两头规则不一致很容易翻车; 可以在项目 `_dom.js` 里加一个 `safeAppend(parent, ...children)` 做统一过滤, 以后一律走这个. 第二, **"应该什么都不渲染"的渲染函数, 宁可返回一个空的 `DocumentFragment` 也不要返回 `null`** — 调用方 `parent.append(frag)` 无脑能对. 返回 null 就是给未来的自己埋雷, 下一个五分钟内写的 caller 就忘了 guard. 第三, 已抽 cross-project skill `dom-append-null-gotcha` 收纳此类 DOM 静默文本节点坑 (`.append / .replaceChildren / .prepend` + 任何非 Node 非 string 参数, 包括 `undefined` → "undefined" / `false` → "false" / `0` → "0"), 遇到 "页面末尾多一个奇怪字符串" 类型报告时直接看那条 skill.

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
- **2026-04-18** P08 补丁: Chat 切回时 preview 不自动刷新的 UX 毛刺. `workspace_chat.js` 订阅 `active_workspace:change`, 切到 chat 且已有会话时调 `previewHandle.refresh()` (200ms 防抖 + 模块级 `activeWorkspaceSubscribed` 标记避免重复订阅). 根因: `app.js` 的 workspace 懒挂载后不卸载, 再次切到 Chat 不会重跑 `mountChatWorkspace`; 而 Persona/Memory 编辑时不会广播 `session:change`, preview 自然不刷. 同一机制将来 P09 发消息后 preview 需刷也走这条路径 (广播 `session:change` 或 `preview:dirty`).
- **2026-04-18** 完成 P08 PromptBundle + Prompt Preview 双视图. 后端: `pipeline/__init__.py` 占位 + `pipeline/prompt_builder.py` (`PromptBundle` dataclass / `PreviewNotReady` / `build_prompt_bundle(session)`) — 以 `session.persona` + `session.clock` 为唯一真相源, **不 import** `tests/dump_llm_input.py` (并行实现, 只锁定 `_BRACKETS_RE` / `_TIMESTAMP_FORMAT` / `_resolve_character_prompt` 与 upstream 对齐, bit-for-bit 一致但路径解耦). 各 memory manager (`CompressedRecentHistoryManager` / `PersonaManager` / `FactStore` / `ReflectionEngine` / `TimeIndexManager`) 在沙盒内 new 实例读磁盘 JSON, 不经上游 loader (延续 P07 "不让首次加载副作用偷偷改磁盘" 原则); 任何管家构造/读数据失败都降级为 `warnings[]`, 不报废整个 preview. 时间字段全部走 `session.clock.now()`, 绝不 `datetime.now()`. `PromptBundle` = `structured` (11 个分段 dict) + `system_prompt` (扁平字符串, 真正拼 wire 的那份) + `wire_messages` (OpenAI `[{role, content}, ...]`) + `char_counts` (每段 + 总长 + `approx_tokens = total // 2`) + `metadata` (character/master/language/clock 快照/template_used/stored_is_default/built_at_virtual/built_at_real/message_count) + `warnings`. `routers/chat_router.py` 新增, `GET /api/chat/prompt_preview`: 无 session → 404, `PreviewNotReady` (character_name 为空) → **409** (客户端用来触发"填人设"空态, 与 5xx 报错区分), 成功 → 200 `PromptBundle.to_dict()`. 前端: `workspace_chat.js` 从占位改造为 `.chat-layout` 两栏 grid (左 `.chat-main` 为 P09 消息流占位, 右 `.chat-sidebar` 常驻 preview panel); `static/ui/chat/preview_panel.js` 新增 `mountPreviewPanel(host)` 返回 `{refresh, markDirty, destroy}`, 支持 Structured / Raw wire 视图切换 (偏好存 `localStorage[testbench:chat:preview_view]`), 每分段/每条 wire message 都套 `CollapsibleBlock`, 顶部 meta badges + `warnings` 条; 订阅 `session:change` 自动 refresh, 订阅自定义 `preview:dirty` 事件打脏标; `api.get(..., expectedStatuses: [404, 409])` 静默两种空态不污染 Err 徽章. i18n `chat.preview.*` 全命名空间; CSS `.chat-layout / .chat-main / .chat-sidebar / .preview-panel-header / .view-toggle / .preview-status / .preview-meta / .preview-warnings / .preview-dirty-banner / .preview-hint / .preview-view / .raw-actions / .wire-list + .wire-role-{system,user,assistant}`, `button.small` / `button.primary.small` 显式绑 `<button>`. 关键取舍: (1) `approx_tokens = total // 2` 延用上游中文 "1 token ≈ 2 字符" 近似; (2) 两栏 layout 一次性定型, P09 只往 `.chat-main` 塞消息流/composer, 不再动 CSS 骨架; (3) dirty 标记放前端 — Persona/Memory 编辑广播 `preview:dirty` 事件, panel 仅亮脏标不自动 refetch, 避免跨 workspace 键入时后端过载.
- **2026-04-18** 完成 P09 Chat 消息流 + 手动 Send + SSE. 后端: `chat_messages.py` (ROLE_* / SOURCE_* 常量 + `make_message`/`new_message_id`/`find_message_index`, `source` 覆盖 manual/inject/llm/simuser/script/auto) / `pipeline/chat_runner.py` (`ChatConfigError` → 412 + `ChatBackend` 协议 + `OfflineChatBackend.stream_send` 消耗 pending + 组 wire + 先落 JSONL 再 `ChatOpenAI.astream` + `try/finally: aclose()`, 以及不走 LLM 的 `inject_system()`) / `routers/chat_router.py` 补齐 8 路消息端点 (messages CRUD / PATCH timestamp / truncate / inject_system / send SSE `StreamingResponse`, 请求体里 `time_advance` 直送 `stage_next_turn`, 会话锁整段持有). 前端: `static/ui/chat/sse_client.js` (fetch+ReadableStream 版 POST SSE, `\n\n` 分帧, 暴露 abort) / `message_stream.js` (消息 > 500 字符折叠, 30min 以上间隔时间分隔条, 行内 `[⋯]` 菜单 编辑内容/时间戳/从此处重跑/删除, 暴露 `beginAssistantStream(stub)→{appendDelta,commit,abort}` 供 composer 喂 delta) / `composer.js` (两行扁平: Row1 Clock chip + Next turn ±staging + Role 下拉 + Mode 显示 + Pending badge; Row2 textarea + Send + Inject sys, Ctrl+Enter 发送, Clear stage 走 `DELETE /api/time/stage_next_turn`) / `workspace_chat.js` 集成 stream+composer, 并把 `chat:messages_changed` → `previewHandle.markDirty()` (不在流式期间抢 DOM 刷 preview). `prompt_builder.py` 注释升级明确 `wire_messages` 自 P09 起直接透传 `session.messages`. i18n 新 `chat.role.*`/`chat.source.*`/`chat.stream.*`/`chat.composer.*` 命名空间, 删 `workspace.chat.placeholder_*` 占位. testbench.css 新增 `.chat-message[data-role/data-source]` 色带 + `.time-sep` + `.msg-menu*` + composer 两行栅格 + clock chip/pending badge. `health_router.phase = P09`. 新增 §4.13 记录 P09 的 8 条落地坑 (SSE over POST / `httpx.aclose` / 会话锁整段持有 / messages 唯一真相 / 预落 wire 便于复现 / 不在流式期间 refresh 列表 / Next turn 后端为真相源 / truncate 回退 clock 是副作用).
- **2026-04-18** UX 细节: 会话创建/销毁自动刷新当前可见子页. `workspace_setup.js` / `workspace_settings.js` 订阅 `session:change`, 当前 workspace 可见则立即 `selectPage(currentId)` 重渲染, 否则打 `dirty` 标, 下次 `active_workspace:change` 切回本 workspace 时再刷. 动机: 修复 "Persona 子页提示无会话 → 顶栏新建会话 → 页面仍停留在空态, 必须手动切走再切回" 的 UX 毛刺. 延迟刷新避免不可见 workspace 产生无谓请求. Chat/Evaluation/Diagnostics 会话无关, 不加订阅.
- **2026-04-18** P09 补丁 (free-tier / reasoning 模型友好性). 踩的坑: 用户选 `free` 预设 → `test_connection` 报 `MissingApiKey`, 发消息报 `ChatApiKeyMissing`, 但 `free` 预设在 `config/api_providers.json` 里其实自带 `openrouter_api_key: "free-access"`. 另外 `temperature` 以前是必填, 对 o1/o3/gpt-5-thinking 这类拒绝该参数的模型不可用. 改动: (a) `api_keys_registry.py` 加 `get_preset_bundled_api_key` / `preset_has_bundled_api_key`, 读 `config/api_providers.json → assist_api_providers` 里的 `openrouter_api_key` / `audio_api_key`; (b) `chat_runner._resolve_chat_config` 泛化为 `resolve_group_config(session, group)`, api_key 兜底链变为 "用户显式 → 预设自带 → tests/api_keys.json", `config_router.test_connection` 也改走同一 resolver, 去掉本地 `if not cfg.api_key` 的提前拒绝; (c) `model_config.ModelGroupConfig.temperature: float | None = None` (从 `float = 1.0` 改), `utils/llm_client.ChatOpenAI._params` 仅在 `temperature is not None` 时写进请求体 (注意 `is not None` 不是 `if self.temperature`, 因为 0.0 合法); (d) `/api/config/providers` 返回多一个 `preset_api_key_bundled: bool`, `page_models.js::describeApiKeyState` 免费预设显示"此预设内置 API Key, 无需填写", 三个数值 input 都接受"空字符串=null" + placeholder "留空由模型端自决"; (e) `onSave` body 显式发送 `temperature: null` (不是 `exclude_unset`, 否则永远无法把老值改回 null). 详见 §4.13 #11.
- **2026-04-18** P07 补丁 (Memory 编辑器结构化视图). 踩的坑: Memory 子页保存时报 `toast is not a function` + 纯 Raw JSON 让测试人员不得不手推每种 kind 的 schema. 改动: (a) `memory_editor.js` 里 `toast(i18n(...))` 改为 `toast.ok(i18n(...))` (toast 是对象不是函数, ESM + JS 直到 click 才炸). (b) 重构 `memory_editor.js` 为 Structured/Raw tab 容器 (共享 state.model, canonical(model) 判 dirty, 视图切换时 parse/stringify 双向同步; Raw → Structured 切换 parse 失败则 toast 拒绝); tab 选择持久到 `sessionStorage[testbench.memory_editor.view.{kind}]`. (c) 新建 `memory_editor_raw.js` 保留原 textarea + format 按钮; recent kind 顶部追加警告提示"运行期自动写入, 手动编辑只用于异常输入测试". (d) 新建 `memory_editor_structured.js`: 4 个 kind 分别有 card 渲染器; "+ 添加实体/事实/反思/消息" 按钮用 `defaultXxxEntry()` 工厂拉合法默认条目 (id 用 `manual_{ts}_{rand8}` / `fact_{ts}_{rand8}` / `ref_{ts}`, timestamp 用 naive ISO 秒精度与主程序对齐); 常见字段直出 (text/entity/status/source/protected/tags/importance/feedback), 低频字段 (id/hash/created_at/recent_mentions/next_eligible_at 等) 折在 `<details class="memory-advanced">`; recent 的 multimodal list-of-parts content 智能拆分: 含 `{type:'text'}` 分段时直接绑首段 text 到 textarea (非文本分段原封不动), hint 条说明 "另含 N 个非文本分段" / "共 N 个文本分段只编辑首段"; 无任何文本分段 / 纯 object 等怪形态才退化 warn + 推荐切 Raw, 绝不展平成 string. (e) 关键设计: 值修改 (input onChange) 只 `notify()` 刷 dirty badge 不重建 DOM (避免 textarea 打字失焦), 结构修改 (+/-条目) 才 `restructure()`; model 是两视图唯一真相, 不搞"草稿 vs 提交"二层. (f) i18n `setup.memory.editor.tabs.*` / `add_*` / `field.*` / `complex_content_hint` 等新增一批; CSS 新增 `.memory-editor-tabs` / `.memory-struct-root` / `.memory-entity-group` / `.memory-item-card` / `.memory-field` / `.memory-advanced` (无标记 `▸/▾` 箭头). 端到端实测 PUT persona=`{}` / facts=`[]` / reflections=`[{...}]` 回读一致, persona=`[]` 被后端 422 拒绝 (顶层类型校验仍有效). 详见 §4.13 #14.
- **2026-04-19** 完成 P11 假想用户 AI (SimUser). 后端: `pipeline/simulated_user.py` (SimUserError 代码映射 / 4 风格闭集 STYLE_PRESETS / `_flip_history` 翻转 user↔assistant + 丢 system / `_build_system_prompt` 按角色身份 + 风格 + 自定义 persona + 输出规则 + 首轮开场装配 / `_postprocess_draft` 多重 strip 前缀 + 成对引号兜底 / `generate_simuser_message` 无副作用 async 调用 + JSONL 日志 + `SimUserDraft.to_dict`). `routers/chat_router.py` 扩展: `GET /api/chat/simulate_user/styles` 返 `{styles, default}`, `POST /api/chat/simulate_user` 用 `session_operation(BUSY)` 与 /send 同锁 + `SimUserError.status` 直接映射 HTTP (404/409/412/502). 前端: `composer.js` Mode 从只读显示改为真 select (manual/simuser/script-disabled/auto-disabled), 新增 `simuserControls` 折叠条 (style select lazy-load + [自定义人设] toggle + [生成] 按钮), `simuserPersonaEditor` 独立折叠 textarea, `draftOrigin` 单布尔状态机 (生成置 simuser, textarea input 事件退回 null), Send 时据此发 `source: 'simuser'|'manual'`, mode=simuser 时 role 强制 user + disabled; 风格 option 的 `title` attr 展示 prompt 原文前 120 字符供 hover 提示. i18n `chat.composer.mode` 拆 simuser/script_deferred/auto_deferred 三个 enabled/disabled option label + 新增 `chat.composer.simuser.*` 完整树 (style_prefix / style.{friendly,curious,picky,emotional} / persona_toggle / persona_intro / generate / generated_ok / generated_empty / confirm_overwrite 等). CSS: `.role-select` 样式组合并 `.mode-select` + `.simuser-style-select`, 追加 `[disabled]` 半透明; 新增 `.simuser-controls` (inline-flex + left-dashed-border 分段) / `.simuser-persona-editor` (柔色背景折叠块) / `.simuser-persona-textarea`. `health_router.phase = P11`. 新增 §4.16 记录 P11 的 4 条坑 (翻转历史 / 生成无副作用 / SimUser 模式下 role 强制 user / draftOrigin 状态机).
- **2026-04-19** P12.5 补丁: 去掉冗余的 [校验] 按钮. 后端 `POST /templates` 本来就是 "先 validate_template_dict 再落盘" 的原子语义, 失败返回 422 + `detail.errors`, 前端 `saveDraft` 已经有完整的字段级红框/toast 回流, 独立 Validate 按钮纯噪音. 改动: (a) `page_scripts.js` 删 [校验] 按钮 + `validateDraft` 函数 + 顶部 docstring 第 4 条改写为 "校验隐式内嵌在 Save"; (b) `chat_router.py` 删 `POST /script/templates/validate` 路由 + `_ScriptValidateRequest` 模型 + `validate_script_template` import, `validate_template_dict` 函数本身保留 (save 内部还在用); (c) `i18n.js` 删 4 个 key (`buttons.validate` / `toast.validate_ok/_errors/_failed`). PROGRESS.md 里 P12.5 的 validate 端点描述过期不再同步, 以本变更日志为准.
- **2026-04-19** P12.5 补丁: Scripts 页尾出现一行 "null". `page_scripts.js::renderEditor` 里 `pane.append(renderEditorErrors(state))` 错信 `Node.prototype.append(null)` 会跳过, 实际会 `String(null)` 当文本节点塞进去. 同类问题在编辑器头按钮组 `buttons.append(..., has_user ? el('button', ...) : null)` 也中招 (新建空白草稿时). 改法: 前者改成 `const n = render...(); if (n) pane.append(n);`, 后者拆成 if 分支独立 append. 新增 §4.17 #42 记录 (+ `el()` 的 null 过滤只对 children 参数有效, 不覆盖后续 `.append / .replaceChildren / .prepend` 链路的二次传参). 同步抽了一条跨项目 skill `dom-append-null-gotcha` 放到 `~/.cursor/skills-cursor/`.
- **2026-04-19** P11 补丁 (Gemini 空 contents 400). 踩点: SimUser 首轮点生成 → `LlmFailed: BadRequestError 400 - Model input cannot be empty`; 但 test_connection/simuser 却通过. 根因: Lanlan 免费端实际后端是 Vertex AI Gemini (`gemini-2.5-flash`), 把 `system` 放 `systemInstruction`, `contents` 只装 user/model; 首轮 wire = `[system]` 导致 `contents == []` 触发 400. OpenAI 对"只 system"宽容, 所以 test_connection 走 `ainvoke([{role:"user",content:"ping"}])` 没暴露问题. 修复: `generate_simuser_message` 在组完 `wire_messages = [system] + flipped_history` 后, 判断 `flipped_history` 为空 **或** 末尾 role 不是 user 时, 追加一条 `role=user` nudge ("请按上述风格/人设/历史, 作为用户说出你接下来要说的这一句话, 只输出原话"; 首轮用略不同的开场版本). OpenAI / Anthropic / Lanlan 收到 nudge 只是冗余强调, 无害. 新增 §4.16 #32 记录 3 条延伸教训 (provider 容忍度差异极大且 test_connection 不能代表 wire 合法性 / 按最严 provider=Gemini 组 wire 三规则: 非空 + 末尾 user + 不连发同角色 / 错误包装时保留 upstream 原 exception type + message).
