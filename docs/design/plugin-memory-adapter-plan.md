# 插件 Memory 适配与 SDK 重构计划

状态：首批 search 与最小 private Persona 已在当前工作区实现并完成针对性验证，尚未发布；按项目约定保持 SDK 0.1.0，不因新增 API 调整版本号，未设旧入口删除期限。Persona 的 scoped 读取、渲染与审计仍不在首批；本文保留基线核对及设计边界，不把受控测试解释成完整生产验收。

讨论日期：2026-10-29 至 2026-10-31。已收敛入口、服务适配、公共设施与兼容约束，完成第 0 步核对，并限定本体最小改动；实施顺序和 Agent 边界统一引用对话计划。继续按“如无需求勿增实体”执行。现状分析基线：`560f5e25d11f82f67feab03748b072ceb5cbb549`。后续实现以届时的代码、测试和正式接口文档为准。

新的插件 Memory 能力适配本体记忆服务，读取本体维护的长期记忆及其正式视图，不再以插件宿主最近用户输入缓存为业务模型。插件 SDK 和服务负责调用契约、传输与结果转换；本体 Memory 保持检索、作用域、渲染、维护和存储规则的所有权。

与 [conversations_v2 计划](/design/plugin-conversation-bus-refactor-plan) 的分工是：conversations 观察这次对话发生了什么；Memory 查询本体保留了什么，以及哪些记忆与本次查询相关。二者可以由插件组合使用，不建立第二份长期记忆库。

## 当前工作区实现记录

2026-10-31，首批通过正式 MemoryClient、MEMORY_SEARCH／MEMORY_PERSONA_GET、既有控制 RPC 和循环内 HTTP 池接通；新增 private HTTP 边界只在当前 PersonaManager 的角色锁内提取允许字段。新读取不调用旧占位 GET 或旧完整 Persona GET，不重写检索、渲染或维护。SDK 与宿主版本声明统一从轻量 SDK 常量取得 0.1.0，保留现有 supported／recommended 检查，但同为 0.1.0 的版本号不能区分新增能力是否存在，使用新增 API 需更新后的宿主／SDK 构建；没有增加版本路由或能力矩阵。

原生 Persona 新路径对损坏冷读数据明确失败，不用正常加载的容错重置伪装空快照；该严格选项只用于新读取，旧 UI／维护默认行为保留。Persona 投影最多 10000 条／1 MiB，插件 HTTP 接收与契约结果也限制 1 MiB；超过限制明确失败，不分页、裁剪或后台补齐。初始化仍可能执行已有必要迁移／角色卡同步，不额外消费 outbox、更新冷却或标记提及。

宿主 Memory HTTP 在途采用进程内最多 8 项的保守初值，无额度即 BUSY，不无界排队挤满公共控制 RPC。锁只保护计数，HTTP 池仍按循环复用，取消／失败归还接纳额度；这不是远端硬计算取消，真实本体引擎负载与取消后计算仍需观察，不能把 SDK timeout 当作上游资源已经释放。

