# NEKO Live 快速开始

NEKO Live 当前有两条使用路径：

- 人猫同播：主播是主主持，NEKO 是搭档，主要接弹幕、接梗和低打断补位。
- 猫猫独播：主播把台前话权交给 NEKO，NEKO 独自接待观众、回应弹幕、维持气氛和控制节奏。

“首次弹幕头像 / ID 锐评”仍是当前最稳定的底层链路；猫猫独播会在这个链路之上增加 Live Status、为什么没说话和 Idle Hosting 的基础能力。

插件启动或配置重载后只有在 `live_enabled=true` 时才会向当前猫猫注入轻量直播语境，让她知道接下来收到的是直播间弹幕/头像锐评事件；未开启直播插件时不会注入直播语境，避免影响 Warthunder 等其他插件发言。真正的沙盒或直播弹幕仍会通过 `respond` 事件触发自然短句回应。
开发者模式开启时会在直播语境之上追加调试语境。手动从面板开启开发者模式时，猫猫会短句播报一次已进入调试状态；插件启动时如果配置已开启，只注入调试语境，不自动播报。
插件关闭时会发送恢复语境，提醒猫猫回到日常聊天状态，不再把后续普通对话当成直播弹幕或头像锐评。

界面分六个一级页：控制台 / 直播间互动 / 观众 / 私信（占位）/ 自动化（占位）/ ⚙设置（+ 开发者模式开启时追加“开发者沙盒”）。

## 猫猫独播最快流程

1. 在“控制台”完成 B 站登录、直播间填写、查询直播间和开始监听。
2. 首次接入先保持“安全测试态（dry_run）”，确认 NEKO 能看到弹幕、能走完整链路。
3. 在“控制台”选择“猫猫独播”。
4. 看顶部 Live Status 结论：
   - 可以开播：NEKO 可以接管台前。
   - 只能测试：链路可测，但不会真实输出。
   - 暂时不会说话：通常是暂停、冷却、安全状态或暂未到开口时机。
   - 不能开播：关键链路未就绪，需要先处理面板提示的问题。
5. 看当前状态：
   - engaged：有观众互动，NEKO 以回应为主。
   - quiet：弹幕变少，NEKO 可以轻补位。
   - idle：冷场，猫猫独播下会成为 Idle Hosting 候选。
   - paused / blocked：NEKO 不应该开口。
6. 如果处于“猫猫独播 + idle + 非暂停 / 非阻断”，NEKO 会尝试用一句短话补位；她不应该解释系统状态、催观众发弹幕或假装有人刚刚说话。
7. 观察 30 分钟时重点看三件事：有没有长时间沉默、有没有刷屏、像不像 NEKO 在主持而不是机器人复读。

## 猫猫独播 30 分钟验收

这一步用于熟人主播陪跑或小范围内测，不用于证明功能全量完成。完整产品判定以 [`independent-mode-product-plan.md`](independent-mode-product-plan.md) 的 `Next Live Test Checklist` 为准；本节只保留现场操作速查。

开播前确认：

