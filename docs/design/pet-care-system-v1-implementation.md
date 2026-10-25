# 猫形态养宠系统首版实施记录（三项目）

> 状态：**部署前代码闭环已形成，正式部署尚未开始**。
> 产品目标：[`宠物养成系统方案_修订稿.md`](./宠物养成系统方案_修订稿.md)，锁定且不得修改。
> 候选数值：[`pet-care-system-v1-values-draft.md`](./pet-care-system-v1-values-draft.md)，可通过配置评审继续调整。
> 服务与发布门禁：`N.E.K.O.Verse/docs/adr/0002-pet-care-server-preparation.md`。

本文只记录当前实现边界、三项目代码位置、关键状态机和验收口径。早期阶段 0–9 的逐日执行日志、本地 SQLite 表结构和已弃用路径由 Git 历史保留，不再作为当前架构说明。

## 1. 证据顺序与文档职责

当前实现事实由可复现的代码、测试、迁移和运行结果判断；产品验收目标由锁定文档决定。两者不一致时，应把差异记为实现缺口，不能用当前代码反向修改产品目标。

本实施记录解释目标当前落在哪些模块、怎样跨仓传输，以及哪些事项仍属于部署。Cat Mind／Avatar 原有文档只说明被复用能力，数值草案只描述一个可运行候选；提交中的阈值、概率、CD、奖励和次数不能因此视为产品定稿。

## 2. 当前目标与首版边界

当前实现必须形成以下闭环：

- 使用 N.E.K.O 统一账号，不建立养宠专用登录；
- 一个账号只有一只宠物，宠物绑定该账号的一个稳定服务端角色；
- 本机 `character_uid` 只是在已验证安装上的角色别名，不是宠物主键；
- 未登录、别名未知、角色不匹配、快照不完整或服务不可用时继续普通猫；
- Web 与 N.E.K.O-PC 承载同一 N.E.K.O 页面和 `PetCareClient`；
- 服务按 `last_settled_at` 懒结算，不运行宠物定时任务；
- 客户端不发奖、不扣库存、不决定概率，也不把显示推算回写服务；
- Cat Mind 继续负责短期自主行为，PetCare 只负责长期状态和养成事务；
- 正式发行不提供本地权威模式，也不迁移开发期 SQLite 数据。

首版已有任务、喂食、梳毛、毛线球、逗猫棒、一个探索地、猫窝／纸条／来信、正常完成与召回、离线结算、生病／赌气、恢复任务、进度及真实消费者对应的解锁。商店、付费猫粮、社交、推送、AIGC、洗澡和复杂小游戏不在本阶段。

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

## 5. 当前代码结构

### 5.1 N.E.K.O

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

`app-pet-care.js` 是现有无打包全局脚本体系中的 renderer 组合根。传输、端点、待确认写入、角色别名和模式门控已经独立；其余状态、DOM 和猫生命周期共享同一 model-owner fence。当前没有为了减少行数继续拆分，因为那会新增全局加载顺序和跨模块可变状态，而没有消除业务权威或重复结算。以后若改为明确的模块加载机制，应按“纯快照校验／展示格式化／DOM 视图／生命周期协调”拆分，并保持现有公开事件和测试夹具。

### 5.2 N.E.K.O-PC

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

### 5.3 N.E.K.O.Verse

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

较大的 `config.py`、`model.py`、`service.py` 和 `admin.py` 分别保持配置闭包、领域聚合、玩家事务及管理员工作流的单一层内职责；仅凭行数不足以支持部署前重拆。已确认并收口两个真实依赖问题：玩家、管理员路由不再互相借用私有鉴权函数，而由同层 `interfaces/http/internal_auth.py` 共享有界请求验证和公钥加载，两个信任域仍使用不同密钥、body limit 和 audience；`application/snapshot.py` 构造的输出模型已移到 `application/contracts.py`，HTTP schema 只重导出兼容名称，应用层不再反向依赖接口层。分层测试会拒绝 domain 依赖 infrastructure／interfaces，以及 application 依赖 interfaces。

Verse 玩家 edge 与 N.E.K.O 网关使用相同的规范路径边界：拒绝空段、点段、尾斜杠、反斜杠、查询／片段混入、超长动态 ID 和非白名单路由；edge 的路由／scope 清单还会与独立服务的实际路由表自动对照。新增内容仍应先扩展能力目录和领域契约，再增加客户端资源／动作和配置，不能在 edge 或管理 UI 中复制领域公式。

## 6. 锁定功能的当前闭环

