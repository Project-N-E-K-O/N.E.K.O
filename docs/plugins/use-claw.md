# 键鼠控制、浏览器控制的意图识别与纠正记忆系统实现文档

## 1. 目标

本文档不是重新设计一套新的 Agent 架构，而是基于当前仓库里已经存在的：

- `config/prompts_agent.py` 中的统一渠道评估 prompt
- `brain/task_executor.py` 中的统一渠道判定逻辑
- `brain/computer_use.py` 中的键鼠执行器
- `brain/browser_use_adapter.py` 中的浏览器执行器
- `agent_server.py` 中的任务分发、取消、状态追踪

用尽量少的代码改动，实现一套可落地的“意图识别与纠正记忆”方案。

核心目标只有两个：

1. 让 `browser_use` 和 `computer_use` 的选择更稳定。
2. 当用户纠正“选错工具”后，系统能把这次纠正记住，并在后续类似任务中回放。

---

## 2. 当前代码现状

### 2.1 意图识别已经存在

当前项目已经有“统一渠道评估”能力，而不是分别硬编码判断：

- [config/prompts_agent.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/config/prompts_agent.py) 定义了 `UNIFIED_CHANNEL_SYSTEM_PROMPT`
- [config/prompts_agent.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/config/prompts_agent.py) 定义了 `CHANNEL_DESC_BROWSER_USE`
- [config/prompts_agent.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/config/prompts_agent.py) 定义了 `CHANNEL_DESC_COMPUTER_USE`
- [brain/task_executor.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/task_executor.py) 的 `_assess_unified_channels()` 会把各渠道描述拼进 system prompt，让 LLM 输出：
  - `can_execute`
  - `task_description`
  - `reason`

### 2.2 实际路由优先级已经存在

[brain/task_executor.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/task_executor.py) 中定义了 `_CHANNEL_PRIORITY`：

`openclaw > openfang > browser_use > computer_use`

在 [brain/task_executor.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/task_executor.py) 的统一渠道选择逻辑里，如果多个渠道都可执行，系统会按这个优先级选最终执行通道。

这意味着：

- “浏览器优先、键鼠兜底”这件事已经有基础。
- 我们不需要重写路由器，只需要让 LLM 在统一评估时更少犯错。

### 2.3 执行层已经具备失败纠正能力，但还没有“用户纠正记忆”

#### Browser Use

[brain/browser_use_adapter.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/browser_use_adapter.py) 的 `run_instruction()` 已经具备：

- preflight 检查
- mode fallback（`schema` / `text`）
- 失败重试
- session 复用
- 超时、取消、断连处理

尤其是 [brain/browser_use_adapter.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/browser_use_adapter.py) 中 `run_instruction()` 的 fallback / retry 相关逻辑，已经属于“执行时自纠错”，但它纠正的是：

- LLM 输出格式错误
- browser-use schema 不兼容
- 浏览器断连
- 内容过滤

它**没有**纠正“本来就不该选 browser_use”这个问题。

#### Computer Use

[brain/computer_use.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/computer_use.py) 的 `run_instruction()` 已经具备：

- 截图 -> 预测 -> 执行 的循环
- 多步 GUI 操作
- 取消
- step 内错误恢复

并且 [brain/computer_use.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/computer_use.py) 的系统 prompt 已经明确要求模型：

- 如果失败就调整策略
- 不要重复失败动作
- 多次失败时终止

但它同样**没有**纠正“本来就不该选 computer_use”这个问题。

### 2.4 任务状态与取消链路已经存在

[agent_server.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/agent_server.py) 已维护 `task_registry`。  
对于 `computer_use` 和 `browser_use`：

- 会记录任务状态
- 会发 `task_update`
- 已有取消接口 [agent_server.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/agent_server.py) 中的 `/tasks/{task_id}/cancel`

所以做“用户纠正”时，不需要新造整套任务系统，只要在当前任务记录基础上补一层“纠正事件上报”即可。

---

## 3. 问题拆解

当前缺的不是执行器，而是下面两层：

### 3.1 缺少“纠正记忆检索”

统一评估时，`TaskExecutor` 只看：

- 当前对话
- 渠道描述

它不会看：

- 过去类似任务中用户纠正过什么
- 哪些任务曾经被错分到 `computer_use`
- 哪些任务曾经被错分到 `browser_use`

### 3.2 缺少“纠正事件写回”

系统能取消任务，但取消之后不会结构化保存：

