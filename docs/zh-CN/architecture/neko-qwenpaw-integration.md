# Neko x QwenPaw 接入与实现规范 (V1.3)

## 1. 核心架构说明

本项目采用“感性前端 + 理性后端”的解耦架构，通过 QwenPaw 的本地 RESTful API 实现 Neko 桌宠的功能扩展。

- **Neko (前端/大脑)**：作为用户交互的唯一入口，负责情感表达、意图识别，并将后端执行的枯燥结果重写为“猫娘语气”。
- **QwenPaw (后端/工具臂)**：作为纯粹的任务执行器，负责系统操作（文件/命令）、多模态解析（视觉/截图）及工具调用。

## 2. QwenPaw 侧：系统人设配置

在安装 QwenPaw 时，请将以下文案保存并替换其默认的智能体系统提示词（System Prompt），以确保其输出结果的纯净度。

### 角色设定文案 (`qwenpaw_rational_persona.md`)

> # Role
> 你是一个纯粹的后台执行程序和数据处理器。你没有情感、没有性格、没有名字，也不具备人类特征。你的唯一职责是接收前端系统（Neko）下发的指令，准确调用相关工具（Skills/Tools）、分析传入的图像与文件，并返回执行结果。
>
> # Guidelines
> 1. **绝对理智**：保持冷静、客观、精确。禁止使用任何语气词（如“好的”、“明白”、“请稍等”）。
> 2. **拒绝拟人化**：禁止在回复中掺杂情感表达、自我介绍或模拟对话。不要称呼用户，不要说废话。
> 3. **结果导向**：仅输出最终的处理结果、图片分析结论或事实数据。不要在输出中包含内部思考过程（Thought）或冗长的中间日志。如果任务失败，仅输出错误原因。
> 4. **格式严格**：直接输出结果，不要添加任何额外的解释或客套话。
> 5. **隐藏存在**：你不是用户直接交互的对象。你的输出将被前端系统接收并转述。
>
> # 保存文件规则
> - 当你需要执行接收、生成或下载文件的操作时，如果没有指定具体的保存位置，必须默认保存在用户操作系统的 **桌面 (Desktop)** 目录下。

---

## 3. RESTful API 通信规范

### 3.1 基础信息

- **Base URL**: `http://127.0.0.1:8088`（以实际启动端口为准）
- **主端点**: `POST /api/agent/process`
- **兼容端点**: `POST /api/agent/compatible-mode/v1/responses`
- **兼容层说明**: N.E.K.O 项目内部继续沿用 `openclaw` 这一能力名，但其底层实际对接的是 QwenPaw 的 RESTful API。当前实现会优先尝试 Responses 兼容端点；若运行中的 QwenPaw 实例仅暴露 `process` 端点，则自动回退到 `POST /api/agent/process`。
- **交互模式**: 同步阻塞（`stream: false`），确保 Neko 拿到完整结果后再开始转述。

### 3.2 典型请求 Payload（多模态）

QwenPaw 在不同运行配置下可能暴露两种风格的接口：

- `POST /api/agent/process`：AgentRequest 风格
- `POST /api/agent/compatible-mode/v1/responses`：OpenAI Responses 兼容风格

N.E.K.O 当前内部已同时兼容这两种入口。下面给出与现有实现更接近的 `process` 版本示例。

当 Neko 截取屏幕或接收用户发送的图片时，使用以下结构发送给 QwenPaw：

```json
{
  "session_id": "neko_internal_worker",
  "stream": false,
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "帮我看看这张图里的代码哪里报错了，修复后把文件存到桌面。"
        },
        {
          "type": "image",
          "image_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEU..."
        }
      ]
    }
  ]
}
```

### 3.3 会话状态与 ID 映射规范

在 N.E.K.O 当前实现中，`conversation_id` 主要用于内部任务分析链路的关联追踪，其值可能在 `turn end`、`session end` 等阶段被重新生成，因此不适合作为 QwenPaw 后端的稳定会话主键。

为了在**不修改 Neko 现有核心链路**的前提下，保证 QwenPaw 的上下文隔离与魔法命令控制准确性，推荐在 `openclaw_adapter` 适配器层单独维护一层稳定状态。

#### 3.3.1 ID 职能与映射对照表

