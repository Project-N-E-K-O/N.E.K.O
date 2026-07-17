# NEKO Live（neko_live）开发总结与路线图

> 本文只记录阶段定位、完成状态和下一阶段路线。架构规范、协作规范和测试门禁以 `development.md` 为准；文档职责矩阵以 `docs/README.md` 为准；宿主 / SDK 历史问题以 `devlog.md` 为准。
> 更新日期：2026-07-15

---

## 1. 定位

`neko_live` 的产品名是 **NEKO Live**，历史代号是「猫娘锐评」，架构定位是**直播中心 (Live Center)**：把主播直播的全生命周期接进 NEKO——开播 → 直播间互动（弹幕 / 进场 / 关注 / 礼物 / SC / 舰长）→ 私信 → 主播侧自动化（猫猫操控电脑）。

"首评观众锐评"（观众首次发弹幕 → 猫娘按人设锐评其 B站昵称+头像）只是**第一个落地的垂直切片**。所有未来能力以 neko_live 的**内部模块**形式集成，不做跨插件宿主。

---

## 2. 架构入口

当前 v0.1 闭环是：

```text
Live Ingest → EventBus → Selection → Roast Pipeline → Runtime → Dashboard
```

详细分层、模块边界、数据边界、pipeline、EventBus 和输出约束不在 roadmap 中重复维护；以 `development.md` 为 Canonical Source。UI 贡献模型以 `ui-architecture.md` 为准。

---

## 3. 当前产品方向

当前产品优先级转向 **Independent Mode（猫猫独立直播）**：先验证 NEKO 能否在 10-50 人直播间里独立撑起 30 分钟不尴尬的直播。

Independent Mode 的产品命题、Slice 顺序、MVP、非目标和内测节奏以 [`independent-mode-product-plan.md`](independent-mode-product-plan.md) 为 Canonical Source。roadmap 只保留方向指针，不复制完整产品计划。

当前阶段先做：

