# 厂商参数方言：extra_body、网关、输出上限、思考预算

全部登记在 `config/providers.py`。客户端侧的消费点在 `utils/llm_client/factory.py`（`create_chat_llm`）和 `utils/llm_client/openai_client.py`（`ChatOpenAI._params`）。

## extra_body 的登记方式

- `MODELS_EXTRA_BODY_MAP`：模型名 → 「常规回合」的 extra_body，绝大多数是关思考的形态。
- `_THINKING_ENABLE_FORM`：关闭常量的 `id()` → 对应的开启常量。按各家 API 自己的语义配对，不是机械地把布尔值翻过来。
- `MODELS_FOCUS_EXTRA_BODY_MAP`：凝神回合用的表，由上面两张表推导（有配对就用开启形态，没有就保留原值）。`focus_extra_body()` 返回深拷贝。

新模型接入时的判断顺序：

1. 它的线上形态和哪个现有常量相同？相同就复用，加进 `MODELS_EXTRA_BODY_MAP`。
2. 只有关闭侧或开启侧任何一边真的不同，才另起常量。例：原生 Anthropic 的关闭形态和 `EXTRA_BODY_CLAUDE` 字面相同，但开启侧不同（新模型要求 `adaptive`，发 `enabled` 直接 400），所以单独有 `EXTRA_BODY_ANTHROPIC`，并且**故意不进** `_THINKING_ENABLE_FORM`：凝神时保持关思考，安全降级。
3. 关不掉思考的模型（Step 的 flash 系列、MiniMax M2.x 等）登记「最低档」或「只改输出格式」的常量，同样不进配对表，凝神时原样保留。
4. 完全不该发任何东西的模型不登记，`get_extra_body` 返回空 dict。

连带机制：

- `_THINKING_ENABLE_FORM` 唯一的消费者是 `MODELS_FOCUS_EXTRA_BODY_MAP` 的推导式，而推导式只遍历 `MODELS_EXTRA_BODY_MAP` 的值。一个常量不进那张 map，给它在配对表里加开启形态就是死代码。
- 配对按 `id()`。测试要钉身份：`MODELS_EXTRA_BODY_MAP[model] is EXTRA_BODY_X`，只比较值抓不到「换成了内容相同的另一个对象」。
- 非思考类的额外参数（如某模型自带的联网搜索工具）也在这张表里，天然不在配对表中，凝神时原样保留。
- `create_chat_llm(extra_body=<不传>)` 自动调 `get_extra_body(model, base_url)`。`extra_body=None` 是显式「不发」。
- 记忆系统里刻意开着思考的后台调用（复核、信号检测、反思合成等）用 `memory_thinking_extra_body(base_url, output_cap)`：按端点给一个压低思考量的参数，未登记的端点返回 `None`（原生思考）。它把 `thinking_budget` 夹到输出上限的一半，给正文留位置。

## 为什么不能按模型名登记带前缀的 id

`MODELS_EXTRA_BODY_MAP` 纯按模型名查，不看端点。对「厂商自己的裸模型名」这通常没问题：转售方多半用别的键名。

⚠️ 但 `vendor/model` 这种命名空间 id 会撞名。`anthropic/claude-...` 在 OpenRouter 上合法，在 Anthropic 兼容网关上也合法，两边要的方言不同（一个要 `reasoning:{effort}`，一个要原生 `thinking` 块）。按名字登记其中一种，就污染了另一个网关上的同名 id，方言错配可能 400，而 `create_chat_llm` 默认拦不住。

规则：

- 登记前先问「这个 id 字符串在别的已接入网关上是否也合法」。撞名就**不要**按名字登记。
- 网关的差异走 `ROUTER_REASONING`：主机名 → `RouterReasoningConfig`。一行描述这台主机怎么把自己的模型 id 映射到已登记的上游 id（剥前缀、点号版本转连字符、别名），以及怎么把登记的 body 改写成它认的形态（`reasoning:{effort}` 提到顶层，或按模型给一对「常规 / 凝神」的 effort）。
- `get_extra_body` / `focus_extra_body` 都带 `base_url`，经 `resolve_router_model` 做映射；`ChatOpenAI._params` 在发请求时调 `rewrite_router_reasoning`，所以凝神的覆盖值走的是同一套线上方言。
- OpenRouter 不需要行：它的方言就是注册表自己的形态。
- 「不发就等于不思考」对某个厂商成立，不代表以后一直成立。新模型可能默认思考、且不接受 `disabled`。

`UPSTREAM_REASONING_FLOOR`（加前缀表）是上游厂商允许的最低推理强度。所有发往网关的 effort 都经 `clamp_reasoning_effort` 抬到下限以上；这是厂商的规则，哪怕某个网关接受更低的值也照此执行。匹配时剥掉网关前缀和变体后缀，精确 id 优先于前缀。

守卫：`tests/unit/test_router_reasoning.py`。

## 输出上限字段

`CacheProviderConfig.token_limit_field`，按 `base_url` 子串匹配到 provider 后取；未知端点保持 `max_completion_tokens`。Anthropic 协议的客户端不查这张表。

各家语义（实测观察，开思考时测的；细节和日期见常量旁注释）：

