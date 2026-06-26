# NEKO Live 开发文档

本文档面向后续参与 `neko_roast` 的开发者，记录**已落地设计**。它是架构边界、模块边界、协作规范、测试门禁和文档要求的 Canonical Source。配套 `live-center-roadmap.md` 只记录阶段目标、完成状态和下一阶段顺序。

对旧插件 `bilibili_danmaku` 采取**选择性复用**：取其**连接+解析层**（`danmaku_core` / `livedanmaku`）与**扫码登录**（`bili_auth_service`），**弃**其自带 LLM / orchestrator / memory（neko_roast 走 NEKO 统一 `dispatcher` → `main_server` 人设）。不直接复制大文件；迁移能力时拆成小模块并补测试证明边界仍成立。

## 命名与范围

当前产品名是 **NEKO Live**。`neko_roast` 是历史包名和内部代号，不作为用户可见产品名扩展。历史代号「猫娘锐评」只用于解释 v0.1 起点；新增文档、UI 文案、i18n 和 manifest 应使用 **NEKO Live**。

“直播中心 / Live Center”是架构定位，表示把主播直播的生命周期接进 NEKO；“弹幕锐评”是当前已落地的 v0.1 功能模块。后续新增模块时不要把产品名、架构定位和单个功能模块混用。

## 当前实现快照

更新日期：2026-06-23

核心闭环：**真实 B站直播间监听 → EventBus → live_events Selection → Roast Pipeline → Runtime → Dashboard**。`neko_roast` v0.1 已进入主线，产品命名已统一为 **NEKO Live**；「弹幕锐评」是第一个落地的垂直切片。锐评采用**自适应焦点**（昵称与头像哪个更有料就主打哪个，看不到的头像绝不脑补）。

协作基线：Phase 1 已落地 Canonical Source、PR 拆分规则和 Reviewer Checklist；Phase 2A 已落地模块 Owner Model 与 Protected Modules / Review Gate。Reviewer Checklist 的唯一 Canonical Source 是 `AGENTS.md`。

已落地能力（详见对应章节）：

- **真实直播接入**：吞并 `DanmakuListener`，`connect/disconnect` 启停真实监听（DoD 真机验证：新观众首条弹幕 → 猫全自动锐评其昵称+头像）。
- **事件中枢窗口择优**（P2.5）：富模型 `on_event` + `get_score` 冷却期缓冲择优（弹幕 / 礼物 / SC / 上舰同窗竞争）+ 首评即时。见「直播事件中枢」。
- **B站登录态**（P5）：扫码登录 + Fernet 加密凭据，接进头像抓取 / 弹幕连接 / 查询，根治 -352。见「B站登录态」。
- **健壮性**：`dry_run` 安全态、限流 / 自动急停 / 队列、配置写竞争免疫、查询 -352 友好降级、房号支持直播间链接输入。见各对应章节。
- **开发者沙盒**：离线 UID / URL 调试、内置 demo 案例。

主要链路（直播弹幕路径）：

```text
弹幕 WS → danmaku_core → on_event(LiveDanmaku 富模型)
  -> bili_live_ingest 包成 LiveEvent → event_bus.publish(type)   事件中枢路由（按 type 分发，见「直播事件中枢（EventBus）」）
  -> live_events 订阅 "danmaku" / "gift" / "super_chat" / "guard"（冷却期缓冲、get_score 择优 / 空闲态首条即时）
  -> handle_live_payload -> pipeline.handle_event:
       safety_guard.before_event()      连接/暂停/队列闸门
    -> bili_identity.resolve()           UID→昵称/头像/META（登录态过 -352）
    -> viewer_profile.upsert() / 沙盒临时
    -> viewer_gate.check_once_per_uid()  每 UID 一次出场锐评；后续普通弹幕不整体跳过
    -> avatar_roast.build_request()      首次出场：头像 / ID / 第一句话自适应焦点锐评 prompt
       or danmaku_response.build_request() 后续普通弹幕：接住当前弹幕，不复用首评模板
    -> safety_guard.before_output()      限流
    -> neko_dispatcher.push_roast()      唯一出口；dry_run 时短路
  -> plugin.push_message -> main_server → 视觉模型 → 猫开口
```

（开发者沙盒 / demo 走同一 pipeline，仅 `source` 不同；详见「Pipeline」。）

开发者模式写入 `developer_tools_enabled`，是调试总控开关。关闭时沙盒查询、模拟弹幕、内置案例和聊天开发者工具都不可用；清空沙盒记录仍可用。沙盒查询和锐评只使用临时 profile，不写观众档案，不进入直播总结，只进入开发者沙盒的运行时最近记录。

沙盒 UID 查询只返回 UID、昵称 / 名字、邮箱字段、头像 URL、头像 MIME、`has_avatar`，以及头像形态 META（`is_default_avatar`、`is_animated_avatar`、`pendant`）。不返回头像 bytes，不返回 base64 data URL，不写本地长期 preview 文件。

内置案例使用 `target="__demo__"`，固定 UID `9000000000000001`、昵称“粉桃猫猫观察员”、头像 `fixtures/demo_avatar.png`，不访问 B 站，用于确认头像输入、pipeline、dispatcher、沙盒结果和 audit 链路。

## 设计原则

- 直播入口和开发者沙盒必须共用 `core/pipeline.py`。
- 所有 NEKO 输出只允许走 `adapters/neko_dispatcher.py`。
- 所有观众档案写入只允许走 `stores/viewer_store.py`。
- 所有审计记录只允许走 `stores/audit_store.py`。
- 直播安全门是必经路径，不允许绕过 `core/safety_guard.py`。
- 隐私相关原始数据不要写入 logger；需要调试时写脱敏 audit，或按项目规范使用 `print`。
- 登录凭据只走 `stores/credential_store.py` 加密落盘，**绝不**写 audit / log / config / UI（只回显 uid / 用户名 / 是否登录）。

## Runtime Observability

运行态观测语言的 Canonical Source 是 `runtime-observability.md`。本文只保留硬约束摘要：

- Phase 2C 当前停靠点：Dispatcher Outcome、Selection Decision Chain 和 Runtime Health Rows 已落地；Runtime Timeline、事件级 `trace_id`、Dashboard 展示和 Monitor emission 尚未落地。
- 若恢复 Runtime Timeline 方向，下一步必须先做 Event Trace ID Standardization；不要用 UID、event type 或时间邻近关系猜测同一条事件。
- 新事件路径必须能说明 Runtime Timeline 中的 stage、outcome 和 skip reason。
- 预期拦截使用 `skipped`，异常使用 `failed`，降级继续运行使用 `degraded`。
- Safety Guard 和 Dispatcher 必须是可见 lifecycle stage，不能被新模块绕过或隐藏。
- Dashboard 未来必须能解释“事件到了哪、为什么没输出、Dispatcher 是否真实输出”，但具体 UI 布局不在本文规定。
- 观测数据必须脱敏；不得暴露 raw payload、cookies、tokens、avatar bytes/base64 或未脱敏私密数据。

## 协作规范

`neko_roast` 已进入多人协作阶段。后续改动必须先按 Feature → Slice → PR 拆分，保持每个 PR 可独立 review、测试和回滚。

### Feature → Slice → PR

- **Feature**：面向用户或主播的完整能力，例如“某类直播互动处理”或“某个配置工作流”。
- **Slice**：Feature 中可独立合并的垂直切片，必须有清晰入口、影响模块、测试范围和文档影响。
- **PR**：一个 PR 只承载一个 Slice，或一个纯文档 / 纯测试 / 纯重构目的。不要把功能、重构、UI、测试和文档治理混成一个大 PR。

一个事件类 Slice 通常需要说明：

- 输入事件和统一事件模型。
- 是否参与 `live_events` Selection；若不参与，说明原因。
- 是否进入 pipeline / safety guard / dispatcher。
- 读写哪些 store、audit 和配置。
- UI / action / hosted-ui context 是否变化。
- 测试命令和文档更新。

### PR 粒度

- 单个 PR 默认控制在 **20 个文件以内**。
- 超过 20 个文件必须在 PR 描述中解释原因，并优先改为 Draft 或拆分。
- 文档治理 PR、测试补齐 PR、纯重构 PR 必须保持目标单一。
- 不要在功能 PR 中顺手重排大文档、重构 `panel.tsx`、清理旧插件或修改无关 host/server 文件。

### Draft PR 使用规则

以下情况默认使用 Draft PR：

- 建立新基础契约，后续 PR 会依赖它。
- 跨模块迁移或大范围文档治理。
- Reviewer 需要先确认边界和命名。
- PR 预计会拆出后续子 PR。

Draft 转 Ready 前必须具备：

- 明确的 Slice 范围。
- 测试命令和结果。
- 文档影响说明。
- 已知风险、回滚 / 降级方式。

### 多 PR 依赖链

优先采用短依赖链：

```text
foundation contract PR
  -> one vertical slice PR
  -> optional UI / docs / polish PR
```

依赖链中的每个 PR 都必须：

- 写明 base PR / merge order。
- 在当前 base 上可单独验证。
- 不依赖未说明的本地状态。
- 保持失败时可回滚，不让主线进入半功能状态。

### Reviewer Checklist

Reviewer Checklist 的唯一 Canonical Source 是 `AGENTS.md`。本文只定义协作背景和 PR 拆分原则；review 时以 `AGENTS.md`「Reviewer Checklist」为准。

## 模块 Owner 与 Review Gate

Owner 是职责角色，不绑定具体人名。一个 PR 可以由多人实现，但触碰 Protected Modules 时必须让对应 Owner 角色参与 review。

### Owner Model

