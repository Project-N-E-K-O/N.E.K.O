# NEKO Live 开发文档

本文档面向后续参与 `neko_roast` 的开发者，记录**已落地设计**。它是架构边界、模块边界、协作规范、测试门禁和文档要求的 Canonical Source。配套 `live-center-roadmap.md` 只记录阶段目标、完成状态和下一阶段顺序。

对旧插件 `bilibili_danmaku` 采取**选择性复用**：取其**连接+解析层**（`danmaku_core` / `livedanmaku`）与**扫码登录**（`bili_auth_service`），**弃**其自带 LLM / orchestrator / memory（neko_roast 走 NEKO 统一 `dispatcher` → `main_server` 人设）。不直接复制大文件；迁移能力时拆成小模块并补测试证明边界仍成立。

## 命名与范围

当前产品名是 **NEKO Live**。`neko_roast` 是历史包名和内部代号，不作为用户可见产品名扩展。历史代号「猫娘锐评」只用于解释 v0.1 起点；新增文档、UI 文案、i18n 和 manifest 应使用 **NEKO Live**。

“直播中心 / Live Center”是架构定位，表示把主播直播的生命周期接进 NEKO；“弹幕锐评”是当前已落地的 v0.1 功能模块。后续新增模块时不要把产品名、架构定位和单个功能模块混用。

## 当前实现快照

更新日期：2026-07-03

核心闭环：**真实 B站直播间监听 → EventBus → live_events Selection → Roast Pipeline → Runtime → Dashboard**。`neko_roast` v0.1 已进入主线，产品命名已统一为 **NEKO Live**；「弹幕锐评」是第一个落地的垂直切片。锐评采用**自适应焦点**（昵称与头像哪个更有料就主打哪个，看不到的头像绝不脑补）。

协作基线：Phase 1 已落地 Canonical Source、PR 拆分规则和 Reviewer Checklist；Phase 2A 已落地模块 Owner Model 与 Protected Modules / Review Gate。Reviewer Checklist 的唯一 Canonical Source 是 `AGENTS.md`。

已落地能力（详见对应章节）：

- **真实直播接入**：吞并 `DanmakuListener`，`connect/disconnect` 启停真实监听（DoD 真机验证：新观众首条弹幕 → 猫全自动锐评其昵称+头像）。
- **事件中枢窗口择优**（P2.5）：富模型 `on_event` + `get_score` 冷却期缓冲择优（弹幕 / 礼物 / SC / 上舰同窗竞争）+ 首评即时。见「直播事件中枢」。
- **B站登录态**（P5）：扫码登录 + Fernet 加密凭据，接进头像抓取 / 弹幕连接 / 查询，根治 -352。见「B站登录态」。
- **健壮性**：`dry_run` 安全态、限流 / 自动急停 / 队列、配置写竞争免疫、查询 -352 友好降级、房号支持直播间链接输入。见各对应章节。
- **开发者沙盒**：离线 UID / URL 调试、内置 demo 案例。
- **观众画像治理**：长期档案只保存安全派生印象（偏好标签、常聊话题、接梗提示、互动风格、回复偏好、短摘要和避坑提示），并提供开发者模式下的单 UID 删除与印象重置动作。见「数据边界」。

### NEKO Live 核心模块边界

`core/runtime.py` 是运行编排层，不再承载直播表现策略本身。当前核心职责拆分如下：

| 模块 | 职责 | 不应做什么 |
|---|---|---|
| `core/runtime.py` | 生命周期、action 兼容入口、hosted-ui context、直播连接对外 API | 不写直播台词策略、不直接选择主动营业话题、不承载状态机细则、不拼装 dashboard 字段、不实现配置持久化细节、不内联模块注册清单 |
| `core/contracts_public.py` | contract 层公开投影 helper，统一 JSON-safe、脱敏、有限数值和对象拒绝规则 | 不读取 runtime，不处理业务路由，不保存数据；不得把 object / bytes 通过 `str()` 变成 dashboard / recent result 字段 |
| `core/contracts_config.py` | `RoastConfig` 字段、配置加载清洗、平台/房号 parser | 不读写配置文件，不启动 listener，不修改 runtime；配置加载只接受标量字符串/数字/bool，不能通过对象的 `__str__` / `__bool__` / `__int__` / `__float__` 推导开关或预算；`to_dict()` 只服务内部配置合并/持久化基线，dashboard 必须使用 `to_public_dict()` |
| `core/runtime_state.py` | runtime-local mutable state、recent 队列、idle/active 节流与轮转缓存初始化 | 不导入模块实现，不触发 pipeline，不读取配置文件 |
| `core/runtime_modules.py` | runtime 模块实例化、ReservedModule 注册顺序、pipeline 装配 | 不处理生命周期启停，不读取直播 payload，不修改配置 |
| `core/live_provider_router.py` | 平台 provider / identity provider 选择，B 站旧房号和抖音 `room_ref` 归一化 | 不实现真实监听，不解析协议包，不保存凭据；status 的 `platform`、`room_ref`、`room_id` 必须由 router 二次清洗，`is_listening` / `start_listening` / status `listening` 只接受 provider 返回的 exact bool，不能把 truthy 对象当成连接成功 |
| `core/module_registry.py` | 模块注册表 facade、模块协议、对外兼容入口 | 不内联 lifecycle hook 隔离，不内联 snapshot 投影，不触发 pipeline |
| `core/module_registry_lifecycle.py` | 模块 setup/teardown/on_enable/on_disable 的单点失败隔离与 audit 记录 | 不读取 status/config_schema，不构造 dashboard，不触发 pipeline |
| `core/module_registry_snapshot.py` | 模块 status/domain/config_schema 的安全投影和 degraded record 组装 | 不调用 lifecycle hook，不写 audit，不修改模块 enabled 状态；公开投影必须递归清洗为 JSON-safe 值，object/bytes 不得字符串化，cookie/token/signature 形态文本必须脱敏 |
| `core/runtime_auth_api.py` | `RoastRuntime` 上 B 站登录 / 凭据 action 的兼容 mixin | 不保存明文凭据，不实现扫码服务，不触发 pipeline |
| `core/runtime_instruction_api.py` | `RoastRuntime` 上直播语境 / 开发者语境 action 的兼容 mixin | 不直接 push message，不修改 pipeline，不清理状态 |
| `core/runtime_config_api.py` | `RoastRuntime` 上配置加载 / 更新 / listener reconcile 旧 helper 的兼容 mixin | 不实现配置持久化细节，不生成输出，不读写观众档案 |
| `core/runtime_live_input_api.py` | `RoastRuntime` 上直播 payload / lookup / result 记录旧入口的兼容 mixin | 不选择输出模块，不暴露 raw payload，不生成台词 |
| `core/runtime_developer_api.py` | `RoastRuntime` 上开发者沙盒 / lookup / 手动事件旧入口的兼容 mixin | 不绕过 `developer_tools_enabled`，不写观众档案 |
| `core/runtime_control_api.py` | `RoastRuntime` 上暂停/恢复/清队列/直播间连接旧 action 的兼容 mixin | 不生成输出，不选择话题，不读取 raw payload |
| `core/runtime_status_api.py` | `RoastRuntime` 只读投影兼容 facade，组合 dashboard / recent-context / live-status mixin | 不直接实现投影细节，不修改运行态，不触发 pipeline |
| `core/runtime_dashboard_api.py` | `RoastRuntime` 上 dashboard state、Runtime Health Rows、dashboard actions 的兼容 mixin | 不计算直播状态规则，不读取 recent context，不修改配置 |
| `core/runtime_recent_context_api.py` | `RoastRuntime` 上 recent interaction / viewer session / route / spent-output helper 的兼容 mixin | 不计算 live status，不拼装 dashboard，不触发输出 |
| `core/runtime_live_status_api.py` | `RoastRuntime` 上 live status、live state、director、readiness、speech explanation 的兼容 mixin | 不构造 prompt context，不拼装 dashboard UI 字段，不触发 hosting / active action，不承载 timing/age 旧 helper |
| `core/runtime_live_status_helpers.py` | `RoastRuntime` 上 live-status timing / age 私有 helper 的兼容 mixin | 不拼装 dashboard，不触发 hosting / active action，不记录 result |
| `core/runtime_config.py` | 配置加载/更新的协调门面、旧 helper 兼容入口 | 不内联配置激活细节，不内联持久化预算，不内联直播监听启停 |
| `core/runtime_config_activation.py` | 配置字段清洗、`RoastConfig` 激活、运行态窗口和 gate / safety guard 同步 | 不持久化配置，不启动/停止直播监听，不触发输出 |
| `core/runtime_config_persistence.py` | 配置持久化 best-effort 预算、host config API 兼容、失败/超时 audit | 不激活运行态配置，不改连接态，不启动/停止直播监听 |
| `core/runtime_live_listener.py` | 配置变化后的直播监听 reconcile、监听启停、连接态和 safety guard 同步 | 不持久化配置，不解析配置字段，不构造输出 |
| `core/runtime_live_controls.py` | 控制面板动作、暂停/恢复/清队列、清观众档案、单 UID 档案删除 / 印象重置、直播间设置/连接/断开、连接快照 | 不生成输出，不选择话题，不读取 raw payload |
| `core/runtime_live_controls.py` | 控制面板动作、暂停/恢复/清队列、清观众档案、单 UID 档案删除 / 印象重置、直播间设置/连接/断开、连接快照 | 不生成输出，不选择话题，不读取 raw payload；`live_connection_snapshot()` 只投影已知连接状态、非负 viewer_count、字符串 last_error 和 dict 型 connection_plan/reconnect，不能字符串化 listener_state 对象 |
| `core/runtime_instructions.py` | 直播常驻语境、开发者语境、语境恢复和调试播报的注入编排 | 不直接调用 `plugin.push_message()`，不改变 pipeline，不清空观众档案 |
| `core/runtime_dashboard.py` | `dashboard_state` 只读面板总装层 | 不计算 health row 细节，不维护 action 列表，不触发 pipeline；config / recent / audit / profile 等进入 UI 前必须已经是公开安全投影 |
| `core/runtime_health.py` | Runtime Health Rows 只读投影 | 不拼装 dashboard，不触发 pipeline，不修改配置/安全门/连接状态；health row 的 outcome/reason/detail/last_error 只接受字符串，count/latency 只接受非负标量，不能字符串化或 `int()` 化对象 |
| `core/runtime_dashboard_actions.py` | hosted-ui dashboard action 列表的静态投影 | 不读取 runtime，不执行 action，不接触连接/安全门状态 |
| `core/runtime_developer_tools.py` | 开发者沙盒入口、B 站用户 lookup、手动模拟事件、沙盒记录清理、开发者模式后端权限检查 | 不绕过 `developer_tools_enabled`，不写观众档案，不清理直播 recent result |
| `core/runtime_bili_auth.py` | B 站登录 action、凭据重载、logout 清理与扫码服务装配 | 不明文记录凭据，不写 config，不触发直播 pipeline |
| `core/runtime_live_input.py` | 直播 payload 归一化入口、Gift/SC/Guard support metadata 暴露、result 投影、弹幕回复 metadata 暴露 | 不选择输出模块，不生成台词，不暴露 raw payload，不绕过 pipeline / safety guard |
| `core/runtime_live_input.py` | 直播 payload 归一化入口、Gift/SC/Guard support metadata 暴露、result 投影、弹幕回复 metadata 暴露 | 不选择输出模块，不生成台词，不暴露 raw payload，不绕过 pipeline / safety guard；support-event 分类只接受字符串 `event.raw["event_type"]`，对象/bytes 不得被字符串化成礼物或 SC 类型；lookup result / audit 的 `room_ref` 只接受字符串或正整数归一结果，不能字符串化对象 |
| `core/pipeline.py` | Public `RoastPipeline` facade, session compatibility helpers, permission gate, safety before-event, and preflight handoff | no viewer/profile resolution, UID lock flow, request building, dispatch call, output text generation, or live connection config writes |
| `core/pipeline_flow.py` | Post-safety event flow: viewer/profile resolution, UID lock/session gate, route handoff, request build, dispatch stage, after-event cleanup | no permission gate, no safety before-event, no dispatcher implementation, no viewer store internals |
| `core/safety_guard.py` | Public safety gate facade: connected / pause / queue / output cooldown / failure auto-stop entrypoints | no direct clock reads, no inline output cooldown math, no inline failure-window trimming |
| `core/safety_guard_cooldown.py` | Output cooldown timing and developer-sandbox cooldown bypass | no queue mutation, no failure-window state, no audit writes |
| `core/safety_guard_failures.py` | Pipeline/output failure windows, audit records, and auto-stop trip logic | no output cooldown math, no queue gate, no event/output decision construction |
| `core/safety_guard_types.py` | Shared safety guard type aliases | no runtime state, no decisions, no audit writes |
| `core/pipeline_routing.py` | 纯路由规则：Gift/SC/Guard support event、hosting source、重复弹幕、独播首评 pacing、模块 route 与 viewer-gate reason | 不查观众档案，不调用 dispatcher，不写 recent result |
| `core/pipeline_viewers.py` | pipeline 的观众身份解析、临时 profile / 持久 profile 准备、对应 step 记录 | 不决定 route，不构造 request，不触发 dispatcher，不写 result |
| `core/pipeline_requests.py` | pipeline route 到模块 `build_request()` 的请求构造入口 | 不解析身份，不读写观众档案，不触发 dispatcher，不记录 result |
| `core/pipeline_dispatch.py` | Output stage after request construction: safety before-output, dispatcher call, dry-run / skipped / pushed branches, successful profile marking | no route decisions, identity resolution, request construction, or UID lock/session gate ownership |
| `core/pipeline_session.py` | pipeline 的 UID lock、session/dry-run 首评标记、独播首评 pacing 状态 | 不决定 route，不构造 request，不触发 dispatcher，不记录 result |
| `core/pipeline_results.py` | pipeline result 旧导入兼容 facade | 不承载 gate skip、dispatch outcome 或 failure accounting 具体实现 |
| `core/pipeline_skip_results.py` | permission / safety before-event / viewer gate / missing uid 的 skip 或 reject result 与 audit 口径 | 不处理 dispatcher 输出，不做安全失败记账，不查 viewer store，不构造 request |
| `core/pipeline_dispatch_results.py` | dispatcher dry-run / skipped / pushed result、audit 和 `record_result` 口径 | 不调用 dispatcher，不做 safety gate，不做安全失败记账，不查 viewer store |
| `core/pipeline_failure_results.py` | dispatcher/pipeline exception 的 failed result、安全失败记账、failure audit 和 `record_result` 口径 | 不处理 gate skip，不处理 successful dispatch outcome，不做 route/request/viewer 逻辑 |
| `core/live_reply_policy.py` / `core/live_output_policy.py` | 插件侧回复合约兼容 facade、旧导入路径出口 | 不承载具体质量兜底、裁剪、记忆渲染或 metadata 桥接细节 |
| `core/live_reply_contract.py` / `adapters/output_contract_bridge.py` | 模块字数上限、插件私有回复策略 metadata、调试/复盘口径 | 不导入 `main_logic`，不修改宿主最终输出层，不要求宿主理解这些字段 |
| `core/live_output_quality.py` | 禁止词 / 低置信漂移 / 模板营业 / 无聊弹幕回复的质量兜底 | 不裁剪句子，不渲染 prompt 合约，不合并 metadata |
| `core/live_output_shape.py` | 最终短句裁剪、未完成二选一修剪、质量兜底应用和 shape metadata | 不维护禁止词表，不读取 recent-output 记忆 |
| `core/live_output_memory.py` | recent NEKO Live 输出负例整理、recent reply avoidance 渲染 | 不判断输出质量，不修改 reply metadata |
| `core/live_output_contract_prompt.py` | 输出合约 prompt 渲染、route note、callback metadata 合并 | 不裁剪最终文本，不直接调用 dispatcher |
| `core/meme_knowledge.py` / `data/meme_knowledge.json` | 插件内热梗知识库、离线检索、可选 prompt 提示和 `meme_hint_*` metadata 来源；第一版由维护者直接编辑 JSON 条目 | 不联网抓取，不替代当前弹幕语义，不强制猫猫用梗，不进入宿主核心 |
| `core/recent_context.py` | recent prompt 上下文构造、同观众 session 上下文、上下文行渲染与压缩 | 不判定 route，不维护 spent-output family 词表，不决定是否开口 |
| `core/recent_context_routes.py` | recent result route 归一化、gift / SC / Guard signal route 识别 | 不渲染 prompt 上下文，不读取 runtime 状态，不生成输出 |
| `core/recent_output_families.py` | 真实 pushed 输出提取、synthetic/dry_run 输出排除、spent-output family 识别与 recent family 窗口统计 | 不判定事件 route，不构造 prompt 文案，不读取直播连接状态 |
| `core/live_status.py` | Live Status / Live State 兼容 facade，统一导出 core / timing / idle / active / director / readiness helper | 不触发 pipeline，不选择素材，不承载基础状态计算、时间阈值表、director 决策或面板解释投影 |
| `core/live_status_core.py` | `live_status_summary` / `live_state_summary` 基础状态投影 | 不读取 runtime 对象，不触发 pipeline / dispatcher，不生成 dashboard 字段 |
| `core/live_status_timing.py` | activity-level 阈值、age/iso-age、recent live danmaku / output 年龄计算 | 不读取配置以外的 runtime 状态，不生成 dashboard 字段 |
| `core/live_status_idle.py` | idle hosting eligibility、quiet state 到 idle hosting 的等待时间 | 不读取 runtime 对象，不决定 active engagement，不生成 dashboard 字段 |
| `core/live_status_active.py` | active engagement eligibility、recent danmaku cooldown、idle hosting takeover / defer reason | 不读取 runtime 对象，不决定最终 next action，不触发 active action |
| `core/live_status_director.py` | Live Director 下一动作决策，聚合 idle / active eligibility 结果 | 不读取 runtime 对象，不生成 dashboard 字段，不触发 hosting / active action |
| `core/live_status_readiness.py` | Solo Test Readiness、“为什么没说话” speech explanation 投影 | 不决定真实连接状态，不触发 pipeline，不修改 readiness 输入 |
| `core/runtime_hosting_api.py` | `RoastRuntime` 上 idle/warmup hosting action、旧 helper、loop 控制的兼容 mixin | 不选择素材实现细节，不持有状态，不绕过 `live_hosting_director` |
| `core/runtime_active_engagement_api.py` | `RoastRuntime` 上主动营业旧 helper / action API 的兼容 mixin | 不持有状态，不选择 topic 实现细节，不绕过 `runtime_active_engagement` / `active_topic_selector` |
| `core/runtime_active_engagement.py` | 主动营业手动/自动触发、quiet/idle gate、skip result 记录、active event 构造 | 不选择 topic 素材，不绕过 pipeline / safety guard / dispatcher |
| `core/active_topic_selector.py` | 主动营业 topic 选择编排、runtime 状态代理 | 不直接调用 dispatcher，不绕过 safety guard，不承载候选筛选细节或旧 helper facade |
| `core/active_topic_compat.py` | 主动营业旧 helper / runtime API 兼容 facade，委托 rules / sources / shapes / selection / pack | 不选择最终 topic，不触发输出，不构造事件/result |
| `core/active_topic_selection.py` | 主动营业选择旧导入兼容 facade | 不承载候选挑选、topic 组装、来源抓取、输出触发或状态机细节 |
| `core/active_topic_candidate_picker.py` | 主动营业候选递进选择、fallback 选择、近期 axis / family / title 避让、topic cache 清理 | 不组装 topic，不写 recent 轮转状态，不抓候选来源，不触发输出 |
| `core/active_topic_builder.py` | 主动营业 topic 字段组装、shape guard 应用、recent 轮转状态记账 | 不选择候选，不清 topic cache，不抓候选来源，不触发输出 |
| `core/active_topic_sources.py` | 主动营业候选来源聚合和 live-thread / recent / trending skip reason 归并 | 不抓网页，不扫描 recent result 细节，不做最终选择，不记录已用 topic，不触发输出 |
| `core/active_topic_live_thread_source.py` | 从近期直播弹幕 result 中聚合多人/多次提到的轻量直播线程候选，生成 `interest` / `relevance` / `risk` / `evidence` 形态 | 不调用 LLM，不新增后台任务或持久化，不做最终选择，不触发输出 |
| `core/active_topic_recent_source.py` | 从近期直播弹幕 result 中筛选主动营业候选、过滤首评/旧弹幕/单 UID 刷屏/低质量文本 | 不抓外部话题，不管理趋势缓存，不做最终选择，不触发输出 |
| `core/active_topic_trending_source.py` | B 站公开趋势候选抓取、缓存、标题压缩和 material profile 补全 | 不读取 recent result，不处理观众刷屏/首评上下文，不做最终选择，不触发输出 |
| `core/live_hosting_director.py` | warmup / idle hosting 的 action 编排、pipeline 触发、自动调度 loop、旧 helper 兼容入口 | 不内联 gate 判断，不选择 host beat 细节，不生成最终台词，不处理普通弹幕，不承载事件/result 样板 |
| `core/live_hosting_gates.py` | warmup / idle hosting 的只读 live-state 快照、skip reason 与自动触发节流判断 | 不构造 ViewerEvent，不触发 pipeline，不记录 result，不选择 host beat |
| `core/live_hosting_events.py` | warmup/idle hosting 的 `ViewerEvent` 构造、gate skip `InteractionResult` 和 audit 记录 | 不选择 host beat，不触发 pipeline，不管理 loop |
| `core/live_hosting_beats.py` | idle hosting host beat 旧导入兼容 facade | 不承载素材规则、候选选择、轮转状态记账、pipeline 触发或事件构造 |
| `core/live_hosting_beat_rules.py` | idle hosting host beat 清洗、stage 归类、stage 排序和标题相似判断 | 不读取 runtime 状态，不触发 pipeline，不构造 ViewerEvent，不记录 result |
| `core/live_hosting_beat_picker.py` | idle hosting host beat 选择、近期 key / axis / family / reply_affordance / title 避让和逐步放宽 | 不承载素材表，不直接写 recent 轮转状态，不触发 pipeline，不构造 ViewerEvent |
| `core/live_hosting_beat_state.py` | idle hosting beat index 推进、recent beat key/axis/title/family/reply_affordance 记账和 payload 补全 | 不选择候选，不读取素材表，不触发 pipeline，不构造 ViewerEvent |
| `core/live_hosting_loop.py` | Auto-loop runner for warmup / active engagement / idle hosting scheduling | no event construction, beat selection, request building, output dispatch, or result recording |
| `core/active_topic_rules.py` | 主动话题过滤兼容 facade、对旧 `_xxx` helper 保持出口 | 不持有 runtime 状态，不承载素材画像、文案 shape 表或安全词表细节 |
| `core/active_topic_meaning.py` | 主动营业候选是否值得展开、泛泛主持话术过滤、过滤原因归类 | 不做安全词维护，不解析 mention，不选择 topic |
| `core/active_topic_safety.py` | 低置信话题、安全词、技术/攻略漂移、素材文本清洁度 | 不选择 topic，不读取 runtime 状态 |
| `core/active_topic_filters.py` | 直接请求、未点名请求、纯反应、测试/运行反馈过滤 | 不判断观众 @ 归属，不处理素材 profile |
| `core/active_topic_mentions.py` | `@猫猫` / `@其他观众` 的 mention 归属判断 | 不生成回复，不读取观众档案 |
| `core/active_topic_rotation.py` | 主动营业 topic 标题归一化、相似度去重、连续 streak 判定 | 不读取素材、不选择 topic、不触发输出 |
| `core/active_topic_materials.py` | 主动营业素材 helper 旧导入兼容 facade | 不承载 family 识别、profile hints、候选选择、原始弹幕过滤或 runtime 状态读取 |
| `core/active_topic_material_family.py` | 主动营业素材 family 识别：短回调、二选一、主播力自测、物件场景、房间氛围等 | 不生成 profile hints，不读取 runtime，不过滤原始弹幕 |
| `core/active_topic_material_profile.py` | 主动营业素材 preferred shape / fun axis / live column / reply affordance / hint 补全 | 不识别 family，不读取 runtime，不选择 topic |
| `core/active_topic_pack.py` | 主动营业素材 topic_pack 分类：micro poll / verdict / callback / observation 等 | 不选择 topic，不记录轮转状态 |
| `core/active_topic_shapes.py` | 主动营业 shape 轮转和 shape streak guard | 不读取素材，不判断话题质量，不触发输出 |
| `core/active_engagement_copy.py` | 主动营业 shape 的 hook / hint / intent / fun-axis / reply-affordance 文案 | 不选择素材、不判断是否开口 |
| `core/live_content.py` | live-content 素材公共访问入口 | 不直接承载大素材表，不做选择、不读 runtime 状态 |
| `core/live_content_materials.py` | 静态素材池兼容 facade，保留旧导入出口 | 不承载具体素材表，不做选择、不读 runtime 状态 |
| `core/live_content_host_materials.py` | idle hosting / warmup hosting host beat 访问器，返回素材 dict 副本 | 不承载素材表，不做主动营业 topic 选择，不读 runtime 状态 |
| `core/live_content_host_catalog.py` / `data/idle_hosting_beats.json` | idle / warmup host beat JSON 素材入口、兼容聚合和坏 JSON 回退；第一版由维护者直接编辑 JSON 条目 | no runtime reads, no selection logic, no prompt rendering |
| `core/live_content_host_catalog_{choice,callback,tease,challenge,mood}.py` | idle / warmup host beat legacy fallback groups split by `fun_axis` | fallback static dict entries only; no rotation, filtering, or runtime reads |
| `core/live_content_active_materials.py` | active engagement fallback topic 访问器，返回素材 dict 副本 | 不承载素材表，不做冷场陪播调度，不读 runtime 状态 |
| `core/live_content_active_catalog.py` | active engagement fallback topic compatibility aggregate; keeps old constant import and original key order | no runtime reads, no selection logic |
| `core/live_content_active_catalog_{choice,callback,tease,challenge,mood}.py` | active engagement fallback topic static material groups split by `fun_axis` | static dict entries only; no rotation, filtering, or runtime reads |

