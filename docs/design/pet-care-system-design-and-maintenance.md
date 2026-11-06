# 猫形态养宠系统设计与维护规范（三项目）

> **文档性质：Current contract。** 本文维护当前三项目 PetCare 的产品行为、权威边界、模块职责和变更规则。
>
> 候选数值：[`pet-care-system-v1-values-draft.md`](./pet-care-system-v1-values-draft.md)。服务部署、数据库和发布门禁由 `N.E.K.O.Verse/docs/adr/0002-pet-care-server-preparation.md` 维护。

## 1. 文档权威与适用范围

当前代码、测试、迁移和可复现运行结果证明系统实际做了什么；本文保存必须长期维持的产品与架构合同。二者冲突时应登记并修复缺口，不能通过静默修改其中一方消解冲突。

本文覆盖 N.E.K.O、N.E.K.O-PC、N.E.K.O.Verse 主应用和独立 PetCare 服务的职责、数据流与维护规则。具体阈值、概率、CD、消耗、奖励和次数由不可变配置 revision 管理，数值草案只是一套可替换候选。部署状态、环境容量和发布证据属于 Verse ADR 或 release manifest，不在本文保存易过期结论。

## 2. 产品边界与非目标

当前系统必须保持以下闭环：

- 使用 N.E.K.O 统一账号，不建立养宠专用登录；
- 一个账号只有一只宠物，宠物绑定该账号的一个稳定服务端角色；
- 本机 `character_uid` 只是在已验证安装上的角色别名，不是宠物主键；
- 未登录、别名未知、角色不匹配、快照不完整或服务不可用时继续普通猫；
- Web 与 N.E.K.O-PC 承载同一 N.E.K.O 页面和 `PetCareClient`；
- 服务按 `last_settled_at` 懒结算，不运行宠物定时任务；
- 客户端不发奖、不扣库存、不决定概率，也不把显示推算回写服务；
- Cat Mind 继续负责短期自主行为，PetCare 只负责长期状态和养成事务；
- 正式发行不提供本地权威模式，也不迁移开发期 SQLite 数据。

当前合同包括任务、喂食、梳毛、毛线球、逗猫棒、一个探索地、猫窝／纸条／来信、正常完成与召回、离线结算、生病／赌气、恢复任务、进度及已有真实消费者对应的解锁。商店、社区货币、付费货币、社交、推送、AIGC、洗澡和复杂小游戏不在当前范围。

## 3. 三项目职责

| 项目 | 当前职责 | 明确不拥有 |
| --- | --- | --- |
| N.E.K.O | 统一账号会话适配、同源薄网关、角色别名上报、`PetCareClient`、UI、用户动作、探索表现、Cat Mind 只读投影和普通猫回退 | 权威数据库、业务规则、奖励／库存写入、内部签名私钥 |
| N.E.K.O-PC | Electron 窗口、透明区域、命中区域、触摸／鼠标输入、桌面 IPC 和既有 Avatar tools 宿主能力 | 登录、宠物快照、任务、数值、结算和第二套 PetCare API |
| N.E.K.O.Verse 主应用 | 统一账号与安装凭据校验、玩家 edge、管理员 BFF、运营管理页面 | 宠物领域事务、PetCare 表和 PetCare 迁移 |
| 独立 PetCare 服务 | 账号唯一宠物、稳定角色与绑定、长期状态、懒结算、任务、库存、解锁、陪玩租约、探索、负面状态、恢复、幂等、配置 revision 和审计 | 社区页面、桌面窗口、猫动画和 Cat Mind 内存状态 |

独立 PetCare 服务当前与 Verse 同仓，便于复用账号和安装凭据契约。源码已经具有独立进程入口、镜像构建边界、数据库 URL 和 Alembic 链；正式专库、专用数据库账号、备份和网络隔离仍由部署阶段实际创建并验证。以后是否拆仓或采用独立部署项目，不得改变这条运行边界。

## 4. 运行拓扑与信任边界

```text
N.E.K.O 页面（浏览器或 N.E.K.O-PC Pet 窗口）
  -> N.E.K.O 同源 /api/pet-care 网关
  -> Verse 公网玩家 edge / 管理员 BFF
       - 验证统一账号、账号状态、session_epoch
       - 验证安装 proof 与 users.id 绑定
       - 签发短时、限定 audience、绑定请求的内部断言
  -> 内网独立 PetCare API
  -> PetCare 专用 PostgreSQL
```

