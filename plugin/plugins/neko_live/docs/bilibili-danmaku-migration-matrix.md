# 旧 `bilibili_danmaku` 能力迁移矩阵

本文是旧插件退役前的能力审计。它记录产品决策，不授权直接复制旧实现或删除旧目录。

## 结论

旧插件共有 47 个公开入口：

| 决策 | 数量 | 含义 |
|---|---:|---|
| 已由 NEKO Live 替代 | 14 | 产品结果已有替代，不迁移旧实现 |
| 候选吸收 | 2 | 先做独立产品/安全设计，再按新边界实现 |
| 应拆独立插件 | 19 | 不属于直播中心职责 |
| 明确废弃 | 12 | 与统一输出、隐私或权限边界冲突 |

因此旧插件目前不能删除。最终退役必须关闭所有前置项，并从最新 `main` 创建独立、非堆叠删除 PR。

## 公开入口

### 旧 LLM 与指导系统

| 旧入口 | 决策 | 说明 |
|---|---|---|
| `get_bg_llm_config` | 废弃 | 统一使用宿主 provider 与 NEKO Pipeline |
| `test_bg_llm` | 废弃 | 不让直播插件探测任意 LLM URL/API key |
| `get_guidance_config` | 废弃 | 由模块化策略和 Dashboard 替代 |
| `update_guidance_config` | 废弃 | 不迁移旧 JSON orchestrator |
| `test_guidance` | 废弃 | 调试输入统一走 Developer Sandbox |

### 直播控制、状态与目标

| 旧入口 | 决策 | 当前对应或后续 |
|---|---|---|
| `set_room_id` | 已替代 | 房号/链接、查询和二次确认 |
| `set_interval` | 已替代 | `activity_level`、Selection 与共享冷却 |
| `send_danmaku` | 独立插件 | 未来高风险 B 站写工具 |
| `get_danmaku` | 已替代 | EventBus、recent result、Timeline；不恢复原文拉取 |
| `get_status` | 已替代 | Hosted UI context 与 runtime snapshot |
| `set_target_lanlan` | 已替代 | 宿主上下文；显式目标只留开发者沙盒 |
| `set_master_bili_account` | 候选吸收 | 延期的主播身份保护，只能从已验证登录 UID 派生 |
| `set_danmaku_max_length` | 独立插件 | 跟随未来发弹幕写工具 |
| `connect` | 已替代 | `connect_live_room` |
| `disconnect` | 已替代 | `disconnect_live_room` 与会话回收 |
| `open_ui` | 已替代 | 插件中心 Hosted UI |

### 登录与凭据

| 旧入口 | 决策 | 当前对应或理由 |
|---|---|---|
| `save_credential` | 废弃 | 不暴露原始凭据写入口 |
| `clear_credential` | 已替代 | provider 注销与加密 store 删除 |
| `reload_credential` | 已替代 | runtime/store 生命周期自动加载 |
| `bili_check_credential` | 已替代 | 收窄后的登录状态 |
| `bili_login` | 已替代 | 扫码登录服务 |
| `bili_login_check` | 已替代 | 扫码轮询与加密保存 |

### B 站通用内容读取

以下能力不属于直播生命周期。若继续维护，应进入独立只读内容插件，不得依赖 NEKO Live runtime、viewer store 或 Dispatcher。

| 旧入口 | 决策 |
|---|---|
| `bili_search` | 独立内容插件 |
| `bili_hot_videos` | 独立内容插件 |
| `bili_hot_buzzwords` | 独立内容插件 |
| `bili_weekly_hot` | 独立内容插件 |
| `bili_rank` | 独立内容插件 |
| `bili_video_info` | 独立内容插件 |
| `bili_comments` | 独立内容插件，单独处理分页与隐私 |
| `bili_subtitle` | 独立内容插件 |
| `bili_danmaku` | 独立内容插件；它是录播弹幕，不是直播 ingest |
| `bili_user_info` | 独立内容插件；直播身份已收窄处理 |
| `bili_user_videos` | 独立内容插件 |
| `bili_favorite_lists` | 独立内容插件，需登录权限评审 |
| `bili_favorite_content` | 独立内容插件，需登录权限评审 |

### B 站写操作

| 旧入口 | 决策 | 说明 |
|---|---|---|
| `bili_reply` | 独立写插件 | 预览、确认、执行分离 |
| `bili_send_dynamic` | 独立写插件 | 单独权限与风控评审 |
| `bili_send_message` | 独立写插件 | 高隐私写操作 |
| `bili_list_tools` | 独立插件 | 只列对应内容/写工具 |
| `ask_neko_bili_reply` | 废弃 | 禁止模型生成后直接写入 |
| `ask_neko_bili_send_dynamic` | 废弃 | 禁止模型无确认发动态 |
| `ask_neko_bili_send_message` | 废弃 | 禁止模型无确认发私信 |
| `ask_neko_send_danmaku` | 废弃 | 禁止模型无确认发弹幕 |

### 历史存储与统计

| 旧入口 | 决策 | 当前对应或后续 |
|---|---|---|
| `query_danmaku` | 废弃 | 不保存原始弹幕历史 |
| `query_gifts` | 候选吸收 | 未来可信支持事件账本；先做脱敏、容量与保留期设计 |
| `query_interact` | 废弃 | 不保存进场/关注流水 |
| `query_stats` | 已替代 | 本场非排名统计；不迁移原文和贡献排行 |

## 非公开内部能力

| 旧组件 | 决策 |
|---|---|
| `danmaku_core.py` / `livedanmaku.py` | 已收窄迁入 `bili_live_ingest` |
| `bili_auth_service.py` | 已迁入 adapters，凭据进入加密 store |
| `TimeWindowAggregator` / `GiftAggregator` | 由 `live_events` 与 `live_support_events` 替代 |
| `BiliContentService` | 候选拆为只读内容与写工具插件 |
| `DanmakuStorage` SQLite | 原始流水废弃；只评审未来脱敏支持账本 |
| `UserRecordManager` | 基础档案已替代；主持人治理另做产品评审 |
| `DanmakuMemory` / `DanmakuAnalyzer` | 由短期主题、Selection 与 Hosting 替代 |
| 旧 LLM / orchestrator / agent | 废弃，避免双模型与双输出 |
| 通用 `netProxy` | 废弃，避免 SSRF 与分发负担 |
| 旧头像缓存 HTTP API | 由收窄 identity 投影替代 |
| 旧事件注入 | 由 Developer Sandbox 替代 |
| 旧 status/ping | 由 Hosted UI context 与 runtime snapshot 替代 |
| `ws_bridge.py` | 废弃，不维护第二条事件/状态通道 |
| 独立 dashboard | 由 Hosted UI 替代 |

## 后续顺序

1. 主播账号身份保护保持延期，重新启动前需维护者拍板。
2. 可信支持事件账本先写独立设计，明确证据、字段、容量、保留期、清理、导出和查询。
3. 只有确认仍有用户需求时，才设计独立内容插件和写工具插件。
4. 所有候选项关闭后，从最新 `main` 创建只做删除与引用清理的独立 PR。

## 退役门槛

- 47 个入口没有未分类项；
- 两个候选吸收项已实现、验证，或由维护者明确取消；
- 独立插件项已迁移，或明确不再维护；
- NEKO Live 与旧插件不再需要同房并行加载；
- 删除 PR 不包含新功能，也不依赖未合并 PR；
- 删除后插件测试、CLI check、文档链接和分发构建通过。