新增直播表现能力时优先判断属于哪一层：pipeline 执行流程编排进 `pipeline`，事件到模块的纯路由规则进 `pipeline_routing`，观众身份 / profile 准备进 `pipeline_viewers`，route 到模块 request 构造进 `pipeline_requests`，UID lock / session 标记 / 首评 pacing 状态进 `pipeline_session`，result / audit 样板进 `pipeline_results`；模块注册 facade 进 `module_registry`，模块生命周期失败隔离进 `module_registry_lifecycle`，模块 status/config_schema 安全投影进 `module_registry_snapshot`；配置更新协调进 `runtime_config`，配置字段清洗 / 内存激活进 `runtime_config_activation`，配置持久化预算进 `runtime_config_persistence`，直播监听 reconcile 进 `runtime_live_listener`；输出合约结构进 `live_reply_contract`，旧导入兼容进 `live_reply_policy` / `live_output_policy`，质量兜底进 `live_output_quality`，最终文本裁剪进 `live_output_shape`，recent-output 负例渲染进 `live_output_memory`，合约 prompt / metadata 合并进 `live_output_contract_prompt`，对宿主只通过 `output_contract_bridge` 传递 metadata；recent prompt 上下文行渲染进 `recent_context`，recent result route / signal 归一化进 `recent_context_routes`，真实 pushed 输出提取与 spent-output family 识别进 `recent_output_families`；基础直播状态进 `live_status`，activity-level 阈值和年龄计算进 `live_status_timing`，idle hosting eligibility 进 `live_status_idle`，active engagement eligibility 进 `live_status_active`，Live Director 下一动作决策进 `live_status_director`，Solo Test Readiness / speech explanation 进 `live_status_readiness`，runtime 上的旧 action/helper 兼容入口按 auth / instruction / config / live-input / developer / control / status / hosting / active-engagement 拆进对应 `runtime_*_api.py`，真实实现仍归对应 `runtime_bili_auth`、`runtime_instructions`、`runtime_config`、`runtime_live_input`、`runtime_developer_tools`、`runtime_live_controls`、`runtime_dashboard`、`runtime_health`、`runtime_dashboard_actions`、`live_hosting_director`、`runtime_active_engagement` 模块；主动营业候选来源进 `active_topic_sources`，主动营业最终选择和轮转进 `active_topic_selector`，旧 helper / runtime API facade 进 `active_topic_compat`，冷场/开场调度进 `live_hosting_director`，warmup / idle gate 与 skip reason 进 `live_hosting_gates`，冷场 host beat 选择 / stage / 近期素材避让进 `live_hosting_beats`，兼容过滤入口进 `active_topic_rules`，话题价值 / 泛泛主持过滤进 `active_topic_meaning`，低置信 / 安全素材过滤进 `active_topic_safety`，请求/反应/运行反馈过滤进 `active_topic_filters`，观众 mention 归属进 `active_topic_mentions`，相似度 / streak 进 `active_topic_rotation`，素材 family / profile 进 `active_topic_materials`，topic_pack 分类进 `active_topic_pack`，shape 轮转/防连发进 `active_topic_shapes`，主动营业 shape 文案进 `active_engagement_copy`，素材访问入口进 `live_content`，素材兼容出口进 `live_content_materials`，冷场陪播素材表进 `live_content_host_catalog`，冷场陪播素材访问器进 `live_content_host_materials`，主动营业 fallback 素材表进 `live_content_active_catalog`，主动营业 fallback 访问器进 `live_content_active_materials`。只有生命周期、action 兼容入口或跨模块 facade 才进入 `runtime.py`；运行态缓存初始化进入 `runtime_state.py`；模块实例化/注册顺序进入 `runtime_modules.py`。
Catalog split note: `data/idle_hosting_beats.json` is the first-place idle / warmup host beat material source for maintainers; `live_content_host_catalog_*` remain legacy fallback groups by `fun_axis`, and `live_content_host_catalog` keeps the old exported constant plus JSON load/fallback behavior. `live_content_active_catalog_*` still hold active-engagement static material groups by `fun_axis`; `live_content_active_catalog` is the compatibility aggregate that preserves old exported constants and original key order. The active choice catalog is further split into `live_content_active_catalog_choice_room.py`, `live_content_active_catalog_choice_props.py`, and `live_content_active_catalog_choice_verdict.py`, while `live_content_active_catalog_choice.py` keeps the public aggregate export.

Active topic split note: `active_topic_selector.py` is the async orchestration layer, `active_topic_candidate_picker.py` owns candidate picking and topic-cache reset, `active_topic_builder.py` owns topic payload assembly and recent rotation bookkeeping, and `active_topic_selection.py` is only a backward-compatible import facade.

Active topic source split note: `active_topic_sources.py` only merges source modules and preserves skip-reason precedence, `active_topic_live_thread_source.py` owns no-cost recent live-thread aggregation, `active_topic_recent_source.py` owns single recent danmaku filtering, and `active_topic_trending_source.py` owns Bilibili trending fetch/cache shaping.

Active topic material split note: `active_topic_materials.py` is a compatibility facade. `active_topic_material_family.py` owns material family classification, while `active_topic_material_profile.py` owns preferred shape / fun axis / live column / reply affordance / hint profile hints.

Safety guard split note: `safety_guard.py` remains the only public safety gate object used by pipeline/runtime. `safety_guard_cooldown.py` owns output timing and sandbox cooldown bypass, while `safety_guard_failures.py` owns failure windows, audit records, and auto-stop trips. New code should not call these helpers directly unless it is extending the guard facade.

Pipeline flow split note: `pipeline.py` owns the public `RoastPipeline` facade and preflight gates before `safety_guard.before_event`. `pipeline_flow.py` owns the post-safety event flow, including viewer resolution, UID lock/session gate, route handoff, request build, dispatch stage, and `safety_guard.after_event` cleanup.

Pipeline result split note: `pipeline_results.py` is a compatibility facade. `pipeline_skip_results.py` owns gate skip/reject result shapes, `pipeline_dispatch_results.py` owns dispatcher dry-run/skipped/pushed outcomes, and `pipeline_failure_results.py` owns dispatcher/pipeline exception result shapes plus safety failure accounting.

Idle hosting beat split note: `live_hosting_beats.py` is a compatibility facade. `live_hosting_beat_rules.py` owns clean material access and stage rules, `live_hosting_beat_picker.py` owns freshness-guard candidate picking, and `live_hosting_beat_state.py` owns beat-index advancement plus recent beat bookkeeping.

Prompt context split note: `modules/_prompt_context.py` is a compatibility facade for live interaction prompt helpers. `_prompt_rules.py` owns shared prompt rule lists and length/charm/anti-repeat contracts, `_prompt_context_compaction.py` owns string compaction for recent prompt lines, and `_prompt_context_blocks.py` owns recent/viewer/live-event prompt block rendering. New live speech modules should import through `_prompt_context.py` unless they are extending one of those internals directly.

Recent context split note: `recent_context.py` is a compatibility facade for runtime recent-memory helpers. `recent_context_builders.py` owns scanning recent results and assembling recent/viewer context lists, `recent_context_lines.py` owns idle/active/viewer context-line rendering, `recent_context_text.py` owns plain text compaction, `recent_context_routes.py` owns route/signal normalization, and `recent_output_families.py` owns actual output extraction plus spent-output family detection.

Runtime active engagement API split note: `runtime_active_engagement_api.py` now owns only active-engagement action compatibility (`trigger_active_engagement`, maybe-trigger, event construction, and skip result recording). `runtime_active_topic_api.py` owns runtime compatibility proxies for topic selection/candidate/shape helpers, while `runtime_active_topic_rules_api.py` owns legacy rule-helper proxies. This keeps action orchestration separate from active topic rule compatibility.

主要链路（直播弹幕路径）：

```text
弹幕 WS → danmaku_core → on_event(LiveDanmaku 富模型)
  -> bili_live_ingest 包成 LiveEvent → event_bus.publish(type)   事件中枢路由（按 type 分发，见「直播事件中枢（EventBus）」）
  -> live_events 订阅 "danmaku" / "gift" / "super_chat" / "guard"（冷却期缓冲、get_score 择优 / 空闲态首条即时；room_topic 仅给 prompt builder 提供弹幕主题上下文）
  -> handle_live_payload -> pipeline.handle_event:
       safety_guard.before_event()      连接/暂停/队列闸门
    -> live_provider.resolve_identity()  平台身份解析；B 站 UID→昵称/头像/META（登录态过 -352），抖音只消费已清洗字段
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
- 所有观众档案写入只允许走 `stores/viewer_store.py`；长期档案字段必须是 JSON-safe 脱敏公开值，UID 不能由 object / bytes 或 cookie/token/signature 形态文本推导。
- 所有审计记录只允许走 `stores/audit_store.py`；audit 会进入 dashboard / recent audit，写入时必须投影成 JSON-safe 脱敏字段，object / bytes 不得字符串化，cookie / token / signature / authorization 等凭据形态文本必须被清洗。
- 直播安全门是必经路径，不允许绕过 `core/safety_guard.py`。
- 隐私相关原始数据不要写入 logger；需要调试时写脱敏 audit，或按项目规范使用 `print`。
- 登录凭据只走 `stores/credential_store.py` 加密落盘，**绝不**写 audit / log / config / UI（只回显 uid / 用户名 / 是否登录）。

### 多平台直播输入边界

后续新增 B 站以外的直播平台时，必须先把平台差异收口在 live provider 层，再进入现有 EventBus / pipeline / safety / dispatcher 链路。`runtime`、`pipeline`、`live_events` 不应散落平台判断分支。

平台接入的推荐形状：

```text
platform ingest
  -> live provider router
  -> EventBus / live_events
  -> runtime.handle_live_payload
  -> provider.normalize()
  -> ViewerEvent(source="live_danmaku")
  -> pipeline
  -> provider identity resolver
  -> viewer_profile
  -> dispatcher
