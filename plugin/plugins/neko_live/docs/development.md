# NEKO Live 开发规范

本文是 NEKO Live 长期开发契约的权威来源。它描述“哪些边界必须保持”，不记录单次测试数字、日期化进度、聊天结论或历史交接流水。

当前阶段与下一步见 [路线图](live-center-roadmap.md)，产品验收见 [Independent Mode Product Plan](independent-mode-product-plan.md)，运行态字段见 [Runtime Observability](runtime-observability.md)。

## 1. 产品与范围

`neko_live` 的产品名是 **NEKO Live**。它负责直播平台只读接入、直播事件归一化、互动选择、安全输出、观众档案与主播控制台。

当前主要能力：

- B 站直播间登录、查询、确认、连接和弹幕接入；
- 实验性抖音只读 bridge 接入；
- 新观众锐评、后续弹幕接话；
- Gift / SC / Guard 可信支持事件的短句致谢；
- 暖场、冷场陪播和主动营业；
- 本场直播统计、观众档案和运行态解释；
- 开发者沙盒、监控和压力工具。

明确不做：

- 不发送弹幕、私信、动态、点赞、关注或其他平台写操作；
- 不保存原始弹幕历史、平台 raw payload、明文凭据或头像二进制；
- 不维护第二套 LLM、orchestrator、memory 或独立输出通道；
- 不整体复制旧 `bilibili_danmaku` / `bilibili_dm` 大文件；
- 不让 provider、模块或 UI 绕过统一 pipeline 和 dispatcher。

旧插件能力的替代、吸收、拆分和废弃决策见 [迁移矩阵](bilibili-danmaku-migration-matrix.md)。矩阵完成不等于允许删除旧插件。

## 2. 架构与不变量

### 2.1 主链路

```text
Live Provider / Developer Sandbox
  -> normalize to safe provider-neutral event
  -> EventBus
  -> Selection / module handler
  -> Pipeline
  -> PermissionGate
  -> SafetyGuard
  -> NekoDispatcher
  -> N.E.K.O output
  -> bounded dashboard / audit projection
```

### 2.2 五条硬边界

1. **唯一输入处理链**：直播与沙盒共用 `core/pipeline.py`。
2. **安全门必经**：任何能触发输出的路径都必须经过 `core/safety_guard.py`。
3. **唯一输出**：所有猫猫发言只走 `adapters/neko_dispatcher.py`；模块不得直接调用 `plugin.push_message()`。
4. **单一存储边界**：观众档案只走 `stores/viewer_store.py`，审计只走 `stores/audit_store.py`。
5. **凭据隔离**：登录凭据只走 `stores/credential_store.py` 加密保存，不得进入 config、audit、logger、UI 或事件 raw 字段。

这些边界优先于“少写几行代码”或“复用旧实现”。无法保持时应先停下，重新设计接口。

### 2.3 Provider-neutral 边界

- provider 只负责连接、解析、清洗和发布安全事件，不直接决定猫猫说什么；
- B 站兼容字段只能在 B 站 provider 边界派生，不能污染其他平台；
- UID 必须带平台命名空间，避免跨平台串档；
- chat/danmaku 可进入普通互动选择；gift/SC/guard 先成为可信候选，再由共享支持事件模块处理；
- member/follow/like/stats 等未批准事件默认只做状态或丢弃，不触发 AI；
- 新平台不得通过在 runtime 到处增加 `if platform == ...` 接入。

抖音 v1 只消费本地 bridge 清洗后的只读事件；不在插件内恢复 protobuf 直连、JS 签名、自动登录或浏览器自动化。详细边界见 [douyin_live_ingest](modules/douyin_live_ingest.md)。

## 3. 模块模型

一个可独立维护的直播能力应位于 `modules/<module_id>/`，并声明：

- 生命周期：`setup` / `teardown`，必要时 `on_enable` / `on_disable`；
- 事件：通过 `ctx.event_bus.subscribe(type, handler, owner=self.id)` 订阅；
- 数据：只使用受控 store 或 runtime 投影；
- 界面：`domain` 与真实有消费者的 `config_schema()`；
- 降级：连接失败、数据缺失、模块异常或输出被阻止时的安全行为。

模块异常必须被隔离并标记 degraded，不能拖垮其他模块或整个面板。EventBus handler 在 teardown 时必须取消订阅；会话级 worker、timer 和队列必须在断开、换房和插件停止时回收。

### 3.1 当前职责

