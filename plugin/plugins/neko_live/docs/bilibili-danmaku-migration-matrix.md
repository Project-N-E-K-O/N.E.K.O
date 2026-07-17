# 旧 `bilibili_danmaku` 能力迁移矩阵

> 更新：2026-07-17。本文是旧插件退役前的能力审计记录，不代表所有“应吸收”项已经实现，也不授权直接删除旧目录。

## 1. 结论

旧 `bilibili_danmaku` 共有 **47 个 `@plugin_entry` 公开入口**。本次逐项核对代码后的建议分布为：

| 决策 | 数量 | 含义 |
|---|---:|---|
| 已由 NEKO Live 替代 | 14 | 已有同等产品结果；不迁移旧实现 |
| 应吸收 | 2 | 只迁移收窄后的产品能力，不复制旧模块 |
| 应拆独立插件 | 19 | 属于通用 B站 内容工具或写操作，不进入直播插件 |
| 明确废弃 | 12 | 与统一 dispatcher、隐私或权限边界冲突，不迁移 |

因此，旧插件**现在仍不能删除**。下一步只处理本文标记的获批迁移；最终退役必须在所有前置项关闭后，从最新 `main` 建独立分支完成。

## 2. 分类规则

- **已由 NEKO Live 替代**：比较产品结果，不要求保留旧入口名、旧 UI 或旧内部实现。
- **应吸收**：对直播产品仍有价值，但必须按 NEKO Live 的 provider-neutral、隐私、安全和唯一 dispatcher 契约重新实现。
- **应拆独立插件**：能力本身可能有价值，但不是直播接入或直播互动职责；不得污染 `bili_live_ingest`。
- **明确废弃**：旧能力违反当前产品边界，或已有更安全的替代方案，不保留兼容入口。

## 3. 47 个公开入口

### A. 旧独立 LLM 与指导系统

| # | 旧入口 | 决策 | 依据与后续 |
|---:|---|---|---|
| 1 | `get_bg_llm_config` | 明确废弃 | NEKO Live 只能走统一 Pipeline / Safety / `NekoDispatcher`；不再维护第二套模型配置。 |
| 2 | `test_bg_llm` | 明确废弃 | 不允许直播插件绕过宿主 provider 配置直接探测任意 LLM URL/API key。 |
| 3 | `get_guidance_config` | 明确废弃 | 旧 aggregator/orchestrator/agent 配置已由模块化直播策略、Dashboard 状态和开发者工具替代。 |
| 4 | `update_guidance_config` | 明确废弃 | 不迁移旧 JSON 指导编排器；主播设置只使用 `RoastConfig` 中有真实消费者的字段。 |
| 5 | `test_guidance` | 明确废弃 | 调试输入统一走 `developer_sandbox`，仍须经过现有 pipeline 与 dispatcher。 |

### B. 直播控制、状态与输出目标

| # | 旧入口 | 决策 | 依据与后续 |
|---:|---|---|---|
| 6 | `set_room_id` | 已由 NEKO Live 替代 | `set_live_room` 支持房号/链接、二次确认和 provider 路由。 |
| 7 | `set_interval` | 已由 NEKO Live 替代 | 旧“批量推送间隔”不再成立；现由 `activity_level`、Selection、hosting/support cooldown 共同控制节奏。 |
| 8 | `send_danmaku` | 应拆独立插件 | B站写操作必须进入未来 `bili_write_tools`，单独做登录态、权限、确认和风控评审。 |
| 9 | `get_danmaku` | 已由 NEKO Live 替代 | 实时事件已经进入 EventBus、Selection、recent result 与隐私安全的 Runtime Timeline；不恢复原文拉取入口。 |
| 10 | `get_status` | 已由 NEKO Live 替代 | Hosted UI context、connection snapshot、模块状态和开发者诊断已覆盖。 |
| 11 | `set_target_lanlan` | 已由 NEKO Live 替代 | 正常输出从宿主上下文解析目标；只有开发者沙盒允许显式 `target_lanlan`，不保留普通用户全局 setter。 |
| 12 | `set_master_bili_account` | 应吸收 | 吸收为“主播账号身份保护”，但维护者已决定延期。未来只从已验证登录凭据派生 UID，并要求显式确认；不得让 owner/master 关系进入 prompt，也不新增自由文本姓名匹配。详细契约见 `development.md`「延期能力：主播账号身份保护」。 |
| 13 | `set_danmaku_max_length` | 应拆独立插件 | 该字段只服务 B站发弹幕；跟随未来 `bili_write_tools`，不回流只读 ingest 或直播回复字数契约。 |
| 14 | `connect` | 已由 NEKO Live 替代 | `connect_live_room` 具备登录/匿名兜底校验、房间 ownership 与监听生命周期保护。 |
| 15 | `disconnect` | 已由 NEKO Live 替代 | `disconnect_live_room` 与会话任务回收已覆盖。 |
| 16 | `open_ui` | 已由 NEKO Live 替代 | 插件中心 Hosted UI 已提供控制台、互动、观众、设置和开发者工具。 |