```

硬边界：

- 新平台的普通弹幕若要触发 AI，必须归一成 `ViewerEvent(source="live_danmaku")`，继续经过 `PermissionGate`、`SafetyGuard`、pipeline、dispatcher。
- 礼物、入场、关注、点赞、热度等非普通弹幕事件默认不得触发 AI；除非单独完成事件族设计、成本讨论、Owner review 和测试，否则只能 signal-only 或丢弃。
- provider 可以保留平台私有连接状态，但传给 `handle_live_payload` / `ViewerEvent.raw` 的 payload 必须是脱敏后的最小字段；cookie、token、签名参数、完整 HTML、protobuf 原包、头像 bytes/base64 不得进入 config、audit、UI、viewer profile 或 recent result。
- 观众身份必须平台前缀化，例如 `bilibili:<uid>`、`douyin:<uid>`；不能继续假设纯数字 UID 全平台唯一。
- 账号认证态和直播间目标必须分离：credential/cookie 只表示平台登录状态，`room_ref` / `room_id` 才表示当前监听目标。
- 新平台的连接、心跳、重连、ack、降级和停止逻辑必须有显式上限与可见状态，不得递归无限重连，不得阻塞插件启动或关闭。
- 若新平台依赖非官方 Web 协议、手动 cookie、额外 runtime 依赖或协议逆向样本，必须按「成本类改动先讨论」列 Decision Points；未批准前只能写探索 Draft。

抖音只读接入的阶段计划记录在 `live-center-roadmap.md`「多平台直播输入 / 抖音只读接入计划（分阶段实施中）」；本文只保留长期架构边界。

## Runtime Observability

运行态观测语言的 Canonical Source 是 `runtime-observability.md`。本文只保留硬约束摘要：

- Phase 2C 当前停靠点：Dispatcher Outcome、Selection Decision Chain、Runtime Health Rows、事件级 `trace_id`、轻量 Runtime Timeline 和 Monitor snapshot emission 已落地。
- Runtime Timeline 必须通过 `trace_id` 关联；不要用 UID、event type 或时间邻近关系猜测同一条事件。
- 新事件路径必须能说明 Runtime Timeline 中的 stage、outcome 和 skip reason。
- 预期拦截使用 `skipped`，异常使用 `failed`，降级继续运行使用 `degraded`。
- Safety Guard 和 Dispatcher 必须是可见 lifecycle stage，不能被新模块绕过或隐藏。
- Dashboard 未来必须能解释“事件到了哪、为什么没输出、Dispatcher 是否真实输出”，但具体 UI 布局不在本文规定。
- 观测数据必须脱敏；不得暴露 raw payload、cookies、tokens、avatar bytes/base64 或未脱敏私密数据。

## 协作规范

`neko_roast` 已进入多人协作阶段。后续改动必须先按 Feature → Slice → PR 拆分，保持每个 PR 可独立 review、测试和回滚。

### 成本类改动先讨论

插件内任何会引入或明显改变成本的改动，必须先讨论并获得维护者拍板，再进入实现。这里的成本包括但不限于：

- 计算成本：内存、CPU、后台 timer、队列、缓存、重试或轮询。
- Token 成本：额外 prompt、recent context、长期上下文、模型调用次数或更长输出链路。
- 依赖成本：新增 Python / TS / runtime 依赖、外部服务、平台 API 或宿主能力假设。
- 数据成本：新增持久化、索引、历史记录、IO、迁移或清理策略。
- 核心逻辑成本：改变 pipeline、safety guard、dispatcher、EventBus、module registry、runtime action、selection、prompt/recent context 等核心路径。

讨论材料不能只是 plain doc 描述，必须列出需要拍板的 Decision Points：

- 要不要做：目标、非目标、触发场景、是否属于当前 Slice。
- 成本预算：预计内存 / CPU / timer / token / IO / 依赖增量，以及默认上限。
- 边界影响：涉及哪些模块、contracts、store、UI、action、hosted-ui context 或本体接口。
- 方案选择：至少列出推荐方案、备选方案、拒绝方案，以及各自取舍。
- 运行策略：开关、默认值、限流、降级、回滚和失败后的可见状态。
- 验证方式：需要补哪些测试、观测指标、audit / dashboard / runtime health 字段。

Decision Points 未被明确回复或批准前，不提交实现 PR。若必须写代码验证可行性，先用 Draft，并把代码标成探索性质，不把它当成已批准实现。

### 主核心收缩边界

NEKO Live 的直播专用回复策略默认留在插件内。宿主 / 主程序核心只提供通用接口、通用消息投递、通用 metadata 透传、通用上下文读取或恢复能力；不得为了 NEKO Live 单独在 `main_logic` 增加最终回复改写、抑制、重试、质量兜底、直播专用 audit 或直播专用 memory 规则。

会影响猫猫正常回复普通用户的逻辑必须优先做成插件内置能力，或先抽象成明确的通用核心接口再评审。当前已确认继续由插件持有的能力包括：低价值弹幕选择性跳过、房间主题桥接、头像 / UID 锐评提示、短输出合约、recent-output 负例、直播热梗提示、冷场素材、主动营业素材和直播复盘口径。插件只能通过 `NekoDispatcher`、request metadata、prompt contract 和只读 Dashboard / Monitor 投影与宿主交互，不能要求宿主理解 NEKO Live 私有策略字段。

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
- PR 合并后预计还会从更新后的主线创建后续独立 PR。

Draft 转 Ready 前必须具备：

- 明确的 Slice 范围。
- 测试命令和结果。
- 文档影响说明。
- 已知风险、回滚 / 降级方式。

### 禁止堆叠式 PR

禁止使用 stacked PR / 堆叠式 PR：不得让一个未合并的功能 PR 以另一个未合并的功能分支或 PR 为 base，也不得同时维护需要逐层 merge、逐层解决冲突、逐层传播上游修复的开放 PR 链。即使 GitHub 显示 base 为 `main`，只要后续 PR 的代码正确性、测试、review、合并或回滚依赖尚未合并的前置 PR，也属于逻辑堆叠，同样禁止。

所有 PR 必须采用独立式 PR：

- 从最新 `main`（或维护者明确指定的 release branch）创建独立分支。
- PR 的 base 必须是 `main` 或指定 release branch，不得是另一个开发中分支。
- 一个 PR 必须能在该 base 上独立 review、测试、合并和回滚；不得依赖其它未合并 PR 才能通过测试或成立。
- 多个 Slice 有先后依赖时，先完成并合并前置 PR；确认其进入主线后，再从更新后的主线创建下一条独立分支和 PR。
- 前置 PR review 修复后，不得把新 head 逐层 merge / cherry-pick 到多个开放下游 PR。尚未开始的后续工作直接基于更新后的主线；已经开始的本地工作应在前置 PR 合并后重新对齐主线，再单独提交。
- `main` 在开发期间推进时，只在当前独立 PR 分支中按普通 merge 方式吸收最新主线并解决一次冲突；不得通过改 base、force-push、rebase 或重写历史维护级联关系。

如果一个 Slice 无法脱离未合并前置实现，应优先采用以下方式重新切分，而不是建立堆叠链：

- 把前置 contract 做成向后兼容、可独立合并的基础 PR，合并后再开始消费方 PR。
- 用 feature flag、兼容 adapter 或暂不接线的声明式入口保证主线始终完整可用。
- 如果拆分会制造不可验证的半成品，则合并为一个目标单一、可完整验证的 PR，并在超过文件数门槛时解释原因。

只有仓库维护者针对紧急发布或不可拆分迁移作出明确书面批准时，才允许例外；例外 PR 必须标明风险、合并顺序和退出堆叠链的计划。普通功能开发、测试补齐、文档治理和重构不适用例外。

如果发现已经开放的堆叠链，立即停止新增下游和逐层传播：先处理最靠近目标分支的 PR，其余下游转 Draft 或冻结；前置 PR 合并后，下一层必须重新以更新后的目标分支为 base，并核对 diff 只包含该 Slice。若历史或 diff 已被累计祖先污染，应关闭旧 PR，从更新后的目标分支新建独立分支，只移植该 Slice 的唯一改动并重新验证。

### Reviewer Checklist

Reviewer Checklist 的唯一 Canonical Source 是 `AGENTS.md`。本文只定义协作背景和 PR 拆分原则；review 时以 `AGENTS.md`「Reviewer Checklist」为准。

## 模块 Owner 与 Review Gate

Owner 是职责角色，不绑定具体人名。一个 PR 可以由多人实现，但触碰 Protected Modules 时必须让对应 Owner 角色参与 review。

### Owner Model

| Owner 角色 | 负责范围 | 主要把关点 |
|---|---|---|
| Core Architecture Owner | `core/contracts.py` 兼容门面、`core/contracts_*` 契约组、`core/event_bus.py`、`core/module_registry.py`、`core/module_registry_lifecycle.py`、`core/module_registry_snapshot.py`、跨模块契约 | 统一事件模型、模块生命周期、模块失败隔离、模块状态投影、扩展边界、兼容性 |
| Event Layer Owner | `modules/bili_live_ingest/**`、B 站协议解析、直播间查询、事件归一化 | 真实直播稳定性、协议变更、风控降级、LiveEvent 输入质量 |
| Selection Owner | `modules/live_events/**`、窗口择优、事件竞争策略 | “猫只有一张嘴”的选择权、冷却窗口、评分权重、避免重复输出 |
| Pipeline Owner | `core/pipeline.py`、`core/pipeline_flow.py`、`core/pipeline_routing.py`、`core/pipeline_viewers.py`、`core/pipeline_requests.py`、`core/pipeline_session.py`、`core/pipeline_results.py`、`core/pipeline_skip_results.py`、`core/pipeline_dispatch_results.py`、`core/pipeline_failure_results.py`、`core/safety_guard.py`、`core/safety_guard_cooldown.py`、`core/safety_guard_failures.py`、`core/safety_guard_types.py`、`adapters/neko_dispatcher.py`、输出边界 | 安全门、限流、dry-run、唯一出口、NEKO 输出语义、事件 route 语义、viewer/profile 准备、request 构造、session gate、result / audit 口径 |
| Output Contract Owner | `core/live_reply_contract.py`、`core/live_reply_policy.py`、`core/live_output_policy.py`、`core/live_output_quality.py`、`core/live_output_shape.py`、`core/live_output_memory.py`、`core/live_output_contract_prompt.py`、`core/meme_knowledge.py`、`data/meme_knowledge.json`、`adapters/output_contract_bridge.py` | 插件侧短句合约、质量兜底、recent-output 负例、热梗检索提示、metadata 桥接、宿主核心冻结边界 |
| Runtime Owner | `core/runtime.py`、`core/runtime_state.py`、`core/runtime_modules.py`、`core/runtime_*_api.py`、`core/runtime_live_status_helpers.py`、插件 action、hosted-ui context、对外入口兼容 | 启停、权限门、运行态一致性、模块装配顺序、旧 action/helper API 兼容 |
| Runtime Controls Owner | `core/runtime_live_controls.py`、直播间连接 action、控制面板队列/暂停/清理动作 | 控制动作语义、连接快照、安全门同步、观众档案清理边界 |
| Active Engagement Flow Owner | `core/runtime_active_engagement_api.py`、`core/runtime_active_engagement.py`、`core/active_topic_sources.py`、`core/active_topic_live_thread_source.py`、`core/active_topic_recent_source.py`、`core/active_topic_trending_source.py`、`core/active_topic_selector.py`、`core/active_topic_compat.py`、`core/active_topic_selection.py`、`core/active_topic_candidate_picker.py`、`core/active_topic_builder.py`、`core/active_topic_rules.py`、`core/active_topic_meaning.py`、`core/active_topic_safety.py`、`core/active_topic_filters.py`、`core/active_topic_mentions.py`、`core/active_topic_rotation.py`、`core/active_topic_materials.py`、`core/active_topic_material_family.py`、`core/active_topic_material_profile.py`、`core/active_topic_pack.py`、`core/active_topic_shapes.py`、`core/active_engagement_copy.py`、`core/live_hosting_director.py`、`core/live_hosting_gates.py`、`core/live_hosting_events.py`、`core/live_hosting_beats.py`、`core/live_hosting_beat_rules.py`、`core/live_hosting_beat_picker.py`、`core/live_hosting_beat_state.py`、`core/live_hosting_loop.py`、`core/live_content.py`、`core/live_content_materials.py`、`core/live_content_active_catalog.py`, `core/live_content_active_catalog_*.py`、`core/live_content_active_materials.py`、`core/live_content_host_catalog.py`, `core/live_content_host_catalog_*.py`、`core/live_content_host_materials.py`、`data/idle_hosting_beats.json` 主动营业/陪播素材 | runtime 兼容入口、主动营业触发 gate、候选来源收集、topic 选择、内容过滤、mention 归属、素材画像、shape 文案和复盘字段 |
| Live Status Owner | `core/live_status.py`、`core/live_status_core.py`、`core/live_status_timing.py`、`core/live_status_idle.py`、`core/live_status_active.py`、`core/live_status_director.py`、`core/live_status_readiness.py` | 直播状态机、activity-level pacing 阈值、idle/active eligibility、director 下一动作、测试准备度和 speech explanation 投影 |
| Recent Context Owner | `core/recent_context.py`、`core/recent_context_routes.py`、`core/recent_output_families.py` | recent prompt 记忆、route / signal 归一化、spent-output family 复读记账 |
| Runtime Config Owner | `core/runtime_config.py`、`core/runtime_config_activation.py`、`core/runtime_config_persistence.py`、`core/runtime_live_listener.py` | 内存先行配置契约、持久化预算兜底、连接态同步 |
| Instruction Context Owner | `core/runtime_instructions.py`、`core/instructions.py`、`adapters/neko_dispatcher.py` 语境入口 | 直播/开发者语境注入顺序、恢复边界、唯一 dispatcher 出口 |
| Live Input Owner | `core/runtime_live_input.py`、`modules/bili_live_ingest/**` 归一化入口、result projection | 直播 payload 进入 pipeline、support-event metadata、monitor metadata、安全脱敏 |
| Developer Tools Owner | `core/runtime_developer_tools.py`、`modules/developer_sandbox`、Hosted UI developer entries | 后端权限检查、沙盒记录隔离、调试 lookup / 手动事件边界 |
| Dashboard Projection Owner | `core/runtime_dashboard.py`、`core/runtime_health.py`、`core/runtime_dashboard_actions.py` | 只读状态投影、Runtime Health Rows、dashboard action 列表、面板字段兼容、隐私安全展示 |
| Dashboard/UI Owner | `ui/panel_compat.tsx`、`ui/panel.tsx`、`ui/panel_components.tsx`、`ui/panel_state.ts`、`ui/panel_helpers.ts`、`config_schema()` 渲染、i18n 文案、面板信息架构 | UI 外壳稳定、模块卡隔离、状态契约、8 locale 同步、Hosted UI 约束；`panel_compat.tsx` 是主分支插件中心可直接加载的完整功能单文件入口，不得退化成最小 fallback 壳 |
| Stores/Privacy Owner | `stores/viewer_store.py`、`stores/audit_store.py`、`stores/credential_store.py`、用户数据边界 | 隐私、凭据、审计、持久化、数据最小化 |
| Documentation Owner | `docs/**`、`AGENTS.md`、onboarding、模块文档、PR 规范 | Canonical Source、协作规则、文档路由、新人入口 |

### Protected Modules

Protected Modules 是需要核心维护者 review 的高风险区域。触碰这些区域时，PR 描述必须说明风险、验证方式和回滚 / 降级方式。

- Core architecture：`core/contracts.py` 兼容门面、`core/contracts_*` 契约组、`core/event_bus.py`、`core/module_registry.py`、`core/module_registry_lifecycle.py`、`core/module_registry_snapshot.py`。
- Event layer：`modules/bili_live_ingest/**`、直播协议解析、LiveEvent schema 或事件归一化。
- Selection：`modules/live_events/**`、`get_score` 权重、冷却窗口、事件竞争策略。
- Pipeline / output：`core/pipeline.py`、`core/pipeline_flow.py`、`core/pipeline_routing.py`、`core/pipeline_viewers.py`、`core/pipeline_requests.py`、`core/pipeline_session.py`、`core/pipeline_results.py`、`core/pipeline_skip_results.py`、`core/pipeline_dispatch_results.py`、`core/pipeline_failure_results.py`、`core/safety_guard.py`、`core/safety_guard_cooldown.py`、`core/safety_guard_failures.py`、`core/safety_guard_types.py`、`adapters/neko_dispatcher.py`、`core/live_reply_contract.py`、`core/live_reply_policy.py`、`core/live_output_policy.py`、`core/live_output_quality.py`、`core/live_output_shape.py`、`core/live_output_memory.py`、`core/live_output_contract_prompt.py`、`core/meme_knowledge.py`、`data/meme_knowledge.json`、`adapters/output_contract_bridge.py`。
- Runtime：`core/runtime.py`、`core/runtime_state.py`、`core/runtime_modules.py`、`core/runtime_*_api.py`、`core/runtime_live_status_helpers.py`、插件 action、hosted-ui context、对外入口兼容。
- Runtime controls：`core/runtime_live_controls.py`、直播间连接 action、控制面板暂停/恢复/清理。
- Active engagement flow：`core/runtime_active_engagement_api.py`、`core/runtime_active_engagement.py`、`core/active_topic_sources.py`、`core/active_topic_live_thread_source.py`、`core/active_topic_recent_source.py`、`core/active_topic_trending_source.py`、`core/active_topic_selector.py`、`core/active_topic_compat.py`、`core/active_topic_selection.py`、`core/active_topic_candidate_picker.py`、`core/active_topic_builder.py`、`core/active_topic_rules.py`、`core/active_topic_meaning.py`、`core/active_topic_safety.py`、`core/active_topic_filters.py`、`core/active_topic_mentions.py`、`core/active_topic_rotation.py`、`core/active_topic_materials.py`、`core/active_topic_material_family.py`、`core/active_topic_material_profile.py`、`core/active_topic_pack.py`、`core/active_topic_shapes.py`、`core/active_engagement_copy.py`、`core/live_hosting_director.py`、`core/live_hosting_gates.py`、`core/live_hosting_events.py`、`core/live_hosting_beats.py`、`core/live_hosting_beat_rules.py`、`core/live_hosting_beat_picker.py`、`core/live_hosting_beat_state.py`、`core/live_hosting_loop.py`、`core/live_content.py`、`core/live_content_materials.py`、`core/live_content_active_catalog.py`, `core/live_content_active_catalog_*.py`、`core/live_content_active_materials.py`、`core/live_content_host_catalog.py`, `core/live_content_host_catalog_*.py`、`core/live_content_host_materials.py`、`data/idle_hosting_beats.json`。
- Live status：`core/live_status.py`、`core/live_status_core.py`、`core/live_status_timing.py`、`core/live_status_idle.py`、`core/live_status_active.py`、`core/live_status_director.py`、`core/live_status_readiness.py`。
- Recent context：`core/recent_context.py`、`core/recent_context_routes.py`、`core/recent_output_families.py`。
- Runtime config：`core/runtime_config.py`、`core/runtime_config_activation.py`、`core/runtime_config_persistence.py`、`core/runtime_live_listener.py`。
- Instruction context：`core/runtime_instructions.py`、`core/instructions.py`、语境注入 / 恢复。
- Live input：`core/runtime_live_input.py`、直播 payload 归一化、support-event metadata、result projection。
- Developer tools：`core/runtime_developer_tools.py`、`modules/developer_sandbox`、沙盒记录清理。
- Dashboard projection：`core/runtime_dashboard.py`、`core/runtime_health.py`、`core/runtime_dashboard_actions.py`。
- Stores / privacy：`stores/viewer_store.py`、`stores/audit_store.py`、`stores/credential_store.py`。
- Dashboard shell：`ui/panel.tsx` 的导航外壳与跨页面编排；`ui/panel_components.tsx` 的展示组件；`ui/panel_state.ts` 的面板状态契约与默认值；`ui/panel_helpers.ts` 的标签/格式化 helper。`ui/panel_compat.tsx` 是 manifest 使用的完整功能单文件入口，用于兼容主分支 / 旧插件中心不支持 Hosted TSX 相对依赖的环境；它应由模块化源码内联生成，保留完整面板能力，允许 `@neko/plugin-ui` import / hooks，但不得包含相对 import、`window.NekoUiKit` 或 `__modules` linker 包装。

### Boundary Guard Tests

`tests/test_module_boundaries.py` 与其它契约、注册表、输出合约和 smoke 测试共同守住模块化和宿主核心冻结边界；它们不验证直播效果，而是验证架构边界：

- 插件源码不得导入 `main_logic`，直播输出合约只通过 `adapters/output_contract_bridge.py` 暴露 metadata。
- `core/contracts.py` 必须保持兼容 re-export 门面；配置、事件、观众、交互结果、安全状态实现分别放在 `core/contracts_config.py`、`core/contracts_events.py`、`core/contracts_viewer.py`、`core/contracts_interaction.py`、`core/contracts_safety.py`，共享类型/时间 helper 放在 `core/contracts_types.py`。
- `core/pipeline_routing.py` 必须保持纯路由规则，不触碰 dispatcher、viewer store、safety guard、request/result 或 result 记录。
- `core/pipeline_viewers.py` 和直播 runtime 调用点必须保持平台中立，只能通过 `live_provider_router` 解析直播 provider / identity provider，不得直接依赖 B 站或抖音 ingest / identity 富模型。
- `core/pipeline_results.py` 只负责 `InteractionResult` 构造、audit / `record_result` 口径和安全失败记账，不做 route、不调 dispatcher、不查 viewer store。
- `core/live_content_*catalog*.py` must stay as static material data or compatibility aggregates: no runtime / pipeline / dispatcher / live status reads; aggregate files must preserve original key order to avoid material-rotation drift.
- `core/live_hosting_beats.py` 只做 host beat 选择和近期素材避让，不创建 `ViewerEvent`、不记录 `InteractionResult`、不触发 pipeline。
- `core/live_hosting_events.py` 只做 warmup/idle hosting 事件和 gate skip result，不选择 host beat、不触发 pipeline、不管理 loop。
- `core/live_hosting_loop.py` only owns loop start/stop/cancel and trigger ordering; it must not construct events, select beats, build requests, dispatch outputs, or record results.
- `adapters/output_contract_bridge.py` 不得引用宿主最终输出函数；插件只提供 `neko_roast` 私有策略 metadata，宿主核心只负责透明传输。

修改 Protected Modules、拆分模块或调整分发边界时，至少跑：

```powershell
uv run pytest plugin/plugins/neko_roast/tests/test_module_registry.py plugin/plugins/neko_roast/tests/test_config_contracts.py plugin/plugins/neko_roast/tests/test_output_contract.py plugin/plugins/neko_roast/tests/test_smoke.py -q
```

如果触碰直播链路或素材选择，还要跑对应定向测试和全量插件测试。

### Distribution Guard Tests

`tests/test_distribution_boundaries.py` 与 Git index、ignore 规则和插件检查器共同验证插件可分发边界，确认本地运行态产物不会进入插件包或 PR：

- `plugin/plugins/*/plugin.toml.lock` 是插件运行态锁文件，不提交、不打包。
- `.codex-live-screen.png` 是本地直播 / UI 验证截图，不提交、不打包。
- `plugin/plugins/neko_roast/plugin.toml.lock` 当前不得出现在 git index 中。

修改 `.gitignore`、插件分发脚本、插件 manifest 或本地运行态文件路径时，至少运行：

```powershell
git ls-files --error-unmatch plugin/plugins/neko_roast/plugin.toml.lock .codex-live-screen.png
git check-ignore -v plugin/plugins/neko_roast/plugin.toml.lock .codex-live-screen.png
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```

第一条命令预期以非零状态退出，表示运行态文件没有进入 Git index；第二条必须列出匹配的 ignore 规则。

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

- `bili_live_ingest`：归一化直播弹幕事件、提供直播间状态查询（带反 -352 + 友好降级，见「直播间查询与 -352 风控」），并**持有真实弹幕监听器**——吞并自 `bilibili_danmaku` 的 `DanmakuListener`（同目录 `danmaku_core.py` + `livedanmaku.py`：WS 连接 + WBI 签名 + 临时 buvid3 反 -352 + zlib/brotli 解压 + 心跳 + 多服务器故障转移 + 断线重连）。`start_listening` 只有收到 AUTH_REPLY `code=0` 后才返回成功；lifecycle lock + generation 防止快速 start/stop 后旧任务复活，成功认证会重置重试预算，terminal task 会清理引用，迟到 callback 必须按 generation 和当前 platform/provider/room ownership 丢弃。`runtime.connect/disconnect_live_room` 启停监听；`stop_listening` 用 `wait_for` 给 ws close 加超时，避免关闭握手拖慢断开。**富模型 `on_event` 回调把 `LiveDanmaku` 包成 `LiveEvent` 发布到 `event_bus`**（按命令名映射 `type`），由订阅者按类型消费（轻量 `on_danmaku`→pipeline 直连已退役，防同一条弹幕双锐评）。见「直播事件中枢（EventBus）」。登录态（若有）传入 `DanmakuListener` 与 lookup（见「B站登录态」）。弹幕本身不含头像，头像由下游 `bili_identity` 按 UID 抓取。该模块当前是只读 ingest：不保留 `send_danmaku`、`msg/send`、csrf 写入 payload 或弹幕长度裁剪配置；未来发弹幕、评论、动态、私信等写能力只能进入 `bili_write_tools`，并单独做权限、登录态和安全评审。
- `bili_identity`：解析 UID、昵称、头像 URL；缺少昵称或头像时按 UID 查询 B 站基础资料，并尝试抓取头像供本次 NEKO 视觉输入使用。同时解析头像形态 META：是否默认头像（noface）、是否动图（大会员动态头像，只取代表帧）、挂件/装扮名（出框头像来源）；抓取或识别失败时安全降级（`avatar_vision_ok=False`），不阻断锐评。
- `douyin_live_ingest`：抖音只读 live provider。当前负责房间 URL/token 解析、cookie-gated 页面元数据读取、内置 `douyinLive` 本地 bridge 启停、bridge 事件清洗与 `LiveEvent` 发布；页面元数据 fetch timeout 必须有默认值和上限，非法或过大的 timeout 不得原样传给 `urlopen`；页面元数据公开投影只接受标量字段，不把嵌套 HTML/JSON 结构字符串化，且候选必须带数字 webcast room id，否则不投影 title / anchor；metadata / lookup status 的 title、anchor、message 必须脱敏并截断，live_status 只能是归一化枚举；页面元数据、lookup status 和 bridge connection plan 的公开 `room_ref` 也必须先走 room parser，不能回显原始输入；公开 `webcast_room_id` 只接受数字，无法安全归一时按缺失降级；v1 不保留插件侧 `webcast/im/fetch`、protobuf、ack、heartbeat、直连 WebSocket 或 JS 签名执行，避免把不稳定直连链路带进运行时；bridge connection plan 只暴露 `ready`、安全 `room_ref`、空 `endpoint`、空 `params`、安全 `missing` 标识（仅 `bridge_executable` / `bridge_runtime`）和脱敏 `message`，不得展示 localhost 端口、bridge URL、signature / token / cookie；bridge 解出的事件必须先包装为 `DouyinTransportEvent` 并调用 `publish_transport_event()`，继续经 `event_model.safe_payload()` 清洗后才能发布到 EventBus；事件类型归一、UID 安全形态过滤与平台前缀、礼物摘要字段、安全 payload 白名单和值级标量化放在同目录 `event_model.py`，避免 bridge 污染监听生命周期或把嵌套 raw/protobuf 结构塞进 `ViewerEvent.raw`；payload 自带 `uid` / `room_ref` / `room_id` / `webcast_room_id` / `avatar_url` 进入 `LiveEvent` / `ViewerEvent.raw` 前也必须重新清洗，UID 仅允许短安全标识形态，无法安全归一时清空；允许的昵称、文本、礼物名、事件标签、bridge 状态消息和重连原因等标量文本字段也必须脱敏并截断，不能因为字段名在白名单中就回显 cookie / token / signature 形态值；发布到 EventBus 前必须能归属到安全 `room_ref`，否则丢弃并暴露脱敏错误；事件级 `room_id` / `webcast_room_id` 只接受纯数字，无法安全归一时不投影；未知事件类型统一归一为 `unknown`，不发布到 EventBus，也不得在 status 中回显原始事件类型文本；`room_ref` 无法安全归一时回退到模块当前房间目标或清空；`avatar_url` 只做无网络成本的字符串级安全校验，拒绝 data/base64、非 HTTP(S)、localhost、本地 IP 字面量和 URL userinfo，并且公开投影只保留 scheme/host/path，不保留 params/query/fragment；`status()` / `listener_state()` 作为公开出口必须再次清洗内部 `state`、`room_ref`、`last_error`、bridge connection plan、reconnect、retry policy、计数、时间戳和事件类型字段，`state` 只能投影为已知生命周期标签或 `unknown`，`listening` 等派生生命周期布尔值也必须基于已清洗 state 计算，公开数值字段必须是非负有限值，不能信任 bridge 内部状态天然安全；gift 仅透传脱敏摘要字段并记录为 signal-only skipped result，不触发 AI；status-only 事件只刷新 `last_status_only_event_type` / `status_only_count`，不发布到 EventBus；连接失败或协议漂移会显式进入脱敏错误状态，并由 `listener_state()` / connection snapshot 只暴露脱敏后的 `last_error`、`connection_plan` 和重连状态。
- `runtime_config.update_config()` 持久化 Douyin `live_room_ref` 前必须通过 `live_provider_router.normalize_room_ref_for_platform()` 归一化，避免 Hosted UI 直接保存原始 URL query 或旧配置残留；从其他平台切到 Douyin 且没有显式传入新 `live_room_ref` 时必须清空目标，不能把 B 站旧房号继承成抖音 token。
- `live_connection_snapshot()` 必须把 provider `listener_state()` 当成不可信公开输入二次过滤：`state` 只能是已知连接状态，`viewer_count` 只能是非负整数，`last_error` 只能是字符串，`connection_plan` / `reconnect` 只能是 dict；object / bytes / container 不得被字符串化。`live_status_summary()` 的公开 `room_ref` / `room_id` 必须优先使用 `live_connection_snapshot()` 已清洗的连接目标；只有快照缺失时才回退 config，且 fallback 只接受字符串或正整数，避免 Douyin URL query、旧配置残留或伪对象绕过 provider router。
- `douyin_live_ingest` metadata fetch 的手动 cookie 只能在拒绝 CR/LF 后作为 `Cookie` 请求头传给 `urlopen`；多行或 header-injection 形态必须丢弃，不能原样进入请求头。
- `modules/live_bridge`：跨平台本地弹幕桥接契约。它只负责连接 localhost WebSocket、解析 JSON、调用 provider adapter，并把结果交回 provider 自己的安全 payload 边界；不得知道抖音协议细节、不得连接公网 bridge URL、不得保存 credential/raw payload。抖音当前通过 `douyin_live_ingest.embedded_bridge` 托管内置 `vendor/douyin_bridge/windows-amd64/douyinLive.exe`，再由 `douyin_live_ingest.external_bridge` + `DouyinLiveBridgeAdapter` 接入 `jwwsjlm/douyinLive` 风格本地 WS；已建立的连接异常断开后只允许 3 次、1/2/4 秒退避重连，初次连接失败仍立即 fail-closed，stop 必须取消唯一 transport task。如果该 bridge 失效，应替换内置 bridge 和/或 adapter/wrapper，不能改 EventBus、viewer profile、互动模块或下游 pipeline。托管进程必须只绑定 localhost、丢弃 stdout/stderr、不透传非字符串 cookie、不把 raw bridge payload 写入 audit/config/UI/profile；Windows 下新 supervisor 启动前允许清理同一 bundled executable path 的旧 `douyinLive.exe` 进程，避免热重载留下孤儿 bridge；只有确认进程已终止后才能删除 ownership marker。
- `douyin_identity`：只把 `douyin_live_ingest` 已清洗的 UID、昵称、头像 URL 转成 `ViewerIdentity`，并在生成 `source_url` / `name` / `nickname` / `avatar_url` 前再次复用 UID、文本和头像 URL 安全过滤，保证 UID 带 `douyin:` 前缀且不把 cookie/token 形态文本写入 profile；头像 URL 也只保留同一套字符串级安全 URL；v1 不抓用户主页、不下载头像、不读取 cookie/raw payload。
- `runtime_douyin_auth`：抖音手动 cookie action。cookie 只进 `douyin` namespace 的 `CredentialStore` 加密落盘；导入 cookie 参数必须是字符串，object / bytes / container 不得被字符串化后保存；导入时允许首行 `Cookie:` 和普通 name=value 续行，但混入 `X-...:` 等非 Cookie header 行必须拒绝，不能保存成凭据；非法 cookie action 必须返回结构化 `saved=False` 结果并记录脱敏 audit，不能把原始 cookie 或异常栈冒泡到 UI；public status / audit 只暴露 `has_cookie`、脱敏后的 `uid` / `nickname` / `saved_at`，其中 `uid` 还必须是可选 `douyin:` 前缀的短安全标识形态，并把误粘到公开字段里的 cookie/token/signature/sign/webcast_sign/跨平台凭据形态文本清空；cookie 有效性只在用户手动触发校验时读取当前房间元数据，结果只暴露 `valid`、脱敏 `room_ref`、`live_status` 和脱敏 `message`，不做后台轮询、网页登录、二维码/手机号登录或浏览器自动化。
- `viewer_profile`：维护观众档案和首次触发判断。
- `avatar_roast`：构造首次出场的头像 / ID / 第一句话锐评请求，并集中产出完整锐评指令（见“输出边界”的自适应焦点规则）。首评 prompt 也必须接入 recent used-material blocklist，避免长直播中不同观众反复听到同一套开头、奖励梗、主持节拍或头像模板。
- `danmaku_response`：构造同一观众后续普通弹幕的接话请求。它不做头像 / ID 首评，不写首评计数，不绕过 pipeline / safety guard / dispatcher；用于让 Independent Mode 下的持续对话不被 `roast_once_per_uid` 整体挡掉。
- `live_support_events`：构造 Gift / SC / Guard 被 `live_events` 选中后的短句致谢请求。它不走头像首评、不把礼物当普通弹幕、不主动索要更多支持，也不绕过 pipeline / safety guard / dispatcher；request metadata 暴露 `support_event_type` / `support_event_tier` / `support_event_label` 供 dry-run、monitor、dashboard 复盘。
- `active_engagement`：构造猫猫独播安静状态下的一次主动营业请求。当前 v1 支持保守自动触发和手动触发，不接 Gift / SC / Guard；它必须继续经过 pipeline / safety guard / dispatcher，用于后续统一直播验证猫猫能否自然抛出一个观众愿意接的话题。v1 会给请求附带轻量 `topic_material`，优先把 6 分钟内有信息量且已经成功输出或 dry-run 到 dispatcher 的近期直播间弹幕聚合成 `live_thread` 共同话题（含 `interest` / `relevance` / `risk` / `evidence`），没有共同线程时再复用单条近期弹幕；没有合适弹幕时再回退到 B 站公开推荐素材，最后使用内置小话题。内置 fallback 话题池需要覆盖多种低压力接话形态，并避免在短时间内重复同一个话题；topic material 不能把“没人说话 / 弹幕少 / 冷场 / 突然安静 / nobody is talking / suddenly quiet”这类房间沉默描述当成主动营业素材，也不能把“求推荐 / 有什么推荐 / any recommendations”这类让观众替 NEKO 选题的内容，或“今晚做什么 / what are we doing”这类开放式选题问句当成主动营业素材；观众直接问 NEKO 的问题、“猫猫你觉得...”这类直接征询 NEKO 看法的弹幕、以及“猫猫讲讲 / 说说 / 聊聊 / 评价一下 / 锐评一下 / 帮我 / 给我 / 能不能 / 可不可以 / 要不要...”和“谢谢 / 感谢 / 辛苦了”这类中文点名请求或感谢，以及 `NEKO help me / give me / rate my / tell me / can you / could you / please / pls / thank you / thanks...` 这类英文点名请求或感谢也不再作为主动营业素材；未点名但明显是“讲讲 / 说说 / tell me / recommend me”式请求，“哈哈 / 笑死 / lol”式纯反应弹幕，以及“状态 / 下一步 / 重启 / 延迟 / 回复太长”式测试或运行反馈，也应由 `danmaku_response` 接住或进入测试复盘，不作为主动营业二次开题素材，避免已经由 `danmaku_response` 接过的话题被二次开场，并分别通过 `topic_recent_skip_reason=filtered_direct_request` / `filtered_reaction` / `filtered_runtime_feedback` 复盘；低信息量但无法归类的近期弹幕仍使用 `filtered_recent_danmaku`；`get the chat moving` / `keep the chat alive` 这类英文主持模板也必须过滤，避免和 Idle Hosting 或观众求助职责混淆；公开推荐素材还会过滤营销、广告、关注转发、抽奖、giveaway / sponsored，以及事故、死亡、灾害、争议、网暴、scandal / controversy 这类沉重或高争议标题，避免猫猫主动营业像在念推广或把直播气氛带偏；外部标题进入 `topic_material` 前会压缩到 40 字以内，避免长标题诱导长篇输出。若当前缓存的外部话题都已经用过，会清一次缓存并尝试重新拉取外部话题，再回退到内置小话题，避免长直播后半段过早耗尽公开素材。首评 `avatar_roast` 的同条弹幕不能再作为主动营业素材，避免首次出场锐评后围绕同一句话二次开题，并通过 `topic_recent_skip_reason=avatar_roast_context` 复盘；被 skipped / failed 的弹幕不能被主动营业放大，并通过 `topic_recent_skip_reason=non_output_danmaku` 复盘；若短窗口内有效 recent danmaku 全部来自同一 UID 且达到 3 条，主动营业不再拿这位观众的弹幕继续开题，而是回退到公开推荐或内置小话题，避免独播被单个观众刷屏素材带偏，并在 recent result 暴露 `topic_recent_skip_reason=single_viewer_flood` 方便复盘；若只有过期 recent danmaku 被过滤，则暴露 `topic_recent_skip_reason=stale_recent_danmaku`。prompt 会把 `topic_shape` 展开为 `shape task`、`example pattern` 和 `viewer reply path`，只约束接话结构，不硬编码猫猫台词，也禁止让观众替 NEKO 决定“想听什么 / 聊什么”。recent result 会暴露 `topic_source` / `topic_shape` / `topic_title` / `topic_key` / `topic_hook` / `topic_pattern` / `topic_intent` / `topic_reply_affordance`，监控脚本还会根据最近结果输出 `latest_topic_repeat`，便于复盘主动营业为什么说这一句、想让观众怎么接、是否复用了同一个素材。
- 主动话题必须显式标记隐私来源：`fallback` / `bili_trending` 属于公开素材，可在 recent result、timeline、audit 与 monitor 中保留 title/key/hook；`recent_danmaku` / `live_thread` 属于 `viewer_derived`，公开投影必须移除 title/key/hook/evidence/interest/keywords，只保留非内容型分类字段。未知 source 即使自称 public 也 fail-private。内部 prompt 仍可使用当前请求所需的短素材，但不得把它变成持久化或监控公开字段。
- `topic_fun_axis` 也是主动营业 recent result / monitor 字段，用来说明该话题的趣味轴是 choice、tease、mood、micro_challenge 还是 viewer_callback；复盘主动营业是否单调时应和 `topic_shape`、`topic_intent`、`topic_reply_affordance` 一起看。
- `active_engagement` v1.5 补充约束：如果最近两次主动营业都来自 `recent_danmaku`，下一次不继续拿近期弹幕开题，改回 B 站公开素材或内置小话题，并通过 `topic_recent_skip_reason=recent_danmaku_source_streak` 复盘；弹幕中的 `@其他观众` 视为 viewer-to-viewer mention，不作为主动营业素材，`@猫猫` / `@NEKO` 等明确喊 NEKO 的情况仍交给普通弹幕接话路径；当连续出现同一 `topic_shape` / `topic_intent` 时，主动营业会切换到其他形态，并在 recent result 暴露 `shape_guard_reason=recent_shape_streak`，方便复盘猫猫是否又在用同一种话题姿势复读。若 shape guard 改变了形态，传给 prompt 的 `hint` 也必须跟随新形态重写，避免监控显示已经换形态但 prompt 仍残留旧的 A/B choice 等矛盾指令。主动营业素材去重不能只看 `topic_key` / `bvid`：如果候选标题和最近用过的 topic title 高度相似，即使 key 不同也应跳过，并通过 `topic_recent_skip_reason=similar_topic_title` 复盘，避免热搜或内置素材换壳后让猫猫围绕同一个话题反复开场。
- `active_engagement` v1.15 补充约束：近期弹幕和 B 站公开素材进入主动营业前会做轻量 topic profile。标题若明显像“二选一 / 还是 / 哪边 / choice”，优先转成 `either_or + choice`；像“挑战 / 任务 / 打分 / 假装正经”，优先转成 `small_challenge + micro_challenge`；像“打盹 / 吐槽 / 离谱 / 奇怪 / 硬撑”，优先转成 `tiny_tease + tease`；像“气氛 / 电台 / 温度 / 状态”，优先转成 `light_stance + mood`。profile 只决定互动结构和接话路径，不硬编码台词；没有明显 profile 的素材仍走既有 shape 轮换。目标是让热榜或近期弹幕不只是“一个标题”，而是带着 `preferred_shape`、`fun_axis`、`reply_affordance` 和 hint 进入 prompt，减少主动营业泛问和模板主持感。
- `warmup_hosting`：构造猫猫独播刚开始、尚无近期互动时的一次开场暖场请求。它与 `idle_hosting` 分开，避免开播第一句话听起来像冷场补位；同样必须经过 pipeline / safety guard / dispatcher，并接入 recent used-material blocklist，避免长直播或重启后重复旧开场、旧主持节拍。
- `live_director_status`：面板状态聚合，不新增输出路径；它只解释下一次自动开口会是 `none` / `warmup_hosting` / `active_engagement` / `idle_hosting`、当前是否 eligible、以及还要等多久，方便统一直播测试时判断猫猫为什么不说话。
- `solo_test_readiness`: dashboard-only streamer readiness aggregation for solo-stream validation. It summarizes preflight, test isolation, warmup hosting, first-viewer roast, follow-up danmaku reply, active engagement, idle hosting, and pacing control; it does not add a new output path, bypass safety, or replace runtime status.
- `developer_sandbox`：提供离线 UID / URL 调试入口。
- `live_events`：直播事件中枢（P2.5）。经 `event_bus` **订阅 `"danmaku"` / `"gift"` / `"super_chat"` / `"guard"` 事件**，解包信封 `raw` 取富模型 `LiveDanmaku`，冷却期缓冲候选互动、按 `get_score()` 打分，冷却结束择优（舰长/总督/SC、礼物、粉丝牌、用户等级、长文本优先）取分最高者投 `pipeline`；空闲态首条仍即时锐评。`modules/live_events/room_topic.py` 是中枢私有协作组件，只维护短期弹幕主题、低质过滤、回复技巧和运行期观众提示，供 `avatar_roast` / `danmaku_response` 的 prompt builder 读取；它不进 registry、不另开输出队列、不写长期档案。Gift / SC / Guard 被中枢选中后由 `pipeline_routing` 转到 `live_support_events`，不得误走 `avatar_roast` 或 `danmaku_response`。详见下文「直播事件中枢」。

预留模块：

- `bili_dm_ingest`：未来接入 B 站私信。
- `contribution_rank`：未来接入贡献值。
- `watch_time`：未来接入进房累计和停留时长。
- `bili_read_tools`：未来接入用户资料、投稿、收藏等读取能力。
- `bili_write_tools`：未来接入发弹幕、评论、动态、私信等写入能力。
- `automation_ops`：未来接入浏览器、键鼠和公开资料工作流。

其它核心组件（非 `InteractionModule`，但同属插件骨架）：


Catalog split note: `data/idle_hosting_beats.json` is the maintained idle-hosting material catalog; `live_content_host_catalog_*` are fallback material group modules. `live_content_active_catalog_*` are active material group modules; the unsuffixed catalog modules are compatibility aggregates only.
- `adapters/`：`neko_dispatcher`（**唯一 NEKO 输出边界** + 头像压缩 + dry_run 短路）、`bili_auth_service`（扫码登录，移植自旧插件）。
- `stores/`：`viewer_store`（**唯一档案写**，本机 JSON `viewer_profiles.json`、目录可配置、加锁防丢更新；读旧档案、upsert、mark_roasted 都必须清洗成 JSON-safe 脱敏公开字段）、`audit_store`（**唯一审计**，写入时统一做 JSON-safe / 脱敏公开投影，非字符串对象和 bytes 不得字符串化成 UI 字段）、`avatar_cache`、`credential_store`（Fernet 加密登录凭据）。

## Pipeline

固定数据流：

```text
ViewerEvent
  -> safety_guard.before_event()
  -> live_provider.resolve_identity()
  -> viewer_profile.upsert() / 沙盒临时 profile
  -> viewer_gate.check_once_per_uid()
  -> avatar_roast.build_request()          first appearance
     or danmaku_response.build_request()   repeat live danmaku
     or live_support_events.build_request() Gift / SC / Guard support event
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
- UID / B 站空间链接 lookup 仍是 B 站专用开发工具；模拟弹幕和内置案例进入 pipeline 后会使用当前 `live_provider` 的身份解析器。
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
- `core/runtime.py` 对外保留 `sync_developer_mode()`、`handle_sandbox_target()`、`clear_sandbox_data()` 等 action API。
- `core/runtime_instructions.py` 负责调试语境注入 / 恢复、直播语境注入 / 恢复和开发者模式播报。
- `core/runtime_developer_tools.py` 负责后端沙盒权限检查、lookup / roast / 手动事件入口和沙盒记录清理；关闭开发者模式时必须拒绝这些入口，不能只依赖前端禁用。
- `adapters/neko_dispatcher.py` 是唯一可以发送调试语境和调试播报的 NEKO 输出边界。
- `ui/panel.tsx` 只负责显示总控开关和禁用按钮；业务权限必须以后端检查为准。