| Owner 角色 | 负责范围 | 主要把关点 |
|---|---|---|
| Core Architecture Owner | `core/contracts.py`、`core/event_bus.py`、`core/module_registry.py`、跨模块契约 | 统一事件模型、模块生命周期、扩展边界、兼容性 |
| Event Layer Owner | `modules/bili_live_ingest/**`、B 站协议解析、直播间查询、事件归一化 | 真实直播稳定性、协议变更、风控降级、LiveEvent 输入质量 |
| Selection Owner | `modules/live_events/**`、窗口择优、事件竞争策略 | “猫只有一张嘴”的选择权、冷却窗口、评分权重、避免重复输出 |
| Pipeline Owner | `core/pipeline.py`、`core/safety_guard.py`、`adapters/neko_dispatcher.py`、输出边界 | 安全门、限流、dry-run、唯一出口、NEKO 输出语义 |
| Runtime Owner | `core/runtime.py`、插件 action、配置生命周期、hosted-ui context | 启停、配置持久化、权限门、运行态一致性 |
| Dashboard/UI Owner | `ui/panel.tsx`、`config_schema()` 渲染、i18n 文案、面板信息架构 | UI 外壳稳定、模块卡隔离、8 locale 同步、Hosted UI 约束 |
| Stores/Privacy Owner | `stores/viewer_store.py`、`stores/audit_store.py`、`stores/credential_store.py`、用户数据边界 | 隐私、凭据、审计、持久化、数据最小化 |
| Documentation Owner | `docs/**`、`AGENTS.md`、onboarding、模块文档、PR 规范 | Canonical Source、协作规则、文档路由、新人入口 |

### Protected Modules

Protected Modules 是需要核心维护者 review 的高风险区域。触碰这些区域时，PR 描述必须说明风险、验证方式和回滚 / 降级方式。

- Core architecture：`core/contracts.py`、`core/event_bus.py`、`core/module_registry.py`。
- Event layer：`modules/bili_live_ingest/**`、直播协议解析、LiveEvent schema 或事件归一化。
- Selection：`modules/live_events/**`、`get_score` 权重、冷却窗口、事件竞争策略。
- Pipeline / output：`core/pipeline.py`、`core/safety_guard.py`、`adapters/neko_dispatcher.py`。
- Runtime：`core/runtime.py`、插件 action、配置持久化、hosted-ui context。
- Stores / privacy：`stores/viewer_store.py`、`stores/audit_store.py`、`stores/credential_store.py`。
- Dashboard shell：`ui/panel.tsx` 的导航外壳、模块渲染器、错误边界、跨页面状态。

### Open Contribution Areas

这些区域适合开放贡献，但仍需要普通 review：

- `docs/**` 与 `docs/modules/**` 的补充和纠错。
- 非核心模块的模块文档、fixture、单测样本。
- Dashboard 小型只读展示、文案调整、8 locale 同步。
- 新模块的 `config_schema()` 声明式参数，但不能绕过 Protected Modules。
- 测试补齐和回归样本，前提是不改变核心行为。

### New Contributor Starter Areas

新开发者优先从这些任务开始：

- 模块文档补齐或 `docs/modules/<module_id>.md`。
- EventBus / live_events 的 fixture 和测试样本。
- Dashboard 只读状态展示或文案修正。
- i18n 同步。
- 小型 docs-only PR 或测试-only PR。

第一周不建议直接修改 Protected Modules。确需触碰时，先拆成 Draft PR，让对应 Owner 角色确认边界后再继续。

## 当前模块

已启用模块：

- `bili_live_ingest`：归一化直播弹幕事件、提供直播间状态查询（带反 -352 + 友好降级，见「直播间查询与 -352 风控」），并**持有真实弹幕监听器**——吞并自 `bilibili_danmaku` 的 `DanmakuListener`（同目录 `danmaku_core.py` + `livedanmaku.py`：WS 连接 + WBI 签名 + 临时 buvid3 反 -352 + zlib/brotli 解压 + 心跳 + 多服务器故障转移 + 断线重连）。`runtime.connect/disconnect_live_room` 启停监听；`stop_listening` 用 `wait_for` 给 ws close 加超时，避免关闭握手拖慢断开。**富模型 `on_event` 回调把 `LiveDanmaku` 包成 `LiveEvent` 发布到 `event_bus`**（按命令名映射 `type`），由订阅者按类型消费（轻量 `on_danmaku`→pipeline 直连已退役，防同一条弹幕双锐评）。见「直播事件中枢（EventBus）」。登录态（若有）传入 `DanmakuListener` 与 lookup（见「B站登录态」）。弹幕本身不含头像，头像由下游 `bili_identity` 按 UID 抓取。
- `bili_identity`：解析 UID、昵称、头像 URL；缺少昵称或头像时按 UID 查询 B 站基础资料，并尝试抓取头像供本次 NEKO 视觉输入使用。同时解析头像形态 META：是否默认头像（noface）、是否动图（大会员动态头像，只取代表帧）、挂件/装扮名（出框头像来源）；抓取或识别失败时安全降级（`avatar_vision_ok=False`），不阻断锐评。
- `viewer_profile`：维护观众档案和首次触发判断。
- `avatar_roast`：构造首次出场的头像 / ID / 第一句话锐评请求，并集中产出完整锐评指令（见“输出边界”的自适应焦点规则）。
- `danmaku_response`：构造同一观众后续普通弹幕的接话请求。它不做头像 / ID 首评，不写首评计数，不绕过 pipeline / safety guard / dispatcher；用于让 Independent Mode 下的持续对话不被 `roast_once_per_uid` 整体挡掉。
- `active_engagement`：构造猫猫独播安静状态下的一次主动营业请求。当前 v1 支持保守自动触发和手动触发，不接 Gift / SC / Guard；它必须继续经过 pipeline / safety guard / dispatcher，用于后续统一直播验证猫猫能否自然抛出一个观众愿意接的话题。v1 会给请求附带轻量 `topic_material`，优先复用 6 分钟内有信息量且已经成功输出或 dry-run 到 dispatcher 的近期直播间弹幕；没有合适弹幕时再回退到 B 站公开推荐素材，最后使用内置小话题。内置 fallback 话题池需要覆盖多种低压力接话形态，并避免在短时间内重复同一个话题；topic material 不能把“没人说话 / 弹幕少 / 冷场 / 突然安静 / nobody is talking / suddenly quiet”这类房间沉默描述当成主动营业素材，也不能把“求推荐 / 有什么推荐 / any recommendations”这类让观众替 NEKO 选题的内容，或“今晚做什么 / what are we doing”这类开放式选题问句当成主动营业素材；观众直接问 NEKO 的问题、“猫猫你觉得...”这类直接征询 NEKO 看法的弹幕、以及“猫猫讲讲 / 说说 / 聊聊 / 评价一下 / 锐评一下 / 帮我 / 给我 / 能不能 / 可不可以 / 要不要...”和“谢谢 / 感谢 / 辛苦了”这类中文点名请求或感谢，以及 `NEKO help me / give me / rate my / tell me / can you / could you / please / pls / thank you / thanks...` 这类英文点名请求或感谢也不再作为主动营业素材；未点名但明显是“讲讲 / 说说 / tell me / recommend me”式请求，“哈哈 / 笑死 / lol”式纯反应弹幕，以及“状态 / 下一步 / 重启 / 延迟 / 回复太长”式测试或运行反馈，也应由 `danmaku_response` 接住或进入测试复盘，不作为主动营业二次开题素材，避免已经由 `danmaku_response` 接过的话题被二次开场，并分别通过 `topic_recent_skip_reason=filtered_direct_request` / `filtered_reaction` / `filtered_runtime_feedback` 复盘；低信息量但无法归类的近期弹幕仍使用 `filtered_recent_danmaku`；`get the chat moving` / `keep the chat alive` 这类英文主持模板也必须过滤，避免和 Idle Hosting 或观众求助职责混淆；公开推荐素材还会过滤营销、广告、关注转发、抽奖、giveaway / sponsored，以及事故、死亡、灾害、争议、网暴、scandal / controversy 这类沉重或高争议标题，避免猫猫主动营业像在念推广或把直播气氛带偏；外部标题进入 `topic_material` 前会压缩到 40 字以内，避免长标题诱导长篇输出。若当前缓存的外部话题都已经用过，会清一次缓存并尝试重新拉取外部话题，再回退到内置小话题，避免长直播后半段过早耗尽公开素材。首评 `avatar_roast` 的同条弹幕不能再作为主动营业素材，避免首次出场锐评后围绕同一句话二次开题，并通过 `topic_recent_skip_reason=avatar_roast_context` 复盘；被 skipped / failed 的弹幕不能被主动营业放大，并通过 `topic_recent_skip_reason=non_output_danmaku` 复盘；若短窗口内有效 recent danmaku 全部来自同一 UID 且达到 3 条，主动营业不再拿这位观众的弹幕继续开题，而是回退到公开推荐或内置小话题，避免独播被单个观众刷屏素材带偏，并在 recent result 暴露 `topic_recent_skip_reason=single_viewer_flood` 方便复盘；若只有过期 recent danmaku 被过滤，则暴露 `topic_recent_skip_reason=stale_recent_danmaku`。prompt 会把 `topic_shape` 展开为 `shape task` 和 `example pattern`，只约束接话结构，不硬编码猫猫台词，也禁止让观众替 NEKO 决定“想听什么 / 聊什么”。recent result 会暴露 `topic_source` / `topic_shape` / `topic_title` / `topic_key` / `topic_hook` / `topic_pattern`，监控脚本还会根据最近结果输出 `latest_topic_repeat`，便于复盘主动营业为什么说这一句、想让观众怎么接、是否复用了同一个素材。
- `warmup_hosting`：构造猫猫独播刚开始、尚无近期互动时的一次开场暖场请求。它与 `idle_hosting` 分开，避免开播第一句话听起来像冷场补位；同样必须经过 pipeline / safety guard / dispatcher。
- `live_director_status`：面板状态聚合，不新增输出路径；它只解释下一次自动开口会是 `none` / `warmup_hosting` / `active_engagement` / `idle_hosting`、当前是否 eligible、以及还要等多久，方便统一直播测试时判断猫猫为什么不说话。
- `solo_test_readiness`: dashboard-only streamer readiness aggregation for solo-stream validation. It summarizes preflight, test isolation, warmup hosting, first-viewer roast, follow-up danmaku reply, active engagement, idle hosting, and pacing control; it does not add a new output path, bypass safety, or replace runtime status.
- `developer_sandbox`：提供离线 UID / URL 调试入口。
- `live_events`：直播事件中枢（P2.5）。经 `event_bus` **订阅 `"danmaku"` / `"gift"` / `"super_chat"` / `"guard"` 事件**，解包信封 `raw` 取富模型 `LiveDanmaku`，冷却期缓冲候选互动、按 `get_score()` 打分，冷却结束择优（舰长/总督/SC、礼物、粉丝牌、用户等级、长文本优先）取分最高者投 `pipeline`；空闲态首条仍即时锐评。礼物/SC/上舰当前复用既有 pipeline 产出端，专属致谢 / 朗读 prompt 留待后续 P3 handler。详见下文「直播事件中枢」。

