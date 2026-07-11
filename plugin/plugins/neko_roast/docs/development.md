# 猫娘锐评开发文档

本文档面向后续参与 `neko_roast`（代号「猫娘锐评」，真身是**直播中心 / Live Center**）的开发者，记录**已落地设计**。它是架构边界、模块边界、协作规范、测试门禁和文档要求的 Canonical Source。配套 `live-center-roadmap.md` 只记录阶段目标、完成状态和下一阶段顺序。

对旧插件 `bilibili_danmaku` 采取**选择性复用**：取其**连接+解析层**（`danmaku_core` / `livedanmaku`）与**扫码登录**（`bili_auth_service`），**弃**其自带 LLM / orchestrator / memory（neko_roast 走 NEKO 统一 `dispatcher` → `main_server` 人设）。不直接复制大文件；迁移能力时拆成小模块并补测试证明边界仍成立。

## 当前实现快照

更新日期：2026-06-18

核心闭环：**真实 B站直播间监听 → 事件中枢窗口择优 → 按当前人设对观众锐评（昵称+头像）→ 猫开口**。「首评新观众锐评」是第一个落地的垂直切片。锐评采用**自适应焦点**（昵称与头像哪个更有料就主打哪个，看不到的头像绝不脑补）。

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
    -> viewer_gate.check_once_per_uid()  每 UID 一次
    -> avatar_roast.build_request()      自适应焦点锐评 prompt
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

## 当前模块

已启用模块：

- `bili_live_ingest`：归一化直播弹幕事件、提供直播间状态查询（带反 -352 + 友好降级，见「直播间查询与 -352 风控」），并**持有真实弹幕监听器**——吞并自 `bilibili_danmaku` 的 `DanmakuListener`（同目录 `danmaku_core.py` + `livedanmaku.py`：WS 连接 + WBI 签名 + 临时 buvid3 反 -352 + zlib/brotli 解压 + 心跳 + 多服务器故障转移 + 断线重连）。`runtime.connect/disconnect_live_room` 启停监听；`stop_listening` 用 `wait_for` 给 ws close 加超时，避免关闭握手拖慢断开。**富模型 `on_event` 回调把 `LiveDanmaku` 包成 `LiveEvent` 发布到 `event_bus`**（按命令名映射 `type`），由订阅者按类型消费（轻量 `on_danmaku`→pipeline 直连已退役，防同一条弹幕双锐评）。见「直播事件中枢（EventBus）」。登录态（若有）传入 `DanmakuListener` 与 lookup（见「B站登录态」）。弹幕本身不含头像，头像由下游 `bili_identity` 按 UID 抓取。
- `bili_identity`：解析 UID、昵称、头像 URL；缺少昵称或头像时按 UID 查询 B 站基础资料，并尝试抓取头像供本次 NEKO 视觉输入使用。同时解析头像形态 META：是否默认头像（noface）、是否动图（大会员动态头像，只取代表帧）、挂件/装扮名（出框头像来源）；抓取或识别失败时安全降级（`avatar_vision_ok=False`），不阻断锐评。
- `viewer_profile`：维护观众档案和首次触发判断。
- `avatar_roast`：构造头像与 ID 锐评请求，并集中产出完整锐评指令（见“输出边界”的自适应焦点规则）。
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
  -> avatar_roast.build_request()
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

**触碰的契约 / store / UI / action**：不碰契约（胜者复用 `bili_live_ingest.normalize` 既有 payload 形状，无新增 `ViewerEvent` 字段）。不直接写 store、不直接 `push_message`。新增 audit op：`live_event_selected`（含 `candidates` 候选数、`score`、`guard_level`、`event_type`）、`live_event_flush_failed`、`live_event_roast_failed`。无新增 UI action / context（`live_events` 出现在 `dashboard_state.modules` 快照里，`status()` 暴露 `buffered` / `window_open`）。

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

`build_request()` 只构造请求、不触发 NEKO；强度取 `ctx.config.roast_strength`。`dispatcher.push_roast()` 直接用 `request.prompt_text` 作为文本 part，按 `avatar_vision_ok` / 压缩结果决定是否附加头像 image part（详见「输出边界」「Message Plane 预算」）。