浏览器和桌面壳不能直连独立服务端口。N.E.K.O 网关属于不可信客户端边界，只转发固定白名单路径、会话凭据、安装证明、幂等键、版本和关联 ID；它不保存服务共享密钥，也不能签发受信账号上下文。

账号、安装和角色是三种身份：

```text
(issuer, subject) -> Verse users.id
users.id + installation proof -> 已验证 client_id
users.id + client_id + character_uid -> PetCare 稳定 role_id
users.id -> 唯一 pet_id -> bound_role_id
```

官方换绑保持同一 `pet_id`，同时校验宠物、绑定和状态版本。本地修改或复制 `character_uid` 不能得到服务认可；退役别名不能由旧设备重新创建。

## 5. 领域不变量

1. 一个统一账号只有一个 `pet_id`。首次绑定创建宠物，角色切换不得创建第二只；官方换绑只改变 `bound_role_id`，并保留宠物长期数据。
2. 猫档位、模型和装扮只决定外观。`character_uid` 是已验证安装上的角色别名，稳定 `role_id` 才能参与服务端绑定。
3. 饱食度、精力、清洁度、心情等状态与养成进度是不同数据。状态按配置变化并受各自边界约束；进度可以按离线规则回退，但不能低于最近解锁存档点，已解锁内容不收回。
4. 服务在读取或操作时依据服务时间和 `last_settled_at` 补算。系统不运行宠物定时结算任务，客户端不轮询、不以本地时钟结算，也不回写展示推算值。
5. 账号已登录、N.E.K.O 账号活动、猫形态 active 和绑定角色匹配是不同事实。只有对应操作要求的事实才能成为前置条件，不能用猫形态在线代替账号活动。
6. 奖励、库存、任务、进度、解锁、探索结果和随机结果由 PetCare 事务决定。失败、取消、超时、冲突或身份变化不得留下半次业务写入。
7. 所有可调数值和内容关系进入不可变配置 revision；稳定 ID、持久语义、服务日边界和内容类型的变化必须有代码、迁移与回滚方案。

## 6. 当前代码结构

### 6.1 N.E.K.O

```text
main_routers/
  pet_care_gateway.py                 固定路径同源代理、会话和本地能力边界
  pet_care_role_alias_outbox.py       角色删除退役的可重试别名发件箱

static/app/
  app-pet-care-api.js                 HTTP 传输、超时、取消和网关路径
  app-pet-care-client.js              PetCare 端点与请求包络
  app-pet-care-mode.js                猫形态／活动／Cat Mind 表现门控
  app-pet-care-pending-writes.js      未知结果写请求的持久幂等重放
  app-pet-care-role-alias-sync.js     角色别名发件箱同步
  app-pet-care.js                     model-owner surface 的组合、状态、UI 和生命周期
  app-cat-mind.js                     原有短期自主行为；只消费版本化只读投影

static/avatar/avatar-ui-buttons/      猫动作、毛线球、逗猫棒、撸猫和探索表现
static/css/pet-care.css               养宠界面及透明宿主的可见区域样式
tests/frontend/pet_care_*.test.cjs    客户端、UI、门控和失败语义
tests/unit/test_pet_care_*.py         网关、别名发件箱和静态边界

既有宿主复用：
  main_routers/community_oauth.py      统一账号会话事实
  main_routers/card_drop_router.py     社区 OAuth 状态入口
  static/app/app-character.js          角色生命周期与本地别名
  static/app/app-auto-goodbye.js       返回／切换模型生命周期
  static/app/app-desktop-window-sensing.js
  static/app/app-ui/return-transitions.js
  static/app/app-ui/surface-floating-controls.js
```

`app-pet-care.js` 是现有无打包全局脚本体系中的 renderer 组合根。传输、端点、待确认写入、角色别名和模式门控保持独立；继续拆分时应按“纯快照校验／展示格式化／DOM 视图／生命周期协调”划分，并保持现有公开事件和 model-owner fence。

### 6.2 N.E.K.O-PC

```text
src/ipc-channels.js
src/main.js
src/preload/entries/pet.js
src/preload/bridges/pet-input-region-bridge.js
src/preload/bridges/pet-idle-companion-bridge.js
src/preload/entries/minimized-chat-ball.js
test/pet-care-input-region-contract.test.js
test/pet-idle-companion-bridge.test.js
```