| 端点 | `max_completion_tokens` | `max_tokens` |
|---|---|---|
| DeepSeek 官方、智谱 GLM（国内 / 国际） | 静默忽略 | 封总量（思考加正文） |
| 硅基流动 | 静默忽略 | 只封正文，思考要靠 `thinking_budget` |
| 百炼（DashScope）、豆包 | 封总量 | 只封正文，思考不受限 |
| OpenAI 原生新模型 | 封总量 | 直接 400 |
| MiniMax-M3、Step、Gemini 兼容端点、OpenRouter、Kimi | 封总量 | 封总量 |

- 两个字段同时发：豆包、Gemini、OpenAI 返回 400。所以永远只发一个。
- 结论：**不能从 extra_body 方言推导上限字段。** 豆包和 DeepSeek 同属 `thinking.type` 方言，上限语义相反；硅基流动属 `enable_thinking` 方言却忽略 `max_completion_tokens`。只能按端点登记。
- 修上限字段会让那些端点上「开着思考的调用」第一次真的被封顶，要和思考预算的改动一起落地，否则正文被思考挤没。

## 思考预算按端点方言

同一个模型在不同端点认不同的参数（实测观察）：DeepSeek 官方认顶层 `reasoning_effort`；同一模型托管在百炼上只认 `thinking_budget`，发 `reasoning_effort` 被忽略；硅基流动要 `enable_thinking` 加 `thinking_budget` 一起发才有上限，只发 `thinking_budget` 反而会关掉某些模型的思考。所以 `memory_thinking_extra_body` 挂在 `CacheProviderConfig` 上，按端点取。

## 接入新网关的做法

1. 用真实 key 发最小请求，分别试：顶层 `reasoning_effort`、`reasoning:{effort}`、`enable_thinking`、`thinking:{type}`、Gemini 的 `extra_body.google.thinking_config`。看返回里的推理 token，不看状态码。
2. 试 `max_completion_tokens` 和 `max_tokens` 各自封的是总量还是正文，以及同时发会不会 400。
3. 按结果在 `ROUTER_REASONING` 加一行；如果它对不同上游认不同参数（有的网关就是这样），就不要做整台主机的统一转换，按模型给 effort 表，其余保持登记的 body 原样发。
4. 实测结论写进常量旁的注释，带日期；没法付费实测的部分明说「未实测」。
5. 加测试钉住每类模型最终发出去的请求体。

已知网关的行为（实测观察，摘要；完整记录在 `config/providers.py` 注释里）：

- 有的网关只认顶层 `reasoning_effort`，其余四种写法全是 200 但被忽略。
- 有的网关按各上游原生格式透传：OpenAI 系认顶层 effort，Anthropic 系认 `thinking` 块，`reasoning:{...}` 和 `enable_thinking` 被忽略。
- 有的网关按上游各认各的：DeepSeek 只认 `reasoning:{effort:none}`，Gemini 只认顶层 effort，Claude 透传原生 `thinking` 块。
- Gemini 3.x 的若干模型关不掉思考：发 `none` 或 `minimal` 返回 400，或者被忽略。

## 其他实测观察

评审机器人常凭文档推断这些点，以下以实测为准（均为实测观察，随厂商更新可能变化）：

- **Qwen realtime 输入转写**：会话创建时自带转写，客户端不传 `input_audio_transcription` 也照样收到转写完成事件。
- **Qwen realtime 的 VAD**：`qwen3` 系列接受 `semantic_vad`；旧的 turbo realtime 模型收到后不回 `session.updated`。代码按模型名里有没有 `qwen3` 选 VAD 类型（`_transport.py`）。
- **Qwen 混合思考模型**：3.5 / 3.6 / 3.7 把思考过程流进 `content`，需要 `ThinkingStreamStripper`；3.8 走 `reasoning_content`，`content` 是干净的，**不要**给它挂剥离器（会把回答憋到流结束，主动搭话会超时）。判定在 `leaks_thinking_in_content`。
- **DeepSeek**：新一代 Flash 的官方调用名是 `deepseek-flash`，旧名仍被接受并在服务端路由到同一模型。
- **MiniMax**：M3 支持关思考（`thinking.type`，复用 `EXTRA_BODY_CLAUDE`）；M2.x 关不掉，只能用 `reasoning_split` 把思考从正文里分出去。
- **原生 OpenAI**：Chat Completions 只认顶层 `reasoning_effort`，嵌套的 `reasoning:{effort}` 返回 400；可用的最低档因模型而异（有的没有 `none`，有的没有 `minimal`），所以关思考有两组常量。
- **OpenRouter 上的 Gemini 3.5 及以后**：拒绝 `effort=none`，要用 `minimal` 或 `low`。
- **Anthropic 的 OpenAI 兼容端点**没有 prompt caching，注入的 `cache_control` 被静默忽略。不要给它开 body 级缓存标记。

仓库里另有 `.agent/skills/gemini-openai-api/SKILL.md` 讲 Gemini 兼容端点的 extra_body 双层嵌套。注意那份文档说常量加在 `config/__init__.py`；现在定义在 `config/providers.py`，`config/__init__.py` 只是转出，新常量加在前者。
