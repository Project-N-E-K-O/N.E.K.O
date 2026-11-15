# 宿主和别的子系统的交界

## 1. 任务大脑只看正在运行的插件

NekoClaw 的任务大脑挑插件时，候选集是这样筛出来的：

1. `nekoclaw/host/plugin_runs.py` 的 `PluginCatalog.plugins()`：只留 `status == "running"` 的。`status` 由插件服务的 `query_service._resolve_plugin_status` 给出，只有进程真的在跑才是 `running`，其余是 `stopped` / `load_failed` / `source_missing`。
2. `nekoclaw/host/selection.py` 的 `routable_plugins`：去掉 `passive` 的插件和没有可见入口的插件。
3. 入口可见性（`is_entry_hidden`）：入口元数据里 `agent_auto` / `agent_exposed` / `llm_exposed` 任一为 `False`，或 `agent_hidden` 为 `True`，就不可见。`entries` 字段缺失时算有一个默认入口 `run`；是空列表则这个插件没有可见入口。
4. id 以 `__llm_tool__` 开头的入口先被摘掉。那些是注册给主对话模型的工具，归另一条派发路径，留着会让两边抢着派发。

这些规则是从旧分析器原样搬过来的，因为对插件作者承诺过「入口怎么被选中」不变。`tests/unit/nekoclaw/test_contract_plugin_selection.py` 守着这份契约，改筛选规则前先看它。

对宿主这边的含义：

- **一个插件只要在运行、不是 `passive`、带可见入口，它的入口描述（含参数提示）就会进每一次选择**，和它当下有没有活干无关。`nekoclaw/host/decision.py` 的做法是：全部候选的描述加起来不超过 `AGENT_PLUGIN_DESC_BM25_THRESHOLD` 个 token 时，整份直接进选择提示词；超过了才先做本地文本匹配加一次粗筛的模型调用来收窄。也就是说常驻入口越多，要么提示词越长，要么每次选择多一次模型调用。
- 所以内置插件的 `[plugin_runtime].auto_start` 默认值不只是「开机启不启动」，它还决定这个插件的入口描述会不会长期占着选择提示词。入口多的适配器类插件尤其明显：`mcp_adapter` 即使一个 MCP 服务器都没配，自带的静态管理入口也有好几个。它现在默认不自启；代价是真要用 MCP 得先手动启动它。
- 给内置插件开自启动、或给一个常驻插件加一批对模型可见的入口之前，先想清楚这笔常驻开销。只给界面或别的插件用的入口，标成对模型不可见。

## 2. 新的 SDK 语义一律 opt-in

规则：给 SDK 或它背后的宿主端点加新语义时，**不带新参数的既有调用方，行为要逐字节不变**。新行为挂在一个显式的参数或标记后面。

理由：一个还没有任何使用者的新子系统，不该顺手改掉今天就在跑的端点的默认语义。opt-in 也让评审的争议面从「好几处行为变更」缩成「一个开关」。

这条规则落地过的例子在 `main_routers/game_router/runtime.py`（小游戏 SDK）：

- 说话端点的 `wait_for_audio_completion` 是请求参数，默认 `False`。写死成等待的话，内置小游戏的客户端超时比它短，每一句台词都会超时。
- `_public_route_state` 的字段白名单只对带 `_sdk_route_instance_id` 的路由生效。对所有路由生效的话，会砍掉内置小游戏用来设开局状态的字段。
- 会话校验只对带 `sdk_route_instance_id` 的请求强制。历史语义是「不带就不校验」。

怎么用：

- 评审「大块新增 + 顺手改核心路径」的改动时，先划一条线：**今天有没有用户在跑这条路径**。线内的按 opt-in 收口，不收口不合并；线外（纯 SDK 内部）记成「第一个真实接入之前必须补」的清单，不拦。
- ⚠️ 判断「是不是真回归」要去翻现有调用方的代码（比如内置小游戏的前端脚本），不要只靠读后端推理。上面两条都是翻了调用点才确认的，而且实际影响比推断的更严重。

## 3. 插件和记忆的边界

现行规则：**插件不直接操作用户的私聊记忆。**

- SDK 给插件的记忆接口是只读的：`MemoryClient` 只有 `search` 和 `get_persona`（`plugin/sdk/shared/memory/client.py`），经插件服务的适配层（`plugin/server/application/memory/`）转到记忆服务。
- 目标用 `MemoryTarget` 表达，分 `private` 和 `scoped` 两种。`scoped` 带 1 到 8 个 `MemorySubject`，主体类型是 `group_chat` / `participant` / `group_participant`（`plugin/sdk/shared/contracts/memory.py`）。
- 接聊天平台的插件（群聊、第三方私信）要读写记忆，走按主体划分的群组记忆（记忆服务的 `/internal/memory/{角色}/scoped_*` 一组接口）。不许去调主对话用的那几个接口（开新会话取上下文、逐轮写入、结算）。那样会把平台上别人说的话写进用户和角色的私聊记录，后面的记忆整理都会把它当成用户本人的对话。