PC 对 PetCare 只理解网页声明的宿主语义：实际控件 `[data-neko-pet-care-interactive]`、可见不透明块 `[data-neko-pet-care-input-surface="true"]`，以及服务快照已确认右键撸猫时的 `[data-neko-petting-enabled="true"]`。disabled／`aria-disabled` 控件本身不占交互命中区域；不透明面板块仍阻止穿透。业务 ID、数值、任务、库存和服务地址均不进入 preload。

### 6.3 N.E.K.O.Verse

```text
app/modules/pet_care/edge/             玩家 edge、管理员 BFF、限流和内部断言
web/src/features/pet-care/admin/       配置、模拟、审批、激活、审计和受控发放 UI

services/pet_care/
  src/neko_pet_care/
    domain/                             领域状态、规则、配置和投影
    application/                        玩家／管理员用例、事务、快照和传输无关输出契约
    infrastructure/                     PostgreSQL、仓储、角色、租约和安全
    interfaces/http/                    内部玩家／管理员 API、schema 和分离信任域的共享断言边界
  migrations/                           独立 Alembic 链
  config/staging/                       未自动激活的候选配置
  scripts/migrate.sh
  Dockerfile
  pyproject.toml / uv.lock

tests/pet_care/                         edge、领域、应用、迁移和安全契约
```

领域层不得依赖 infrastructure 或 interfaces，应用层不得依赖 HTTP 接口层。玩家与管理员 HTTP 使用同层共享的有界请求验证，但保留不同的密钥、body limit 和 audience；传输无关的输出模型归 application contract 所有。

Verse 玩家 edge 与 N.E.K.O 网关使用相同的规范路径边界：拒绝空段、点段、尾斜杠、反斜杠、查询／片段混入、超长动态 ID 和非白名单路由；edge 的路由／scope 清单还会与独立服务的实际路由表自动对照。新增内容仍应先扩展能力目录和领域契约，再增加客户端资源／动作和配置，不能在 edge 或管理 UI 中复制领域公式。

## 7. 功能合同

| 功能 | 当前实现语义 |
| --- | --- |
| 登录入口 | 未登录时继续普通猫，不显示养宠面板、数值或可用操作；每个猫形态 episode 最多出现一次轻提示，可进入现有统一 OAuth，不建立第二套登录态 |
| 账号活动 | 登录状态与猫形态在线状态分开；事件驱动 `session/resume` 只确认经鉴权的 N.E.K.O 账号活动，不轮询或上传交互正文 |
| 唯一宠物与绑定 | 首次绑定创建账号唯一宠物和稳定角色；角色切换不创建第二只宠物；官方换绑保留宠物数据 |
| 状态显示 | 服务按 `last_settled_at` 补算；快照携带服务时间和版本化投影，客户端只读推算显示，不回写 |
| 每日任务 | 每日打工先完成再单独领取小鱼干；毛线球是独立、不可领取、无任务奖励的 `0/3` 进度项；每日登录是账号活动结算，不属于任务集合，也没有领取操作 |
| 喂食 | 当前小鱼干消耗、饱食和互动变化由服务配置；满值拒绝；候选小鱼干进度为 0，不代表以后所有食物都必须为 0 |
| 梳毛 | 有 CD，可在 CD 后多次恢复清洁；当前候选只限制每日首次成长进度，不把动作限制成每天一次 |
| 毛线球 | 只有用户主动触发、猫真实回应且终态成功才结算；Cat Mind 自主玩耍不计入 |
| 逗猫棒 | 独立消耗品和 `PlayTeaserWandAction`；签发与完成两段校验，成功事务才扣库存并结算 |
| 状态界面入口 | 默认 `hover`：猫形态或探索猫窝悬停约 400ms 后出现；桌面端可改为仅从设置打开的 `manual`，或仅响应已配置快捷键的 `shortcut`。三种方式共用同一语义锚点、统一账号、角色绑定、服务状态和忙碌门禁；切换方式只取消尚未完成的悬停计时，不强制关闭已打开界面。恢复在途探索时 `manual`／`shortcut` 只恢复猫窝和活动门禁，不自动展开；显式打开后若指针未进入猫或面板，沿用既有 700ms 离开宽限收起。移动、拖拽、重力下坠或贴边探头时不触发悬停，也不把 PetCare 悬停计入 Cat Mind |
| 探索 | 一个地点；出发扣状态，猫离场并留下猫窝／纸条；来信按服务时间逐步公开；完成和召回都由服务结算 |
| 强制召回 | 模型返回链触发同一探索召回；表现验收上限为 1 秒；提前召回按服务确认的完成比例结算，完整完成才有完整奖励和加成 |
| 负面状态 | 同一离线段只判定一次，生病和赌气可并存；生病需完成求药、领取并使用药品，赌气任务只保证获得逗猫棒，最终由成功的受租约保护互动清除 |
| 进度回退 | 连续离线超过候选门槛才回退；不得低于最近解锁存档点，已解锁内容不收回 |
| 解锁 | 当前只登记已有真实消费者的猫窝信箱和右键撸猫；不为满足数量建立占位内容 |