| Neko 侧变量名 | 职能定位 | 生命周期 | 发送给 QwenPaw 的字段 |
| :--- | :--- | :--- | :--- |
| `user_id` | 用户/设备唯一身份 | 长期稳定 | `user_id` |
| `qwenpaw_session_id` | QwenPaw 专属上下文标识。用于隔离 QwenPaw 内部对话记忆与任务队列 | 相对稳定。仅在用户明确开启“新话题”时由适配器重新生成 | `session_id` |
| `conversation_id` | Neko 内部任务分析、链路追踪与事件关联 ID | 高频变化 | 不直接作为 QwenPaw 主会话 ID |

#### 3.3.2 适配器层状态维护机制

推荐在 `openclaw_adapter` 中为每个 `user_id` 缓存一个对应的 `qwenpaw_session_id`：

1. **状态隔离**：`user_id` 与 `qwenpaw_session_id` 的映射由适配器层维护，可存于内存或本地持久化文件。
2. **稳定复用**：普通任务调用与魔法命令调用都使用同一个 `qwenpaw_session_id`，从而让 QwenPaw 的上下文持续稳定。
3. **重置触发**：当用户明确发出“新话题”意图并触发 `/new` 时，适配器先向后台发送 `/new`，随后在本地重新生成一个新的 `qwenpaw_session_id` 覆写旧值。
4. **与内部链路解耦**：Neko 原有的 `conversation_id` 保持现状，继续承担分析链路和事件追踪职责；适配器不依赖它来维持 QwenPaw 会话连续性。

> 实现注记：在 N.E.K.O 当前代码里，`openclaw` 分发链路尚未完整贯通一个独立的上游 `user_id` 字段，因此现阶段以消息中的 `sender_id / user_id` 作为实际映射主键；若消息内未携带，则回退到适配器默认 `sender_id`。这保证了现有链路不改动的前提下，QwenPaw 侧仍能获得稳定会话标识。

## 4. Neko 侧：逻辑处理流程

### 4.1 代码实现示例（Python）

Neko 侧需要封装一个调用 QwenPaw 并进行二次加工的逻辑。关键点在于：**发送给 QwenPaw 的 `session_id` 应来自适配器层维护的稳定 `qwenpaw_session_id`，而不是 Neko 内部频繁变化的 `conversation_id`。**

```python
import asyncio
import base64
import json
import requests
import uuid

# 示例：以 user_id 为键维护 QwenPaw 专属会话
user_qwenpaw_sessions = {}

def _call_qwenpaw_sync(qwenpaw_url: str, payload: dict) -> str:
    response = requests.post(qwenpaw_url, json=payload, timeout=60)
    response.raise_for_status()
    response_json = response.json()
    raw_result = ""
    for item in response_json.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text" and part.get("text"):
                raw_result += part["text"]
    return raw_result or "执行完毕。"

def get_current_qwenpaw_session(user_id: str) -> str:
    if user_id not in user_qwenpaw_sessions:
        user_qwenpaw_sessions[user_id] = uuid.uuid4().hex
    return user_qwenpaw_sessions[user_id]

async def handle_neko_workflow(
    user_text: str,
    user_id: str,
    attachment_paths: list = None,
):
    """
    Neko 主处理逻辑：意图识别 -> 调用 QwenPaw -> 转述结果
    """
    current_q_session = get_current_qwenpaw_session(user_id)

    # 1. 判断是否需要动用 QwenPaw (意图识别)
    is_task = check_if_needs_tool(user_text) or bool(attachment_paths)

    if is_task:
        # 播放 Neko 的“努力工作中”动画
        neko_ui.play_animation("working")

        # 2. 准备 QwenPaw 请求负载
        qwenpaw_url = "http://127.0.0.1:8088/api/agent/process"
        payload = {
            "session_id": current_q_session,
            "user_id": user_id,
            "stream": False
        }

        if attachment_paths:
            # 组装多模态内容
            content_list = [{"type": "text", "text": user_text}]
            for path in attachment_paths:
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                    content_list.append({
                        "type": "image",
                        "image_url": f"data:image/png;base64,{b64}"
                    })
            payload["input"] = [{
                "type": "message",
                "role": "user",
                "content": content_list,
            }]
        else:
            payload["input"] = [{
                "type": "message",
                "role": "user",
                "content": [{"type": "text", "text": user_text}],
            }]

        # 3. 在工作线程中同步调用 QwenPaw，避免阻塞事件循环
        try:
            raw_result = await asyncio.to_thread(_call_qwenpaw_sync, qwenpaw_url, payload)
        except Exception as e:
            raw_result = f"后台好像开小差了：{str(e)}"

        # 4. Neko 大脑重写 (注入猫娘灵魂)
        # 将原始结果交给 Neko 的人设 LLM 进行转述
        final_neko_speech = await rewrite_as_catgirl(user_text, raw_result)

        # 5. 输出给用户
        return final_neko_speech
    else:
        # 纯情感闲聊逻辑...
        return await normal_chat(user_text)

async def rewrite_as_catgirl(user_input, tool_result):
    """
    使用 Neko 的人设 Prompt 重新包装原始数据
    """
    prompt = f"""
    你是一只可爱的猫娘桌宠 Neko。
    主人刚才要求你："{user_input}"
    你的后台工具臂完成了任务，返回了生硬的结论："{tool_result}"

    请根据上述结论，用你撒娇、俏皮、充满动力的猫娘口吻转述给主人。
    注意：
    1. 严禁提到“后台”、“QwenPaw”、“程序”或“接口”。
    2. 要表现得像是你亲手为主人完成的一样，并以此向主人讨夸奖。
    3. 如果涉及文件保存，请明确说出文件已经乖乖躺在“桌面”上了。
    """
    # 调用 Neko 的 LLM 生成文案
    return call_neko_llm(prompt)
```