| 子系统 | 主要职责 |
|---|---|
| `bili_live_ingest` / `douyin_live_ingest` | 平台连接、解析、清洗和事件发布 |
| `bili_identity` / `douyin_identity` | 从已清洗字段构造安全观众身份 |
| `live_events` | 候选窗口、价值选择、低质与重复过滤 |
| `avatar_roast` / `danmaku_response` | 首次出场与后续弹幕请求构造 |
| `live_support_events` | 可信支持事件去重、连击、优先调度与短句致谢 |
| `warmup_hosting` / `active_engagement` | 开场、冷场和主动营业 |
| `live_audience_session` | 单次会话的有界、脱敏统计 |
| `viewer_profile` | 观众档案读取与安全派生偏好 |
| `developer_sandbox` | 受开发者总控保护的模拟输入 |

具体契约见 [模块参考](README.md#模块参考)。

## 4. EventBus、Selection 与 Pipeline

### 4.1 EventBus

- 事件必须使用稳定、provider-neutral 的 type；
- 每个订阅者按 owner 隔离，失败需留下脱敏 audit；
- 没有订阅者的事件可以静默丢弃；
- handler 不持有 provider raw payload，也不直接输出；
- 新增事件族时同时定义 stage、outcome 和必要的 skip reason。

### 4.2 Selection

猫只有一个输出口，Selection 必须在有界窗口内择优：

- 高可信、高价值支持事件优先于普通弹幕；
- 明确问题、粉丝上下文、长而具体的内容可以提高普通弹幕价值；
- 纯符号、模板刷屏、伪装礼物文本和重复消息不能冒充高价值事件；
- 低价值事件可以被跳过，但不能阻断更高价值候选；
- 窗口、队列、连击状态和去重集合必须有硬上限。

### 4.3 Pipeline

Pipeline 负责身份、权限、安全、路由、请求构造、派发和结果投影。新增路径应复用现有阶段，不建立平行 pipeline。

任何异常都应转换成稳定的失败或跳过结果，并在 `finally` 或会话回收路径释放占用。不能因为 dashboard 刷新、audit 写入或次要 metadata 缺失而把已经成功的动作改报为失败。

## 5. 输出与节奏

- `NekoDispatcher` 是唯一出口，负责 dry-run、冷却、急停、队列和 message-plane 降级；
- `dry_run` 产品默认关闭，普通主播界面不把它当作日常开关；开发者测试需要无声验链时主动开启；
- 图片只作为当次请求的临时视觉输入，超出预算时降级为纯文字；
- 看不到头像时不得让模型描述头像，应退回昵称或安全 META；
- 直播回复应短、自然、适合 TTS，不索要更多礼物，不把支持事件当作普通观众原文；
- `rate_limit_seconds`、`activity_level`、选择窗口与支持事件调度共同控制节奏，不能各自建立互不知情的限流器。

回复形状与质量边界见 [output_contract](modules/output_contract.md)。

## 6. 可信支持事件

Gift / SC / Guard 只能由 provider 的可信结构化事件触发。观众发送“送了人气票 x999999”之类普通弹幕时，只能作为文本处理，不能升级为支持事件。

支持事件处理必须：

- 使用 provider event ID 或等价证据去重；
- 区分迟到连击更新与新事件，避免重复致谢；
- 使用有界优先队列，保留更高价值事件；
- setup 失败时 fail-closed，不能留下半启用 worker；
- 切房、断线、重连和 teardown 时排空或取消旧会话 worker；
- 只向 dashboard/audit 投影脱敏摘要，不保存 provider raw。

详细契约见 [live_support_events](modules/live_support_events.md)。

## 7. 配置与并发

- 配置只有在真实 runtime 或 UI 消费时才应存在；禁止为未来功能预建字段；
- 模块配置优先使用 `config.<module_id>.*` 或现有明确契约，避免继续膨胀全局扁平字段；
- 面板弹窗使用草稿状态，只有“保存设置”才写入；取消或关闭不应污染已保存快照；
- 同一配置更新必须合并当前快照，不能用旧表单覆盖其他并发字段；
- 开始/结束、登录、换平台、确认房间和保存配置都要有 pending 锁；
- 会话进行中不得切换平台、账号或直播间，必须先结束当前会话；
- 无账号连接是显式、单次、受限兜底，不得持久化为等价登录状态。

## 8. 数据、隐私与日志

允许持久化：

- 加密登录凭据；
- 观众基础档案和安全派生偏好；
- 必要、脱敏、容量受限的审计记录。

禁止持久化：

- 原始弹幕、私信、cookie、token、签名参数；
- 完整 provider payload、HTML、protobuf、WebSocket frame；
- 头像 bytes、base64/data URL；
- 可反推出真实用户的无界流水或排行榜。

普通 logger 也受同样限制。调试需要的字段必须脱敏、截断，并在不再需要时删除。观众档案、审计和凭据的具体边界见 [viewer_stores](modules/viewer_stores.md)。

## 9. UI 约定

普通面板固定为控制台、直播间互动、观众、设置四个一级页面；开发者工具按开发者模式条件显示。

控制台承载主播必须完成的开播流程；详细设置、资料和诊断进入对应页面或弹窗。关闭功能不能导致卡片布局跳动。状态刷新只在页面可见且直播相关状态需要刷新时运行，保持单请求、低频、可清理。

维护源码和 `panel_compat.tsx` 的同步、Hosted UI 限制、键盘与弹窗规则见 [UI 架构](ui-architecture.md)。

## 10. Runtime Observability

运行态必须回答三件事：事件走到哪一步、为什么没有输出、主播下一步该做什么。

- 新事件路径必须定义 Timeline stage 与 Event Outcome；
- 预期不输出必须使用稳定 Skip Reason；
- SafetyGuard 与 Dispatcher 必须作为可见阶段；
- dashboard/monitor 只从 runtime 或 audit 的安全事实派生；
- 刷新失败显示“状态可能过期”，不能伪造实时状态。

字段与命名以 [Runtime Observability](runtime-observability.md) 为准，不在本文重复维护列表。

## 11. 协作与 PR 规则

### 11.1 Feature → Slice → PR

- 一个 PR 只承载一个可独立 review、测试、合并和回滚的 Slice；
- 默认不超过 20 个文件，超过时必须解释，并优先使用 Draft 先确认形状；
- 不把功能、无关重构、宿主修改、面板重写和旧插件删除混在一起；
- 文档、测试或纯重构可以独立成 PR。

### 11.2 禁止堆叠式 PR

- 开放 PR 不得以另一个未合并功能分支为实际依赖；
- 即使 GitHub base 显示 `main`，只要正确性、测试、review、合并或回滚依赖另一个未合并 PR，也属于逻辑堆叠；
- 有依赖时先合并前置 PR，再从更新后的目标分支创建下一分支；
- 发现已有堆叠后停止继续传播，先处理离目标分支最近的 PR，再重建下一 Slice；
- 只有维护者书面批准的紧急发布或不可分割迁移可以例外，并必须记录顺序、风险和回归独立 PR 的计划。

### 11.3 成本类改动先拍板

新增依赖、后台轮询、常驻进程、队列、网络请求、token/context、存储或核心复杂度前，先写明：

- 成本类型与预算；
- 影响的模块和接口；
- 备选方案与取舍；
- 推荐方案；
- 降级、回滚与观测；
- 必跑测试。

## 12. Review Gate

以下范围需要核心维护者重点 review：

- `core/contracts*`、`event_bus*`、`module_registry*`；
- provider 接入、协议解析、事件归一化；
- `live_events` 选择权重和窗口策略；
- `live_support_events` 证据、去重、连击、优先调度和会话回收；
- pipeline、SafetyGuard、Dispatcher；
- runtime、配置持久化、Hosted UI action；
- viewer/audit/credential store；
- 面板导航外壳、跨页面状态和 `panel_compat.tsx`。

Review 至少检查：范围、架构边界、唯一输出、观测语义、成本、隐私、文档和验证。

## 13. 测试门禁

从仓库根目录运行：

```powershell
uv run pytest plugin/plugins/neko_live/tests -q
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_live
git diff --check
```

根据改动补充：

- UI：Hosted TSX 解析、`panel_compat.tsx` 完整性、可见性刷新和交互测试；
- i18n：8 个 locale key 集合、占位符和格式一致；
- provider：fixture、断线/重连、脱敏和 B 站兼容回归；
- 支持事件：伪礼物、重复 ID、迟到连击、队列上限和切房 worker 回收；
- store：原子写、路径失败、容量、清理和敏感数据负例；
- runtime：stage/outcome/skip reason、刷新失败和会话代际竞态。

文档-only PR 可以说明未运行代码测试，但仍须验证链接、Markdown 和 `git diff --check`。长期文档不记录易失真的测试总数；以 CI 和当次 PR 为准。

## 14. 文档完成标准

新模块或重要流程没有对应开发文档，视为未完成。更新时按 [文档索引](README.md#更新路由) 找到唯一权威来源，不要在 README、roadmap、product plan 和 development 中复制同一段状态。

历史测试过程、单次截图和聊天结论留在 PR/issue；长期文档只保留当前有效的规则、产品决定和可执行步骤。