测试要求：

- 覆盖启动时“直播语境 -> 调试语境”的顺序，且启动不发 `respond` 播报。
- 覆盖面板手动开启时播报一次，关闭时只恢复调试语境。
- 覆盖开发者模式关闭时后端沙盒入口不会进入 pipeline，也不会 push 给 NEKO。
- 覆盖 8 个 locale 都有 `panel.fields.developerMode` 和 `panel.dev.developerModeDisabled`，且不再使用旧 `chatDevTools` / `sandboxDebug` 文案。

## 安全测试态（dry-run）

`dry_run`（`RoastConfig` 字段，可经 `update_config` 动作切换）是接真实直播间前的安全测试开关，**产品默认关闭**：普通用户不需要理解该术语，连接后即按其它安全门正常工作。开发者、试播人员和压力工具需要无声验链时必须主动开启；压力工具自身仍默认 dry-run，且不得自动修改产品默认值。开启时整条 pipeline 照常跑——安全门、当前 `live_provider` 身份解析、头像/清洗字段处理、`avatar_roast` 锐评 prompt 构造都会执行——但 `neko_dispatcher.push_roast()` 在真正 `push_message` 之前短路，返回 `dry_run(target=..., image_part_bytes=..., text_len=...)` 摘要，**绝不投递给猫猫**。`build_request()` 把 `ctx.config.dry_run` 写进 `InteractionRequest.dry_run`，dispatcher 据此判断。

