# 外部事件注入 — 测试员手册 (P25)

> 面向**测试员**的操作手册. 背景/架构/设计权衡见 [`P25_BLUEPRINT.md`](P25_BLUEPRINT.md). 本文只说**怎么测**, 不说**为什么这样设计**.

本手册覆盖三个外部事件入口:

- **Avatar Interaction** — 模拟主人用道具对桌宠做交互 (棒棒糖 / 猫爪 / 锤子).
- **Agent Callback** — 模拟后台 agent 任务完成通知.
- **Proactive Chat** — 模拟定时 / 空闲主动搭话.

---

## 0. 测试前准备 (所有场景通用)

1. 已启动 testbench 服务 (`python -m tests.testbench.server`, 默认 `http://localhost:48920`).
2. 浏览器打开后**先创建一个会话** (Sessions → New), 否则所有 `/api/session/*` 端点都返回 `404 NoActiveSession`.
3. 填写 Persona (角色卡):
   - `character_name` — **必填** (否则 `build_prompt_bundle` 抛 `PreviewNotReady` → `reason=persona_not_ready`).
   - `master_name` — 选填, 默认 `主人`.
   - `language` — 选填, 默认 `zh-CN`. 支持 `zh-CN` / `zh-TW` / `en` / `ja` / `ko` / `ru`; 填 `es` / `pt` 会**静默回退**到英文 (见 §4).
4. 填写 Chat 模型配置 (Model Config → chat 组), 并点一次 **Test Connection** 确认配置通过. 否则事件触发会返回 `reason=chat_not_configured`.

> 找不到"外部事件注入"面板? 它默认**折叠**在左侧聊天输入框下方 (r5 以后的位置). 展开即可看到 3 个子 tab.

### 0.1 快速查 `SimulationResult.reason` 代码

每次触发后响应的 `reason` 字段 (accepted 时为 null) 是闭集, 只会是以下之一:

| reason 代码 | 含义 | 典型触发 |
|---|---|---|
| `null` / 缺失 | 成功 (`accepted=true`) | — |
| `invalid_payload` | payload 字段非法 / 组合违反 allowlist | avatar 填 `lollipop + poke` |
| `dedupe_window_hit` | 同 `(interaction_id, tool, action)` 在 8000ms 内重复 | avatar 连点 |
| `empty_callbacks` | agent_callback 的 `callbacks` 是空数组 | `{"callbacks": []}` |
| `pass_signaled` | proactive LLM 返回 `[PASS]` | 正常的"不主动搭话"信号 |
| `llm_failed` | wire 组装好了但 LLM 调用抛错 | api_key 错 / 网络错 / provider 返 5xx |
| `persona_not_ready` | `build_prompt_bundle` 抛 `PreviewNotReady` | Persona 里 character_name 没填 |
| `chat_not_configured` | `resolve_group_config` 抛 `ChatConfigError` | Chat 模型配置组不完整 |

如果你看到**除以上之外**的 reason 代码, 说明主程序运行时机制被误复现了 (L29 + §2.1 禁止), 请报告.

---

## 1. Avatar Interaction (道具交互)

### 1.1 Payload 字段

| 字段 | 必填 | 取值 |
|---|---|---|
| `tool_id` | 是 | `lollipop` / `fist` / `hammer` |
| `action_id` | 是 | 取决于 `tool_id`: `lollipop` → `offer` / `tease` / `tap_soft`; `fist` → `poke`; `hammer` → `bonk` |
| `intensity` | 否 | `normal` / `rapid` / `burst` / `easter_egg`. **组合受限** (`_AVATAR_INTERACTION_ALLOWED_INTENSITY_COMBINATIONS`), 详见下表 |
| `touch_zone` | 否 (仅 `fist` / `hammer` 有效) | `ear` / `head` / `face` / `body` |
| `text_context` | 否 | 自由文本; 会拼入 LLM instruction |
| `reward` | 否 | 自由文本; 会拼入 LLM instruction |
| `interaction_id` | **是 (UI 自动填)** | 用于 **8000ms 去重窗口**, 同 `interaction_id` + `tool_id` + `action_id` 的二次触发会返回 `reason=dedupe_window_hit` |