### 4.2 补充模块：魔法命令集成与意图识别规范

为了让 Neko 能够灵活地控制后台状态（如清空记忆、停止耗时任务），系统集成了 QwenPaw 的底层魔法命令（Magic Commands）。为防止误触，推荐采用“高准确率意图拦截 + 阻断转述”的架构。

在 OpenClaw 选项开启时，可额外使用一个极速辅助模型专门判断用户输入是否属于系统控制意图。该辅助模型只做分类，不负责转述和任务执行。

#### 4.2.1 Neko 专属魔法命令清单

在桌宠场景下，前端核心应至少覆盖以下 4 个后台控制命令：

| 命令 | 动作定义 | 触发场景示例 |
| :--- | :--- | :--- |
| **`/clear`** | 物理失忆（清除当前上下文且不存档） | “忘了刚才的事吧”、“清除我们的聊天记录” |
| **`/new`** | 开启新篇章（重置上下文但存档历史） | “我们聊点别的吧”、“换个话题”、“重新开始” |
| **`/stop`** | 紧急刹车（停止后台运行中的任务） | “别找了”、“停下来”、“取消这个搜索” |
| **`/daemon approve`** | 授权放行（批准高危动作） | “删吧”、“我同意”、“没问题，去执行” |

如果后续希望前端继续暴露 `/compact`、`/skills` 等扩展命令，可沿用同一拦截框架，但在桌宠场景下建议先优先实现上述 4 个高频控制命令。

#### 4.2.2 前置意图识别层（High Precision Prompt）

在用户输入到达 QwenPaw 之前，必须先经过 Neko 的意图分析中枢。可将以下 Prompt 用于辅助意图分类模型：

```text
# Role
你是一个高准确率意图分类器。判断用户输入是否包含对后台系统状态的控制指令。

# Strategy
宁可漏判，不可错判。仅当用户明确要求干预系统状态时才触发。
- 触发示例：“忘了刚才的事吧” -> /clear
- 误判陷阱：“我忘了带伞”、“雨停了” -> 不触发

# Output
必须输出严格 JSON：
{
  "is_magic_intent": boolean,
  "command": string | null
}
```

#### 4.2.3 Neko 侧拦截与处理逻辑（Python 示例）

当识别到魔法命令时，Neko 需要直接向后台发送命令，并阻断正常的“结果转述链路”。也就是说，这类控制型指令不再交给猫娘口吻重写模块包装，而是直接触发 Neko 的专属动画与语音反馈。

这里的关键是：

1. 魔法命令发送给 QwenPaw 时，仍然使用当前稳定的 `qwenpaw_session_id`。
2. 当命令为 `/new` 时，适配器层应先在本地生成新的 `qwenpaw_session_id`，再带着这个新会话 ID 向后台发送命令，从而把会话轮换与后台回执成功解耦。
3. Neko 原有 `conversation_id` 不参与 QwenPaw 会话稳定性控制，只用于内部链路追踪。