| 功能 | 当前实现语义 |
| --- | --- |
| 登录入口 | 未登录时普通猫；每个猫形态 episode 最多出现一次轻提示，可进入现有统一 OAuth，不建立第二套登录态 |
| 账号活动 | 登录状态与猫形态在线状态分开；事件驱动 `session/resume` 只确认经鉴权的 N.E.K.O 账号活动，不轮询或上传交互正文 |
| 唯一宠物与绑定 | 首次绑定创建账号唯一宠物和稳定角色；角色切换不创建第二只宠物；官方换绑保留宠物数据 |
| 状态显示 | 服务按 `last_settled_at` 补算；快照携带服务时间和版本化投影，客户端只读推算显示，不回写 |
| 每日任务 | 每日打工先完成再单独领取小鱼干；毛线球是独立、不可领取、无任务奖励的 `0/3` 进度项；每日登录是账号活动结算，不属于任务集合，也没有领取操作 |
| 喂食 | 当前小鱼干消耗、饱食和互动变化由服务配置；满值拒绝；候选小鱼干进度为 0，不代表以后所有食物都必须为 0 |
| 梳毛 | 有 CD，可在 CD 后多次恢复清洁；当前候选只限制每日首次成长进度，不把动作限制成每天一次 |
| 毛线球 | 只有用户主动触发、猫真实回应且终态成功才结算；Cat Mind 自主玩耍不计入 |
| 逗猫棒 | 独立消耗品和 `PlayTeaserWandAction`；签发与完成两段校验，成功事务才扣库存并结算 |
| 悬浮入口 | 猫形态悬停约 400ms 后出现；移动、拖拽、重力下坠或贴边探头时不出现，也不把 PetCare 悬停计入 Cat Mind |
| 探索 | 一个地点；出发扣状态，猫离场并留下猫窝／纸条；来信按服务时间逐步公开；完成和召回都由服务结算 |
| 强制召回 | 模型返回链触发同一探索召回；表现验收上限为 1 秒；提前召回按服务确认的完成比例结算，完整完成才有完整奖励和加成 |
| 负面状态 | 同一离线段只判定一次，生病和赌气可并存；生病需完成求药、领取并使用药品，赌气任务只保证获得逗猫棒，最终由成功的受租约保护互动清除 |
| 进度回退 | 连续离线超过候选门槛才回退；不得低于最近解锁存档点，已解锁内容不收回 |
| 解锁 | 当前只登记已有真实消费者的猫窝信箱和右键撸猫；不为满足数量建立占位内容 |

锁定文档把喂食列为可产生进度的来源类别，但没有要求每种食物都必须给正进度；当前候选的小鱼干只恢复状态。锁定文档第 9 节的“3 到 5 个解锁项”仍标为工作假设；当前实现只有两个真实消费者，后续新增内容必须先具备动作、资源或界面入口，再通过新配置 revision 加入。

当前首个探索地点完整完成固定带回 1 根逗猫棒，其他掉落尚未定义。锁定文档允许探索概率获得食物或其他内容，但没有指定小鱼干；在新食物及其资源、用途和获取平衡完成前，不以“小鱼干低概率掉落”代替这项未定义内容。

## 7. 写操作、失败和一致性

改变宠物长期状态的业务操作遵循同一原则：

1. 客户端携带该操作适用的幂等键、配置／状态／绑定版本及业务前置条件；
2. edge 每次重新验证账号、安装凭据和请求范围；
3. PetCare 在一个数据库事务中补算、校验、修改、写不可变收据和审计；
4. 服务返回确认快照或稳定业务拒绝；
5. 客户端只在服务确认后显示奖励、库存、任务、进度、解锁和探索结果。

超时或断线表示“结果未知”，不能当作未执行。客户端保存完全相同的请求正文和幂等键，在相同账号／角色／版本上下文中重放；上下文变化时停止自动重放。取消、失败、过早终态、活动冲突、版本冲突和身份变化不能留下半次写入。

喂食、梳毛及取得服务陪玩租约后的毛线球／逗猫棒可先显示可回滚的状态预览；获得类结果不做乐观展示。用户可见错误由一个展示边界映射成短提示，不显示异常正文、源码路径、内部 ID 或机器错误码。

`session/resume` 是账号活动确认，角色别名同步按自然来源 revision 去重，官方换绑使用宠物／绑定／状态三项版本；它们不强行套用普通宠物业务写的同一请求字段集合。

## 8. Cat Mind、Avatar 与探索门控

PetCare 的饱食度、精力、清洁度、心情和进度不与 Cat Mind 的食欲、困意、精力、社交需求和刺激需求共用字段、公式或持久化。

- 完整且已授权的 PetCare 快照可以提供有时限的只读 Cat Mind 权重偏置；schema v3 的短期偏置到期只撤回 Cat Mind 输入，不会让仍处于有效账号／绑定上下文的长期状态显示快照整体失效；
- 未登录、角色不匹配、快照不完整、投影过期或服务不可用时撤回投影，继续普通 Cat Mind；
- 用户照料／陪玩表现占用猫实体时，只临时拦截冲突的自主动作；
- 探索期间统一门控 journey、playground、重力、贴边探头及其他依赖猫实体的输出；
- 左键返回模型、用户主动操作和 Cat Mind 自己的生命周期清理仍使用原有链路。

PetCare 不保存 Cat Mind 五维，不替 Cat Mind 选择动作；Cat Mind 不发奖励、不推进任务，也不结算 PetCare 状态。

## 9. 配置与内容扩展

所有状态边界、衰减、恢复、活动消耗、概率、CD、次数、奖励、任务、地点和解锁阈值都进入不可变配置 revision。候选文件只是一份导入输入，启动或读取文件不会自动激活。