**组合约束** — 填错会返回 `reason=invalid_payload`:

- `lollipop` + `offer` / `tease` → 只能 `intensity=normal`
- `lollipop` + `tap_soft` → `rapid` / `burst`
- `fist` + `poke` → `normal` / `rapid`
- `hammer` + `bonk` → 全部 4 档 (含彩蛋 `easter_egg`)

### 1.2 典型测试步骤

1. **触发事件**: 面板填好字段, 点 **触发事件**.
2. **看 wire**: 右侧 Prompt Preview 区的 **当前 wire** 标签页立刻更新, 末尾一条是 `role=user` 的 **full instruction** (含 tool / action / intensity / touch_zone / text_context / reward 全部字段). 如果只看到 `[主人摸了摸你的头]` 这种短 memory note, 是看错标签页了 — memory note 是对话气泡里显示的那条.
3. **看对话**: 左侧聊天区会多出一对 `user` (memory note) + `assistant` (LLM 回复).
4. **看日志**: 诊断 → 日志, 搜 `avatar_interaction_simulated`. `detail.accepted=true` 是成功, `detail.reason` 是被拒时的原因代码.

### 1.3 覆盖场景建议

- **正常交互**: `fist + poke + normal` — 最短配置, 验收通路.
- **触发去重**: 连点 2 次**相同 tool/action** — 第二次应得 `reason=dedupe_window_hit`, 对话区**不新增消息**.
- **触发 rank upgrade**: 第 1 次 `intensity=normal` → 第 2 次 `intensity=rapid` (8000ms 内) → 第 3 次 `intensity=burst`. 高 rank 会**覆盖**前一次, memory note 变成 "连续 / 暴力" 复数描述.
- **非法组合**: `lollipop + poke` — 应得 `reason=invalid_payload`.
- **预览 prompt**: 点 **预览 prompt** (不触发). 面板弹窗展示即将送给 LLM 的完整 wire, 关掉弹窗后**不会**真的发 LLM, 也**不消耗** 去重窗口.
- **mirror_to_recent**: 勾选 **同步写入最近对话记忆**. 触发后除 `session.messages` 以外, `memory/recent.json` 也会多一对 pair (去记忆页面能看到).

---

## 2. Agent Callback (后台 agent 回调)

### 2.1 Payload 字段

| 字段 | 必填 | 取值 |
|---|---|---|
| `callbacks` | **是** | 字符串数组 `["msg1", "msg2"]` 或对象数组 `[{"text": "msg1"}, ...]`. **空数组** 会返回 `reason=empty_callbacks` |

### 2.2 典型测试步骤

1. 面板 → Agent Callback tab → **触发类型**选任意模板, 或直接填 `callbacks` 数组.
2. 触发. **注意**: Agent Callback 的 instruction 只进 wire, **不进** `session.messages` (只有 assistant 回复会), 所以对话气泡里**不会**出现 "`[系统通知: ...]`" 这种条目 — 这是刻意设计 (对齐主程序 `prompt_ephemeral(persist_response=False)` 语义).
3. **看 wire**: 右侧预览末尾消息以多语言 `AGENT_CALLBACK_NOTIFICATION` 开头 (zh: `======[系统通知: 以下是最近完成的后台任务情况...]`).
4. **看对话**: 只会多出一条 `assistant` 回复 (LLM 对 callback 的回应).

### 2.3 覆盖场景建议

- **单条 callback**: `callbacks=["图片生成完成"]`.
- **多条 callback**: `callbacks=["A 完成", "B 完成", "C 失败"]` — 验证 LLM 是否合理聚合.
- **空数组**: `callbacks=[]` → `reason=empty_callbacks`.
- **非字符串元素**: `callbacks=[{"text": "ok"}]` — 应正常接受 (取 `.text` 字段).

---

## 3. Proactive Chat (主动搭话)

### 3.1 Payload 字段

| 字段 | 必填 | 取值 |
|---|---|---|
| `kind` | **是** | `home` / `screenshot` / `window` / `news` / `video` / `personal` / `music` 等, 详见主程序 `config.prompts_proactive.PROACTIVE_CHAT_PROMPTS` 的 key |
| `topic` | 否 | 主动对话话题 (r5 之后 UI 允许手动填). 默认由 dispatch table 自动选 |
| `hours_since_last_interaction` | 否 | 距上次对话的小时数, 影响 prompt 里的 time_passed 片段 |
| 其它 `kind` 相关字段 | 见 dispatch table | 例如 `personal` 需 `personal_event` 字段 |