喂食是可配置的进度来源类别，但不要求每种食物都提供正进度；当前候选的小鱼干只恢复状态。当前只登记两个已有真实消费者的解锁，后续内容必须先具备动作、资源或界面入口，再通过新配置 revision 加入。

当前首个探索地点完整完成固定带回 1 根逗猫棒，其他掉落尚未定义。探索可以配置食物或其他内容，但没有默认指定小鱼干；在新食物及其资源、用途和获取平衡完成前，不用臆测掉落填补空项。

## 8. 写操作、失败和一致性

改变宠物长期状态的业务操作遵循同一原则：

1. 客户端携带该操作适用的幂等键、配置／状态／绑定版本及业务前置条件；
2. edge 每次重新验证账号、安装凭据和请求范围；
3. PetCare 在一个数据库事务中补算、校验、修改、写不可变收据和审计；
4. 服务返回确认快照或稳定业务拒绝；
5. 客户端只在服务确认后显示奖励、库存、任务、进度、解锁和探索结果。

超时或断线表示“结果未知”，不能当作未执行。客户端保存完全相同的请求正文和幂等键，在相同账号／角色／版本上下文中重放；上下文变化时停止自动重放。取消、失败、过早终态、活动冲突、版本冲突和身份变化不能留下半次写入。

喂食、梳毛及取得服务陪玩租约后的毛线球／逗猫棒可先显示可回滚的状态预览；获得类结果不做乐观展示。用户可见错误由一个展示边界映射成短提示，不显示异常正文、源码路径、内部 ID 或机器错误码。

`session/resume` 是账号活动确认，角色别名同步按自然来源 revision 去重，官方换绑使用宠物／绑定／状态三项版本；它们不强行套用普通宠物业务写的同一请求字段集合。

## 9. Cat Mind、Avatar 与探索门控

PetCare 的饱食度、精力、清洁度、心情和进度不与 Cat Mind 的食欲、困意、精力、社交需求和刺激需求共用字段、公式或持久化。

- 完整且已授权的 PetCare 快照可以提供有时限的只读 Cat Mind 权重偏置；schema v3 的短期偏置到期只撤回 Cat Mind 输入，不会让仍处于有效账号／绑定上下文的长期状态显示快照整体失效；
- 未登录、角色不匹配、快照不完整、投影过期或服务不可用时撤回投影，继续普通 Cat Mind；
- 用户照料／陪玩表现占用猫实体时，只临时拦截冲突的自主动作；
- 探索期间统一门控 journey、playground、重力、贴边探头及其他依赖猫实体的输出；
- 左键返回模型、用户主动操作和 Cat Mind 自己的生命周期清理仍使用原有链路。

PetCare 不保存 Cat Mind 五维，不替 Cat Mind 选择动作；Cat Mind 不发奖励、不推进任务，也不结算 PetCare 状态。

## 10. 配置、管理端与内容扩展

所有状态边界、衰减、恢复、活动消耗、概率、CD、次数、奖励、任务、地点和解锁阈值都进入不可变配置 revision。候选文件只是一份导入输入，启动或读取文件不会自动激活。

管理流程是：校验 → 建立草稿 → 模拟同一 payload hash → 不同管理员审批 → CAS 激活 → 审计。已激活 revision 不原地修改；回退也创建新草稿。普通运营修改不能改变已经持久化的 ID 语义、服务日边界、动作消耗身份或解锁类型，这些变化需要代码、迁移和回滚方案。