- Live Status 是“可以开播”，或明确知道当前是“只能测试”。
- 已选择“猫猫独播”。
- dry_run 状态符合本次目的：链路验证保持开启，真实陪跑前手动关闭。
- 顶部状态刷新后，面板能解释 NEKO 为什么暂时没说话。
- 节奏档位先用“标准”；如果 NEKO 太安静再切“活跃”，太吵再切“安静”。
- 可选：在仓库根目录运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\plugin\plugins\neko_roast\tools\monitor_live.ps1 -Once` 做现场快照；字段解释先看 `-Help`，完整记录口径以 [`independent-mode-product-plan.md`](independent-mode-product-plan.md) 的 `Next Live Test Checklist` 为准。
  - 不确定监控参数时先运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\plugin\plugins\neko_roast\tools\monitor_live.ps1 -Help`。
  - 真实输出测试建议加 `-ExpectRealOutput -BackendLogPath <backend-log>`，优先看 `alerts`；`alerts=-` 表示这一帧没有检测到已知真实输出风险。未显式传 `-BackendLogPath` 时，脚本会尝试读取当前目录或仓库根目录下的 `.codex-backend-live-test.log`；如果出现 `test_isolation`，先清理受控测试窗口；如果出现 `backend_log_missing`，说明监控没有读到后端日志，需要补传日志路径后再判断 watchdog、串台或长回复。
  - 现场先看这 8 个信号：`alerts`、`solo_test_focus`、`solo_test_hint`、`director_action`、`latest_route`、`latest_output_length_status`、`recent_actual_idle_hosting`、`recent_actual_active_engagement`。只有这些指向异常时，再展开看下面的细分字段。
  - `recent_*` 是最近尝试数，包含 skipped / failed；`recent_actual_*` 是最近实际 pushed / dry_run 的输出数。判断开场暖场、冷场陪播、主动营业有没有真正说出口时，优先看 `recent_actual_warmup_hosting`、`recent_actual_idle_hosting` 和 `recent_actual_active_engagement`；如果出现 `warmup_missing`，说明导演认为开场暖场已经可以说，但最近窗口里还没有实际 warmup 输出；如果出现 `warmup_repeat`，说明开场暖场实际输出超过一次，需要确认 warmup 状态为什么重新出现。
  - 如果出现 `avatar_bias`，优先看 `avatar_roast_share`、`recent_danmaku_response` 和 `entrance_pacing_window`，确认猫猫是不是又把普通弹幕当成连续首评，以及当前活跃度下连续首评会被压多久；如果出现 `long_reply`，同时看 `latest_output_len`、`recent_long_reply_count` 和 `recent_long_reply_*`，确认长回复主要来自首评、后续接话、冷场陪播还是主动营业，避免旧长回复被最新短回复盖住。
  - 如果出现 `generic_host_prompt`，看 `recent_generic_host_prompt_count`、`log_generic_host_prompt` 和最新输出 / 后端日志，确认主动营业是不是退化成“大家快来互动 / 发弹幕 / get the chat moving”这类模板句。
  - 如果主动营业感觉无聊或接不住，看 `recent_topic_intent_quick_vote` / `recent_topic_intent_tiny_answer` / `recent_topic_intent_tease_back` / `recent_topic_intent_agree_or_pushback`，确认最近话题是不是只在同一种接话形态里打转；如果出现 `topic_intent_bias`，说明最近主动营业已经明显偏向同一种接话方式，下一轮应优先调 topic pool 或形态轮换。
  - 如果主动营业话题本身无聊，看 `recent_topic_source_fallback` / `recent_topic_source_bili_trending` / `recent_topic_source_recent_danmaku`，确认最近主要靠内置兜底、B 站公开素材，还是近期弹幕开题；如果出现 `topic_source_bias`，说明最近素材来源过于单一。
  - 如果出现 `proactive_in_engaged`，说明最新一条实际输出是开场暖场 / 冷场陪播 / 主动营业，但当前房间状态是 `engaged`，优先判断猫猫是否在观众刚互动时抢话。
  - 如果主动营业一直围着同一位观众转、突然翻旧弹幕、首评后又围着同一句话开题，或把 skipped / failed 弹幕放大成全场话题，确认 recent result 的 `topic_source` 是否长期为 `recent_danmaku`；当前开发包会在同一 UID 短窗口连续提供 3 条有效素材时回退到中立话题，并通过 `latest_topic_recent_skip_reason=single_viewer_flood` 标记原因；过期 recent danmaku 被过滤时会标记 `stale_recent_danmaku`；首评上下文被过滤时会标记 `avatar_roast_context`；未输出弹幕被过滤时会标记 `non_output_danmaku`；近期弹幕本身不适合主动营业时会标记 `filtered_recent_danmaku`，其中点名/未点名请求、纯反应和运行反馈会进一步标记为 `filtered_direct_request` / `filtered_reaction` / `filtered_runtime_feedback`。监控还会输出 `recent_topic_skip_*` 计数，并在对应计数非零时给出 `topic_filter_direct_request` / `topic_filter_reaction` / `topic_filter_runtime_feedback` 提示，方便判断这类过滤是否反复发生。
  - `checkout=mismatch`：当前插件服务来自另一个 N.E.K.O 工作区，先重启正确工作区的后端再继续直播测试。
  - `solo_test_focus=chain_only`：dry_run 开启，本轮只验证链路，不判断真实开口。
  - `solo_test_focus=test_isolation`：受控测试隔离还没干净，先清空观众档案或确认本轮不需要首评基线。
  - `solo_test_focus=warmup_hosting`：当前可以观察猫猫独播开场暖场是否自然、是否只说一次。
  - `solo_test_focus=danmaku_response`：可以发一条真实弹幕，观察弹幕到回复。
  - `solo_test_focus=active_engagement`：当前导演判断主动营业可触发，观察猫猫是否自然抛出一个可接话的小话题。
  - `solo_test_focus=idle_hosting`：可以进入冷场补位观察。
  - `solo_test_focus=latency`：当前优先记录延迟。
  - `solo_test_focus=setup_mode` / `preflight` / `unblock`：先处理模式、开播前检查或阻断状态。
  - `solo_test_hint=expect_active_engagement`：当前可以观察主动营业是否发生，以及话题是否具体、不过度求互动。
  - `solo_test_hint=expect_warmup_hosting`：当前可以观察开场暖场是否发生，以及是否像开播第一句而不是冷场补位。
  - 主动营业偏少时看 `active_min_interval` 和 `active_min_wait`；`standard` 约 90 秒，`active` 约 60 秒。
  - `solo_test_hint=expect_idle_hosting`：当前可以观察冷场补位是否发生。
  - `solo_test_hint=watch_latency`：优先记录弹幕到回复的延迟。
  - `solo_test_hint=wait_idle_cooldown`：冷场候选已满足，但还在最小间隔内。
  - `solo_test_hint=clear_viewer_profiles`：观众档案仍存在，若要测试首评基线先清档案。
  - `solo_test_hint=switch_to_solo_stream` / `fix_preflight` / `wait_until_unblocked`：先处理模式、开播前检查或阻断状态。

