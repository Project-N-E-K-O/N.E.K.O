---
name: proactive-chat
description: 主动搭话（/api/proactive_chat）及周边——门控链、防复读、用户禁令、截图暂存/水印/授权、开屏问候、活动猜测、海外新闻源、凝神——的不变式、既定取舍和踩过的坑。改 main_logic/proactive_chat/、main_logic/core/proactive.py、main_logic/core/greeting.py、memory/anti_repeat.py、memory/user_directives.py、main_logic/activity/，或动隐私模式、首启 A/B 分支之前先读。
---

# 主动搭话及周边

## 已有文档（这里不重复）

- 模块归属、依赖方向、对外协议、行为不变量：`docs/design/proactive-chat-domain-refactor-design.md`。
- `reason_code` / `stage` 的用法：`docs/design/proactive-reason-code-guide.zh-CN.md`。常量现在的定义处是 `main_logic/proactive_chat/contracts.py`，`main_routers/system_router/proactive_parsing.py` 只是兼容 re-export。
- 活动追踪器：`docs/design/user-activity-tracker.md`。凝神：`docs/design/focus-truename-mode.md`，旋钮和每个旋钮的理由在 `config/focus_settings.py` 的注释里。
- 旁听检查（一轮结束后要不要替她办事）：`docs/design/nekoclaw-rewrite.md` 6.4 节。

排查「这一轮为什么没说话」：先看 `/api/proactive_chat` 响应里的 `reason_code`，再看日志里的 `主动搭话本轮未发起`、`proactive ... blocked`、`proactive regen`。带原文的行只走 `print`，不进 `logger`。

## 产品原则（维护者已定，同类评审意见按此判）

1. **隐私模式只管「AI 能不能看屏幕和程序状态」，不能拿它关掉「理解用户」的特性。**
   隐私模式就是 `proactiveVisionEnabled` 的反面（`utils/preferences.py::is_privacy_mode_enabled`）。判据看特性的输入：输入是用户主动发来的话（文字/语音）→ 不查隐私模式；输入是用户没主动发的屏幕、窗口、进程状态 → 只在采集那一处门控。理由：从用户说的话里读情绪和对话状态是陪伴产品的核心，用户的消息不是「屏幕数据」。
   - 现在合法的门控点：活动追踪器整体短路（`main_logic/activity/tracker.py::_privacy_mode_active`，读不到设置按「已开启」处理）、主动搭话不取活动快照（`service.py`）、截图和窗口来源的授权检查。
   - 隐私模式下活动快照是 `None`。消费方按「没有数据」降级，不要在上面再叠一层自己的隐私检查。
   - 凝神、用户情绪读数、话题候选、记忆反思都不受隐私模式影响。看到「隐私模式开着就禁用某个理解用户的功能」的代码，按 bug 处理。
2. **首启默认值的 A/B 只测首次启动，不为已退役分支补迁移。**
   `utils/token_tracker/telemetry.py::_TELEMETRY_BRANCHES` 的分支只决定首启时某个设置的默认值；前端的覆写由 `static/app/app-settings.js` 的 `_FIRST_LAUNCH_PENDING_KEY` 把门，过了首启的用户不再响应。分支退役后，旧值校验不通过、按当前池重抽是预期行为。老用户落在哪个分支对实验没有意义（分析时按真实首启样本过滤），不要为「老分组归因」加确定性映射。
   - 例外只有一种：退役的实验曾经改写过用户偏好值时，可以在重抽那一刻做一次性回滚（现有例子 `_rollback_retired_proactive_interval`）。
   - 如果池里有不受首启把门的实验（比如常驻弹窗），重抽会让一部分老用户开始看到它。这种情况同样不加迁移，但 PR 描述里不能写「用户无感知」。
3. **下线实验要留底，不能纯 revert。**
   下线 = 从 `_TELEMETRY_BRANCHES` 移除 + 在该常量上方的注释和前端对应位置（`app-settings.js` 首启分支的注释）写明这个实验做过什么、为什么停。要回滚偏好的，保留 `_RETIRED_*` 常量和回滚函数。理由：实验历史要能审计、能重启。本仓库的间隔类实验只有「参数常量 + 留底注释」，没有可以单独留着不启用的代码模块。