验证包含原生范围／并发／错误样例、插件 HTTP 适配及 SDK 错误、真实 Memory ASGI route→RpcPeer／PluginRouter→SdkContext／MemoryClient 的跨边界调用。跨边界 search 独立性测试使用受控检索算法，未宣称真实模型／向量引擎验收；原有 hybrid_recall、scope、HTTP 池和 SDK 打包另有回归。共享下行、Main 覆盖和完整实施安排只引用 [对话计划的工作区记录](/design/plugin-conversation-bus-refactor-plan#当前工作区实现记录)，不再维护第二套进度表。

本轮实现复审补齐严格读取的失败边界：新增 Persona 投影冷读以 stat 区分文件不存在与访问失败，畸形 private 分类容器在既有迁移／角色卡同步前拒绝，避免失败读取误走初始化或覆盖；旧非严格 ensure 路径不变。插件适配复用本体现有角色名校验，将非法目标与缺少端点区分，原生 HTTP 400 明确映射参数错误；接收分块在复制前检查响应字节上限。没有新增 PersonaService、缓存、连接或回退读取。

完整测试补齐了 private Persona GET 的角色发布保护：既有 ensure 可能同步角色卡或迁移并保存，故新端点参与现有 character publication fence；围栏保持时沿原链路返回 409，在途读取参与既有排空，旧 Persona GET 不改。结构化 SDK search 不生成提示词，召回架构守门将其与必须统一渲染的提示词客户端显式分开；未知新客户端仍需分类，不削弱原渲染预算约束。

## 系统稳定性与调用定位

插件接口优先低延迟、有界资源和故障隔离。对话观察是 best effort；Memory 是按预算访问本体能力的薄适配，不为了凑齐插件上下文持续等待、无限重试或干扰 Main 的记忆调度。查询成功、无命中和失败仍应准确表达，不用“允许失败”掩盖错误含义。

部分诊断、来源或历史关联缺失时保持未知；这些信息尚未完整不妨碍先适配现有真实搜索。降级与错误处理服务于调用方理解本次结果，不扩展成完整性恢复或状态追赶系统。

如无需求勿增实体。新增适配应接走插件自行拼接 HTTP、编码目标、处理上游状态与管理连接的重复工作；先复用现有 SDK 请求能力、RPC、HTTP 池和服务启停，再决定新增对象。职责需要集中，不等于每个职责必须独占一个类、实例或文件。

接口组织遵循 [对话计划的精简原则](/design/plugin-conversation-bus-refactor-plan#如无需求勿增实体)，使用独立的 Memory 客户端，不另加聚合门面或全局能力矩阵。新读取操作直接表达不支持、未就绪、失败和成功无命中；不要求与对话统一目标、结果或传输。Memory 的目标必须显式限定角色和主体，对话则允许不加角色筛选，二者用途不同。

## 现状与重新定位

### 旧接口没有形成真正的 Memory 适配

```text
self.bus.memory.get(bucket_id=...)
  → USER_CONTEXT_GET
  → 插件宿主 GlobalState
  → 最近用户输入缓存

self.ctx.query_memory(...)
  → MEMORY_QUERY
  → MemoryQueryService
  → GET /search_for_memory/{角色}/{query}
  → 已弃用的占位结果
```

第一条不访问本体记忆库，还存在进程内写入可达性与 SDK record 包装问题。第二条虽访问 Memory server，接入的却不是当前真实检索接口。

旧 `bucket_id="default"` 是用户输入汇总缓存名称，不代表当前角色、主人或私有长期记忆。不能通过改一个 URL 或把 bucket 自动映射成角色来完成重构。

放弃旧定位是停止基于该缓存模型增加新能力，不是立即改变旧方法的返回含义。旧接口留作兼容，新能力使用独立入口、操作与类型化结果。

### 本体记忆对象

| 对象 | 含义 | 插件使用边界 |
| --- | --- | --- |
| Facts | 从对话提取、去重并维护的原子事实 | 检索与结构化读取，不自行重复抽取一份平行事实库 |
| Persona | 人物、角色、关系等长期信息 | 读取正式视图；不等于完整角色卡或最终 system prompt |
| Reflections | 带证据、确认、争议和抑制等规则的认识 | 保留类型与本体规则，不能把检索分数当成可信度 |
| Archived facts | 已归档事实 | 由本体决定如何参与检索 |
| Recent history | 可能经过压缩和整理的近期记忆历史 | 不承诺逐条还原前台聊天记录 |
| Rendered context | 为模型使用筛选和预算化后的记忆文本 | 独立于搜索与原始对象读取 |
| EventLog | 内部审计、恢复与重放日志 | 不是现成的插件变化通知流 |

`entity`（如 master、neko、relationship）是内容分类，`subject/scope` 是主体与隔离范围，两者不能合并成一个含糊的 owner。

## 现有接口的适配判断

| 本体接口 | 现有能力 | 计划用途 |
| --- | --- | --- |
| `POST /query_memory/{角色}` | 内容、时间以及 subject 范围内的结构化检索 | 第一批核心适配 |
| `GET /get_persona/{角色}` | 完整内部 persona JSON，可能包含 scoped 内容与内部维护字段 | 不直接作为新 SDK 数据源；首批由本体补正式 private 投影，不把旧 UI／memory_browser 接口改成新 SDK 结果 |
| `GET /get_recent_history/{角色}` | 格式化近期历史文字 | 不解析展示字符串来冒充结构化消息 |
| `GET /get_settings/{角色}` | 渲染 persona／反思等内容 | 渲染候选，不等于无副作用原始读取 |
| `GET /new_dialog/{角色}` | 新对话背景、近期历史及其他动态内容 | 不是 Memory 总读取入口 |
| `POST /internal/memory/{角色}/scoped_context` | 显式 subjects 下的预算化背景 | 后续渲染适配，保留顺序与状态语义 |
| `POST /internal/memory/{角色}/turn_context` | Main 自动回合记忆补充 | 不直接开放为插件通用查询 |
| `/cache`、`/process`、`/settle` 等 | 缓存、写入、抽取与结算 | 不纳入首批读取 API |

当前混合检索由本体执行 BM25、向量检索及融合排序。代码还会把 persona 渲染预算未驻留的条目加入候选池，部分旧注释对此描述滞后。适配器应保留实际结果类别，不能照旧注释重做检索池。

### 首批 search 的覆盖限制

| 本体数据 | 当前正式搜索边界 |
| --- | --- |
| Facts／Reflections | 本体筛选后参与相关检索，不保证枚举全部条目或全部生命周期状态 |
| Archived facts | 内容召回中主要参与关键词路径，纯时间召回参与时间排序 |
| Persona | 私有 query／组合查询补入按背景规则未驻留的非保护、非抑制条目；已驻留、保护和抑制条目不能靠 search 枚举，scoped 搜索也不追加 Persona 池；纯时间路径不加入 Persona |
| Recent／对话原文 | 当前 search 不直接提供近期历史或全部原文检索，不能用命中还原逐条聊天 |
| Main 实际上下文 | 搜索或重新渲染均不证明某次 Main 回复实际看到了这些记忆 |

因此 search 未命中不能推断用户没有相应偏好；候选中的条目也可能因相关性、过滤或返回预算未被选中，刚发生的对话可能尚未被后台抽取。首批以独立的 private Persona 当前知识快照满足角色已有的人物／关系知识读取，不改变 search 的候选策略，也不让 SDK 私自拉完整 JSON 拼池或重排。该快照仍不包含所有 Facts、Reflections、归档、原文历史或 Main 实际上下文，详见 [get_persona 的范围与语义](#get_persona)。

### 不复用 Main 的回合调度身份

自动 `turn_context` 按角色维护最新请求和正在执行的工作，新请求会把旧请求标记为被替代。插件对同角色直接调用该入口，可能干扰 Main 的自动召回。

插件主动查询使用真实搜索接口。未来若提供插件自己的回合背景构造，可复用本体算法，但需要独立的调用与调度契约，不能共用 Main 的替代请求身份。

### 区分读取 渲染与使用反馈

`new_dialog` 涉及同步、活动及语言状态；其他渲染接口也可能刷新抑制等状态。不能因为 HTTP 方法是 GET 就宣称没有领域副作用。

读取一条记忆、把它渲染给插件的模型、角色实际向用户提及它，是不同事实。搜索不默认标记已提及，渲染不默认表示 Main 已使用；使用反馈不在本计划中设计。

重新查询或渲染的结果也不是 Main 某条回复实际使用的记忆快照；后者属于另一项生产端观察能力，不在本计划中冒充实现。

## 适配架构与所有权

```text
插件个体
  → MemoryClient(self.ctx)
  → PluginContext 控制 RPC
  → 插件服务 Memory 适配层
  → Memory server HTTP
  ├→ search：本体真实检索 → 现有检索池与相关领域组件
  └→ get_persona：本体 private 投影 → 当前 PersonaManager
```

| 层 | 负责的内容 |
| --- | --- |
| SDK | 显式目标、参数、类型化结果和稳定错误 |
| PluginContext 与 RPC | 连接身份、截止时间、取消、响应关联 |
| 插件服务适配层 | HTTP 连接复用、协议转换、业务状态检查与错误映射 |
| Memory server | 作用域过滤、检索、渲染、读取视图和领域一致性 |
| 本体存储组件 | 文件、索引、锁、缓存、迁移、归档与维护 |

不在插件进程创建 FactStore 或 PersonaManager，不导入 Memory runtime 的全局实例，不由插件服务直接读写记忆 JSON／SQLite。跨进程导入同一模块不会取得运行服务中的实例，还会绕开现有缓存、删除和迁移边界。

已有 `memory.scoped_client` 的 wire 规则可在宿主侧复用或借鉴，但可分发 SDK 不因此依赖完整本体源码。补充读取视图时，在 Memory 服务内部复用已有领域方法，不让大型 routes 模块继续承担另一套业务实现。runtime 组件初始化或重绑定后，适配应访问当前服务实例，不持有另一份长期驻留的引擎。

第一批搜索主要改插件 SDK、RPC 和服务适配层；private Persona 另在 Memory server 补正式投影及薄读取边界。两者均不要求改 Main/Agent 对话流程，不重做记忆引擎。

这里的层表示不同职责与通信边界，不是新增运行时对象的清单。SDK 客户端、Context 请求适配和薄 handler 可以保留必要边界；它们不重复解析主体、解释检索结果或层层包装同义字典。普通对话消息继续沿用 message-plane 读取，Memory 的 HTTP 适配不成为其他领域读取的中心。

### 非 plugin 接入边界

非 plugin 的回复身份、观察出口和覆盖增量只由 [对话计划的最小边界](/design/plugin-conversation-bus-refactor-plan#非-plugin-改动的最小边界) 与总实施表维护；Memory 不复制一套 Main 实施顺序。1B／1C 不依赖 5 的早期输入、6A／6B 回复阶段或 7A／7B 播放观察完成，也不要求 Main 提供完整模型上下文。

| 范围 | 复用与必须补的内容 | 后置或不随本次改动 |
| --- | --- | --- |
| 真实 search | 复用已有 query_memory 请求、候选池、范围归并、时间和预算行为；主要完成插件侧正式适配，现有 200 业务错误由适配器识别 | 不因接 SDK 改写 FactStore／检索算法／时间解析，不新增 Main 调度身份、召回状态机或只为插件维护的记忆副本 |
| private Persona | 必须在已有 Persona 领域补正式 private 投影及薄 HTTP 路由；复用当前 Manager、角色锁、正常 ensure 和原生 private 判定，只提取允许字段 | 不以此修整全部渲染 helper、迁移策略、归档、冷却、修正或管理 API；不先复制全量 JSON 再本地过滤，不建新引擎／PersonaService |
| 原有 Persona UI 读取 | 保持旧 UI／memory_browser 用途，新 SDK 独立读取正式投影 | 不把旧端点改成 private，也不新增冻结所有内部字段／正常维护行为的插件兼容层 |
| 初始化与公共设施 | 允许既有必要加载／迁移／角色卡同步；沿用当前 runtime 与 HTTP 池所有权 | 不额外消费 outbox、刷新冷却、标记提及或触发抽取／反思 LLM；不要求零内部写入，不改 Main 的生成、TTS、播放或自动召回流程 |

Persona 的 scoped、渲染、审计和写入／使用反馈仍留待实际需求，不因 Main 分批观察而提前建设。Agent C 只负责上述本体 private 边界，Agent B 负责插件侧协议／适配，公共接线由集成负责人完成；精确文件归属和交接只维护在对话计划的统一负责人表。若后续发现必须改变既有记忆领域规则，需作为新的范围问题单列，不能混入“补一个读取接口”的实现。

### 公共设施与连接生命周期

两者复用现有宿主设施，不建设通用数据平台。本节作为公共复用规则的主要说明；对话领域具体分工见 [conversations 的模块设计](/design/plugin-conversation-bus-refactor-plan#模块归属与实现组织)。

| 实际复用点 | 使用方式 | 各领域保留的内容 |
| --- | --- | --- |
| `plugin/core/rpc.py` | 使用既有 RpcPeer、dispatcher、截止时间、取消与响应关联 | 操作名、请求与业务结果 |
| SDK Result 与基础错误 | 使用现有轻量类型，Memory 明确包成 Ok/Err | Memory 的无命中与业务失败、对话的事件回调语义 |
| SDK 延迟导出与 Context | 保留轻量导出及现有请求／订阅适配，按领域实际用法取得客户端 | Memory 显式构造客户端，对话从 bus 取得；各自 DTO 与调用流程 |
| `utils.http.internal_client` | 宿主请求时获取当前循环的内部 HTTP 池 | Memory 上游路径、payload 与状态映射 |
| 宿主生命周期与插件退出收尾 | 登记并清理各自资源，沿用现有独立／嵌入模式所有权 | 单个 watcher、独立查询和服务池的不同寿命 |
| 序列化、时间与基础诊断工具 | 在实际边界调用，减少重复转换 | 各自字段含义、预算、保留与丢弃策略 |

不增加 BaseObservableDataService、统一 Record／Query 或要求 Memory 实现 watch 的父类。对话可使用 message plane，Memory 使用控制 RPC 后访问 HTTP；公共工具通过组合复用，不强制相同返回容器、传输路径、存储或 scope。

Memory 的 `Ok/Err` 和对话回调可采用不同形式，基础失败含义明确即可。目标显式传入，不要求为组合使用统一 MemoryTarget 与对话条件。相同内容出现在近期观察与压缩记忆历史中是正常领域差异，不合并为万能消息，也不互相兜底。

当前内部 HTTP helper 按事件循环复用 client，已处理多循环环境。backend 在实际请求中获取该池，不在 service 构造或 SDK 属性访问时创建 client，不每次请求用 `async with AsyncClient()`，也不把启动循环创建的 client 跨线程使用。

单个请求不关闭池。独立 plugin server 沿用其应用关闭逻辑清理池；嵌入宿主的池由所属服务／事件循环现有关闭流程处理。新增 backend 不额外注册第二份全局 atexit 或 supervisor，也不关闭其他服务正在复用的 client。

Memory 慢或不可用不阻塞对话事件派发和视图更新。并发限制按请求所属循环与服务执行，不增加一把跨业务全局串行锁；停止 watcher 不取消插件所有独立 Memory 查询，插件退出时才统一停止其拥有的请求。

资源保护优先补在既有接纳、调度和清理位置。限制长期请求时同时关注其占用的公共 RPC 在途资源，不让等待 Memory 额度的请求无界堆积；不为此预建另一套 RPC dispatcher、统一任务平台或独立 supervisor。具体并发与批量参数按实际负载测量确定。

Memory 结果仍依赖插件共享下行返回，应与 [对话计划的共享下行隔离](/design/plugin-conversation-bus-refactor-plan#共享下行通道的隔离) 一起验证，不能只测 HTTP 耗时。Persona 等大结果也受单响应及总待发字节／在途额度约束，不能因属于 RPC 响应而无限取得控制优先权，挤住 STOP 等小型生命周期消息。客户端 timeout 不能代替本体执行资源限制，取消后仍可能运行的计算应计入并发负载观察；不据此增加取消服务或任务平台。

对话事件不自动搜索或写回 Memory，Memory 查询不依赖 watcher 或完整对话缓存。插件业务回调是否调用 Memory 由插件决定，框架不为补齐上下文自动发起查询。

## 模块归属与契约设计

以下为职责落点，当前 HTTP 适配保留在单个领域模块，不要求每个职责各建文件、类或实例。首批公开签名按本文已定契约，后续读取在对应阶段确定；内部按 memory 能力命名，不以替代阶段或某个调用插件命名。

```text
plugin/sdk/shared/contracts/memory.py
  MemoryTarget / MemorySubject / 结果 DTO / 显式 wire 编解码

plugin/sdk/shared/memory/client.py
  异步 SDK 门面、请求与 Ok/Err 转换

plugin/server/application/memory/
  __init__.py        两个读取的目标／预算／HTTP 映射，按复杂度再拆分

plugin/server/messaging/handlers/memory.py
  保留旧 handler，并增加新操作的薄转交
```

### 一处契约与一次上游映射

目标、结果和编解码集中在轻量 contracts 模块，依赖标准库与已有 SDK 基础类型。宿主直接引用叶子模块，不导入 `plugin.sdk.plugin` 的完整门面；SDK 类型模块不导入 core、server、app、FactStore 或 PersonaManager，不读取配置、不产生网络和任务。

SDK 作者使用正式门面导出，不依赖 shared 路径。当前 SDK 打包测试枚举整个子树，新增模块需保持独立可导入及完整收集；不为本次功能迁移所有已有 SDK 模块。

本体响应字段到 SDK 结果含义只映射一次，例如 `tier` 到公开类别、排序分数、事件时间和 scope 的映射，集中在 HTTP 适配位置；独立拆分时由 backend 承担。service 不再次解析另一套同义 MemoryHit，core 传输不再包一层 MemoryRecord；handler 只通过契约编码响应。

MemorySubject 在 SDK 中是轻量值与 wire 描述，不复制本体身份归并、检索过滤或信任逻辑。主体编码遵循现有本体 wire 契约，以固定样例保持一致；本体仍进行实际领域校验，不反向依赖插件 SDK。

### 调用路径与错误职责

```text
SDK client
  → Context 注入的异步请求能力
  → 既有控制 RPC
  → 新操作的薄 handler
  → 必要的 Memory 适配逻辑（目标、预算与协议转换）
  → 使用当前循环的内部 HTTP 池访问本体
  → 一次上游结果映射
  → 契约编码、SDK 解码为公开结果
```

第一批增加 MEMORY_SEARCH 与 MEMORY_PERSONA_GET 操作，分别承载真实搜索与 private Persona 快照，不重用旧 MEMORY_QUERY 或 USER_CONTEXT_GET 语义。后续读取按对应阶段的实际能力注册，不预建空操作，也不为每个 SDK 方法增加一套请求等待实现。

MemoryClient 由插件显式构造，PluginContext、SdkContext 和 NekoPluginBase 只复用或补通现有请求适配，不新增 Memory accessor、客户端工厂或各自的搜索／Persona 业务逻辑。SDK client 借用已有请求能力，通过小型接口或注入函数连接传输，不自己创建 RpcPeer 或直接 import server。

目标与预算处理不重复本体的时间算法、候选池或 scope 合并；HTTP 适配处理 HTTP／业务错误，并保留明确错误码。RPC 继续管理传输失败，SDK 边界一次转换为 MemoryError 和 Result，不由每层包一份含义相同的 result 字典。这两个职责可以先位于同一领域模块，拆分后也不重复解释数据。

不要求先创建 repository、通用 backend 工厂或继承体系，也不要求首批必须实例化一个 service 和一个 backend。搜索与 Persona 读取先共用一个 Memory 领域模块及现有请求设施；只有拥有实际资源或状态时才需要相应对象。能力增加且复杂度需要时，再按上述职责拆分，不能为第二个方法另建 PersonaService、专用连接或纯转调层。

### 兼容归属与按需创建

现有 `application/messages/memory_query_service.py` 和 context_query_service.py 可保留为旧语义实现，新的真实 Memory 放在 memory 领域。兼容保护的是公开操作与行为，不固定 handler 名称、文件位置或内部调用链；新操作使用独立语义，不按调用者是否新版切换旧操作含义。

首批正式访问方式确定为 MemoryClient(self.ctx)，从 plugin.sdk.plugin 导入并由插件显式构造；不新增 self.memory 或 ctx.memory 属性。旧插件可能已经使用 self.memory，不能被只读 property、基类赋值或同名描述符覆盖。客户端借用既有 Context 请求能力，不创建新的 RpcPeer、HTTP 池或连接；构造时不做 I/O，方法调用时才请求。

在包含新模块的新版 SDK 中，Context 包装保持既有兼容判断，新能力不加入旧上下文的强制属性清单。使用时缺失传输能力返回明确错误，不影响其他功能；旧宿主自带旧 SDK 缺少导出的情况由版本要求处理。MemoryError 复用现有 SdkError 基础，不另建并行错误层级。

新客户端通过现有错误码机制区分能力不支持、未就绪和请求失败，正常无命中仍是成功结果。新 Result、参数校验和错误规则限于新操作；共享包装器不因本次重构顺带收紧所有旧 Bus 的行为，明确问题按兼容修复流程单列。

第一批不维护插件侧搜索结果或 Persona 快照的长期缓存，连接池由宿主复用，本体已经负责事实和索引缓存。避免为减少一次 HTTP 请求而再引入记忆删除、更新与 scope 失效同步。未来有实测收益才讨论有限缓存，不默认查询 conversations 或在后台补齐。

### 结构化读取的职责保持

persona、近期历史和背景渲染的正式视图在 Memory server 中由已有领域组件构造，routes 只负责 HTTP 入口，插件 backend 只做公开契约适配。不要为每个字段增加一层 proxy，也不让 SDK 抓取内部文件或解析展示文本。

新增能力时保持单向依赖：Memory 本体不导入插件类型，插件 service 不访问本体 runtime 单例，SDK 不导入宿主。依赖注入用于替换 HTTP 调用或读取接口进行测试，不增加公共容器框架。

## 目标范围与主体模型

新的 Memory API 不使用 bucket。每次调用显式传入角色与范围，避免后台任务或多角色插件依赖一个可能变化的“当前角色”。

MemoryTarget、MemorySubject、首批搜索结果与 Persona 快照使用轻量 dataclass 及显式 wire 编解码，从 plugin.sdk.plugin 导出；主体编码沿用本体规则。以下用法已在 SDK 0.1.0 接通；近期历史与渲染的数据形态在后续阶段再冻结。

```python
# SDK 0.1.0 用法。
from plugin.sdk.plugin import MemoryClient, MemoryTarget, MemorySubject

memory = MemoryClient(self.ctx)

private_target = MemoryTarget.private(character="YUI")

group_target = MemoryTarget.scoped(
    character="YUI",
    subjects=[
        MemorySubject.group_chat(
            platform="qq",
            conversation_id="group-123",
        ),
        MemorySubject.group_participant(
            platform="qq",
            conversation_id="group-123",
            actor_id="user-456",
        ),
    ],
)
```

| 规则 | 要求 |
| --- | --- |
| 私有目标 | 明确选择该角色的 legacy private 范围 |
| Scoped 目标 | 必须有合法 subjects，保留主体与 scope |
| 空值处理 | 空列表或错误主体不得归一化成私有查询 |
| 顺序 | 保留 subjects 顺序；背景渲染中可能代表预算优先级 |
| 身份 | 不按昵称猜测同一人物，不在插件侧复制账号／实体归并规则 |
| 范围过滤 | 由本体在排名、渲染或读取响应序列化之前执行，不先取全部再由宿主／SDK 本地筛选 |
| 能力差异 | search 支持 private／scoped；首批 get_persona 只支持 private，合法 scoped 目标明确返回不支持，不回落或忽略 subjects |

当前检索接口中，省略 subjects 保留旧私有语义，显式空列表不合法；适配必须保留两者差别。目标描述符不是额外授权凭证，不改变现有主体身份和访问边界。

## SDK 门面与分批能力

下文 memory 是上述显式构造的客户端，无额外聚合门面或基类便利属性。SDK 客户端只提供实际接通的能力，后续接口不以空实现占位。

| 能力批次 | 目标 API | 范围 |
| --- | --- | --- |
| 第一批 | `search(...)` | 真实内容检索、时间检索、组合查询、显式作用域与正确错误处理 |
| 第一批 | `get_persona(...)` | 仅 private 当前知识快照，保留来源、保护与抑制标记；不含 scoped、渲染和审计 |
| 后续需求候选 | 近期记忆历史读取 | 若有需求，先明确消息／摘要、范围及数量语义；首批不冻结方法签名或 DTO |
| 后续需求候选 | 预算化背景渲染 | 若有需求，先明确预算、调度隔离与副作用；首批不冻结方法签名或 DTO |

该表区分首批确定能力与后续需求候选，不表示已承诺交付全部候选。第一批不以重构全部本体记忆管理 API 为前提。

### search

```python
# SDK 0.1.0 签名。
async def search(
    *,
    target: MemoryTarget,
    query: str | None = None,
    time: str | None = None,
    language: str | None = None,
    budget_ms: int | None = None,
    timeout: float = 5.0,
) -> Result[MemorySearchResult, MemoryError]:
    ...
```

```python
# 目标用法：三种检索共用本体检索契约。
await memory.search(target=private_target, query="学习计划")
await memory.search(target=private_target, time="2026-10")
await memory.search(
    target=private_target,
    query="学习计划",
    time="2026-10",
)
```

| 参数 | 含义 |
| --- | --- |
| `target` | 角色和主体范围 |
| `query` | 查询内容 |
| `time` | 交由本体解析的事件时间条件 |
| `language` | 请求语言；省略时由本体按目标范围解析，不强塞插件进程语言 |
| `budget_ms` | 本体语义检索对向量分支的等待预算；None 不指定，当前纯时间路径不使用，不是整轮计算硬上限 |
| `timeout` | SDK 整次调用等待上限，与本体向量等待预算不同 |

SDK 只要求 query 与 time 至少一个去除首尾空白后非空，完全空的调用返回参数错误；不在 SDK 判断时间表达式是否有效或复制本体解析算法。首批沿用本体对无效时间的既有回退或空结果行为，本体未报告时间条件是否生效时保持未知，不能因请求带了 time 就宣称已过滤。严格时间过滤另有需求时再补本体验证，不阻塞真实搜索。

timeout=5.0 覆盖从 SDK 发起到结果解码的总等待，控制 RPC 和 HTTP 传递剩余截止时间，不在每层重新取得完整预算。budget_ms=None 时不自动设定或推算值；显式值按上游契约透传。当前 hybrid_recall 从该值扣除已用时间来等待向量分支，超时回落关键词；不约束全部加载／BM25／序列化，也不应用于 recall_by_time，不宣称它限制了整轮计算。整次调用仍受 timeout 总截止约束。取消停止本次等待并传播已有取消信号，不增加任务 ID 或取消服务，不承诺远端计算已停止或维护副作用已回滚。

现有上游没有公开的参数不自动成为 SDK 能力。例如若以后增加结果数量、排序或类型筛选，需要明确是在本体执行还是仅截取返回结果；作用域绝不能退化为返回后的本地筛选。

### 搜索结果与命中对象

```python
# 目标用法：沿用现有 SDK 的 Ok/Err。
from plugin.sdk.plugin import Err

result = await memory.search(
    target=private_target,
    query="用户喜欢什么饮料？",
)
if isinstance(result, Err):
    self.logger.warning("记忆查询失败：{}", result.error.code)
else:
    search = result.value
    for hit in search.items:
        print(hit.id, hit.text, hit.kind, hit.retrieval_score)
```

```text
MemorySearchResult
  target
  query
  requested_time
  items[]
  candidates_total
  elapsed_ms
  diagnostics

MemoryHit
  id                    本体提供时保留，可缺失
  kind                  本体 tier 字符串，保留未来未知类别
  text
  entity
  subject               已知主体与作用域
  retrieval_score       检索排序分数，时间检索可以为空
  created_at            记忆记录时间
  event_start_at        已知事件起始时间
  event_end_at          已知事件结束时间
```

kind 直接保留上游 tier 字符串，当前已知 fact、reflection、fact_archive、persona 等；未知类别透传，不用封闭枚举使整条结果失败，也不增加一组同义 raw 字段。text 和类别等核心字段畸形时返回协议错误，不伪装无命中。id 缺失或为空时为 None，排序分数和时间只在上游提供时填值，诊断缺失时保持 None。

subject 保存本体明确提供的主体与 scope；legacy private 命中的 subject 可为 None，由结果 target 表达私有归属。检索分数不是置信度，记录时间不是事件发生时间，entity 不是 subject。裸 id 只在其本体命名范围内解释；首批不新增全局 MemoryRef，跨类别引用另有实际需求时再明确角色、类别、subject／scope 与本地 ID 的组合。

正常无命中返回 `Ok` 加空 items，执行失败返回 `Err`。诊断只保留本体确实报告的信息，不根据结果多少推测向量服务是否可用。

搜索结果承载有实际含义的命中与上游信息，不额外增加通用 Page／Lookup 容器。条目引用在实际需要且本体身份足够明确时再定，缺失 ID 不由正文哈希补造；诊断不足不通过新增状态矩阵、重复查询或预建诊断系统补齐。

### get_persona

首批已确定提供 private Persona 的当前知识快照，解决 search 无法枚举角色已保留人物／关系知识的问题。它是本体当前 Persona 条目的结构化投影，不是检索结果、可直接注入模型的背景、完整角色卡或 Main 某次回复实际使用的上下文。

```python
# SDK 0.1.0 签名。
async def get_persona(
    *,
    target: MemoryTarget,
    timeout: float = 5.0,
) -> Result[MemoryPersona, MemoryError]:
    ...
```

```python
# 目标用法：与 search 共用同一个显式客户端。
result = await memory.get_persona(
    target=MemoryTarget.private(character="YUI"),
    timeout=5.0,
)
if isinstance(result, Err):
    self.logger.warning("Persona 读取失败：{}", result.error.code)
else:
    for section in result.value.sections:
        for entry in section.entries:
            print(section.entity, entry.text, entry.source,
                  entry.protected, entry.suppressed)
```

```text
MemoryPersona
  target                  本次显式 private 目标
  sections[]              MemoryPersonaSection
    entity                本体分类字符串，保留未知分类
    entries[]             MemoryPersonaEntry
      id                  本体已有 ID；缺失或空值为 None
      text                当前条目正文
      source              本体来源字符串；缺失时为 unknown
      protected           本体保护标记
      suppressed          本体 suppress 字段的公开名称
```

这些 DTO 在同一 Memory contracts 叶子模块定义并从正式门面导出，不新增通用知识对象、Page／Lookup、全局 MemoryRef 或独立 Persona 客户端。private 归属由 target 表达，不为尚未支持的 scoped 读取增加 section.subject 或空扩展结构。

#### 快照包含与排除

| 项目 | 已定契约 |
| --- | --- |
| 当前条目 | 返回所选角色 private 范围内当前保留的 Persona 条目，包括已驻留／未驻留、保护与抑制条目；不做相关性排名、渲染 token 裁剪或保护条目渲染数量裁剪 |
| 分类与顺序 | 保留本体 entity 字符串，不封闭为 master／neko／relationship 三类；按本体当前分类及条目顺序返回，不把顺序解释为重要性、可信度或跨调用稳定位置；无条目的分类可省略 |
| 来源 | 保留 character_card、settings、reflection、manual 等实际字符串及未来未知来源；不增加完整来源链或据此宣称内容已核实，缺失来源不猜测 |
| 保护 | protected 表达本体保护状态，不是正确性、授权或可信度标记；快照不意味着插件可修改该条目 |
| 抑制 | suppressed 表达本体读取时保存的抑制状态，通常限制主动提及，不代表条目已删除；插件不得把含抑制条目的整份快照当作可自由主动提及的模型背景 |
| 排除 | 不包含独立 Facts／Reflections 池、Persona 归档、待确认修正、版本历史、证据计数与争议推导、source_id 追溯链、embedding、token 缓存、内部维护时间或完整 persona.json |
| 一致性 | 在当前 PersonaManager 既有角色锁内完成正常 ensure 与 private 最小字段提取，形成独立结果；遵循现有锁入口，避免重复持锁；不暴露可变内部引用，不要求跨存储或跨两次请求的事务快照 |
| 完整性与资源 | 不提供 query、limit、max_tokens、分页或静默裁剪；投影时累计条目／正文大小并及时拒绝超限，HTTP 读取与共享下行在既有请求路径执行有界响应保护；整次明确失败，不能返回部分条目冒充完整快照；阈值及并发额度留待实测 |

id 不由正文哈希补造，也不承诺跨角色、类别或生命周期全局唯一。正常无条目返回 Ok(MemoryPersona) 与空 sections；无此能力、未就绪、存储受限、畸形数据和超限均为 Err，不伪装空快照。当前本体实际保留的旧字符串条目可作为 private 条目读取，缺失 ID 为 None、source 为 unknown，protected／suppress 按本体缺省 False 投影；不为模拟内部规范化器把任意对象强制转成文字或把错误类型转成布尔值。正文等核心字段畸形时明确协议错误。

隔离判断先检查原条目的范围字段，再取允许字段；普通分类内的旧字符串条目没有主体字段，按 legacy private 处理。最小投影可直接在锁内构造或仅复制必要字段，不要求先深拷贝完整 persona JSON、embedding、版本历史或其他范围的数据，也不调用会原地补字段／删除旧字段的内部规范化器。释放锁后只编码独立结果；不为快照读取另外整理一套迁移或修复系统。

#### Private 隔离与本体接入

首批只接受 MemoryTarget.private(character=...)。scoped 目标即使只有一个主体也明确返回不支持，空 subjects 等非法目标仍是参数错误；不回落私有、不合并私有与群组、不因同名人物或角色卡来源而放宽范围。search 已有的 scoped 能力保持独立，不因此缩减。

Memory server 在响应序列化之前完成隔离：排除 scoped 分类及损坏的 scoped 分类标识，并在可返回分类内逐条用本体现有 legacy private 判断校验 subject_kind／subject_id／scope 均为空。部分字段缺失或冲突不能降级为 private。不能只凭分类名、分类级 metadata 或现有渲染 helper 的非 scoped 分类分支判断，也不能把全量 JSON 送到插件宿主再过滤。

```text
MemoryClient.get_persona(target=private)
  → Context 既有控制 RPC：MEMORY_PERSONA_GET
  → 薄 handler → 同一 Memory 适配模块与当前循环 HTTP 池
  → Memory server：GET /internal/memory/{角色}/persona（拟新增，仅 private）
  → 当前 PersonaManager：既有加载／角色锁内 private 过滤与最小字段提取
  → 独立投影结果（不复制完整内部对象）
  → HTTP → 一次 SDK 字段映射 → Ok(MemoryPersona)／Err
```

上图 HTTP 路径是已新增的正式 private 读取边界；路由仅负责参数、错误和转交，投影逻辑落在已有 Persona 领域代码，不新建通用投影框架。Memory 本体不依赖插件 DTO。旧 GET /get_persona/{角色} 继续服务现有 UI／memory_browser，不被重定向为 private SDK 投影；这不把其内部字段、正常维护或文件布局新增为永久插件兼容契约，也不让新 SDK 回退调用它。确定缺少新 RPC 操作或 HTTP 端点时明确报能力不可用；参数／角色错误及普通 HTTP 故障不能只凭状态码一律当作缺少能力。search 是否可用仍按其独立调用结果判断，不增加探测轮询、另一条旧宿主读取路径或全局能力矩阵。

读取不标记已提及，不强化／削弱证据，不发起抽取、反思、修正 LLM 或 Main 回合任务；不为刷新 suppress 再执行冷却维护，返回保存的标记，不自行按时间推算当前可否提及。允许复用现有正常加载、迁移、角色卡／设置同步等必要初始化维护，不承诺零文件写入；正式读取边界不额外消费长期同步 outbox 或执行整轮维护。其结果只代表本体在本次读取边界已加载的 Persona，不保证刚发生的对话已抽取或所有后台工作已追平。timeout=5.0、剩余截止传播、取消与连接所有权沿用 search 的规则，不承诺初始化写入可回滚。

scoped Persona、预算渲染、证据／版本审计及使用反馈均不在首批，不预建对应参数、空操作、后台监听或专用缓存。后续若确有需求，按独立业务契约增加，不能把当前快照逐步扩成内部 JSON 的永久镜像。

### get_recent_history

本节仅保留后续需求边界，方法名是能力候选，不是首批预留 API。具体签名、DTO、数量含义、顺序和分页，在需求确认时再确定。

摘要不能被伪装成用户或角色刚说过的一句话，记忆历史条目数也不必等于原始消息条数。当前 HTTP 展示文本不解析回结构化消息；已有私有历史不代表所有 subject 都有独立历史能力。不支持的目标不退回私有数据，不编造原始消息身份。

### render_context

本节仅保留后续需求边界，不在首批冻结方法签名、token／引用字段或 MemoryContext 对象。若确有为插件模型构造背景的需求，应与 Main 的自动召回调度隔离，不能直接包装其 turn_context，也不无条件套用 new_dialog 的状态行为。

预算与抑制等领域规则由本体执行，不能在宿主侧随意截断背景后假装语义完整。渲染的维护副作用、语言状态和必要来源信息届时另定，不因某个现有接口缺少引用字段就预建来源追踪系统；不从文字反推来源，也不冒充 Main 本轮实际上下文。

搜索、private Persona 快照、未来渲染和实际提及反馈保持不同语义。新增 private 快照不构成上线渲染或反馈能力的要求。

## 错误 可用性与降级

```text
MemoryError
  code
  message
  details               沿用现有错误上下文，确有需要时提供
```

| 结果类别 | 契约方向 |
| --- | --- |
| 成功有结果 | `Ok` 加类型化结果 |
| 成功无数据 | search 返回 Ok 加空 items；get_persona 返回 Ok 加空 sections，不是裸空列表或服务故障 |
| 参数或范围错误 | 明确失败，不静默更换目标 |
| 能力不支持 | 与无数据、暂时不可用区分 |
| 服务未就绪或存储受限 | 保留可识别状态，不映射为无命中 |
| 上游检索失败 | 即使 HTTP 200 也检查业务 error_code |
| 调用超时或传输失败 | 返回明确错误，保留总截止时间与取消语义 |
| 降级检索 | 本体提供诊断时如实保留；未提供时保持未知 |
| 协议不匹配 | 明确失败，不把畸形响应当空数据 |

当前部分内部失败返回 HTTP 200 和 `error_code="hybrid_recall_failed"`；初始化或存储受限也可能返回 409，组件未就绪可能返回 503。适配器不能只检查 HTTP 成功。

Main 工具为保证对话继续而采用的“失败视作无相关记忆”策略，不照搬到开发者 SDK。向量检索超预算后可能仅用关键词结果，现有响应未必暴露足够诊断；适配可先保留未知，不为补全诊断反复检索。若以后承诺可观察的降级状态，再在 Memory server 补字段，不在 SDK 猜测。

MemoryError 使用既有 SdkError 基础的轻量别名，不新增继承体系；search 返回 Ok(MemorySearchResult)，get_persona 返回 Ok(MemoryPersona)，失败均为 Err(SdkError)。以现有 code 为主，领域确需区分时补最小 code，不预设 retryable 字段或自动重试。参数／目标错误、缺失能力、未就绪、上游业务失败、超时、响应超限和协议错误在一次 SDK 边界映射；畸形结果与 HTTP 200 的业务失败均不能转成空列表或空快照。不混用多层 result 字典，也不默认记录查询、召回正文或 Persona 内容。

## 旧接口兼容与迁移

本次重构发布保留当前受支持的公开旧接口，不要求旧插件为新 Memory 修改代码；兼容范围和运行组合沿用 [对话计划的版本要求](/design/plugin-conversation-bus-refactor-plan#兼容范围与版本要求)。不追溯恢复早已删除的 API，不为兼容任意内部实现建立第二套引擎或框架。

旧公开行为与新能力的边界如下；表中记录的既有错误包装不因列出基线就变成永久承诺，修复按下文单列。

| 旧入口 | 保留内容 |
| --- | --- |
| `self.bus.memory.get(bucket_id=..., limit=20, timeout=5.0)` | 保留 bucket 解释、近期输入缓存数据来源、默认值及旧列表／记录包装；不把 `default` 自动映射成角色或 MemoryTarget |
| SDK `ctx.query_memory(bucket_id, query, timeout)` | 保留现有调用形式和返回包装；其首参数仍按旧路径传给角色查询，不因新 target 模型改义 |
| 公开 SDK 支持的旧读取方式 | 保留实际可用的同步／异步调用和异常约定，不因新异步客户端改变旧用法 |
| 旧 USER_CONTEXT_GET／MEMORY_QUERY | 保留操作名及对应结果含义；占位查询不静默变成真实搜索或新 Result，内部实现可重构 |

Core 当前使用 `query_memory(lanlan_name, ...)` 且有自适应读取，而 SDK 包装参数名是 bucket_id；这些差异用于检查公开结果，不冻结未公开的 Core 签名或结构。旧 `bus.memory` 不再作为长期记忆能力扩展；插件主动迁移近期输入读取时使用 conversations_v2，需要真实记忆时使用新客户端。不把 default 猜成当前角色，也不把用户缓存迁移成长期事实。

新 search／get_persona 的 `Ok`／`Err`、显式目标与错误规则不覆盖旧返回值，未使用新能力时不影响旧 Context 的无关功能；旧 Persona HTTP 继续供现有 UI 使用，不改成新 private 投影，不新增冻结内部 JSON 的插件兼容层。新读取不受支持时明确报错，不以旧 JSON、输入缓存或搜索结果补造快照。SDK 版本保持 0.1.0，既有 `[plugin.sdk]` 检查保持不变，但不能据此区分新旧构建能力；运行组合统一按对话计划约定，不再维护第二份版本判断或兼容矩阵。

旧 `bus.memory` 的 SDK record 包装错误按 [已知错误的兼容例外](/design/plugin-conversation-bus-refactor-plan#已知错误的兼容例外) 单独处理：说明旧／新表现和影响并验证，不为模拟错误增加对象，也不让修复阻塞真实搜索。新客户端不依赖旧包装形状。

本次保留旧入口，不预设删除版本；后续弃用另行说明范围和迁移方式。兼容只做必要的薄适配，不固定旧 HTTP 调用、handler 或文件布局来模拟历史实现。

## 暂不纳入的写入 管理与监听

第一批不提供任意 JSON 保存、persona 覆盖、删除、合并、导入和 memory watch。插件不得把已经由本体处理的 conversations 消息再次默认写回记忆，制造重复抽取和历史。

写入、管理与使用反馈需要另外的业务需求，本计划不设计对应命令或把已有管理路由自动扩展为 SDK。

内部 EventLog 供审计与恢复，事件先于视图落盘且会压缩，不能直接包装成“已提交记忆变化”的订阅流。监听确有后续需求时另定提交与作用域语义，当前不以定时重搜或结果差分冒充 memory watch。

## 已确定契约与实施安排

### 第一批实施边界

两份计划的总顺序、依赖、Agent 轮次、共享文件负责人和提交规则只维护在 [conversations 的实施顺序与多 Agent 分工](/design/plugin-conversation-bus-refactor-plan#实施顺序与多-agent-分工)。本文对应其中 1B／1C，只补充 Memory 的领域模块和验收，不另排一套 Memory 阶段或人员表。不在插件侧重写检索、读取文件或创建独立引擎。

首批包括 search 与 get_persona 两条独立纵向读取链，可分别实现和验证；search 不依赖 Persona 读取成功，也不为取得搜索结果先拉快照。两者共用 MemoryClient、Context 请求、薄适配与 HTTP 池，不因增加 Persona 再建 Service、引擎、缓存、门面或调度体系。

真实 search 继续适配现有接口；Persona 在 Memory server 增加最小 private 投影与薄路由。附加诊断不齐全不阻塞首批；确需增加业务错误或稳定字段时，在 Memory 服务补最小结构化响应，不改 Main/Agent 对话流程。

### 结构化读取的阶段边界

首批 Persona 由 Memory server 提供正式 private 投影，scoped Persona、近期历史、渲染和审计留待后续实际需求，不增加空实现。渲染先明确预算、调度隔离与副作用，各能力不支持的目标明确反馈，不自动回落。

### 第 0 步核对记录

2026-10-31，基于本文基线完成，测试总结果及运行方式只维护在 [conversations 的第 0 步记录](/design/plugin-conversation-bus-refactor-plan#第-0-步核对记录)。本文只记录 Memory 的映射和样例。使用合成文本，不读取用户真实记忆；在第 0 步执行时目标操作、客户端和投影尚未实现；该基线测试不能替代后续新能力验收。

#### 现有路径与错误基线

| 核对项 | 当前真实行为 | 后续接入约束 |
| --- | --- | --- |
| 旧 memory.get | SDK bucket_id 为必填关键字参数，limit=20／timeout=5；Core 保留自适应同步／异步。USER_CONTEXT_GET → UserContextQueryService → GlobalState，返回 bucket_id／history；数量会限制到 1..500 | 旧默认 bucket 名不等于参数可以省略，更不是角色；新读取不使用旧缓存。现有 SDK 对 Core MemoryRecord 的包装问题单列，不为了兼容错误重做数据源 |
| 旧 query_memory | SDK async query_memory(bucket_id, query, timeout) 转发 Core，Core 按旧首参数作角色；MEMORY_QUERY handler 取 ctx.remaining，旧 service 每次创建 HTTP client，访问占位 GET 并返回 result 包装 | 新 MEMORY_SEARCH 独立接通真实 POST，使用现有池；不顺便改义旧操作、全局统一旧 Result 或强行迁移旧 service |
| 真实搜索请求 | routes.QueryMemoryRequest 接受 query／time／subjects／language／budget_ms；原生 subjects 用 subject_kind／subject_id／scope，省略或 null 为 private，显式 [] 或超过 8 项拒绝；主体归并由本体执行 | SDK target.kind 为 private／scoped 的选择标记；只在适配处映射一次，private HTTP 省略 subjects，scoped 按原生编码且不回落。SDK 不把昵称或平台字段原样当原生主体，也不复制账号归并 |
| 搜索返回 | results 行有 id／text／tier／entity／subject_kind／subject_id／scope／score 及三种时间；private 结果当前把 scope 填为 legacy_private；诊断缺省不返回。语义返回 query，纯时间还返回 time 且 score=null | results → items，tier → kind，score → retrieval_score；private 三字段组合按下文转换，不把显式 legacy_private 误判为 scoped。requested_time 取本次请求，不证明过滤成功；时间沿用原生字符串或 null，不改成对话 observed_at 秒数 |
| 预算 | budget_ms=None 时向量等待无此额外限制；有值时 hybrid_recall 按剩余预算等向量，超时用关键词；纯时间分支不使用 budget_ms | 保留当前能力及命名，文档不能把它描述成全计算硬预算；timeout 才覆盖整次 SDK 等待，不新增时间解析或预算控制器 |
| 原生失败 | query_memory 初始化不完整为 503；异常可能 HTTP 200 加 error_code=hybrid_recall_failed。409 为存储受限等服务边界；参数错误 422 也可能在原生主体转换时产生 | 先检查 HTTP／业务错误再解码 items；失败不变成成功空结果。原有 RpcRemoteError.code／details 在 SDK 一次转换时保留，不能只保留 str(error)；UNKNOWN_OPERATION 在新边界映射能力不可用，其余不靠状态码猜缺少能力 |
| Persona 当前读取 | GET /get_persona 先 consume_for_name 再 aget_persona；后者 ensure 可迁移／同步／保存，返回内部可变对象。渲染 helper 对普通分类直接取整个 section；内部 normalize_entry 会原地补删字段并把任意对象转字符串 | 新 private 投影不能直接调用旧 HTTP 或照搬浅视图；在当前 Manager 既有角色锁内 ensure、逐级过滤、只提取允许字段。复用初始化维护，但不额外消费 outbox／跑冷却，不复制完整 JSON 或重用有副作用规范化 |
| 请求与连接 | SdkContext 不暴露通用异步控制请求；Core 私有请求 → RpcPeer → registry／request_router → downlink。内部 HTTP helper 按循环建池；独立 plugin HTTP 生命周期关闭共享池，嵌入模式由外层所有者关闭 | 集成负责人补最小私有请求转交，两个 Memory 方法共用且不建新 peer；handler 继续以 ctx.remaining 访问当前循环池，SDK 不导入宿主／Memory 引擎，不额外关闭共享池 |

#### Memory 目标 wire 与结果映射

目标使用一种显式判别：private 为 `{"kind":"private","character":"YUI"}`；scoped 为 `{"kind":"scoped","character":"YUI","subjects":[...]}`，subjects 行直接使用本体 wire 字段。这里的 target.kind 是目标类别，不是命中 tier 或主体 subject_kind。private 不发送 subjects，scoped 必须有 1..8 个合法主体。已有构造器对组件中的 `%`、`:` 按本体方式编码，以原生固定样例核对，不在 SDK 写身份归并。以下为业务请求片段，request_id／from_plugin／timeout／deadline 由既有控制 RPC 补充，不新增请求信封。

```json
{
  "search_request": {"type": "MEMORY_SEARCH", "target": {"kind": "private", "character": "YUI"}, "query": "喜欢的饮料", "time": "2026-10", "budget_ms": 100},
  "scoped_target": {"kind": "scoped", "character": "YUI", "subjects": [
    {"subject_kind": "group_chat", "subject_id": "qq:group-123", "scope": "group_chat:qq:group-123"},
    {"subject_kind": "group_participant", "subject_id": "qq:group-123:user-456", "scope": "group_participant:qq:group-123:user-456"}
  ]},
  "native_search_body": {"query": "喜欢的饮料", "time": "2026-10", "budget_ms": 100},
  "native_search_result": {"results": [
    {"id": "fact-1", "text": "喜欢茶", "tier": "fact", "entity": "master", "subject_kind": null, "subject_id": null, "scope": "legacy_private", "score": 0.0327, "created_at": "2026-10-30T12:00:00", "event_start_at": null, "event_end_at": null}
  ], "query": "喜欢的饮料", "candidates_total": 1, "elapsed_ms": 2.0},
  "sdk_search_value": {"target": {"kind": "private", "character": "YUI"}, "query": "喜欢的饮料", "requested_time": "2026-10", "items": [
    {"id": "fact-1", "text": "喜欢茶", "kind": "fact", "entity": "master", "subject": null, "retrieval_score": 0.0327, "created_at": "2026-10-30T12:00:00", "event_start_at": null, "event_end_at": null}
  ], "candidates_total": 1, "elapsed_ms": 2.0, "diagnostics": null},
  "persona_request": {"type": "MEMORY_PERSONA_GET", "target": {"kind": "private", "character": "YUI"}},
  "native_persona_projection": {"sections": [
    {"entity": "master", "entries": [
      {"id": "p1", "text": "喜欢茶", "source": "manual", "protected": false, "suppressed": true},
      {"id": null, "text": "旧格式偏好", "source": "unknown", "protected": false, "suppressed": false}
    ]},
    {"entity": "neko", "entries": [{"id": "card-1", "text": "角色基础信息", "source": "character_card", "protected": true, "suppressed": false}]}
  ]},
  "failure_with_http_200": {"results": [], "query": "喜欢的饮料", "candidates_total": 0, "elapsed_ms": 0.0, "error_code": "hybrid_recall_failed"},
  "missing_operation_response": {"type": "PLUGIN_TO_PLUGIN_RESPONSE", "to_plugin": "audit", "request_id": "r3", "result": null, "error": {"code": "UNKNOWN_OPERATION", "message": "Unknown request type: MEMORY_PERSONA_GET"}}
}
```

角色只用于经过校验／转义的上游路径，不把 target 原封不动送给 /query_memory。非空 scoped_target 的 subjects 加入同一原生 body；缺省的 query／time／language／budget_ms 不必发送 null。正常空 results 仍构造完整 MemorySearchResult，失败片段必须成为 Err。new DTO、Ok／Err 均不以 Python 对象跨进程传送：控制响应 result 只有契约字典，SDK 再解码包装；不把旧 service 的 result 内层再包一次。

native_search_result 的 subject_kind／subject_id 均缺失且 scope 为 null 或 legacy_private 时，公开 subject=None，由 target 指明 private；scoped 三字段完整时形成公开主体与范围。缺少一部分字段或互相矛盾时为协议错误，不误归私有。这个读取结果映射不替代本体原始条目的 is_legacy_private_entry 判定，后者要求三字段都为空；两种边界不能混用。

新的 Persona HTTP 边界返回上述 sections，不导入插件 DTO；公开 suppressed 在该正式投影从本体 suppress 取值。插件适配只验证／解码，并在 SDK 结果中带上本次显式 target，不重复做范围筛选或冷却解释。样例的 p1 来自当前 retained 条目，旧字符串按本体缺省投影，neko 保护条目也保留；原始分类中的 scoped／部分范围字段条目在本体投影前排除，不传到宿主后再过滤。正常空结果为 sections=[]，不能用空 sections 掩盖缺少端点、存储错误或超限。

### 步骤 1B／1C 实施范围

两项工作均依赖总表步骤 0 的最小协议样例与接线约定，可与 1A 普通对话读取并行。1C 的本体投影不依赖 search 已实现；1B／1C 的插件侧契约、客户端和适配由同一 Agent B 维护。Agent C 负责 1C 的本体领域与薄路由，集成负责人接入公共 Context、导出和 handler registry。具体共享文件以 [统一负责人表](/design/plugin-conversation-bus-refactor-plan#共享文件的统一负责人) 为准，本节不另分配所有者。

| 工作包 | 模块范围 | 交付 | 本步验收 |
| --- | --- | --- | --- |
| 1B：Memory 契约与客户端 | 拟定的 `plugin/sdk/shared/contracts/memory.py`、`shared/memory/client.py`，借用现有 Context 请求能力；公共导出／请求适配交集成负责人接线 | 显式 MemoryClient(self.ctx)、MemoryTarget／Subject、搜索 DTO 与 search；构造不做 I/O，新能力缺失才报错 | 轻量契约可独立导入并打包；private／scoped 编码固定样例一致，空 subjects 不回落；query／time 非空检查、未知 tier／缺失 ID 及 Ok／Err 正确；不覆盖插件 memory 属性或扩大旧 Context 强制条件 |
| 1B：真实搜索适配 | 同一 `plugin/server/application/memory` 领域模块，`messaging/handlers/memory.py` 新操作的薄转交；复用 `utils.http.internal_client` 与既有 RPC，本体搜索原则上无需业务改动 | MEMORY_SEARCH 访问现有 POST /query_memory，沿用本体检索、时间与范围规则；不改旧 MEMORY_QUERY／USER_CONTEXT_GET | 验证内容、纯时间及组合查询、scope 在排名前生效、200 业务失败／409／503／超时／畸形结果与成功无命中区分；总截止传剩余时间，取消／池所有权正确；不创建独立引擎、复制搜索策略或调用 Main turn_context；旧公开读取回归 |
| 1C：本体 private 投影 | `memory/persona/` 中实际适合的读取实现，复用 scopes 判定、当前 PersonaManager 与角色锁；`app/memory_server/routes.py` 只补薄读取边界 | 正式 private 当前知识快照，锁内完成范围判断和最小字段提取；不先复制完整内部 JSON，不新建 PersonaService、投影框架或第二份引擎 | 固定驻留／未驻留、保护／抑制、未知来源／分类、旧字符串和缺字段样例；排除 scoped 分类、损坏范围和普通分类内混入的 scoped 条目；并发更新／删除结果独立一致，不原地规范化、不额外刷新冷却／标记提及／触发 LLM；旧 UI 读取不改成新投影 |
| 1C：插件侧接通与集成 | 扩展同一 Memory 契约、客户端、领域适配和 handler；公共注册由集成负责人完成 | get_persona 与 MEMORY_PERSONA_GET，经正式 HTTP 投影返回 Ok(MemoryPersona)／Err；合法 scoped 目标不支持，非法目标不回落 | 真实 Memory HTTP → 控制 RPC → 插件 SDK 全链路通过；空快照、能力缺失、角色错误、未就绪、畸形及超限不混淆；不回退旧 JSON／搜索结果，不以假上游通过代替交付，不使 search 依赖 Persona 成功 |

上述行是同一纵向能力内的实施责任边界，不要求各建类、各建 PR 或各维护一份 Result／DTO。按实际接通能力定义和公开契约；不为等本体路由而先发布空 get_persona，也不因 search 先完成让另一个 Agent 再写一份 MemoryClient。领域测试随各自实现提交，HTTP／RPC／SDK 接通后由集成负责人核对整体行为。

1C 原生验收同时检查改动范围：现有渲染、冷却、归档、UI 读取及其他写入路径不被新投影重定向或重写；不为新读取另起维护任务。Main 后续按路径接入回复／播放不会成为 Memory 的隐式触发器，Memory 结果也不用于补齐回复 owner、收口原因或播放关联。

单请求的预算、投影大小与超限处理随 1B／1C 完成，不能以“公共资源以后做”推迟。总表步骤 2 使用这些真实结果样例验证共享 RPC 在途及下行接纳，必须在开放实时对话监听前具备基础限制；步骤 8 再以慢搜索、大 Persona、观察流并发确定阈值，覆盖 STOP 延迟、锁内提取、编码解码、积压及关闭。测试工具和连接生命周期继续复用，不另外建设 Memory 负载平台或任务管理器。

### 首批已经确定

| 项目 | 采用方案 |
| --- | --- |
| 入口与类型 | 从 plugin.sdk.plugin 导入 MemoryClient(self.ctx)，显式构造、无基类同名属性；轻量 dataclass、一次契约编解码 |
| 目标 | search 使用 MemoryTarget.private／scoped 与 MemorySubject，主体编码沿用本体；get_persona 首批只接受 private；空 subjects 不回落，范围由本体在排名／响应前过滤 |
| 搜索 | search 与独立 MEMORY_SEARCH，query／time 至少一个非空；时间有效性由本体判断，不新增严格时间策略 |
| 搜索结果 | Ok(MemorySearchResult)／Err(SdkError)，MemoryError 是轻量别名；类别保留 tier 字符串、缺失 ID 和诊断为 None，不新增全局 MemoryRef |
| Persona API 与结果 | get_persona(target, timeout=5.0) 与 MEMORY_PERSONA_GET；Ok(MemoryPersona)，分类仅 entity／entries，条目仅 id、text、source、protected、suppressed；已有身份及未知来源不猜造 |
| 能力范围 | search 提供相关命中；get_persona 提供 private 当前保留 Persona 条目，含驻留／保护／抑制条目，不做排名或渲染裁剪；不承诺全记忆、原文历史或 Main 实际上下文 |
| Persona 本体边界 | 新增正式 private 读取端点，在当前 Manager 既有角色锁内逐级隔离并提取最小字段，不复制完整内部 JSON；旧 UI 接口不改成新投影，新 SDK 不使用它兜底 |
| Persona 读取语义 | 保留本体来源、保护与保存的抑制标记；不标记已提及、不额外刷新冷却或触发 LLM，不承诺零初始化写入／后台追平；scoped、渲染、审计不纳首批 |
| 错误 | 200 业务失败和畸形响应不变成无命中，使用现有 code 与必要的最小领域 code，不建设新错误继承或重试系统 |
| 预算 | timeout=5.0 是总截止时间，RPC／HTTP 传递剩余时间；budget_ms=None 不自动填值，当前仅约束语义检索的向量等待，不作用于纯时间路径或全轮计算 |
| 取消与资源 | 传播现有取消，不承诺远端计算或维护回滚；借用既有 Context 与循环内 HTTP 池，不加任务平台、连接池或长期结果缓存 |
| 兼容 | 新操作不改旧 bucket／占位语义，不占用旧插件 memory 属性；版本与 bug 例外共用对话计划的规则 |

### 接入时核对

| 项目 | 核对内容 |
| --- | --- |
| 请求与响应 | 现有 HTTP 的 subject wire、tier、时间、业务错误与可用诊断，以真实固定样例映射一次；不为补齐元数据重复检索 |
| Persona 领域接入 | 核实当前 Manager 的锁与 ensure 入口、动态分类及 legacy private 判定，锁内过滤并提取最小字段；固定旧字符串／缺字段／损坏 scope 样例，不调用原地规范化或重选已定范围 |
| 截止与关闭 | 在现有 RPC／HTTP 路径传递剩余时间，确认独立与嵌入模式的池所有者；不新增另一份 dispatcher 或关闭逻辑 |
| 发布 | SDK 保持 0.1.0，不因新增导出调整版本号；核对更新构建确实包含所需 API；不承诺旧 SDK 可导入新类型，不自动安排旧接口删除 |

### 留待实测或后续能力

| 项目 | 确定时机与边界 |
| --- | --- |
| 并发与连接参数 | 依据检索耗时、公共在途资源、连接复用和失败表现调整；总截止与有界接纳规则不变 |
| Persona 响应保护 | 依据条目规模、冷加载、锁内提取与并发读取测阈值及额度；覆盖 HTTP／RPC 的单结果和总待发字节，验证 STOP 等小消息延迟；超限明确失败，不引入分页、分片或后台补齐 |
| 模块拆分 | 根据实现复杂度决定 service／backend 是否拆文件或对象，不为结构齐全预建框架 |
| scoped Persona／审计 | 后续有实际需求再明确主体范围、投影或证据／版本读取，首批不预建字段、操作或监听 |
| 近期历史 | 后续阶段确定条目类型、字段、顺序和必要分页，不解析展示文本或退回私有范围 |
| render_context | 对应阶段确定预算执行、维护副作用、调度与 provenance，未明确前不提供空实现 |
| 额外诊断／引用 | 严格时间、全局记忆引用和更细降级诊断只有实际需求时再补本体契约，均不阻塞首批 |

## 验收重点

1. 从正式门面显式构造 MemoryClient(self.ctx)，MEMORY_SEARCH 对接真实 query_memory，不经过占位 GET 或用户输入缓存。
2. query／time 的非空检查与本体时间解析分开，内容、时间及组合查询沿用本体行为；未报告的时间过滤或降级状态保持未知。
3. 私有与 scoped 目标明确，空 subjects、非法主体不回落，范围过滤发生在排名前。
4. dataclass 与 wire 一次映射，tier 包括未知字符串均保留；缺失 ID、时间和诊断为 None，不造置信度、全局引用或额外同义字段。
5. Ok／Err 区分无命中、200 业务失败、409 受限、503 未就绪、超时和畸形响应；MemoryError 复用 SdkError，不因共享别名丢失 code。
6. 查询不影响 Main 的按角色自动召回替代机制，不标记已提及，不依赖 watcher 或对话缓存。
7. 不创建平行记忆库、独立引擎或结果长期缓存，不读取本体文件或跨进程 runtime 单例。
8. 旧公开 memory.get、query_memory 与受支持 RPC 的参数、返回及调用方式保持，内部 Core／handler 可以重构，bug 修复单列而非永久模拟。
9. 新客户端不覆盖旧插件自己的 memory 属性，不扩大旧 Context 强制属性；已有版本声明检查保持不变；同为 0.1.0 时不能据此判断新增能力，导出缺失与运行时操作缺失分别按现有错误处理。
10. get_persona 首批仅 private 正式投影，合法 scoped 明确不支持、非法目标不回落；未来近期历史另定结构并区分消息与摘要，不解析展示文本。
11. 渲染在预算、调度、副作用与来源契约明确后接入，不冒充 Main 实际上下文，缺失 token 或来源 ID 不补造。
12. timeout=5.0 覆盖整次请求，RPC／HTTP 使用剩余时间；budget_ms=None 不自动填值，显式值沿用本体向量等待语义，不冒充纯时间或整轮计算硬限制；取消不承诺远端计算或维护回滚。
13. 使用当前循环的既有 HTTP 池，单请求不关闭共享池；独立与嵌入模式按已有生命周期清理。
14. 新 DTO 与客户端导入或取得时不做 I/O，实际使用缺失能力才报错；不承诺旧 SDK 可导入新增类型。
15. 不新增聚合门面、能力矩阵、纯转调对象或并行调度／资源管理体系；本体改动限制为必要的 private 投影和薄路由，不借机重写搜索、渲染或维护，服务与 HTTP 适配职责按复杂度组合。
16. 慢 Memory 不拖住独立对话派发，测量连接复用、耗时、并发与失败表现，不以诊断齐全或自动重试作为通过条件。
17. 不设计写入、管理或 memory watch；内部 EventLog、管理路由、定时搜索差分均不冒充公共记忆变化流。
18. search 的范围限制在文档及真实样例中可见，未命中不被解释成没有 Persona 或近期事实；get_persona 独立补足 private 当前条目，不改变搜索候选策略或建立数据缓存。
19. 并发观察流与慢 Memory 下同时测量 HTTP、公共 RPC 在途和结果下行延迟，超时／取消后资源仍有界，不用客户端超时替代本体接纳保护。
20. MEMORY_PERSONA_GET 复用同一客户端、RPC 与 HTTP 池，正式本体端点不返回全量内部 JSON；旧 UI 读取不被改成新投影，不新增内部字段兼容层；确定缺少新端点时明确报能力不可用，角色错误与普通上游故障不误报能力缺失，search 不受此依赖。
21. Persona 快照覆盖 private 已驻留、未驻留、保护和抑制条目，保留来源／保护／抑制；未知 entity／source 字符串可返回，缺失 ID 不伪造，不混入归档、待确认修正、证据或版本历史。
22. 本体响应前逐级隔离：scoped 分类、损坏前缀、混入普通分类的 scoped 条目和部分 subject 字段均不能泄漏；并发更新／删除下最小字段提取保持一致，不暴露可变缓存，不复制 embedding／版本历史或原地规范化。
23. Persona 读取不额外标记提及、更新冷却、改变证据、触发 LLM 或消费整轮同步；正常初始化维护有明确边界，不以当前快照宣称后台工作已追平或内容都可主动使用。
24. Persona 不做相关排名或渲染裁剪，正常空快照、缺少能力、未就绪、协议错误与超限可区分；测大快照、冷加载、锁内提取及共享下行负载，超限不静默返回部分数据，大 RPC 结果不挤占 STOP 等小消息所需余量。

## 现状依据

以下链接固定到分析基线。当前支持能力仍以代码与 [Plugin SDK reference](/plugins/sdk-reference) 为准，本计划不表示新门面已经可用。

- [旧 memory bus](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/plugin/core/bus/memory.py)、[缓存查询 service](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/plugin/server/application/messages/context_query_service.py) 和 [旧记忆查询 service](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/plugin/server/application/messages/memory_query_service.py)：旧路径与定位。
- [Memory HTTP 接口](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/app/memory_server/routes.py)：真实检索、subject 请求、persona、历史和渲染契约。
- [混合检索实现](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/memory/hybrid_recall.py)：检索池、scope 过滤、时间与返回字段。
- [自动回合召回](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/memory/turn_context.py)：按角色替代请求与预算。
- [主体与范围](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/memory/scopes.py) 和 [已有 scoped client](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/memory/scoped_client.py)：隔离与 wire 规则。
- [PersonaManager](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/memory/persona/manager.py)、[近期历史](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/memory/recent.py) 和 [Memory runtime](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/app/memory_server/runtime.py)：数据视图、维护和运行实例所有权。
- [EventLog](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/memory/event_log.py)：内部审计、先事件后视图、重放和压缩语义。
- [检索测试](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/tests/unit/test_hybrid_recall.py)、[回合记忆测试](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/tests/unit/test_memory_turn_context.py)、[近期历史接口测试](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/tests/unit/test_memory_server_recent_history_endpoint.py) 和 [scoped wire 测试](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/tests/unit/test_scoped_client_wire.py)：现有行为的回归依据。
- [SDK 延迟导出](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/plugin/sdk/shared/__init__.py)、[Context 能力包装](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/plugin/sdk/shared/core/context.py) 和 [打包测试](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/tests/unit/test_plugin_sdk_packaging.py)：独立叶子模块、兼容接入与导出组织。
- [控制 RPC](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/plugin/core/rpc.py)、[循环内 HTTP 池](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/utils/http/internal_client.py) 和 [plugin HTTP 生命周期](https://github.com/Project-N-E-K-O/N.E.K.O/blob/560f5e25d11f82f67feab03748b072ceb5cbb549/plugin/server/http_app.py)：请求生命周期、连接复用及独立／嵌入模式关闭的复用点。