### C. 登录与凭据

| # | 旧入口 | 决策 | 依据与后续 |
|---:|---|---|---|
| 17 | `save_credential` | 明确废弃 | 不再暴露 SESSDATA/bili_jct 等原始密钥写入口；正常路径使用扫码登录与加密 store。 |
| 18 | `clear_credential` | 已由 NEKO Live 替代 | `bili_logout` 删除本机加密凭据和密钥。 |
| 19 | `reload_credential` | 已由 NEKO Live 替代 | 凭据由 runtime/store 生命周期加载；不保留人工 reload。 |
| 20 | `bili_check_credential` | 已由 NEKO Live 替代 | `bili_login_status` 返回收窄后的登录状态。 |
| 21 | `bili_login` | 已由 NEKO Live 替代 | 扫码登录服务已迁入 `adapters/bili_auth_service.py`。 |
| 22 | `bili_login_check` | 已由 NEKO Live 替代 | 扫码轮询、加密持久化与连接凭据注入已覆盖。 |

### D. B站通用内容读取

以下入口不属于直播生命周期。若继续维护，应共同进入独立的只读 `bili_content_tools` 插件；该插件不得导入 NEKO Live runtime、viewer store 或直播 dispatcher。

| # | 旧入口 | 决策 | 依据与后续 |
|---:|---|---|---|
| 23 | `bili_search` | 应拆独立插件 | 通用视频搜索。 |
| 24 | `bili_hot_videos` | 应拆独立插件 | 通用热门视频读取；NEKO Live 主动营业已有收窄、安全的公开素材源，不需要暴露通用工具。 |
| 25 | `bili_hot_buzzwords` | 应拆独立插件 | 通用热词读取。 |
| 26 | `bili_weekly_hot` | 应拆独立插件 | 通用每周热门读取。 |
| 27 | `bili_rank` | 应拆独立插件 | 通用分区排行读取。 |
| 28 | `bili_video_info` | 应拆独立插件 | 通用视频详情读取。 |
| 29 | `bili_comments` | 应拆独立插件 | 通用评论读取，需独立处理分页、隐私和内容安全。 |
| 30 | `bili_subtitle` | 应拆独立插件 | 通用字幕读取。 |
| 31 | `bili_danmaku` | 应拆独立插件 | 这是录播视频弹幕读取，不是直播弹幕 ingest。 |
| 32 | `bili_user_info` | 应拆独立插件 | 通用公开用户信息读取；直播头像/身份已由 `bili_identity` 收窄处理。 |
| 33 | `bili_user_videos` | 应拆独立插件 | 通用用户投稿读取。 |
| 34 | `bili_favorite_lists` | 应拆独立插件 | 账号收藏夹读取，需独立登录权限评审。 |
| 35 | `bili_favorite_content` | 应拆独立插件 | 账号收藏内容读取，需独立登录权限评审。 |

### E. B站写操作与工具目录

| # | 旧入口 | 决策 | 依据与后续 |
|---:|---|---|---|
| 36 | `bili_reply` | 应拆独立插件 | 评论/回复属于高风险写操作，必须显式确认并进入独立 `bili_write_tools`。 |
| 37 | `bili_send_dynamic` | 应拆独立插件 | 发动态含文本、图片、话题和定时发布权限，不属于直播插件。 |
| 38 | `bili_send_message` | 应拆独立插件 | 私信是高隐私写操作，不得进入直播监听或自动互动链路。 |
| 39 | `bili_list_tools` | 应拆独立插件 | 只在独立内容/写工具插件存在时提供其能力目录。 |
| 40 | `ask_neko_bili_reply` | 明确废弃 | 不保留“模型生成后直接写入”的复合入口；未来必须生成、预览、确认、执行分离。 |
| 41 | `ask_neko_bili_send_dynamic` | 明确废弃 | 同上；禁止模型无独立确认直接发动态。 |
| 42 | `ask_neko_bili_send_message` | 明确废弃 | 同上；尤其不得自动生成并发送私信。 |
| 43 | `ask_neko_send_danmaku` | 明确废弃 | 同上；直播发言能力若获批，也必须由独立写工具和显式确认控制。 |

### F. 历史存储与统计