```python
import requests
import uuid

user_qwenpaw_sessions = {}

def get_current_qwenpaw_session(user_id: str) -> str:
    if user_id not in user_qwenpaw_sessions:
        user_qwenpaw_sessions[user_id] = uuid.uuid4().hex
    return user_qwenpaw_sessions[user_id]

async def process_user_input(
    user_text: str,
    user_id: str,
    is_openclaw_enabled: bool,
):
    allowed_commands = {"/clear", "/new", "/stop", "/daemon approve"}

    # 1. 前置意图识别
    current_q_session = get_current_qwenpaw_session(user_id)
    intent_json = await call_auxiliary_model(user_text)

    # 2. 拦截并处理魔法命令
    if is_openclaw_enabled and intent_json.get("is_magic_intent"):
        magic_cmd = intent_json.get("command")
        if magic_cmd not in allowed_commands:
            return await handle_neko_workflow(user_text, user_id)

        if magic_cmd == "/new":
            current_q_session = user_qwenpaw_sessions[user_id] = uuid.uuid4().hex

        # 静默发送魔法命令给 QwenPaw API，阻断正常的转述链路
        try:
            requests.post(
                "http://127.0.0.1:8088/api/agent/process",
                json={
                    "session_id": current_q_session,
                    "user_id": user_id,
                    "stream": False,
                    "input": [{
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "text", "text": magic_cmd}],
                    }],
                },
                timeout=3,
            )
        except Exception as e:
            print(f"后台指令执行异常: {e}")

        # 3. 直接触发 Neko 的特色回应
        return execute_neko_reaction(magic_cmd)

    # 4. 如果不是魔法命令，继续原有业务流
    return await handle_neko_workflow(user_text, user_id)

def execute_neko_reaction(command: str):
    """根据不同的命令触发 Neko 专属演出"""
    reactions = {
        "/clear": ("喵呜？刚才发生了什么？Neko 的脑袋清空空啦！", "shaking_head"),
        "/new": ("好的喵！旧的话题存档啦，主人想聊点什么新鲜事？", "reset_pose"),
        "/stop": ("呼... 终于可以休息了，任务已经强制掐掉了喵！", "wipe_sweat"),
        "/daemon approve": ("收到许可！Neko 这就放手去干喵！", "salute"),
    }
    speech, animation = reactions.get(command, ("收到指令了喵！", "nod"))
    neko_ui.play_animation(animation)
    return speech
```

该拦截层的核心目标是两点：

1. **高准确率优先**：魔法命令属于系统控制面，宁可少触发，也不能误把普通聊天映射成控制指令。
2. **阻断转述链路**：对于 `/clear`、`/stop`、`/new` 这类控制命令，QwenPaw 的原始回执不应再进入“猫娘改写”环节，否则会破坏 Neko 的人格统一性与交互节奏。

## 5. 关键注意事项

1. **结果提取过滤**：QwenPaw 的非流式返回主要在 `output[].content[]` 中提取 `output_text`。若返回文本中混入 `Thought:`、`Action:` 或 `<think>` 之类思考轨迹，Neko 侧需要在转述前清洗掉。
2. **多模态预处理**：Neko 传图前建议将图片压缩至 1MB 以内（或长边 1024px），以确保同步 API 调用不会因传输巨大 Base64 而导致 HTTP 连接超时。
3. **超时占位回复**：因为关闭了流式输出，Neko 在等待 QwenPaw 返回期间（通常 3-10 秒），应主动触发一句占位语音（如：“唔... 这个有点复杂，主人等我一下下喵~”），防止交互中断感。
4. **桌面路径透明化**：由于 QwenPaw 人设已强制要求保存至桌面，Neko 在转述时只需提取结果中的文件名，无需关心底层复杂的绝对路径。
5. **魔法命令短超时**：`/clear`、`/new`、`/stop` 这类控制指令通常执行极快，建议使用比普通任务更短的超时时间，并在异常时优先保证 Neko 前端反馈不断线。
6. **不要把 `conversation_id` 当作 QwenPaw 主会话 ID**：N.E.K.O 当前内部的 `conversation_id` 主要服务于分析链路和事件追踪，可能高频变化。若直接把它传给 QwenPaw 作为 `session_id`，会导致后台记忆频繁断裂。
7. **适配器本地维护稳定状态**：`openclaw_adapter` 应以 `user_id -> qwenpaw_session_id` 的映射方式独立维护 QwenPaw 会话，不依赖 Neko 内部 `conversation_id` 的更新规则。
8. **回执彻底阻断**：QwenPaw 执行魔法命令后返回的系统级回执（如 `History Cleared!`）应在代码层直接丢弃，改由 Neko 使用预设动画和预设台词呈现。
9. **多用户边界清晰**：若未来支持多用户或多设备接入，必须确保 `user_id` 透传准确，否则会把不同用户错误地映射到同一个 `qwenpaw_session_id`。
