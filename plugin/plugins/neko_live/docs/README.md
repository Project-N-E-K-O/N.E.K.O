# NEKO Live 文档索引

这里是 `neko_live` 的文档地图。每类事实只保留一个权威来源；其他文档只做摘要和链接，避免同一状态在多处逐渐失真。

## 从哪里开始

| 读者 | 先读 | 用途 |
|---|---|---|
| 第一次使用的主播 | [快速开始](quickstart.md) | 登录、确认直播间、开始与结束直播 |
| 内测主播 | [猫猫独播体验指南](solo-stream-test-guide.md) | 低风险真机试播、观察点和关停步骤 |
| 第一次接手的开发者 | [开发者指南](developer-guide.md) | 五分钟建立架构心智模型并找到代码入口 |
| 准备贡献代码的人 | [贡献指南](contributing.md) | 选任务、建分支、提交 PR 和必查清单 |

## 权威文档

| 事实类型 | 权威来源 |
|---|---|
| 产品定位、当前能力、明确不做 | [`../README.md`](../README.md) |
| 主播操作流程 | [quickstart.md](quickstart.md) |
| 架构、不变量、模块边界、隐私、安全、测试门禁 | [development.md](development.md) |
| UI 分区、Hosted UI 兼容入口、刷新与交互约束 | [ui-architecture.md](ui-architecture.md) |
| Timeline、Outcome、Skip Reason、Dashboard/monitor 投影 | [runtime-observability.md](runtime-observability.md) |
| Independent Mode 的产品目标、范围和验收 | [independent-mode-product-plan.md](independent-mode-product-plan.md) |
| 当前阶段、下一步、延期项 | [live-center-roadmap.md](live-center-roadmap.md) |
| 旧 `bilibili_danmaku` 能力去留 | [bilibili-danmaku-migration-matrix.md](bilibili-danmaku-migration-matrix.md) |

## 模块参考

- [live_events](modules/live_events.md)：事件窗口、候选选择和低价值过滤。
- [live_support_events](modules/live_support_events.md)：可信礼物 / SC / Guard 的去重、连击和短句致谢。
- [live_audience_session](modules/live_audience_session.md)：本场直播的有界、脱敏统计。
- [live_hosting](modules/live_hosting.md)：暖场、冷场与主动营业。
- [output_contract](modules/output_contract.md)：回复长度、质量与唯一输出边界。
- [viewer_stores](modules/viewer_stores.md)：观众档案、审计与凭据边界。
- [douyin_live_ingest](modules/douyin_live_ingest.md)：实验性抖音只读 bridge 接入。
- [twitch_live_ingest](modules/twitch_live_ingest.md)：TwitchIO Helix/EventSub 只读接入、Device Code Flow、加密 token、账号与目标频道分离，以及首阶段明确不做的首页/写能力边界。

模块文档只描述该模块拥有的契约、数据、安全边界、测试和降级策略。跨模块总规则仍以 `development.md` 为准。

## 更新路由

- 改按钮、页面或主播操作顺序：更新 `quickstart.md`。
- 改架构、数据、安全、协作或测试门禁：更新 `development.md`。
- 改 UI 分区、状态刷新或 `panel_compat.tsx`：更新 `ui-architecture.md`。
- 改运行态字段、跳过原因或监控语义：更新 `runtime-observability.md`。
- 改产品范围或验收口径：更新 `independent-mode-product-plan.md`。
- 改当前优先级或延期决定：更新 `live-center-roadmap.md`。
- 改某个模块的输入、输出、存储或降级：更新对应 `modules/*.md`。

不要把日期化的工作日志、聊天记录、截图验收过程或单次测试数字写进长期规范。需要保留的证据应放在 PR、issue 或发布记录中；文档只保留仍然有效的结论。

仓库级文档分层、状态、多语言和链接规则见 [`docs/contributing/documentation.md`](../../../../docs/contributing/documentation.md)；本目录只补充 NEKO Live 组件自有的事实来源。
