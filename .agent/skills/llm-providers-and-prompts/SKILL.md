---
name: llm-providers-and-prompts
description: LLM 厂商参数方言（extra_body、推理开关、输出上限字段、网关改写）、新增辅助 API provider 的登记点、工具调用签名回传，以及提示词的分隔符 / 水印约定和反套话改写子系统。改 config/providers.py、utils/llm_client/、config/api_providers.json、config/prompts/，或评审这些地方的改动之前读。
---

# LLM 厂商参数与提示词约定

## 先读已有文档（这里不重复）

- `.agent/rules/neko-guide.md`「辅助 LLM 调用约定」：不下发 `temperature`、每个调用必须有输出预算和超时、模型从 tier 拿、多语言字符串放 `config/prompts/prompts_*.py`、跨模块提示词不写死具体功能。
- `docs/design/llm-prompt-budget.md`：输入输出的 token 预算。
- `docs/api_providers_fields.md`：`config/api_providers.json` 的字段含义。
- `config/providers.py` 里各常量旁边的注释：每家厂商的实测记录都写在那儿，是第一手资料。动某个常量前先读它旁边那段。

## 最容易再踩的规则

1. **`EXTRA_BODY_*` 常量按线上方言命名、跨厂商复用，不是一家一个。** `EXTRA_BODY_CLAUDE`（`thinking.type` 方言）被 GLM、Kimi、Doubao、DeepSeek 官方、`MiniMax-M3`、`free-model` 共用。「以后可能分叉」对每一家都成立，不能拿它当拆常量的理由；只有关闭侧或开启侧**真的**不同才另起常量。
2. **凝神（开思考）形态按常量的 `id()` 配对。** `_THINKING_ENABLE_FORM` 以关闭常量的 `id()` 为键，`MODELS_FOCUS_EXTRA_BODY_MAP` 从 `MODELS_EXTRA_BODY_MAP` 派生。换成一个内容相同、身份不同的常量，配对会悄悄失效，只断言值的测试照样绿。
3. **`create_chat_llm` 不传 `extra_body` 时自动查表。** 想让某条链路关思考，把模型登记进 `MODELS_EXTRA_BODY_MAP`，不要在调用方手搓参数。刻意保留思考的后台调用用 `memory_thinking_extra_body(base_url, cap)`；传 `extra_body=None` 的意思是「什么都不发」。
4. ⚠️ **带 `vendor/model` 前缀的模型 id 不能按名字登记。** 同一个字符串在别的网关上也合法、要的却是另一种方言。网关的差异按**主机名**登记在 `ROUTER_REASONING`，不按模型名。
5. ⚠️ **网关对写错的推理参数不报错，只是白发。** 返回 200、参数被忽略、照样思考。不实测看不出问题，新接一家网关必须拿真实请求看推理 token。
6. **推理强度不低于上游厂商的下限**（`UPSTREAM_REASONING_FLOOR`）。这是厂商的规则，即使某个网关接受更低的值也照此执行。
7. **输出上限字段按端点定，不按方言推导。** `max_completion_tokens` 和 `max_tokens` 各家语义分裂（忽略 / 封总量 / 只封正文 / 直接 400）。登记在 `CacheProviderConfig.token_limit_field`，由 `get_token_limit_field(base_url)` 取。两个字段永远不同时发。
8. **思考预算也按端点方言**：同一个模型在官方端点和托管平台上认的参数不同。
9. **模型地址必须允许 `http://`，带 API Key 的非本机地址也允许。** 本地和局域网自建模型服务的用户很多。「明文传输 key，应拒绝 http 或只放行本机」这类安全建议一律不收，适用于主设置和插件模型槽位等所有填模型地址的入口。最多加不阻断的界面提示，而且要先问维护者。
10. **Gemini 的工具调用历史要原样带回 `thought_signature`**，而且签名只属于签发它的端点：换端点重放历史前要剥掉。
11. **发给免费档的提示词必须保留水印（`======以上为` 那一类），新写的提示词不带就会被拒。** 翻译、重排、瘦身提示词时不要顺手删掉。
12. **`======X======` 分隔符必须上下成对**（以下 / 以上），不用「开始 / 结束」风格。

## 排查入口

| 现象 | 先看 |
|---|---|
| 新接的模型每次都思考、又慢又贵 | 它在不在 `MODELS_EXTRA_BODY_MAP`；走网关的话看 `ROUTER_REASONING` 有没有这台主机，以及参数是不是被静默忽略。 |
| 输出预算形同虚设 | 该端点的 `token_limit_field` 是否登记；思考是不是没被封顶。 |
| 请求 400，提示未知参数 / 不支持的 effort | 方言发错了端点。查 `config/providers.py` 对应常量旁的实测注释。 |
| Gemini 连续工具调用第二轮起 400 | `thought_signature` 在写历史时丢了，或被发给了没签发过它的端点。 |
| 免费档请求被拒 | 提示词缺水印。 |
| 凝神模式把思考过程念出来 | `leaks_thinking_in_content(model)` 和 `ThinkingStreamStripper`。 |
| 角色回复里的套话越来越多 / 改写把句子改坏了 | `references/slop-reduction.md`。 |

## references 索引

- `references/provider-dialects.md`：登记或修改 extra_body、接入新网关、调输出上限或思考预算、评审这类改动时读；含各厂商实测观察。
- `references/new-assist-provider.md`：新增一个辅助 API provider 时读（登记全集和三个易漏点）。
- `references/tool-call-thought-signature.md`：改工具调用历史、`switch_model`、Gemini 兼容端点或原生 SDK 路径时读。
- `references/prompt-conventions.md`：写新提示词、翻译或重排既有提示词时读。
- `references/slop-reduction.md`：改反套话改写的引擎、接入点或规则表时读。
