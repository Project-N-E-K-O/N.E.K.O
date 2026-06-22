# 直播中心（neko_roast）开发总结与路线图

> 本文只记录阶段定位、完成状态和下一阶段路线。架构规范、协作规范和测试门禁以 `development.md` 为准；文档职责矩阵以 `docs/README.md` 为准；宿主 / SDK 历史问题以 `devlog.md` 为准。
> 更新日期：2026-06-21

---

## 1. 定位

`neko_roast`（猫娘锐评）是**代号**，真身是一个**直播中心 (Live Center)**：把主播直播的全生命周期接进 NEKO——开播 → 直播间互动（弹幕 / 进场 / 关注 / 礼物 / SC / 舰长）→ 私信 → 主播侧自动化（猫猫操控电脑）。

"首评观众锐评"（观众首次发弹幕 → 猫娘按人设锐评其 B站昵称+头像）只是**第一个落地的垂直切片**。所有未来能力以 neko_roast 的**内部模块**形式集成，不做跨插件宿主。

---

## 2. 架构入口

当前 v0.1 闭环是：

```text
Live Ingest → EventBus → Selection → Roast Pipeline → Runtime → Dashboard
```

详细分层、模块边界、数据边界、pipeline、EventBus 和输出约束不在 roadmap 中重复维护；以 `development.md` 为 Canonical Source。UI 贡献模型以 `ui-architecture.md` 为准。

---

## 3. 已完成进度（均真机验证）