30 分钟内按阶段观察：

| 时间 | 直播间状态 | 重点观察 |
|---|---|---|
| 00:00-05:00 | 刚开播 | NEKO 是否能自然接待观众，状态是否可信 |
| 05:00-10:00 | 低弹幕 | NEKO 是否以回应为主，不抢、不刷 |
| 10:00-15:00 | 无弹幕 | Idle Hosting 是否能补位，且不尴尬 |
| 15:00-20:00 | 偶发弹幕 | NEKO 是否能从陪播切回回应 |
| 20:00-25:00 | 低弹幕 | 话术是否开始重复，节奏是否需要调档 |
| 25:00-30:00 | 无弹幕或收尾 | 是否仍像 NEKO 在主持，而不是模板自动回复 |

记录结论时只记影响直播效果的事情：

- 最长沉默大约多久。
- 有没有明显刷屏。
- 观众发弹幕到 NEKO 回复是否偏慢。
- 冷场补位是否重复、油腻或像客服。
- 锐评强度是否符合当前档位。
- 主播是否敢继续把台前交给 NEKO。

记录问题时使用同一张表，方便测完直接判断下一步改哪里：

| 时间点 | 当时状态 | 观众/主播动作 | NEKO 表现 | 影响 | 初步归因 |
|---|---|---|---|---|---|
| 例如 12:30 | 无弹幕 / idle | 无人发言 | NEKO 3 分钟未补位 | 偏安静 | 节奏太保守 |
| 例如 18:05 | 偶发弹幕 | 观众发弹幕 | 回复间隔偏长 | 互动断开 | 响应延迟 |

本轮验收只给三个结论：

- 可以继续内测：30 分钟内没有死亡沉默、没有明显刷屏，主播愿意继续把台前交给 NEKO。
- 需要调参再测：主要问题是太安静、太吵、冷却不合适或档位选择不合适。
- 需要改话术再测：主要问题是冷场补位重复、油腻、像客服，或不像 NEKO。

测完后的下一步按问题分流：