### 3.2 典型测试步骤

1. 面板 → Proactive tab → 选 `kind`, 填 `topic` (可选).
2. 预览 — Proactive 的 prompt 结构和前两个不一样, **建议每次先点 预览 prompt**, 确认 `topic` / `hours_since_last_interaction` 字段被正确拼进 prompt.
3. 触发. LLM 有两种返回:
   - **正常回复** → `assistant` 消息写入 `session.messages`.
   - **`[PASS]`** → LLM 表示"此刻不主动搭话", `reason=pass_signaled`, **不写入** `session.messages` (正确设计).
4. **看日志**: 诊断 → 日志, `proactive_simulated`, `detail.reason=pass_signaled` 是 LLM 主动选择 PASS 的标记.

### 3.3 覆盖场景建议

- **每种 kind 跑一遍**: `home` / `screenshot` / `window` / `news` / `video` / `personal` / `music`. 主要是验证 prompt dispatch 正确.
- **PASS 场景**: 用一个"明显没啥好聊的"设置 (如 `hours_since_last_interaction=0.01`), 看 LLM 是否返 `[PASS]`.
- **手填 topic**: 填一个具体话题 (如 "最近在写的小说"), 验证 prompt 里被正确拼入.

---

## 4. 语言回退 (es / pt)

`persona.language` 取以下值时, 系统 prompt 会**静默回退**到英文 (**不打 WARNING**):

- `es` (西班牙语)
- `pt` (葡萄牙语)

测试方法:

1. Persona → `language` 设为 `es`.
2. 触发任一事件.
3. 看右侧 wire — `AGENT_CALLBACK_NOTIFICATION` 应以英文 `======[System Notice: The following background tasks...]` 开头, 不是中文或日文.
4. 看控制台 / 日志 — **不应**有 `WARNING: Unexpected lang code es` 输出 (主程序 `_SILENT_FALLBACK = {'es', 'pt'}` 契约).

如果你看到 WARNING, 或者回退到了非英文的语言, 说明上游契约发生漂移 — 报告并打开一个 issue.

---

## 5. mirror_to_recent 三态

每次事件触发响应 (`SimulationResult`) 里都有 `mirror_to_recent` 字段, 三个子字段表达完整意图:

| `requested` | `applied` | `fallback_reason` | 含义 |
|---|---|---|---|
| `false` | `false` | `null` | tester 没勾, 系统也没写 — **默认行为** |
| `true` | `true` | `null` | tester 勾了, 系统成功写入 `memory/recent.json` |
| `true` | `false` | 非空字符串 | tester 勾了但**系统拒绝写**. 常见 `fallback_reason`: `no_pair_produced` (LLM 返回空) / `pass_signaled` (proactive PASS) / `empty_assistant_reply` / `memory_dir_missing` |

**永远不应**出现的组合: `requested=false, applied=true` — 这意味着系统在 tester 没要求的情况下偷偷写了记忆, 属于 L17 违反.

---

## 6. 常见问题排查

### Q1: 事件触发显示成功, 但右侧 wire 标签页空白

**原因**: 你可能在看 "历史 wire" 或旧版的 "下次 /send 预估 wire" 面板. **当前 wire** (r6 之后的唯一标签) 会在每次触发后覆盖. 刷新页面或点一下 tab 切换.

### Q2: 对话气泡里没出现新消息, 但日志显示 `accepted=true`

检查 `kind`:
- `agent_callback` — **只有 assistant 回复**会入消息列表, instruction 不入 (设计)
- `proactive` — 如果 `reason=pass_signaled`, assistant 回复是 `[PASS]` 字面量, **不入** 消息列表

这两种都是正确行为.

### Q3: 每次点都 `reason=dedupe_window_hit`

8000ms 去重窗口没过期. 解决办法 (二选一):
- 等 8s.
- 点面板的 **重置去重缓存** 按钮 (调 `POST /external-event/dedupe-reset`).