| 阶段 | 内容 | 验证 |
|---|---|---|
| **传输修复** | `message_plane/pub_server.py`：wire 的遗留 `binary_data` 是原始 bytes，`json.dumps` 撞 bytes 抛错被静默吞 → **所有带图 push_message 都到不了 main_server**。改 `json.dumps(default=...)` 转 base64 + 失败记 debug。 | PR [#1843](https://github.com/Project-N-E-K-O/N.E.K.O/pull/1843)，CI 全绿；已写进 main 工作区 |
| **P0 底层硬化** | ① `RoastConfig.dry_run` 安全测试态（pipeline 照跑、`push_roast` 短路不投猫猫）；② `ViewerStore` 加锁防并发丢更新；③ 连接层冒烟 | 单测 + 真机 |
| **P1 吞并连接层** | `danmaku_core.py`(DanmakuListener) + `livedanmaku.py` 吞并进 `modules/bili_live_ingest/`；`BiliLiveIngestModule` 持有监听器（`on_danmaku` fire-and-forget 喂 pipeline）；`connect/disconnect_live_room` 启停真实监听 | 真机：连 81004 → 6 真实观众走完整 pipeline（按 UID 抓真实头像） |
| **DoD** | **真实直播间新观众首条弹幕 → 猫猫全自动开口锐评其昵称+头像** | 真机：dry_run 关 → main 日志见 vision 模型 + send_lanlan_response |
| **P2-T2.1 限流** | `safety_guard.before_output(event)` 按 `rate_limit_seconds` 控最小锐评间隔（直播态生效、沙盒豁免） | 单测 + 真机 |
| **富模型修复** | `livedanmaku.from_danmaku`：`info[7]`（int 大航海等级）被当列表 → 任意弹幕 TypeError 被吞、`on_event("DANMU_MSG")` 永不触发，已修 + 全下标加守卫 | `tests/test_livedanmaku.py` 9 用例 |
| **人气值 UI** | 后端透传的 `viewer_count` 之前没在面板渲染 → `panel.tsx` 加"人气值"卡 + 8 locale | **无需 rebuild 前端**：panel.tsx 由 plugin-manager 用 sucrase **运行时转译**（`hosted/tsxRuntime.ts`），后端已确认供含人气值卡的源码（`hosted-ui/source` 含 `viewer_count`/`panel.stats.viewers`）→ UI 里(重)开 neko_roast 面板即见；待肉眼确认 |
| **P2.5 事件中枢** | 激活 `live_events` 中枢：富模型 `on_event` 接入 + `get_score` 开窗择优（弹幕 / 礼物 / SC / 上舰同窗竞争，舰长/总督/SC/礼物/牌子/高等级/长文本优先）+ 首评即时；轻量 `on_danmaku`→pipeline 直连退役防双锐评；新增 `safety_guard.output_cooldown_remaining()` 对齐窗口与限流冷却 | 单测 `tests/test_live_events.py` 8 用例 + 契约 1 条；**真机✓**（连 81004：一个窗口缓冲 4 条弹幕候选 → `get_score` 挑出舰长 `guard=3/score=1562` 投递，丢另 3 路人；dry_run 全程未投猫；断开后 `live_events.reset()` 清空生效）。gift/SC/guard 接线已单测覆盖，待真机补样本 |
| **配置写竞争（插件侧免疫 + host 修复已进）** | `runtime.update_config` 反转为「先内存生效 → 带预算（4s）尽力持久化、超时/失败不回滚不阻塞」+ `asyncio.Lock` 串行化；host/core 修复 `Fix plugin host config and data root handling (#1884)` / `08b317f6` 已进入当前 `Roast` 分支，插件侧兜底继续保留 | 契约新增 2 用例（`update_config`/`connect` 持久化卡死不阻塞）；host/core 切片 `plugin/tests/unit/core/test_host_storage_layout_env.py` + `plugin/tests/unit/sdk/plugin/test_sdk_v2_plugin_base.py` 已用于验证修复依据；**真机✓**（原 500 的 `update_config{dry_run}`→OK 4.1s+`config_persist_timeout`，`connect`→OK 4.5s 真连上） |
| **旧 bilibili_danmaku 软退役** | 移植 `from_danmaku` `info[7]` 崩溃+字段错位修复到旧插件 `livedanmaku.py`；README/manifest 加弃用横幅（指向 neko_roast、勿同房双连）。未删除（git-tracked 38 文件 + CI/测试引用 + P5 复用其 auth 代码源） | 独立加载 smoke：舰长 `guard=3/vip=True/score=1370`、短数组/空 info 不崩；旧插件无既有单测 |
| **A1 anti-352 for lookup** | `lookup_room_status` 加临时 buvid3（首页 Set-Cookie + 6h 缓存）+ 浏览器 headers + 撞 -352 刷新重试一次 + 成功 60s 缓存；只降频率，彻底消除需登录态(P5) | 契约 +3 用例；**真机 2026-06-17**：buvid3 能抓到、机制通，但本机重度风控 IP 4 房间仍全 -352 → 匿名不足，需 P5 登录态 |
| **A2 直播间链接输入** | `contracts.parse_room_id`（数字 / 链接 `live.bilibili.com/<id>`）；from_mapping + update_config + connect/lookup/set 三入口都过它；action `room_id` schema 收 string、面板送原始串；占位符 8 locale 同步 | 契约 +3 用例；**真机 2026-06-17 ✅**：action 传 4 种链接形态均正确解析房号（含 query/h5）；面板侧待前端重开面板肉眼验 |
| **P5 登录态（登录部分）** | Fernet 加密凭据 store + 扫码登录服务（移植旧插件）+ runtime 4 action + 凭据接进 identity/ingest/lookup（**根治 -352、恢复头像**，credential=None 时零回归）+ 面板登录卡 + 8 locale；本地注销 | 契约/store +6 用例；**真机 2026-06-17 ✅**（用户扫码本人账号 uid 1408555810）：同房 81004 登录前匿名 lookup -352、登录后 -352 彻底消失 + 头像抓取恢复（`has_avatar:true`）+ 凭据加密落盘可解密回环。私信/写能力留待后续；登录卡 UI 肉眼验为非阻塞收尾 |
| **UI 架构重构（6-tab 生命周期）** | 薄外壳 + 模块贡献：6 个一级页（控制台/直播间互动/观众/私信/自动化/⚙设置 + dev 条件追加）；`ModuleRegistry.setup_all/teardown_all` 逐模块 try/except 隔离 + `degraded` 标记；`BaseModule.config_schema()` 契约 + schema 驱动功能卡（boolean→Toggle / select→Select）；「一张嘴」切分（功能参数进卡、平台参数留设置）。契约文档 `docs/ui-architecture.md` | 单测 +4（`test_module_registry.py`）；契约 `test_panel_uses_six_top_level_tabs_in_order`；panel transpile OK |
| **观众档案本地 JSON 持久化** | 历史上用于绕开宿主 `store.enabled` 构造期冻结 bug（见 `docs/devlog.md`），当前作为简洁可审计的档案写入边界继续保留：`viewer_store.py` 改写本机 `viewer_profiles.json`（原子写 tmp+os.replace + asyncio 锁 + 不可写回退默认目录 + audit）；dashboard 暴露 `viewer_store` 状态。#1884 已修复 host 数据根刷新；`viewer_store_dir` 自定义入口仍暂时屏蔽，待插件侧重新回归后恢复 | 单测 +4（`test_viewer_store.py`）；默认目录持久化可用；自定义目录入口暂缓 |
| **事件中枢地基（EventBus 真订阅分发）** | 把接入与处理解耦——`bili_live_ingest` 把富模型包成 `LiveEvent` 统一信封（`contracts.LiveEvent`：type/uid/payload/source/ts/schema_version/raw）发布到 `EventBus`；`EventBus` 升级为真订阅分发（`subscribe(type,handler,owner)` / `publish`），每订阅者隔离 + 归属（owner）+ audit（`event_handler_failed`）+ 无订阅者静默丢弃。`live_events` 改为**经 bus 订阅 `"danmaku"` / `"gift"` / `"super_chat"` / `"guard"`** 的示范订阅者（`submit()` 签名不变、内部择优复用既有 pipeline 语境）。**这是「分发给其他开发者各写各事件 handler」的核心契约**（development.md「直播事件中枢（EventBus）」含第三方加 handler 配方）| 单测 +8（`test_event_bus.py`）+ 契约 +1；端到端经 bus 的 `test_live_listener_routes_rich_event_through_hub_to_pipeline` 仍绿；gift/SC/guard 接线已单测覆盖，专属 P3 handler 待做 |
| **可靠性收尾（兜底层②④收口）** | ① UI 错误边界：`panel.tsx` `safeModuleCard` 用 try/catch 包每张互动模块卡的同步渲染（hosted-ui runtime 无 class error boundary），未来第三方模块 `config_schema`/渲染抛错只塌成一张降级卡（`panel.modules.renderError`）不黑屏整盘（兜底层④，见 ui-architecture §4）。② 模块 `on_enable/on_disable` 生命周期钩子：`ModuleRegistry.enable/disable` 隔离调用（单点失败标 degraded + audit），地基件，待接 per-module 启停真实调用方 | 单测 +3（`test_module_registry.py`）+ 契约 +1（`test_panel_wraps_module_cards_in_error_boundary`）；panel transpile OK；i18n +1 键 8×228 |

测试基线：`uv run pytest plugin/plugins/neko_roast/tests -q` → **58 passed**；CLI check **0 error**（6 条模板 warning 允许）。PR1903-PR1909 已进入主线；后续改动按 `development.md` 的协作规范拆分 Slice，不混入非本插件改动。

---

## 4. 关键决策与历史问题入口

本节只保留路线图相关的决策摘要。宿主 / SDK 侧历史问题、配置写竞争、storage layout、message plane 等事故记录以 `devlog.md` 和 `development.md` 对应章节为准。

- **吞并策略**：取 `bilibili_danmaku` 的**连接+解析层**（`danmaku_core`/`livedanmaku`，含匿名 WS、WBI 签名、临时 buvid3 反 -352 风控、zlib/brotli 解压、心跳、多服务器故障转移、断线重连）；**弃**其自带 LLM/orchestrator/memory（neko_roast 走 `dispatcher → main_server` 统一人设）。参照系：弹幕姬 `copyliu/bililive_dm` 的小插件契约（4 事件 + 统一模型 + 故障隔离）作为未来扩展点设计蓝本。
- **弹幕不含头像**：B站 DANMU_MSG 无头像 URL；头像由下游 `bili_identity` **按 UID 抓取**。
- **配置写竞争（反复咬人）**：host 的 `update_own_config` 持久化曾偶发卡 10s 超时（咬过 dev 模式切换、disconnect）。`connect/disconnect_live_room` 已改为**内存直设 `live_enabled`**（gate/safety 共享同一 config 对象，即时生效）绕开；host/core 修复 `Fix plugin host config and data root handling (#1884)` / `08b317f6` 已进入当前 `Roast` 分支。
  - **2026-06-16 P2.5 真机验证时复现并确认更严重**：在「只重后端不重前端」的环境下（正是 §5 警告的触发条件），`update_config{dry_run}` 和 `connect_live_room`（其内部 `set_live_room` 仍走 `update_config` 持久化 `live_room_id`）**稳定** 500 / `Entry timed out after 10.0s`，且 `runtime.update_config` 的 except 内存兜底**没机会跑**（host 在兜底前就杀了 entry，audit 无 `config_persist_failed`）。即 connect 当前也会被这个 race 卡住，不止「偶发」。
  - **2026-06-16 插件侧根治（症状消除，已真机验证）**：`runtime.update_config` 反转为**先内存生效（`_activate_config`，runtime 行为以内存为准、即时权威）→ 再带预算尽力持久化**（`_persist_config_best_effort`：`asyncio.wait_for(_, _CONFIG_PERSIST_BUDGET_SECONDS=4.0)`，超时记 `config_persist_timeout`、失败记 `config_persist_failed`，**都不回滚、不阻塞**）；并用 `asyncio.Lock` 串行化插件自身并发写。host 持久化即便异常，action 也在 ≤4s 内成功返回、配置已生效。真机：reload 新代码后，原本 500 的 `update_config{dry_run}` → **OK 4.1s + dry_run=True + audit `config_persist_timeout`**；`connect_live_room` → **OK 4.5s + 真连上**。#1884 已修复 host/core 侧配置与数据根处理，但插件侧兜底继续保留。
- **ws close 握手挂起**：`websockets` 的 `ws.close()` 等关闭握手可达 ~10s → `stop_listening` 用 `asyncio.wait_for` 限时（先 cancel task 再 bounded await），断开稳定 ~4s。
- **不阻塞接收循环**：`on_danmaku` 用 fire-and-forget task 跑 pipeline，并发由 `safety_guard.queue_limit` 兜底。
- **`rate_limit_seconds=0` bug**：`from_mapping` 用 `int(x or 20)` 把 0 吞成 20 → 限流关不掉，已改为显式 None 判断。

---

## 5. 运行与验证入口

运行步骤、action 调用、日志位置和用户操作不在 roadmap 中维护，避免和使用文档漂移：

- 用户/主播流程：见 `quickstart.md`。
- 开发者运行态和常用 action：见 `developer-guide.md`「开发环境 & 运行态」。
- 测试门禁：见 `development.md`「测试门禁」和 `AGENTS.md`「Required Checks」。

---

## 6. 路线图（短线已完，下面是长线，按需推进）

- **P2.5 事件中枢/事件族（地基）**：✅ **已完成当前地基**——接入富模型 `on_event` + `get_score()` 开窗缓冲值优选（`live_events` 中枢，`DANMU_MSG` / `SEND_GIFT` / `SUPER_CHAT_MESSAGE` / `GUARD_BUY` 同窗竞争；首评即时；见 development.md「直播事件中枢」）。**完整版进度**：~~定 `LiveEvent` 统一信封（`type/uid/payload/ts/source/schema_version/raw`）~~ ✅（`contracts.LiveEvent`）；~~`EventBus` 升级为真正的订阅分发（每订阅者隔离+归属+audit）~~ ✅（`core/event_bus.py`，见 development.md「直播事件中枢（EventBus）」）；~~`InteractionModule` 补 `on_enable/on_disable`~~ ✅（`ModuleRegistry.enable/disable` 隔离调用）；~~窗口择优扩到非弹幕事件~~ ✅（gift/SC/guard 参与 `get_score` 竞争）。**剩**：按 `event.type` 做专属 handler / prompt（P3）。
- **P4 档案/记忆**：✅ **本地 JSON 持久化地基已落地**（`viewer_store.py` → `viewer_profiles.json`，目录可配置 `viewer_store_dir`，绕开宿主 store 冻结 bug，见 development.md「数据边界」/ devlog.md）。**剩余**：`contribution_rank` + `watch_time` + 跨场观众记忆。
- **P5 私信**（独立域）：`bili_dm_ingest`（收）+ `bili_write_tools`（发，需登录态）。扫码登录直接复用 `bilibili_danmaku/bili_auth_service.py`（QR 生成→轮询→拿 SESSDATA/bili_jct/buvid3 加密存）。
- **P6 主播自动化**：`automation_ops`（猫猫操控电脑/读公开资料，复用 NEKO CUA/agent）。

---

## 7. 待拍板（动手前先定）

1. ~~**值优选策略**：爆量时全评 / `get_score` 优选 / 采样？~~ ✅ **已定**：`get_score` 开窗优选 + 首评即时（P2.5 已落地，见 development.md「直播事件中枢」）。
2. **`automation_ops` 归属**：直播中心内的模块，还是它去调用的独立能力？
3. **登录态 cookie**怎么拿/存（P5 前必答；P0–P3 匿名读弹幕即可）。
4. ~~**退役旧 `bilibili_danmaku`**（与 neko_roast 同房间会双连冲突；旧插件有同款 from_danmaku bug）~~ ✅ **2026-06-16 软退役（fix + 标记弃用，未删除）**：①把同款 `from_danmaku` `info[7]` 字段错位崩溃 bug 移植修复到 `bilibili_danmaku/livedanmaku.py`（含 vip/svip 错位）；②README + manifest description 加弃用横幅（指向 neko_roast、警告勿同房双连）。**未删除**：它 git-tracked（38 文件）+ 被 CI/host 测试引用，且是 P5 复用的 `bili_auth_service.py` 代码源、neko_roast 尚未功能对等——完整删除待 neko_roast 对等后单独走 branch/PR。
5. ~~**配置写竞争根治**（host 级，可能与在途 WIP 相关）~~ ✅ **插件侧已根治症状**（`update_config` 内存先行 + 带预算尽力持久化，见 §4 与 development.md「配置持久化与写竞争」）；host/core 修复 `Fix plugin host config and data root handling (#1884)` / `08b317f6` 已进入当前 `Roast` 分支。

---

## 8. 已知限制

- 自适应焦点由 LLM 判断，非确定性；`pendant` 依赖 bilibili_api。
- B站协议会变（WBI / 风控），需跟进。
- `lookup_live_room` 的 HTTP 路径已做 A1 反 -352 降频（临时 buvid3、浏览器 headers、撞 -352 刷新重试一次、成功缓存），但重度风控 IP 仍可能失败；已把失败码翻成人话（`bili_live_ingest._friendly_lookup_message`：-352→"风控校验失败，稍后重试/换网络/登录"，并在面板 Alert 显示该 message 而非死写"请检查房间号"），**根治**需登录态（P5）。注意：查询失败 ≠ 监听失败，弹幕 WS 路径通常仍可连。
- 插件侧配置持久化仍按“内存先行 + 4s 预算”看待：host 持久化异常时配置仍内存即时生效，但**那一次的持久化会失败**（`config_persist_timeout`），即该次改动不落盘——stop/start 后可能还原成 `plugin.toml` 里的值。无竞争 / 无异常时应秒过。
- ~~富模型 `on_event` 尚未被 pipeline 消费~~ ✅ P2.5 已由 `live_events` 中枢消费，`on_danmaku`→pipeline 直连已退役；`medal_info` 字段顺序沿用旧实现，精确化留待事件族梳理。

---

## 9. ⚠️ 不要碰的在途 WIP

跨模块禁碰范围和 Reviewer 硬规则以 `AGENTS.md` 为准。路线图只保留下一阶段方向，不维护临时工作区状态。

---

## 10. 下一阶段 TODO（接手即可做）

按性价比 / 依赖排序。A 组是健壮性、可独立小步做；B 组是功能路线（详见 §6）。

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
   - **配置写竞争**：插件侧已免疫（`update_config` 内存先行 + 带预算持久化，见 §4 / development.md「配置持久化与写竞争」）；#1884 已进入当前 `Roast`，后续改动只需保持插件侧兜底不退化。
   - **`PluginStore.store.enabled` 构造期冻结**：#1884 已让 runtime helpers 可在 effective config 就绪后刷新；neko_roast 仍保留观众档案本地 JSON 边界，不回切 PluginStore。
   - **插件数据跟随 selected_root**：#1884 已在插件子进程启动前刷新 storage layout env；`viewer_store_dir` 自定义入口仍暂时屏蔽，下一阶段应先做插件侧回归，再恢复 UI 入口。

### B. 功能路线（详见 §6）

4. ~~**P2.5 完整版（事件中枢地基）**：`LiveEvent` 统一信封、`EventBus` 真订阅分发（隔离 + 归属 + audit）、`InteractionModule` 补 `on_enable/on_disable`、窗口择优扩到 gift/SC/guard~~ ✅（见 §3「事件中枢地基」+ development.md「直播事件中枢（EventBus）」）。
5. **P4 档案 / 记忆**：`contribution_rank` + `watch_time` + 跨场观众记忆（v0.1 观众档案只存 8 个字段，见 development.md「数据边界」）。
6. **P5 私信 + 登录**：`bili_dm_ingest`（收）+ `bili_write_tools`（发，需登录态）+ 扫码登录（复用 `bilibili_danmaku/bili_auth_service.py`：QR 生成→轮询→拿 SESSDATA/bili_jct/buvid3）。**顺带根治 -352**（登录态风控等级断崖下降）。
7. **P6 主播自动化**：`automation_ops`（复用 NEKO CUA/agent）。
8. **收官：删除 bilibili_danmaku**：neko_roast 功能对等（尤其 P5 复用完其 `bili_auth_service.py`）后，正式删除旧插件 38 个 tracked 文件 + 清 CI/host 测试/前端引用，走 git branch/PR。当前为**软退役**（fix + 弃用横幅，见 §7-5）。

---

## 11. 项目成熟度与分发就绪度评估（2026-06-18）

> 一次诚实的自评，供接手者判断「现在处于什么阶段、离团队级分发还差什么」。结论：**架构与可靠性产品级，工程治理（版本控制 / CI）缺课，功能完成度还在 v0.1 一个切片**——底子很好的优秀地基，但尚未「交付就绪」。

| 维度 | 评级 | 依据 |
|---|---|---|
| 架构设计 | A− | 清晰分层 + 四条不变量，且用契约测试**锁死设计意图**（不只测行为）|
| 可靠性工程 | A− | 五层兜底是真功夫：`safety_guard`（滑窗失败计数→自动急停 / 队列溢出→降级 / 限流）、`pipeline`（每步审计 + `finally` 清队列）、`dispatcher`（dry_run + 头像压不进预算则降级纯文字）|
| 代码质量 | B+ | `pipeline`/`safety_guard`/`dispatcher` 教科书级；`panel.tsx` 单文件 1200 行偏胖 |
| 文档 | A | 「无文档=未完成」真在执行；但偏厚、跨文档有同事实冗余 |
| 测试 | B | 58 个插件单测/契约扎实；但硬骨头（真连 B站 / 视觉 / 消息面 / 面板渲染）只真机验、未进 CI |
| 工程治理 | B− | 插件已提交并推送到 `Roast` 分支，开始有提交轨迹；仍缺主仓门禁 CI / PR 评审轨迹 |
| 功能完成度 | 早期 | v0.1 只有「首评锐评」一个垂直切片落地 |

**优点（有代码支撑）**：可靠性刻进代码而非口号；对抗真实世界的疤痕（-352 风控、配置写竞争免疫、消息面吞图 bug 修复）；契约测试锁架构红线；克制复用 + 隐私自觉（凭据加密不落 log/UI、头像 bytes 不落盘）。


**分发就绪 TODO（按优先级）**：
1. **上 CI / 门禁**：把 `plugin/plugins/neko_roast/tests` + CLI check 跑成主仓门禁。这步与「要分发」目标直接对齐。
2. **回归 host/core 修复后的插件侧状态**：确认默认档案目录、storage layout、`viewer_store_dir` 隐藏状态和配置持久化提示都与当前实现一致。
