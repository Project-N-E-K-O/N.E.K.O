# proactive_chat reason_code 说明书

状态：v1 可观测性说明草案。`reason_code` 与 `stage` 已进入响应体。

本文说明 `/api/proactive_chat` 返回值中的 `reason_code` 字段。它的目标不是改变主动搭话策略，而是让维护者、测试者和前端调试工具能读懂：

- 本轮主动搭话最终为什么 `chat`、`pass` 或 error。
- 这个原因发生在主动搭话流程的哪个粗粒度阶段。
- 出现该原因时，维护者优先检查哪些模块或运行条件。

## 设计原则

### 1. `reason_code` 是稳定机器码

`reason_code` 应保持稳定、短小、可统计。它不承载自然语言解释，也不应该因为文案调整频繁改名。

推荐用途：

- 单元测试和浏览器 contract 测试。
- 后端日志聚合。
- 前端调试面板。
- 统计主动搭话跳过、失败、成功的分布。

不推荐用途：

- 直接展示给普通用户。
- 放入会随语言变化的自然语言句子。
- 把临时 debug 文案编码成新的 code。

### 2. `action` 和 `reason_code` 分工明确

`action` 只回答“前端应该怎么处理这轮结果”：

- `chat`：本轮已经投递主动搭话。
- `pass`：本轮没有投递主动搭话。
- 缺省或 error 响应：本轮请求失败。

`reason_code` 回答“为什么是这个结果”。

### 3. `stage` 解释原因发生的流程阶段

当前 v1 响应同时包含 `reason_code` 和 `stage`：

```json
{
  "action": "pass",
  "reason_code": "PASS_SOURCE_EMPTY",
  "stage": "source_selection"
}
```

`stage` 表示原因发生的流程阶段，`reason_code` 表示稳定原因。

注意：`stage` 是粗粒度阶段，不是完整链路 trace。它用于帮助维护者快速定位排查方向。

## 流程阶段建议

| stage | 含义 |
|---|---|
| `entry_guard` | 请求入口、角色存在性、静默模式、游戏路由、并发状态等早退检查 |
| `activity_gate` | 用户活动、隐私、专注/游戏/屏幕限制、概率节流等判断 |
| `source_selection` | web、vision、music、meme、未收尾话题等素材收集与筛选 |
| `model_decision` | Phase 1/Phase 2 模型判断是否值得说、说什么 |
| `generation` | 模型生成文本、流式输出、格式自救 |
| `dedup` | 字面相似度、BM25、素材级去重 |
| `delivery` | TTS/消息投递、用户接管、落库提交 |
| `runtime_error` | 超时、配置异常、内部异常 |

v1 响应体会包含 `stage`。目前它由 `reason_code` 默认映射生成，少数跨阶段原因可在具体分支中覆盖。

## reason_code 对照表

| reason_code | action/error | 建议 stage | 含义 | 优先检查 |
|---|---|---|---|---|
| `CHAT_DELIVERED` | `chat` | `delivery` | 本轮主动搭话已经成功投递。 | 检查 `source_mode`、`source_tag`、`source_links`、`turn_id` 是否符合预期。 |
| `PASS_BUSY` | `pass` 或 409 error | `entry_guard` | 当前 AI 正在响应、已有 proactive 在跑，或语音 guard 拒绝。 | 检查 session state、`try_start_proactive`、`can_start_proactive`、语音会话状态。 |
| `PASS_ACTIVITY_BUSY` | `pass` | `activity_gate` | 用户近期活跃、语音会话进行中，或 session 活动状态不适合在本轮主动搭话。 | 检查用户活跃状态、`prepare_proactive_delivery`、WebSocket/session 可用性。 |
| `PASS_DELIVERY_BUSY` | `pass` | `delivery` | 投递准备阶段拒绝提交本轮主动搭话，例如 mini-game invite 已决定要发但 delivery guard 未放行。 | 检查 `prepare_proactive_delivery`、当前 speech id、投递前 session 状态。 |
| `PASS_DISABLED` | `pass` | `entry_guard` | 主动搭话被静默模式或等价开关禁用。 | 检查角色是否处于 goodbye silent 或主动搭话相关开关。 |
| `PASS_ROUTE_ACTIVE` | `pass` | `entry_guard` | 游戏路由正在接管交互，普通主动搭话跳过。 | 检查 `game_router.is_game_route_active` 和游戏会话状态。 |
| `PASS_PRIVACY` | `pass` | `activity_gate` | 用户处于隐私/关闭倾向状态，主动搭话不得继续读取或打扰。 | 检查 activity snapshot 的 `propensity`、privacy mode、当前前台应用分类。 |
| `PASS_RESTRICTED_SCREEN_ONLY` | `pass` | `activity_gate` | 当前只允许 screen-only 场景，但本轮没有可用 vision/screen 来源。 | 检查 vision 模型配置、截图权限、enabled modes。 |
| `PASS_THROTTLED` | `pass` | `activity_gate` | 概率节流或频率门控让本轮跳过。 | 检查 `skip_probability`、用户活动强度、前端调度间隔。 |
| `PASS_SOURCE_EMPTY` | `pass` | `source_selection` | 没有可用信息源，或信息源获取/筛选后没有可讲内容。 | 检查 enabled modes、web/music/meme/vision 获取结果、未收尾话题是否存在。 |
| `PASS_MODEL_PASS` | `pass` | `model_decision` | 模型或规则判断本轮不值得说，或输出 `[PASS]`。 | 检查 Phase 1 结果、prompt、模型配置和筛选理由。 |
| `PASS_GENERATION_EMPTY` | `pass` | `generation` | Phase 2 输出为空、格式自救失败，或流式输出被拦截。 | 检查 Phase 2 streaming、tag 格式、生成内容是否被清空。 |
| `PASS_DUPLICATE` | `pass` | `dedup` | 输出与近期主动搭话过于相似，或 BM25 regen 后仍判定重复。 | 检查 `_proactive_chat_history`、相似度阈值、BM25 分数、素材级豁免逻辑。 |
| `DELIVERY_PREEMPTED` | `pass` | `delivery` | 用户在投递前或投递中接管，本轮主动搭话未提交。 | 检查 proactive speech id、state preempted、用户输入时间线。 |
| `DELIVERY_FAILED` | `pass` | `delivery` | 生成结果存在，但 buffered delivery/TTS/提交阶段失败。 | 检查 `finish_proactive_delivery`、TTS、前端连接、异常日志。 |
| `ERROR_TIMEOUT` | error | `runtime_error` | `/api/proactive_chat` 外层处理超时。 | 检查模型响应时间、stream timeout、网络和 provider 状态。 |
| `ERROR_INTERNAL` | error | `runtime_error` | 内部异常或模型配置异常。 | 检查 server traceback、模型配置、依赖服务。 |
| `ERROR_CHARACTER_NOT_FOUND` | error | `entry_guard` | 请求中的角色不存在或 session manager 找不到角色。 | 检查 `lanlan_name`、角色配置、session 初始化。 |
| `ERROR_SOURCE_FETCH_FAILED` | error 或 `pass` | `source_selection` | 已启用的信息源全部获取失败。它表示后端/外部来源故障，不等同于正常没有素材。 | 检查 web/music/meme/vision 获取异常、网络、依赖服务和 source fetch 日志。 |
| `PASS_UNSPECIFIED` | `pass` | unknown | 兼容兜底码。旧分支缺少明确 code 时由 helper 补上。 | 新增或重构分支时应替换为更具体的 code。 |