管理流程是：校验 → 建立草稿 → 模拟同一 payload hash → 不同管理员审批 → CAS 激活 → 审计。已激活 revision 不原地修改；回退也创建新草稿。普通运营修改不能改变已经持久化的 ID 语义、服务日边界、动作消耗身份或解锁类型，这些变化需要代码、迁移和回滚方案。

新道具或内容的顺序固定为：

1. 定义稳定内容 ID、用户名称、类别、资源和真实获取来源；
2. 在 PetCare 注册业务能力、校验和结算；
3. 在 N.E.K.O 提供对应资源、动作适配和用户展示；
4. 增加跨仓契约测试；
5. 最后创建并激活引用该能力的配置 revision。

管理员页面只编辑代码已经支持的内容和运营数值，不承担“无代码创建任意新动作”的职责，也不提供任意宠物状态编辑器。

## 10. 本地联调与正式部署边界

本地三项目联调运行的是正式 PetCare 服务代码和隔离 PostgreSQL／Redis，不再运行 N.E.K.O 内的 SQLite 权威镜像。`NEKO_PET_CARE_DEV_BASE_URL` 只允许把 N.E.K.O 的 PetCare 固定白名单网关指向 loopback Verse edge；它不能改变社区链接、OAuth callback 或其他社区 API，也不能让页面直连独立服务。

仓库外、被忽略的本地夹具只可用于开发：它复用已经验证的统一账号事实，在全新隔离库中完成接线，不创建账号映射、不保存真实 token、不接触生产数据，也不进入提交、镜像或部署配置。

本地闭环能证明接口、领域、迁移和页面接线；不能证明真实 OAuth callback、生产 introspection、安装凭据撤销、生产网络、密钥轮换、备份恢复、目标负载或最终安装包交互。

## 11. 连续提交与结构演进

| 项目／提交 | 解决的问题 | 当前地位 |
| --- | --- | --- |
| N.E.K.O `a60e30b6` | 建立本地账号唯一宠物、角色绑定、普通猫回退和 Cat Mind 边界 | 历史行为基线；其中本地权威已迁出 |
| N.E.K.O `302673f9` | 完成本地 SQLite 闭环、任务、库存、探索、离线、负面状态、UI 和阶段测试 | 历史差分基线；不随官方客户端发布 |
| N.E.K.O `8bccf048` | 删除本地权威，接入同源网关、正式客户端契约、待确认写入、角色别名同步和完整 UI／动作闭环 | 当前客户端结构基线 |
| Verse `a70135f8` | 社区退出通知受信 opener，补齐统一会话生命周期 | PetCare 复用的账号前置能力 |
| Verse `dce37bd1` | 增加独立 PetCare 服务、专用迁移、玩家 edge、管理员治理和运营页面 | 当前正式服务代码基线，尚未部署 |
| PC `070f2ad`、`0b54eb9`、`aa7be9f`、`ddb39db`、`559b6a1`、`a07e20c`、`e04e41c`、`dfeacb1`、`c345c68` | 分别建立猫形态启动、Cat Mind observation、毛线球、Avatar tools、桌面感知、拖动、透明穿透和资源组选项 | PetCare 复用的桌面宿主基础 |
| PC `ca05563` | 让实际养宠控件参与原生命中区域，禁用控件不占命中 | PetCare 输入契约起点 |
| PC `3f43b3f` | 补齐不透明面板块、桌面宿主和毛线球桥的最终集成 | 当前 PC 结构基线 |

这些提交说明了迁移过程，不改变当前所有权：旧代码曾存在，不代表应继续保留第二套服务。

## 12. 验证与剩余门槛

部署前应分别验证以下层次，不能用一层通过替代另一层：

- N.E.K.O：网关、角色别名发件箱、客户端、UI、任务、陪玩、探索、Cat Mind／Avatar 回归；
- N.E.K.O-PC：Windows／macOS 的透明穿透、不透明块命中、触摸／鼠标输入和真实猫动作；
- Verse edge／Web：账号与安装凭据、管理员权限、错误文案、配置工作流和审计；
- PetCare 服务：领域、事务、幂等、并发、配置、迁移、PostgreSQL 约束和安全断言；
- 跨项目：统一登录／退出、首次绑定、官方换绑、离线恢复、探索恢复和未知结果重试。

具体测试次数和发布物摘要属于每次运行的验证记录或 release manifest，不固定写入本实施文档，避免旧计数被误当作当前证据。

仍需在部署阶段取得的证据包括：环境匹配的真实 OAuth 与安装 proof、非空数据库升级、目标 PostgreSQL 并发和锁行为、密钥轮换、网络隔离、镜像扫描与 SBOM、备份／PITR 恢复、目标负载、staging 配置激活、Windows 最终回归及 macOS 透明 Pet 窗口完整实机流程。Linux 暂不作为首发阻断项。

完成这些证据前，准确结论是“部署前代码闭环已形成”，不是“正式服务已经部署”或“可以跳过 staging 上线”。