| # | 旧入口 | 决策 | 依据与后续 |
|---:|---|---|---|
| 44 | `query_danmaku` | 明确废弃 | NEKO Live 不持久保存或检索原始弹幕历史；只保留受限、脱敏的本场上下文。 |
| 45 | `query_gifts` | 应吸收 | 仅吸收为未来“可信支持事件账本”的脱敏聚合查询；不得保存 provider raw、伪礼物文本或无限期用户明细。本阶段尚未实现。 |
| 46 | `query_interact` | 明确废弃 | 不持久保存进场/关注流水；当前产品也不对这类事件自动开口。 |
| 47 | `query_stats` | 已由 NEKO Live 替代 | 本场非排名统计由观众页/运行状态覆盖；旧弹幕排行、复读原文和贡献排行不迁移。 |

## 4. 非公开内部能力

| 旧组件 | 决策 | 当前对应或后续边界 |
|---|---|---|
| `danmaku_core.py` / `livedanmaku.py` | 已由 NEKO Live 替代 | 已收窄迁入 `modules/bili_live_ingest/`，只负责连接、解析、可信事件投影和 EventBus 发布。 |
| `bili_auth_service.py` | 已由 NEKO Live 替代 | 已迁入 `adapters/bili_auth_service.py`，凭据由加密 store 管理。 |
| `TimeWindowAggregator` / `GiftAggregator` | 已由 NEKO Live 替代 | `live_events` 负责 Selection，`live_support_events` 负责可信支持事件去重、连击与有界优先调度。 |
| `BiliContentService` | 应拆独立插件 | 按只读与写操作进一步分包；不得放进 `bili_live_ingest`。 |
| `DanmakuStorage` SQLite | 部分吸收、其余废弃 | 原始弹幕、进场、关注、排行不迁移；仅可信支持事件的脱敏有限账本进入后续独立设计。 |
| `UserRecordManager` | 部分替代、部分候选吸收 | 基础档案、安全派生偏好、重置/删除已由 `viewer_store` 覆盖；block/not-reply 等显式主持人治理需另做产品评审，不复制旧 JSON 模型。 |
| `DanmakuMemory` / `DanmakuAnalyzer` | 已由 NEKO Live 替代 | 房间短期主题、节奏和接话上下文由 `room_topic`、Selection、session context 与 hosting 模块承担；不恢复独立 LLM 分析。 |
| `llm_client.py` / `orchestrator.py` / `background_llm_agent.py` / `intelligence_card.py` | 明确废弃 | 与宿主统一 provider、pipeline、safety、dispatcher 重复且可能产生双输出。 |
| `http_api.py` 的 `netProxy` | 明确废弃 | 不在插件内开放通用网络代理；避免 SSRF、额外端口和分发负担。 |
| `http_api.py` 的头像缓存 | 已由 NEKO Live 替代 | `bili_identity` 只投影所需头像/身份信息，不提供长期原始头像缓存接口。 |
| `http_api.py` 的事件注入 | 已由 NEKO Live 替代 | 使用 `developer_sandbox` 与 `submit_manual_live_event`，仍走统一安全链路。 |
| `http_api.py` 的 status/ping | 已由 NEKO Live 替代 | Hosted UI context、runtime snapshot 与插件宿主健康状态覆盖。 |
| `ws_bridge.py` / 旧外部客户端桥 | 明确废弃 | 当前没有分发依赖者；不得为兼容旧 UI 继续维护第二条事件/状态通道。 |
| `static/index.html` / 独立 dashboard | 已由 NEKO Live 替代 | 当前 Hosted UI 四区结构是唯一普通用户面板。 |

## 5. 获批迁移的建议顺序

1. ⏭ **主播账号身份保护（已记录，延期）**：产品结果与安全契约已写入 `development.md`；当前不实现。未来只从已验证登录 UID 派生并要求显式确认，作用于 viewer/session gate；不新增“主人姓名”自由文本配置，不把关系信息写入 prompt。
2. **可信支持事件账本设计（当前下一项）**：先写独立 spec，限定 provider 证据、脱敏字段、容量、保留期、清理、导出和查询；不得直接复用旧 SQLite schema。
3. **通用 B站 工具拆分决策**：只有维护者确认仍需要内容读取或写入时，才分别设计 `bili_content_tools` / `bili_write_tools`；写工具必须带显式确认与权限审计。
4. **旧插件退役**：上述获批项完成、其余项明确关闭后，再从最新 `main` 建独立删除 PR，并迁移构建注释与通用测试夹具引用。

## 6. 退役门槛

- 47 个入口均有稳定决策，且没有未分类项。
- 所有“应吸收”项已实现、验证，或由维护者明确取消。
- 独立插件项已迁移，或明确决定不再维护。
- NEKO Live 与旧插件不再需要同房并行加载。
- 删除 PR 不包含新功能、不堆叠未合并 PR，并从最新 `main` 创建。
- 删除后完整插件测试、CLI check、文档链接和分发构建检查通过。