预留模块：

- `bili_dm_ingest`：未来接入 B 站私信。
- `contribution_rank`：未来接入贡献值。
- `watch_time`：未来接入进房累计和停留时长。
- `bili_read_tools`：未来接入用户资料、投稿、收藏等读取能力。
- `bili_write_tools`：未来接入发弹幕、评论、动态、私信等写入能力。
- `automation_ops`：未来接入浏览器、键鼠和公开资料工作流。

其它核心组件（非 `InteractionModule`，但同属插件骨架）：

- `core/`：`pipeline`（统一处理链）、`safety_guard`（安全门：连接/暂停/队列/限流/急停 + `output_cooldown_remaining` 给中枢对齐窗口）、`runtime`（装配 + 配置/凭据生命周期）、`contracts`（数据契约 + `parse_room_id`）、`permission_gate`、`module_registry`（模块注册 + 故障隔离 + `enable/disable` 触发 `on_enable/on_disable` 生命周期钩子，单点失败标 degraded + audit）、`event_bus`（**直播事件中枢**：`LiveEvent` 按 `type` 的真订阅分发，每订阅者隔离 + 归属 + audit，见「直播事件中枢（EventBus）」）、`instructions`（直播/调试语境提示词）。
- `adapters/`：`neko_dispatcher`（**唯一 NEKO 输出边界** + 头像压缩 + dry_run 短路）、`bili_auth_service`（扫码登录，移植自旧插件）。
- `stores/`：`viewer_store`（**唯一档案写**，本机 JSON `viewer_profiles.json`、目录可配置、加锁防丢更新）、`audit_store`（**唯一审计**）、`avatar_cache`、`credential_store`（Fernet 加密登录凭据）。

## Pipeline

固定数据流：

```text
ViewerEvent
  -> safety_guard.before_event()
  -> bili_identity.resolve()
  -> viewer_profile.upsert() / 沙盒临时 profile
  -> viewer_gate.check_once_per_uid()
  -> avatar_roast.build_request()          first appearance
     or danmaku_response.build_request()   repeat live danmaku
  -> safety_guard.before_output()
  -> neko_dispatcher.push_roast()
  -> audit_store.record()
```

沙盒事件 `source == "developer_sandbox"` 时：

- 使用临时 `ViewerProfile`，不写 `viewer_store`。
- 不受 `roast_once_per_uid` 限制。
- 成功、跳过、失败都应回显到沙盒最近记录。
- 沙盒最近记录只保存轻量摘要，不保存完整 request、大 prompt、头像 bytes 或 base64。

## 开发者模式总控

`developer_tools_enabled` 是开发者模式的唯一总控，不再拆出独立的“聊天开发者工具”或“沙盒调试”开关。维护 UI 或 action 时不要新增第二个调试开关。

开启开发者模式时：

- Hosted UI 的 UID 查询、模拟弹幕、内置案例按钮可用。
- 动态聊天工具 entry 可用，猫猫可以在普通聊天中调用 UID 查询和沙盒锐评工具。
- runtime 会在直播语境之后叠加 `NEKO_ROAST_DEVELOPER_INSTRUCTIONS`。
- 只有用户从面板手动从关闭切到开启时，才通过 `respond` 播报一次进入开发者模式；插件启动、配置重载、重复保存不自动播报。

关闭开发者模式时：

- Hosted UI 的 UID 查询、模拟弹幕、内置案例按钮必须禁用。
- 后端 `submit_viewer_event`、`lookup_only`、动态聊天工具 entry 也必须拒绝执行，不能只依赖前端禁用。
- runtime 只发送 `NEKO_ROAST_DEVELOPER_RESTORE_INSTRUCTIONS` 退出调试态，仍保留直播锐评语境。
- 不清空 `recent_sandbox_results`，不清空头像 preview cache，不影响观众档案或直播总结。
- “清空沙盒记录”仍可用，因为它是清理动作，不触发查询、pipeline 或 NEKO 输出。

实现入口：

- `__init__.py` 负责注册 / 启停动态聊天工具 entry，并在 UI `update_config` 中判断是否需要播报。
- `core/runtime.py` 负责 `sync_developer_mode()`、调试语境注入 / 恢复、后端沙盒权限检查。
- `adapters/neko_dispatcher.py` 是唯一可以发送调试语境和调试播报的 NEKO 输出边界。
- `ui/panel.tsx` 只负责显示总控开关和禁用按钮；业务权限必须以后端检查为准。

测试要求：

- 覆盖启动时“直播语境 -> 调试语境”的顺序，且启动不发 `respond` 播报。
- 覆盖面板手动开启时播报一次，关闭时只恢复调试语境。
- 覆盖开发者模式关闭时后端沙盒入口不会进入 pipeline，也不会 push 给 NEKO。
- 覆盖 8 个 locale 都有 `panel.fields.developerMode` 和 `panel.dev.developerModeDisabled`，且不再使用旧 `chatDevTools` / `sandboxDebug` 文案。

## 安全测试态（dry-run）

`dry_run`（`RoastConfig` 字段，可经 `update_config` 动作切换）是接真实直播间前的安全开关，**默认开启**。开启时整条 pipeline 照常跑——安全门、`bili_identity` 身份解析、头像抓取、`avatar_roast` 锐评 prompt 构造都会执行——但 `neko_dispatcher.push_roast()` 在真正 `push_message` 之前短路，返回 `dry_run(target=..., image_part_bytes=..., text_len=...)` 摘要，**绝不投递给猫猫**。用途：灌真实弹幕样本、验证整条链路是否产出正确的锐评请求，而不让猫猫在直播间开口。只有主播确认进入正式输出窗口时才把 `dry_run` 置回 `false`。`build_request()` 把 `ctx.config.dry_run` 写进 `InteractionRequest.dry_run`，dispatcher 据此判断。

## 配置持久化与写竞争

`runtime.update_config` 的契约：**内存即时生效、持久化尽力而为**。

host 的 `update_own_config`（把配置写回 `plugin.toml`）在「只重后端不重前端」等场景会被前端的并发配置访问卡满写竞争，偶发（实测下甚至稳定）挂满，触发 host 的 10s entry 超时把整个 action 杀成 500。早期实现先 `await` 持久化再 apply 内存，被这一杀连内存兜底都来不及跑——表现为 `update_config` / `connect_live_room`（其 `set_live_room` 走 `update_config` 持久化 `live_room_id`）/ 开发者模式切换全部点不动。

现在反过来：

1. **先内存生效**：`_activate_config(RoastConfig.from_mapping(...))` 一步把新配置装进 `self.config`（gate / safety_guard 共享同一对象，即时权威）；若改了 `developer_tools_enabled` 顺带 `sync_developer_mode`。
2. **再带预算尽力持久化**：`_persist_config_best_effort` 用 `asyncio.wait_for(self._persist_config_update(clean), timeout=_CONFIG_PERSIST_BUDGET_SECONDS)`（默认 4.0s，远低于 host 的 10s entry 限），超时记 `config_persist_timeout`、失败记 `config_persist_failed`，**都不回滚已生效的内存配置、不阻塞**。
3. **串行化**：`asyncio.Lock`（`_get_config_lock`，懒初始化）避免插件自身并发 `update_config` 内存 apply 互踩 / 叠加持久化。

效果：host 持久化即便卡死，action 也在 ≤4s 内成功返回、runtime 行为已按新配置生效。代价：写竞争时那一次改动**不落盘**（stop/start 后还原成 `plugin.toml` 的值），且每次 `update_config` 等满 4s 预算（无竞争时秒过）。

> 边界：这是**插件侧免疫**；host/core 修复 `Fix plugin host config and data root handling (#1884)` / `08b317f6` 已进入当前 `Roast` 分支，但插件仍保留这层预算兜底，避免未来 host 持久化异常拖垮直播 action。`connect/disconnect_live_room` 另对 `live_enabled` 做内存直设，不依赖持久化即时性。测试：契约 `test_update_config_does_not_block_on_hanging_persistence`、`test_connect_does_not_block_on_hanging_config_persistence`（注入卡死的 `update_own_config`，断言 action 不阻塞、内存生效、记 `config_persist_timeout`）。

## 房号输入（数字 / 链接）

房号入口统一过 `contracts.parse_room_id(value) -> int`：接受 int、纯数字串、含 `live.bilibili.com/<id>` 的链接（含 `/h5/`、`/blanc/`、query），解析不出返回 0。让用户直接粘直播间链接，不必手动找房号。