### Q4: `reason=chat_not_configured`

Chat 模型配置没填完整. 去 Model Config → chat 组, 至少填 `provider` / `base_url` / `model` / `api_key`, 点 **Test Connection** 验证.

### Q5: Prompt injection 检测把 tester 填的合法字段当成攻击了

r5 引入的检测会扫描**所有** tester 可填字段 (text_context / reward / topic / callbacks / persona_hint / extra_hint). 在 诊断 → 错误 页面你会看到 `prompt_injection_detected` 条目. 如果是误报 (你的合法测试输入被当成注入了), 这是 positive signal — 测试员应该记录下来, 主程序的注入检测可能需要改 — 不阻塞你的测试, 事件仍会触发.

---

## 7. 预览 vs 触发 — 什么时候用哪个?

P25 r5 之后, **预览 prompt** 和 **触发事件** 是两个独立按钮, 各自有使用场景:

- **预览 prompt** (`/external-event/preview`):
  - 只**构造 wire 并同步返回**, **不**调 LLM, **不**写 `session.messages`, **不**动去重缓存, **不**动 `session.last_llm_wire`.
  - 对 avatar, 它**不会消耗去重窗口** — 同一个 `interaction_id` 你可以无限次预览.
  - **使用时机**: 确认 payload 字段被正确拼入 instruction; 新手熟悉系统; 调试 i18n 语言回退问题.
- **触发事件** (`/external-event`):
  - 真的发 LLM, 真的写 `session.messages` (如果 kind 允许), 消耗去重窗口, 覆盖 `session.last_llm_wire`.
  - **使用时机**: 正式测试, 验证 LLM 回复 / 记忆影响.

**建议工作流**: 新场景 → 先 **预览** 确认结构 → 再 **触发** 看 LLM 反应.

---

## 8. 与 Auto-Dialog / SimUser 的关系

外部事件可以**打断**正在进行的 auto-dialog (双 AI 自动对话) 或 SimUser 生成流程:

- Auto-Dialog / SimUser 也会占用 session 的 BUSY lock. 如果 auto-dialog 在跑, 你触发外部事件会得到 `409 SessionConflict` (`busy_op` 字段告诉你谁在占用).
- 解决方法: 暂停 auto-dialog (面板上有 pause 按钮) → 触发外部事件 → 继续 auto-dialog.
- **右侧 Prompt Preview** 的 source 标签会显示 `chat.send` / `avatar_event` / `agent_callback` / `proactive_chat` / `auto_dialog_target` / `auto_dialog_simuser` / `simulated_user` / `memory.llm` / `judge.llm` — 区分是哪一路 LLM 调用写的. 测试自动对话混合场景时这个标签特别有用.

---

## 9. 相关 API 直连 (非 UI)

```bash
# 触发
curl -X POST http://localhost:48920/api/session/external-event \
  -H "Content-Type: application/json" \
  -d '{"kind":"avatar","payload":{"interaction_id":"t1","tool_id":"fist","action_id":"poke"},"mirror_to_recent":false}'

# 预览 (dry-run, 不触发 LLM, 不消耗去重)
curl -X POST http://localhost:48920/api/session/external-event/preview \
  -H "Content-Type: application/json" \
  -d '{"kind":"avatar","payload":{"interaction_id":"t1","tool_id":"fist","action_id":"poke"}}'

# 去重缓存状态
curl http://localhost:48920/api/session/external-event/dedupe-info

# 清空去重缓存
curl -X POST http://localhost:48920/api/session/external-event/dedupe-reset
```

---

## 10. 汇报问题时请附带

1. **SimulationResult JSON** — `POST /external-event` 的整个响应体.
2. **诊断日志条目** — 诊断 → 日志 → 搜 `avatar_interaction_simulated` / `agent_callback_simulated` / `proactive_simulated` → 展开 JSON detail.
3. **当前 wire 快照** — 右侧 Prompt Preview "当前 wire" 标签页的 JSON.
4. **Persona + Model Config 截图** — 如果是回退 / 配置错误类问题.

> 记忆层 (`recent.compress` / `facts.extract` / `reflect`) 的影响评估是另一条测试链路, 见 `tests/testbench/docs/` 下其它 guide.
