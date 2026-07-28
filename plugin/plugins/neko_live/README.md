# NEKO Live (`neko_live`)

NEKO Live 是 N.E.K.O 的直播互动中心。它连接支持的平台直播事件，选择值得回应的内容，经统一安全链路让猫猫发言，并向主播展示可理解的运行状态。

历史上的“新观众头像锐评”现在只是一个互动模块。当前产品还包含：

- B 站登录、直播间查询/确认和实时弹幕接入；
- 实验性抖音只读 bridge 接入；
- Twitch 首阶段只读接入，包含 Device Code 授权、频道状态查询、EventSub 聊天与可见支持事件；
- 首次出场锐评与后续普通弹幕接话；
- Gift / SC / Guard 可信支持事件的短句致谢；
- 暖场、冷场陪播和主动营业；
- 本场直播统计、观众档案与安全派生偏好；
- Live Status、Runtime Timeline、开发者沙盒和压力工具；
- 四区 Hosted UI：控制台、直播间互动、观众、设置，开发者模式条件追加开发者工具。

## 文档

- [第一次使用：快速开始](docs/quickstart.md)
- [第一次接手：开发者指南](docs/developer-guide.md)
- [全部文档与职责矩阵](docs/README.md)
- [长期开发规范](docs/development.md)
- [当前路线图](docs/live-center-roadmap.md)

## 核心边界

- 所有直播与沙盒输入共用 Pipeline 和 SafetyGuard。
- 所有猫猫输出只走 `NekoDispatcher`。
- 观众档案、审计和凭据各自只有一个受控 store 边界。
- provider 只接入、清洗和发布安全事件，不直接决定或发送回复。
- 普通弹幕文本不能伪造 Gift / SC / Guard。
- 新 UI 文案必须同步 8 个 locale。

详细规则见 [开发规范](docs/development.md)。

## 数据与隐私

观众档案只保存基础身份和安全派生偏好，不保存原始弹幕。UID 使用平台命名空间；公开运行态使用不可逆短关联 ID。登录凭据加密存本机，不能进入配置、日志、审计、UI 或事件 raw。

开发者沙盒只保留运行时临时记录，不写观众档案或直播总结。头像只作为当次请求的临时视觉输入，超出 message-plane 预算时降级为纯文字。

## 明确不做

- 不发送平台弹幕、私信、动态、点赞或关注。
- 不保存原始弹幕历史、provider raw、贡献排行榜或无限期支持流水。
- 不执行浏览器/键鼠自动化，也不在插件内维护抖音签名和 protobuf 直连。
- 不建立第二套 LLM、memory、orchestrator 或输出通道。
- 不整体复制旧 `bilibili_danmaku` / `bilibili_dm` 大文件。

旧插件能力的去留见 [迁移矩阵](docs/bilibili-danmaku-migration-matrix.md)。