落点（每个 room_id 入口都经它，保证落盘永远是 int）：
- `RoastConfig.from_mapping` 的 `live_room_id`（配置加载，容错）。
- `runtime.update_config`：持久化前把 `clean["live_room_id"]` 归一成 int（saveConfig 路径）。
- `runtime.connect_live_room` / `set_live_room` / `lookup_live_room`：各自 `room_id` 参数（action 路径）。

UI 侧：3 个 room action 的 `room_id` input_schema 收 `string`、handler 传原始值（runtime 解析）；面板 `saveConfig`/`connectRoom`/`lookupLiveRoom` 送**原始串**（不再 `Number()` 截断，否则链接在前端就成 0）；占位符 `panel.placeholders.roomId` 已 8 locale 同步为「房号或链接」。测试：`test_parse_room_id_accepts_number_and_url` / `test_update_config_parses_room_url` / `test_set_live_room_accepts_bilibili_url`。

## 直播间查询与 -352 风控

「查询直播间」和「弹幕监听」走**两条不同网络路径**，反爬健壮性不同：

- **弹幕 WS 路径**（`connect_live_room` → `danmaku_core.DanmakuListener`）：有临时 buvid3 + WBI 签名 + 浏览器 headers + 多服务器故障转移，扛得住 B站 `-352` 反爬风控，匿名只读也能连。
- **查询 HTTP 路径**（`lookup_live_room` → `bili_live_ingest._lookup_room_status_sync`，urllib + `to_thread`）：A1 已补临时 buvid3 cookie + 浏览器 headers（`getInfoByRoom` **不需** WBI 签名——WS 的 `_get_real_room_id` 调它也没签）。但匿名 buvid3 在 IP 被重度风控时仍可能 `code=-352`，彻底消除需登录态。

**已落地处理（友好降级，非根治）**：
- `BiliLiveIngestModule._friendly_lookup_message(code, raw)` 把失败码翻成人话：`-352` → 「B站风控校验失败（-352）：匿名查询被反爬拦截，可稍后重试/换网络/登录后再查；直播间监听（弹幕）通常仍可用」；房间不存在（`code in {1, 19002000}` / 含「不存在」「未找到」）→ 「请确认房间号」；其它非零码 → 带 `code` + 原始 message（不再裸码）。
- 面板查询失败 `Alert` 显示该 message（`panel.tsx`：`liveRoomResult.message || t("panel.room.lookupFailed")`），不再死写「请检查房间号」，避免把风控误导成房号错误。

**已落地（A1，反 -352，2026-06-17）**：`_lookup_room_status_sync` 重构为
1. **临时 buvid3**：`_fetch_buvid3_sync` 访问 B站首页从 Set-Cookie 抽 buvid3（`_parse_buvid3_from_cookies`），`_get_buvid3(force=)` 带 6h TTL 缓存；
2. **浏览器 headers**：`_BROWSER_HEADERS`（UA/Accept/Accept-Language/Origin）+ 每房 Referer + `Cookie: buvid3=...`；
3. **撞 -352 重试一次**：`_do_room_lookup` 返回 `(status, code)`；`code==-352` 时刷新 buvid3 再试一次（只一次，别硬刷加重风控）；
4. **成功短期缓存**：`_room_status_cache` 按 room_id 缓存 60s，避免重复请求。

**关键认知**：**查询失败 ≠ 监听失败**。lookup 撞 -352 时，弹幕 WS 监听往往仍可正常连（它有反 -352）。面板「查询直播间」失败时，可直接「开始监听」。

**彻底消除方向**：A1 只降低 -352 频率（匿名 buvid3 + 退避缓存），**重度风控 IP 仍可能撞墙**；彻底稳定需**登录态**（P5，复用 `bili_auth_service.py` 拿 SESSDATA/buvid3）。2026-06-17 真机：本机连日测试已重度风控，buvid3 确认能抓到（len=46）但 `getInfoByRoom` 4 房间仍一致 -352 —— 匿名不足，坐实需 P5。

测试：`test_friendly_lookup_message_translates_risk_control_and_codes`（码→人话映射）、`test_parse_buvid3_from_cookies`（buvid3 抽取）、`test_lookup_retries_once_on_352_with_fresh_buvid3`（-352 刷新 buvid3 重试）、`test_lookup_caches_successful_result`（成功缓存）。

## B站登录态（P5）

**功能目的**：用 B站 扫码登录的凭据绕过 -352 风控——匿名 buvid3 不足以过 `getInfoByRoom` 与 `get_user_info`（重度风控 IP 会一致 -352，见「直播间查询与 -352 风控」），登录态可靠根治。**核心收益**：登录后头像抓取不再被 -352 吞（招牌锐评恢复完整头像），查询与弹幕连接也更稳。

**不做什么**：不做服务端 token 吊销（注销 = 本地删凭据）；v0.1 不用写能力（发弹幕/私信留待后续）。

**安全模型**：凭据（SESSDATA/bili_jct/DedeUserID/buvid3）经 **Fernet 对称加密**落盘到 per-plugin data 目录（`plugin.data_path()`），密钥 `bili_credential.key` + 密文 `bili_credential.enc` 分别 `chmod 600`（非 Windows）。**凭据绝不写 audit / log / config / UI**——只回显 uid / 用户名 / 是否登录。可**本地注销**（删 key+enc）。

**责任模块 / 入口数据流**：
- `stores/credential_store.py` `CredentialStore`：加密 `save`/`load`/`delete` + `build_credential()`（构 `bilibili_api.Credential`，走 `to_thread` 不阻塞）。
- `adapters/bili_auth_service.py` `BiliAuthService`（移植自旧插件 `bilibili_danmaku`）：编排 `bilibili_api.login_v2` 扫码状态机，凭据存取由注入的 store 三回调负责。
- `core/runtime.py`：持 `credential_store` + `bili_auth` + 缓存 `bili_credential`（`start()` 时 `reload_credential()` 载入已存凭据）；方法 `bili_login`/`bili_login_check`/`bili_login_status`/`bili_logout`。
- **凭据接入三处**（`bili_credential` 为 None=未登录时**行为与匿名完全一致**，零回归）：`bili_identity._fetch_profile_by_uid` 的 `get_user_info(credential=)`、`bili_live_ingest` 的 `DanmakuListener(credential=)`、lookup 的 `_credential_cookie()`（登录时带完整 cookie 过 -352）。

**UI / action**：面板「直播间配置」页顶部「B站登录」卡（扫码图 + 检查登录 + 退出登录 + 登录状态，挂载时拉一次状态）；4 个 `@ui.action`（group `auth`）：`bili_login` / `bili_login_check` / `bili_login_status` / `bili_logout`。

**经过 safety_guard 吗 / 失败降级**：登录流程**不经 pipeline**（账号管理、不产出锐评）。凭据缺失 / 失效 / `bilibili_api` 或 `cryptography` 不可用 → 安全降级为匿名（行为同未登录）；保存失败 → 报错不静默。

**读写了哪些用户数据**：只读写本机加密凭据文件；**不进** viewer_store / audit（明文）。

**测试**：`tests/test_credential_store.py`（加解密往返 / 落盘为密文 / 删除）；契约 `test_bili_login_status_without_credential_is_logged_out`、`test_bili_logout_removes_local_credential`、`test_credential_cookie_built_from_credential`。

**真机验证（2026-06-17，用户扫码本人账号 uid 1408555810）**：同房 81004 — 登录前匿名 lookup 撞 `-352`；扫码登录后同 lookup `ok:true`，`-352` 彻底消失。头像抓取恢复（`submit_viewer_event{lookup_only}` → `fetched:true / has_avatar:true`）。持久化端到端：登录后 `bili_login_status` 读回 `logged_in:true`（load→解密→build_credential 回环，证明 `.enc` 落盘可解密）。

**已知限制**：① 依赖 `bilibili_api` + `cryptography`（NEKO 内置）；② 本地注销不吊销服务端 token；③ 凭据过期需重新登录（`bili_login_status` 会报失效）；④ 登录卡 UI 肉眼验为非阻塞收尾项。

## 限流（rate_limit_seconds）

`safety_guard.before_output()` 按 `rate_limit_seconds` 控制**最小锐评间隔**：直播态下两次锐评投递之间至少隔这么多秒，期间到达的事件返回 `skipped`（reason `rate limited`），不投给猫猫——避免爆量房间猫猫连珠炮。开发者沙盒事件（`source == "developer_sandbox"`）不受限流，保证即时调试反馈。`rate_limit_seconds = 0` 关闭限流。`safety_guard.resume()` 会重置间隔计时。

> 更新（P2.5，已接入）：值优选由 `live_events` 中枢接管。冷却期内不再 skip 掉所有人，而是缓冲候选互动、按 `get_score` 择优，冷却结束投分最高者；空闲态首条仍即时锐评不缓冲。`rate_limit_seconds` 现在既是 `before_output` 的硬限流闸门，也是中枢的开窗时长，二者对齐——中枢 flush 出来的胜者不会反被 `before_output` 判限流。当前参与同窗竞争的类型为 `DANMU_MSG` / `SEND_GIFT` / `SUPER_CHAT_MESSAGE` / `GUARD_BUY`。详见「直播事件中枢」。

## 直播活跃度（activity_level）

`activity_level` 是主播侧的三档节奏控制，不暴露复杂阈值参数：

- `quiet`：更耐心，较晚进入 `idle`，Idle Hosting 间隔更长。
- `standard`：默认节奏，保留当前低弹幕独播基线。
- `active`：更积极，较早进入 `idle`，Idle Hosting 间隔更短。