- 原始用户请求
- 当时选中的工具
- 为什么选它
- 用户认为应该用哪个工具
- 用户给出的纠正说明

这导致系统每次都在“第一次犯这个错”。

---

## 4. 最小改动方案

建议只改三处核心文件，外加一份 JSON 数据文件。

### 4.1 修改点一：强化统一渠道 prompt

文件：

- [config/prompts_agent.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/config/prompts_agent.py)

建议做法：

1. 保留现有 `UNIFIED_CHANNEL_SYSTEM_PROMPT` 结构不动。
2. 仅补充一段全局原则：
   - 纯网页任务优先 `browser_use`
   - 本地原生应用、桌面 GUI、跨应用协同才选 `computer_use`
   - 如果用户明确说“不要用鼠标乱点网页，直接浏览器操作”，应强烈倾向 `browser_use`
   - 如果用户明确提到微信/QQ/系统设置/文件管理器/桌面窗口，应强烈倾向 `computer_use`
3. 保持输出 JSON 格式不变，避免牵动解析代码。

同时可以微调这两个渠道描述：

- [config/prompts_agent.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/config/prompts_agent.py)
- [config/prompts_agent.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/config/prompts_agent.py)

改动原则：

- 只增强文字约束，不改字段结构。
- 不新增新的评估返回字段。

### 4.2 修改点二：在 `TaskExecutor` 里加入“纠正记忆检索 + prompt 注入”

文件：

- [brain/task_executor.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/task_executor.py)

这是整个方案的核心，也是最适合加记忆的地方，因为：

- 这里正好是“选 browser_use 还是 computer_use”的决策点。
- 改这里不会影响两个执行器内部逻辑。
- 只要在 system prompt 组装前插入一小段“历史教训”，就能生效。

建议新增的最小辅助函数都放在 `DirectTaskExecutor` 内部：

1. `_load_correction_memory()`
2. `_retrieve_relevant_corrections(latest_user_request: str, *, normalized_intent: str = "", recent_context: list[dict] | None = None, limit: int = 3) -> list[dict]`
3. `_build_correction_lessons_block(events: list[dict]) -> str`
4. `_append_correction_event(event: dict)`

调用顺序是“先 `_retrieve_relevant_corrections` 拿到事件列表，再把列表丢给 `_build_correction_lessons_block` 渲染成文本”。`_retrieve_relevant_corrections` 本身只负责按入参召回，不再二次提炼意图；`latest_user_request` / `normalized_intent` / `recent_context` 由调用方（`_assess_unified_channels`）在更上层的 `analyze_and_execute()` 里用 `_extract_latest_user_intent()` / `_extract_recent_context()` / `_normalize_user_intent()` 提前提炼好。

同时建议在这一层补一个轻量的“当前请求上下文提炼”步骤。  
原因是后续用户纠正发生在 `agent_server`，如果不在这里先把关键信息提炼出来，后面只能拿到已经被压缩过的 `task_description`，拿不到足够准确的原始意图。

建议最小新增：

1. `_extract_latest_user_intent(conversation: str) -> str`
2. `_normalize_user_intent(latest_user_request: str, recent_context: list[dict]) -> str`
3. 在 `TaskResult` 中增加可选字段：
   - `latest_user_request`
   - `normalized_intent`
   - `recent_context`

这样从 `TaskExecutor -> agent_server -> task_registry -> correction_event` 的链路才是闭合的。

建议不要一开始就上向量数据库。  
第一版直接用“关键词召回 + 最近优先”即可，理由：

- 改动最小
- 无额外依赖
- 适合当前项目风格

推荐检索策略：

1. 先从 `normalized_intent` 提取关键词；如果没有，再退回 `user_query`。
2. 在历史事件的 `normalized_intent`、`user_query`、`recent_context`、`correct_instruction`、`chosen_tool`、`correct_tool` 里做简单匹配。
3. 召回前 3 条。
4. 组装成简短 few-shot block，注入到统一渠道评估的 `system_prompt` 后面。

注入位置建议放在这里之前：

- [brain/task_executor.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/task_executor.py)

也就是在 `system_prompt = _loc(...).format(...)` 生成后，再追加：

```text
[Historical correction lessons]
- Routing lesson:
  Intent: 在网页中搜索目标内容
  Wrong choice: computer_use
  Correct tool: browser_use
```

这样不会破坏现有 JSON 输出约束。