## 配置持久化与写竞争

`runtime.update_config` 的契约：**内存即时生效、持久化尽力而为**。

host 的 `update_own_config`（把配置写回 `plugin.toml`）在「只重后端不重前端」等场景会被前端的并发配置访问卡满写竞争，偶发（实测下甚至稳定）挂满，触发 host 的 10s entry 超时把整个 action 杀成 500。早期实现先 `await` 持久化再 apply 内存，被这一杀连内存兜底都来不及跑——表现为 `update_config` / `connect_live_room`（其 `set_live_room` 走 `update_config` 持久化 `live_room_id`）/ 开发者模式切换全部点不动。

现在反过来：

1. **先内存生效**：`_activate_config(RoastConfig.from_mapping(...))` 一步把新配置装进 `self.config`（gate / safety_guard 共享同一对象，即时权威）；若改了 `developer_tools_enabled` 顺带 `sync_developer_mode`。
2. **再带预算尽力持久化**：`_persist_config_best_effort` 用 `asyncio.wait_for(self._persist_config_update(clean), timeout=_CONFIG_PERSIST_BUDGET_SECONDS)`（默认 4.0s，远低于 host 的 10s entry 限），超时记 `config_persist_timeout`、失败记 `config_persist_failed`，**都不回滚已生效的内存配置、不阻塞**。
3. **串行化**：`asyncio.Lock`（`_get_config_lock`，懒初始化）覆盖旧配置快照、内存 apply、提示词副作用、持久化和 listener reconcile，避免并发 `update_config` 读取过期状态。平台或房间变化时必须先捕获并停止旧平台的具体 provider 实例，再启动新平台；不能在激活新配置后通过动态 router 反查并误停新 provider。

效果：host 持久化即便卡死，action 也在 ≤4s 内成功返回、runtime 行为已按新配置生效。代价：写竞争时那一次改动**不落盘**（stop/start 后还原成 `plugin.toml` 的值），且每次 `update_config` 等满 4s 预算（无竞争时秒过）。

> 边界：这是**插件侧免疫**；host/core 修复 `Fix plugin host config and data root handling (#1884)` / `08b317f6` 已进入当前 `Roast` 分支，但插件仍保留这层预算兜底，避免未来 host 持久化异常拖垮直播 action。`connect/disconnect_live_room` 另对 `live_enabled` 做内存直设，不依赖持久化即时性。测试：契约 `test_update_config_does_not_block_on_hanging_persistence`、`test_connect_does_not_block_on_hanging_config_persistence`（注入卡死的 `update_own_config`，断言 action 不阻塞、内存生效、记 `config_persist_timeout`）。

> Hosted UI 的局部设置保存必须保持 patch 语义：修改 `rate_limit_seconds`、队列、dry-run、模块开关等非直播目标字段时，不得顺手提交 `live_platform` / `live_room_ref` / `live_room_id` 的表单默认值；否则前端默认值可能把抖音监听目标覆盖回 B 站。契约测试：`test_update_config_preserves_douyin_target_on_partial_rate_limit_update` 与 `test_panel_advanced_save_does_not_resubmit_live_target_defaults`。

## 压力工具安全边界

`tools/live_random_danmaku_pressure.py` 与 `tools/live_silence_pressure.py` 是开发者故障注入工具，不是普通直播入口。两者必须默认 dry-run、默认不连接直播间，并在真实输出前同时要求 `--real-output` 与 `--confirm-real-output`。preflight 遇到 paused、tripped、blocked、已有其它房间监听或连接超时必须 fail-closed，且必须发生在提交测试事件之前。工具不得调用 `resume_roast` 或 `clear_queue`，不得覆盖并发配置；只允许 compare-and-restore 自己仍拥有的值，并且只断开自己建立且仍指向同一房间的连接。

## 房号输入（数字 / 链接）

房号入口统一过 `contracts.parse_room_id(value) -> int`：接受 int、纯数字串、含 `live.bilibili.com/<id>` 的链接（含 `/h5/`、`/blanc/`、query），解析不出返回 0；object / bytes / container 不得被字符串化成房号。让用户直接粘直播间链接，不必手动找房号。

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
- `stores/credential_store.py` `CredentialStore`：命名空间加密 `save`/`load`/`delete`；默认 `bili` namespace 保持旧 `bili_credential.*` 文件名，抖音等新平台使用独立 `{namespace}_credential.*` 文件；`build_credential()` 仍只服务 B 站 `bilibili_api.Credential`，走 `to_thread` 不阻塞。
- `adapters/bili_auth_service.py` `BiliAuthService`（移植自旧插件 `bilibili_danmaku`）：编排 `bilibili_api.login_v2` 扫码状态机，凭据存取由注入的 store 三回调负责。
- `core/runtime.py`：持 `credential_store` + `bili_auth` + 缓存 `bili_credential`（`start()` 时 `reload_credential()` 载入已存凭据），对外保留 `bili_login`/`bili_login_check`/`bili_login_status`/`bili_logout` action API。
- `core/runtime_bili_auth.py`：装配 `CredentialStore` + `BiliAuthService`，实现 runtime 侧登录 action 委托、凭据重载和本地 logout 清理。
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

当前实现同时影响四类决策：`live_state_summary()` 的 `quiet` / `idle` 阈值、`idle_hosting_status()` 的最小陪播间隔、独播首评节流窗口，以及 Idle Hosting prompt 的主持姿态。`quiet` 更偏轻观察、少直接提问；`active` 允许一个具体、低压力的小问题；`standard` 保持中间策略。当前 `standard` 会在约 60 秒无观众活动后进入 `quiet`，约 120 秒后进入 `idle`；独播刚开播且没有任何观众活动时先进入 `warmup`，但若约 45 秒仍无人接话，会自动转入 `idle`，避免猫猫一直卡在开场等待。自动 Active Engagement 在普通弹幕输出后等待约 45 秒，标准档自身最小间隔约 60 秒；`active` 档普通弹幕后等待约 30 秒、自身最小间隔约 45 秒；`quiet` 档仍保持约 210 秒/300 秒的保守节奏。Idle Hosting 标准档最小间隔约 90 秒。独播首评节流窗口为 `quiet=75s`、`standard=45s`、`active=30s`：安静档更少连续头像 / ID 出场锐评，活跃档更快放开新观众出场。`live_state_summary()` 会把观众活动与 NEKO 自己的输出分开统计：`last_viewer_activity_age_sec` 决定 `engaged` / `quiet` / `idle`，`last_output_age_sec` 只用于解释最近是否说过话，避免猫猫自己的主动营业永久阻止冷场陪播。面板会展示最近观众活动间隔、最近输出间隔、多久算安静、多久算冷场，便于主播理解为什么猫猫现在说或不说。

面板的控制台、冷场陪播卡和主动营业卡必须保留这两个节奏字段；主动营业卡还要拆分展示 `minimum_interval_remaining`、`recent_danmaku_cooldown_remaining` 与 `idle_hosting_wait_remaining`，避免只看到一个合并 cooldown 时无法判断到底是自身最小间隔、刚接过弹幕导致等待，还是已经接近冷场窗口而主动让位给 Idle Hosting。当前决策卡也会展示最近主动营业的 `topic_source` / `topic_shape` / `topic_title` / `topic_key` / `topic_hook` / `topic_pattern` / `topic_intent` / `topic_fun_axis` / `topic_reply_affordance`，用于下一次直播复盘话题是否足够具体、是否吸引观众接话，以及是否长期停在同一种趣味轴。

当 `solo_stream` 的观众沉默时间已经接近 `idle_threshold_seconds` 时，自动 Active Engagement 必须让位给 Idle Hosting；当前让位窗口随活跃度变化：`quiet=45s`、`standard=25s`、`active=15s`。这样下一次真实无弹幕窗口不会被主动营业刚好抢掉，直播复盘也能明确区分“主动话题不足”和“冷场陪播没有触发”。

Idle Hosting 不是简单定时器输出。每次 `idle_hosting` 事件会附带一个轻量 `host_beat`，在软观察、小二选一、轻吐槽、小状态等低压力主持节拍之间轮换；选择时优先避开最近使用过的 `host_beat_key`、`host_beat_fun_axis` 和 `host_beat_reply_affordance`，避免连续冷场补位都停在同一种 mood / choice / tease 形态，或反复让观众用同一种方式接话。第一版素材维护入口是 `data/idle_hosting_beats.json`：每条必须包含 `key`、`live_column`、`shape`、`fun_axis`、`title`、`hint`、`reply_affordance`，可选 `idle_stage` 和 `meme_query`；JSON 损坏、缺字段或重复 key 会由 loader 跳过/回退到 legacy Python 素材，不能影响直播输出。`meme_query` 会复用 `data/meme_knowledge.json` 的热梗检索，但只给 `idle_hosting` prompt 一个最多 1 条的可选调味提示，并通过 `meme_hint_ids` / `meme_hint_tags` 进入 request metadata；它不能改变 host beat 的主要方向，也不能让猫猫为了用梗硬改冷场节拍。prompt 只能把 host beat 当方向，最终仍必须生成一句自然的 NEKO 直播补位。`host_beat_key` / `host_beat_shape` / `host_beat_title` / `host_beat_fun_axis` / `host_beat_reply_affordance` 会进入 recent result、recent interaction context 与 历史 monitor 设计（当前切片未分发脚本） 输出，方便下一次冷场补位避免复用同一个开场、包袱形状或主持节拍，也方便判断这句冷场话术是否给了观众一个低门槛接话点。

若 `solo_stream` 进入真冷场后已经连续 2 次由 `idle_hosting` 实际输出或 dry_run，且期间没有新的 `live_danmaku` 观众活动，Live Director 可以把下一次自动动作切到 `active_engagement`（reason=`idle_hosting_streak`）。这是冷场陪播和主动营业之间的交接阀：避免猫猫长时间只做同一种空场补位，同时仍保持 Safety / dry_run / Dispatcher 必经。

当前切片的可执行验收流程是：在 Dashboard 查看健康行和 `live_explain`，在 recent results 核对 route / status / reason 与下述 metadata，再按同一测试时段人工检查后端日志。不要运行 `-BackendLogPath` 或 `-ExpectRealOutput`；这些参数只属于未随当前切片分发的历史 monitor。

直播测试时，历史 monitor 设计（当前切片未分发脚本） 可以通过 `-BackendLogPath <path>` 读取后端日志尾部并输出 `log_watchdog` / `log_contamination` / `log_reply_len` / `log_reply_length_status` / `log_generic_host_prompt` / `log_reply_repeat` / `log_reply_suppressed`；真实输出测试可加 `-ExpectRealOutput` 聚合 `alerts`。这些字段只用于验收复盘：`log_watchdog` 帮助发现 playback gate watchdog 或缺失 `voice_play_end` 造成的卡顿，`log_contamination` 帮助识别 Warthunder 等非 NEKO Live 主动输出污染，`log_reply_length_status` / `recent_long_reply_count` 帮助标记最新或最近窗口内的回复长度异常，`recent_long_reply_*` 会把最近长回复按 `avatar_roast`、`danmaku_response`、`idle_hosting`、`active_engagement`、`warmup_hosting` 拆开，方便确认到底是哪条直播路径在拖长；`recent_generic_host_prompt_count` / `log_generic_host_prompt` / `generic_host_prompt` 帮助抓出“大家快来互动 / 发弹幕 / 还有人吗 / 在不在 / get the chat moving”这类模板式营业话术；其中 `log_generic_host_prompt` 只检查后端日志里的 `send_lanlan_response text=...` 回复文本，不扫描 prompt 指令，避免把“禁止说某句”的提示词误报成猫猫已说出口；`log_reply_repeat` 会把后端日志里最新一条真实 `send_lanlan_response text=...` 与最近最多 10 条输出窗口比较，用于抓“猫猫实际播出的句子和前面某句高度相似”这一类复读风险，包含隔几句又绕回旧话术的非相邻复读，也包含共享大量短片段但换序改写的换皮复读；若两句都命中同一组直播高频包袱 anchor（例如惊喜 / 小鱼干或奖励 / 特别企划或节目 / 大家互动或弹幕接话），即使文字已经换序换词，也应按 `reply_repeat` 处理；如果后端日志里已经出现 `NEKO Live repeated reply detected`、`NEKO Live repeated reply suppressed`、`neko_live_reply_repeat=true` 或 `neko_live_reply_suppressed=repeat`，监控也会直接认定复读，触发后会在 `alerts` 中标记 `reply_repeat`。`log_reply_suppressed` 是兼容旧测试日志或外部实验日志的观测字段；如果插件 recent result 仍显示 `pushed` 但直播间没有听到声音，可以把这个字段和 `reply_suppressed` alert 作为排障线索，但不能把它当成当前插件已经改写宿主最终出口的证明。`avatar_roast_share` / `avatar_roast_bias` 帮助识别普通弹幕路线是否仍被首次出场锐评吞掉，`entrance_pacing_window` 帮助确认当前活跃度下连续首评会被压多久，`latest_topic_recent_skip_reason` 和 `recent_topic_skip_*` 帮助复盘主动营业素材为什么被过滤，例如单 UID 刷屏、旧弹幕、首评上下文、未输出弹幕，或近期弹幕本身不适合主动营业；`recent_topic_shape_*` / `recent_topic_intent_*` 会统计最近主动营业是二选一、轻站队、轻吐槽还是小挑战，以及对应接话意图，`recent_topic_axis_*` 会统计主动营业的趣味轴是否长期偏向 choice / tease / mood / micro_challenge / viewer_callback 中的一类，方便判断话题是否太单调或缺少接话路径；`recent_host_beat_axis_*` 会统计最近冷场陪播的趣味轴是否过窄；`recent_topic_reply_affordance_top` / `recent_host_beat_reply_affordance_top` 会显示最近主动营业或冷场陪播最常复用的观众接话路径；当最近主动营业或冷场陪播至少 3 次且同一话题形态、接话意图、active topic 趣味轴、host beat 趣味轴或接话路径占比过高时，`alerts` 会出现 `topic_shape_bias`、`topic_intent_bias`、`topic_axis_bias`、`host_beat_axis_bias`、`topic_reply_affordance_bias` 或 `host_beat_reply_affordance_bias`，提示下一轮应优先调 topic / host beat 池、接话路径或形态轮换，而不是继续加新事件类型；点名/未点名请求、纯反应和运行反馈类素材过滤会额外在 `alerts` 中提示 `topic_filter_direct_request` / `topic_filter_reaction` / `topic_filter_runtime_feedback`，方便直播现场快速判断主动营业为什么没有拿近期弹幕开题；若主动营业或冷场陪播已实际输出但缺少可见接话路径，`alerts` 会提示 `topic_reply_missing` 或 `host_beat_reply_missing`；`alerts` 还帮助现场优先发现 dry_run、断连、stale、失败/跳过、延迟、watchdog、串台、长回复、真实回复复读、复读抑制观测、模板式营业、测试隔离不干净、最近失败、头像锐评偏航、冷场陪播缺席、主动营业缺席和 `backend_log_missing`；它们不参与业务路由、节奏判断或输出决策。`backend_log_missing` 只表示监控没有读到后端日志，不能据此判断 playback / TTS / 长回复风险已经消失。

真实输出测试时，如果 `live_status.reason=live_disabled`，历史 monitor 设计（当前切片未分发脚本） 的 `alerts` 会额外输出 `live_disabled`，这表示 NEKO Live 总开关未开启，应先恢复插件开关再判断断连、冷场、主动营业或输出链路问题。

主动营业复盘还应看 `recent_topic_source_fallback` / `recent_topic_source_bili_trending` / `recent_topic_source_recent_danmaku`。如果话题无聊且长期偏向 `fallback`，优先扩充或调整内置小话题；如果长期偏向 `bili_trending`，优先检查公开素材过滤和标题压缩；如果长期偏向 `recent_danmaku`，优先确认是否被单个观众或上一轮话题带偏。当最近主动营业至少 3 次且同一素材来源占比过高时，`alerts` 会出现 `topic_source_bias`。如果素材来源已经分散但观众仍觉得复读，再看 `recent_topic_shape_either_or` / `recent_topic_shape_light_stance` / `recent_topic_shape_tiny_tease` / `recent_topic_shape_small_challenge` 和 `topic_shape_bias`，优先判断是不是互动形态本身太单调。