当前实现同时影响四类决策：`live_state_summary()` 的 `quiet` / `idle` 阈值、`idle_hosting_status()` 的最小陪播间隔、独播首评节流窗口，以及 Idle Hosting prompt 的主持姿态。`quiet` 更偏轻观察、少直接提问；`active` 允许一个具体、低压力的小问题；`standard` 保持中间策略。当前 `standard` 会在约 60 秒无观众活动后进入 `quiet`，约 120 秒后进入 `idle`；自动 Active Engagement 在普通弹幕输出后等待约 75 秒，标准档自身最小间隔约 120 秒，保证不抢刚发生的接话，同时仍能在下一次真实测试中更容易观察到主动营业；`active` 档自身最小间隔约 90 秒，`quiet` 档约 300 秒。独播首评节流窗口为 `quiet=75s`、`standard=45s`、`active=30s`：安静档更少连续头像 / ID 出场锐评，活跃档更快放开新观众出场。`live_state_summary()` 会把观众活动与 NEKO 自己的输出分开统计：`last_viewer_activity_age_sec` 决定 `engaged` / `quiet` / `idle`，`last_output_age_sec` 只用于解释最近是否说过话，避免猫猫自己的主动营业永久阻止冷场陪播。面板会展示最近观众活动间隔、最近输出间隔、多久算安静、多久算冷场，便于主播理解为什么猫猫现在说或不说。

面板的控制台、冷场陪播卡和主动营业卡必须保留这两个节奏字段；主动营业卡还要拆分展示 `minimum_interval_remaining`、`recent_danmaku_cooldown_remaining` 与 `idle_hosting_wait_remaining`，避免只看到一个合并 cooldown 时无法判断到底是自身最小间隔、刚接过弹幕导致等待，还是已经接近冷场窗口而主动让位给 Idle Hosting。当前决策卡也会展示最近主动营业的 `topic_source` / `topic_shape` / `topic_title` / `topic_key` / `topic_hook` / `topic_pattern`，用于下一次直播复盘话题是否足够具体、是否吸引观众接话。

当 `solo_stream` 的观众沉默时间已经接近 `idle_threshold_seconds` 时，自动 Active Engagement 必须让位给 Idle Hosting；当前让位窗口是进入 `idle` 前 15 秒。这样下一次真实无弹幕窗口不会被主动营业刚好抢掉，直播复盘也能明确区分“主动话题不足”和“冷场陪播没有触发”。

Idle Hosting 不是简单定时器输出。每次 `idle_hosting` 事件会附带一个轻量 `host_beat`，在软观察、小二选一、轻吐槽、小状态等低压力主持节拍之间轮换；prompt 只能把它当方向，最终仍必须生成一句自然的 NEKO 直播补位。`host_beat_key` / `host_beat_shape` / `host_beat_title` 会进入 recent result、recent interaction context、面板当前决策卡与 `monitor_live.ps1` 输出，方便下一次冷场补位避免复用同一个开场、包袱形状或主持节拍。

直播测试时，`monitor_live.ps1` 可以通过 `-BackendLogPath <path>` 读取后端日志尾部并输出 `log_watchdog` / `log_contamination` / `log_reply_len` / `log_reply_length_status` / `log_generic_host_prompt`；真实输出测试可加 `-ExpectRealOutput` 聚合 `alerts`。这些字段只用于验收复盘：`log_watchdog` 帮助发现 playback gate watchdog 或缺失 `voice_play_end` 造成的卡顿，`log_contamination` 帮助识别 Warthunder 等非 NEKO Live 主动输出污染，`log_reply_length_status` / `recent_long_reply_count` 帮助标记最新或最近窗口内的回复长度异常，`recent_generic_host_prompt_count` / `log_generic_host_prompt` / `generic_host_prompt` 帮助抓出“大家快来互动 / 发弹幕 / get the chat moving”这类模板式营业话术；其中 `log_generic_host_prompt` 只检查后端日志里的 `send_lanlan_response text=...` 回复文本，不扫描 prompt 指令，避免把“禁止说某句”的提示词误报成猫猫已说出口。`avatar_roast_share` / `avatar_roast_bias` 帮助识别普通弹幕路线是否仍被首次出场锐评吞掉，`entrance_pacing_window` 帮助确认当前活跃度下连续首评会被压多久，`latest_topic_recent_skip_reason` 和 `recent_topic_skip_*` 帮助复盘主动营业素材为什么被过滤，例如单 UID 刷屏、旧弹幕、首评上下文、未输出弹幕，或近期弹幕本身不适合主动营业；点名/未点名请求、纯反应和运行反馈类素材过滤会额外在 `alerts` 中提示 `topic_filter_direct_request` / `topic_filter_reaction` / `topic_filter_runtime_feedback`，方便直播现场快速判断主动营业为什么没有拿近期弹幕开题；`alerts` 还帮助现场优先发现 dry_run、断连、stale、失败/跳过、延迟、watchdog、串台、长回复、模板式营业、测试隔离不干净、最近失败、头像锐评偏航、冷场陪播缺席、主动营业缺席和 `backend_log_missing`；它们不参与业务路由、节奏判断或输出决策。`backend_log_missing` 只表示监控没有读到后端日志，不能据此判断 playback / TTS / 长回复风险已经消失。

Dispatcher 会在真实输出请求 metadata 与 `dry_run(...)` 摘要中标记 `live_reply_contract=short_tts_line`、`max_reply_chars=40` 和 `response_module_hint=...`。这不是绕过核心生成逻辑的硬截断，而是直播插件向核心输出链路和现场调试面暴露的短回复意图；若后端实际 `send_lanlan_response` 仍变长，以 `monitor_live.ps1` 的长回复告警为准继续收敛。

## 富模型弹幕解析（`livedanmaku.LiveDanmaku.from_danmaku`）

`livedanmaku.py` 的 `LiveDanmaku` 是吞并自 `bilibili_danmaku` 的富模型（覆盖 30+ 字段，含 `get_score()` 打分），是后续 P2.5「事件中枢 / 事件族」的前置。`danmaku_core._dispatch_message` 在收到 `DANMU_MSG` 时除了发轻量 `on_danmaku`，还会用 `from_danmaku(data)` 产出 `LiveDanmaku` 并发 `on_event("DANMU_MSG", ld)`。

**已修 bug（2026-06-16）**：`from_danmaku` 误把 B 站 `DANMU_MSG.info[7]`（大航海等级，**普通 int**：0 无 / 1 总督 / 2 提督 / 3 舰长）当作可下标列表（`info[7][3]`、`info[7][1]`、`info[7][2]`），任意一条正常弹幕都会在 `len(info[7])` 抛 `TypeError: object of type 'int' has no len()`，被 `_dispatch_message` 的 `except Exception: pass` 吞掉——表现为 `on_event("DANMU_MSG")` 永不触发，富模型计数恒为 0（冒烟时发现）。同时 `admin` 只判了外层 `len(info) > 2`、未判内层长度，短 `info[2]` 会 `IndexError`。

**正确字段映射**（`info` 真实结构，仅列本类用到的）：

- `info[1]` 弹幕文本。
- `info[2]` 用户数组 `[uid, uname, is_admin, is_vip, is_svip, ...]` → `admin`/`vip`/`svip` 从这里取（带内层长度守卫）。弹幕 payload **不含头像 URL**，`face_url` 置空，头像由下游 `bili_identity` 按 UID 抓取。
- `info[3]` 粉丝牌数组（可为空）→ `medal` / `fans_medal_*`，解析失败安全降级为 `None`。
- `info[4]` 用户等级数组 `[user_level, ...]`。
- `info[7]` 大航海等级（**int**）→ `guard_level`，直接取 int；偶有实现返回列表时取 `[0]` 兜底。

所有下标都加了内层长度 / 类型守卫，任意稀疏 / 异常 payload 都不再抛异常，最坏情况退化为空字段而非整条丢弃。测试见 `tests/test_livedanmaku.py`（9 个用例：完整 payload、guard 各等级、短用户数组、缺 `info[7]`、vip/svip、face_url 置空、`from_raw_json` 路由、空 info、打分反映 guard/admin）。

> 已知限制：`medal_info` 的下标映射沿用旧实现（`[level, name, color, up_name, ?, anchor_roomid]`），与部分真实 payload 的牌子字段顺序未必完全一致，但已被 try/except 守住不会崩；精确化留待事件族统一梳理。
>

## 直播事件中枢（live_events / 窗口择优）

P2.5：把已落地但无人消费的富模型 `LiveDanmaku` 接上 pipeline，并用 `get_score()` 在一批直播互动里挑最值得响应的那个。这是「事件中枢/事件族」地基的第一步。

**功能目的**：爆量房间里不再「冷却后谁先冒泡锐评谁」（可能是个发"8888"的路人），而是冷却期缓冲候选、按价值择优（舰长/总督/SC、礼物、粉丝牌、用户等级、长文本优先）。顺带：每个冷却窗口只有 1 条进 pipeline，缓解 `queue_limit` 溢出。


**责任模块**：`modules/live_events/__init__.py`（`LiveEventsModule`）。

**入口与数据流**：
```text
danmaku_core._dispatch_message(DANMU_MSG)
  -> _emit("on_event", "DANMU_MSG", LiveDanmaku)
  -> bili_live_ingest._on_live_event(cmd, ld)         # 注册为 on_event 回调，同步非阻塞
  -> live_events.submit(ld)
       ├─ 空闲态（冷却已过且无开窗）：即时 _roast(ld)             # 保留「首评观众即开口」DoD
       └─ 冷却期：缓冲并保留 get_score 最高者，开一个对齐冷却的窗口
            -> _flush_after(remaining): 到点取分最高者 _roast(best)
  -> ctx.handle_live_payload(payload)  -> normalize -> pipeline.handle_event
```
`submit()` 同步、非阻塞（只缓冲 / 打分），真正的 pipeline 在中枢 spawn 的后台 task 里跑，不拖慢弹幕接收循环。