1. Live Status / 开播前检查 / 为什么没说话：基础能力已落地并完成首轮验收。
2. Idle Hosting / 冷场陪播：状态推导、手动触发和自动触发基础能力已落地并完成首轮验收。
3. Pacing Control / 节奏控制：quiet / standard / active 三档基础能力已落地并完成首轮验收；当前三档会同时影响 quiet/idle 状态阈值、Idle Hosting 最小间隔和独播首评节流窗口。
4. Danmaku Response / 后续弹幕接话：作为 Active Engagement 前的过渡 Slice，已在当前开发分支接入。同一 UID 首次出场仍走 `avatar_roast`，后续普通弹幕走 `danmaku_response`。
5. Active Engagement / 主动营业：已接入保守 v0，只在猫猫独播的安静状态下触发一次轻话题；支持自动触发与手动触发，不接 Gift / SC / Guard（这些事件由 `live_support_events` 单独处理）。
6. Warmup Hosting / 开场暖场：已接入猫猫独播开场状态，避免开播第一句被当成冷场陪播。
7. 2026-06-24 已完成第一轮真实猫猫独播验证：真实直播间连接、真实输出、后续弹幕接话、主动营业和冷场陪播均跑通；当时礼物 / 灯牌 / 上舰类信号已被 ingest 捕获但仍可能走普通弹幕路径，后续已在 Live Feel Pack v1.6 收口为 signal-only。已离线补齐短回复约束、弹幕后主动营业等待、首评 / 接话 / 冷场 / 主动营业结果标签和 Gift Signal v0 标记。
8. 2026-06-25 已完成第二轮约 1 小时真实独播验证：主链路仍可用，`danmaku_response` 后续接话有效，插件侧延迟多为 0-1000ms；当时暴露 `voice_play_end` / playback gate watchdog 反复卡住，以及 dispatcher 对 `danmaku_response` 曾附带头像 image part、导致后续接话被头像污染。后续开发包已把头像 image part 限制到显式 opt-in 请求，补了浏览器音频已播空时主动释放 backend playback gate 的修复，并加入独播首评节流和 `live_enabled` 测试隔离。这里是历史验证记录；当前发布验收状态与暂停决定以本文件“当前执行顺序”为准。
9. 2026-06-25 已完成第三轮真实独播验证：播放与后续弹幕接话更稳定，回复长度明显收敛，`active_engagement` 确认能进入 pipeline 并输出；但直播效果暴露新主线问题：主动营业频率偏保守、话题吸引力不足，`idle_hosting` 在真实测试中未被充分触发。下一步从“链路正确”转向 **Independent Pacing v1 + Active Engagement v1**：区分观众活动与 NEKO 自己输出，保证真冷场能进入 `idle_hosting`，并让主动营业给出更具体、更容易接话的直播话题。
10. 2026-06-25 已落地下一次直播前准备包的一部分：`live_state` 已拆分观众活动与 NEKO 输出，`idle_hosting` 不再被猫猫自己的主动输出永久挡住；`active_engagement` 已接入轻量 topic material、话题形状轮换、shape task、reply hook、互动意图、接话路径、分离等待字段和 recent result 可观测字段，标准档最小间隔已收紧到约 90 秒，活跃档约 60 秒。当前开发包还扩大了主动营业内置兜底话题池和去重窗口，并过滤“没人说话 / 弹幕少 / 冷场 / suddenly quiet”这类房间沉默素材；首评 `avatar_roast` 的同条弹幕、过期 recent danmaku、skipped / failed 弹幕、未点名请求、纯反应弹幕和测试 / 运行反馈都不再用于主动营业开题，同一 UID 短窗口连续提供 3 条有效素材时也不再继续拿它开题，避免主动营业重复首评、翻旧弹幕、放大被跳过内容或被单个观众刷屏带偏。下一次直播重点验证真实 no-danmaku 窗口是否能进入 `idle_hosting`，以及主动营业话题是否更容易让观众接话。
11. 2026-06-26 已完成第四轮真实独播验证：`danmaku_response`、`idle_hosting`、`active_engagement` 在同一长跑中均有 `pushed` 输出，观众档案清理后能重新写入，回复长度整体稳定，主链路和安全状态可持续运行；但产品侧暴露新主线：主动营业素材仍过度依赖近期弹幕 / 表情包，观众会感到复读，弹幕中的 `@` 其他观众也需要区分是否真的在喊 NEKO。下一步优先做 Active Engagement 多元化、mention parsing、反复读 / 话题形态去重和最近结果档案展示澄清，不推进 Gift / SC / Guard。
12. 2026-06-27 已落地 Live Feel Pack v1.5：主动营业不再连续依赖 recent danmaku 作为开题素材，`@其他观众` 会被识别为 viewer-to-viewer mention 并从主动营业素材中过滤，主动营业会避开连续相同 topic shape / intent，首评成功后的 recent result profile 快照会同步 `roast_count`，`monitor_live.ps1` 也能输出 `topic_viewer_mention`、`topic_source_streak`、`topic_shape_guard` 和 `topic_shape_bias` 等信号。下一次直播重点验证主动营业是否更有变化、是否还被观众感到复读，以及 `@其他观众` 时猫猫是否不乱插话。
13. 2026-06-27 已补 Live Feel Pack v1.6：Gift / Guard / SC 在专属模块完成前曾收口为 signal-only skipped result，不再误走 `avatar_roast` 或普通弹幕回复；当独播无观众活动且 `idle_hosting` 已连续输出 3 次后，`active_engagement` 可接管一次，避免冷场陪播长期复读同一种“空房间补位”；`monitor_live.ps1` 改用 solo readiness 的 `profile_count`，并对 `warmup_hosting` / `idle_hosting` / `active_engagement` 使用更严格的 60 字长回复告警阈值。
14. 2026-06-28 已完成下一轮真实独播验证：`solo_stream` 真输出状态稳定，最近 30 条均为 `pushed`，`avatar_roast` 仅占约 14%，`danmaku_response` / `idle_hosting` / `active_engagement` 都有实际输出，说明独播链路已经从“只会首评”推进到“能接话、能补位、能主动开口”。新的产品缺口是直播无弹幕窗口仍偏无聊：冷场陪播和主动营业虽然触发，但话题有时像模板主持或普通提问，缺少让观众一两个字就能接上的趣味钩子；同时最近窗口仍出现较多长回复告警。下一步优先做 Idle / Active Content Quality：短句化、趣味化、低门槛接话、减少“大家想聊什么 / 快来互动”式泛问。
15. 2026-06-28 已补 Live Feel Pack v1.7：`idle_hosting` 的内置主持节拍从 4 类扩到至少 8 类，增加一个字/一个词、小挑战、桌面二选一和小电台式轻观察；`active_engagement` 的内置 fallback 话题扩到至少 20 个，并为每个话题标注 `fun_axis` 和 `reply_affordance`，让 prompt 能明确这句话的趣味点和观众怎么接。运行态会尽量避开最近用过的 host beat / active topic 趣味轴与 reply_affordance，避免连续几次都停在同一种接话方式；监控也会输出 `recent_topic_axis_*` / `recent_host_beat_axis_*` 与偏科告警；素材测试会拦截“大家来互动 / 发弹幕 / 想聊什么 / get the chat moving”等模板主持诱导。下一次直播重点验证无弹幕窗口是否更不无聊、主动营业是否更像 NEKO 自己带节奏，而不是模板主持。
16. 2026-06-28 已补 Live Feel Pack v1.9：`idle_hosting` 与 `active_engagement` 现在共享 recent host material family 记账，二选一、一个字/一个词回调、小电台氛围、桌面/屏幕场景、主播力自测、轻吐槽和小挑战等内容族不会只在各自模块内去重；`monitor_live.ps1` 会输出 `latest_topic_family` / `latest_host_beat_family`、`recent_topic_family_*` / `recent_host_beat_family_*`，并在内容族过度单一时给出 `topic_family_bias` / `host_beat_family_bias`。下一场直播重点观察：猫猫是否还会隔着模块复用同一种开场、同一种“主播力/小电台/二选一/一个词”主持手法；如果仍无聊，下一步应继续扩内容族和现场素材，而不是先提高开口频率。
17. 2026-06-29 已补 Live Feel Pack v1.10：插件侧复盘口径不只看逐字相似，也应识别高信号内容家族换皮复读，例如小鱼干 / 奖励、特别企划、主播力自测、一个字 / 一个词 / 暗号回调、安静冷场，以及带明显短片段重叠的二选一、房间氛围、桌面场景、轻吐槽、小挑战。prompt 侧也会把 `topic_family`、`host_beat_family`、`spent_output_family`、`fun_axis`、`shape`、`intent` 视为已用素材，普通弹幕回应的真实输出也会被归类成已用旧梗，`idle_hosting` / `active_engagement` 选素材时也会优先避开最近真实输出已经用过的 spent-output family，避免“换了词但还是同一套主持手法”。下一场直播重点观察：`log_reply_repeat` 是否能抓到换皮复读，以及猫猫是否减少绕回旧梗。
18. 2026-06-29 已补 Live Feel Pack v1.11：长直播防复读窗口继续加长；当前插件侧保留 recent-output 复盘口径，callback negative examples 扩到最近 12 条；`idle_hosting` / `active_engagement` 的 key、fun axis、shape、intent、host material family 记账窗口也加长，减少 30~60 分钟后又绕回旧主持手法的概率。主动营业 fallback 在严格避让后仍会优先选择未用过的 key，避免因为标题相似就直接复用同一个兜底话题。下一场直播重点观察：猫猫是否还会隔十几句后回到“小鱼干 / 小电台 / 主播力 / 一个词 / 二选一”等旧梗。
19. 2026-06-29 已补 Live Feel Pack v1.12：把“观众接话模板复读”单独纳入插件侧复盘口径、未来输出合约和运行时素材族检测。即使不是逐字复读，只要连续绕回“想听什么 / 想看什么 / 聊点什么 / 发弹幕 / 接一句 / 互动 / 扣 1 / 吱一声 / 冒个泡 / 给点反应 / 还在吗 / 有人吗 / 在不在”等观众召唤套路，也应被视为 audience-prompt family 的换皮复读；真实输出里的这类句子也会标记为 `spent_output_family=audience_prompt`，让 `idle_hosting` / `active_engagement` 下一轮选素材时优先避开。下一场直播重点观察：猫猫是否减少“换个说法让大家发弹幕”的主持模板感。
20. 2026-06-29 已补 Live Feel Pack v1.13：插件侧通过 `live_reply_contract=short_tts_line`、`neko_live_output_policy`、recent-output negative examples 和 monitor 复盘口径减少直播短回复污染下一轮生成；宿主普通 AI turn 文本隔离不属于当前插件改动范围，插件不直接修改主程序核心。下一场直播重点观察：后半场是否减少“接着上一句展开”“围绕上一句换皮续写”的污染感。
21. 2026-06-29 已补 Live Feel Pack v1.14：把 `cross_server` 普通 memory / analyzer 汇合点和热切新 session 的 `message_cache_for_new_session` 记录为宿主侧风险边界。带 `live_reply_contract=short_tts_line` 的 `gemini_response` 和 turn end 可能污染普通上下文，但插件只提供 metadata、recent-output 约束和监控线索，不直接写这些宿主路径。
22. 2026-06-29 已补 Live Feel Pack v1.15：`active_engagement` 的近期弹幕和 B 站公开素材会先做轻量 topic profile，识别它更适合二选一、轻吐槽、小挑战还是氛围立场，并把 `preferred_shape`、`fun_axis`、`reply_affordance` 和 hint 一起传给 prompt。这样主动营业不再只拿“一个标题”硬转话题，而是优先形成一个可被观众一两个字接住的互动格式。下一场直播重点观察：热榜 / 近期素材触发时，猫猫是否更少泛问“聊什么”，更多给出具体 A/B、小挑战或轻吐槽钩子。
23. 2026-06-29 已补 Live Feel Pack v1.16：`idle_hosting` 和 `active_engagement` 的素材都增加 `live_column`，用于标记 NEKO 自有小栏目格式，例如 micro poll、tiny verdict、tiny radio、room thermometer、one-word callback。该字段会进入 prompt、recent result 和 recent interaction context，方便下次直播复盘猫猫到底在用哪类主持格式；它不新增 UI 页面、不改变触发频率，也不允许绕过 Safety / dry_run / dispatcher。下一场直播重点观察：无弹幕窗口和主动营业是否更像“NEKO 自己的小栏目”，而不是普通主持人泛问。
24. 2026-06-29 已补 Live Feel Pack v1.17：继续扩无弹幕窗口和主动营业的内容质量池，冷场陪播素材当时不少于 24 条、当前已扩为 JSON 维护 32 条，主动营业 fallback 素材不少于 36 条，并新增 NEKO 小法庭、尾巴状态、桌面守护神、两字暗号、灯光滤镜、猫猫天气预报等更具体的低门槛接话格式。下一场直播重点观察：无人发言时是否不再反复绕回同一种小电台 / 选题 / 召唤观众话术，以及观众是否更容易用一两个字接上。
25. 2026-06-29 已补 Live Feel Pack v1.18：冷场陪播从“随机抽素材”推进为轻量连续策略：第一次冷场优先 `settle`，第二次优先 `column`，第三次及以后优先 `callback`，再交给已有 idle-to-active 交接阀判断是否切主动营业。主动营业结果也新增 `topic_pack`，用于区分 micro poll、NEKO verdict、room mood、room observation、viewer callback、micro challenge 等节目包。下一场直播重点观察：连续无弹幕时猫猫是否有层次地从轻观察走到小栏目 / 回调，而不是同一种话术反复抽卡。
26. 2026-06-29 已补 Danmaku Response Quality v2：`danmaku_response` prompt 会标记当前弹幕的轻量 `danmaku_profile`，区分 `viewer_to_viewer_mention`、`question`、`emoji_or_reaction`、`short_line`、`normal_line` 和 `empty`。普通弹幕接话现在更强调“只接当前这句”：短反应不展开，问题先直接回答，`@其他观众` 不当成喊 NEKO，`@猫猫` 仍正常接住，同一观众历史只作为防复读材料。recent result 与 `monitor_live.ps1` 会输出 `latest_danmaku_profile` / `latest_danmaku_reply_shape`，方便直播中确认分类是否符合预期。下一场直播重点观察：普通后续弹幕是否更短、更贴当前句，是否减少把观众互相 @ 或旧话题误接成新回复。
27. 2026-07-02 已补 Runtime Timeline / `trace_id` / Monitor emission / CI gate 收口：事件级 `trace_id` 已贯穿 live payload、`ViewerEvent`、`InteractionResult`、recent result 与 `live_explain`；Dashboard 与 monitor 均可看到 privacy-safe timeline stage/status/route/reason；`live_support_events` 已让 Gift / SC / Guard 进入短句致谢 handler；测试大文件已按责任域拆分，主仓 `Plugin Tests` workflow 新增 `NEKO Roast gate (Windows)`，自动跑 `uv run pytest plugin/plugins/neko_live/tests -q` 与 CLI check。下一步不再补链路地基，优先做真实直播验证、UI 模块化、P4 画像治理收口和旧 `bilibili_danmaku` 最终删除前置清理。
28. 2026-07-03 已补观众画像治理 v0.3：`viewer_store` 长期保存安全派生印象字段（常聊话题、接梗提示、互动风格、回复偏好、短摘要、避坑提示），prompt 私有提示和 Dashboard 只读投影均不保存 / 展示原始弹幕、raw payload、头像 bytes/base64、cookie/token/signature；开发者模式新增单 UID 档案删除与印象重置动作，并在观众档案表逐行提供二次确认按钮，便于修正错误画像而不必清空整场测试数据。画像质量层已补 `profile_freshness` / `memory_use_rule`：单次话题或玩梗只算证据，不进稳定 top topic/joke；过期画像会降权，prompt 要求当前弹幕优先、不要当众复述档案。下一步继续做真实直播验证、画像管理 UI 体验打磨、`watch_time` / `contribution_rank` 等非 raw 指标，以及旧插件最终退役前置清理。
29. 2026-07-07 已补 Live Feel Pack v1.19 / 交接素材包：热梗知识库移入 `data/meme_knowledge.json`，当前 36 条，由 `core/meme_knowledge.py` 做插件内离线检索，供 `danmaku_response` 和冷场 `meme_query` 作为可选调味提示，并通过 `meme_hint_ids` / `meme_hint_tags` 暴露调试 metadata；冷场陪播素材移入 `data/idle_hosting_beats.json`，当前 32 条，由 `core/live_content_host_catalog.py` 加载，坏 JSON、缺字段或重复 key 会跳过/回退到 legacy Python 素材。该包不联网、不改宿主核心、不强制猫猫用梗；下一步交接重点是素材人工维护、真实直播复盘与 UI/画像治理，不推进抖音 transport。
30. 2026-07-07 已完成回复链路收缩交接：直播专用最终输出治理不再推进到主程序核心，插件只通过 `NekoDispatcher`、prompt contract、request metadata、recent-output 负例和 Dashboard / Monitor 只读投影约束直播效果；`live_events` 内置选择性回复，`activity_level=quiet` 会派生更严格的 `reply_selection_policy=quiet`，`standard/active` 仍只跳过低价值弹幕，不新增与直播风格重复的 `reply_selection_mode`。交接入口见 `handoff-2026-07-07.md`；下一步先真实直播复盘低价值弹幕过滤、头像/UID 过度锐评、prompt token 和延迟，再决定 UI 是否展示更多只读解释字段。
31. 2026-07-07 已完成 B 站 ingest 写能力收口：`modules/bili_live_ingest/danmaku_core.py` 删除旧 `DanmakuListener.send_danmaku()`，并用 `test_bili_live_ingest_stays_readonly` 锁住只读边界，防止 `msg/send`、csrf 写 payload 或 `danmaku_max_length` 这类发送配置回流到监听器。`bili_live_ingest` 只负责连接、查询、事件归一和 EventBus 发布；未来写弹幕、评论、动态、私信等能力必须单独进入 `bili_write_tools`，并和 P5 登录态 / 权限 / 安全评审一起拆 PR。
32. 2026-07-14 已完成主播面板重构：普通导航收敛为“控制台 / 直播间互动 / 观众 / 设置”，开发者工具按模式条件显示；控制台承载账号、直播间二次确认、节奏与唯一开始 / 结束直播主动作；互动模块使用稳定卡片和详情弹窗；观众页分离本场数据与安全画像；设置页分离安全、隐私和帮助。
33. 2026-07-15 已补齐账号兜底运行时契约：`connect_live_room` 的 UI、action schema、runtime API 和压力工具统一传递严格布尔值 `allow_accountless`；B 站登录缺失、失效或无法校验时默认 fail closed，只有本次调用显式确认才进入 `limited_accountless`，连接快照公开 `auth_mode`，停止连接即清空且不落盘；抖音明确拒绝匿名兜底。聚焦回归覆盖默认拒绝、显式允许、有效登录优先、校验异常、参数类型和平台边界。
34. 2026-07-17 已完成当前礼物调度器离线收尾：`live_support_events` 现在以有界优先队列串行调度支持事件，隔离 provider 事件去重、连击单调更新、迟到结束包、队列淘汰后的可重试性、同观众同礼物的安全轻量聚合、审计写入故障和切场 / close 后迟到任务；主播主题与直播节奏弹窗也会从成功保存后的本地配置重新打开，不再回退到旧值。持久礼物账本、诊断事件日志、完整拥塞 / 公平 / 熔断状态机仍是后续独立评审的长期目标，不属于本阶段收尾。