新道具或内容的顺序固定为：

1. 定义稳定内容 ID、用户名称、类别、资源和真实获取来源；
2. 在 PetCare 注册业务能力、校验和结算；
3. 在 N.E.K.O 提供对应资源、动作适配和用户展示；
4. 增加跨仓契约测试；
5. 最后创建并激活引用该能力的配置 revision。

管理员页面只编辑代码已经支持的内容和运营数值，不承担“无代码创建任意新动作”的职责，也不提供任意宠物状态编辑器。

## 11. 本地联调与正式部署边界

本地三项目联调运行的是正式 PetCare 服务代码和隔离 PostgreSQL／Redis，不再运行 N.E.K.O 内的 SQLite 权威镜像。`NEKO_PET_CARE_DEV_BASE_URL` 只允许把 N.E.K.O 的 PetCare 固定白名单网关指向 loopback Verse edge；它不能改变社区链接、OAuth callback 或其他社区 API，也不能让页面直连独立服务。

仓库外、被忽略的本地夹具只可用于开发：它复用已经验证的统一账号事实，在全新隔离库中完成接线，不创建账号映射、不保存真实 token、不接触生产数据，也不进入提交、镜像或部署配置。

本地闭环能证明接口、领域、迁移和页面接线；不能证明真实 OAuth callback、生产 introspection、安装凭据撤销、生产网络、密钥轮换、备份恢复、目标负载或最终安装包交互。

## 12. 维护变更矩阵与禁区

| 变更 | 必须同步检查的层 | 最低验证 |
| --- | --- | --- |
| 新食物或清洁用品 | PetCare 内容目录、效果与获取来源；N.E.K.O 资源、名称和操作入口；管理端可编辑投影 | 配置闭包、库存事务、UI 展示和失败无副作用 |
| 新陪玩道具或动作 | 稳定内容 ID、陪玩租约和结算；Avatar 动作适配；资源与终态回报 | 签发／成功／取消／超时、库存只扣一次、任务来源隔离 |
| 新任务 | 任务类型、周期、目标、奖励和幂等围栏；客户端按服务投影渲染 | 完成与领取分离、跨服务日、重复请求、无奖励任务 |
| 新探索地点或掉落 | 地点配置、随机结果持久化、来信计划、完成／召回公式；猫离场表现 | 刷新不重抽、召回比例、恢复在途探索、活动互斥 |
| 新解锁 | 稳定解锁 ID、阈值、存档点和真实消费者 | 达标、回退不越存档点、已解锁不收回、未授权入口隐藏 |
| API 或快照字段 | PetCare contract、Verse edge 白名单、N.E.K.O client 和兼容策略 | schema／路径 parity、旧客户端失败方式、脱敏夹具 |
| 纯数值调整 | 新配置 revision、模拟、审批、CAS 激活和审计 | 同一 payload hash、边界模拟、回退 revision |

维护时不得重新引入客户端或 PC 权威状态，不得把 `character_uid` 提升为宠物主键，不得在 edge、管理 UI 或动画模块复制领域公式，不得把 Cat Mind 五维与 PetCare 长期字段合并，也不得为满足数量增加没有消费者的占位内容。未知结果必须沿原幂等请求恢复，不能通过新接口绕过收据语义。

## 13. 验证矩阵

变更应分别验证以下层次，不能用一层通过替代另一层：

- N.E.K.O：网关、角色别名发件箱、客户端、UI、任务、陪玩、探索、Cat Mind／Avatar 回归；
- N.E.K.O-PC：Windows／macOS 的透明穿透、不透明块命中、触摸／鼠标输入和真实猫动作；
- Verse edge／Web：账号与安装凭据、管理员权限、错误文案、配置工作流和审计；
- PetCare 服务：领域、事务、幂等、并发、配置、迁移、PostgreSQL 约束和安全断言；
- 跨项目：统一登录／退出、首次绑定、官方换绑、离线恢复、探索恢复和未知结果重试。

具体测试次数和发布物摘要属于每次运行的验证记录或 release manifest，不固定写入本文。真实 OAuth／安装凭据、非空数据库升级、生产网络、密钥轮换、备份恢复、目标负载、staging 激活和最终安装包实机证据由 Verse ADR 与发布流程维护。