**节奏选择（已拍板）**：「首评即时 + 冷却期择优」。空闲态第一条弹幕立即锐评（不缓冲，保住已真机验证的 DoD），只有在 `rate_limit_seconds` 冷却期内才缓冲择优。

**与 safety_guard 协同**：`rate_limit_seconds` 现在一物两用——既是 `safety_guard.before_output` 的硬限流闸门，也是中枢的开窗时长。中枢通过新增的只读助手 `safety_guard.output_cooldown_remaining()` 把窗口对齐到冷却结束，因此 flush 出来的胜者到达 `before_output` 时冷却已过、不会被判「rate limited」。中枢另持有一个**本地** `_last_dispatch_at` 同步时间戳：投递后紧接着到的事件按本地冷却挡回缓冲分支，避免在 `before_output` 写入 `_last_output_at` 之前并发触发第二次即时锐评（防双锐评）。`rate_limit_seconds = 0` 时两段冷却都为 0，退化为每条即时（与限流关闭语义一致）。

**经过 safety_guard 吗 / 失败如何降级**：中枢只站在 pipeline **前面**做「选谁」，胜者照走完整 pipeline——`before_event`（连接/暂停/队列）、`before_output`（限流）、安全门必经，四条不变量（唯一出口 / 唯一档案写 / 唯一审计 / 安全门）原样保持。`get_score()` 抛错 → 该候选记 0 分（`_safe_score`）；窗口 flush 抛错 → 记 `live_event_flush_failed` 并复位窗口；`handle_live_payload` 抛错 → 记 `live_event_roast_failed`，不影响后续窗口。断开直播间时 `runtime.disconnect_live_room` 调 `live_events.reset()` 取消待触发窗口，避免迟到的择优在断开后误投（即便误投，pipeline 也会因 `live_enabled=False` 被 `permission_gate` 拦下）。

**触碰的契约 / store / UI / action**：胜者仍复用 `bili_live_ingest.normalize` 既有 pipeline 输入形状，不新增专属 Gift / SC / Guard handler，不直接写 store、不直接 `push_message`。`ViewerEvent.to_dict()` 会公开轻量 `event_type`（来自 raw payload）供 dashboard / monitor 标记 `gift_signal`、`super_chat_signal`、`danmaku_signal`，但不暴露完整 raw payload。新增 audit op：`live_event_selected`（含 `candidates` 候选数、`score`、`guard_level`、`event_type`、`selected` 脱敏摘要、`dropped_candidates` 脱敏摘要 + `skip_reason`）、`live_event_flush_failed`、`live_event_roast_failed`。无新增 UI action / context（`live_events` 出现在 `dashboard_state.modules` 快照里，`status()` 暴露 `buffered` / `window_open`）。

**读写了哪些用户数据**：中枢本身不落任何用户数据——只在内存里短暂持有「当前分最高的一条候选」，投递后即清。头像不经中枢（弹幕不含头像，由下游 `bili_identity` 按 UID 抓）。档案 / 审计 / 总结的写入仍由既有边界负责。

**测试命令与主要场景**：`plugin/plugins/neko_roast/tests/test_live_events.py`（8 用例：空闲态首条即时；冷却期开窗按 `get_score` 择优、整窗只投 1 条；高价值礼物可胜过普通弹幕；EventBus `"gift"` 接线进入中枢；本地冷却挡第二条防并发双锐评；空 uid / 空文本丢弃；`reset` 取消开窗；`safety_guard.output_cooldown_remaining` 时序）。契约测试 `test_live_listener_routes_rich_event_through_hub_to_pipeline` 锁住「富模型 `on_event` → 中枢 → pipeline」打通。

**已知限制**：① 礼物 / SC / 上舰当前只复用既有 pipeline 输出语境，还不是专属「致谢 / 朗读 / 欢迎」handler。② 「首评即时」下，空闲态第一条互动即使紧随其后到了更高价值的观众也不会被改选——这是用「临场感」换来的，已拍板取舍。③ 窗口择优依赖 `get_score()` 的打分权重（见 `livedanmaku.get_score`），权重调整会改变择优结果。

## 直播事件中枢（EventBus）与新增事件 handler

> **这是「把插件分发给其他开发者、各写各事件 handler」的核心契约。** P2.5 完整版地基：接入与处理彻底解耦。


**不做什么**：EventBus 不决定「选谁」（那是 `live_events` 窗口择优的事）、不拼 prompt、不投递 NEKO（仍走四条不变量）；无订阅者的类型在总线上流动但静默丢弃。

**责任模块**：`core/event_bus.py`（`EventBus`）、`core/contracts.py`（`LiveEvent` 信封）。

**LiveEvent 统一信封**（`contracts.LiveEvent`）：`type`（路由键）/ `uid` / `payload`（类型专属轻量 dict）/ `source` / `ts` / `schema_version` / `raw`（原始富模型，需完整字段的 handler 走它）。各类型的精确 `payload` schema 随对应 handler 落地敲定（见 roadmap §7-2）。

**入口与数据流**：
```text
danmaku_core on_event(cmd, 富模型)
  → bili_live_ingest._on_live_event：_to_live_event(cmd, 富模型) → LiveEvent（raw=富模型）
  → ctx.event_bus.publish(type, live_event)
      # 命令名→type：DANMU_MSG→danmaku / SEND_GIFT→gift / SUPER_CHAT_MESSAGE→super_chat
      #             / GUARD_BUY→guard / INTERACT_WORD→entry / 其余→cmd 小写
  → EventBus 逐订阅者隔离派发
       live_events 订阅 "danmaku" / "gift" / "super_chat" / "guard"：
           _on_bus_event 解包 raw → 既有 submit() 窗口择优 → pipeline
       （其它类型：无订阅者 → 静默丢弃，待后续 P3 handler 订阅）
```

**三条保证**（LIVE 可靠性第一）：① **隔离**——一个 handler 抛错（含其 async 任务）只记 audit，不波及其余订阅者 / 发布方；② **归属**——每个订阅带 `owner`（模块 id），失败记 `event_handler_failed`（带 owner + event_type）；③ **静默丢弃**——发布到无订阅者的类型 = no-op（任意模块子集都能安全运行）。handler 可同步可异步（返回协程则调度为隔离后台 task，其异常同样进 audit）。

**经过 safety_guard 吗 / 失败降级**：EventBus 本身不经 pipeline（只路由）；订阅者把胜者交给 `pipeline` 才走安全门、四条不变量。handler 抛错被隔离（见上）。

**读写了哪些用户数据**：EventBus 不落任何用户数据，只在内存里同步派发引用。

**如何新增一个事件 handler 模块（给第三方开发者）**：
1. 在 `modules/<your_id>/__init__.py` 写一个 `BaseModule` 子类，声明 `id` / `title` / `domain`（如 `"interaction"`）。
2. 在 `setup(ctx)` 里订阅：`self._unsub = ctx.event_bus.subscribe("gift", self._on_gift, owner=self.id)`；`teardown` 里 `self._unsub()`。
3. handler `_on_gift(event: LiveEvent)`：从 `event.payload` / `event.raw` 取字段，**绝不**自己 `push_message`——把数据整理成 payload 交给 `ctx.handle_live_payload(...)`（或未来事件族 pipeline），让它走 `safety_guard → 产出 → neko_dispatcher` 四条不变量。
4. 功能参数用 `config_schema()` 声明（面板自动渲染功能卡，见「UI 约定」/ ui-architecture §3）。
5. 新增 UI 文案同步 8 个 locale；补单测（订阅 / 隔离 / 产出）。
6. 在 `runtime` 注册你的模块（`registry.register`）。**`live_events`（订 `danmaku` / `gift` / `super_chat` / `guard` 做窗口择优）是可照抄的参考订阅者。**

**测试**：`tests/test_event_bus.py`（路由 / 静默丢弃 / 同步与 async handler 失败隔离 + 归属 audit / unsubscribe / 信封 `to_dict`）；契约 `test_live_events_subscribes_to_bus_and_unknown_type_is_silently_dropped`、`test_live_listener_routes_rich_event_through_hub_to_pipeline`（端到端经 bus）。


## 锐评生成：自适应焦点与头像 META

让锐评显得"有脑子"而非机械夸赞的核心：会取舍焦点、能用上头像形态、看不到就不编。

### 头像形态 META（`bili_identity`）

`bili_identity.resolve()` 除 UID / 昵称 / 头像 URL / 头像 bytes 外，还解析三个头像形态字段写入 `ViewerIdentity`：

- `is_default_avatar`：头像 URL 含 `noface` → B站默认头像，无可锐评画面。
- `is_animated_avatar`：用 PIL 判 `is_animated`（大会员动态头像，只取代表帧）；解码失败按静态处理。
- `pendant`：从 `get_user_info()` 的 `pendant.name` 取头像挂件 / 装扮名（出框头像、特典装扮的来源），无则空串。

只读属性 `avatar_vision_ok = bool(avatar_bytes)`：是否拿到可喂给视觉模型的头像帧。抓取 / 识别失败时为 False，pipeline 不中断，锐评降级为只评名字 / META。这些 META 也出现在沙盒 `lookup` 返回（`to_public_dict()`），但不返回头像 bytes / base64。

### 自适应焦点规则（`avatar_roast`）

`avatar_roast.build_request()` 集中产出完整的 `InteractionRequest.prompt_text`（见 `_build_prompt()`），结构为「事实行 + 要求行」：