Live Feel Pack v1.5 增加三个监控信号：`recent_topic_skip_viewer_to_viewer_mention` 用来确认主动营业是否过滤了观众互相 `@`；`recent_topic_skip_recent_danmaku_source_streak` 用来确认是否因为 recent danmaku 连续主导而回退到其他素材；`latest_topic_shape_guard_reason` 用来确认主动营业是否因为连续相同形态 / 意图而切换 topic shape。对应 `alerts` 分别是 `topic_viewer_mention`、`topic_source_streak` 和 `topic_shape_guard`。Live Feel Pack v1.8 追加 `recent_topic_skip_similar_topic_title` / `topic_similar_title`，用于确认主动营业是否因为标题换皮但内容太像而跳过候选，避免长直播后半段反复围绕同一类话题开场。
监控里的 `recent_*` 路由计数表示最近尝试数，包含 skipped / failed；`recent_actual_*` 表示最近实际 pushed / dry_run 的路由数。判断开场暖场、冷场陪播、主动营业是否真的输出，以及判断 `proactive_in_engaged`、`active_blocks_idle`、`warmup_repeat`、`warmup_missing`、`idle_missing`、`active_missing`、`avatar_roast_share` 和 `avatar_bias` 时，应优先看 actual 口径，避免把被跳过或失败的尝试误认为猫猫已经说过。`active_blocks_idle` 表示冷场陪播已经 eligible 且 `active_idle_wait` 已归零，但 director 仍选择主动营业，应优先检查主动营业和冷场陪播的让位关系。

Dispatcher 会在真实输出请求 metadata 与 `dry_run(...)` 摘要中标记 `live_reply_contract=short_tts_line`、`max_reply_chars=...`、`response_module_hint=...` 和插件私有的 `neko_roast_output_policy`。这些字段由插件内 `adapters/output_contract_bridge.py` / `core/live_reply_contract.py` 统一生成：当前只用于插件 prompt 约束、dry-run 摘要、hosted UI / monitor 复盘和调试定位。主程序核心可用能力仅限通用插件基础设施：`push_message(parts, ai_behavior="respond")` 触发猫猫回应、`target_lanlan` 定向到当前猫娘 session、metadata 透明透传到 proactive bridge / recent result、`hosted-ui/context` 给面板取宿主上下文。插件不得通过导入 `main_logic`、修改 `send_lanlan_response()`、新增 host output contract、改写 mirror / memory / TTS 最终出口来实现直播专用发言治理；猫娘怎么说、说多长、如何防复读、如何兜底低质量回复，都必须留在 `neko_roast` 的 prompt、素材选择、viewer profile、recent-output 负例和 monitor 复盘里。

插件侧仍需要在 prompt、素材选择和 monitor 中处理两类直播现场高风险草稿：一是结尾停在“选 A 还是选 B”这类未完成二选一的问题；二是生成了低置信度教程、游戏攻略、惩罚审判、公开处刑、泛泛喊弹幕互动，或把核电站 / 代码 / 电路 / 泰拉瑞亚等素材硬编成“你是打算...”式含糊技术问句等不适合独播场景的句子。历史 monitor 设计（当前切片未分发脚本） 只能把后端日志里的 `neko_live_reply_shape_reason` / `NEKO Live ...` 当成历史兼容或外部实验信号；当前验收以插件 recent result、metadata、真实输出长度和人工复盘为准继续收敛素材和提示词。

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

> Provider-neutral update: `live_events` no longer assumes Bilibili-only `LiveDanmaku` input. Provider events are read through `modules/live_events/provider_event.py`; Bilibili object-style events and already-sanitized dict events are both accepted. Public `uid`, `room_ref`, `avatar_url`, `danmaku_text`, nickname, non-negative finite numeric fields, signal-only summaries, candidate audit summaries, and room-topic prompt examples must use those helpers before reaching `ctx.handle_live_payload()`, audit, recent results, or prompt context.

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

**触碰的契约 / store / UI / action**：胜者仍复用 `bili_live_ingest.normalize` 既有 pipeline 输入形状，不直接写 store、不直接 `push_message`。Gift / SC / Guard 胜者由 `pipeline_routing` 转到 `live_support_events`；`ViewerEvent.to_dict()` 只公开轻量 `event_type` 和 support summary 字段（如 gift 名称、数量、coin 总量、guard level）供 dashboard / monitor 标记 `gift_signal`、`super_chat_signal`、`danmaku_signal` 与 support-event metadata，不暴露完整 raw payload。新增 audit op：`live_event_selected`（含 `candidates` 候选数、`score`、`guard_level`、`event_type`、`selected` 脱敏摘要、`dropped_candidates` 脱敏摘要 + `skip_reason`）、`live_event_flush_failed`、`live_event_roast_failed`。无新增 UI action（`live_events` 出现在 `dashboard_state.modules` 快照里，`status()` 暴露 `buffered` / `window_open`）。

**读写了哪些用户数据**：中枢本身不落任何用户数据——只在内存里短暂持有「当前分最高的一条候选」，投递后即清。头像不经中枢（弹幕不含头像，由下游 `bili_identity` 按 UID 抓）。档案 / 审计 / 总结的写入仍由既有边界负责。

**测试命令与主要场景**：`plugin/plugins/neko_roast/tests/test_live_events.py`（8 用例：空闲态首条即时；冷却期开窗按 `get_score` 择优、整窗只投 1 条；高价值礼物可胜过普通弹幕；EventBus `"gift"` 接线进入中枢；本地冷却挡第二条防并发双锐评；空 uid / 空文本丢弃；`reset` 取消开窗；`safety_guard.output_cooldown_remaining` 时序）。契约测试 `test_live_listener_routes_rich_event_through_hub_to_pipeline` 锁住「富模型 `on_event` → 中枢 → pipeline」打通。

**已知限制**：① `live_events` 只负责选中 support event，不负责写致谢 prompt；具体短句由 `live_support_events` 处理。② 「首评即时」下，空闲态第一条互动即使紧随其后到了更高价值的观众也不会被改选——这是用「临场感」换来的，已拍板取舍。③ 窗口择优依赖 `get_score()` 的打分权重（见 `livedanmaku.get_score`），权重调整会改变择优结果。

## Gift / SC / Guard 短句致谢（live_support_events）

**功能目的**：礼物、SC、上舰被中枢选中后不再伪装成普通弹幕，也不只停在 signal-only 观测，而是由独立模块生成一条短句致谢。它只承认支持事件并自然接住现场，不做贡献榜、奖励承诺、仪式化播报，也不能向观众索要更多礼物 / SC / 上舰。

**责任模块**：`modules/live_support_events/__init__.py`（`LiveSupportEventsModule`），路由在 `core/pipeline_routing.py`，request 构造在 `core/pipeline_requests.py`。

**入口与数据流**：
```text
live_events 选中 gift / super_chat / guard
  -> ctx.handle_live_payload(payload)
  -> pipeline_routing.support_event_type(event)
  -> live_support_events.build_request(event, identity, profile)
  -> safety_guard.before_output()
  -> neko_dispatcher.push_roast()
```

**经过 safety_guard 吗 / 失败如何降级**：经过。`live_support_events` 只构造 `InteractionRequest`，仍走 profile 准备、request build step、`before_output`、dispatcher、dry-run、audit 和 Runtime Timeline；失败口径与其它 pipeline request 相同，不新增直连输出。

**触碰的契约 / store / UI / action**：request metadata 新增 `support_event_type`、`support_event_tier`、`support_event_label`，`output_contract_bridge` 据此把 `response_module_hint` 标为 `live_support_events`，但 `support_event_type` 只接受真实字符串，object / bytes / container 不得被字符串化成支持事件路由；短回复合约给该模块独立 32 字上限，`live_output_contract_prompt` 增加“短句致谢、不索要更多支持、不做仪式”的 route note。`runtime_live_input.expose_request_metadata()` 将 support metadata 投影进 recent result；`ViewerEvent.to_dict()` 只暴露 support summary 字段，不暴露 raw payload。UI 只新增模块卡和 route label，无新增 action。

**读写了哪些用户数据**：不新增长期字段。观众档案仍由 normal pipeline / `viewer_profile` 更新；模块只读取当前事件摘要、近期上下文、观众偏好投影和 live-events prompt block。礼物名称、数量、coin 总量、guard level 只作为当前事件的安全摘要进入 request/result projection。

**测试命令与主要场景**：`test_handle_live_payload_routes_gift_to_support_events` 锁住 gift 进入 `live_support_events`、timeline/metadata/result projection 完整；`test_handle_live_payload_routes_support_events_through_pipeline` 覆盖 `gift` / `guard` / `super_chat` / `sc`；solo stream simulation 覆盖 Gift 和 SC 与普通弹幕、hosting 路线共存。

**已知限制**：当前只做短句感谢，不做 SC 朗读细分、舰长欢迎流程、贡献榜、粉丝牌权益或主播运营动作。

## 直播事件中枢（EventBus）与新增事件 handler

> **这是「把插件分发给其他开发者、各写各事件 handler」的核心契约。** P2.5 完整版地基：接入与处理彻底解耦。


**不做什么**：EventBus 不决定「选谁」（那是 `live_events` 窗口择优的事）、不拼 prompt、不投递 NEKO（仍走四条不变量）；无订阅者的类型在总线上流动但静默丢弃。

**责任模块**：`core/event_bus.py`（`EventBus`）、`core/contracts_events.py`（`LiveEvent` 信封）、`core/contracts.py`（兼容导出）。

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

直播开启后允许注入一份**轻全局直播情景**，用于保证真实直播质量；它只能走 `NekoDispatcher.push_context_instructions()` / `ai_behavior="read"`，不能由 runtime、module 或 UI action 直接 `plugin.push_message()`。`sync_live_instructions()` 只有在 `live_enabled=true`、直播连接可用、房间不是 offline/preparing、`dry_run=false` 且 `live_status_summary=ready_to_stream` 时注入；`dry_run`、断连、未开播或输出通道不可用时不得注入，必要时先 restore。轻全局情景只允许包含直播身份、`live_mode`、独播/人猫同播角色边界、观众对象、禁止提主人/后台/操作员、短 TTS 输出边界，以及 `stream_theme` 的短主题锚点。当前弹幕、头像、UID、冷场/暖场节奏、具体回复合约和 recent-output 负例仍必须收束在插件发出的单次 `respond` 事件 prompt 内。

轻全局直播情景必须可清理：插件停止、断开直播间、关闭 `live_enabled`、进入 offline/preparing/disconnected、切换直播模式或短主题签名变化时，必须通过 `NekoDispatcher.push_context_restore()` 退出旧语境，再按新签名决定是否重新注入。插件只能用 restore 覆盖后续理解，不能假设宿主会物理删除已经进入普通历史、hot-swap cache、memory 或最终 TTS 的旧文本。
`live_enabled` 也是运行态输出总闸，而不只是 UI 开关或 permission gate。`live_status_summary()` 必须在 `live_enabled=false` 时返回 `cannot_stream/live_disabled`，即使旧监听器快照仍显示 connected；自动 `warmup_hosting` / `active_engagement` / `idle_hosting` 因此不得进入 pipeline、不得写 recent result，避免 NEKO Live 未开启时污染其他插件测试或主猫普通发言。

2026-07-09 补充：已知房间状态为 `offline` / ended / preparing 时，`live_status_summary()` 也必须返回 `cannot_stream/live_room_offline`，即使 provider 已经创建监听任务并报告 connected。B 站 listener 的 `on_live` / `on_preparing` 回调只更新缓存的 `live_status`，不直接生成输出；自动暖场、冷场和主动营业必须等待该状态重新变为可播。这样可以支持“先连上房间等开播”，但不会在未开播房间里提前进入独播主持。

2026-07-09 补充：宿主通用 `push_message(ai_behavior="respond")` wrapper 会把事件描述成“回应主人”的通用回调。NEKO Live 请求进入 dispatcher 时必须携带插件侧 live delivery boundary，明确这是直播间发言生成请求，不是给 `{MASTER_NAME}` 的私聊；该边界用于抵消通用 wrapper 的语义偏移，但仍不修改宿主 `send_lanlan_response()` / final TTS 出口。
如果 `developer_tools_enabled=true`，插件会通过 `NekoDispatcher.push_developer_instructions()` 叠加开发者调试语境。手动从面板开启开发者模式时，额外通过 `respond` 播报一次进入调试状态；插件启动或配置重载时只静默注入，不自动播报。
关闭开发者模式时，插件会发送开发者调试恢复语境，只退出调试态，不关闭直播锐评语境，也不清空沙盒记录。
插件停止、断开直播间或关闭 `live_enabled` 时会通过 `NekoDispatcher.push_context_restore()` 发送一段 `ai_behavior="read"` 的恢复上下文，提醒猫猫停止把后续普通对话理解成直播间弹幕、头像锐评事件或观众互动事件。这是兼容历史常驻注入的清理路径，不是新的直播状态入口。
`disconnect_live_room` / `stop_live_listener(mark_disabled=True)` 同时必须清掉 `live_room_context` 里的房间标题、主播名和房间号，只保留 `live_status=unknown` 作为已退出直播语境的哨兵状态；这些字段只能在下一次连接或查询直播间后重新进入事件级 prompt。

锐评指令的**文本构造**集中在 `avatar_roast.build_request()`：它产出完整的 `InteractionRequest.prompt_text`，包含观众昵称/UID/弹幕、头像可见性与 META，以及给猫猫的锐评规则。规则编码了**自适应焦点**——昵称与头像哪个更有料就主打哪个，两者都有料就抓反差/呼应，都平淡就转弹幕/进场时机/直播节奏，避免硬尬夸；并强制“看不到的头像绝不脑补、避免与最近几条锐评重样、一句话适合 TTS”。独播（`solo_stream`）下，如果首次出场事件带有当前弹幕，首评应优先接住这句话，头像/昵称只作为出场印象点缀，避免变成纯头像或纯 ID 锐评；同播（`co_stream`）低打断。`build_request()` 只构造请求、不触发 NEKO。

`avatar_roast` 通过 `bili_identity` 解析出的 META 决定头像规则：`avatar_vision_ok=False`（没取到/识别不了）或默认头像 → 只能就“头像配置（默认/会动/带挂件）或昵称”发挥；能看到头像 → 可锐评其具体内容。

`danmaku_response.build_request()` 只用于同一 UID 已经完成出场锐评后的普通 `live_danmaku` 后续接话。`roast_once_per_uid` 的语义因此收敛为“每个观众只做一次出场锐评”，而不是“每个观众只能让 NEKO 回应一次”。后续弹幕仍必须经过 viewer profile、safety guard、dispatcher、dry_run 和 pacing；成功输出不调用 `viewer_profile.mark_roasted()`，避免把普通聊天回复继续累计成首评次数。

即使主播关闭 `roast_once_per_uid`，同一直播运行会话内的同 UID 后续弹幕仍优先进入 `danmaku_response`，不继续重复头像 / ID 首评模板。该开关只影响是否用持久观众档案拦住跨会话首评，不允许把独播后续聊天退回成“每条弹幕都首评”。

独播首评有独立节流：`solo_stream` 中真正的 `avatar_roast` 之间按活跃度间隔，`quiet=75s`、`standard=45s`、`active=30s`。若短时间内又有新 UID 发送弹幕，pipeline 不再连续做头像 / ID 出场锐评，而是把这条弹幕交给 `danmaku_response` 正常接话；但这只是临时降级，不能把该 UID 标记为已完成头像锐评。只有真实进入 `avatar_roast` 路由且成功 pushed / dry-run 到 dispatcher 的出场首评才允许写首评标记，避免新人被节流吞掉后再也补不上头像锐评。

在 `dry_run` 链路验证中，pipeline 可以在同一运行会话内把一次成功到达 dispatcher 的首评 dry-run 视为临时出场标记，使同 UID 下一条弹幕走 `danmaku_response`；该标记只存在于当前 `RoastPipeline` 实例内，不写 `viewer_store`，不增加 `roast_count`，也不调用 `viewer_profile.mark_roasted()`。重新开始监听直播间会清空该临时标记，保证下一轮链路验证从干净窗口开始。

直播修正：本轮会话中只有某个 UID 的 `avatar_roast` 真正成功 pushed 后，pipeline 才写入 session claim；dry-run 只写当前 pipeline 的临时验证标记。若首评在 `before_output` 被跳过、dispatcher 失败或被上游标记为 skipped，则不能 claim，该 UID 后续仍可在合适弹幕中自然补一次头像 / ID 锐评。补评 prompt 必须把当前弹幕当入口，表现成自然诙谐的直播接话，不能说“补评”“重试”“刚才漏了”或暴露 pipeline 状态。

`danmaku_response` 的 prompt 只围绕当前弹幕接话：不能重复首次出场、头像、ID 或进场锐评模板；除非当前弹幕本身相关，否则不主动评价头像或昵称；独播（`solo_stream`）提示 NEKO 是台前唯一主播，需要自然接住话题；同播（`co_stream`）提示低打断，给主播留空间。

**2026-06-25 长直播发现的输入隔离缺口**：路由已经能把同 UID 后续弹幕送进 `danmaku_response`，但 dispatcher 曾经在 `identity.avatar_bytes` 存在时无条件附加头像 image part。结果是后续接话虽然没有走 `avatar_roast`，模型仍然看到头像，容易再次评价同一观众头像。当前修复是把头像视觉输入限制在显式 opt-in 的请求：`avatar_roast`（以及显式的开发者 demo / 未来明确声明需要视觉输入的模块）可以带头像；`danmaku_response`、`idle_hosting`、`active_engagement`、`warmup_hosting` 默认是纯文本输出请求。这个问题是输入边界污染，不是 `roast_once_per_uid` 失效。

`recent_interaction_context()` 会从最近成功投递或 dry_run 的互动结果中提取轻量上下文（路由、事件来源、观众弹幕、active topic / host beat 的形态、内容族、趣味轴、接话路径，以及确实可用的真实短句 `InteractionResult.output`），供 `avatar_roast`、`danmaku_response`、`warmup_hosting`、`idle_hosting` 和 `active_engagement` prompt 使用。`queued_to_neko(...)`、`dry_run(...)`、`skipped_to_neko(...)` 等 dispatcher / 测试摘要不能当成猫猫已播出台词。它不把完整历史 prompt 塞回模型；进入 prompt 前每行会压成短摘要，并优先保留 `NEKO already said: ...` 这类已播出台词，且在 prompt 中必须被描述为 **used material / anti-repeat only**：这是禁复用清单，不是续写上下文、脚本前缀或下一句要延续的话题。目标是让下一次开口避开同一个开场、包袱形状、奖励梗、计划、观众问卷、主持节拍、接话路径或已经播出的短句。

`viewer_session_context(uid)` 只在当前 runtime session 内按 UID 提取最近少量该观众的已投递 / dry-run 弹幕上下文和可用的真实猫猫已输出短句，供 `danmaku_response` 判断“这是同一位观众的后续接话”。dispatcher / dry_run 摘要不进入 `NEKO already said`。它不写长期档案，不总结完整聊天历史；进入 prompt 前也会压成短摘要，并必须作为 same-viewer used material 使用。除非当前弹幕明确要求继续同一个话题，否则不能默认续写该观众上一轮内容；用途是避免同一观众后续弹幕再次被当成首次出场，减少重复头像 / ID / 首评模板，也避免把上一轮长回复或已播出台词喂回模型造成复读。

`recent_results` / `recent_sandbox_results` 是 dashboard / monitor 会直接读取的公开投影，只允许来自 `InteractionResult.to_public_dict()` / `to_sandbox_dict()`。这些方法必须复用 `contracts_public.py` 的 JSON-safe / 脱敏规则：request metadata、event topic / host beat 摘要、identity、profile、steps、output 和 reason 都不能原样塞入 object、bytes、cookie/token/signature/authorization 形态文本或 prompt 原文；非字符串对象不得通过 `str()` 进入 recent result。