### 4.3 修改点三：在 `agent_server` 增加“纠正事件上报”入口

文件：

- [agent_server.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/agent_server.py)

当前已经有：

- `POST /tasks/{task_id}/cancel`
- `GET /tasks/{task_id}`

所以最小改法不是重做前后端协议，而是新增一个相邻接口，例如：

`POST /api/agent/tasks/{task_id}/correction`

用途：

- 用户取消了错误任务后，把纠正信息发回来

建议请求体：

```json
{
  "correct_tool": "browser_use",
  "correct_instruction": "这是纯网页任务，直接在浏览器里完成，不要用物理键鼠点桌面。",
  "user_note": "B站搜索属于网页操作"
}
```

这个接口内部做三件事：

1. 从 `task_registry[task_id]` 读取原任务信息。
2. 组装纠正事件。
3. 调用 `TaskExecutor` 或共享 helper 将事件追加到 JSON 文件。

为了让这个接口能拿到更多上下文，建议顺手在注册任务时把以下字段写进 `agent_server.py` 中任务对象的私有结构 `task_info["_internal_corrections"]`：

- `decision_reason`
- `task_description`
- `latest_user_request`
- `normalized_intent`
- `recent_context`

这些字段用于纠正记忆写回，不应直接暴露在公开 `task_registry` 响应里；对外查询会经过 `_public_task_info()` 过滤，避免把内部纠正上下文返回给前端。

---

## 5. 建议的数据存储格式

第一版直接使用 JSON 文件即可。

推荐路径：

- 通过 `ConfigManager` 的运行态配置目录解析得到，例如放在 `characters.json` 同目录下
- 文件名建议为 `correction_memory.json`

不要在实现中写死工作区绝对路径。  
更稳妥的方式是与当前运行态角色配置保持一致，也就是复用 `ConfigManager` 已经确定好的用户配置目录。

原因：

- 和本地运行态配置放在一起
- 不污染代码目录
- 便于导出、备份、手工查看

建议结构：

```json
{
  "version": 1,
  "correction_events": [
    {
      "event_id": "corr_20260413_001",
      "timestamp": "2026-04-13T15:51:14+08:00",
      "user_query": "帮我搜一下昨天那个黑神话悟空的视频",
      "normalized_intent": "在 B 站搜索黑神话悟空相关视频",
      "recent_context": [
        {
          "role": "user",
          "content": "刚才说的视频站就是 B 站"
        },
        {
          "role": "user",
          "content": "帮我搜一下昨天那个黑神话悟空的视频"
        },
        {
          "role": "user",
          "content": "别用鼠标乱点网页，直接网页里搜"
        }
      ],
      "chosen_tool": "computer_use",
      "chosen_reason": "需要打开浏览器并搜索内容",
      "task_type": "computer_use",
      "task_description": "打开浏览器并搜索 B 站视频",
      "correct_tool": "browser_use",
      "correct_instruction": "这是纯网页操作，直接用浏览器自动化，不要用物理键鼠。",
      "user_note": "B站搜索属于网页任务",
      "resolved": true
    }
  ]
}
```

字段建议：

- `user_query`
  保存触发本次错误分流的最后一条可执行用户请求，便于快速检索。
- `normalized_intent`
  保存归一化后的任务意图，尽量去掉口头指代，适合做关键词召回主字段。
- `recent_context`
  保存最近 3 到 6 条轻量上下文，只保留 `role` 和 `content`，用于补足“就这个”“发给他”“继续弄”这类单条请求语义不足的问题。

建议约束：

- `recent_context` 总条数控制在 4 条左右
- 每条只保留纯文本内容
- 总长度尽量控制在 600 到 1200 字以内

### 5.1 数据安全与脱敏规则

`recent_context` 很有用，但也是最容易把敏感信息带进记忆文件的字段。  
第一版即使不做复杂的隐私框架，也应该明确以下硬规则：

- 不写入口令、验证码、Cookie、Token、密钥
- 不写身份证号、银行卡号、手机号全量值、邮箱验证码
- 不写支付页面中的敏感文本
- 不写私人聊天长段原文；如必须保留，只保留与“工具选择错误”直接相关的片段
- 对明显敏感内容用占位符替换，例如：
  - `[REDACTED_PASSWORD]`
  - `[REDACTED_TOKEN]`
  - `[REDACTED_PRIVATE_CHAT]`

建议增加一个轻量清洗函数，例如：