4. **参与度闸是设计，不是缺陷。**
   记忆抽取链路里只在用户发过言时才累加计数或触发后台模型调用的判断（`app/memory_server/post_turn.py` 的 `if user_msgs:`、`signal_extraction.py` 的 `if not user_msgs_text`、`rows.py::_trim_to_user_msg_bracket` 掐掉首尾没有用户回应的 AI 段）都是有意的：她自说自话、用户没搭理的内容不该沉淀成记忆。评审说「纯 AI 轮/主动搭话轮走不到某条路径」时，先当作设计意图，要改先问维护者。
   同理：主动搭话轮不触发旁听检查；凝神只靠关键词单轮进不去；主动搭话只让凝神冷却、不抬升。
5. **主动搭话和开屏问候撞车，不在前端加避让阀门。**
   问候投递前也走 `try_start_proactive`（`main_logic/core/greeting.py`），和 `/api/proactive_chat` 在同一个会话状态机上互斥，不会同时生成。前端再加阀门只多挡几秒空窗，却要引入计时、音频归属和新协议。再遇到「某两类发声撞车」的需求，先查后端状态机是否已经互斥。

## 改动时必须保持的不变式

- **一轮一个归属。** 文字模式用 `mgr.state.try_start_proactive(round_id=...)` 原子占位；之后每个 `await` 边界都要重查 `_round_preempted()`，投递时带 `expected_speech_id`、`expected_round_id`、`expected_user_engagement_time` 三样。新增退出分支必须走 `_end_proactive`。
- **提交成功才记账。** 搭话历史、素材历史、计数、话题使用、防复读语料都只在投递提交成功（`finish_proactive_delivery` 通过全部归属检查并发布）之后写。
- **Phase 2 自己拼 prompt，不经过 `_build_initial_prompt`。** 任何靠「会话启动时注入 system prompt」生效的约束，在主动搭话里都要单独接（禁令就是这样补上的）。
- ⚠️ **拦截闸要排在所有会把草稿内容写出去的检测之前。** 用户禁令闸排在字面查重、BM25、长窗检测之前，因为那些检测命中时会把草稿片段落盘。判断顺序时问「这道闸之前有没有东西会把内容写出去」，不是「最后有没有挡住投递」。
- **禁令命中是 drop，不是重写。** 重写是一次真实的模型调用，而命中的前提已经是「模型看过禁令还是提了」。禁令的词不进日志、不进响应体，只报条数。
- **推歌、推图的台词不进防复读语料**，改按素材去重。把它们塞回语料会让其它通道被误杀。
- ⚠️ **前端没带 `avatar_position` 是「不要叠水印」的明确表态**，不是漏传。只有后端兜底截图可以用请求里的旧坐标。
- **主动搭话的截图暂存槽和用户自己的待发图片是两个槽**，不能合并。
- **prompt 里给模型看的内部标签必须本地化；输出侧的剥离名单从 prompt 表派生**，不手写。
- **海外新闻候选必须带出处事实进 Phase 2**，匹配不上的选题宁可跳过。
- **`activity_guess` 成功时不打日志。** 看到空闲时有规律的模型请求却没有业务日志，先查它。

## references 索引

| 文件 | 什么时候读 |
|---|---|
| `references/gate-chain.md` | 加闸、改闸顺序、处理抢占或轮次归属、动语音快路径或短路分支、动旁听检查的外部意图信号 |
| `references/anti-repeat.md` | 改防复读阈值或语料、排查「再也不搭话」「推歌变少」、有人问「为什么常规回复没被拦」 |
| `references/user-directives.md` | 动「别再提 X」的抽取、注入、主动搭话硬闸、TTL，或处理负反馈相关评审 |
| `references/output-leaks.md` | 主动搭话把内部标签、英文枚举当台词说出来 |
| `references/screenshot.md` | 动主动搭话截图的暂存、水印、授权与拒绝 |
| `references/startup-greeting.md` | 动开屏问候的去重窗口、角度轮换、时段提示 |
| `references/activity-guess.md` | 排查空闲时的静默模型请求，或改活动叙述的触发 |
| `references/overseas-news.md` | 动海外区新闻来源或 Phase 1/2 的选题匹配 |
| `references/focus.md` | 动凝神的开关、指示信号、思考气泡 |
| `references/anti-repeat-effects-store.md` | 改 `memory/anti_repeat_effects.py`，或给别的按角色落盘的 sidecar 加缓存 |