Danmaku Response Quality v2 进一步把“普通弹幕接话”从首评模板里拆清楚：`danmaku_response` 会在 prompt 中标记当前弹幕的轻量 `danmaku_profile`，包括 `viewer_to_viewer_mention`、`question`、`emoji_or_reaction`、`short_line`、`normal_line` 和 `empty`。短反应 / 表情 / 哈哈类弹幕只允许做很短的情绪镜像；问题弹幕必须先直接回答；`@其他观众` 只能作为公开内容的极短旁白，不能当成观众在喊 NEKO，也不能替观众之间调停；`@猫猫` 仍然视为当前弹幕目标。`danmaku_profile`、`danmaku_reply_target` 和 `danmaku_reply_shape` 会进入 request metadata、recent result 和 历史 monitor 设计（当前切片未分发脚本） 的 `latest_danmaku_profile` / `latest_danmaku_reply_shape`，便于直播复盘判断猫猫为什么这么接话；`runtime_live_input.expose_request_metadata()` 只把字符串 metadata 复制到 recent result 顶层，object / bytes / container 值不得字符串化成监控字段。这个 profile 只影响回复形状，不新增事件类型，不绕过 viewer profile、safety guard、dispatcher、dry_run、cooldown 或 pacing。

Danmaku Response Quality v3 追加 `content_request` 形状：当观众明确要求讲笑话、解释、展开、说说、起外号或编一段时，prompt 必须当场交付内容，不能只说“好呀我来讲”；`output_contract_bridge` 会给这类请求 expanded 输出额度，插件侧 `live_output_quality.py` / `live_output_shape.py` 只作为插件内 prompt、metadata 和复盘口径的治理来源，不能把“空承诺式回复”兜底塞进宿主最终出口。同一轮还加入当前观众 target lock：recent context 只用于防重复或轻微承接，不能抢走当前弹幕目标。

Danmaku Response 可见目标锁要求：直接弹幕回复必须让观众听得出“猫猫正在回谁”。`danmaku_response` prompt 需要写入 `visible_reply_target`，并要求纠错、否认、自我修正类回复也先点当前观众；`NekoDispatcher` 在投递前追加 `NEKO Live visible reply target` 边界，带 `current_viewer` 与短 `current_danmaku`。最终输出整形仍可为普通直接弹幕补观众名前缀，但不能依赖宿主核心执行该逻辑；即使最终整形没有触发，模型 prompt 本身也必须尽量保证第一分句可辨认当前观众。

Active Engagement 回答识别要求：当最近 `active_engagement` 输出包含明确的短回应钩子时，`active_hook_answers.py` 必须把 A/B/C/D、1/2/3/4、中文一二三四，以及“扣1/扣个1/打1/选1”识别为 `active_hook_answer`，让它们走 `danmaku_response` 而不是被首评、已锐评或低价值上下文压掉。孤立数字只有在最近存在有效互动钩子时才特殊处理，避免数字刷屏误触发。

Danmaku Response Quality v4 追加 runtime 级 recent room context：`recent_context_builders.build_recent_room_danmaku_context()` 会从 recent result 中抽取近几条 `live_danmaku`，过滤 `1` / `666` / 哈哈等低价值重复，归纳问候、内容请求、选择题、问题求助、贴贴玩梗、游戏/直播等房间主题；`RuntimeRecentContextApiMixin.recent_room_danmaku_context()` 暴露给 prompt，`room_danmaku_context_block()` 只作为 `danmaku_response` 的 advisory block 使用。它不改变路由、不新增队列、不持久化画像；规则是“当前观众优先，若近期弹幕同主题则简短桥接主题，避免逐条复读或重复抛同一个问题”。当当前弹幕不是问候 / 表情 / @其他观众 / 空短反应，且 room context 确实有 `room_theme` 时，`danmaku_response` 会把该轮标记为 `reply_length_mode=room_bridge`，dispatcher 与插件 prompt 合约按 48 字上限允许最多两句小连接：先回答当前观众，再轻轻桥接房间共同话题；不得借此开启主持计划、复盘或新投票。

Danmaku Response Review Pack v1 把直播复盘字段收拢到 recent result / monitor / live_explain：`runtime_live_input.expose_request_metadata()` 会安全投影 `danmaku_profile`、`danmaku_reply_target`、`danmaku_reply_shape`、`danmaku_anchor_hint`、`reply_length_mode` 和 `room_theme`，字段只接受真实字符串，不会把 object / bytes / container 字符串化成监控控制字段。历史 monitor 设计（当前切片未分发脚本） 对应输出 `latest_reply_length_mode`、`latest_reply_target`、`latest_anchor_hint`、`latest_room_theme` 和 `latest_reply_shape_reason`；其中 `latest_reply_shape_reason` 优先读 hosted-ui recent metadata，backend log 只能作为历史兼容线索，不能要求宿主实现 NEKO Live 专用 output audit。`runtime_dashboard_explain.latest_result` 暴露同一组安全短字段，便于把 trace_id、shape reason 和 room bridge 决策对上。这个包只用于复盘“分类错 / 桥接错 / 插件侧 prompt 或 fallback 设计错”，不新增路由、不改变回复节奏、不暴露原始 room examples。

Danmaku Selection Pack v1 把“猫猫是否要回这一条普通弹幕”收进 `live_events`。`room_topic.remember_live_event()` 必须先记录房间上下文，随后 `live_events` 才能按插件内策略跳过低价值弹幕；跳过只写 privacy-safe audit `live_event_reply_skipped` 和 status，不进入 pipeline、不调用 dispatcher、不写原始弹幕文本。公开配置仍只使用既有 `activity_level`：`standard` / `active` 派生 `reply_selection_policy=selected`，只跳过 `666`、纯反应、重复数字等低信息弹幕；`quiet` 派生 `reply_selection_policy=quiet`，额外跳过低优先级普通短句，但问题、内容请求、问候、舰长/高分事件仍放行。不要新增和 `activity_level` 重叠的 `reply_selection_mode` 配置项；`reply_selection_policy` 只是 Dashboard / Monitor 复盘字段。稳定 skip reason 是 `selection.low_value_danmaku` 和 `selection.quiet_low_priority`。

所有直播开口 prompt 必须复用 `anti_repeat_rules()`：先对照 NEKO Live 的 recent-output 记忆，避免复用上一句的开头、句式、包袱、话题切法、奖励梗、计划、观众问卷或主持节拍，也不能把上一句换词改写成新回复。插件侧 `recent_interaction_context()` 只能作为“已用素材 / 已用主持节拍”的 blocklist，不能当成下一句的脚本前缀或继续话题；`viewer_session_context()` 同样只能用于同一观众的轻量连续感和防复读，不能默认续写该观众上一轮话题，除非当前弹幕明确要求继续；若当前草稿和 recent context 共享同一主题、开头或 joke shape，应换角度或只回答当前弹幕。recent context 里出现的 `topic_family`、`host_beat_family`、`fun_axis`、`shape`、`intent` 也必须按已用素材处理，不能只避开原句却继续复用同一类“主播力 / 小鱼干 / 暗号 / 小电台 / 二选一”主持手法。插件通过 `live_reply_contract=short_tts_line`、`neko_roast_output_policy`、recent-output negative examples 和 `anti_repeat_rules()` 在请求侧追加同样的 anti-repeat 约束：模型不要继续、复述或改写最近 12 条 NEKO Live 输出。文本 / 语音 proactive 直播回调会把 NEKO Live metadata 透明传给宿主；当前插件只把它视为插件私有提示、复盘和调试字段，不直接改写宿主普通 AI turn、流式缓冲、mirror、memory 或最终 TTS 发送路径。

同一条隔离规则只能在插件侧尽力规避：带 `live_reply_contract=short_tts_line` 的直播请求会携带 `neko_roast_output_policy` 和 recent-output 负例，提醒模型不要把直播短播报当作下一轮普通聊天上下文；插件不修改宿主 memory / analyzer / turn end 路径。

热切新 session 期间的 `message_cache_for_new_session` 属于宿主侧风险边界：NEKO Live 短播报不能作为新 session 的普通上下文预热材料；插件当前不直接写这条宿主路径，只在自身 recent-output / voice-echo 复盘口径里保留直播材料。

独播冷场判断只应由可接话的观众弹幕活动刷新。`entry` / 进房、礼物、SC、Guard 或其他非弹幕 live health row 不应把房间误判为 engaged，否则低弹幕直播间会因为有人进出而一直无法触发 `idle_hosting`。Gift / SC / Guard 后续要作为独立高价值事件接入，不在这里冒充普通弹幕活跃度。

`active_engagement` 只用于 quiet moment 的轻量主动营业。每次主动营业都必须给观众一个明确接话把手：A/B 选择、一个词/一个字回答、小立场或轻微可反驳的玩笑；禁止泛泛喊“大家互动”“弹幕刷起来”“想听什么”。内置 fallback 话题池只提供原材料，最终输出仍必须经过短回复合约和统一 dispatcher。

Live Feel Pack v1.16 增加 `live_column` 素材字段，用来标记 NEKO 自有的小栏目格式，例如 micro poll、tiny verdict、tiny radio、room thermometer、one-word callback。它是给 prompt 和复盘使用的格式提示，不是 UI 新页面，也不是让 NEKO 正式报幕；输出仍必须是一句短 TTS 台词，并且必须经过 Safety、dry_run 和 dispatcher。`idle_hosting` 的 host beat、`active_engagement` 的 fallback / recent / B 站公开素材都应带 `live_column`，recent result 和 `recent_interaction_context()` 也要暴露该字段，方便下一次直播复盘判断猫猫是在用哪种主持格式，而不是只看标题。

Live Feel Pack v1.17 继续扩 `idle_hosting` 和 `active_engagement` 的内容质量池，而不是提高自动开口频率。冷场陪播素材当前由 `data/idle_hosting_beats.json` 维护 32 条，主动营业 fallback 素材至少覆盖 36 条，并继续要求每条素材具备明确 `live_column`、`fun_axis` 和 `reply_affordance`。新增素材优先围绕 NEKO 自有小栏目、小判定、小电台、小法庭、桌面/尾巴/灯光/暗号等具体可接话画面，避免“大家互动 / 发弹幕 / 想聊什么”式模板主持。下一次直播复盘如果仍觉得无聊，应先看最近 `live_column`、`fun_axis`、`reply_affordance` 是否单一，再决定是否继续扩素材池或调整触发节奏。

Live Feel Pack v1.18 增加直播节目节奏字段，但仍不改变自动触发频率。`idle_hosting` 会按连续冷场次数优先选择 `idle_stage`：第一次偏 `settle`（轻观察 / 小状态），第二次偏 `column`（小栏目 / 二选一 / 轻判定），第三次及以后偏 `callback`（一字/两字回调或三秒挑战），随后仍由现有 idle-to-active handoff 决定是否切到 `active_engagement`。`active_engagement` 会给 topic 推导 `topic_pack`，例如 `micro_poll`、`neko_verdict`、`room_mood`、`room_observation`、`viewer_callback`、`micro_challenge`。这些字段进入 prompt、recent result 和 recent interaction context，用于复盘猫猫的节目层次，而不是让主播调复杂参数。

2026-06-28 真实独播验证后补充：`idle_hosting` 和 `active_engagement` 的下一阶段重点是内容质量，而不是更高频率。没人发弹幕时，NEKO 可以说话，但不能只说泛泛的主持模板；输出应优先是短句氛围补位、小二选一、轻吐槽、NEKO 自己的小状态或一个低门槛接话点。每次主动营业只允许一个清晰 reply hook，避免同时问多个问题、要求观众帮忙选题、或把“直播间没人说话”本身当成话题。复盘时如果链路已经 `pushed` 但观众仍觉得无聊，应优先调整 `host_beat` / `topic_material` / `topic_shape` / `reply_affordance`，而不是先扩 Gift / SC / Guard 或提高自动开口频率。

2026-07-03 直播节奏补充：独播主持要像同一个人在说话。`warmup_hosting`、`idle_hosting`、`active_engagement` 共享 host-output 冷却，自动 loop 一轮最多推送一个成功开口；最近 30 秒内已有 host 模块 `pushed` / `dry_run` 时，其他 host 模块必须等待，除非 active engagement 是为了接管连续 idle hosting。避免 5-10 秒内连续抛出多个不相干主持钩子，让观众感觉像多个人同时控场。

Live Feel Pack v1.7 把这个方向落实为内容素材约束：`idle_hosting` 的 `host_beat` 至少覆盖软观察、小二选一、轻吐槽、小状态、一个字/一个词、小挑战等形态，并且每个 beat 都必须给出 `fun_axis` 与 `reply_affordance`；当前冷场 beat 池不得少于 14 条，必须包含三字暗号、画面感观察、猫爪按钮、自嘲轻吐槽等不同接话形态，避免长直播无弹幕窗口过快轮回同几句补位。冷场 beat 轮换除了避开最近 key、fun_axis 和 reply_affordance，也会避开和最近 beat title 高度相似的候选；若所有候选都相似，则优先选择未用过的 key，避免卡死或立刻重复同一个 beat。`active_engagement` 的 fallback topic 至少覆盖 choice、tease、mood、micro_challenge、viewer_callback 等 `fun_axis`，并且每个 topic 都必须给出 `reply_affordance`；主动营业选题也会优先避开最近相同的 reply_affordance，避免连续几次都只是在要求观众投票、回一个词或轻推回同一种接话路径。新增话题或冷场 beat 时不要只加“聊什么”的标题，必须同时说明它为什么有趣、观众可以怎样用很短弹幕接上；素材标题、hint 和 reply path 里也不能写入“大家来互动 / 发弹幕 / 想聊什么 / get the chat moving”等模板主持诱导，即使是否定句也要避免把这类短语喂给模型。

Live Feel Pack v1.9 进一步把“不要复读”从单模块扩展成跨模块约束：`idle_hosting` 和 `active_engagement` 会共享最近使用过的 host material `family`，例如 `choice_vote`、`short_callback`、`room_mood`、`object_scene`、`host_self_test`、`tease`、`micro_challenge`。冷场刚用过某类二选一或电台氛围后，主动营业应优先换成别的内容族；主动营业刚用过某类主播力自嘲或一个词回调后，下一次冷场也应优先换族。`family` 是素材选择和 prompt 约束用的内部字段，不是展示给观众的台词；若所有候选都被 recent family 命中，可以逐步放宽到 key / title 去重，避免无话可说，但正常路径应优先避开同一内容族造成的体感复读。

历史 monitor 设计（当前切片未分发脚本） 会把这层内容族也暴露给直播复盘：`latest_topic_family` / `latest_host_beat_family` 用于看最近一次主动营业或冷场陪播用了哪类主持手法，`recent_topic_family_*` / `recent_host_beat_family_*` 用于看最近窗口内是否一直停在同一类；当最近主动营业或冷场陪播至少 3 次且同一内容族占比过高时，`alerts` 会出现 `topic_family_bias` 或 `host_beat_family_bias`。这类告警对应的是“听起来像在复读同一种主持套路”，应优先扩素材池或调整 family 轮换，而不是先提高开口频率。

为了覆盖长直播里“隔几句又绕回旧说法”的问题，直播开口模块默认会把最近 12 条已输出 / dry-run 的 live material 作为负例上下文喂给 prompt。这个上下文仍然只用于 anti-repeat：模型不能延续、总结或复述这些旧内容；当前弹幕或当前主持节拍永远优先。每条上下文都会被压缩，避免为了防复读把 prompt 变成长历史回放。对于 `idle_hosting` / `active_engagement`，负例上下文会带上 `family`、`fun_axis` 和 `reply_affordance`，让模型知道不只是某个标题或某句话已经用过，同一种“二选一 / 小电台 / 一个词回调 / 轻吐槽 / 小挑战”等主持手法和同一种观众接话路径也应视为已使用材料。

普通弹幕回应也要参与同一套已用素材记账：`recent_interaction_context()` 会从真实播出的 NEKO 文本中提取 `spent_output_family`，用于标记“小鱼干奖励 / 弹幕接话 / 主播力自测 / 暗号回调”等已经用过的旧梗。该字段会进入 `recent_results[*].spent_output_family`，历史 monitor 设计（当前切片未分发脚本） 会输出 `latest_spent_output_family` 与 `recent_spent_output_family_*`，方便直播现场确认“旧梗 family 是否正在重复”；当最近真实输出至少 3 次且同一 spent-output family 占比过高时，`alerts` 会出现 `spent_output_family_bias`。dispatcher / dry_run / queued placeholder 摘要不能产生 `spent_output_family`，避免测试摘要被误当猫猫已经播出的旧梗；monitor 统计 `spent_output_family` 时也只按真实 `pushed` 输出计数，不把 dry_run 当成观众已经听过的内容。prompt 侧必须把 `spent_output_family` 和 `topic_family` / `host_beat_family` 一样视为 forbidden material，避免只避开原句却继续复用同一类包袱。`idle_hosting` / `active_engagement` 的素材选择也会优先避开最近真实输出已经用过的 spent-output family；如果候选全部命中，才逐步放宽到现有 key / title / family fallback，避免直播被防复读规则卡死。英文 family token 必须按完整词或明确短语命中，不能因为 `explain` / `catch` / `presentation` 这类子串误判成 plan / chat / reward。

`spent_output_family_bias` 的占比按带有 spent-output family 的 recent result 条数计算，而不是按逗号分隔后的标签总数计算；例如一条输出同时标记 `reward,audience_prompt` 时，不能让多标签本身稀释 `reward` 的重复占比。

Live Feel Pack v1.10 把这条规则沉淀为插件侧复盘口径：即使模型没有逐字复读，只要真实输出命中了同一类高信号直播包袱，例如小鱼干 / 奖励、特别企划、主播力自测、一个字 / 一个词 / 暗号回调、安静冷场，或在二选一、房间氛围、桌面场景、轻吐槽、小挑战等内容族上又和近期输出有明显短片段重叠，也应视为换皮复读。下一场直播如果仍觉得猫猫“说法变了但意思又绕回来了”，优先看 `log_reply_repeat`、`topic_family_bias` 和 `host_beat_family_bias`；`log_reply_suppressed` 只作为旧测试或外部实验日志线索。

Live Feel Pack v1.12 把 audience-prompt 复读作为单独的高风险主持套路处理。连续输出里如果都在召唤观众“想听 / 想看 / 聊点 / 发言 / 发弹幕 / 接话 / 来一句 / 扣 1 / 吱一声 / 冒个泡 / 给点反应 / 还在吗 / 有人吗 / 在不在 / 打个分”，即使句子不相似，也应视为 `audience_prompt` family 的换皮复读，并进入插件侧复盘。真实 pushed 输出中的这些表达也会被 `_spent_output_families()` 标成 `audience_prompt`，进入 `recent_interaction_context()`、`spent_output_family`、素材选择避让和 monitor 复盘口径。普通“弹幕”一词本身不能单独作为强复读信号，避免把正常接弹幕误判成模板营业；需要命中更明确的观众召唤动作。

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

## 交接清单

当前交接重点是 `neko_roast` 插件内的直播发言质量层，不包含抖音 transport，也不要求宿主核心新增直播专用输出逻辑。

1. 热梗知识库由 `data/meme_knowledge.json` 维护，加载/检索入口是 `core/meme_knowledge.py`。当前库 36 条，只做离线静态检索和 prompt 可选提示；不要联网抓热榜，不要把梗写成强制台词，不要把命中结果当成新的路由或记忆。
2. 冷场陪播素材由 `data/idle_hosting_beats.json` 维护，加载入口是 `core/live_content_host_catalog.py`，选择/轮转仍归 `core/live_hosting_beats.py`。当前库 32 条；legacy `live_content_host_catalog_*` 只作为坏 JSON、缺字段或重复 key 时的兜底。
3. `meme_query` 是冷场 beat 上的可选调味字段，最多给 `idle_hosting` prompt 一条热梗提示。它不能改变 host beat 的主方向，不能绕过 Safety / dry_run / Dispatcher，也不能替代当前弹幕语义。
4. 观测字段只用于复盘：`meme_hint_ids` / `meme_hint_tags` 解释本次提示命中了哪些梗，`host_beat_*` / `topic_*` / `*_family` 解释直播话术形态和复读风险。任何新字段都应先确认 `runtime-observability.md` 是否已经定义语义。
5. 素材改动的最低验证是 JSON 解析 + 当前素材选择 / 输出合约测试：`uv run python -m json.tool plugin/plugins/neko_roast/data/meme_knowledge.json`、`uv run python -m json.tool plugin/plugins/neko_roast/data/idle_hosting_beats.json`、`uv run pytest plugin/plugins/neko_roast/tests/test_active_topic_core.py plugin/plugins/neko_roast/tests/test_output_contract.py -q`。触碰选择逻辑、prompt 合约或 runtime metadata 时，再加对应 runtime / contract 测试和 CLI check。
6. 下一任优先看五份文档：`quickstart.md` 看直播现场操作和素材维护速查，`independent-mode-product-plan.md` 看产品验收口径，`runtime-observability.md` 看 Monitor / Dashboard 字段语义，`live-center-roadmap.md` 看阶段状态，本文看模块边界和测试门禁。