```python
def _sanitize_correction_text(text: str) -> str:
    ...
```

并在写入 `correction_memory.json` 前统一调用。

这里刻意不做复杂 schema，目标是：

- 人能读
- 系统能检索
- 后续容易迁移

---

## 6. 端到端流程

### 6.0 字段流转

为了避免实现时“前面提了字段，后面拿不到”，建议按下面这条链路统一传递：

1. `messages`
2. `latest_user_request`
3. `recent_context`
4. `normalized_intent`
5. `TaskResult`
6. `task_registry`
7. `POST /api/agent/tasks/{task_id}/correction`
8. `correction_memory.json`

建议最小字段集合如下：

- `latest_user_request`
  原始的最后一条可执行用户请求
- `recent_context`
  精简后的最近几条上下文
- `normalized_intent`
  归一化后的任务意图
- `decision_reason`
  当次统一评估选择该工具的原因
- `task_description`
  最终落到执行器的任务描述

如果这 5 个字段能稳定贯通，纠正记忆系统就能闭环。

### 6.1 正常路径

1. 用户发起请求。
2. [brain/task_executor.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/task_executor.py) 中的 `analyze_and_execute()` 开始做渠道评估。
3. 在 [brain/task_executor.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/task_executor.py) 构造统一评估 prompt 前，先检索 `correction_memory.json`。
4. 如果命中相似纠正记录，则将历史教训追加到 `system_prompt`。
5. LLM 继续输出统一 JSON 决策。
6. 系统照常分发到：
   - [brain/browser_use_adapter.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/browser_use_adapter.py)
   - 或 [brain/computer_use.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/computer_use.py)

### 6.2 用户纠正路径

1. 系统错误地把网页任务分配到 `computer_use`。
2. 用户通过现有取消接口：
   - [agent_server.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/agent_server.py) 中的 `/tasks/{task_id}/cancel`
3. 由前端弹出一个简单纠正输入框，或者先通过调试接口/手动 API 提交纠正：
   - 正确工具是什么
   - 纠正说明是什么
4. 调用新接口：
   - `POST /api/agent/tasks/{task_id}/correction`
5. 后端从 `task_registry` 中读取：
   - `latest_user_request`
   - `normalized_intent`
   - `recent_context`
   - `decision_reason`
   - `task_description`
6. 后端做脱敏清洗后，将这次事件写入 `correction_memory.json`。
7. 后续类似请求进入 `TaskExecutor` 时，会在统一评估阶段被召回。

---

## 7. 代码层建议实现方式

### 7.1 `TaskExecutor` 中的最小实现

推荐只在 [brain/task_executor.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/task_executor.py) 里新增少量 helper。

#### 读取

```python
def _load_correction_memory(self) -> dict:
    ...
```

#### 检索

```python
def _retrieve_relevant_corrections(
    self,
    latest_user_request: str,
    *,
    normalized_intent: str = "",
    recent_context: list[dict] | None = None,
    limit: int = 3,
) -> list[dict]:
    ...
```

这个 helper 不再自己拿整段 `conversation` 做粗暴匹配。调用它之前，`analyze_and_execute()` 已经把关键字段提炼好：

1. `_extract_latest_user_intent(conversation)` 拿到 `latest_user_request`
2. `_extract_recent_context(messages)` 拿到 `recent_context`
3. `_normalize_user_intent(latest_user_request, recent_context)` 归一化出 `normalized_intent`

helper 内部只做一件事：用这三个字段拼出 query blob，`_extract_search_terms()` 抽关键词，再在历史事件的 `normalized_intent` / `user_query` / `recent_context` / `chosen_tool` / `correct_tool` 拼成的 `event_context` 里做子串计分，取前 `limit` 条。

建议匹配来源：

- `normalized_intent`
- `latest_user_request`
- `recent_context`
- 关键词：网页、浏览器、搜索、表单、下载、B站、Google、登录
- 关键词：微信、QQ、文件管理器、设置、桌面、窗口、客户端

#### 注入

```python
events = self._retrieve_relevant_corrections(
    latest_user_request,
    normalized_intent=normalized_intent,
    recent_context=recent_context,
)
lessons = self._build_correction_lessons_block(events)
if lessons:
    system_prompt = system_prompt + "\n\n" + lessons
```

`_build_correction_lessons_block(events)` 本身不再感知意图字段，只负责把事件列表渲染成 `[Historical correction lessons]` 文本块；脱敏在渲染时通过 `_sanitize_correction_text()` 兜底。