- 事实行：昵称 / UID、弹幕（若有）、头像情况（由 `_avatar_guidance()` 给出）、挂件名（若有）。
- 要求行编码以下规则：
  1. **自适应焦点**：昵称和头像哪个更有梗就主打哪个；两个都有料就抓它们之间的反差 / 呼应；都平淡就拿弹幕、进场时机或当前直播节奏发挥，不硬尬夸。
  2. **具体优先**：抓一个具体细节切入并给个有依据的小判断，不泛泛夸、不逐字复述字段。
  3. **头像规则**（`_avatar_guidance` 按 META 给出三种）：看不到（`avatar_vision_ok=False`）→ 绝不脑补画面，只能就"没换 / 会动 / 带挂件"或昵称发挥；默认头像 → 从"懒得换头像"或昵称切入；能看到 → 可锐评具体内容，但只评真看到的。
  4. **防复述**：别和最近几条锐评用同样的开头和句式。
  5. **简洁 + 节奏**：一句话、有包袱、适合 TTS；强度由 `roast_strength`（gentle/normal/sharp）决定；独播（`solo_stream`）提示更主动撑场，同播（`co_stream`）低打断。
  6. 只输出锐评本身，不解释、不复述规则。

`build_request()` 只构造请求、不触发 NEKO；强度取 `ctx.config.roast_strength`。`avatar_roast` 会显式设置 `allow_avatar_image=True`，因此 `dispatcher.push_roast()` 才会按 `avatar_vision_ok` / 压缩结果附加头像 image part（详见「输出边界」「Message Plane 预算」）。

> 已知限制：自适应焦点由 LLM 依据 prompt 判断，非确定性；`pendant` 依赖 `bilibili_api` 返回 `pendant` 字段，缺失则无该 META；`co_stream_output_policy` / `solo_output_policy` 目前仅作语义占位，投递节奏的差异化尚未接入（当前只用 `live_mode` 给 prompt 节奏提示）。

## 输出边界

任何需要让猫猫回应的功能都必须通过 `NekoDispatcher`。不要在模块里直接调用 `plugin.push_message()`。

插件启动和配置变化时会通过 `NekoDispatcher.push_context_instructions()` 注入一段 `ai_behavior="read"` 的轻量上下文，告诉猫猫这是直播间弹幕/头像锐评场景，以及锐评要自然、短句、适合 TTS 播放。这段上下文只用于让 LLM 理解插件语境，不写观众档案，不进入直播总结，也不代表一次锐评已经发生。
如果 `developer_tools_enabled=true`，插件会在直播语境之后通过 `NekoDispatcher.push_developer_instructions()` 叠加开发者调试语境。手动从面板开启开发者模式时，额外通过 `respond` 播报一次进入调试状态；插件启动或配置重载时只静默注入，不自动播报。
关闭开发者模式时，插件会发送开发者调试恢复语境，只退出调试态，不关闭直播锐评语境，也不清空沙盒记录。
插件停止时会通过 `NekoDispatcher.push_context_restore()` 再发送一段 `ai_behavior="read"` 的恢复上下文，提醒猫猫停止把后续普通对话理解成直播间弹幕、头像锐评事件或观众互动事件。xTLM 的做法是连接后注入常驻玩法语境，本插件在此基础上额外补了关闭恢复，避免关闭后仍残留直播锐评状态。

锐评指令的**文本构造**集中在 `avatar_roast.build_request()`：它产出完整的 `InteractionRequest.prompt_text`，包含观众昵称/UID/弹幕、头像可见性与 META，以及给猫猫的锐评规则。规则编码了**自适应焦点**——昵称与头像哪个更有料就主打哪个，两者都有料就抓反差/呼应，都平淡就转弹幕/进场时机/直播节奏，避免硬尬夸；并强制“看不到的头像绝不脑补、避免与最近几条锐评重样、一句话适合 TTS”。独播（`solo_stream`）下，如果首次出场事件带有当前弹幕，首评应优先接住这句话，头像/昵称只作为出场印象点缀，避免变成纯头像或纯 ID 锐评；同播（`co_stream`）低打断。`build_request()` 只构造请求、不触发 NEKO。

`avatar_roast` 通过 `bili_identity` 解析出的 META 决定头像规则：`avatar_vision_ok=False`（没取到/识别不了）或默认头像 → 只能就“头像配置（默认/会动/带挂件）或昵称”发挥；能看到头像 → 可锐评其具体内容。

`danmaku_response.build_request()` 只用于同一 UID 已经完成出场锐评后的普通 `live_danmaku` 后续接话。`roast_once_per_uid` 的语义因此收敛为“每个观众只做一次出场锐评”，而不是“每个观众只能让 NEKO 回应一次”。后续弹幕仍必须经过 viewer profile、safety guard、dispatcher、dry_run 和 pacing；成功输出不调用 `viewer_profile.mark_roasted()`，避免把普通聊天回复继续累计成首评次数。

独播首评有独立节流：`solo_stream` 中真正的 `avatar_roast` 之间按活跃度间隔，`quiet=75s`、`standard=45s`、`active=30s`。若短时间内又有新 UID 发送弹幕，pipeline 不再连续做头像 / ID 出场锐评，而是把这条弹幕交给 `danmaku_response` 正常接话，并把该 UID 标记为已完成出场处理，避免之后又补一次头像锐评。

在 `dry_run` 链路验证中，pipeline 可以在同一运行会话内把一次成功到达 dispatcher 的首评 dry-run 视为临时出场标记，使同 UID 下一条弹幕走 `danmaku_response`；该标记只存在于当前 `RoastPipeline` 实例内，不写 `viewer_store`，不增加 `roast_count`，也不调用 `viewer_profile.mark_roasted()`。重新开始监听直播间会清空该临时标记，保证下一轮链路验证从干净窗口开始。

`danmaku_response` 的 prompt 只围绕当前弹幕接话：不能重复首次出场、头像、ID 或进场锐评模板；除非当前弹幕本身相关，否则不主动评价头像或昵称；独播（`solo_stream`）提示 NEKO 是台前唯一主播，需要自然接住话题；同播（`co_stream`）提示低打断，给主播留空间。

**2026-06-25 长直播发现的输入隔离缺口**：路由已经能把同 UID 后续弹幕送进 `danmaku_response`，但 dispatcher 曾经在 `identity.avatar_bytes` 存在时无条件附加头像 image part。结果是后续接话虽然没有走 `avatar_roast`，模型仍然看到头像，容易再次评价同一观众头像。当前修复是把头像视觉输入限制在显式 opt-in 的请求：`avatar_roast`（以及显式的开发者 demo / 未来明确声明需要视觉输入的模块）可以带头像；`danmaku_response`、`idle_hosting`、`active_engagement`、`warmup_hosting` 默认是纯文本输出请求。这个问题是输入边界污染，不是 `roast_once_per_uid` 失效。

`recent_interaction_context()` 会从最近成功投递或 dry_run 的互动结果中提取轻量上下文（路由、事件来源、观众弹幕或 idle hosting beat），供 `danmaku_response` 和 `idle_hosting` prompt 使用。它不假装掌握猫猫最终 TTS 文本，也不把完整历史 prompt 塞回模型；目标只是让下一次接话知道刚发生过什么，并明确避免复用同一个开场、包袱形状或主持节拍。

NEKO 输出由 `adapters/neko_dispatcher.py` 中的 `NekoDispatcher.push_roast()` 统一负责，pipeline 通过 `self.ctx.dispatcher.push_roast(request)` 进入。`push_roast()` 直接使用 `request.prompt_text` 作为文本 part；只有 `request.allow_avatar_image=True` 时才会按可见性附加头像 image part（压缩后超预算则省略并在文本里说明降级），不再自行拼装字段；然后调用：

```python
plugin.push_message(
    source="neko_roast",
    visibility=[],
    ai_behavior="respond",
    parts=parts,
    priority=...,
    metadata=...,
    target_lanlan=...,
)
```

其中 `ai_behavior="respond"` 是让猫猫按当前人设生成回应的关键。`visibility=[]` 表示这些字段只作为给猫猫的输入，不作为普通可见消息直接展示。头像 bytes 只作为本次 `parts` 的 image 输入，不写入观众档案或沙盒记录。

Hosted UI action 会补 `_ctx.lanlan_name`，插件进程复用 `ctx._current_lanlan`。沙盒模拟弹幕默认投递给当前界面猫猫；如果无法解析目标猫猫，必须返回友好失败并显示在沙盒结果中，不能假装成功。

## 数据边界

观众档案 v0.1 只存：

- UID
- 昵称
- 头像 URL
- 首次出现时间
- 最近出现时间
- 锐评次数
- 最近锐评时间
- 最近输出摘要

不要在 v0.1 写入主页资料、贡献值、进房累计、原始弹幕 payload 或头像 bytes。头像 bytes 只允许进入内存缓存或一次性输出请求。

**持久化（本地 JSON，当前固定默认目录）**：观众档案落本机 JSON 文件 `viewer_profiles.json`，当前仍**不走宿主 PluginStore**，以保持档案写入路径简单、可控、便于审计。历史上的 `store.enabled` 构造期冻结与插件数据不跟随 selected_root 已由 `Fix plugin host config and data root handling (#1884)` / `08b317f6` 修复（见 `docs/devlog.md`）。存储目录当前固定使用 `plugin.data_path()`；`viewer_store_dir` 自定义位置入口在 2026-06-19 真机测试后暂时屏蔽，待插件侧重新回归配置持久化 / host 数据根后再恢复。`viewer_store.py` 仍保留自定义目录能力与回退逻辑，但本阶段不向主播暴露。dashboard 暴露 `viewer_store`（当前目录 / 可写 / 是否自定义），面板据此显示与告警。

开发者沙盒数据规则：