Gift / SC / Guard 已有短句致谢 handler，但贡献榜、权益、朗读流程仍视为增强项，不作为 Independent Mode 成立条件。

---

## 4. 已完成进度（验证状态逐项列出）

| 阶段 | 内容 | 验证 |
|---|---|---|
| **传输修复** | `message_plane/pub_server.py`：wire 的遗留 `binary_data` 是原始 bytes，`json.dumps` 撞 bytes 抛错被静默吞 → **所有带图 push_message 都到不了 main_server**。改 `json.dumps(default=...)` 转 base64 + 失败记 debug。 | PR [#1843](https://github.com/Project-N-E-K-O/N.E.K.O/pull/1843)，CI 全绿；已写进 main 工作区 |
| **P0 底层硬化** | ① `RoastConfig.dry_run` 安全测试态（pipeline 照跑、`push_roast` 短路不投猫猫）；② `ViewerStore` 加锁防并发丢更新；③ 连接层冒烟 | 单测 + 真机 |
| **P1 吞并连接层** | `danmaku_core.py`(DanmakuListener) + `livedanmaku.py` 吞并进 `modules/bili_live_ingest/`；`BiliLiveIngestModule` 持有监听器（`on_danmaku` fire-and-forget 喂 pipeline）；`connect/disconnect_live_room` 启停真实监听 | 真机：连 81004 → 6 真实观众走完整 pipeline（按 UID 抓真实头像） |
| **DoD** | **真实直播间新观众首条弹幕 → 猫猫全自动开口锐评其昵称+头像** | 真机：dry_run 关 → main 日志见 vision 模型 + send_lanlan_response |
| **P2-T2.1 限流** | `safety_guard.before_output(event)` 按 `rate_limit_seconds` 控最小锐评间隔（直播态生效、沙盒豁免） | 单测 + 真机 |
| **富模型修复** | `livedanmaku.from_danmaku`：`info[7]`（int 大航海等级）被当列表 → 任意弹幕 TypeError 被吞、`on_event("DANMU_MSG")` 永不触发，已修 + 全下标加守卫 | `tests/test_livedanmaku.py` 9 用例 |
| **人气值 UI** | 后端透传的 `viewer_count` 之前没在面板渲染 → `panel.tsx` 加"人气值"卡 + 8 locale | **无需 rebuild 前端**：panel.tsx 由 plugin-manager 用 sucrase **运行时转译**（`hosted/tsxRuntime.ts`），后端已确认供含人气值卡的源码（`hosted-ui/source` 含 `viewer_count`/`panel.stats.viewers`）→ UI 里(重)开 neko_live 面板即见；待肉眼确认 |
| **P2.5 事件中枢** | 激活 `live_events` 普通弹幕中枢：富模型 `on_event` 接入 + `get_score` 开窗择优 + 首评即时；轻量重复回调退役防双锐评；新增 `safety_guard.output_cooldown_remaining()` 对齐窗口与限流冷却。Gift / SC / Guard 已拆到 `live_support_events.scheduler`，不再和普通弹幕同窗竞争 | 单测与契约覆盖弹幕中枢、支持事件独立接线、真实性、去重、连击、优先级及队列边界；真机已验证弹幕窗口择优，支持事件仍需后续受控样本校准 |
| **配置写竞争（插件侧免疫 + host 修复已进）** | `runtime.update_config` 反转为「先内存生效 → 带预算（4s）尽力持久化、超时/失败不回滚不阻塞」+ `asyncio.Lock` 串行化；host/core 修复 `Fix plugin host config and data root handling (#1884)` / `08b317f6` 已进入当前 `Roast` 分支，插件侧兜底继续保留 | 契约新增 2 用例（`update_config`/`connect` 持久化卡死不阻塞）；host/core 切片 `plugin/tests/unit/core/test_host_storage_layout_env.py` + `plugin/tests/unit/sdk/plugin/test_sdk_v2_plugin_base.py` 已用于验证修复依据；**真机✓**（原 500 的 `update_config{dry_run}`→OK 4.1s+`config_persist_timeout`，`connect`→OK 4.5s 真连上） |
| **旧 bilibili_danmaku 待退役** | 连接、解析与扫码登录能力已独立迁入 NEKO Live，当前无运行时导入；但旧插件仍是可加载、可手动启动的完整插件，README/manifest 尚无真实弃用横幅，并保留平台内容读取、弹幕/评论/动态/私信写入、历史查询、用户管理和独立分析能力。旧目录有 41 个 tracked 文件，另有构建注释和通用测试夹具引用 | 只能确认直播主链路已独立，不能宣称完整功能对等；删除前须先完成能力清单与逐项迁移/替代/放弃决策 |
| **A1 anti-352 for lookup** | `lookup_room_status` 加临时 buvid3（首页 Set-Cookie + 6h 缓存）+ 浏览器 headers + 撞 -352 刷新重试一次 + 成功 60s 缓存；只降频率，彻底消除需登录态(P5) | 契约 +3 用例；**真机 2026-06-17**：buvid3 能抓到、机制通，但本机重度风控 IP 4 房间仍全 -352 → 匿名不足，需 P5 登录态 |
| **A2 直播间链接输入** | `contracts.parse_room_id`（数字 / 链接 `live.bilibili.com/<id>`）；from_mapping + update_config + connect/lookup/set 三入口都过它；action `room_id` schema 收 string、面板送原始串；占位符 8 locale 同步 | 契约 +3 用例；**真机 2026-06-17 ✅**：action 传 4 种链接形态均正确解析房号（含 query/h5）；面板侧待前端重开面板肉眼验 |
| **P5 登录态（登录部分）** | Fernet 加密凭据 store + 扫码登录服务（移植旧插件）+ runtime 4 action + 凭据接进 identity/ingest/lookup（**根治 -352、恢复头像**，credential=None 时零回归）+ 面板登录卡 + 8 locale；本地注销 | 契约/store +6 用例；**真机 2026-06-17 ✅**（用户扫码本人账号 uid 1408555810）：同房 81004 登录前匿名 lookup -352、登录后 -352 彻底消失 + 头像抓取恢复（`has_avatar:true`）+ 凭据加密落盘可解密回环。私信/写能力留待后续；登录卡 UI 肉眼验为非阻塞收尾 |
| **UI 架构重构（6-tab 生命周期）** | 薄外壳 + 模块贡献：6 个一级页（控制台/直播间互动/观众/私信/自动化/⚙设置 + dev 条件追加）；`ModuleRegistry.setup_all/teardown_all` 逐模块 try/except 隔离 + `degraded` 标记；`BaseModule.config_schema()` 契约 + schema 驱动功能卡（boolean→Toggle / select→Select）；「一张嘴」切分（功能参数进卡、平台参数留设置）。契约文档 `docs/ui-architecture.md` | 单测 +4（`test_module_registry.py`）；契约 `test_panel_uses_six_top_level_tabs_in_order`；panel transpile OK |
| **观众档案本地 JSON 持久化** | 历史上用于绕开宿主 `store.enabled` 构造期冻结 bug（见 `docs/devlog.md`），当前作为简洁可审计的档案写入边界继续保留：`viewer_store.py` 改写本机 `viewer_profiles.json`（原子写 tmp+os.replace + asyncio 锁 + 不可写回退默认目录 + audit）；dashboard 暴露实际在用的 `viewer_store` 路径和可写状态，替换失败会清理 tmp。#1884 已修复 host 数据根刷新；`viewer_store_dir` 自定义入口按当前产品范围继续隐藏 | 默认目录、自定义目录、回退读取、嵌套目录状态、路径冲突、失败 tmp 清理与档案治理回归通过；自定义目录 UI 不在当前范围 |
| **事件中枢地基（EventBus 真订阅分发）** | 把接入与处理解耦——provider ingest 把富模型包成 `LiveEvent` 统一信封发布到 `EventBus`；`EventBus` 提供隔离、归属与 audit。`live_events` 只订阅 `"danmaku"`，`live_support_events` 独立订阅 `"gift"` / `"super_chat"` / `"guard"`，确保每个事件族只有一个生产消费者。**这是「分发给其他开发者各写各事件 handler」的核心契约** | `test_event_bus.py`、listener lifecycle、live events 与 support scheduler 契约共同覆盖；rich event 经 bus 到唯一 handler，支持事件不会重复进入普通弹幕窗口 |
| **可靠性收尾（兜底层②④收口）** | ① UI 错误边界：`panel_components.tsx` 的 `ModuleRenderBoundary` 用 try/catch 包每张互动模块卡的同步渲染，单卡失败不黑屏整盘。② `ModuleRegistry.enable/disable` 对真实模块生命周期调用做隔离，单点失败标 degraded + audit | 地基、单测、契约和 panel transpile 已完成。普通功能偏好开关继续使用明确 runtime config gate，不把偏好开关误接成模块卸载；后续只有真实模块需要动态装卸时才使用 lifecycle API |

历史阶段测试基线（2026-06-20；当前基线以 `development.md`「测试门禁」为准）：`uv run pytest plugin/plugins/neko_live/tests -q` → **546 passed**；CLI check **0 error**（6 条模板 warning 允许）。`Plugin Tests` workflow 已在 `roast` 分支通过，新增 `NEKO Roast gate (Windows)` 自动运行 neko_live 测试套件与 CLI check；后续改动按 `development.md` 的协作规范拆分 Slice，不混入非本插件改动。

---

## 5. 关键决策与历史问题入口

本节只保留路线图相关的决策摘要。宿主 / SDK 侧历史问题、配置写竞争、storage layout、message plane 等事故记录以 `devlog.md` 和 `development.md` 对应章节为准。

- **吞并策略**：取 `bilibili_danmaku` 的**连接+解析层**（`danmaku_core`/`livedanmaku`，含匿名 WS、WBI 签名、临时 buvid3 反 -352 风控、zlib/brotli 解压、心跳、多服务器故障转移、断线重连）；**弃**其自带 LLM/orchestrator/memory（neko_live 走 `dispatcher → main_server` 统一人设）。参照系：弹幕姬 `copyliu/bililive_dm` 的小插件契约（4 事件 + 统一模型 + 故障隔离）作为未来扩展点设计蓝本。
- **弹幕不含头像**：B站 DANMU_MSG 无头像 URL；头像由下游 `bili_identity` **按 UID 抓取**。
- **配置写竞争（反复咬人）**：host 的 `update_own_config` 持久化曾偶发卡 10s 超时（咬过 dev 模式切换、disconnect）。`connect/disconnect_live_room` 已改为**内存直设 `live_enabled`**（gate/safety 共享同一 config 对象，即时生效）绕开；host/core 修复 `Fix plugin host config and data root handling (#1884)` / `08b317f6` 已进入当前 `Roast` 分支。
  - **2026-06-16 P2.5 真机验证时复现并确认更严重**：在「只重后端不重前端」的环境下（正是 §6 警告的触发条件），`update_config{dry_run}` 和 `connect_live_room`（其内部 `set_live_room` 仍走 `update_config` 持久化 `live_room_id`）**稳定** 500 / `Entry timed out after 10.0s`，且 `runtime.update_config` 的 except 内存兜底**没机会跑**（host 在兜底前就杀了 entry，audit 无 `config_persist_failed`）。即 connect 当前也会被这个 race 卡住，不止「偶发」。
  - **2026-06-16 插件侧根治（症状消除，已真机验证）**：`runtime.update_config` 反转为**先内存生效（`_activate_config`，runtime 行为以内存为准、即时权威）→ 再带预算尽力持久化**（`_persist_config_best_effort`：`asyncio.wait_for(_, _CONFIG_PERSIST_BUDGET_SECONDS=4.0)`，超时记 `config_persist_timeout`、失败记 `config_persist_failed`，**都不回滚、不阻塞**）；并用 `asyncio.Lock` 串行化插件自身并发写。host 持久化即便异常，action 也在 ≤4s 内成功返回、配置已生效。真机：reload 新代码后，原本 500 的 `update_config{dry_run}` → **OK 4.1s + dry_run=True + audit `config_persist_timeout`**；`connect_live_room` → **OK 4.5s + 真连上**。#1884 已修复 host/core 侧配置与数据根处理，但插件侧兜底继续保留。
- **ws close 握手挂起**：`websockets` 的 `ws.close()` 等关闭握手可达 ~10s → `stop_listening` 用 `asyncio.wait_for` 限时（先 cancel task 再 bounded await），断开稳定 ~4s。
- **不阻塞接收循环**：`on_danmaku` 用 fire-and-forget task 跑 pipeline，并发由 `safety_guard.queue_limit` 兜底。
- **`rate_limit_seconds=0` bug**：`from_mapping` 用 `int(x or 20)` 把 0 吞成 20 → 限流关不掉，已改为显式 None 判断。

---

## 6. 运行与验证入口

运行步骤、action 调用、日志位置和用户操作不在 roadmap 中维护，避免和使用文档漂移：

- 用户/主播流程：见 `quickstart.md`。
- 开发者运行态和常用 action：见 `developer-guide.md`「开发环境 & 运行态」。
- 测试门禁：见 `development.md`「测试门禁」和 `AGENTS.md`「Required Checks」。

---

## 7. 路线图（短线已完，下面是长线，按需推进）

- **P2.5 事件中枢/事件族（地基）**：✅ **已完成当前地基**——接入富模型 `on_event`；普通弹幕由 `live_events` 做 `get_score()` 开窗择优与首评即时，Gift / SC / Guard 由 `live_support_events.scheduler` 独立完成真实性、去重、聚合、优先级与有界排队（见 development.md 对应章节）。`LiveEvent` 统一信封、EventBus 隔离分发和模块 lifecycle 均已落地。**剩**：更细的事件族产品能力，例如 SC 朗读、上舰欢迎、贡献榜或权益流程（P3+）。
- **P4 档案/记忆**：✅ **安全画像 v1 与普通用户治理已落地**（`viewer_store.py` → `viewer_profiles.json`；个性化默认开启、90 天惰性清理、普通用户开关/清空全部/单 UID 重置印象与删除档案）。当前版本明确不采集 `contribution_rank`、`watch_time`；真实直播只需校准现有画像质量和隐私说明。
- **P5 私信 / 平台写能力**：不属于当前产品范围。若未来立项，`bili_dm_ingest`（收）与 `bili_write_tools`（发）必须作为独立域重新做权限、登录态、风控和安全评审；现有扫码登录能力已经独立位于 `adapters/bili_auth_service.py`，不得依赖旧插件包。
- **P6 主播自动化**：不属于当前产品范围。若未来立项，须先拍板 `automation_ops` 的归属、授权模型、可撤销边界和成本；当前不执行浏览器或键鼠自动化。

---

## 8. 待拍板（动手前先定）

1. ~~**值优选策略**：爆量时全评 / `get_score` 优选 / 采样？~~ ✅ **已定**：`get_score` 开窗优选 + 首评即时（P2.5 已落地，见 development.md「直播事件中枢」）。
2. **`automation_ops` 归属**：仅在未来重新立项主播自动化时再拍板；当前不做浏览器或键鼠自动化。
3. ~~**登录态 cookie 怎么拿/存**~~ ✅ **已定**：B 站扫码登录凭据只经 `CredentialStore` 加密落盘，公开状态只回显安全账号信息；无账号兜底必须由本次连接显式确认。平台写能力若未来立项仍需独立权限评审。
4. **退役旧 `bilibili_danmaku`**（与 NEKO Live 同房间会双连冲突）：2026-06-16 只完成了旧 `from_danmaku` 字段错位修复；当前代码中的 README/manifest 并没有真实弃用横幅，插件仍可加载和手动启动。直播连接、解析和扫码登录已迁入；47 个公开入口与内部能力的迁移矩阵已于 2026-07-17 完成。当前先处理矩阵中的获批吸收项和独立插件取舍，完成前不得删除 41 个 tracked 文件。
5. ~~**配置写竞争根治**（host 级，可能与在途 WIP 相关）~~ ✅ **插件侧已根治症状**（`update_config` 内存先行 + 带预算尽力持久化，见 §5 与 development.md「配置持久化与写竞争」）；host/core 修复 `Fix plugin host config and data root handling (#1884)` / `08b317f6` 已进入当前 `Roast` 分支。

---

## 9. 已知限制

- 自适应焦点由 LLM 判断，非确定性；`pendant` 依赖 bilibili_api。
- B站协议会变（WBI / 风控），需跟进。
- `lookup_live_room` 的 HTTP 路径已做 A1 反 -352 降频（临时 buvid3、浏览器 headers、撞 -352 刷新重试一次、成功缓存），但重度风控 IP 仍可能失败；已把失败码翻成人话（`bili_live_ingest._friendly_lookup_message`：-352→"风控校验失败，稍后重试/换网络/登录"，并在面板 Alert 显示该 message 而非死写"请检查房间号"），**根治**需登录态（P5）。注意：查询失败 ≠ 监听失败，弹幕 WS 路径通常仍可连。
- 插件侧配置持久化仍按“内存先行 + 4s 预算”看待：host 持久化异常时配置仍内存即时生效，但**那一次的持久化会失败**（`config_persist_timeout`），即该次改动不落盘——stop/start 后可能还原成 `plugin.toml` 里的值。无竞争 / 无异常时应秒过。
- ~~富模型 `on_event` 尚未被 pipeline 消费~~ ✅ P2.5 已由 `live_events` 中枢消费，`on_danmaku`→pipeline 直连已退役；`medal_info` 字段顺序沿用旧实现，精确化留待事件族梳理。

---

## 10. ⚠️ 不要碰的在途 WIP

跨模块禁碰范围和 Reviewer 硬规则以 `AGENTS.md` 为准。路线图只保留下一阶段方向，不维护临时工作区状态。

---

## 11. 下一阶段 TODO（接手即可做）

按性价比 / 依赖排序。A 组是健壮性、可独立小步做；B 组是功能路线（详见 §7）。

### 当前执行顺序（2026-07-17）

1. ~~**补齐账号兜底运行时契约**：B 站未登录时只有调用方显式传入“本次允许受限无账号连接”才可继续；缺失、无效或无法校验的登录态默认 fail closed。该意图需贯穿 UI、action schema、runtime API、连接快照和压力工具，抖音不提供匿名兜底。~~ ✅ **已完成（2026-07-15）**。
2. ~~**统一开发文档当前基线**：修正测试数量、真机暂停状态、Gift / SC / Guard 当前行为、Viewer Memory v1、ReservedModule 定位和多平台礼物路由；把 monitor 字段细节归还 `runtime-observability.md`，补齐文档索引。~~ ✅ **已完成（2026-07-15）**。
3. ~~**处理离线可确认的架构债**：六个无消费者的 ReservedModule 空壳与 Dashboard 假模块已移除，真实导入失败的 `ReservedModule` 降级仍保留；`config_schema` 已按唯一真实消费者收窄为 boolean / select / text / string，不再把无消费者的数值字段和 `show_if` 伪装成待补能力，并新增生产 schema 契约测试。~~ ✅ **已完成（2026-07-15）**。后续只有能证明减少维护或运行成本的项目才进入实现，不为了文件大小继续拆分或预建能力。
4. ~~**收口真机反馈暴露的礼物调度与弹窗状态问题**：礼物事件完成 provider ID 去重、连击单调合并、有界优先调度、拒绝 / 淘汰后的可重试、会话任务回收和内存聚合状态；主播主题与直播节奏弹窗保存成功后以本地已保存配置作为再次打开的基线。~~ ✅ **已完成（2026-07-17）**。本阶段不新增持久礼物账本、诊断事件日志或完整拥塞 / 公平 / 熔断机制。
5. ~~**确认观众记忆产品契约后再加普通用户控制**~~ ✅ **已完成（2026-07-15）**：个性化记忆默认开启，固定保留 90 天并惰性清理；关闭只停止安全派生偏好的学习与 prompt 使用，不影响基础档案、首次出场记录和本场防复读；普通用户可清空全部档案，或对单 UID 重置印象 / 删除档案。当前明确不加入 `watch_time`、`contribution_rank`、raw 弹幕历史、后台清理任务或新依赖。

### 下一阶段（当前离线收尾完成后）

1. ~~**旧插件能力迁移矩阵与退役评估**~~ ✅ **矩阵已完成（2026-07-17）**：已逐项核对 `bilibili_danmaku` 的 47 个公开入口及内部存储 / 分析能力，结果记录在 `bilibili-danmaku-migration-matrix.md`。主播账号身份保护的产品契约已记录，但维护者决定延期；当前下一项是可信支持事件账本的独立设计，通用 B站 内容/写工具仅在维护者确认需要时再拆。最终删除仍须从最新 `main` 建独立分支，不得与功能迁移 PR 堆叠。

### 真机测试恢复后

1. ⏸ **完成新版面板下的真实 30 分钟独播验证**：发布前仍须按 `independent-mode-product-plan.md` 的 `Release Validation Acceptance Checklist` 验证登录、直播间确认、普通弹幕、Gift / SC / Guard 短句致谢、冷场、主动营业、结束直播和恢复路径。维护者已于 2026-07-15 明确暂缓真机测试；恢复前不得为了补结论自行启动前后端或连接真实直播间。
2. ⏸ **UI 性能与可访问性验收**：只读区块已开始从 `panel.tsx` 拆出，面板状态刷新契约也已离线收口为仅可见时 3 秒低频刷新、隐藏即停、恢复可见立即补刷、单请求串行并清理 `visibilitychange` 监听。进一步拆分只在能降低实际维护或渲染成本时进行；打包版 Hosted UI 的窄宽、键盘、焦点、状态播报、对比度、空闲 CPU 和监听器泄漏实测随真机验收一并暂缓，不再重做导航结构。

### A. 健壮性（建议优先）

1. ✅ **anti-352 for lookup（已实现 + 真机；机制通，但重度风控 IP 仍 -352）**
   - 做了什么：`_lookup_room_status_sync` 加 ① 临时 buvid3（首页 Set-Cookie 抽取 `_parse_buvid3_from_cookies` + 6h 缓存）② 浏览器 headers（`_BROWSER_HEADERS` + Referer + Cookie）③ 撞 `-352` 刷新 buvid3 **重试一次** ④ 成功结果 **60s 缓存**。详见 development.md「直播间查询与 -352 风控 / A1」。
   - 取舍：`getInfoByRoom` **不需 WBI**（WS 的 `_get_real_room_id` 调它也没签），故未做 WBI；为不引 async 重构，沿用 sync urllib + `to_thread`，**未抽** `bili_web.py` 共享模块（按需再抽）。
   - 局限：只**降低** -352 频率，重度风控 IP 仍可能撞墙；彻底消除需**登录态**（P5）。
   - **真机（2026-06-17）**：`_fetch_buvid3_sync` 确认能抓到 buvid3（len=46），机制全跑通；但 `getInfoByRoom` 对**本机 IP（连日测试已重度风控）4 个房间一致 -352** → 匿名 buvid3 **不足以**过其风控（疑似还需 WBI 签名，或纯 IP 级封禁）。**彻底消除 = P5 登录态**。可选加强（未做、优先级低于 P5、对已封 IP 未必有效）：给 getInfoByRoom 也加 WBI（`danmaku_core._wbi_sign` 已有，sync 化）。单测：`test_parse_buvid3_from_cookies`、`test_lookup_retries_once_on_352_with_fresh_buvid3`、`test_lookup_caches_successful_result`。

2. ✅ **支持直播间链接输入（已实现：代码 + 单测）**
   - 做了什么：`contracts.parse_room_id`（吃数字 / 纯数字串 / `live.bilibili.com/<id>` 链接，含 `/h5/`、`/blanc/`、query）；`from_mapping` 的 `live_room_id` + `update_config`（持久化前归一）+ `connect/lookup/set_live_room` 三入口都过它；3 个 action 的 `room_id` schema 改收 `string`、handler 传原始值；面板 `saveConfig/connectRoom/lookupLiveRoom` 送**原始串**（去掉 `Number()` 截断）；占位符「房号或链接」8 locale 同步。
   - 单测：`test_parse_room_id_accepts_number_and_url`、`test_update_config_parses_room_url`、`test_set_live_room_accepts_bilibili_url`。

3. **host/core 修复已进当前分支，做插件侧回归收口（详见 `docs/devlog.md`）**
   - **配置写竞争**：插件侧已免疫（`update_config` 内存先行 + 带预算持久化，见 §5 / development.md「配置持久化与写竞争」）；#1884 已进入当前 `Roast`，后续改动只需保持插件侧兜底不退化。
   - **`PluginStore.store.enabled` 构造期冻结**：#1884 已让 runtime helpers 可在 effective config 就绪后刷新；neko_live 仍保留观众档案本地 JSON 边界，不回切 PluginStore。
   - **插件数据跟随 selected_root**：#1884 已在插件子进程启动前刷新 storage layout env；插件侧已完成默认目录、自定义目录失败回退、实际路径状态和失败 tmp 清理回归。`viewer_store_dir` 自定义入口按当前产品范围继续屏蔽，不因回归通过自动恢复 UI。

### B. 功能路线（详见 §7）

4. ~~**P2.5 完整版（事件中枢地基）**：`LiveEvent` 统一信封、`EventBus` 真订阅分发（隔离 + 归属 + audit）、`InteractionModule` 补 `on_enable/on_disable`、窗口择优扩到 gift/SC/guard~~ ✅（见 §4「事件中枢地基」+ development.md「直播事件中枢（EventBus）」）。
5. **P4 档案 / 记忆**：安全画像 v1 已落地弹幕计数、偏好标签计数、常聊话题、接梗提示、互动风格 / 回复偏好、短摘要、避坑提示和运行时派生投影；普通观众页提供精简摘要、详情、重置印象与删除档案，设置页提供默认开启的个性化开关、90 天说明和清空全部档案。`contribution_rank`、`watch_time` 暂不进入当前版本；未来若新增仍须单独确认产品价值、采集可信度和隐私成本，并继续遵守“不存原始弹幕 / raw payload / token / cookie / 可反推私密内容长文本”的边界。
6. **P5 登录与显式无账号兜底校验已落地；私信 / 写能力不在当前范围**：B 站扫码登录已能加密保存凭据并服务 lookup、头像和监听；未登录时 runtime 只接受本次调用显式确认的受限兜底。未来 `bili_dm_ingest`（收）与 `bili_write_tools`（发）若重新立项，仍须独立做权限、登录态和安全评审，不得因为已有登录就默认开放写能力。
7. **P6 主播自动化不在当前范围**：未来若重新立项 `automation_ops`，先做授权、撤销、成本和安全边界评审；当前不执行浏览器或键鼠自动化。
8. **收官：删除 bilibili_danmaku**：47 个入口与内部分析/存储能力的迁移矩阵已完成，但获批迁移和独立插件取舍尚未关闭。确认剩余能力均已替代、迁移或明确废弃后，再从最新 `main` 建独立分支删除 41 个 tracked 文件，并迁移构建注释和通用测试夹具引用。不得把删除混入功能迁移 PR。

### C. 多平台直播输入 / 抖音只读接入计划（分阶段实施中）

本路线只覆盖**插件端新增抖音直播输入**，不改变 NEKO 主程序输出链路；v1 目标是“抖音弹幕与安全礼物摘要进入现有 provider-neutral NEKO Live pipeline”。接入模块本身只发布安全事件，不直接触发输出；礼物被 Selection 选中后可由 `live_support_events` 产生短句致谢。抖音发言、点赞、关注、私信或直播间运营自动化仍不在范围内。

**参考结论**：`cvv-cat/Douyin_Spider` 证明了浏览器 cookie + 直播间页面解析 + webcast WebSocket + protobuf/gzip + ack/heartbeat 的技术路线可行，能接收弹幕、礼物、入场、关注、点赞和热度等事件；但该项目不是官方开放 API，且仓库未见明确 LICENSE，因此只允许参考协议流程和字段语义，**不得直接复制实现代码或 JS 签名文件**。抖音开放平台的直播互动能力更偏“挂载玩法/小玩法”的授权场景，不适合作为任意直播间只读弹幕输入的 v1 依赖。

**开发原则**：
- 抖音只作为新的 live provider 接入，不在 runtime 里堆 `if platform == ...` 分支。
- B 站现有行为必须先被 provider router 包住并保持不变，再接抖音。
- 抖音弹幕进入 pipeline 时仍归一为 `ViewerEvent(source="live_danmaku")`，继续经过 `PermissionGate`、`SafetyGuard`、pipeline、dispatcher。
- 入场、关注、点赞、热度 v1 不触发 AI 回复；礼物只由 ingest 发布安全摘要，是否回复由共享 Selection 与 `live_support_events` 决定，provider 不得直接新增 LLM turn/token/TTS。
- 凭据、cookie、签名参数、完整 HTML、protobuf 原包不得进入 config、audit、UI、viewer profile、recent result 或 `ViewerEvent.raw`。
- 观众 UID 必须平台前缀化，例如 `bilibili:<uid>`、`douyin:<uid>`；若从旧纯数字 UID 迁移，先清空或迁移观众档案，避免跨平台串档。

**推荐切片顺序**：

1. **Provider Router 地基（B 站行为不变）**
   - 新增平台 provider/registry/router，但只注册 B 站 provider。
   - 收口 `runtime_live_controls`、`runtime_live_listener`、`runtime_live_input`、`runtime_health` 对 `bili_live_ingest` 的直接调用。
   - 收口 `pipeline_viewers` 对 `bili_identity` 的直接调用。
   - 保留 `runtime.bili_live_ingest` / `runtime.bili_identity` 兼容属性，避免一次性打碎既有测试和旧 action。
   - 验证重点：B 站连接、lookup、EventBus、live_events、pipeline、runtime health 全部行为不变。
   - **当前进度**：已完成 `LiveProviderRouter`、runtime live control/listener/input/health 的 provider 调用收口，以及 `pipeline_viewers` 身份解析收口；旧测试/沙盒上下文仍通过 legacy B 站 identity adapter 兼容。

2. **平台配置契约**
   - 新增 `live_platform`（默认 `bilibili`）和通用 `live_room_ref`。
   - 保留 `live_room_id` 作为 B 站兼容字段；B 站旧配置加载时自动映射到 `live_room_ref`，但落盘兼容策略单独说明。
   - 新增平台 room parser：B 站继续接受数字 / `live.bilibili.com`；抖音接受 `live.douyin.com` URL 或直播间标识。
   - 修改 live status / connection snapshot 时，同时展示 `platform`、`room_ref`，但不破坏既有 `room_id` 字段。
   - **当前进度**：`live_room_id` 只在 B 站 provider 中作为兼容目标参与派生；抖音等非 B 站 provider 不会从旧数字房号推导 `room_ref`，避免平台切换或旧配置残留串目标。

3. **通用直播富事件模型**
   - 新增 provider-neutral event model，例如 `LiveProviderEvent(platform,event_type,uid,nickname,text,avatar_url,room_ref,score,raw_safe)`。
   - B 站 `LiveDanmaku` 先适配成通用事件，`live_events` 不再直接依赖 B 站 `MessageType`。
   - 保持现有窗口择优语义：普通弹幕可进 pipeline；gift/SC/guard 作为安全候选参与 Selection，被选中后由已批准的 `live_support_events` 处理。

4. **抖音凭据模块**
   - 新增抖音 cookie 存取能力，优先做手动导入 cookie + 加密保存 + 状态检查 + 本地删除。
   - 不做自动网页登录、二维码登录、手机号登录或浏览器自动化。
   - B 站 `credential_store.py` 已抽 namespace 能力；不允许把抖音 cookie 混进 B 站凭据文件。
   - **当前进度**：已完成基础 runtime action、独立加密落盘和 Hosted UI 入口（`douyin_cookie_import` / `douyin_cookie_status` / `douyin_cookie_validate` / `douyin_cookie_delete`）；公开状态只回显脱敏后的 uid / nickname / saved_at，uid 仅允许可选 `douyin:` 前缀的短安全标识形态，误把 cookie/token/signature/sign/webcast_sign 形态文本粘到公开字段时会清空；cookie 有效性校验只在用户手动触发时读取当前房间元数据，不做后台轮询、网页登录或浏览器自动化。

5. **抖音只读 ingest**
   - 实现直播间信息解析、初始 webcast fetch、WebSocket 连接、protobuf/gzip 解包、ack、heartbeat、断线重连和事件清洗。
   - v1 事件范围：`chat` 进入普通弹幕 pipeline；`gift` 发布安全摘要并可进入共享支持事件路径；`member/follow/like/stats` 只做轻量状态或先丢弃。
   - 断线重连必须有上限、退避和可见状态，不允许无限递归重连。
   - 解析失败/风控/cookie 失效必须降级为 disconnected 或 auth-required，不得阻塞插件启动。
   - **当前进度**：已完成 `douyin_live_ingest` 骨架、provider router 接入、房间 URL/token 解析、页面 fetch/`RENDER_DATA`/Next RSC 元数据解析、lookup audit / metadata / bridge connection plan 安全投影、有上限的重连退避状态、内置 `douyinLive` bridge 进程监督、通用 localhost bridge transport、bridge JSON 适配、fixture/bridge 事件归一化、UID / room_ref / avatar_url 进入 `ViewerEvent.raw` 前的安全清洗、`chat -> danmaku -> live_events` 通路、标准 gift 与 bridge contribution/light-gift 形态的安全摘要透传，以及 `member/follow/like/stats` status-only 不发布边界。礼物下游复用 provider-neutral Selection 与 `live_support_events`，不在抖音 provider 内直接产出；插件侧 `webcast/im/fetch`、protobuf、ack、heartbeat 和直连 Douyin WebSocket 已从 v1 运行时移除，避免继续维护被 `DEVICE_BLOCKED` 卡住的链路；连接失败会通过 connection snapshot 暴露脱敏后的 `last_error` / `connection_plan` / `reconnect` 状态。JS 签名执行、自动登录和浏览器自动化仍未纳入 v1。

6. **抖音身份解析**
   - 新增 `douyin_identity`，只把已清洗事件字段转成 `ViewerIdentity`。
   - `uid` 使用 `douyin:<stable_id>`，且 `stable_id` 只接受短安全标识形态；昵称和头像缺失时安全降级，不额外硬抓用户主页。
   - v1 不做抖音头像二次抓取，避免额外网络压力、隐私面和风控。
   - **当前进度**：已完成独立 `douyin_identity` 模块并接入 provider router；它只消费 ingest 清洗后的事件字段，并在生成公开 `source_url` / `name` / `nickname` / `avatar_url` 前再次过滤 unsafe UID、敏感文本和 avatar URL，不抓主页、不下载头像、不写入 cookie/raw payload。

7. **UI 功能接入**
   - 控制台顶部加平台选择；下方认证区和房间输入随平台切换。
   - B 站显示扫码登录 + B 站房间输入；抖音显示 cookie 登录状态/导入/校验/删除 + 抖音直播间 URL 输入。
   - 账号和房间分离：账号是认证态，房间是监听目标；切换房间不等于切换账号。
   - 新增 UI 文案必须同步 8 个 locale。
   - **当前进度**：已完成 Hosted UI 平台选择、B 站/抖音认证区切换、抖音 Cookie 导入/状态/手动校验/删除 action 暴露、抖音直播间 URL 输入、平台切换时清空旧房间目标并关闭监听开关，以及 8 locale 文案同步；真实抖音监听走内置本地 bridge 路径，失败时通过 `unsupported` / `disconnected` + 脱敏 `last_error` 降级。

8. **验证与回归**
   - 基础门禁：`uv run pytest plugin/plugins/neko_live/tests -q`、`uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_live`。
   - 分层测试：provider router 兼容、配置迁移、room parser、通用事件模型、凭据加密、抖音 ingest fixture、安全礼物摘要与共享 support route、断线/失效降级、connection snapshot 脱敏可见。
   - 手动 dry-run：B 站原链路不退化；抖音无 cookie 提示清楚；抖音有效 cookie 能接入弹幕；礼物只在被 Selection 选中后走 `live_support_events`；断开后 safety guard 变 disconnected。
   - **当前进度**：已补 Hosted UI connect action 契约测试，覆盖从面板传入完整抖音直播 URL 后，后端归一化为安全 `room_ref`、启动抖音 provider、公开 snapshot/config/audit 不泄漏 URL query 的路径；已跑通抖音 ingest / bridge / router、live events / live status / runtime controls、smoke / config / lifecycle 回归，以及 Hosted TSX parser 单测与 Node gate。同 checkout 真实 Hosted UI E2E 已通过：抖音平台切换、房间查询、完整直播 URL 归一为安全房间号、bridge-only 监听启动到 `receiving` 状态均可用。B 站手动 dry-run 已切回 `bilibili` 房间 `6` 验证查询和监听可进入 `receiving`，随后恢复到抖音房间。真实抖音房间 `300294032039` 已捕到 bridge contribution 形态礼物，进入 `gift_signal` / `live_event_signal.unsupported_gift`，未触发 AI，且贡献用户 UID 优先来自 `userContributeList` 而不是主播/接收方 id；`monitor_live.ps1` 已补 `recent_observed_signal_*` / `recent_skipped_signal_*` 与 `latest_gift_*`，能区分“礼物已看见”和“没有进入输出链”。本轮额外验证了热重载 / 父进程异常退出后残留的旧 `douyinLive.exe` 会在下一次 start 前按同 executable path 清理，正常 `disconnect_live_room` 也会关闭当前 bridge 进程；随后对房间 `300294032039` 做 12 次 / 约 2 分钟短长连 monitor 采样，连接保持 `connected=True` 且无 monitor alerts，bridge 进程保持单实例并有 localhost established 连接。

**当前接手建议**：
- 当前只读 bridge transport 已接入；后续仍不得新增 protobuf runtime、JS 签名执行、自动登录、浏览器自动化，或新增/更换 bridge 运行依赖，除非重新做成本/风险评审。
- 抖音 v1 已收敛为 bridge-only：插件不再维护直连 WebSocket/protobuf/签名链路，而是托管内置 `douyinLive` 本地 bridge，并用可替换 backend / adapter 收口。
- 下一步优先完成离线文档/能力盘点，并在真机测试恢复后做 Hosted UI 完整链路回归、真实直播复盘和 bridge 分发取舍复核；不是继续补直连协议，也不是另建 provider 专属礼物答谢 handler。

**2026-07-07 交接快照**：
- 当前分支 `roast` 的接手基线为 `ca186130 Improve neko roast live prompt knowledge`；抖音观众身份修复在 `dc8a9a4d Fix Douyin viewer identity key`，该提交的 GitHub `Plugin Tests` 已通过。
- 抖音只读主路径已经闭环：Hosted UI 可切平台、输入完整 `live.douyin.com` URL、归一为安全 `room_ref`，插件启动内置 `douyinLive` bridge 并进入 `receiving`；停止监听会关闭当前 bridge 进程，下一次 start 会清理同 executable path 的旧残留。
- 真实房间验证结果：`768049166245` 能收到弹幕，部分观众信息因直播间隐私策略降级为脱敏昵称 / 默认头像，但 `webcastUid` 可作为稳定 opaque id；`703082690217` 能收到弹幕、较完整昵称和真实 `aweme-avatar` URL；`300294032039` 曾捕到 contribution/light-gift 形态礼物并进入 signal-only，不触发 AI。
- 2026-07-07 本机联调补充确认：N.E.K.O 后端与 N.E.K.O.-PC Electron 同时运行时，手动 Cookie 导入、状态检查、房间查询、bridge 启动、事件转发和停止回收路径可用；停止后无 `douyinLive.exe` 残留，bridge 端口只剩短暂 `TIME_WAIT`。二进制分发策略继续暂缓，不作为内部 v1 收尾阻塞项。
- 观众档案规则已定：抖音 profile key 使用 `douyin:<webcastUid>` / 等价稳定 opaque id，`id` / `idStr=111111` 视为平台隐私占位，不得作为档案主键；昵称和头像都是可降级 metadata，不因默认头像或缺头像判定连接失败。
- 交接后优先跑一次真实 Hosted UI 回归：平台切到 Douyin -> 输入房间 URL -> 查询/监听 -> `receiving` -> 弹幕进入 pipeline -> viewer profile 不串成 `111111` -> 停止后无 `douyinLive.exe` 残留。礼物只需确认 signal 可见，不做礼物答谢。
- 未完成 / 不阻塞：bridge 二进制分发策略、普通主播登录体验，以及旧 `bilibili_danmaku` 矩阵中的后续获批迁移。能力迁移矩阵、礼物答谢和普通用户画像治理已经完成；贡献榜、`watch_time` / `contribution_rank` 明确不进入当前版本。

**实施前需要维护者确认的新增成本点**：
- 是否接受抖音 v1 依赖内置非官方本地 bridge；这是二进制分发、协议漂移和进程生命周期成本，不是 UI 问题。
- 是否接受新增 provider router 作为核心 runtime 边界；这是为了避免后续平台分支污染 B 站链路。
- 是否接受通用事件模型替换 `live_events` 的 B 站富模型依赖；这是多平台复用的关键，但会触碰 Selection Protected Module。
- 是否接受清空或迁移观众档案到平台前缀 UID；否则跨平台 UID 撞车风险不可控。
- 是否接受 v1 只读：弹幕可触发普通回复，安全礼物摘要可复用共享短句致谢，其他状态事件不触发 AI。

**抖音 bridge-only Decision Points（当前拍板状态）**：

| 决策点 | 当前结论 | 成本/风险 | 后续动作 |
|---|---|---|---|
| bridge 形态 | 内置 MIT `douyinLive` 本地进程 + localhost WS，插件只消费清洗后的 JSON | 额外进程、端口占用、二进制更新、协议漂移 | 保持 `bridge_backend.py` + adapter 边界；失效时只换 bridge/backend/adapter |
| 插件侧直连 | v1 不做 `webcast/im/fetch`、protobuf、ack、heartbeat、JS signature | 降低协议维护成本；功能稳定性依赖 bridge | 不恢复直连，除非另做成本评审 |
| 生命周期 | bridge supervisor 负责启动/停止；transport 只连 localhost；状态进入 `listener_state`；Windows 下启动前清理同路径旧 bridge 进程 | CPU/网络/日志量随本地进程和重连增加；启动时多一次本机进程扫描 | Hosted UI 验证连接、断开、失败降级和无残留进程 |
| 事件范围 | `chat -> danmaku` 进入 pipeline；标准 gift 与 contribution/light-gift 形态投影为安全礼物摘要，并复用共享 Selection / `live_support_events`；member/follow/like/stats status-only | 支持事件会使用既有短句回复成本；贡献事件可能只有分值没有礼物名/头像 | 保持安全摘要和共享短句边界；更复杂朗读、欢迎、权益或贡献流程另做成本评审 |
| 登录体验 | v1 保持手动 cookie/可无 cookie bridge；不做扫码、网页登录自动化或浏览器托管 | 分发易用性不足；自动登录会带来风控、隐私和额外依赖成本 | 分发前单独评审登录体验 |

守门规则：当前已实现内置 bridge 托管、localhost bridge transport、adapter 清洗和事件归一化；后续新增 protobuf runtime、JS 执行、自动登录、浏览器自动化、额外依赖或更大事件范围前，必须同步测试、状态投影、降级路径、回滚说明和成本评审。

**抖音登录体验后续 Decision Point（不阻塞内部 v1 bridge-only listener）**：

- 内部 v1 先采用手动 Cookie 导入 + 加密保存 + 手动直播间链接，以验证只读弹幕 transport 是否能稳定进入现有 NEKO Live pipeline。
- 普通主播不会自然知道 Cookie 在哪里，因此分发前需要单独评审登录体验：打开抖音登录页 + 手动复制指引、浏览器 Cookie 导入、嵌入网页登录 / 扫码登录、自动刷新 Cookie 等方案必须另列成本、隐私、账号风控、依赖和回滚策略。
- 未单独拍板前，不实现自动网页登录、二维码登录、浏览器自动化、浏览器 Cookie 数据库读取或 Cookie 自动刷新。

**明确不做**：
- 不接入抖音发弹幕、点赞、关注、私信、礼物答谢 TTS 或自动运营。
- 不把 `Douyin_Spider` 作为运行时依赖，不复制其未授权代码。
- 不新增浏览器自动登录，不保存明文 cookie，不把 cookie 写入 audit/log/config/UI。
- 不让礼物/入场/关注/点赞绕过 pipeline 直接触发 NEKO。

---

## 12. 项目成熟度与分发就绪度评估（2026-07-03）

> 这是 2026-07-03 的历史自评快照，不代表当前测试数量或完成状态；当前测试基线以 `development.md`「测试门禁」为准，当前阶段状态以本路线图后续更新为准。当时的结论是：**架构与可靠性产品级，测试治理和 CI gate 已进入可交付轨道，功能完成度已从 v0.1 单切片进入 Independent Mode 产品验证期**。

| 维度 | 评级 | 依据 |
|---|---|---|
| 架构设计 | A− | 清晰分层 + 四条不变量，且用契约测试**锁死设计意图**（不只测行为）|
| 可靠性工程 | A− | 五层兜底是真功夫：`safety_guard`（滑窗失败计数→自动急停 / 队列溢出→降级 / 限流）、`pipeline`（每步审计 + `finally` 清队列）、`dispatcher`（dry_run + 头像压不进预算则降级纯文字）|
| 代码质量 | B+ | `pipeline`/`safety_guard`/`dispatcher` 教科书级；Hosted UI 已从单一 `panel.tsx` 入口拆出 `panel_components.tsx` 与 `panel_helpers.ts`，但仍需继续控制面板复杂度 |
| 文档 | A | 「无文档=未完成」真在执行；但偏厚、跨文档有同事实冗余 |
| 测试 | B+ | 截至该快照，`plugin/plugins/neko_live/tests` 为 546 passed；原 100KB+ 测试大文件已按 config / pipeline / runtime active engagement / monitor 主题拆分，最大测试文件约 56KB；硬骨头（真连 B站 / 视觉 / 消息面 / 面板渲染）仍主要靠真机验证 |
| 工程治理 | B | `Plugin Tests` workflow 已新增 `NEKO Roast gate (Windows)`，在 `roast` 分支自动跑 neko_live 测试套件与 CLI check；PR 评审轨迹与发布节奏仍需后续补齐 |
| 功能完成度 | Independent Mode 验证期 | 「首评锐评」闭环已稳定，后续弹幕接话、Idle/Warmup Hosting、Active Engagement、Runtime Timeline、Gift/SC/Guard 短句致谢均已接入；下一步看真实直播验证和产品体验收口 |

**优点（有代码支撑）**：可靠性刻进代码而非口号；对抗真实世界的疤痕（-352 风控、配置写竞争免疫、消息面吞图 bug 修复）；契约测试锁架构红线；克制复用 + 隐私自觉（凭据加密不落 log/UI、头像 bytes 不落盘）。


**分发就绪 TODO（按优先级）**：
1. ⏸ **真实直播验证**：仍是发布门槛，重点看 `live_support_events` 的 Gift / SC / Guard 致谢是否短、是否不索要更多支持、是否不污染普通弹幕接话 / 主动营业节奏；维护者恢复真机测试前不执行。
2. ✅ **UI 离线收口**：四区导航、控制台主流程、只读数据区块拆分与仅可见时低频刷新契约已完成。进一步拆分必须证明能降低维护或渲染成本，不再为了文件大小继续重构。
3. ~~**P4 画像治理打磨**~~ ✅ **产品契约与普通用户控制已完成**：当前只需在发布验收中校准现有画像质量；不补 `watch_time`、`contribution_rank`。
4. **旧 `bilibili_danmaku` 获批迁移与退役**：47 个入口及内部能力矩阵已完成；主播身份保护已记录并延期，当前先做可信支持事件账本设计。通用内容/写工具仅在维护者确认需要时拆独立插件，最终删除另走独立 branch/PR。