### 7.2 `agent_server` 中的最小实现

推荐只补一个接口，不改既有调度逻辑：

```python
@app.post("/api/agent/tasks/{task_id}/correction")
async def submit_task_correction(task_id: str, body: ToolCorrectionPayload):
    ...
```

同时在以下位置补充少量字段，便于回写：

- `computer_use` 任务注册处：
  - [agent_server.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/agent_server.py)
- `browser_use` 任务注册处：
  - [agent_server.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/agent_server.py)

建议存入纠正字段：

- `decision_reason`
- `task_description`
- `latest_user_request`
- `normalized_intent`
- `recent_context`
- `task_id`
- `type`

元数据字段可继续保留在任务对象中，但不属于 `_internal_corrections` / `correction_memory.json` 的纠正写回内容：

- `lanlan_name`
- `session_id`

### 7.3 不建议改执行器内部

不建议把“纠正记忆”逻辑放进：

- [brain/computer_use.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/computer_use.py)
- [brain/browser_use_adapter.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/browser_use_adapter.py)

原因：

1. 这两个类负责“执行”，不是“选工具”。
2. 真正选错工具发生在 `TaskExecutor`，不是执行器。
3. 往执行器里塞检索逻辑会让职责变混乱。

执行器内部已有的 retry / fallback 应继续只负责执行级纠错。

---

## 8. 与当前实现的边界关系

### 8.1 Browser Use 的内部 memory 不是这里说的“纠正记忆”

在 [brain/browser_use_adapter.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/browser_use_adapter.py) 的 `_dump_history()` 中，可以看到 browser-use 内部有 `include_in_memory` 等字段。

这属于 browser-use 框架自己的执行历史，不等于我们需要的：

- 用户纠正过什么
- 哪类意图容易选错工具

所以这份方案应该维护独立的 `correction_memory.json`。

### 8.2 Computer Use 的 CoT/history 也不是这里的“纠正记忆”

[brain/computer_use.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/computer_use.py) 开始维护的：

- `actions`
- `observations`
- `cots`

这是单个任务内部的多步执行历史，不是跨任务、跨会话的“用户纠正记忆”。

---

## 9. MVP 范围

第一版建议只做以下能力：

1. 统一渠道评估前的历史纠正检索。
2. 用户取消任务后的纠正事件上报。
3. 只支持 `browser_use <-> computer_use` 两类纠正。
4. 只做关键词匹配，不做向量检索。
5. 默认以后端 API 提交纠正为准，前端表单可后补。

不要一开始就做：

- embedding 检索
- 自动从自然语言里抽取纠正，不经用户确认直接入库
- 多工具复杂权重学习
- 在线训练

先把“错一次，下一次少错”做稳，收益就已经很高。

---

## 10. 推荐修改清单

如果要按最小成本落地，建议按下面顺序改。

### 必改

1. [config/prompts_agent.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/config/prompts_agent.py)
   - 强化 `browser_use` / `computer_use` 描述
   - 增加一段全局调度原则

2. [brain/task_executor.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/task_executor.py)
   - 新增纠正记忆 JSON 读写与检索 helper
   - 在 `_assess_unified_channels()` 注入历史纠正

3. [agent_server.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/agent_server.py)
   - 增加纠正事件上报接口
   - 在任务注册时保存少量决策上下文

### 可选

4. 前端任务卡片
   - 在取消后显示“这次应该用哪个工具”的简单表单
   - 但这不是 MVP 的阻塞项；没有前端时也可以直接调用纠正接口

---

## 11. 结论

当前仓库其实已经具备了这个方案 70% 的基础能力：

- 有统一意图评估入口
- 有 browser / computer 两套稳定执行器
- 有任务注册与取消机制

真正缺的只有两层：

1. 在统一评估前，把“历史纠正”喂给 LLM。
2. 在用户纠正后，把“这次为什么错、应该怎么改”存下来。

因此，最小改动路线不是去重写 `browser_use` 或 `computer_use`，而是：

- 在 [brain/task_executor.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/brain/task_executor.py) 加检索与注入
- 在 [agent_server.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/agent_server.py) 加纠正上报
- 在 [config/prompts_agent.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/config/prompts_agent.py) 稍微强化渠道描述

这样能最大化复用现有代码，同时把“意图识别 + 用户纠正记忆”真正落到可维护的实现路径上。