- `recent_sandbox_results` 只保留运行时内存短期记录，插件重启即消失。
- 开发者模式关闭时不清空 `recent_sandbox_results`；只阻止继续查询、模拟弹幕和调用聊天开发者工具。
- “清空沙盒记录”只清沙盒内存记录和历史头像预览缓存，不影响观众档案、直播总结或真实直播记录。
- “清空观众档案”只清 `viewer_profiles.json` 中的观众档案，用于受控独播测试前重置首评状态；不清空 `recent_results`、沙盒记录、直播总结或安全队列。
- 沙盒查询不写 viewer store，不返回 base64 data URL，不写长期 preview 文件。
- 沙盒锐评结果不进入 `recent_results`，不进入直播总结。

## UI 约定

Hosted UI 位于 `ui/panel.tsx`。外壳 = **生命周期-域导航**（薄外壳 + 模块贡献），完整契约见 `docs/ui-architecture.md`。

界面分为**六个一级页**（+ `开发者沙盒` 按开发者模式条件追加），id / 顺序固定（契约测试 `test_panel_uses_six_top_level_tabs_in_order` 锁住）：

- `控制台 console`：开播总入口。**B站登录卡**（扫码图 + 检查登录 + 退出登录 + 登录状态，见「B站登录态」）+ 直播间 ID（**支持直播间链接**）+ 查询直播间 / 开始锐评（已开播时切为停止 / 暂停 / 恢复）+ 状态总览四格（直播间 / 监听 / **实时人气值** `live_connection.viewer_count` 由 `danmaku_core` 解析心跳回包，未连接显示 `-` / 安全状态）+ 直播模式 + dry_run 速开关。（原「直播间配置」页已折入此页。）
- `观众 viewers`：直播总结（本场真实锐评粗报 + 最近锐评摘要，数据来自运行时内存 `recent_results`，沙盒结果不进）+ 观众档案（UID / 昵称 / 锐评次数 / 最近出现 / 最近输出摘要）。
- `私信 dm`：占位页（即将上线，对应预留模块 `bili_dm_ingest`）。
- `自动化 automation`：占位页（即将上线，对应预留模块 `automation_ops`）。
- `⚙设置 settings`：平台参数。「节奏与安全」卡（dry_run / 自动急停 / 冷却秒数 / 队列上限 + 保存设置 / 清空队列）+ **「档案存储」卡**（当前只读展示插件默认目录；自定义入口暂时屏蔽，见「数据边界」）+ 高级状态（队列 / 安全门 / 最近 audit）+ 模块总览表 + 开发者模式开关。
- `开发者沙盒 dev`：仅开发者模式开启时出现。UID/URL 调试、只查询资料、模拟弹幕、请求结果、独立的最近沙盒记录和清空沙盒记录。

**「一张嘴」切分**：功能级参数（开关 / 强度 / 去重…）跟功能走、进「直播间互动」功能卡；平台级参数（dry_run / 节奏 / 队列 / 急停 / 模式）留「设置」。`live_enabled`（开启弹幕锐评）是功能级开关，**单一真相源 = 弹幕锐评卡的绿色卡头开关**（设置页不再重复）。

新增 UI 文案必须同步 8 个 locale 文件。

**模块卡错误边界**（兜底层④，可靠性第一原则）：`modulesSection` 里每张互动模块卡都经 `safeModuleCard(key, title, render)` 渲染——hosted-ui runtime 无 class 组件 / `componentDidCatch`，故用 `try/catch` 包同步渲染调用，未来任意第三方模块的 `config_schema` / 自定义渲染抛错只塌成一张降级卡（`panel.modules.renderError` 文案 + degraded 徽章），不黑屏整盘。配合 `ModuleRegistry` 的 degraded 隔离（层①），构成「一个模块炸了不搞砸直播」的完整保证。详见 `docs/ui-architecture.md` §4。

## 接入现有 B 站插件的规则

已**选择性复用** `bilibili_danmaku`：吞并其连接+解析层（`danmaku_core` / `livedanmaku` → `bili_live_ingest`）、移植 `bili_auth_service`（扫码登录 → `adapters`），并修了搬来的 `from_danmaku` `info[7]` bug；**弃**其 LLM / orchestrator / memory（neko_roast 走 NEKO 统一人设）。旧插件已**软退役**（移植 bug fix + 弃用横幅，未删——它仍是 P5 等的代码源；见 roadmap §7-5）。

未来如需复用更多旧插件能力，仍遵循：

- 优先软适配（调稳定 entry / 订阅标准事件出口）；确需吞并则**拆成小模块 + 补测试**证明边界仍成立。
- 不直接复制旧插件大文件；不引入其 LLM / 编排 / 记忆。
- **勿与 neko_roast 同直播间双连**旧插件（双 WS 冲突）。

## 测试门禁

Python 命令必须通过 `uv run` 执行。文档-only PR 可以不跑完整插件测试，但必须在 PR 描述中说明“仅文档变更，未运行代码测试”。任何触碰 Python、UI、i18n、契约、配置 schema、manifest 或 runtime 行为的 PR，至少运行：

```powershell
uv run pytest plugin/plugins/neko_roast/tests -q
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```

截至 2026-06-26：`uv run pytest plugin/plugins/neko_roast/tests -q` → **283 passed**；CLI check **0 error**（6 条模板 warning 允许）。当前允许存在模板级 warning（插件目录不是独立 git 仓库、无独立 `.github` / `.vscode` 配置），**不能存在 error**。

> 注：`plugin/tests/unit/server/test_plugin_ui_query_service.py` 是 host 侧测试，不在 neko_roast 验证范围内；跨模块禁碰范围以 `AGENTS.md` 为准。

若直播体验修复触碰 N.E.K.O 主前端播放门（例如 `static/app-audio-playback.js` 的 `voice_play_start` / `voice_play_end` 行为），必须额外运行对应主仓静态契约测试；当前播放门修复的最小回归命令是 `uv run pytest tests/unit/test_app_audio_playback_static.py -q`。

## 文档更新要求

文档职责以 `docs/README.md` 的 Canonical Source 矩阵为准。后续新增功能模块时，开发者必须同步留下对应文档；没有对应文档的新功能视为未完成。

新增或修改功能文档至少包含：

- 功能目的和不做什么。
- 责任模块。
- 入口和数据流。
- 触碰的契约、store、UI action/context。
- 是否经过 `safety_guard`，以及失败时如何降级。
- 读取或写入了哪些用户数据。
- 测试命令和主要测试场景。
- 已知限制。

按改动类型更新：

- 用户可见流程：更新 `docs/quickstart.md`。
- 架构、模块、pipeline、数据边界、协作规则、测试门禁：更新本文档。
- 新人阅读路径：更新 `docs/developer-guide.md`。
- 阶段目标和下一阶段顺序：更新 `docs/live-center-roadmap.md`。
- UI 架构和 Hosted UI 约束：更新 `docs/ui-architecture.md`。
- Agent / reviewer 硬规则：更新 `AGENTS.md`。
- 宿主 / SDK 侧历史问题：更新 `docs/devlog.md`。

## Message Plane 预算

头像进入 `push_message(parts=[{"type": "image", ...}])` 前必须经过 dispatcher 压缩，目标是低于 message plane 的内联 payload 预算（`MESSAGE_PLANE_PAYLOAD_MAX_BYTES`，默认 256KB；注意 wire payload 同时带 base64 与遗留 `binary_data`，实际占用约为原始 JPEG 的 ~2.3 倍）。若压缩后仍然过大，本次应省略 image part，改为纯文字锐评请求；不要为了保留头像而让整条 `respond` 被 ingest 丢弃。

历史坑（已修）：wire payload 的遗留 `binary_data` 字段是原始 `bytes`，而 message_plane PUB 端用 `json.dumps` 发布——`bytes` 不可 JSON 序列化会抛错并被上游 `except` 静默吞掉，导致**任何带图 `push_message`（不止本插件）都到不了 main_server**，表现为 UI 显示 queued 但猫猫无反应。已在 `plugin/message_plane/pub_server.py` 用 `json.dumps(default=...)` 把 bytes 转 base64 修复（消费端读 `parts[].binary_base64`，不受影响）。

## 直播语境提示词

`core/instructions.py` 里的长期提示词采用和 xTLM 类似的结构：先用 `ai_behavior="read"` 注入“猫猫正在和主播一起直播”的常驻场景，再用每条弹幕事件的 `ai_behavior="respond"` 触发即时反应。

关闭插件时不能假设模型会自动忘掉这段常驻场景；必须发送 `NEKO_ROAST_RESTORE_INSTRUCTIONS`，用新的 `read` 上下文覆盖直播状态。恢复消息同样只走 `NekoDispatcher`，不要在 runtime、module 或 UI action 中直接调用 `plugin.push_message()`。
开发者模式是直播语境上的第二层上下文：先注入 `NEKO_ROAST_CONTEXT_INSTRUCTIONS`，再按开关注入 `NEKO_ROAST_DEVELOPER_INSTRUCTIONS`。退出开发者模式只发送 `NEKO_ROAST_DEVELOPER_RESTORE_INSTRUCTIONS`，不要误发完整插件关闭恢复语境。

维护时不要只给字段说明。需要保留“猫猫是直播间同播伙伴，不是后台系统或插件播报员”的场景，让模型把弹幕当作直播现场互动来接话。即时事件提示词可以包含 UID、昵称、弹幕、强度、直播模式等结构化字段，但输出要求必须强调自然短句、不要复述字段、不要解释流程。

直播输出 prompt 的短回复合约集中在 `modules/_prompt_context.py` 的 `short_reply_rules()`：所有会让 NEKO 开口的直播路径都应共享同一套约束，即一句话、不写段落、最多 14 个中文字符或 8 个英文词；短弹幕要更短地回，优先给一个紧凑的直播包袱，不解释、不铺垫、不写第二句，不用逗号串多个分句，也不续写上一轮话题。当前已接入 `avatar_roast`、`danmaku_response`、`warmup_hosting`、`idle_hosting` 和 `active_engagement`。新增开口模块时必须复用该合约，并补契约测试锁住。