⚠️ 这条目前是**约定，不是技术上的权限收口**。记忆服务对本机回环来的调用只认「是不是本机」，不认调用方是谁（`utils/instance_access.py` 的 `_local_native`）；插件子进程也能直接 import 宿主的模块。所以：

- 把一个插件移出仓库，**不等于**它就调不到这些接口了。别把「移出去」当成权限收口写进说明里。
- 真要禁止，得在记忆服务这一侧加限制。在那之前，评审插件相关改动时把这条当硬性约定来查。

另外：记忆服务里按角色名寻址的写接口受「角色释放围栏」保护，被围栏时返回 409。插件打这些接口要能处理 409。见 `references/adjacent-fences.md`。

## 4. `[MESSAGE FORWARD]` 日志和占位符替换

插件服务日志里的这一行：

```
[MESSAGE FORWARD] Plugin: X | Source: … | Priority: N | Message ID: … | Content length: L
```

是插件消息被转发时打的（`plugin/core/communication.py` 的 `_forward_message`），**早于宿主对消息做的任何处理**。读它的时候注意：

- `Content length` 只算文字部分。只带图片的推送（比如纯截图）长度是 0，不是内容丢了。
- 同一条内容在去重窗口内重复转发，只打一行 `suppressed N duplicates`。
- 它反映不了占位符有没有被替换。

占位符的契约（写在 `plugin/sdk/plugin/base.py` 里 `push_message` / `finish` 的说明中）：插件文本里可以写 `{MASTER_NAME}` / `{LANLAN_NAME}`，由宿主在**注入边界**按会话替换，因为插件不知道这段文本最后会进哪个会话。

- 替换的唯一实现是 `main_logic/core/callback_render.py` 的 `apply_role_placeholders`。新增「把插件文本注入给模型或显示给用户」的位置，都要过这个函数，不要另写一份替换。
- 名字为空时占位符**原样保留**，不替换成空串。这是有意的：留着字面量比留一个空洞不容易误导。
- 主日志出于隐私不打指令原文。所以不能凭插件侧的日志断言「宿主没替换」；要验证，用同样结构的回调直接调 `_build_callback_instruction(..., master_name=...)` 看结果。

## 5. 已有文档写清楚的取舍，评审里别再翻

**插件页面写路由的 token**（`docs/design/security/local-mutation-auth.md`）。会反复出现两类互相矛盾的意见，都按现行规则回复：

- 「页面路由应该强制 token」→ 不收。市场上已发布的插件页面不带 token，强制会让它们失效。对完全匹配或已配置的来源，它们本来就能读到 token，强制不增加边界。例外是 NAS 的 hostname 兜底：同一台 NAS 上别的端口的页面能过来源校验，却因为 CORS 读不到 token，强制 token 拦得住它们。默认不强制是为了兼容做的取舍，公网部署者可以自己开 `NEKO_PLUGIN_PAGE_MUTATION_REQUIRE_TOKEN=1`。
- 「只靠 hostname 兜底通过、又没有 `Sec-Fetch-Site` 头的请求应该拒绝」→ 不收。浏览器对纯 HTTP 的局域网来源不发这个头，这时「外层代理改了端口的市场插件页面」和「同一台 NAS 上别的端口的应用」在请求上分不开，按市场插件优先放行。反过来「带了 `same-site` / `cross-site` 也应该放行」同样不收。
- 新增插件页面会直接调用的写路由，用 `PluginPageMutationGuardedRoute`。

**回复尾图**（`docs/plugin-reply-tail.md`）。已经定下、文档里写明的行为：一次调用的上下文从工具调用那一刻起保留 120 秒；整条回复的附件提交阶段有 5 秒期限；原文一直不可见的图片到期丢弃。评审里出现下面这些，按既定结论回复：

- 「把提交附件挪到回合收尾之后，或放进后台任务」→ 不收。连接卡住时后面的发送同样会卡，挪了省不下等待，还会和下一轮并发，归属要重新论证；5 秒期限已经封顶。
- 「120 秒应该从回复结束起算」「连接写异常应该记成 uncertain 而不是 failed」→ 不收，现行为已在文档里说明。

**知识包市场订阅**（`plugin/server/routes/knowledge_market.py` 的模块说明）。几条容易被当成 bug 的设计：

- 插件服务这边的任务到 `stage: "staged"` 就结束。包有没有真的可用，由市场页去对 `GET /market/knowledge/subscriptions` 的结果，不由这个任务负责。
- 退订请求带上已知的全部版本，只作废列出的版本；包不在、或装的是列表外的版本，都算成功。重复发送是安全的。
- 插件服务从不直接和记忆服务说话，知识相关的读写都经主服务的代理。
- 配对用的令牌沿用插件市场已有的那一个，不为知识包另设权限更窄的令牌。「应该用更窄的令牌」这类意见按「和现有插件市场配对一致，没有引入新权限」回复。