- 太安静 / 太吵：先调整节奏档位或 Pacing Control。
- 弹幕到回复慢：记录具体时间点和大约延迟，优先排查响应链路耗时。
- 冷场话术尴尬：先调整 Idle Hosting 文案，不急着加主动营业。
- 状态看不懂：先细化 Live Status / 为什么没说话，再继续测试。

## 详细步骤

1. （可选但推荐）在“控制台”顶部“B 站登录”卡扫码登录本人账号：根治 B 站 `-352` 风控、恢复头像抓取。匿名也能连弹幕，但在被风控的 IP 上“查询直播间”和头像抓取需要登录态。
2. 在“控制台”填写 B 站直播间 ID，或直接粘贴直播间链接（`live.bilibili.com/<id>`，含 h5 / 带 query 都行）。
3. 点击“查询直播间”，确认标题、主播和开播状态是目标直播间。查询撞 -352 失败不代表监听不行——弹幕监听有独立反风控，可直接开始监听。
4. 点击“开始锐评”（即开始监听），确认状态变为已连接；如果房间号有变化，会在此时自动保存。之后房间里观众的真实弹幕会按 UID 抓取头像走完整 pipeline；“控制台”状态四格显示直播间 / 监听 / 实时人气值 / 安全状态。
5. 首次接入默认处于“安全测试态（dry_run）”：整条 pipeline 照常跑（身份解析、头像抓取、锐评 prompt 构造），但不会真的让猫猫开口。确认锐评请求正确后，再由主播手动关掉，恢复真实投递。
6. 在“控制台”选择直播模式：人猫同播或猫猫独播。
7. 在“直播间互动”的“弹幕锐评”卡：用绿色开关开启 / 关闭锐评，选择锐评强度（温柔 / 正常 / 毒舌）pill，按需开启同人去重（每 UID 一次）。
8. 在“设置 → 节奏与安全”保持“自动急停”开启，按需调整冷却秒数和队列上限。冷却秒数即最小锐评间隔，爆量房间靠它控制猫猫不连珠炮，`0` 关闭限流；爆量时同一冷却窗口内会按价值（舰长 / SC / 粉丝牌 / 等级 / 长文本）择优，只评分最高的一条。
9. 观众档案当前固定保存在本机插件默认 AppData 目录；“自定义存储位置”在 2026-06-19 真机测试中确认重启后不稳定，入口已暂时屏蔽，留到下一阶段修复。
10. 到“观众”页查看本场概况：直播总结（本场锐评粗报 + 最近锐评摘要）和观众档案（UID、昵称、锐评次数、最近出现时间）。
11. 需要离线测试时，开启开发者模式后到“开发者沙盒”输入 UID 或 B 站空间链接。
12. 点击“查询资料”只抓取 UID、昵称和头像状态；“发射模拟弹幕”走统一 pipeline 让当前界面猫猫按人设输出；“运行内置案例”用内置演示观众 + 测试头像、不访问 B 站；“清空沙盒记录”遗忘沙盒临时记录和头像预览缓存，不影响观众档案、直播总结或真实直播记录。

开发者模式关闭时，沙盒查询、模拟弹幕、内置案例和聊天开发者工具不可用；清空沙盒记录仍可使用。关闭开发者模式只退出调试态，不关闭插件，也不清空既有沙盒临时记录。

开发者沙盒数据只用于调试：不写观众档案，不进入直播总结，不保存头像 bytes 或 base64 data URL，插件重启后运行时沙盒记录会消失。

开发者模式开启后，猫猫在普通文字或语音对话里也可以调用开发者沙盒工具。例如让猫猫“查一个 B 站 UID 123456”，工具会返回基础资料；让猫猫“查一下并锐评这个 UID”，会走同一条查询和锐评 pipeline。

当前版本不会发送 B 站弹幕、私信、动态，也不会抓取主页资料、贡献值或进房累计。

沙盒和真实弹幕的头像都会在发送给 NEKO 前压缩；如果头像仍然过大，会自动降级为只按昵称和弹幕锐评，避免消息被 message plane 丢弃后看起来“没触发”。