## 当前 v1 落地状态

已完成：

- `main_routers/system_router.py` 中已集中定义 `PROACTIVE_REASON_*` 常量。
- `main_routers/system_router.py` 中已集中定义 `PROACTIVE_STAGE_*` 常量和 `reason_code -> stage` 默认映射。
- 已提供 `_proactive_pass_body`、`_proactive_chat_body`、`_proactive_error_body` helper。
- `_end_proactive` 会对缺少 `reason_code` 或 `stage` 的旧式响应做兜底补码和补阶段。
- 主动搭话主流程和 mini-game invite 路径的主要 `chat` / `pass` 出口已经补码。
- 单元测试已加入静态防漏检查，避免新增裸 `{"action": "pass"}` 或 `{"action": "chat"}` 时漏填 `reason_code`。
- REST 文档已说明 `/api/proactive_chat` 响应包含稳定 `reason_code` 和粗粒度 `stage`。

尚未完成：

- 尚未提供面向前端调试面板的 code -> 文案映射表。
- 入口 CSRF/origin 校验失败属于通用本地 mutation 校验，目前不是 proactive 专用 `reason_code`。
- 浏览器 contract 报告验证的是响应形状，不代表每个生产业务分支都自然触发过。

## 新增 reason_code 的规则

新增 code 前先判断：

1. 这个原因是否会被统计或测试长期依赖。
2. 现有 code 是否已经能准确表达。
3. 它是流程阶段差异，还是原因差异。

建议新增 code 的情况：

- 维护者看到现有 code 后仍无法判断该查哪段逻辑。
- 两个原因虽然都导致 `pass`，但修复方向完全不同。
- 前端调试面板需要区分它们。

不建议新增 code 的情况：

- 只是自然语言 message 不同。
- 只是同一原因在不同语言下的显示不同。
- 只是临时 debug 或实验分支。

## 维护检查清单

修改 `/api/proactive_chat` 时应检查：

- 新增 `action: "pass"` 或 `action: "chat"` 的响应是否带 `reason_code`。
- 新增 error 响应是否带 `reason_code`。
- 新增响应是否能得到准确 `stage`，必要时覆盖默认映射。
- 新增 code 是否同步到本说明书。
- 新增 code 是否进入浏览器 contract 测试或等价测试。
- 如果一个 code 覆盖范围变大，是否会让排查信号变钝。

## 后续建议

短期建议：

- 把本说明书作为 reason-code 标准表维护。
- 给前端调试面板增加 code -> 中文解释映射。
- 为 `PASS_UNSPECIFIED` 增加统计或日志提醒，避免新分支长期依赖兜底。

中期建议：

- 继续细化少数跨阶段 code 的分支级 `stage` 覆盖。
- 将 activity gate、source selection、model decision 的早退判断逐步抽成小函数，让 code 和 stage 映射更集中。

长期建议：

- 在 debug 模式下输出轻量 trace，例如本轮经过哪些 gate、选中了哪些 source、在哪个阶段结束。
- 普通响应仍保持简洁，只暴露最终 `reason_code` 和 `stage`。