> 已知限制：自适应焦点由 LLM 依据 prompt 判断，非确定性；`pendant` 依赖 `bilibili_api` 返回 `pendant` 字段，缺失则无该 META；`co_stream_output_policy` / `solo_output_policy` 目前仅作语义占位，投递节奏的差异化尚未接入（当前只用 `live_mode` 给 prompt 节奏提示）。

## 输出边界

任何需要让猫猫回应的功能都必须通过 `NekoDispatcher`。不要在模块里直接调用 `plugin.push_message()`。

插件启动和配置变化时会通过 `NekoDispatcher.push_context_instructions()` 注入一段 `ai_behavior="read"` 的轻量上下文，告诉猫猫这是直播间弹幕/头像锐评场景，以及锐评要自然、短句、适合 TTS 播放。这段上下文只用于让 LLM 理解插件语境，不写观众档案，不进入直播总结，也不代表一次锐评已经发生。
如果 `developer_tools_enabled=true`，插件会在直播语境之后通过 `NekoDispatcher.push_developer_instructions()` 叠加开发者调试语境。手动从面板开启开发者模式时，额外通过 `respond` 播报一次进入调试状态；插件启动或配置重载时只静默注入，不自动播报。
关闭开发者模式时，插件会发送开发者调试恢复语境，只退出调试态，不关闭直播锐评语境，也不清空沙盒记录。
插件停止时会通过 `NekoDispatcher.push_context_restore()` 再发送一段 `ai_behavior="read"` 的恢复上下文，提醒猫猫停止把后续普通对话理解成直播间弹幕、头像锐评事件或观众互动事件。xTLM 的做法是连接后注入常驻玩法语境，本插件在此基础上额外补了关闭恢复，避免关闭后仍残留直播锐评状态。

锐评指令的**文本构造**集中在 `avatar_roast.build_request()`：它产出完整的 `InteractionRequest.prompt_text`，包含观众昵称/UID/弹幕、头像可见性与 META，以及给猫猫的锐评规则。规则编码了**自适应焦点**——昵称与头像哪个更有料就主打哪个，两者都有料就抓反差/呼应，都平淡就转弹幕/进场时机/直播节奏，避免硬尬夸；并强制“看不到的头像绝不脑补、避免与最近几条锐评重样、一句话适合 TTS”。独播（`solo_stream`）会提示更主动撑场，同播（`co_stream`）低打断。`build_request()` 只构造请求、不触发 NEKO。

`avatar_roast` 通过 `bili_identity` 解析出的 META 决定头像规则：`avatar_vision_ok=False`（没取到/识别不了）或默认头像 → 只能就“头像配置（默认/会动/带挂件）或昵称”发挥；能看到头像 → 可锐评其具体内容。

NEKO 输出由 `adapters/neko_dispatcher.py` 中的 `NekoDispatcher.push_roast()` 统一负责，pipeline 通过 `self.ctx.dispatcher.push_roast(request)` 进入。`push_roast()` 直接使用 `request.prompt_text` 作为文本 part，再按可见性附加头像 image part（压缩后超预算则省略并在文本里说明降级），不再自行拼装字段；然后调用：

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

**「一张嘴」切分**：功能级参数（开关 / 强度 / 去重…）跟功能走、进「直播间互动」功能卡；平台级参数（dry_run / 节奏 / 队列 / 急停 / 模式）留「设置」。`live_enabled`（开启猫娘锐评）是功能级开关，**单一真相源 = 弹幕锐评卡的绿色卡头开关**（设置页不再重复）。

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

截至 2026-06-19：**58 passed**；CLI check **0 error**（6 条模板 warning 允许）。当前允许存在模板级 warning（插件目录不是独立 git 仓库、无独立 `.github` / `.vscode` 配置），**不能存在 error**。

> 注：`plugin/tests/unit/server/test_plugin_ui_query_service.py` 是 host 侧测试，不在 neko_roast 验证范围内；跨模块禁碰范围以 `AGENTS.md` 为准。

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