## 冗余代码判定

交接前做过一轮静态冗余检查：当前没有发现整个 Python 模块无人导入。清理时按下面的口径判断，避免把兼容边界当成垃圾代码删掉。

1. `ReservedModule` 对应的空壳目录不是冗余：`bili_dm_ingest`、`contribution_rank`、`watch_time`、`bili_read_tools`、`bili_write_tools`、`automation_ops` 是 UI / registry 预留能力，当前必须保持 `ENABLED = False` 与只读占位，不承载真实逻辑。
2. 以 compatibility / facade 命名或在模块边界表中标为旧导入出口的文件不是冗余，例如 `active_topic_compat.py`、`active_topic_selection.py`、`active_topic_materials.py`、`pipeline_results.py`、`live_hosting_beats.py`、`recent_context.py` 和 `modules/_prompt_context.py`。这些文件的职责是保护旧 import、runtime mixin 和测试边界；如果要删除，必须先改调用方和契约测试。
3. 插件 action / lifecycle 方法不是普通未引用函数：`@plugin_entry`、`@ui.action`、`@lifecycle` 注册的方法由宿主反射调用，不能按本仓搜索不到直接调用来删除。
4. 写能力必须和只读 ingest 分离。`modules/bili_live_ingest/danmaku_core.py` 已收口为只读监听器，不再保留旧 `DanmakuListener.send_danmaku()`；未来若需要发弹幕、评论、动态或私信，只能在 `bili_write_tools` 这类独立写模块中接入，并单独做权限、登录态和安全评审。
5. 本地运行态文件不是源码：`__pycache__` 和 `plugin.toml.lock` 可以清理，`vendor/douyin_bridge/windows-amd64/douyinLive.exe` 是内置 bridge 二进制，虽然被 git ignore，但不要在普通卫生清理里删除。

## 数据边界



- UID
- 昵称
- 头像 URL
- 首次出现时间
- 最近出现时间
- 锐评次数
- 最近锐评时间
- 最近输出摘要
- 直播弹幕计数
- 偏好标签计数（例如 `questions` / `tech_ai` / `meme`）


**持久化（本地 JSON，当前固定默认目录）**：观众档案落本机 JSON 文件 `viewer_profiles.json`，当前仍**不走宿主 PluginStore**，以保持档案写入路径简单、可控、便于审计。历史上的 `store.enabled` 构造期冻结与插件数据不跟随 selected_root 已由 `Fix plugin host config and data root handling (#1884)` / `08b317f6` 修复（见 `docs/devlog.md`）。存储目录当前固定使用 `plugin.data_path()`；`viewer_store_dir` 自定义位置入口在 2026-06-19 真机测试后暂时屏蔽，待插件侧重新回归配置持久化 / host 数据根后再恢复。`viewer_store.py` 仍保留自定义目录能力与回退逻辑，但本阶段不向主播暴露。dashboard 暴露 `viewer_store`（当前目录 / 可写 / 是否自定义），面板据此显示与告警。

Dashboard 的 `live_explain` 只读投影只允许展示链路阶段、最近结果状态、主题 key、偏好标签计数、常聊话题 / 接梗提示计数、短摘要、避坑提示、`trace_id` 和 Runtime Timeline 的 stage/status/route/reason，不得新增原始弹幕全文、raw payload、完整 prompt、cookie/token/signature 形态文本或头像 bytes。CI gate 由 `test_dashboard_state_exposes_privacy_safe_live_explanation` 锁住后端投影隐私边界，由 `test_panel_renders_live_explanation_and_viewer_preference_columns` 锁住 UI 字段和 8 locale 同步。

开发者沙盒数据规则：

- `recent_sandbox_results` 只保留运行时内存短期记录，插件重启即消失。
- 开发者模式关闭时不清空 `recent_sandbox_results`；只阻止继续查询、模拟弹幕和调用聊天开发者工具。
- “清空沙盒记录”只清沙盒内存记录和历史头像预览缓存，不影响观众档案、直播总结或真实直播记录。
- “清空观众档案”只清 `viewer_profiles.json` 中的观众档案，用于受控独播测试前重置首评状态；不清空 `recent_results`、沙盒记录、直播总结或安全队列。单 UID 治理动作分两类：`delete_viewer_profile` 删除该 UID 的整条档案并清掉 session 首评标记；`reset_viewer_impression` 只清 `preference_tags` / `favorite_topics` / `running_jokes` / `impression_summary` / `avoid_guidance` 等印象字段，保留 `roast_count` / `last_result` / `danmaku_count`，用于修正错误印象而不重新触发首评。
- “清空观众档案”这类危险操作必须在后端 action / runtime 再次检查 `developer_tools_enabled`；Hosted UI 可以把按钮保持可点击，以便开发者模式关闭时弹出明确提示，但不能只靠 disabled 作为权限边界。修改这类按钮时必须同步 `ui/panel.tsx` 和完整兼容入口 `ui/panel_compat.tsx`，并保留 smoke gate，避免打包版表现和源码面板不一致。
- 沙盒查询不写 viewer store，不返回 base64 data URL，不写长期 preview 文件。
- 沙盒锐评结果不进入 `recent_results`，不进入直播总结。

## UI 约定

Hosted UI manifest 入口位于 `ui/panel_compat.tsx`，这是由模块化源码内联出的完整功能单文件兼容入口；实际维护源码仍在 `ui/panel.tsx`，展示组件、数据区段、状态契约、标签/格式化 helper 分别位于 `ui/panel_components.tsx` / `ui/panel_data_sections.tsx` / `ui/panel_state.ts` / `ui/panel_helpers.ts`。外壳 = **生命周期-域导航**（薄外壳 + 模块贡献），完整契约见 `docs/ui-architecture.md`。导出给主分支插件中心测试的包必须使用这个完整入口，不要改成只显示基础状态 / Raw state 的最小 fallback 面板。

保存动作分两类：平台 / 房间切换可以显式提交直播目标字段；设置页、高级项和模块卡的即时保存必须只提交变更 patch，不能用 `configDefaults.live_platform` 或空房间字段做全量覆盖。改 `saveConfig()` 或新增保存按钮时，必须同步检查抖音平台下修改频率不会跳回 B 站。

界面分为**六个一级页**（+ `开发者沙盒` 按开发者模式条件追加），id / 顺序固定（契约测试 `test_panel_uses_six_top_level_tabs_in_order` 锁住）：

- `控制台 console`：开播总入口。**B站登录卡**（扫码图 + 检查登录 + 退出登录 + 登录状态，见「B站登录态」）+ 直播间 ID（**支持直播间链接**）+ 查询直播间 / 开始锐评（已开播时切为停止 / 暂停 / 恢复）+ 状态总览四格（直播间 / 监听 / **实时人气值** `live_connection.viewer_count` 由 `danmaku_core` 解析心跳回包，未连接显示 `-` / 安全状态）+ 直播模式 + dry_run 速开关。（原「直播间配置」页已折入此页。）
- `观众 viewers`：直播总结（本场真实锐评粗报 + 最近锐评摘要，数据来自运行时内存 `recent_results`，沙盒结果不进）+ `live_explain` 链路解释（阶段状态 / 主题 key / 安全偏好标签汇总 / 常聊话题 / 接梗提示 / 最近结果状态）+ 观众档案（UID / 昵称 / 锐评次数 / 弹幕数 / 熟悉度 / 画像置信度 / 画像新鲜度 / 偏好标签 / 常聊话题 / 接梗提示 / 安全摘要 / 回复建议 / 避坑提示 / 最近出现 / 档案治理）。开发者模式开启时，每行可二次确认触发 `reset_viewer_impression` 或 `delete_viewer_profile`；开发者模式关闭时按钮禁用，后端仍会再次拦截权限。
- `私信 dm`：占位页（即将上线，对应预留模块 `bili_dm_ingest`）。
- `自动化 automation`：占位页（即将上线，对应预留模块 `automation_ops`）。
- `⚙设置 settings`：平台参数。「节奏与安全」卡（dry_run / 自动急停 / 冷却秒数 / 队列上限 + 保存设置 / 清空队列）+ **「档案存储」卡**（当前只读展示插件默认目录；自定义入口暂时屏蔽，见「数据边界」）+ 高级状态（队列 / 安全门 / 最近 audit）+ 模块总览表 + 开发者模式开关。
- `开发者沙盒 dev`：仅开发者模式开启时出现。UID/URL 调试、只查询资料、模拟弹幕、请求结果、独立的最近沙盒记录和清空沙盒记录。

**「一张嘴」切分**：功能级参数（开关 / 强度 / 去重…）跟功能走、进「直播间互动」功能卡；平台级参数（dry_run / 节奏 / 队列 / 急停 / 模式）留「设置」。`live_enabled`（开启弹幕锐评）是功能级开关，**单一真相源 = 弹幕锐评卡的绿色卡头开关**（设置页不再重复）。

新增 UI 文案必须同步 8 个 locale 文件。

**模块卡错误边界**（兜底层④，可靠性第一原则）：`modulesSection` 里每张互动模块卡都经 `ModuleRenderBoundary` 渲染——hosted-ui runtime 无 class 组件 / `componentDidCatch`，故用 `try/catch` 包同步渲染调用，未来任意第三方模块的 `config_schema` / 自定义渲染抛错只塌成一张降级卡（`panel.modules.renderError` 文案 + degraded 徽章），不黑屏整盘。配合 `ModuleRegistry` 的 degraded 隔离（层①），构成「一个模块炸了不搞砸直播」的完整保证。详见 `docs/ui-architecture.md` §4。

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

截至 2026-07-12：`uv run pytest plugin/plugins/neko_roast/tests -q` → **1243 passed**；CLI check **0 error**（6 条模板 warning 允许）。当前允许存在模板级 warning（插件目录不是独立 git 仓库、无独立 `.github` / `.vscode` 配置），**不能存在 error**。

> 注：`plugin/tests/unit/server/test_plugin_ui_query_service.py` 是 host 侧测试，不在 neko_roast 验证范围内；跨模块禁碰范围以 `AGENTS.md` 为准。

触碰 UI 或打包兼容入口时，还必须确认 `ui/panel_compat.tsx` 保持完整面板功能：允许 `@neko/plugin-ui` import / hooks，但不得包含相对 import、`window.NekoUiKit` 或 `__modules`。`test_hosted_ui_manifest_entry_is_main_branch_compatible` 是这条规则的 smoke gate；不要用最小 fallback 面板替代完整入口。

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
- 若涉及任何成本：Decision Points、已拍板选项、成本预算和后续观测方式。

按改动类型更新：

- 用户可见流程：更新 `docs/quickstart.md`。
- 架构、模块、pipeline、数据边界、协作规则、测试门禁：更新本文档。
- 新人阅读路径：更新 `docs/developer-guide.md`。
- 阶段目标和下一阶段顺序：更新 `docs/live-center-roadmap.md`。
- UI 架构、Hosted UI 约束、`panel_compat.tsx` 兼容入口策略：更新 `docs/ui-architecture.md`。
- Agent / reviewer 硬规则：更新 `AGENTS.md`。
- 宿主 / SDK 侧历史问题：更新 `docs/devlog.md`。

## Message Plane 预算

头像进入 `push_message(parts=[{"type": "image", ...}])` 前必须经过 dispatcher 压缩，目标是低于 message plane 的内联 payload 预算（`MESSAGE_PLANE_PAYLOAD_MAX_BYTES`，默认 256KB；注意 wire payload 同时带 base64 与遗留 `binary_data`，实际占用约为原始 JPEG 的 ~2.3 倍）。若压缩后仍然过大，本次应省略 image part，改为纯文字锐评请求；不要为了保留头像而让整条 `respond` 被 ingest 丢弃。

历史坑（已修）：wire payload 的遗留 `binary_data` 字段是原始 `bytes`，而 message_plane PUB 端用 `json.dumps` 发布——`bytes` 不可 JSON 序列化会抛错并被上游 `except` 静默吞掉，导致**任何带图 `push_message`（不止本插件）都到不了 main_server**，表现为 UI 显示 queued 但猫猫无反应。已在 `plugin/message_plane/pub_server.py` 用 `json.dumps(default=...)` 把 bytes 转 base64 修复（消费端读 `parts[].binary_base64`，不受影响）。

## 直播语境提示词

`core/instructions.py` 里的直播提示词只保留历史恢复和显式兼容入口；正常直播不再先用 `ai_behavior="read"` 注入“猫猫正在直播”的常驻场景。参考 xTLM 的分层时，只复用它的事件 push、优先级、聚合/覆盖思路：稳定工具规则才适合长期注入，直播主题、房间标题、独播/同播角色和当前弹幕都必须留在单次事件 prompt。

关闭插件时不能假设模型会自动忘掉历史版本注入过的常驻场景；必须发送 `NEKO_ROAST_RESTORE_INSTRUCTIONS`，用新的 `read` 上下文覆盖直播状态。恢复消息同样只走 `NekoDispatcher`，不要在 runtime、module 或 UI action 中直接调用 `plugin.push_message()`。
开发者模式是独立的调试上下文；退出开发者模式只发送 `NEKO_ROAST_DEVELOPER_RESTORE_INSTRUCTIONS`，不要误发完整插件关闭恢复语境。

维护时不要只给字段说明。需要保留“猫猫是直播间同播伙伴，不是后台系统或插件播报员”的场景，让模型把弹幕当作直播现场互动来接话。即时事件提示词可以包含 UID、昵称、弹幕、强度、直播模式等结构化字段，但输出要求必须强调自然短句、不要复述字段、不要解释流程。
## Douyin Live Bridge

The Douyin live input path is a read-only provider bridge owned by `modules/douyin_live_ingest`. `modules/live_bridge` owns the provider-neutral localhost WebSocket transport and bundled-process lifecycle, while `core/runtime_douyin_auth.py` owns encrypted cookie import, validation, status, and deletion through the `douyin` credential namespace. `modules/douyin_identity` projects only sanitized stable identity fields.

Room references are limited to supported `live.douyin.com` URLs or bounded room tokens. The local transport accepts only loopback WebSocket endpoints, bounds message size and timeouts, uses ping/pong deadlines to detect half-open connections, and maps external bridge payloads through `bridge_adapter.py` and `event_model.py`. Process cleanup and port readiness probes run off the async runtime thread. Windows stale cleanup is limited to the exact PID recorded by this plugin for the bundled executable; it never scans and kills every matching executable. Routable events are published to EventBus and continue through the normal pipeline, `safety_guard`, and `neko_dispatcher`; status-only events update module status without producing NEKO output.

Cookies are encrypted by `CredentialStore` and never enter event payloads, public status, audit detail, logs, or UI. Event normalization retains only bounded public identity, room, text, and support-event fields. Opaque UIDs are accepted by shape rather than rejected for incidental words such as `token` or `signature`; credential-shaped values still fail the UID character contract. `ViewerEvent.source` remains `live_danmaku` for every provider event because it is the pipeline, permission, and connection source; gift, guard, and super-chat routing is carried by sanitized `raw.event_type`. Missing bridge executables, invalid rooms, unavailable metadata, exhausted retries, and absent credentials degrade to sanitized `unsupported`, `disconnected`, or logged-out status instead of bypassing the pipeline.

Focused validation:

```powershell
uv run pytest plugin/plugins/neko_roast/tests/test_douyin_bridge.py -q
uv run pytest plugin/plugins/neko_roast/tests -q --maxfail=1
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```

The bundled bridge metadata is Windows-only and does not include a fallback network client. To roll back, unregister `douyin_live_ingest` and `douyin_identity`, stop the local bridge supervisor, and leave the encrypted `douyin_credential.*` files unused. Bili ingest, EventBus, pipeline, safety, dispatcher, and viewer stores remain unchanged.

直播输出 prompt 的回复合约集中在 `modules/_prompt_context.py` 的 `short_reply_rules()` 与 `adapters/output_contract_bridge.py` 的 metadata：默认仍是短 TTS 友好回复，普通 `danmaku_response` 保持短句、只接当前弹幕且不主动追加追问；但当当前弹幕明确要求“讲笑话 / 讲讲 / 展开 / 为什么 / 怎么看 / 起外号”等可展开内容时，dispatcher 会标记 `reply_length_mode=expanded` 并把该次 `danmaku_response` 上限提高到 56 字，允许两句把包袱或解释讲完整。房间近期弹幕形成共同主题时，`danmaku_response` 可标记 `reply_length_mode=room_bridge` 并使用 48 字上限，允许当前回答后加一个短房间桥接，但仍不得把短问候、表情、@其他观众或空短反应放长。`danmaku_response` 还会调用 `core/meme_knowledge.py` 读取 `data/meme_knowledge.json` 做插件内静态热梗检索，并把命中的结果作为可选提示块注入 prompt，同时通过 `meme_hint_ids` / `meme_hint_tags` 暴露调试 metadata；该 JSON 是第一版人工维护入口，坏 JSON、缺字段或重复 id 会被加载器降级/跳过，不得影响直播输出。该提示只用于“自然能接上时加一点调味”，不得解释梗来源、堆多个梗、盖过当前弹幕本意，且不联网抓取外部热榜。`avatar_roast` 和 `live_support_events` 仍保持短句，避免首评锐评、礼物 / SC / Guard 致谢拖成长段；`warmup_hosting` / `idle_hosting` / `active_engagement` 使用 host 规则，允许一个很短、具体、低压力的接话钩子，但仍不能扩成主持词、计划、观众问卷或多句铺陈。新增开口模块时必须复用对应类别的合约，并补契约测试锁住。

直播连续性必须遵守“接明确线程，不继承私聊”的边界：当前弹幕永远优先，recent context / same-viewer context 默认只作为 anti-repeat 和 spent material；只有当前弹幕明确延续同一玩法或同一话题时才能继续。连续玩法的最小已落地案例是成语接龙：近期房间上下文出现“成语接龙”后，后续 4 字中文弹幕可被 `danmaku_response` 识别为 `idiom_chain_turn`，必须继续游戏，不能问“为什么说这个”。后续新增小游戏或连续玩法时，应补对应 profile / metadata / prompt contract 测试，不要把它做成长期画像或跨场记忆。

普通弹幕里的礼物 / SC / 上舰声称只是不可信文本 claim，不能升级成 support event。`event_signal`、recent room theme、active thread topic、`danmaku_response` metadata 都只能把 `event.raw["event_type"] in {"gift","guard","super_chat","sc"}` 当成真实 support 事件；“我投喂了超级大火箭”“送了灯牌”这类普通 danmaku 最多按玩笑接住，不得触发真实感谢、贡献榜、礼物运营或房间主题漂移。这个边界必须同时存在于 prompt 和出声前质量守门：`danmaku_response` 会标记 `viewer_claimed_support=unverified_danmaku_claim` / `support_claim_contract=unverified_danmaku_claim_no_thanks`，`live_output_quality` 发现模型仍输出“谢谢/感谢 + 礼物/火箭/SC”等组合时必须强制 fallback；真实 `live_support_events` 不受这个假礼物拦截影响。

独播模式是台前直播，不是后台私聊延续。所有会开口的模块（`danmaku_response` / `avatar_roast` / `warmup_hosting` / `idle_hosting` / `active_engagement`）都必须禁止 owner、master、operator、backstage human、carbon-based human、private chat、pre-stream relationship memory 等关系泄露；`solo_stream` 中的 “you” 指当前观众或直播间，不指隐藏操作者。输出必须是可直接播出的口语，不得包含括号动作、舞台动作旁白或角色扮演注释；质量守门发现这类文本时必须回退。
