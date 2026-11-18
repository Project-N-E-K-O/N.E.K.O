# Gemini 工具调用的 `thought_signature`

症状：Gemini 连续工具调用，从第二轮请求起 400（`INVALID_ARGUMENT`）。根因是统一的工具调用历史只存了 `id / name / args`，签名在写历史那一步就丢了。

代码：`utils/llm_client/openai_client.py`（流式聚合 `extra_content`）、`utils/llm_client/messages.py`（`ToolCallAggregate.extra_content`）、`main_logic/omni_offline_client/_tools.py`（写历史）、`_genai_support.py`（原生 SDK 路径的双向转换）、`_streaming.py`（换端点时剥离）。测试：`tests/unit/test_tool_call_thought_signature.py`、`tests/unit/test_vision_model_endpoint_switch.py`。

## 协议上的事实

- OpenAI 兼容端点上的载体是 `tool_calls[].extra_content.google.thought_signature`。原生 SDK 上是 `Part.thought_signature`（字节）。历史里统一存前一种 JSON 可序列化的形态。
- 每一步的**第一个** function call 必须带签名，否则 400。并行调用时只有第一条带。
- 回放顺序必须是「全部调用在前、全部结果在后」（FC1, FC2, FR1, FR2），交错成 FC1, FR1, FC2, FR2 会 400。
- 文本 part 上也有签名，但厂商文档明说不强制校验。「只覆盖 function call」是有依据的取舍，不是协议缺口；评审把它报成阻断时按此说明。
- ⚠️ **两条链路的 base64 字母表不同**（实测观察）：兼容端点下发标准 base64（含 `+/=`），原生 SDK 自己序列化出来的是 URL-safe（`-_`）。解码端两种都要收、补齐 padding，但保留严格校验（`validate=True`）：宽松模式会静默丢掉非字母表字符，把损坏的串解成「看起来合理但错误」的字节。
- 老版本 SDK 的 `Part` 不认这个字段会抛异常，所以回放时要有「去掉签名再建一次」的降级。

## 签名绑定它的端点

`extra_content` 只有签发它的端点看得懂，但**历史比路由活得久**。

- `switch_model(vision_model, use_vision_config=True)`（用户发图就会触发，之后整个会话都用新模型）会换掉 `self.llm` / `base_url` / `api_key`，但刻意保留 `_conversation_history`。
- OpenAI 的 Python SDK 不会剥掉 `messages[].tool_calls[]` 里的未知键，厂商私有字段会被原样 POST 给从没签发过它的端点；校验严格的端点会拒掉请求，之后这个会话每一轮都失败。
- 规则：**只在端点或账号真的变了时剥**（`_strip_route_bound_tool_call_extras`）。同一端点换模型不剥，那正是签名要起作用的场景。比较端点时把空串和 `None` 归一、忽略末尾斜杠和主机名大小写这类不代表换厂商的差异。

同一条 assistant 回合上的 `reasoning_content` 有完全相同的「绑定端点」性质，而且更早就存在。它有自己的厂商约定（开思考的端点要求原样回传），目前刻意没有一并处理；动它要单独做。

## 评审这类改动时

对抗式复核里被「驳回」的意见不要直接当终判。反驳理由是「真实数据不会这样」时，去查真实数据到底长什么样（这里的字母表问题就是这么确认的）。
