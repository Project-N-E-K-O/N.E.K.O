# PR #2951 公共知识边界收敛设计

> 状态：持续修复记录。第一至第二十九轮均已实施；第三十轮的设计与本地实现提交已完成，远端关闭状态以 PR head、CI 和完整分页后的 review threads 为准。第三轮方案基于提交 `2381e79b8` 的全部未解决线程（含 outdated）和 review body 中的 outside-diff 评论整理，并由 `7b972d227` 至 `f4a9aaf31` 的五个提交完成；第四轮及其 review-body 补充由 `d33a80b25` 至 `6e4a3e131` 的六个实现提交完成；第五轮及其后续补充由 `43c138ce4` 至 `079375f14` 的八个实现提交完成；第六轮由 `f2b350d0d` 至 `e6202a280` 的四个实现提交完成；第七轮由 `b5050222c` 与 `aef63512d` 两个实现提交完成；第八轮由 `bcbabbf29` 至 `b7c350c27` 的六个实现提交完成；第九轮由 `b663f327a` 至 `f67093f4a` 的四个实现提交完成；第十轮由 `182639596` 至 `db432daeb` 的七个实现提交完成；第十一轮由 `2d10e7d89`、`324ea2493` 与 `decb1d9a2` 三个实现提交完成；第十二轮由 `0e033249f` 完成；第十三轮由 `9bc071d2f` 与 `8e877fd56` 两个实现提交完成；第十四轮由 `359a2532e`、`6eb28d494`、`4cda7b874` 与 `89b8a30d3` 四个实现提交完成；第十五轮由 `ab899a225` 完成；第十六轮由 `8612faa50` 完成；第十七轮由 `ea79d433f` 完成；第十八轮由 `03f7c5167` 完成；第十九轮由 `f11367626` 完成；第二十至第二十九轮的提交与回归证据记录在各轮实施证据章节。评论数量是对应审查轮次的历史快照，不代表当前未解决线程数量；代码、测试和 CI 是最终事实来源。

## 目标与非目标

第一轮不逐条堆叠补丁，而是把评论收敛为 11 个拥有明确责任边界的修复单元。目标是：

- 已提交的数据不能被后续辅助步骤改写成失败；
- 异步路由不直接执行可能阻塞的磁盘或 SQLite 操作；
- 后台任务、请求体、缓存和轮询都有固定上限及可观察终态；
- 检索过滤、模型预热和配置错误采用一致、可恢复的降级语义；
- 管理界面区分输入错误、服务错误、超时等待和真实任务失败；
- 维护脚本和请求生命周期显式释放资源。

非目标：改变知识包五字段 Schema、扩大当前容量预算、引入新的远端服务、改变 Memory Server 边界，或为修复评论重新设计整个公共知识 API。

## 全局不变量

1. `packs.json` 与在线 `knowledge.db` 中已激活来源共同表示安装事实；`state.json` 是可恢复作业日志，不得反向否定已经完成的安装。
2. 事件循环只负责协调。可能等待文件锁、SQLite busy timeout、迁移或大量反序列化的工作必须进入工作线程。
3. 所有 fire-and-forget 任务必须有强引用、并发上限、去重键、终态记录和清理路径。
4. 任意入站正文都必须在累积到内存前执行字节限制；`Content-Length` 只用于快速拒绝，不能替代流式计数。
5. 自动检索失败时可以降级为无知识或 BM25，但不能中断正常会话。
6. 超时表示“客户端停止自动等待”，不等同于“服务端任务失败”。
7. 兼容性版本覆盖确定性分块算法；改变 chunk 序列时必须同步处理预构建索引契约。

## 修复单元 A：知识包激活提交点

涉及：`knowledge/pack_jobs.py` 的激活状态分裂评论。

### 边界

`install_pack()` 成功返回是不可逆提交点。此前的异常可以把作业置为 `failed`；此后的路由刷新、状态日志和清理属于收尾步骤，不得再把作业写成 `failed`。

### 设计

- `_activate_job()` 返回结构化结果，至少包含 `committed`、`state`、`retrieval_mode` 和可选 `warning`。
- 提交前再次检查取消状态；提交开始后不再接受取消。
- `install_pack()` 成功后先尝试原子写入 `state=active`，再执行路由刷新和 payload 清理。
- 路由刷新失败只记录警告。后续查询可通过已有数据库变更通知或下一次刷新恢复。
- 若 active 状态写入失败，保留 staging payload，返回 `committed=True`；外层不得覆盖为 failed。下一轮通过注册表中的 `pack_id`、来源和订阅版本核对安装事实并补写 active 状态。
- 只有 `committed=False` 的异常路径可以写 `failed` 并清理正文 payload。

### 验收

- 注入 `refresh_routing_index()` 异常后，在线包存在，作业不是 failed。
- 注入 active 状态写入异常后，在线包存在、payload 保留；恢复轮次可收敛到 active。
- `install_pack()` 本身失败时仍保持旧在线版本并进入 failed。

## 修复单元 B：异步协调与阻塞 I/O

涉及：Router 初始化、pack job SQLite 查询两条评论。

### 边界

- 新增 `_service_async()`，使用 `asyncio.to_thread(_service)` 完成可能触发迁移的服务构造。
- 所有异步公共知识路由先 `await _service_async()`，再把服务方法放入 `to_thread`；禁止在 `to_thread(_service().method)` 的参数求值阶段同步构造服务。
- `process_pack_jobs()` 把 `chunk_status()`、被替换 ready chunk 统计等同步读取合并到一个工作线程 helper，确保同一轮容量决策来自一个同步快照。
- 文档中“异步协调路径不阻塞事件循环”的描述以该规则为准。

### 验收

- 用线程标识断言服务初始化、staging 状态读取和在线状态读取均不在事件循环线程。
- SQLite busy timeout 不阻塞一个并行的事件循环 tick。

## 修复单元 C：订阅任务所有权、去重与容量

涉及：`knowledge_market.py` 的任务强引用和无限并发两条评论。

### 边界

- `_task_workers: dict[str, asyncio.Task]` 保存强引用；任务终态仍保留在 `_tasks`，按既有 TTL 清理。
- 同一 `package_id` 同时最多一个任务。相同版本/频道的重试返回既有 `task_id`；不同版本与正在执行的同包任务冲突时返回 409。
- 全局最多 4 个活动订阅任务。达到上限返回 429，并携带稳定错误码 `knowledge_subscription_busy`；不建立隐藏排队队列。
- done callback 只移除仍指向自身的 worker，主动读取任务异常，避免“Task exception was never retrieved”。
- TTL 清理不得删除仍有活动 worker 的任务记录；服务关闭时取消并等待全部 worker。

### 验收

- 任务在 GC 后仍运行；完成后 worker 引用被移除、状态记录保留。
- 同包重复提交只有一次下载；不同版本冲突；第五个并发任务被拒绝。
- 一个任务失败不影响其他任务，也不会遗留未读取异常。

## 修复单元 D：Bridge 请求体预算

涉及：`market_bridge.py` 无界 `request.body()` 评论。

### 边界

- Bridge 不调用 `request.body()`。先检查合法 `Content-Length`，随后按 64 KiB 流式读取并累计。
- `packs/import` 上限为 `MAX_PACK_BYTES + 64 KiB`。
- `subscriptions/apply` 上限为正文、manifest、vectors 三项既有上限之和，再加 256 KiB multipart 开销。
- 其余 JSON mutation 统一上限 64 KiB。
- 超限在 Bridge 返回 413，稳定错误码 `knowledge_request_too_large`，且不向 Main Server 发起请求。
- 当前容量下可以在通过上限检查后一次性转发 bytes；关键安全边界是“先限流、后聚合”，无需引入不可重放的双向流。
- 通用 `/market/knowledge/{path}` 是本机管理面，只允许 loopback client、loopback Host 及同端口本地 Origin。远端 Market 即使通过一次性码换得共享 bridge token，也只能使用独立注册、明确允许的订阅路由，不能进入管理 catch-all。
- Main Server 的 `/api/public-knowledge/subscriptions/apply` 在 FastAPI multipart 解析之前执行精确路径 ASGI 守门：先拒绝超限 `Content-Length`，再把实际请求流写入至多 1 MiB 内存后转磁盘的 bounded spool；只有实际总量不超限才向下游重放。路由内的单制品限制继续作为第二层校验。

### 验收

- 有、无、伪造 `Content-Length` 三种情况下都不能超过上限。
- 超限请求不调用下游 client；边界值请求原样转发。
- 伪造较小或省略 `Content-Length` 的 multipart 仍按实际字节返回 413，且 FastAPI 不开始解析文件。
- 远端 Origin 携带有效配对 token 调用 `packs/remove` 等管理路径仍返回 403；本机同源管理调用和专用远端订阅调用保持可用。

## 修复单元 E：检索过滤正确性

涉及：禁用条目在候选截断后过滤的评论。

### 边界

- 在发出 FTS/LIKE 查询或执行向量 top-K 前读取 disabled 集合。
- 词法检索可按禁用条目数扩大候选窗口，因为一条词法候选对应一条 entry；向量检索不能使用这一近似，因为一个 entry 可拥有多个 chunk。向量路径必须先把 `(source_tag, title)` 映射为 entry rowid，从 eligible chunk mask 中排除全部禁用 rowid，再执行 `argpartition`。
- 排序、合并和最终 `limit` 仍只作用于启用条目；`include_disabled=True` 保持原行为和原预算。
- override 文件无效继续抛出领域错误，由管理 API 显示可诊断状态；自动会话路径按既有安全降级返回空结果。

### 验收

- 前 12 个强匹配均禁用、第 13 个启用时仍能返回第 13 个。
- 单个禁用 entry 拥有 65 个高分 chunk、启用 entry 位于原 top-64 之外时，仍返回启用 entry。
- `include_disabled=True` 可返回禁用项；来源过滤和 FTS/LIKE 去重不回归。

## 修复单元 F：注册表元数据与统计查询分层

涉及：自动会话每包查询、legacy migration 重复全表扫描两条评论。

### 边界

- `list_installed_packs()` 保留面向管理 UI 的富统计语义。
- 新增只读注册表快路径，只返回已校验的 `source_tag`、`auto_context` 和有效 material type，不打开 SQLite。自动会话只调用该快路径。
- KnowledgeStore 新增按来源一次聚合 entries/chunks/ready 的查询；legacy migration 每个数据库最多执行一次聚合，禁止每个 pack 调用 `list_active_entries()` 或 `source_chunk_status()`。
- 注册表损坏时快路径返回空社区来源，内置来源仍可用。

### 验收

- 自动会话来源选择不构造 KnowledgeStore、不执行逐包状态查询。
- 100 个 pack 的迁移统计仍只有常数次数据库查询，并保持原统计值。

## 修复单元 G：向量模型预热与超限负缓存

涉及：预构建索引不预热模型、超限 snapshot 反复加载两条评论。

### 边界

- `index_embedding_batch(load_model=True)` 的 `load_model` 同时表示“确保查询模型可用”。因此模型请求必须发生在 `no_work` 提前返回之前；加载必须经 `_KnowledgeInferenceCoordinator.ensure_loaded()` 串行化，并保留 `inference_busy`、`model_load_timeout`、`embedding_unavailable` 等稳定状态。`load_model=False` 绝不隐式加载模型。
- 当前模型发生变化时，`local` 与 `prebuilt_only` 两种策略下的旧模型 ready vectors 必须在同一 store transaction 中标为 stale；不兼容且不可搜索的 prebuilt cache 不得继续占用全局 ready-vector 预算。
- snapshot 缓存支持 `ready` 与 `rejected` 两种记录。拒绝记录至少绑定数据库 identity、chunk revision、模型 ID 和拒绝原因。
- 命中 rejected 记录时立即抛出相同的 `MemoryError`，不再加载向量行；数据库 identity/revision 或模型变化后自然失效并重试。
- 缓存只保存原因和键，不保存超限矩阵。

### 验收

- 只有预构建 ready chunks、没有本地 embedding work 时，`load_model=True` 仍请求一次模型；false 时不请求。
- 模型加载与 query/background inference 互斥；协调器繁忙或超时不会启动第二个 native inference。
- 旧模型的 local/prebuilt ready chunks 都从 ready 预算释放，当前模型向量不受影响。
- 同一 revision 的超限 snapshot 只执行一次大读取；revision 变化后重新评估。

## 修复单元 H：配置文件、数据库通知与安全降级

涉及：非法 UTF-8、硬编码数据库名、未捕获 `sqlite3.Error` 三条评论。

### 边界

- `load_disabled_entries()` 将 `UnicodeDecodeError` 与 JSON 语法错误统一转换为 `CatalogOverrideError`，不泄露原始文件内容。
- `catalog_overrides.set_entry_disabled()` 只负责 override 原子写入，不推断数据库路径、不发缓存通知。
- `KnowledgeService.set_entry_disabled()` 持有真实 database path，并负责一次 `notify_database_changed()` 与已实例化 routing state 刷新。
- `_safe_load_records()` 额外捕获 `sqlite3.Error`；只在自动路由快路径吞掉并返回空快照，管理与诊断路径仍保留错误可见性。
- `KnowledgeRetriever.search(include_disabled=False)` 捕获 `CatalogOverrideError` 并返回空候选，保证自动会话失败关闭；显式管理读取和 status 仍显示 `catalog_override_invalid`。`include_disabled=True` 不依赖 override，可用于诊断和恢复。

### 验收

- 非 UTF-8 override 返回 `catalog_override_invalid` 而非 500。
- 自定义数据库文件名只失效对应数据库缓存；通知恰好一次。
- 锁定或损坏数据库不阻断普通对话路由。

## 修复单元 I：新安装健康语义

涉及：数据库缺失即 degraded 的评论。

### 边界

- `database_exists=False`、override 不损坏且没有迁移失败证据时，定义为合法空状态：`integrity_ok=True`、entries/chunks 为 0、检索模式 BM25。
- “missing” 是观测字段，不是错误。`packs.json` 同样使用 `missing | ready | invalid` 三态：缺失表示尚未安装社区包，合法健康；存在但无法读取、JSON/结构/版本非法则必须 degraded，不能由 `list_installed_packs()` 的空列表降级掩盖。
- 只有数据库存在但 integrity check 失败、override 无效、pack registry 无效或迁移失败才 degraded。
- 状态读取不得为了证明健康而创建空数据库。

### 验收

- 全新目录 `/status` 返回 ready/available、0 entries，且磁盘上仍不产生数据库。
- 损坏数据库和损坏 override 仍返回 degraded。
- 损坏 `packs.json` 时 `pack_registry_state=invalid`、`integrity_ok=False`；缺失时为 `missing` 且保持健康。

## 修复单元 J：前端请求与轮询状态机

涉及：详情错误、导入错误混淆、轮询竞争、十分钟静默停止四条评论。

### 边界

- `openEntry()` 捕获 API 错误，仅在组件仍存活时显示 `loadFailed`，并保持原 drawer 状态。
- 导入分两阶段：文件读取/JSON 解析失败显示 `invalidPack`；API、网络或服务端拒绝显示 `operationFailed`。
- pending job 从 `Set` 改为以 job ID 为键、记录各自开始时间的 Map；新任务不会继承旧任务的十分钟预算。
- 任意时刻只有一个 in-flight poll。timer 只负责唤醒，poll 的 `finally` 统一安排下一次；并发调用复用当前 promise 或直接返回。
- 十分钟只停止该 job 的自动轮询，显示一次 `importStillProcessing`，不显示失败。后端 job 记录保留，刷新页面/概览仍能看到真实状态。
- 组件卸载清理 timer 和本地跟踪状态，不取消服务端任务。
- entry detail 使用独立 latest-request gate；只有最新请求可更新 drawer 或显示错误，组件卸载会使在途请求失效。
- overview refresh 链只允许“当前 promise 自己”在 `finally` 中清空全局引用，旧链结束不得把已排队的新链误标为空闲。
- job 截止时间检查位于 `packJobs()` 成功/失败分支之外；持续网络失败只影响退避次数，不能绕过十分钟本地等待上限。

### 验收

- 请求未完成时添加第二个 job 不会产生第二条轮询链。
- 两个 job 有独立截止时间；超时只提示一次，晚到的 active 仍可通过刷新观察。
- JSON 错误、HTTP 错误、active、failed、cancelled 和超时使用不同语义。
- 先点 A 再点 B 且 A 后返回时，drawer 保持 B；A 的晚到错误也不弹 toast。
- A refresh 结束时 B 已排队，则 C 仍排在 B 后；不能与 B 并行。
- `packJobs()` 持续拒绝时，job 到期后删除本地跟踪、只提示一次 `importStillProcessing`，且不再调度轮询。

## 修复单元 K：确定性分块与资源生命周期

涉及：短尾分块、route owner 清理、SQLite 连接关闭、异步测试未让出事件循环四条评论。

### 分块边界

长无断句正文采用均衡滑窗：先计算满足 `MAX_CHARS` 的最少窗口数，再在窗口间保持不超过 `OVERLAP_CHARS` 的重叠，使末窗不会只携带极少新字符。必须满足全文无丢失、顺序不变、窗口不超长、chunk 数不增加。

该算法会改变 chunk ID。由于 protocol v1 尚在本 PR 中首发，修复在合并前纳入 v1 基线，并同步更新确定性测试与预构建制品测试；若发现仓库外已经发布 v1 制品，则停止实施此项并改为提升 `CHUNKER_VERSION`，不能静默破坏已发布制品。

### 生命周期边界

- 新增请求级 route-owner 清理 helper；正常 turn end 消费 owner，终止且不重试的 discard 路径显式丢弃 owner。重试路径保留同一 request owner。
- 只读 SQLite helper 改为 `contextmanager`，在 `finally` 调用 `connection.close()`；调用方继续使用 `with`，但语义变为真正关闭。
- 调度后台任务的测试在断言前至少 `await asyncio.sleep(0)`，保证被测任务获得一次运行机会。

### 验收

- 1,201 字符无断句文本不会生成“1 个新字符 + 120 重叠字符”的尾块，且逐字符可重建原文。
- terminal discard 后 owner map 无残留；retry 时仍保留，正常结束只消费一次。
- 脚本正常返回和异常退出均关闭连接。
- 测试在后台 planner 真被调度后再断言。

## 第一轮实施顺序与提交边界

1. 文档提交：本文及设计索引。
2. 一致性与安全提交：A、C、D。
3. 事件循环与检索提交：B、E、F。
4. 索引与配置提交：G、H、I。
5. 前端与生命周期提交：J、K。
6. 回归修正提交：仅包含测试发现的必要修正。

第一轮已由 `5f4c87fb9`、`4493d4db5`、`9868b2243`、`7a23b65ef`、`d9b9c02ae` 实施。每个实现提交均由对应定向测试说明；只有代码和测试已经覆盖的审查线程才可解决。

## 第一轮评论覆盖矩阵

| 修复单元 | 评论主题数 | 覆盖主题 |
| --- | ---: | --- |
| A | 1 | 激活后状态分裂 |
| B | 2 | Router 初始化阻塞、pack job SQLite 阻塞 |
| C | 2 | Task 强引用、订阅无限并发 |
| D | 1 | Bridge 无界请求体 |
| E | 1 | 禁用项截断有效候选 |
| F | 2 | 自动会话逐包查询、迁移重复全表扫描 |
| G | 2 | 预构建模型不预热、超限 snapshot 重复加载 |
| H | 3 | 非 UTF-8、错误数据库通知、SQLite 路由降级 |
| I | 1 | 空安装误报 degraded |
| J | 4 | 详情错误、导入错误、轮询竞争、轮询超时 |
| K | 4 | 短尾分块、owner 泄漏、连接关闭、异步测试时序 |
| 合计 | 23 | 第一轮审查时的全部未解决评论（历史快照） |

## 第二轮复审：13 项边界补充

第二轮包含 10 个 GitHub 行级未解决线程和 CodeRabbit review body 中 3 个 outside-diff-range 建议。Outside 类型不是 GitHub review thread，不能单独标记 resolved，但其技术内容仍按同一标准验证和实施。Greptile 的文件数量限制、CodeRabbit walkthrough/risk/Autofix 摘要不计为独立代码问题。

### 优先级与实际严重度

| # | 优先级 | 问题 | 复核结论 | 解决边界 |
| ---: | :---: | --- | --- | --- |
| 1 | P2 | 旧模型 prebuilt ready vectors 继续占预算 | 成立 | `local` 与 `prebuilt_only` 的不兼容模型向量同事务 stale |
| 2 | P2 | 禁用项在 semantic top-K 后过滤 | 成立 | disabled key 先映射 rowid，再从 chunk eligible mask 排除 |
| 3 | P2 | 非法 `packs.json` 被显示成空安装且健康 | 成立 | registry 明确 `missing/ready/invalid`，invalid 参与 integrity |
| 4 | P1 | 远端 Market 可借共享 token 进入管理 catch-all | 成立，影响最高 | 通用管理 bridge 强制本地同源，远端只保留专用订阅路由 |
| 5 | P2 | 删除已安装包后 staged replacement 可再次激活 | 成立 | pack-specific lock 线性化 stage/activate/remove，remove 同锁取消非终态 job |
| 6 | P2 | pack 清洗、分块预检和索引校验阻塞事件循环 | 成立 | canonicalize/parse/validate/preflight/prebuilt validation 整体 `to_thread` |
| 7 | P3 | entry detail 晚到响应覆盖新选择 | 成立，原评论 P2 偏高 | 独立 latest-request gate 同时保护成功与错误分支 |
| 8 | P3 | 设计文档仍写“实施中/当前 23 条未解决” | 成立 | 标为已实施，评论数量改为历史轮次快照 |
| 9 | P3 | `packJobs()` 持续失败时 job 永不超时 | 成立，原 Major 偏高 | 截止检查移到请求成功/失败之外 |
| 10 | P2 | 非法 catalog override 可中断自动检索 | 成立 | 自动 search 返回空候选，显式管理诊断保留错误 |
| 11 | P2 | 后台预热绕过 inference coordinator（outside） | 成立；评论所写文件名有误，实际在 `vector_index.py` | 经 `ensure_loaded` 串行并传播 busy/timeout/unavailable |
| 12 | P3 | 旧 overview promise 的 finally 清空新链（outside） | 成立 | promise identity guard，仅当前链可清引用 |
| 13 | P2 | Main Server multipart 在 parser 前无总量上限（outside） | 成立 | 精确路径 ASGI 预读、实际字节计数、bounded spool、验证后重放 |

这 13 项技术核心均成立，没有应当忽略的误报；但第 7、9 项属于局部 UI 生命周期，不应上调为会破坏数据或安全边界的高优先级。第 11 项建议的目标正确，但原评论把实现位置误写成 `knowledge/pack_jobs.py`。

### 并发与提交点设计

同一 `pack_id` 的三个写动作共享一个由 pack ID 哈希得到的进程内 RLock：

1. `stage_pack` 在锁内复核 pending/capacity 并原子创建 job；
2. `_activate_job` 在锁内再次读取 state，若已 cancelled 则不提交，否则以 `install_pack()` 为提交点；
3. `remove_pack` 在同一锁内先取消该 pack 全部非终态 job，再删除在线来源。

因此并发顺序只有两种合法结果：remove 先线性化时，旧 job 被取消且不能复活；activate 先线性化时，remove 随后删除刚提交的版本。无论调度顺序如何，remove 返回后都不会被调用前已存在的 staged replacement 重新安装。state-file lock 仍只保护单 job journal，registry lock 仍只保护 registry/database 原子更新；各锁职责不混用。

### 请求与资源预算设计

订阅总量上限是 `MAX_PACK_BYTES + MAX_PREBUILT_MANIFEST_BYTES + MAX_PREBUILT_VECTOR_BYTES + 256 KiB` multipart 开销。ASGI 守门针对精确路径工作：

1. 声明长度超限时不读取正文，直接 413；
2. 声明长度缺失、非法或偏小时，逐 ASGI message 累加实际字节；
3. 前 1 MiB 保存在内存，之后转临时文件，累计一旦超限即关闭 spool 并 413；
4. 通过后按 64 KiB 重放给 FastAPI parser，最终由各 `UploadFile` 的单制品限制再次校验。

这一区分“总 envelope 预算”和“单 artifact 预算”，既阻止 parser 前无界临时磁盘写入，也不把合法的大向量文件全部缓存在内存。

### 失败语义

- `pack_registry_state=missing` 和 `database_exists=False` 是合法空安装；`invalid` 才降级。
- 自动检索无法信任 override 时返回空，不猜测哪些条目可用；管理 status/写接口继续暴露可修复错误。
- inference coordinator 的 `inference_busy`、`model_load_timeout`、`embedding_unavailable` 原样成为 batch state；成功预热后若无索引工作仍返回 `no_work`。
- UI 十分钟到期表示停止自动等待，不把服务端 job 改成 failed；网络错误使用指数退避，但不能延长每个 job 的独立 deadline。

### 第二轮实现提交与验收

| 提交 | 覆盖项 | 关键验收 |
| --- | --- | --- |
| `62e029dd0` | #4、#13 | 远端管理调用 403；声明/实际超限 413；合法 multipart 字节级重放一致 |
| `28b10adc0` | #1、#2、#10、#11 | local/prebuilt 同步 stale；disabled 65 chunks 不挤出启用项；invalid override 自动返回空；预热经 coordinator |
| `9d4bca59b` | #3、#5、#6 | registry invalid 降级；remove 后 replacement 不激活；pack/prebuilt 校验在线程池执行 |
| `a5d1def7d` | #7、#9、#12 | late detail/旧 finally 不再改写新状态；API 连续失败仍按 deadline 停止 |
| 文档提交 | #8 | 本文和设计索引只把已实施内容列入 implemented records |

第二轮定向 Python 测试必须使用 `uv run pytest`；前端至少通过 `vue-tsc --build` 与 i18n 完整性检查。最终回归通过后，10 个行级线程可以逐项 resolved；3 个 outside 建议只能通过代码更新与 review 回复说明已处理。

## 第三轮复审：剩余边界的实施设计

本轮以 `2381e79b8` 为代码快照，复核 GitHub 上全部 unresolved 行级线程（包括已经 outdated 但仍未手动解决的线程）及 review body 中的 outside-diff 评论。按“当前代码是否已经覆盖评论所述失败路径”重新归并后，结果为：

- 6 项原问题已经由当前代码解决；
- 1 项只完成了外围耗时校验的线程化，JSON 解码仍在事件循环中，属于半修复；
- 7 项问题在当前代码中仍成立；
- 另有 2 个不对应新 review thread、但会阻断 PR 合并的 Windows CI 回归。

这里的“已解决”只表示原评论描述的准确失败路径已经消失，不表示相邻实现没有继续优化空间。第三轮实现不得为了顺手优化而扩大 PR；下面明确列为“后续增强”的内容不作为本轮线程关闭条件。

### 复核分类

| 类别 | 问题 | 当前结论 | 第三轮动作 |
| --- | --- | --- | --- |
| 已解决 | 不兼容的 `prebuilt_only` 向量继续占 ready 预算 | 代码已同时 stale `local` 与 `prebuilt_only` | 增加精确策略回归测试后关闭线程 |
| 已解决 | disabled chunk 在 semantic top-K 后才过滤 | eligible mask 已在截断前排除 disabled rowid | 保留现有 65-chunk 测试；条目多样性另列后续增强 |
| 已解决 | 非法 `packs.json` 被当作健康空安装 | status 已暴露 `pack_registry_state=invalid` 并降级 integrity | 关闭线程 |
| 已解决 | 远端 Origin 可进入本地管理 bridge | catch-all 已先执行本地同源校验 | 关闭线程 |
| 已解决 | remove 返回后 staged replacement 再激活 | stage、activate、remove 已共享 pack lock | 增加真实并发时序测试后关闭线程 |
| 已解决 | entry detail 晚响应覆盖新选择 | latest-request gate 已同时保护成功、失败及卸载 | 关闭线程 |
| 半修复 | 本地 pack 导入在事件循环执行重 CPU 工作 | pack/prebuilt 校验已进线程；最大约 10 MiB JSON 仍同步解码 | 修复单元 L |
| 仍成立 | path-specific 413 文案误称“全局上限” | 稳定错误码和上限值正确，仅文案错误 | 修复单元 M |
| 仍成立 | unsubscribe 信任调用方 `pack_id` | 可误删本地导入包或其他订阅包 | 修复单元 N |
| 仍成立 | unsubscribe 不取消活动订阅 worker | 下载、安装和删除可并发交错 | 修复单元 O |
| 仍成立 | 损坏的 staged `state.json` 被静默忽略 | 作业、容量和同包冲突均会消失 | 修复单元 P |
| 仍成立 | takeover completion 泄漏 route owner | takeover 分支未消费 `_text_route_owners` | 修复单元 Q |
| 仍成立 | 未来数据库 Schema 被当前版本原地降级 | 版本检查发生在 DDL/修复写入之后，且最终覆盖为 7 | 修复单元 R |
| 仍成立 | 拉丁词直接匹配没有词边界 | `java` 会命中 `javascript` | 修复单元 S |
| CI 阻断 | 3 个 unit tests 构造了不完整 manager | 测试夹具缺少生产构造器已初始化的 owner map | 修复单元 T |
| CI 阻断 | Study Companion 纯注册导入加载 NumPy | route 聚合导入把 `knowledge.vector_index` 带入进程 | 修复单元 T |

### 第三轮新增不变量

8. 删除知识包的授权身份必须来自本地持久化订阅元数据或可信 Market descriptor；请求中的 `pack_id` 只能做一致性校验，不能作为删除授权。
9. 持久化作业一旦创建目录，就必须处于“可解析”或“显式隔离”状态；损坏、缺失或暂时不可读的状态不得从列表、容量和同包互斥中消失。
10. 高于当前 `SCHEMA_VERSION` 的数据库必须在任何 DDL、DML、journal mode 切换或修复动作之前被拒绝，旧程序不得尝试解释或降级新格式。
11. 取消内存 worker 后必须等待其真正结束，并把任务写成可观察的终态；取消期间不得允许同一 `package_id` 启动新订阅。
12. 拉丁词的自动直接匹配按拉丁字母/数字边界判断；CJK 相邻字符不是拉丁边界阻挡，因此 `Java开发` 可以命中 `Java`，`JavaScript` 不可以。

## 修复单元 L：有界 JSON 的事件循环边界

涉及：`main_routers/public_knowledge_router.py::_bounded_json_payload()` 的半修复评论。

### 边界

流式读取和字节累计继续由事件循环协调；UTF-8 解码、`json.loads()` 和根对象判定合并为纯同步 helper `_decode_json_object(raw: bytes | bytearray) -> dict`，统一通过 `await asyncio.to_thread(...)` 执行。把已累计的 `bytearray` 原样传给 `to_thread`，不能在参数求值时先调用 `bytes(raw)`，否则大对象复制仍发生在事件循环。helper 不访问 request、service 或全局状态。

返回契约保持 `(payload, too_large)`：只有实际或声明体积超限时 `too_large=True`；非法 UTF-8、非法 JSON 或非对象根仍返回 `({}, False)`，不借这次修复改变现有 API 错误语义。

### 验收

- 在线程标识测试中，`_decode_json_object` 不运行在事件循环线程；
- 超限输入在进入 decoder 前返回，不能分配第二份 JSON 字符串；
- 合法对象、数组根、非法 UTF-8、非法 JSON 和恰好等于上限的输入保持原响应；
- 解码接近上限的 JSON 时，一个并行 event-loop tick 能继续运行。

## 修复单元 M：请求体上限的稳定错误语义

涉及：`utils/asgi_body_limit.py::_reject()` 的 path-specific 文案评论。

### 边界

同一个 `_reject()` 同时服务全局上限和精确路径上限，错误文本不得声称是哪一类配置触发。统一改为“请求体超过允许的体积上限。”；`payload_too_large`、`knowledge_request_too_large` 和响应中的实际 `max_bytes` 保持不变。

这是后端机器可读错误契约，不新增前端文案，也不引入 i18n key。前端应继续依据 `error_code` 映射本地化提示，不能解析中文 `error`。

### 验收

- 全局 JSON 上限返回 `payload_too_large`、全局 `max_bytes` 和中性文案；
- 订阅 multipart 精确路径返回 `knowledge_request_too_large`、路径专用 `max_bytes` 和同一中性文案；
- 声明长度超限与实际流式累计超限的 payload 完全一致。

## 修复单元 N：订阅删除的可信所有权

涉及：`plugin/server/routes/knowledge_market.py::unsubscribe_knowledge_package()` 信任调用方 `pack_id` 的 P1 评论。

### 身份模型

为订阅元数据增加 `provider_package_id`。由于现有订阅 hand-off 是字符串字典，该值使用无前导零的正十进制字符串；新安装的 `plugin-market` 订阅必须写入该字段。`provider`、`provider_package_id` 和 `remote_id` 共同构成不可变提供方身份，`version` 与 `channel` 是可更新版本信息。

兼容规则如下：

- `validate_subscription()` 接受旧记录缺少 `provider_package_id`，但 Plugin Market 发起的新 apply 必须提供；
- 已有非空 `provider_package_id` 不得在 replacement 中改变；
- 旧记录从空值升级到非空值，只能发生在当前请求已经通过可信 descriptor 校验时；
- 本地导入包没有 subscription 元数据，永远不具备 Market unsubscribe 资格。

`provider_package_id` 是现有 subscription 子对象的向后兼容可选字段，`PACK_REGISTRY_SCHEMA_VERSION` 保持 4，`SUBSCRIPTION_PROTOCOL_VERSION` 保持 1：旧 registry 可以缺少它，新 Main Server 能读取旧记录；但新 Plugin Market apply 必须携带它。读取旧 registry 时不原地重写，等可信 replacement 时再持久化，避免一次列表操作产生写入。

### 所有权解析

新增 `_resolve_owned_subscription(package_id, claimed_pack_id)`，返回服务端确认的 `pack_id`，不直接删除：

1. 从 Main Server 的已安装 pack 列表中筛选 `subscription.provider == "plugin-market"` 且 `provider_package_id` 等于请求 `package_id` 的记录；
2. 找到唯一记录后，以该记录的真实 pack ID 为删除目标；调用方 `claimed_pack_id` 不一致时返回 `subscription_identity_mismatch`，绝不改用调用方值；
3. 没有新字段匹配时，只允许检查 caller 指向的旧订阅候选。候选必须是 `plugin-market` 订阅，并具有 version、channel 和 remote_id；随后按请求 `package_id` 获取可信 descriptor，且 descriptor 的 `pack_id`、`remote_id`、version、channel 全部一致才授权；
4. 旧记录无法联网验证、descriptor 不一致或元数据不足时返回 `subscription_ownership_unverifiable`，失败关闭；
5. 没有对应订阅时返回 `subscription_not_found`。本地包即使恰好同名也走该分支。

Main Server 删除接口只接收解析后的真实 pack ID。Market 上报使用原 `package_id`，但上报失败仍是 best-effort，不回滚已经完成的本地删除。

### 验收

- 用某个本地导入包的 pack ID 调用 unsubscribe 不会删除它；
- `package_id=A, pack_id=B` 不能删除 B，也不能删除 A 对应包；
- 新格式订阅在离线状态下仍能凭持久化身份安全删除；
- 旧格式订阅在线且 descriptor 完全一致时可删除，离线或不一致时失败关闭；
- replacement 可为旧记录补入 provider package ID，但不能改变既有非空值；
- 重复或冲突的 provider package ID 被诊断为 registry identity error，不任选第一条删除。

## 修复单元 O：unsubscribe 与活动任务的线性化取消

涉及：unsubscribe 忽略 `_active_package_tasks` / `_task_workers` 的 P2 评论，并与修复单元 N 共用可信身份。

### 并发顺序

新增 `_unsubscribing_package_ids: set[int]`。unsubscribe 在第一次 `await` 前完成冲突检查并登记 package ID；subscribe 也必须先检查该集合，命中时返回 409 `knowledge_subscription_conflict`。单事件循环内“检查并登记”之间没有让出点，因此不需要额外 asyncio lock。

登记后的顺序固定为：

1. 读取当前活动 task/worker 快照；
2. 若 worker 存在，调用 `cancel()` 并 `await asyncio.gather(worker, return_exceptions=True)`，确认下载、校验或等待 job 的协程已经退出；
3. cancellation handler 或 done callback 把任务写成 `status=stage="cancelled"`，设置 `completed_at`、`error_code="cancelled_by_unsubscribe"`，再清理 worker 和 active-package 映射；
4. 解析可信 pack 身份。worker 已经拿到 descriptor 时，必须在下一次 await 前把 `resolved_pack_id` 与 `resolved_remote_id` 写入 task，因此取消方可以直接使用；尚未解析 descriptor 的 worker 不可能已经提交 durable job，可按修复单元 N 的旧记录/可信 descriptor 流程继续；
5. 调用 Main Server 的 cancel-and-remove 语义：同一个 pack lock 内先取消该 pack 的全部非终态 durable jobs，再删除在线包；只取消到 staged job、尚无在线安装时也返回成功。结果显式返回 `removed_pack`、`removed_entries` 与 `cancelled_jobs`；只有 `removed_pack=False` 且 `cancelled_jobs=0` 才是 not found，不能把已删除的零条目空包误判为不存在；
6. 最后执行 Market best-effort 上报，并在 `finally` 清除 `_unsubscribing_package_ids`。

`asyncio.CancelledError` 必须单独处理，不能落入当前通用 Exception 分支并把用户取消误记成 internal failure。done callback 仍负责消费非取消异常，但不得覆盖已经写入的 cancelled 终态。

### 验收

- resolving、downloading、verifying 阶段取消后 worker 结束，task 保留 cancelled 终态；
- Main Server 已创建 durable job、Plugin worker 正在等待时，unsubscribe 能取消 job，且该包之后不会激活；
- 已激活后 unsubscribe 删除在线包；
- unsubscribe 登记后、删除返回前，同 package subscribe 始终返回 409；
- 两个并发 unsubscribe 只有一个执行删除，另一个在 reservation 存续时稳定返回 409 conflict；
- 取消到 staged-only 状态返回成功而不是表面 not found；
- `finally` 在网络、身份解析和 Main Server 失败时都释放 reservation，允许用户重试。

## 修复单元 P：损坏作业日志的隔离与容量守恒

涉及：`knowledge/pack_jobs.py::_read_json()` 把所有读取失败折叠为空字典的 P2 评论。

### 持久化结构

新 job 不直接在最终目录中边写边公开。持有 jobs-root mutation lock 时，先创建 `.creating-<uuid>` 临时目录，原子写入不可变的 `identity.json`，再写 pack/index artifacts，最后写初始 `state.json`；全部成功后在同一文件系统内把目录原子重命名为最终 `<job_id>`。这样并发列表不会把正常创建中的 job 误判成 missing state。identity 至少包含：`job_id`、`pack_id`、`created_at`、`entries_total`、`chunks_total`、`content_bytes`。processor 不修改 identity；mutable state 只记录阶段、进度、重试和结果。

JSON 读取改为判别结果而非空字典：`valid | missing | invalid | unreadable`。对共享/杀毒软件造成的临时 `OSError` 只做小次数、短间隔同步重试；仍失败后归类 unreadable，不能假装目录不存在。

### 隔离语义

- state 无效但 identity 有效：列表暴露 `state="degraded"`、`reason="invalid_job_state"` 及 identity 预算；容量统计和同 pack 互斥继续计入；processor 跳过，不自动清理；
- state 缺失但 identity 有效：同样 degraded，reason 为 `missing_job_state`；
- identity 与 state 都无法建立可信身份：暴露 orphan 诊断，并全局拒绝新 staging，错误码 `knowledge_job_registry_invalid`。此时无法安全判断它属于哪个 pack 或占用多少容量，不能按零处理；
- 启动时发现遗留 `.creating-*` 目录也按 orphan 暴露并失败关闭；它可能来自进程崩溃，不能在不知道另一个进程是否仍写入时自动删除；
- terminal auto-cleanup 不处理 degraded/orphan。管理端提供显式 discard 动作，按经过目录穿越校验的 job ID 删除隔离目录；不在后台自动修复或删除证据；
- 本轮不实现从损坏 state 自动恢复执行。若以后增加 repair，必须重新验证 pack artifact、容量和订阅身份后生成新 state。

为兼容旧 job：没有 identity 但 state 有效时，先按 state 正常展示和处理；只有在持有 job state lock、字段完整且 artifacts 可验证时才可补写 identity。不能仅凭目录名推断 pack ID。

### 验收

- `state.json` 为截断 JSON、数组、缺失或持续 unreadable 时，job 均出现在诊断中而不是消失；
- identity 有效的 degraded job 继续占 entries/chunks/content 容量，并阻止同 pack 重复 staging；
- identity 也无效时，任意新 staging 失败关闭，不创建第二个 job 目录；
- 列表与 staging 并发时，完整 job 只在原子 rename 后出现，不产生瞬时 degraded 记录；崩溃遗留的 `.creating-*` 明确成为 orphan；
- processor 重启不会执行或自动删除隔离 job；
- 显式 discard 只能删除选定的 `.staging/<job_id>`，成功后容量与冲突解除；
- 旧格式有效 job 可继续完成，兼容补写失败不破坏原 state。

## 修复单元 Q：takeover 完成路径的 route-owner 释放

涉及：`main_logic/core/turn.py::handle_response_complete()` takeover 分支的 P2 评论。

### 边界

在 takeover 分支的第一个 `await` 之前捕获 `active_request_id`，同步消费该 request 的 `_text_route_owners`，并清空只属于旧轮的 pending meta/text。`_active_text_request_id` 使用 compare-and-clear：仅当共享字段仍等于快照时置空。随后才 `await _clear_tts_pipeline()`。

这样既不发送旧轮 `turn_end`，也不在 TTS cleanup 的让出窗口删除新请求刚登记的 owner 或 request ID。不要用 `getattr(..., {})` 掩盖测试夹具缺字段；生产构造器已经保证 owner map 存在，CI 夹具应按修复单元 T 补齐。

### 验收

- takeover completion 后旧 request owner、pending meta 和旧文本均不存在；
- 在 `_clear_tts_pipeline()` await 期间注入新 request，新 owner 与新 active ID 保留；
- takeover 路径不发 `turn_end`，普通完成路径仍只消费一次 owner；
- TTS cleanup 抛错时也不能让旧 owner 永久残留，因此共享状态清理必须发生在 await 前。

## 修复单元 R：数据库 Schema 的向前兼容拒绝

涉及：`knowledge/store.py::_initialize()` 在确认版本前执行迁移和覆盖版本的 P1 评论。

### 版本探测

新增 `KnowledgeSchemaTooNewError(KnowledgeStoreError)` 和只读 `_read_schema_markers(connection)`。连接建立后只允许设置 row factory 与 busy timeout；在下列探测完成前，不得执行 `journal_mode=WAL`、CREATE、ALTER、DELETE、修复 source tag 或 metadata UPDATE：

1. 读取 `PRAGMA user_version`；0 表示旧版本尚未使用该标记；
2. 查询 sqlite schema 判断 metadata 表是否存在；存在时读取唯一 `schema_version` 值；
3. metadata 值存在但不是规范正整数时，抛 `KnowledgeStoreError`，不猜测；
4. 任一非零标记大于 `SCHEMA_VERSION` 时抛 `KnowledgeSchemaTooNewError`；
5. 两个非零标记不一致时失败关闭，不能选择较小值继续；
6. metadata 表或 schema row 缺失、且 user_version 为 0 时视为可迁移 legacy 数据库。

探测通过后才进入现有 migration/repair，并在成功提交时同时写 `metadata.schema_version` 与 `PRAGMA user_version=SCHEMA_VERSION`。当前数据库的 metadata=7、user_version=0 是合法过渡态，会在一次成功初始化后补齐 user_version。初始化失败不写 `_INITIALIZED_DATABASES`；连接由现有 `finally` 关闭。

status/管理 API 捕获 `KnowledgeSchemaTooNewError`，返回 degraded 及稳定诊断 `knowledge_schema_too_new`，包含 `supported_schema_version`，但不把数据库内容或异常堆栈暴露给前端。读、写、status 和后台 indexer 都必须经过同一 guard，不能只保护 mutation。

### 验收

- 构造 metadata 或 user_version 为 8 的数据库，分别调用读取、写入、status 和后台索引入口，均拒绝且错误稳定；
- 拒绝前后数据库文件 identity、schema、表内容、schema_version、user_version 和 journal mode 均不变；
- 非整数/负数 metadata marker 失败关闭；两个非零 marker 不一致失败关闭；
- 只有 metadata marker 的当前 v7 数据库可正常打开并补齐 user_version；
- 无 marker 的真实 legacy fixture 能迁移，空目录的健康语义不回归；
- 失败连接不会污染 initialized cache，替换为受支持数据库后可重新初始化。

## 修复单元 S：自动直接匹配的拉丁词边界

涉及：`knowledge/service.py::_normalized_direct_text()` 删除所有分隔符后执行 substring 的 P2 评论。

### 匹配模型

保留两套规范化，不能用一个“删除所有非字母数字”的字符串同时服务所有语言：

- compact normalization：NFKC + casefold + 仅保留字母数字，用于完整查询等价和含 CJK/非拉丁 term 的既有紧凑子串匹配；
- folded surface：NFKC + casefold + 规范空白，但保留标点，用于纯拉丁/数字 term 的字面查找和边界判断。

term 仅包含拉丁字母、数字、组合音标及允许标点时，嵌入式匹配的左右邻接字符不能是拉丁字母、组合音标或十进制数字。CJK 字符、空白和标点都构成合法边界，因此 `Java开发` 与 `学习Java` 可匹配，`JavaScript`、`myjava2` 不可匹配。不要使用 Python `\b`：Unicode 正则会把汉字也视为 word character，从而错误拒绝 `Java开发`。

含 CJK 的 term 保留 compact 子串路径。混合 term 因自身已有非拉丁区分度，也走 compact 路径。`_is_short_query_embedded_in_term()` 必须复用同一拉丁边界 helper，避免 direct 分支修好后 corpus semantic 辅助分支仍把 `java` 当作 `javascript` 的一部分。

长度规则保持现状：普通嵌入式 direct term 仍至少 4 个字母数字；短 query-in-term 仍只在 corpus + semantic 前提下使用。带语义标点的技术名词（如 `node.js`）按 folded surface 精确标点匹配；`C++`、`C#` 只允许完整查询等价，不因单字母 `c` 触发嵌入匹配。此处不引入可配置分词器。

### 验收

- `java` 不匹配 `javascript`、`myjava2`，匹配 `Java 开发`、`Java开发` 和标点包围的 Java；
- 中文 term 继续按紧凑子串匹配；NFKC、大小写折叠和带音标拉丁字母按同一边界工作；
- `node.js` 不因删除点号而命中 `nodejs`；`C++` 完整查询可命中，但普通 `c` 不可；
- direct 和 corpus short-query 两条路径使用同一组反例；
- 原有 2 字符最低阈值、4 字符嵌入阈值和 semantic score 阈值不改变。

## 修复单元 T：合并阻断的测试夹具与轻量导入

### Unit pytest

3 个失败测试通过 `object.__new__(LLMSessionManager)` 绕过生产构造器，夹具没有 `_text_route_owners`，而生产 `manager.py` 已在构造时初始化该字段。修复测试 `_make_manager()`，显式设为空字典；不得为了不完整测试对象在生产路径添加 defensive `getattr`。同时把 takeover 用例扩展为修复单元 Q 的 owner 清理和并发保留断言。

### Plugin pytest

`study_companion` 注册导入经过 `plugin.server.routes` package 后加载 `knowledge_market`；该模块从聚合层 `knowledge.api` 导入两个轻量订阅符号，聚合层继续加载 service、vector index 和 NumPy。将 `SUBSCRIPTION_PROTOCOL_VERSION` 与 `load_canonical_pack_artifact` 改为直接从 `knowledge.subscriptions` 导入；`MAX_PACK_BYTES` 继续来自 `knowledge.packs`。不为一个导入边界回归重构整个 routes package，也不添加运行时 sys.modules 清理。

### 验收

- 3 个当前失败的 manager unit tests 通过，且新 takeover 竞态测试通过；
- `test_study_plugin_registration_import_does_not_load_numpy` 通过；
- Plugin Market 的订阅 descriptor、canonical artifact 和下载上限行为不变；
- 在 fresh interpreter 中导入 Study Companion 注册模块后，`numpy` 与 `knowledge.vector_index` 均未出现在 `sys.modules`。

## 第三轮兼容、并发与失败矩阵

| 场景 | 可信事实 | 允许结果 | 禁止结果 |
| --- | --- | --- | --- |
| 新格式 Market unsubscribe | registry 的 provider package ID | 取消同包任务并删除解析出的真实 pack | 按 caller pack ID 删除 |
| 旧格式 unsubscribe，Market 离线 | 无法完成所有权证明 | 明确失败、保留数据 | 猜测 remote ID 或 pack ID 后删除 |
| unsubscribe 与 subscribe 并发 | 先登记的 package reservation | 旧任务完全结束后才允许重试 | 删除期间启动新 worker |
| staged state 损坏 | immutable identity 或 orphan 事实 | degraded/quarantine、计入预算、显式清理 | 静默跳过、自动删除、按零计费 |
| 数据库 Schema > 7 | 数据库自身版本标记 | 只读探测后 degraded | WAL/DDL/DML/版本覆盖 |
| takeover cleanup 期间新请求到达 | active request 快照与 owner map | 仅清旧 request | 清掉新 request 状态 |
| 拉丁 term 嵌在更长拉丁 token | 邻接拉丁字符/数字 | 不视为 direct match | `java` 自动命中 `javascript` |
| path-specific body 超限 | error code 与实际 limit | 中性人类文案 | 声称触发全局配置 |

## 第三轮实施顺序、提交边界与关闭条件

1. CI 与低风险边界提交：L、M、T。先恢复可用验证信号，不混入数据迁移。
2. 数据库兼容提交：R。独立提交便于审查“拒绝前零写入”的证据。
3. 作业持久化提交：P。包含旧 job 兼容、隔离展示、容量和显式 discard 的完整闭环。
4. 订阅身份与并发提交：N、O。二者共享身份和取消顺序，不能只实现“取消 worker”而仍信任 caller pack ID。
5. 会话与匹配提交：Q、S。它们互不共享状态，测试文件可清晰归属。
6. 回归修正提交：仅处理上述定向测试与全量 CI 暴露的必要问题，不顺带扩大检索或路由架构。

每个提交先运行对应定向测试，再运行受影响测试组。Python 使用项目 Python 3.11 的 `uv run pytest`；Plugin 测试在独立进程验证 import graph。只有满足以下全部条件后才可回复并 resolve 对应评论：实现已提交、评论所述反例有精确测试、相邻失败语义有至少一个负例、相关 CI 通过。

### 不属于第三轮关闭条件的后续增强

- semantic top-K 已在截断前排除 disabled chunks；“单个启用 entry 的大量 chunks 挤出其他启用 entry”属于结果多样性问题。若产品要求每个 entry 至少一个候选，应在 vector snapshot 中按 entry rowid 取最大分后再做 entry top-K，或渐进扩大 chunk 窗口直到得到足够不同 entry。该变化会影响相关性排序与性能，不与本轮 disabled 正确性评论绑定。
- stage/remove 已有共享 pack lock；本轮只补真实并发回归，不改为数据库级分布式锁。当前桌面单进程模型不需要扩大锁范围。

## 第三轮实施结果

| 提交 | 覆盖单元 | 关键结果 |
| --- | --- | --- |
| `7b972d227` | L、M、T | JSON 解码离开事件循环；413 文案统一；manager 测试夹具完整；知识轻量导入不再加载 NumPy/vector index |
| `425d118b1` | R | 未来 Schema 在 WAL/DDL/DML 前拒绝，status 暴露稳定 degraded 诊断 |
| `d56d2c778` | P | job 原子发布；损坏状态隔离且计入容量；orphan 失败关闭；显式 discard |
| `682f2cd40` | N、O | provider package identity 持久化；unsubscribe reservation、worker 终态、durable job 取消及 Main Server 二次所有权校验 |
| `f4a9aaf31` | Q、S | takeover 同步释放旧 owner 并保护新请求；拉丁词按脚本边界匹配 |

本地合并前回归覆盖知识库、公共知识路由、请求体守门、Plugin Market、Study Companion 轻量导入及核心 takeover 生命周期，共 354 项测试通过。本次改动文件 Ruff 检查通过。全仓 Ruff 仍有一个既存、范围外的 `ASYNC220`（CosyVoice server 在 async 函数中调用 `subprocess.Popen`）以及旧 `noqa` 格式警告，不在本 PR 中顺带修改。GitHub CI 结果仍以对应提交上的远端检查为准。

## 第四轮：迁移、资源与并发边界

第四轮来自第三轮实现后重新收集的 12 个有效未解决线程。两个关于 `package_id` 字符串/整数直接比较和 subscribe/unsubscribe 检查窗口的评论经实际控制流复核不成立：持久化身份比较前显式执行 `str(package_id)`；subscribe 从 reservation 检查到 worker 映射登记之间没有 `await`，单事件循环不能在该同步区间插入 unsubscribe。两条线程已回复依据并关闭，不计入本轮。

### 修复单元 U：legacy 迁移输入必须全部可证实

涉及：旧数据库读取失败被折叠为空集合、旧 `packs.json` 无效时被静默跳过两条 P1 评论。

迁移采用“先验证全部输入、再创建候选结果、最后原子发布”三阶段。任何一个已发现的 legacy 数据库或注册表无法读取，都不能用其余输入生成权威目标库。

- 为迁移增加严格读取入口；`list_active_entries()` 等面向自动检索的安全降级 API 不得用于迁移。严格入口传播 `KnowledgeStoreError`、`sqlite3.Error` 和锁超时，并在读词条、来源策略、向量前执行只读完整性与 Schema 兼容检查。
- legacy 数据库缺失与“路径存在但不是可读 SQLite 数据库”是不同状态。后者中止迁移并保留恢复副本；不得发布空 `knowledge.db`，也不得写入表示迁移完成的目标文件。
- legacy `packs.json` 缺失表示该来源没有注册表，可以继续；路径存在但读取失败、JSON 非对象、Schema 不支持或 `packs` 非对象时中止迁移。复用 pack registry 的严格解析契约，不能在 migration 中维护第二套宽松 parser。
- 全部输入验证成功后才创建 stage。stage 内的数据库、registry 和 override 完成 `quick_check`、计数与引用一致性检查后，才 `os.replace()` 发布。失败只清理未发布 stage，不修改 legacy 输入和既有目标。
- 服务状态将迁移失败映射为稳定的 `knowledge_legacy_migration_failed`，只暴露输入类别和安全原因码，不暴露本地路径、SQL 或原始 JSON。

验收：损坏、锁定和临时不可读数据库分别使迁移失败且不生成目标；缺失 registry 可迁移，存在但损坏/不可读/未来版本 registry 必须失败；修复输入后重试可成功；失败前后恢复副本内容不变。

### 修复单元 V：前端导入体积的第一道防线

涉及：`file.text()` 前未检查 `File.size` 的 P2 评论。

- Plugin Manager 在任何 `await file.text()` 或 `JSON.parse()` 前比较文件字节数与知识包制品上限。空文件仍交给格式错误路径；超限文件不读取、不解析、不发送 Bridge 请求。
- 前端常量明确表示“知识包文件上限”，与后端 `MAX_PACK_BYTES` 保持 10 MiB。增加契约测试防止两端数值漂移；Bridge envelope 的额外预算不混入文件上限。
- 增加 `knowledge.importTooLarge` i18n key，并同步所有现有语言。不能把体积错误伪装成 JSON 格式错误。
- 后端流式限制仍是权威安全边界；客户端检查只改善页面可用性，不能替代 Bridge/Main Server 校验。

验收：上限加一字节的 File 不调用 `text()` 和 API；恰好上限仍进入解析；提示走本地化体积错误；伪造前端仍被后端拒绝。

### 修复单元 W：LIKE 查询必须按字面量解释

涉及：规范化查询中的 `_`、`%` 被 SQLite 当作通配符的 P2 评论。

- 新增单一 `_escape_like_pattern()`，按“先转义 escape 字符，再转义 `%`、`_`”的顺序处理用户片段，再在两端添加 `%` 作为系统控制的 contains 通配符。
- 所有五个 LIKE 分支使用同一参数和显式 `ESCAPE '\\'`；继续参数化查询，不能字符串拼接 SQL。
- FTS 失败后进入 LIKE fallback 时同样使用字面语义。普通字母、CJK、空格和连字符规范化行为不变。

验收：仅含 `_`、`%`、反斜杠及其组合的查询只命中字面包含者；普通 contains 查询不回归；恶意引号不改变 SQL 结构；来源过滤仍生效。

### 修复单元 X：degraded 必须是可终止、可诊断状态

涉及：Market 轮询忽略 degraded job、status 只捕获 future Schema 两条 P2 评论。

- `_wait_for_pack_job()` 将 `degraded` 与 `cancelled/failed` 一样视为终态失败，抛稳定 `job_degraded`；保留服务返回的安全 `reason` 供日志诊断，但不把路径或异常正文传给远端 Market。worker 立即结束并释放四个订阅槽之一，隔离 job 仍只能由显式 discard 删除。
- `KnowledgeService.get_status()` 将数据库兼容探测包在统一错误边界。`KnowledgeSchemaTooNewError` 保持专用 `knowledge_schema_too_new`；其余 `KnowledgeStoreError`/SQLite 打开、锁定、损坏、非法或冲突 marker 返回 `integrity_ok=False`、`schema_state="invalid_or_unavailable"`、`error_code="knowledge_database_unavailable"`。
- 抽取零数据 degraded payload builder，保证不同失败路径都含前端依赖的计数、registry/job 状态和向量预算字段，避免修复 500 时制造响应 shape 分叉。
- status 不尝试修复、迁移或覆盖错误数据库；后台索引仍失败关闭。暂时锁定与持久损坏使用同一外部错误码，详细类别只进入本地日志。

验收：degraded job 一次轮询即结束任务并释放 active 映射；显式 discard 仍可用；损坏、锁定、非法 marker、marker 冲突和未来 Schema 的 status 均返回结构化 degraded，其中未来 Schema 保持专用字段；新安装空目录仍健康。

### 修复单元 Y：provider package ID 的唯一规范形式

涉及：`isdecimal()` 接受 Unicode 十进制数字的 Minor 评论。

- 公共契约为 ASCII 正十进制字符串：`[1-9][0-9]{0,18}`。导出一个规范化/校验 helper，由 subscription 解析、Main Server 删除边界和需要持久化身份的路径共同使用。
- 不使用 `isdecimal()`、`isdigit()` 或先 `int()` 再判断原字符串；全角数字、阿拉伯-印度数字、符号、空白、前导零和 20 位以上值全部拒绝。
- Market 请求模型与 descriptor 的整数上限同步为 19 位最大值。合法整数持久化时只通过 `str(value)` 生成规范 ASCII。
- 既存非 ASCII 值不可能由受支持写路径产生，按损坏身份失败关闭，不做猜测式转换。

验收：`1`、19 位最大值通过；`0`、前导零、20 位、全角和阿拉伯-印度数字在 subscription 与 remove 两处得到相同拒绝；新安装、replacement 和离线 unsubscribe 的合法身份不回归。

### 修复单元 Z：TTS 清理按 speech 所有权执行

涉及：takeover 旧请求在 `_clear_tts_pipeline()` 等待后清空新请求 pending chunks 的 Major 评论。

- `_clear_tts_pipeline()` 接收调用入口在首个 `await` 前捕获的 `expected_speech_id`。清理只拥有该 speech 的 pending text、done 记账、回放状态和可识别响应；不得在等待后无条件清空共享新世代状态。
- `tts_pending_chunks` 已携带 speech ID，清理时过滤旧 ID 而非 `clear()`。等待 worker 处理 interrupt 后，在 `tts_cache_lock` 内比较 `current_speech_id`；若已切换，只删除旧 ID 项并保留新 ID chunks 与新轮 done flags。
- 对不能按 speech ID 区分的 worker interrupt/响应队列，定义一个单调递增 TTS generation。interrupt 记录目标 generation，后续请求入队携带新 generation；迟到的旧响应由 handler 丢弃，不能通过“等待后清空整个响应队列”保护正确性。
- 所有清理调用点必须显式传入旧 speech 快照。确实要关闭整个 worker 的 lifecycle shutdown 使用单独 `clear_all=True`，避免普通 takeover 借用全局销毁语义。

验收：旧 cleanup 的等待窗口内启动新请求，新 `tts_pending_chunks`、done 状态和音频均保留；旧 speech 的 pending/迟到响应被丢弃；连续两次 takeover、worker 未 ready、worker 已 ready 和 shutdown 全清理路径均有测试。

### 修复单元 AA：终态作业历史有界保留

涉及：成功、失败、取消的 staging 目录永久增长的 P2 评论。

- terminal job 保留短期诊断价值，但采用双界限：默认保留 7 天且每类知识根最多保留最近 100 个 terminal 目录。超过任一界限的旧目录可删除；非终态和 degraded/orphan 永不自动删除。
- prune 在持有 jobs-root 跨进程 mutation lock 时执行；先按可信 `updated_at/created_at`、再按目录 mtime 排序。identity/state 无法可信读取的目录已经属于 degraded，不得作为过期 terminal 清理。
- 删除目标必须是 `_jobs_root` 的直接子目录且名称等于已验证 job ID；不跟随 symlink，不接受路径穿越。失败记录日志并留待下轮，不阻断本轮索引。
- `/packs/jobs` 仅返回保留窗口中的 terminal history；当前 active、pending、degraded 始终完整返回。

验收：第 101 个 terminal job 删除最旧项；超过 TTL 的 terminal 被删；新 terminal、非终态、degraded、orphan、symlink 均不被误删；并发 list/process/prune 不产生半删除状态。

### 修复单元 AB：mutation lock 的跨进程线性化

涉及：维护 CLI 与 Main Server 共享路径时 `threading.RLock` 无法互斥的 P2 评论。

- 保留当前按规范化路径共享的进程内 `RLock`，在最外层进入时再获取同路径 sidecar lock file 的 OS advisory exclusive lock；最外层退出时释放。嵌套同线程调用只增加深度，不能对同一文件锁二次阻塞。
- 使用项目已有的 `portalocker` 提供跨平台 advisory exclusive lock（底层分别采用操作系统文件锁）。锁文件位于目标同目录并使用稳定、无用户输入的派生名称；锁文件持久存在是正常状态，不以删除 lock file 表示释放。
- 获取顺序固定为进程内锁后文件锁；多路径操作继续使用既有上层 pack-operation 锁顺序，禁止在持有具体 state/registry 锁后反向获取 pack root 锁。
- 维护 CLI 的所有 mutation 复用同一 helper。只读诊断不取独占锁；会基于读结果写回的 read-modify-write 必须把读取和提交放在同一锁区间。
- 文件锁获取失败或超时应明确终止维护操作，不退化为无锁执行。服务路径继续在工作线程等待，不能阻塞事件循环。

验收：两个独立 Python 3.11 进程对同一路径互斥；不同路径可并行；同线程嵌套不死锁；cancel 与 activate、policy 与 registry 更新的受控竞态最终状态一致；异常退出由 OS 自动释放锁。

### 修复单元 AC：bounded spool 的磁盘 I/O 不占事件循环

涉及：`SpooledTemporaryFile` rollover 后同步读写的 P2 评论。

- 网络 `receive()` 仍在事件循环协调；spool 的 `write`、`seek`、`read`、`close` 全部通过 `asyncio.to_thread` 执行。为避免每个小 chunk 都产生线程切换，可在内存阶段累计到固定 64 KiB 块后批量写，但内存累计仍受总上限控制。
- replay receive 改为 async 读取 helper，一次读取并判定 `more_body`，不使用同步“多读一个字节再 seek 回退”。spool 访问保持单消费者，关闭发生在下游 ASGI app 完成后的 `finally`。
- 超限、disconnect、下游异常和正常完成都必须关闭 spool；413 payload 与现有稳定错误语义不变。

验收：强制 rollover 后 write/read/seek/close 均在线程池线程执行；伪造或缺失长度仍受实际字节限制；边界值可逐字节重放；disconnect、413 和下游异常不泄漏临时文件。

### 修复单元 AD：从完整的有效标签总体随机抽样

涉及：先取 ranked top-100 再随机抽样造成永久偏差的 P2 评论。

- KnowledgeStore 增加按精确 tag 选择启用 entry rowid 的有界随机查询，使用 JSON tag 成员匹配而非文本 search 排名。禁用集合在抽样前排除。
- 不使用 `ORDER BY RANDOM()` 扫描并排序完整大表。先取得符合条件的 rowid 总体或使用 reservoir sampling；当前社区总 entry 上限 20,000，可在工作线程中对 rowid 流做等概率 reservoir，内存保持 O(limit)。随后按选中 rowid 批量加载 entry。
- `CORPORA_SAMPLE_TAGS` 白名单、调用方 limit 1..3 和 material type 路由保持不变。相同 entry 不重复；少于 limit 时返回全部。
- 为可重复测试允许向内部 helper 注入 RNG，但产品路径继续使用进程随机源。

验收：构造 101+ 同标签条目并控制 RNG，原 top-100 外条目可被选中；禁用项永不出现；其他 tag、正文中仅出现标签文字但 tags 不含该值的 entry 不进入总体；1/3 上限和空集合行为不回归。

## 第四轮实施顺序、提交边界与关闭条件

1. 先提交本文和索引，冻结失败语义与测试口径。
2. 数据安全提交：U。迁移两个 P1 必须同一提交闭环，防止只保护数据库却继续丢 registry。
3. 查询与健康提交：W、X、AD。三者共享 KnowledgeStore/Service 读取边界，但测试按问题分组。
4. 身份与前端提交：V、Y。V 必须同步全部 i18n；Y 必须同步 Market 请求上限和 Main Server 校验。
5. 生命周期提交：Z、AA。TTS 只改 speech 所有权，job prune 只处理可信 terminal，不互相耦合。
6. 并发与 I/O 提交：AB、AC。先让 file lock 有独立跨进程测试，再把所有 spool 文件操作移出事件循环。
7. 全量回归后补写“第四轮实施结果”。只有实现提交已推送、精确反例测试通过、相邻失败语义有负例且远端相关 CI 通过，才回复并 resolve 对应线程。

第四轮不改变知识包五字段内容 Schema、不自动修复损坏数据库/作业、不扩大 20,000 chunk 与 10 MiB 文件预算，也不以自动删除 degraded 证据换取容量恢复。

## 第四轮实施结果

| 提交 | 覆盖单元 | 关键结果 |
| --- | --- | --- |
| `d33a80b25` | U | legacy 数据库、来源策略、向量和 registry 全部严格读取；任一已存在输入不可证实时中止且不发布目标 |
| `a6559c36d` | W、X、AD | LIKE 字面转义；degraded 轮询终止；普通数据库错误结构化降级；完整标签总体 reservoir 抽样 |
| `b8790f2bd` | V、Y | 前端在读取前拒绝超过 10 MiB 的文件并补齐八种语言；provider ID 统一为 ASCII 正整数格式 |
| `527b6e935` | Z、AA | TTS 按旧 speech ID 清理；terminal job 按 7 天与 100 条双上限安全裁剪 |
| `50495e40d` | AB、AC | mutation lock 增加可重入跨进程文件锁；spool 的 write/seek/read/close 全部离开事件循环 |
| `6e4a3e131` | AE、AF | spool 所有权移交前的取消/异常路径关闭临时文件；前端 degraded 立即终止轮询；状态默认值与作业状态集合统一来源 |

本地合并前回归覆盖知识库、公共知识路由、请求体守门、Plugin Market、Study Companion 轻量导入及核心 takeover 生命周期，共 377 项测试通过；本轮所有改动文件 Ruff 检查通过。前端 `vue-tsc --build` 与 i18n 完整性检查通过，八种语言均为 732 个键。测试退出后的 telemetry 日志在受限沙盒中仍会报告既存的本机配置目录写入失败，但 pytest 返回码为 0，不影响上述结果。GitHub CI 结果仍以对应提交上的远端检查为准。

## 第四轮审查正文补充

重新检查 review body 后发现两条未生成独立 review thread 的 outside-diff 有效评论。它们不改变 U–AD 的总体方案，但补齐 AC 的所有权异常路径和 X 的前端终态一致性。

### 修复单元 AE：spool 构造方在所有权移交前负责异常清理

- `_spool_bounded_body()` 创建 spool 后、成功返回给调用方前，是临时文件的唯一所有者。`receive()`、异步 `write()` 或最终 `seek()` 抛出任何 `BaseException`（包括取消）时，它必须在线程中关闭 spool 后重新抛出原异常。
- 正常、disconnect 和超限返回表示所有权已移交给 `__call__()`；仍由外层既有分支或 `finally` 关闭，避免双重关闭成为正确性前提。
- close 自身失败不能覆盖原始接收、写入或取消异常。测试分别注入 receive 取消与 write 失败，并断言 close 已执行且原异常保持不变。

### 修复单元 AF：管理界面与后端使用同一作业终态

- 前端导入轮询将 `degraded` 与 `failed/cancelled` 一样从 pending 集合移除，显示已有的操作失败提示并触发概览刷新；不得继续等待十分钟后显示“仍在处理”。
- 后端仍保留 degraded 作业证据，只有显式 discard 才删除；前端这里只停止自动轮询，不自动清理服务端状态。
- 状态统计使用 `TERMINAL_STATES | {DEGRADED_STATE}` 作为“不再 pending”的单一表达，同时保留 degraded 与可自动裁剪 terminal 的生命周期差异。

同一 review body 的三个维护性建议一并按最小范围处理：删除 Schema marker 校验中不可达的空字符串分支；用 helper 共享空 chunk status 字段；用 pack job 常量构造非 pending 状态集合。它们不单独改变外部契约，也不扩大本轮边界。

## 第五轮：持久化恢复与检索精度

第五轮来自提交 `0d727115c` 完成后新增的 7 条 review thread。逐项追踪生产调用链后 6 条成立，混合脚本 routing 一条不成立；其中 semantic entry 去重曾作为第三轮后续增强记录，本轮既然已有可构造的漏召回反例，正式纳入正确性边界。

### 修复单元 AG：作业状态时间字段必须先验证再参与排序

- `_read_job()` 在返回可信 state 前统一规范 `created_at` 与 `updated_at`：仅接受非负整数语义，布尔值、浮点、空值、非数字字符串和负数均把作业隔离为 `degraded`，原因码为 `invalid_job_timestamps`。
- identity 中可信的 `created_at` 仍可用于 degraded 展示；不可把损坏 state 的时间值传给 `int()` 排序或 TTL 裁剪。
- `list_pack_jobs()`、status 和 terminal prune 对任意合法 JSON state 都不能抛出类型转换异常。

### 修复单元 AH：健康响应的 chunk 字段集合固定

- `_empty_chunk_status()` 与 `KnowledgeStore.chunk_status()` 保持同一字段集合，补齐 `chunks_local`、`chunks_prebuilt_only` 以及所有 `chunks_local_*` 计数。
- 空数据库、未来 Schema、普通数据库不可用和健康数据库的 status 只允许数值不同，不允许字段缺失。用集合等价测试锁定契约。

### 修复单元 AI：semantic 截断以 entry 而非 chunk 为单位

- 完成来源与 disabled 过滤后，先扫描所有合格 chunk，为每个 entry rowid 保留最高分 chunk；随后才对唯一 entry 候选排序并应用现有候选预算和最终 `limit`。
- 相同分数使用 entry rowid 与 chunk index 建立稳定次序；`best_chunk_index` 继续指向该 entry 的最高分 chunk。
- 总向量上限仍为 20,000，单次扫描 O(chunks)，候选映射 O(unique entries)；不扩大快照和返回预算。

### 复核结论 AJ：混合脚本 routing 评论不成立

- `KnowledgeService._get_routing_state()` 是产品中唯一的 `RoutingConfig` 构造点；它使用 `_effective_match_policy()`，而该函数只在 `KNOWLEDGE_MATCH_POLICY` 上替换来源集合，保留 `latin_word_boundaries=False`。
- 因此评论所指 `_contains_latin()` 分支当前产品路径不可达。`C语言` 会进入完整的 compact strong term `c语言`，单独的 `C` 不会命中；仓库也没有第二个启用 Latin boundary 的 `MatchPolicy` 实例。
- 不为不可达的预留分支扩大本轮实现。在线程中回复完整调用依据并 resolve；若未来启用该开关，启用提交必须先定义纯 Latin 与混合脚本契约及回归测试。

### 修复单元 AK：degraded 作业必须有受控恢复入口

- Bridge allowlist 暴露既有 `POST packs/jobs/discard`，仍受 loopback、token、CSRF、64 KiB 正文上限和 Main Server 二次 mutation 校验保护。
- 前端 API 增加 discard 方法；管理页在知识包页展示隔离作业的 job/pack/reason，并要求用户确认后逐个丢弃。成功后刷新 status、pack 与 job 状态，失败显示既有操作失败反馈。
- 维护 CLI 增加互斥动作 `--discard-job JOB_ID`，只调用既有 `discard_degraded_pack_job()`；非 degraded、非法路径或不存在作业返回非零，不提供任意目录删除能力。

### 修复单元 AL：词法精确匹配保留有意义标点

- 搜索同时构造 Unicode NFKC、casefold、空白规范化但保留标点的 folded surface。标题和 alias 的 1000/950 精确分只比较该 surface；compact normalization 继续用于 contains、recognition 和 tag fallback。
- `C++`、`C#`、`.NET` 等不再折叠成同一个精确键；大小写和兼容字符仍可等价。FTS/LIKE 只负责候选召回，不改变最终精确排序。

### 修复单元 AM：degraded 不阻断无关向量维护

- indexer 的 pending gate 使用 `TERMINAL_STATES | {DEGRADED_STATE}`，与 `process_pack_jobs()` 和 status 的非 pending 语义一致。
- degraded 仍使 registry health 为 invalid 并保留人工恢复提示，但不会占用可推进作业、不会令无关 bundled/installed source 的 `index_embedding_batch()` 永久停摆。

## 第五轮实施顺序与关闭条件

1. AG、AH、AM 先修持久化和健康面，确保损坏作业不会让诊断与后台维护同时失效。
2. AI、AL 独立收敛检索语义，分别覆盖 chunk 拥塞和标点术语的反例；AJ 只回复不可达调用链证据。
3. AK 贯通 Main Server、Bridge、前端和 CLI；不复制删除逻辑，只暴露既有严格 discard 能力。
4. 完整回归、前端类型/i18n、窄屏横向溢出与远端 CI 通过后，逐条回复提交和测试依据，再 resolve 7 条线程。

## 第五轮实施结果

| 提交 | 覆盖单元 | 关键结果 |
| --- | --- | --- |
| `43c138ce4` | AG、AH、AM | 严格验证作业时间字段；空状态补齐本地向量策略字段；degraded 不再占用 pending gate |
| `2a114dd23` | AI、AL、AJ | semantic 在 entry 去重后截断；词法精确匹配保留标点；以唯一生产构造链证明混合脚本评论不成立 |
| `5557d1760` | AK | Bridge、管理界面和维护 CLI 统一暴露既有严格 discard；网页提供确认、反馈和八语言文案 |
| `4b75b24b4` | AN | identity 的创建时间在可信返回前按 state 同一规则规范化，拒绝布尔值与浮点 fallback |
| `e5979c43e` | AO | 标点精确标题/alias 在 FTS/LIKE 截断前召回，避免被同一 compact token 的宽候选挤出 |
| `34e05caed` | AP | 缺失 identity 的 legacy state 复用严格目录身份校验；合法旧任务保持兼容，篡改任务隔离 |
| `d76c9447a` | AQ | 管理目录按请求批量解析来源映射，社区 registry 最多读取一次且离开事件循环 |
| `079375f14` | AR | Marketplace terminal 任务增加 200 条上限，创建和完成后裁剪且保留活动 worker |

本地合并前回归覆盖知识库、公共知识路由、请求体守门、Plugin Market、Study Companion 轻量导入及核心 takeover 生命周期，共 420 项测试通过；本轮 Python 改动 Ruff 检查通过。前端 `vue-tsc --build`、API Vitest（8 项）和 i18n 完整性检查通过，八种语言均为 736 个键。

恢复入口另在 390px 与 1024px 视口完成真实渲染。两种宽度的横向溢出均为 0，删除按钮保持 72×44px；窄屏底部增加含 safe-area 的滚动安全区后，按钮与固定浮层不再碰撞。独立 fresh-eyes 复审未发现新的 blocker 或 major。测试退出后的 telemetry 日志在受限沙盒中仍会报告既存的本机配置目录写入失败，但 pytest 返回码为 0。GitHub CI 结果以本节文档提交所在远端头部的检查为准。

## 第五轮审查正文补充：identity 时间戳信任边界

提交 `2a114dd23` 后的 review body 指出，AG 虽已严格验证 state 时间字段，但 `_validated_identity()` 仍先用 `int()` 转换 identity 的 `created_at`。这会接受 `true` 和 `1.5`；当 state 缺少 `created_at` 时，该值可能作为 fallback 进入正常作业。

### 修复单元 AN：identity 时间戳必须在可信返回前规范化

- `_validated_identity()` 在返回 `state="valid"` 前，使用与 state 相同的时间戳规范化函数检查 `created_at`。只接受非负整数或规范 ASCII 十进制字符串；布尔值、浮点、负数和其他字符串均使 identity 无效。
- 无效 identity 统一隔离为 `invalid_job_identity`，展示时间回退到可信目录 mtime；不得把不可信 identity 时间传给 state fallback、排序或裁剪。
- 保留对合法旧 identity 数字字符串的兼容，不改变 job/pack identity、容量计数或显式 discard 边界。

验收：state 缺少 `created_at` 且 identity 分别为 `true`、`1.5` 时，作业均进入 degraded，列表与 status 不抛异常；合法整数 identity 的恢复行为不回归。

## 第五轮后续线程：候选召回、作业身份与有界读取

提交 `4b75b24b4` 后重新收集全部未解决线程，又出现 4 条可构造的有效边界。它们不改变前述容量和失败语义，只补齐候选生成、legacy 作业认证、管理查询线程边界和内存任务保留上限。

### 修复单元 AO：标点精确项必须在通用候选截断前召回

- KnowledgeStore 增加按原始标题或 alias 精确查询的参数化入口，保留标点并支持 SQLite ASCII 不区分大小写；来源过滤与通用 FTS/LIKE 使用同一约束。
- KnowledgeRetriever 先合并精确候选，再补充各自有上限的 FTS/LIKE 候选；最终仍由 `_folded_exact_surface()` 做 NFKC、casefold 和空白规范化判分。精确查询不替代通用召回，也不扩大最终返回 limit。
- 精确候选自身仍使用现有 `candidate_limit`。同一 surface 有大量重复项时任取其中稳定前缀不影响“查询项被较宽 compact token 挤掉”的边界；disabled 余量继续计入上限。

验收：构造超过 candidate limit 的较早 `C` 候选，再插入 `C++`，查询 `C++` 且 limit=1 必须返回 `C++`；来源与 disabled 过滤不回归。

### 修复单元 AP：缺失 identity 的 legacy state 必须自证目录身份

- 保留当前对本 PR 早期 staged job 的兼容，但只有 state 自身完整满足 identity 契约时，缺失 `identity.json` 的作业才可继续：`job_id` 必须等于目录名且无路径语义，`pack_id` 合法，创建时间与容量计数均可验证。
- 抽取单一 identity payload 校验器，磁盘 identity 与 legacy state 共用；不得出现“有 identity 严格、无 identity 反而直接信任”的分叉。
- legacy state 认证失败后进入 `invalid_job_identity` degraded/orphan，不参与调度、不解析它声明的其他目录；仍可通过严格 discard 恢复。

验收：合法无 identity 的早期作业仍可激活；缺失 identity 且 state 的 job_id 指向其他目录或非法路径时只隔离当前目录，不能处理目标目录。

### 修复单元 AQ：管理目录的来源元数据一次读取且离开事件循环

- source registry 提供批量解析入口：一次读取并解析 `packs.json`，合并内置来源、社区来源和未知来源 fallback，返回请求所需 tag 的完整映射。
- entries 搜索页、普通目录页和单条详情在构造响应前，通过 `asyncio.to_thread` 批量加载来源映射；`_entry_payload()` 只做纯内存格式化，不再在事件循环中触发文件读取。
- registry 不可读时保持既有展示降级：社区来源显示安全的 tag/Unknown，不在只读目录请求中覆盖或修复文件。

验收：100 条、多个社区来源的页面只读取 registry 一次，读取发生在非请求线程；内置和未知来源展示不回归。

### 修复单元 AR：Marketplace 订阅任务记录同时受 TTL 与数量上限约束

- 与相邻安装任务注册表一致，内存 `_tasks` 最多保留 200 条；TTL 清理后仍超限时，按可信 `completed_at`、再按 `created_at` 删除最旧 terminal 记录。
- `_task_workers` 中的活动任务和尚无 `completed_at` 的记录永不因数量裁剪。创建与 done callback 后都执行裁剪，使快速成功/失败序列不能等一小时才释放。
- 裁剪 terminal 时不触碰正在 unsubscribe 的 package reservation；活动映射继续由 `_subscription_done()` 的所有权检查清理。

验收：201 条 terminal 只保留最新 200 条；混合活动与 terminal 时只删除最旧 terminal；TTL、同包去重、4 worker 上限和任务查询行为不回归。

## 第六轮：跨层竞态、快照一致性与输入成本

第五轮后续提交 `33f40e971` 的复审新增 7 条行内线程和 2 条 review-body outside-diff 评论。逐条沿生产调用链复核后均存在可构造反例，纳入本轮实际修复；它们不改变知识包 Schema、容量总额或显式恢复原则。

### 修复单元 AS：作业列表只接受最新请求结果

- `refreshAll()`、`loadPackJobs()` 与 `pollImportJobs()` 共用同一个 latest-request gate；任何较早请求完成后都不得覆盖较新列表。
- degraded 作业 discard 成功时先使所有已发出的列表请求失效，再本地移除该项并刷新概览。discard 成功后才发出的请求可正常写回。
- gate 只裁决 `packJobs` 及由该响应派生的轮询状态，不取消网络请求，也不改变服务端作业生命周期。

验收：挂起旧请求、成功 discard、再让旧请求返回时，被删除作业不会重新出现；三个入口互相交错时只有最后开始且未失效的请求可提交结果。

### 修复单元 AT：403 刷新不能清除更新后的 Bridge token

- 每次请求保存实际使用的 token。收到 token-invalid 403 时，仅当全局缓存仍等于该旧 token 才清空；随后调用普通 `token()`，复用现有的 in-flight refresh。
- 一个较晚返回的旧 403 不得清除另一个请求刚取得的新 token，也不得启动第二次刷新；每个业务请求仍最多重试一次。
- 其他 403、网络错误和服务端错误继续保持现有错误映射。

验收：两个并发请求使用同一旧 token，首个 403 完成刷新后第二个旧 403 才返回时，token endpoint 总调用数仍只有初取与一次刷新，新 token 不被清空。

### 修复单元 AU：维护状态读取容忍损坏时间字段

- `inspect_pack_jobs()` 对 `created_at` 使用与运行时相同的安全非负整数规范：拒绝布尔值、浮点、容器、负数和非规范字符串，并回退为 0 排序。
- `--status` 的职责是暴露诊断信息；单个合法 JSON state 的损坏时间字段不能令整个命令崩溃，也不能触发写入或自动修复。

验收：字符串、列表、布尔、浮点和负值混合存在时命令稳定返回，合法时间仍按新到旧排列。

### 修复单元 AV：标题与 alias 的精确召回使用 Unicode folded 等价

- 把 NFKC、Unicode casefold 与空白规范化的 exact-surface helper 放入共享过滤模块；最终判分和数据库精确候选使用同一实现。
- 每个 SQLite 连接注册确定性的只读自定义 collation，标题和 `json_each(alias)` 的等值比较显式使用该 collation。它只影响精确候选，不改变 FTS、LIKE 或持久化 Schema。
- 不依赖 SQLite 内建 `NOCASE` 的 ASCII 范围；`Straße`/`STRASSE`、兼容字符和大小写变体应在宽候选截断前召回。

验收：以大量宽候选挤占预算时，Unicode folded 等价的标题和 alias 仍能进入候选并获得精确分；来源和 disabled 过滤不回归。

### 修复单元 AW：legacy 迁移错误进入结构化降级面

- `_service()` 把 `KnowledgeStoreError` 与现有 `OSError`、`ValueError` 一并记录为服务初始化错误；status 返回稳定 degraded 描述，读写业务端点返回既有 503，而不是泄漏 HTTP 500。
- 此处只转换服务边界，不吞掉迁移内部错误，也不发布空迁移结果或覆盖损坏输入。

验收：损坏、过新或暂时不可读的 legacy 数据库触发 `KnowledgeStoreError` 时，status 可诊断且 entries/mutation 均为结构化 503。

### 修复单元 AX：管理搜索分页基于固定候选窗口

- 管理目录的 `search_page()` 使用覆盖 API 最大 offset、最大页长和尾部探测项的固定排名窗口；不同页不得通过改变 retriever limit 改变候选池。
- 固定窗口仅用于管理搜索分页；聊天检索继续按调用 limit 使用小候选预算。offset 超出 API 约束仍由路由拒绝。
- 排名与稳定 tie-break 规则不变，分页只在同一固定结果序列上切片。

验收：相同查询的相邻页向 retriever 传入相同窗口，结果无重复、无因页码变化产生的重排；空页和 has-more 探测不回归。

### 修复单元 AY：向量快照与 entry 行必须属于同一 revision

- KnowledgeStore 提供按预期 chunk revision 批量读取 entry 的事务入口：先在同一 SQLite 读事务中验证 revision，再读取 rowid；不匹配返回显式 stale，而不是把旧向量分数贴到新 entry。
- VectorIndex 使用该入口装配命中。revision 已变化时本轮 semantic 结果 fail closed，并使下一次查询重载快照；BM25 合并仍可提供结果。
- 不以读后再检查替代事务快照，因为 rowid 可在两个独立读取之间被删除和复用。

验收：在载入向量快照后替换数据库并复用 rowid，旧分数绝不返回新 entry；revision 一致时批量装配和最佳 chunk 标记不回归。

### 修复单元 AZ：每条知识的识别词元数据有独立上限

- 每个 term role 最多 64 项；全部受支持 term role 的 UTF-8 聚合字节最多 32 KiB。计数和字节校验在复制、规范化、分块与 prompt 拼接前完成。
- term 上限独立于正文 content budget，并继续受 10 MiB artifact 总上限保护；超限包以稳定校验错误拒绝，不截断、不静默丢词。
- 合法条目的去重与 normalization 语义不变。

验收：第 65 个同 role term 和超过 32 KiB 聚合值均在 staging 早期失败；边界值、Unicode 字节计数和正常包不回归。

### 修复单元 BA：作业目录枚举不等待在 indexer 事件循环

- `process_pack_jobs()` 通过 `asyncio.to_thread` 调用 `list_pack_jobs()`；等待跨进程 jobs-root 文件锁、解析状态文件和裁剪目录都发生在工作线程。
- 后续调度仍在原事件循环串行决定；不新增 worker、不改变每轮激活数和 pending/degraded 语义。

验收：线程标识证明列表读取不在 indexer loop；人为持有文件锁时并行 tick 仍可运行，调度结果不回归。

## 第六轮实施顺序与关闭条件

1. AV、AW、AX、AY 先收敛检索、迁移和快照一致性，分别用可复现数据反例锁定。
2. AS、AT、BA 修复跨请求和事件循环竞态；前端两个 gate 使用独立并发测试，后台枚举验证线程身份。
3. AU、AZ 收紧损坏元数据与不可信包输入，拒绝策略不得顺带修改磁盘内容。
4. 运行知识库、路由、维护脚本和前端 API/组件回归，并复查 Python lint、前端类型与 i18n。
5. 实现提交推送且远端相关检查通过后，在每条行内线程回复对应设计单元、提交和测试依据再 resolve；两条 outside-diff 在 review summary 留下同等证据。

## 第六轮实施结果

| 提交 | 覆盖单元 | 关键结果 |
| --- | --- | --- |
| `f2b350d0d` | AV、AW、AX、AY | 精确召回共享 Unicode folded collation；legacy store 错误结构化降级；管理分页固定候选窗口；向量命中按 chunk revision 在同一读事务装配 |
| `d51d48e22` | AS、AT、BA | 三个作业列表入口共享请求 gate；延迟旧 403 复用唯一 token refresh；作业枚举离开 indexer loop |
| `8d0a12db7` | AU、AZ | 维护状态安全规范损坏时间；term 每 role 64 项且单 entry 聚合 32 KiB 上限 |
| `e6202a280` | BA | terminal payload 清理与目录枚举一并移入工作线程，补齐文件删除阶段的事件循环边界 |

本地回归覆盖 Knowledge Store、检索、向量、Pack、作业、Indexer、Router、Bridge 与维护脚本，共 280 项通过并保留 3 条既有依赖弃用警告；测试退出后的 telemetry 仍因沙盒配置目录权限记录既有日志错误，但 pytest 返回码为 0。第六轮新增前端 API 与 request-gate 回归共 11 项通过，`vue-tsc --build` 通过；全部本轮 Python 改动 Ruff 与 diff whitespace 检查通过。

复审时 PR 远端头部仍为 `33f40e971`，未出现第六轮 7 条行内线程和 2 条 outside-diff 之外的新评论。只有实现提交可从 PR 远端读取并且相关 CI 通过后，才允许回复并 resolve 这些线程；本地验证不能替代该关闭条件。

## 第七轮：前端完成态与标签元数据成本

第六轮提交推送到 `39a80fe26` 后，Codex 复审新增 3 条行内评论。沿交互时序和 pack 校验路径复核后均成立；它们分别补齐 stale response 后的轮询生命周期、重复 mutation 的目标状态，以及与 term 相邻的 tag 元数据成本。

### 修复单元 BB：过期响应只丢弃数据，不终止轮询生命周期

- `pollImportJobs()` 收到响应后，仅在 request gate 仍为 latest 时写入 `packJobs`、解析作业终态并重置退避；响应过期时跳过这些数据操作，但不得从函数提前返回。
- stale、成功和请求失败三条路径都必须进入统一的 timeout 清理与下一轮 timer 调度。只有组件已销毁或 pending 集合为空时才停止。
- 不把 stale 当作网络失败增加退避，也不使用旧响应产生成功/失败通知。

验收：poll 进行中启动 `loadPackJobs()` 或 `refreshAll()`，随后旧 poll 返回时不会覆盖列表，pending job 仍会安排下一次轮询；销毁和超时路径不回归。

### 修复单元 BC：entry mutation 提交请求目标值

- 点击时先捕获 `requestedDisabled = !row.disabled`，请求和成功后的本地赋值都使用该固定值；不得在 await 后再次读取并反转可变的 `row.disabled`。
- 两个并发同向请求无论完成顺序如何，本地最终值都与后端请求值一致。错误请求不提交本地状态，保留既有失败提示。
- 本轮不增加全页 mutation 锁；后端 set 操作本身幂等，固定目标赋值即可收敛重复点击。

验收：同一原始状态连续触发两次请求并逆序完成，本地与后端都保持请求目标值；单次禁用、恢复和失败行为不回归。

### 修复单元 BD：tag 元数据在构造 entry 前有界

- 每条社区 entry 最多 64 个 tag，全部 tag 的 UTF-8 聚合字节最多 32 KiB；数量与字节在复制、KnowledgeEntry normalization、FTS 拼接和 UI 渲染前验证。
- 原始输入中重复 tag 同样计入成本，禁止依赖后续去重绕过前置工作上限。非法 Unicode 使用稳定校验错误拒绝。
- source tag 仍由安装边界注入，社区 payload 继续禁止自行声明 `source:` tag；合法 tag 语义和 artifact 10 MiB 总上限不变。

验收：第 65 个 tag、超过 32 KiB 的 Unicode tag 均在 validation 早期失败；64 项边界、正常 tag 与 source-tag 禁止规则不回归。

## 第七轮实施顺序与关闭条件

1. 先提交本节，冻结 BB–BD 的失败语义和验收反例。
2. BB、BC 在同一前端提交中只调整状态提交点；BD 独立修改 pack 校验并补充边界测试。
3. 运行前端 API/request-gate 与类型检查、Pack/作业/路由相关 pytest、Ruff 和 diff 检查。
4. 推送后重新等待当前头部全部检查，并重新收集所有 unresolved threads；只回复和 resolve 已有可见实现与通过证据的问题。

## 第七轮实施结果

| 提交 | 覆盖单元 | 关键结果 |
| --- | --- | --- |
| `b5050222c` | BB、BC | stale poll 只跳过响应提交并继续调度；entry mutation 捕获并提交固定的 requested disabled 状态 |
| `aef63512d` | BD | 每条社区 entry 的 tag 限制为 64 项、32 KiB UTF-8 聚合成本，并在 KnowledgeEntry 构造前拒绝 |

第七轮 Pack 回归 34 项通过；前端知识 API 与 request-gate 回归 11 项及 `vue-tsc --build` 通过；对应 Python 文件 Ruff 与 diff whitespace 检查通过。最终远端检查结果以本节实施记录提交所在 PR 头部为准。

## 第八轮：未发布兼容撤销、身份守恒与候选隔离

第七轮实施提交 `ec0b29e17` 的复审产生 6 条未解决行内线程，并在 CodeRabbit review body 中产生 1 条 outside-diff 有效评论。分页读取全部 review threads 并沿生产调用链复核后，7 条均成立；因此本节只冻结修复边界和关闭条件，不提前 resolve。此前已经回复并修复的线程在 GitHub 上均为 resolved。

本轮不扩大知识包文件、entry、chunk 或向量容量，不改变五字段内容 Schema，不自动修复损坏的持久化证据，也不通过增加无界候选或主线程工作量换取正确性。

### 修复单元 BE：删除自动旧库迁移

涉及：`knowledge/legacy_layout.py::_collect_legacy_data()` 对恢复源调用 `KnowledgeStore.assert_compatible()`，可能原地迁移旧 Schema 的 P2 评论。

#### 发布事实与决策门

- PR 与 `upstream/main` 的共同基线是 `fb78210ada`。该基线只有原始数据文件，没有本 PR 新增的统一公共知识运行时；`knowledge/legacy_layout.py` 到本 PR 的 `7d3b16a0f` 才出现。
- `moegirl-knowledge/knowledge.db`、`corpora/knowledge.db` 和 `public-knowledge/knowledge.db` 是本 PR 早期至中期的开发布局。当前 Git 历史中没有 tag 包含 `7d3b16a0f`，包含它的远端引用只有本 PR 分支及其备份分支。
- 因此默认结论是：这些格式没有形成已发布兼容合同。为未发布中间格式保留启动时自动迁移，会永久引入源库改写、注册表合并、策略继承、向量兼容、冲突恢复和失败发布边界，收益不足以覆盖风险。
- 实施前由维护者做最后一次外部分发确认。只有提供测试包、预览版或其他分发渠道及明确的数据保留承诺，才暂停删除；不能以“也许有人运行过 PR 分支”建立生产兼容合同。

#### 删除范围

- 删除 `knowledge/legacy_layout.py` 及 `KnowledgeService.__init__()` 中的自动探测和调用。服务首次发布只认识调用方传入的 database path 或最终统一的 `<knowledge_root>/knowledge.db`。
- 删除仅为 split/previous-unified 布局存在的 entries、registry、override、embedding policy 和 vector 合并代码；删除相应迁移专用测试及架构文档中的自动迁移承诺。
- 旧目录在运行时完全忽略：不读取、不移动、不改写，也不自动删除。开发期间运行过早期分支的人应删除自己的测试数据并从原制品重新导入；产品代码不为开发工作区提供隐式恢复。
- 保留最终统一数据库自身的 Schema guard、损坏状态诊断和当前格式内的事务更新；修复单元 R 等正式统一格式边界不因删除 layout migration 而撤销。修复单元 U、AW 中只服务旧布局合并的部分成为历史记录，由本单元取代；通用数据库错误的结构化 degraded 语义继续保留。

若最终出现真实外部分发证据，替代方案必须是独立、显式、一次性的离线维护工具：用户先备份并主动运行，工具在 staging 副本上升级和合并，成功后明确选择发布目标；不得恢复为每次服务构造时自动探测和改写旧库。该工具需要另立设计和测试，不属于本轮默认实现。

验收：旧 split/previous-unified 目录存在时，构造 KnowledgeService 不访问它们、不创建统一数据库，也不改变旧文件；仓库不再包含 runtime migration import/call 和迁移专用测试；全新安装、最终统一路径读取及重新导入知识包通过。若维护者在实施前提供外部分发证据，本单元停止执行并记录证据，不能半保留自动迁移。

### 修复单元 BF：取消无 identity 作业兼容

涉及：`knowledge/pack_jobs.py::_read_job()` 允许缺少 identity 的 state-only 作业，并且 mutable state 重复携带容量计数的 P2 评论。

- `identity.json` 成为所有 staged job 的强制信任根。文件缺失、不可读、字段非法或 `job_id` 与目录不一致时，一律进入 `degraded/orphan`；删除修复单元 AP 从 `state.json` 反推身份的兼容分支。
- 隔离作业不参与 processor，不自动修复或删除，只能通过管理端、Bridge 或维护 CLI 的既有严格 discard 显式清理。身份不可证实时继续全局拒绝新 staging，避免把未知容量按零处理。
- 新导入继续在 `.creating-<uuid>` 临时目录内完整写入 identity、pack/index artifacts 和初始 state；全部成功后才在同一文件系统原子重命名为公开 job 目录。列表不观察半成品目录为正常作业。
- `entries_total`、`chunks_total` 和 `content_bytes` 只在 identity 中持久化。state 不再重复保存这些不可变字段；`_read_job()` 在构造内存响应时从可信 identity 注入它们，容量准入只读取该可信视图。
- 为明确诊断已存在的 identity 作业，若 state 仍带有旧重复计数，字段必须先按严格非负整数规则验证并与 identity 相等；不一致时隔离为 `job_capacity_identity_mismatch`，但容量和展示仍使用 identity 值。新写 state 不再产生重复字段。
- staging 后的实际 chunk 数必须与 identity 中 preflight 的确定性 projected chunk 数一致；不一致表示实现或制品契约破坏，作业隔离，不能修改 identity 追随 mutable state。

验收：缺失、损坏或目录身份不一致的 identity 只能诊断和 discard，绝不调度；分别篡改 state 三项重复计数为 0、负数、布尔、浮点和不同正整数，均不能改变 20,000 entries/chunks 与 64 MiB content 的容量准入；新作业 state 不再持久化重复容量字段，列表和管理响应仍显示 identity 中的可信计数。

### 修复单元 BG：disabled override 使用规范化 entry 身份

涉及：`knowledge/catalog_overrides.py` 以原始展示标题作为 key，pack 更新仅改变大小写、NFKC 或空白时会重新启用词条的 P2 评论。

- `EntryKey` 的语义固定为 `(source_tag, normalize_knowledge_title(title))`。`entry_key()`、`set_entry_disabled()` 和 `load_disabled_entries()` 使用同一规范化函数；读取旧 override 时在内存中规范化并折叠等价重复项，但不因只读操作改写文件。
- override 文件只保存身份键；当前展示标题继续来自 entries 表，不把规范化字符串显示给用户。下一次显式 enable/disable mutation 会以规范形式原子写回整个集合，实现惰性收敛。
- `KnowledgeStore.entry_rowids_for_keys()` 必须按同一 NFKC、casefold、空白规范匹配数据库展示标题，可复用已注册的确定性 Unicode folded collation；不能把规范 key 与原始 SQL title 做二进制等值比较。
- 词法、semantic、routing、随机素材、目录展示和 status 的 disabled 判断全部继续通过共享 `entry_key()`/rowid 映射，不允许某条路径保留原始 title 比较。

验收：禁用 `Straße` 后以 `STRASSE`、兼容字符或不同空白更新同一来源词条，词法、semantic、routing 和随机素材都仍排除它；管理页仍显示新标题且可重新启用；旧格式 override 首次读取有效、首次 mutation 后收敛为规范 key。

### 修复单元 BH：目标素材类型拥有独立候选预算

涉及：`KnowledgeService.asearch()` 在区分 `target_material_type` 前使用共享候选池，knowledge 候选可把 corpus 全部挤出的 P2 评论。

- 仅当调用方指定 `target_material_type` 时，根据已读取的 `source_types` 把允许来源拆成 primary 与 fallback 两组；两组分别执行词法候选召回、semantic entry 去重、RRF 融合和固定候选截断，随后按 primary 优先、fallback 补足的既有返回契约组合。
- query embedding 仍只准备一次。prepared semantic query 可对两组来源分别评分，不能为每种素材重新加载模型或重复生成 embedding。
- 每组使用当前固定候选预算，不把预算乘成无界值。没有 primary 来源或 primary 无命中时允许 fallback；存在合格 primary 时，任意数量的高分 fallback 都不能在截断前删除它。
- 未指定 target 的自动会话双类型检索保持现有共享融合与排序，避免本轮改变普通聊天相关性。

验收：BM25-only 下构造至少 24 个更早或同分 knowledge 候选和一个 corpus 候选，显式 corpus 查询必须先返回 corpus；semantic-only 和双路融合具有同一保证；primary 不足、来源白名单、deadline 及无 target 排序不回归。

### 修复单元 BI：indexer 的第二次作业枚举离开事件循环

涉及：`knowledge/indexer.py` 在 `process_pack_jobs()` 后直接调用 `service.list_pack_jobs()` 的 P2 评论。

- 保留 post-processing fresh enumeration，因为当前轮可能刚改变作业终态；通过 `await asyncio.to_thread(service.list_pack_jobs)` 执行，不复用可能过期的轮前快照。
- 文件锁等待、目录遍历、JSON 解析和 terminal prune 全部留在工作线程；事件循环只接收返回快照并计算 `pending_jobs`、`backlog` 与是否进入普通 embedding batch。
- 不新增后台 worker、不改变每轮作业推进数量，也不放宽 degraded/terminal 的 pending 语义。

验收：分别覆盖轮前和轮后两次枚举的线程标识；另一进程持有 jobs-root lock 时并行 event-loop tick 可运行；作业刚完成后同轮可以正确进入普通向量维护。

### 修复单元 BJ：UTF-8 元数据在编码前受剩余预算约束

涉及：`knowledge/packs.py` 在编码全部 tag 后才检查 32 KiB 聚合上限的 Minor 评论；相同输入成本规则同时适用于修复单元 AZ 的 terms。

- 抽取只计算、不保留编码副本的 bounded UTF-8 累计 helper。对每个原始字符串先计算剩余预算；由于合法 UTF-8 每个 Unicode code point 至少占一个字节，`len(value) > remaining` 时可在编码前立即拒绝。
- 通过字符下界检查后才执行一次 `value.encode("utf-8")`；捕获预算内字符串的 `UnicodeEncodeError`，再以实际 bytes 长度检查 remaining 并累计。单次临时编码至多由 32 KiB 字符预算约束，不再由 10 MiB artifact 上限决定峰值。
- tag 与两个 term role 共用 helper 和各自既有 32 KiB entry 预算；数量上限、重复项计费、source tag 禁止、错误字段名和边界值语义保持不变。
- 不截断、不自动丢弃超限 metadata，也不把 Python 字符数误当最终 UTF-8 字节数。

验收：超长 ASCII tag 在 encode 前拒绝；字符数在预算内但 UTF-8 字节超限的多字节 tag 在有界编码后拒绝；孤立 surrogate 返回 UTF-8 校验错误；terms 的同类反例、恰好 32 KiB 和 64 项边界均通过。

### 修复单元 BK：概览刷新使用共享世代和分资源 latest gate

涉及：`KnowledgeManager.vue::refreshOverviewInBackground()` 只使缓存 stale，无法阻止旧 `loadStatus()`、`loadPacks()` 或 `refreshAll()` 响应覆盖新状态的 outside-diff 评论。

- 引入共享 `overviewEpoch`，代表 status、packs 与 session cache 所属的服务端事实世代。任何成功 mutation 安排后台刷新时，必须在 timer/queue/网络 await 之前同步推进 epoch，使此前所有在途概览响应立即失效。
- status 与 packs 各自保留 latest-request gate。每次网络请求同时捕获当前 epoch 和该资源 request ID；只有组件未销毁、epoch 仍相同且资源 gate 仍 latest 时，才能写 ref、对应 cache、loading/error 状态。
- `refreshAll()` 开始一个新 epoch，并让 status/packs 两个请求共享该 epoch，但分别取得资源 request ID。这样同一次全量刷新可同时提交两个结果，后发的 packs-only 请求也只取代旧 packs，不会错误阻止仍有效的 status。
- `refreshOverviewInBackground()` 在排队前推进 epoch并标 stale；实际执行时使用该 epoch 发起成对刷新。若等待串行 refresh chain 期间又产生 mutation，新 epoch 会使旧排队任务即使执行也不能提交。
- stale 请求的 `finally` 不得清除更新请求的 loading 标志；只有拥有对应 resource request ID 的请求可结束该资源 loading。卸载时推进 epoch并使两个资源 gate 失效。

验收：删除 pack 后让删除前 `loadPacks()` 最晚返回，已删除项不能重新出现；mutation 前 status 与 mutation 后 packs 交错时缓存不能混入旧世代；同一 `refreshAll()` 的 status/packs 都可提交；后发 packs-only 不饿死 status；旧请求的 finally 不提前关闭新请求 loading。

## 第八轮实施顺序与关闭条件

1. 先提交本节和设计索引，冻结 BE–BK 的信任来源、失败语义和回归反例。
2. 数据安全提交优先完成 BE、BF、BG：容量篡改必须失败关闭，未发布旧布局必须从 runtime 移除，disabled 身份必须跨 pack replacement 保持。
3. 检索与调度提交完成 BH、BI：目标类型独立预算与轮后目录枚举分别验证，不混入排序调参或 worker 架构调整。
4. 输入和前端提交完成 BJ、BK：bounded UTF-8 helper 覆盖 terms/tags；概览并发测试使用可控延迟 Promise 覆盖 epoch、resource gate 和 loading 所有权。
5. 运行 Python 3.11 下相关 pack/job/service/retrieval/indexer/router 回归、Ruff 和 diff 检查；确认迁移专用测试已删除且最终统一路径仍有覆盖。运行前端知识 API/request-gate 测试与 `vue-tsc --build`。若新增用户可见文案，必须同步全部 i18n；按当前方案不需要新增文案。
6. 实现提交推送且 PR 远端相关检查通过后，逐条回复设计单元、提交和精确反例测试，再 resolve 6 条行内线程。BK 是 review-body outside-diff，没有 GitHub Conversation 对象；通过 PR 评论留下同等实现与验证依据，并等待下一轮 review 不再报告。

BE、BF 只有同时满足以下证据才允许关闭：Git 历史证明没有 tag、release 或正式远端分支包含旧布局；备份分支和本 PR 历史提交不构成发布兼容承诺；启动服务不再扫描或打开旧分库；新安装空目录和最终统一数据库均正常工作；无 identity 作业只能隔离和显式清理；篡改 state 容量计数不能影响容量准入。若第一项出现反证，立即停止 BE 的 runtime 删除方案，另立显式离线迁移设计。

任何一条仅有文档方案、没有远端可见实现和通过证据时都不得关闭。第八轮实施结果应在完成后追加为提交矩阵，不用计划文本冒充已实施状态。

## 第八轮实施结果

| 单元 | 实现提交 | 落地结果 |
| --- | --- | --- |
| BE | `bcbabbf29` | 删除运行时旧布局迁移模块和启动调用；正式实现只识别统一数据库，旧开发目录保持未读取、未改写、未删除。 |
| BF | `53ddbf478` | `identity.json` 成为 staged job 强制信任根；缺失或不一致作业隔离为 degraded/orphan；容量只信任 identity，state 重复计数仅作一致性校验。 |
| BG | `15abf7d5f` | disabled override、entry key 和 rowid 映射统一使用标题规范化身份，pack replacement 的大小写、NFKC 与空白变化不再绕过禁用状态。 |
| BH | `ab4b9073d` | 显式目标素材查询为 primary/fallback 分别分配 lexical、semantic 与 RRF 候选预算；query embedding 仍只生成一次。 |
| BI | `ab4b9073d` | indexer 的轮后 fresh job enumeration 移入工作线程，保留同轮观察终态的语义且不阻塞事件循环。 |
| BJ | `7fa478d9f` | terms 与 tags 共用 bounded UTF-8 累计校验；字符下界先拒绝明显超限输入，单次编码受 32 KiB 剩余预算约束。 |
| BK | `b7c350c27` | 概览刷新使用共享 epoch 与 status/packs 分资源 latest gate；响应、缓存、错误与 loading 收尾全部要求票据仍属当前世代。 |

验证证据：BE 相关回归 40 项通过；BF 作业测试 50 项及相邻回归 98 项通过；BG 相关回归 71 项通过；BH/BI 目标测试 41 项及相邻回归 91 项通过；BJ 回归 38 项通过；BK 的 `vue-tsc --build` 通过，请求门控与可控延迟响应测试 8 项通过。Python 改动均通过对应 Ruff 检查，BK 新增工具与测试通过 ESLint 和 Prettier 检查，未新增用户可见文案或 i18n key。上述实现提交均已推送到 `origin/codex/unify-public-knowledge`。

BE 的删除决策以仓库历史核验为前提：共同基线不包含公共知识库实现，旧分库和迁移代码只存在于本 PR 的开发提交，没有 tag、release 或正式远端分支构成兼容承诺。若以后出现外部分发反证，应另行提供显式、离线、先备份的迁移工具，不恢复启动时自动探测和改写。

## 第九轮：分页审查补充与剩余边界

本轮纠正一次审查收集错误：PR 当前共有 117 个 review threads，前 100 个均已解决，但第二页 17 个线程中仍有 16 个未解决。今后关闭轮次必须通过 `reviewThreads.pageInfo.hasNextPage` 遍历至末页；单页返回零条未解决线程不构成“全量清零”证据。CI、自动审查 check 和 `reviewDecision` 也不能替代 conversation 分页结果或 review-body outside-diff 核对。

16 个线程经当前代码复核后分为两组：

| 状态 | 评论 | 结论与证据 |
| --- | --- | --- |
| 已实现待关闭 | `r3850069845`：indexer 第二次作业枚举阻塞事件循环 | BI 已由 `ab4b9073d` 将轮后 fresh enumeration 移入工作线程，并覆盖两次枚举线程身份。 |
| 已实现待关闭 | `r3850069853`：显式目标素材候选被共享池挤出 | BH 已由 `ab4b9073d` 为 primary/fallback 分配独立 lexical、semantic 与 RRF 预算。 |
| 已实现待关闭 | `r3850069859`：自动旧布局迁移原地修改源库 | BE 已由 `bcbabbf29` 删除 `legacy_layout.py`、启动调用和迁移测试，旧目录不再被扫描或打开。 |
| 已实现待关闭 | `r3850069865`：mutable state 容量计数绕过 identity | BF 已由 `53ddbf478` 固定 identity 容量信任根；不一致 state 隔离且准入始终采用 identity。 |
| 已实现待关闭 | `r3850069872`：disabled override 未规范化标题身份 | BG 已由 `15abf7d5f` 统一 override、entry key 和 rowid 映射的规范化身份。 |
| 仍成立 | 其余 11 条 | 按 BL–BV 实施；只有远端实现、精确反例测试和相邻失败语义均可见后才允许关闭。 |

### 修复单元 BL：discard 只接受可信的直接子作业目录

涉及：`knowledge/pack_jobs.py::discard_degraded_pack_job()` 接受 `job_id=".."`，可把 `.staging/..` 当成 degraded orphan 并递归删除知识库根目录的 P1 评论。

- 抽取 staged job ID 的单一严格解析器。公开 job ID 必须匹配生成器契约：合法 pack ID、一个分隔符和 12 位小写十六进制随机后缀；`.`、`..`、绝对路径、分隔符、空白、大小写十六进制和额外路径片段全部拒绝。
- 所有接收外部 job ID 的 get、cancel、discard 路径共用该解析器，不能继续以 `Path(job_id).name == job_id` 作为路径安全判断。
- destructive discard 在加锁后解析 `jobs_root` 与 `job_dir`；要求解析后的 `job_dir.parent` 精确等于解析后的 jobs root，并拒绝符号链接、junction 或解析到 root 外的目录。身份读取和 degraded 判定只能发生在路径约束通过之后。
- `shutil.rmtree()` 的目标只能是上述已验证直接子目录；不得对 root、`.creating-*`、锁文件或未知普通目录执行递归删除。失败保持目录原样并返回稳定失败，不尝试扩大清理范围。

验收：对 `..`、`.`、绝对路径、正反斜杠、合法前缀加路径尾巴、symlink/junction 和 jobs root 本身逐一调用 discard，知识库数据库、注册表和其他作业字节级不变；合法 degraded job 仍可显式删除，正常 job 仍不可 discard。

### 修复单元 BM：删除未发布的 attributed-entry schema 自动迁移

涉及：`KnowledgeStore._migrate_legacy_entries()` 丢弃并重建 entries 表后没有回填 FTS 的 P1 评论。

- 该 attributed-entry schema 与旧布局一样只来自本 PR 的中间开发提交；共同基线、tag、release 和正式远端分支均没有公共知识库。沿用 BE 的发布边界，删除 `_migrate_legacy_entries()`、`_repair_legacy_source_tags()`、`.legacy.bak` 写入和相应迁移测试，不继续完善未发布格式的 FTS 回填。
- 已存在 entries 表时，初始化先只读核对最终五字段形状。若存在旧 `aliases` 等 attributed schema，抛出结构化 `unsupported_knowledge_schema` 并停止；不得 drop、rename、backup、更新 schema marker 或创建空 FTS。
- 空数据库仍可原子创建最终 schema；已经是最终 entries 形状的统一数据库继续执行当前受支持的派生表/marker 校验。本单元不删除正式 schema 内必要的同版本派生数据修复。
- 若以后出现外部分发反证，只能另做显式、离线、先备份并在 staging 中转换的工具；不得恢复服务启动时自动原地迁移。

验收：旧 attributed fixture 启动失败且源文件 hash、mtime、schema、FTS 和 marker 全部不变；新安装空目录与最终 schema 正常；代码和文档中不再出现 attributed 自动迁移或 `.legacy.bak` 承诺。

### 修复单元 BN：status 的 degraded envelope 是可消费的健康结果

涉及：前端通用 `request()` 对任何 `ok: false` 抛错，导致后端刻意返回的 structured degraded status 无法展示的 P2 评论。

- 不放宽 mutation 和普通查询的失败语义。仅 status API 使用专用判别：当响应包含合法 `status.status="degraded"` 健康对象时返回该对象；缺少 status、结构非法、token 失败或其他 `ok: false` 仍抛 `KnowledgeApiError`。
- 后端 unavailable status 补齐稳定展示字段：`name`、`integrity_ok=false`、`available=false`、`error_type/error_code`；前端类型把 available 与诊断字段建模，不以 stale cache 覆盖当前 degraded 事实。
- status 仍参与 BK 的 epoch/resource gate；迟到 degraded 响应不能覆盖更新世代，迟到异常也不能显示旧错误。
- 复用现有 degraded/ready 文案，不新增 i18n key；若最终确需新增可见诊断文案，必须同步 8 个 locale。

验收：service 初始化失败时管理页展示 degraded 而非 generic load failure；普通 packs/import 的 `ok:false` 仍拒绝；非法 status envelope、旧响应竞态和 token refresh 回归均通过。

### 修复单元 BO：一次查询生成全部 pack chunk 状态

涉及：`list_installed_packs()` 为每个 pack 调用一次 `source_chunk_status()`，形成逐包 SQLite 打开和 `json_each` 扫描的 P2 评论。

- 在 `KnowledgeStore` 增加 batch source status 查询。将去重后的 source tags 作为一个 JSON 参数传给 SQLite `json_each(?)`，与 entry tags 和 chunks 一次 join/group，避免 SQLite 参数数量上限和逐包连接。
- 单次只读连接返回 `source_tag -> {chunks_total, chunks_ready, chunks_prebuilt_only}`；无命中 source 在内存补零。重复 registry source、空 source 和非 source tag 在查询前稳定过滤。
- `list_installed_packs()` 先解析可信 registry，再进行一次 batch 查询并合并；不得因 pack 数量增加而增加数据库连接数。数据库不可用时保持该展示接口既有降级语义，不把本单元偷偷变成容量准入策略。

验收：1、100 和数千个一词条 pack 均只打开一次数据库并执行一次 grouped status 查询；各 pack ready/missing 结果与旧逐包实现相同；重复 source、零 chunks、prebuilt-only 和损坏 registry 回归通过。

### 修复单元 BP：端到端请求超时覆盖写锁预算

涉及：Main Server mutation 最多等待 30 秒跨进程锁，但 Bridge 与管理端当前都在 15 秒放弃请求，可能出现“前端失败、后台稍后提交”的 P2 评论。

- 抽取知识 mutation lock timeout 常量，消除 Bridge 对内部 30 秒预算的隐式猜测。POST 转发的 connect timeout 保持短值，read/write/overall timeout 必须大于锁预算并留出受限 body 读取、校验与提交余量；GET 继续使用短超时。
- 同步调整管理端 `executeRequest()`：知识 POST 的客户端预算必须严格大于 Bridge POST 预算，GET 保持 15 秒。不能只放宽中间一跳，让浏览器先取消。
- 超时仍返回稳定 unavailable/timeout 错误，不把不确定结果宣称为回滚成功。import 依靠 staged job identity 保持重试可诊断；remove/policy mutation 在超时后由下一次 packs/status 刷新核实最终事实。
- 不用无限超时，也不在本轮引入新的 durable RPC 系统；若受限 mutation 在新预算内仍可能无界运行，应另立异步作业契约。

验收：锁竞争超过 15 秒但小于 30 秒时 POST 最终返回真实提交结果，不出现 502；GET 仍快速失败；Bridge 和浏览器预算顺序有静态测试，超出最终预算后的状态刷新可观察实际结果。

### 修复单元 BQ：缺失 term role 规范为空列表

涉及：`terms.get(role, ())` 的默认 tuple 随后被 list 校验拒绝，导致省略 terms 或仅提供一个 role 的合法 pack 无法导入的 Major 评论。

- 缺失整个 `terms` 字段规范为 `{}`，缺失 `alias` 或 `recognition` 规范为 `[]`；规范后再执行现有数量、字符串类型和 bounded UTF-8 累计预算。
- 只有缺失可默认。显式 `null`、tuple、string、object、非字符串数组和未知 role 继续严格拒绝；不自动修复调用方显式提供的错误类型。
- 内存模型仍输出两个 tuple role，canonical artifact 的确定性序列化与 hash 不因“缺失”和“显式空数组”产生身份漂移。

验收：无 terms、仅 alias、仅 recognition 和两个空列表均通过且得到等价规范模型；显式错误类型、64 项/32 KiB 边界和 canonical round trip 均通过。

### 修复单元 BR：安装前取消订阅是成功的 unsubscribe

涉及：active subscription 在 resolving/downloading/verifying 阶段被取消后仍强制查找本地 pack/job，最终返回 subscription_not_found 且不报告取消的 P2 评论。

- task 创建时持久化本次请求的 `requested_pack_id`；unsubscribe 必须先验证 package ID 对应的 active task 与 claimed pack ID 一致，不能仅凭 package ID 取消其他身份。
- 若取消发生在 `pending/resolving/downloading/verifying`，这些阶段尚未调用 Main Server mutation；worker 清理完成后直接返回 `{ok:true, cancelled:true, removed:false}` 并 best-effort 上报 unsubscribe，不再要求本地 artifact 存在。
- `installing/indexing/completed` 阶段可能已有 durable job 或 pack，不走上述快捷成功路径；继续解析可信 resolved identity，并通过严格 remove/job 状态核实，避免把仍可能提交的 mutation 当成已撤销。
- 重复 unsubscribe 对已成功取消且无 artifact 的同一 task 返回幂等成功；身份不匹配、不可验证 ownership 和真正不存在且从未有 active task 的请求保持现有失败语义。

验收：在 descriptor 返回前、下载中、校验中取消均成功且无本地 job/pack；错误 claimed pack 不得取消；installing 竞态不能遗留已订阅 pack 却报告 removed；上报失败不改变本地成功结果。

### 修复单元 BS：自动会话选择也使用分素材候选池

涉及：BH 只保护显式 target；`aselect_conversation_materials()` 的无 target 双类型搜索仍共享候选池，knowledge 可挤出全部 corpus 的 P2 评论。

- 不改变所有普通无 target 搜索。为自动会话选择增加明确的 per-material candidate reservation 模式，仅该调用在请求 knowledge/corpus 配额时按 material type 拆分 source pools。
- lexical、prepared semantic 与 RRF 对每个素材池分别保留固定候选预算；query embedding 仍只生成一次，source allowlist、deadline 和 disabled 集合在各池一致应用。
- 各池结果保留可比较的原始证据，交给现有会话选择器按 knowledge/corpus quota 分配；任一素材无合格结果时允许另一类型按现有规则补足，但不得在候选截断前相互驱逐。
- 显式 target 继续走 BH primary/fallback；普通单类型或不要求素材配额的搜索保持当前共享排序，避免全局相关性漂移。

验收：BM25-only、semantic-only 和融合模式下，至少 24 个更高 knowledge 候选不能让一个合格 corpus 从自动会话 quota 消失；反向 crowding、allowlist、deadline、disabled 和单类型排序均回归。

### 修复单元 BT：容量准入读取失败必须 fail closed

涉及：`KnowledgeStore.community_usage()` 捕获数据库错误并返回全零，stage admission 可在锁定或损坏时错误接受超限 job 的 P2 评论。

- 为 `community_usage()` 增加与其他 store 统计一致的 strict 模式；展示/诊断调用可保留 fail-soft 零值，所有容量和替换扣减计算必须使用 strict 模式。
- `_ensure_community_capacity()` 读取总量或任一 replacement source 失败时转换为稳定 `knowledge_capacity_unavailable`/registry admission error，禁止创建 `.creating-*` 或公开 job 目录。
- 不使用缓存零值，不在读取失败时假设数据库不存在；只有路径确实不存在才按空安装处理。容量证据必须来自同一受信写锁窗口，避免读取后到 staging 之间被另一个进程修改。
- 锁恢复后允许调用方显式重试；不得后台自动补 stage，也不得把失败请求晚提交。

验收：locked、corrupt、marker conflict 和临时 I/O failure 均拒绝 staging 且目录无变化；数据库恢复后重试使用真实 installed usage；展示 status 的既有 degraded/fail-soft 行为不被容量严格模式误改。

### 修复单元 BU：维护重建遵守全局 ready-vector 上限

涉及：`--rebuild/--full` 循环持续生成本地向量，可能超过 20,000 ready 上限并使 snapshot 永久 truncated 的 P2 评论。

- 维护脚本与后台 indexer 共用 `MAX_READY_VECTOR_CHUNKS`，不得复制第二个数值。每轮在选择 work 前读取全库 ready 数，`work_budget=min(batch_size, cap-ready)`；预算为零立即停止。
- prebuilt 与 local ready 向量共同计入上限。`--rebuild` 保留的 ready 向量先占预算；`--full` 若重置派生数据，则按重置后的真实 ready 数重新计算，不能预先假设为零。
- 因 cap 停止时结果明确标记 `capacity_limited`，保留 eligible remaining 供诊断；不得错误报告 complete，也不得继续生成一个越界 batch。
- snapshot 上限继续作为防御性 fail-closed，不以提高 snapshot cap 掩盖维护脚本越界。

验收：已有 19,999 ready 时最多新增 1；恰好 20,000 时不调用 embedding；prebuilt+local 混合计数正确；cap 停止、普通完成、失败重试和 `--full` 重置语义均可区分。

### 修复单元 BV：随机素材排除使用共享规范化 entry key

涉及：disabled 集合保存规范化标题，但 `sample_entries_by_tag()` 以展示标题构造 raw tuple，导致 Apollo 等词条仍可被随机抽到的 P2 评论。

- 随机采样排除判断改用 `catalog_overrides.entry_key(entry)`；不得在 store 内再次手写一套 title normalization。该依赖只指向模型与规范化 helper，不引入 store 循环依赖。
- reservoir sampling 的完整候选遍历、均匀性和 limit 语义保持不变；只在增加 eligible count 前排除规范化 disabled key。
- 展示标题继续保留原文，override 文件继续保存规范身份；本单元不改变返回条目内容。

验收：禁用 `Apollo`、`Straße` 和 NFKC/空白变体后，在确定性 randrange 与多轮采样中均永不返回；重新启用后恢复候选资格；超过 100 条的完整 reservoir 公平性测试保持通过。

## 第九轮实施顺序与关闭条件

1. 先提交本节设计，随后只回复并 resolve 表中 5 个已有远端实现和反例测试的线程；11 个仍成立线程保持 open。
2. 第一实现提交处理 BL 与 BM：先消除递归删除 P1，再删除未发布 attributed schema 自动迁移。两者不与功能优化混交。
3. 第二提交处理 BQ、BV、BN：修复确定性输入/身份和 degraded 展示；若无新用户文案，不改 i18n。
4. 第三提交处理 BT、BP、BR：容量、超时和取消统一遵循 fail-closed 或可证明的阶段边界，重点覆盖“请求失败但后台晚提交”反例。
5. 第四提交处理 BO、BS、BU：批量查询、自动素材候选预算和维护向量上限分别验证，不借性能修复改变普通搜索排序。
6. Python 必须使用 3.11 项目环境；运行相关 pack/job/store/service/indexer/router/maintenance 回归、Ruff、前端 API/request gate 测试、`vue-tsc --build`、i18n 校验与分页 conversation 核对。

关闭一条线程必须同时满足：远端提交包含当前行对应实现；精确反例测试在当前头提交通过；相邻失败语义有负例；线程内回复说明提交和测试；GraphQL resolve 成功。最终清零检查必须遍历全部 117+ threads，并单独检查 review body 的 outside-diff 评论。CI 全绿或自动 review check 通过本身不满足关闭条件。

## 第九轮实施结果

BL–BV 已按上述顺序拆为四个实现提交并推送到 `origin/codex/unify-public-knowledge`：

| 提交 | 修复单元 | 实施证据 |
| --- | --- | --- |
| `b663f327a` | BL、BM | 外部 job ID 只接受合法 pack ID 加 12 位小写十六进制后缀，discard 在删除前验证真实直接子目录；未发布 attributed schema 自动迁移、备份和 source tag 修复已删除，旧 schema 只读失败且源文件 hash、mtime、表结构和数据保持不变。 |
| `2f7b2c1ba` | BQ、BV、BN | 缺失 term role 规范为空列表，显式错误类型继续拒绝；随机素材排除统一使用规范化 `entry_key`；只有字段完整的 degraded status envelope 可被 status API 消费，mutation 和非法 envelope 仍抛错。 |
| `2b300a8da` | BT、BP、BR | 容量准入使用 strict usage 并在不可读时 fail closed；30 秒写锁、40 秒服务转发和 45 秒浏览器 mutation 预算形成严格层级，GET 保持 15 秒；subscribe 强制保存 pack identity，pre-install 取消幂等成功，错误身份和 installing 后路径继续 fail closed。 |
| `f67093f4a` | BO、BS、BU | pack chunk 状态使用一个 JSON 参数和一次 grouped SQLite 查询；自动会话选择为 knowledge/corpus 分配独立 lexical、semantic 与 RRF 候选池且 query embedding 仍只准备一次；维护重建逐轮按全库 ready 数扣减 20,000 上限并显式返回 `capacity_limited`。 |

精确反例包括：`..`/分隔符/大写后缀 job ID 不得删除根目录；旧 attributed fixture 启动失败但字节和 mtime 不变；无 terms、单 role 与显式错误类型；NFKC/casefold disabled 素材永不被抽样；损坏 status envelope 和 mutation 失败不被放行；容量读取失败不公开任何 job；锁预算顺序；pre-install 取消的幂等与身份反例；多 pack 只进行一次 batch status；30 个更高 knowledge 候选不能挤掉 corpus；19,999 ready 只允许新增 1，20,000 ready 不调用 embedding。

最终相邻回归：Python 3.11 下 282 项通过；前端 Knowledge API 12 项通过；`vue-tsc --build` 通过；8 个 locale 共 756 个 i18n key 无缺失或占位符不一致；相关 Python 文件 Ruff 通过。未新增用户可见文案或 i18n key。

## 第十轮：作业根信任、代理拓扑与会话能力等价

本轮以远端头提交 `6b14e479f` 为代码基线。通过 PR timeline 的 5 个分页读取到 119 个带解决状态的 review thread，其中 108 个已解决、11 个未解决；REST 同时返回 188 条行级 review comment。11 个未解决线程中，CodeRabbit 与 Codex 对“旧预安装取消覆盖后续成功订阅”给出了两条等价评论，因此本轮归并为 10 个独立修复单元。计数是 2026-08-25 的历史快照，后续复审必须重新分页，不能把本节数字当作当前值。

| 线程 | 来源与级别 | 单元 | 结论 |
| --- | --- | --- | --- |
| `discussion_r3851340737` | CodeRabbit Major / Security | BW | `.staging` 根可以是指向外部目录的链接，成立 |
| `discussion_r3851603140` | Codex P1 | BX | Main Server 代理后的合法 Origin 被按插件端口拒绝，成立 |
| `discussion_r3851552914`、`discussion_r3851603151` | CodeRabbit Major + Codex P1 | BY | 旧取消任务可覆盖较新的成功订阅，同一根因 |
| `discussion_r3851603164` | Codex P1 | BZ | voice transcript 无 lookup 能力，成立 |
| `discussion_r3851153779` | Codex P2 | CA | 异步状态写与取消写未线性化，成立 |
| `discussion_r3851603145` | Codex P2 | CB | 外层 30 秒代理早于内层 40 秒 mutation 截止，成立 |
| `discussion_r3851603172` | Codex P2 | CC | 失败用户轮次的 route owner 可污染主动轮次，成立 |
| `discussion_r3851603155` | Codex P2 | CD | 普通服务构造重复取得 legacy migration 写锁，成立 |
| `discussion_r3851153804` | Codex P2 | CE | disabled 总量无界放大每次候选窗口，成立 |
| `discussion_r3851153790` | Codex P2 | CF | recognition exact 被较弱 substring 分支抢先返回，成立 |

### 第十轮新增不变量

1. 任何会写入或递归删除 staged job 的路径，都必须先证明 `.staging` 是知识根的真实直接子目录，而不是符号链接、junction 或其他 reparse point。
2. `cancelled`、`active`、`failed`、`degraded` 等终态只能在持有同一状态锁并读取最新快照后决定；迟到的异步写不得用旧快照复活终态。
3. 同一 mutation 调用链的超时预算必须严格满足“内层提交边界 < 外层转发 < 浏览器等待”，且只在 knowledge mutation 路径扩大预算。
4. 文本与语音不要求使用相同注入机制，但都必须拥有调用公共知识 lookup 的可达路径；普通文本仍由 host 确定性解析，Realtime 会话由 lookup-capable tool 提供能力。
5. `KnowledgeService` 构造只绑定路径和内存状态，不做迁移、不等待文件锁、不打开 SQLite；迁移只能由显式生命周期入口触发。
6. 自动检索的工作量只由请求预算和固定上限决定，不得与 disabled catalog 的总基数线性增长。

### 修复单元 BW：staging 根目录必须是受信真实目录

涉及：`knowledge/pack_jobs.py` 的 `.staging` 根可链接到外部目录，随后 cancel 写状态或 discard 递归删除外部 job 的 Major 安全评论。

- 新增单一 `_validated_jobs_root()` 边界，在解析 job 子目录前检查 `.staging` 存在时是目录、不是符号链接，并在 Windows 上拒绝 directory junction / reparse point；不存在时只有明确的 staging 创建路径可以创建真实目录。
- 解析后的 `.staging` 必须满足 `resolved_root.parent == resolved_knowledge_root`，job 必须满足 `resolved_job.parent == resolved_root`。只验证 job 自身不是链接不再足够。
- cancel、discard、list、stage 和恢复入口共享该 helper；任何验证失败均 fail closed，不创建锁文件、不写 `state.json`、不清理 payload，也不尝试“修复”外部目标。
- mutation lock 锚定在已验证的知识根内；不能先沿不可信 `.staging` 取锁，再进行链接检查。进入锁后重新验证一次根身份，关闭检查与删除之间的替换窗口。
- 本轮不删除可疑链接。诊断只返回稳定的 registry/path-invalid 结果，由用户在应用外人工处理。

验收：`.staging` 指向外部目录、job 子目录是链接、Windows junction/reparse、检查后替换根目录四类反例均不得改变外部文件；真实直接子目录中的合法 cancel/discard/stage 保持可用；路径失败不得留下 `.creating-*` 或新锁文件。

### 修复单元 BX：本地 Origin 校验理解 Main Server 代理拓扑

涉及：打包 UI 从 Main Server 发出的 `/market/*` 请求保留浏览器 Origin、删除原 Host 后，被插件服务按插件端口拒绝的 P1 评论。

- `_require_local_bridge_token_access()` 继续要求 client、Host 和 Origin host 都是 loopback，继续拒绝远端 Market Origin、HTTPS、userinfo、路径、query 与 fragment；bridge token 校验保持不变。
- Origin 端口允许集合固定为 `{request_port, _main_server_port()}`：前者覆盖 Vite/插件服务直连，后者覆盖打包 UI 经 Main Server 代理。不得放宽为任意 loopback 端口，也不得信任 `X-Forwarded-*` 决定安全边界。
- `_main_server_port()` 继续动态读取 launcher 可能改写的实际端口；不能硬编码 48911。
- Main Server 代理仍删除原 Host，避免把浏览器 Host 伪装成插件服务 Host；本修复只让插件服务识别已知的第二个本地入口。

验收：插件端口直连 Origin 与 Main Server Origin 均通过；第三个 loopback 端口、远端域名、HTTPS、带凭据或路径的 Origin 均 403；`/market/pair-code` 和 knowledge POST 在打包代理链路可用，远端 Market 仍只能走 token exchange。

### 修复单元 BY：幂等取消只参考最新相关订阅尝试

涉及：同一 package 先预安装取消、再成功订阅、再 unsubscribe 时，旧 `preinstall_cancelled` 记录使调用方提前成功且不删除新包的两条重复评论。

- `_cancel_active_subscription()` 在没有 active worker 时先取得该 `package_id` 的最新 retained task，而不是先筛选“任意 cancelled task”。只有最新 task 自身是 `preinstall_cancelled=True` 时才允许幂等快捷返回。
- 最新 task 已 completed/installed、进入 installing/indexing，或已持有 `resolved_pack_id` 的情况下，旧取消记录完全不可见；流程必须继续解析 durable pack 并调用 `packs/remove`。
- 快捷返回前仍校验 `requested_pack_id` / `resolved_pack_id` 与 claimed pack identity；身份不一致继续稳定失败，不能因幂等语义放宽 ownership。
- task 新旧以现有插入顺序与 `created_at` 双重约束。若时间字段损坏或顺序无法证明，禁用快捷成功并走 durable lookup，失败方向必须是多做一次可信核实而不是漏删。
- 成功取消、重新订阅、再取消的 Market report 只在实际最新状态完成后发送；不得报告远端 unsubscribed 而本地 pack 仍 active。

验收：`cancel → resubscribe completed → unsubscribe` 必须调用一次 `packs/remove`；连续重复取消同一个未安装 attempt 仍幂等成功；旧取消与新 installing 竞态、错误 claimed pack、损坏时间字段、task TTL 清理均有负例。两条远端线程使用同一实现和测试证据关闭。

### 修复单元 BZ：Realtime 会话获得公共知识 lookup 能力

涉及：host resolver 只运行在文本输入分支，而内置 `query_public_knowledge` 工具固定 `lookup_enabled=False`，导致语音请求没有公共知识 lookup 可达路径的 P1 评论。

- 保留文本模式现状：普通文本在响应前调用 `build_public_knowledge_turn_context()`，其工具只暴露 `sample`，避免一次请求既 host lookup 又重复发起 LLM lookup。
- Realtime/voice session 同步工具时，把 `query_public_knowledge` 注册为 `lookup_enabled=True`，schema 同时暴露 `lookup` 与 `sample`；handler 继续复用既有显式 lookup 预算、来源标注、禁用条目和 fail-soft 语义。
- 能力选择由最终 session 类型决定，而不是最初输入猜测。session 创建、text/voice rebuild 和语言切换后的 tool resync 必须原子替换 schema，不能让旧 session mode 的工具定义残留。
- 工具说明明确要求显式“查询本地知识/资料库”类语音请求使用 lookup；普通闲聊仍不强制工具调用。自动随机素材继续由 sample 提供。
- 不在 `handle_input_transcript()` 中直接运行 host resolver：Realtime provider 可能已经开始响应，迟到上下文会进入错误轮次。本轮用 session capability 达成语音可用性，不引入 response buffering 或新的 transcript gate。

验收：Realtime session 的 tool schema 含 `lookup`/`sample`，Offline text 只含 `sample`；语音显式知识调用能返回带来源的结果；text↔voice 重建后 schema 立即切换；lookup 超时、BM25 降级和空结果不阻塞音频响应；文本 host lookup 不发生重复工具调用。

### 修复单元 CA：异步 job state 写入与取消线性化

涉及：`_write_state_async()` 在线程中使用调用前快照直接覆盖 `state.json`，没有取得 cancel 所用状态锁的 P2 评论。

- 保留 `_write_state()` 作为“调用方已持锁”的底层原子写 helper；新增同步 `_update_state_locked()`，在 `mutation_lock(_state_path(job_dir))` 内重新读取最新 state、检查终态，再应用变更。
- `_write_state_async()` 改为 `to_thread(_update_state_locked, ...)`。锁等待与 JSON I/O 全部留在工作线程，事件循环只协调结果。
- 若锁内发现 `cancelled`、`active` 或 `degraded`，迟到的 validating/embedding/failed 写返回当前终态且不覆盖。是否允许 `failed` 后人工恢复继续由既有显式恢复入口决定，普通 worker 不复活。
- exception handler 写 failed 前也必须在锁内比较最新状态；若已 cancelled，只执行与 cancelled 兼容的 payload 清理并返回 cancelled，不再写 failed。
- 调用方不得依据写入前的 `state` 决定后续激活；必须使用 locked update 返回的新快照，并在进入 `_activate_job()` 后继续遵守其提交点规则。

验收：在三处 `_write_state_async()` 前后分别注入 cancel，终态始终为 cancelled 且 payload 不被错误复活；failed 与 cancel 竞争、prebuilt rejection 与 cancel 竞争、锁等待不阻塞 event-loop tick；正常 hybrid/BM25 激活结果不变。

### 修复单元 CB：mutation 超时预算覆盖完整代理链

涉及：打包 UI 的调用链为 browser → Main Server `/market` → plugin bridge → Main Server knowledge API；外层 Main Server 固定 30 秒，比内层 POST 40 秒更早超时的 P2 评论。

- 统一记录四层预算：跨进程 mutation lock 30 秒、plugin→Main knowledge POST 40 秒、Main→plugin 的 packaged knowledge mutation 代理 45 秒、浏览器 mutation 50 秒。每层至少给下一层 5 秒收尾余量。
- `app/main_server/web_app.py` 只对非 GET 的 `/market/knowledge/*` 使用 45 秒；普通 Market、OAuth、静态与 GET 请求保持原预算，避免一次知识修复扩大全部插件请求的资源占用。
- 前端 Knowledge API mutation gate 更新为 50 秒；GET 继续 15 秒。所有数值从共享 knowledge timeout 常量导入或由一个基础常量推导，禁止三处复制漂移。
- 下游已返回稳定业务错误时原样转发。真正的外层 timeout 返回稳定 504/`knowledge_mutation_timeout`，不伪装为连接失败 502，也不自动重试可能已提交的 mutation。
- 本单元不承诺任意失控 I/O 都能撤销；它保证正常锁预算内外层不会先放弃。超过最外层预算时，状态查询仍是唯一恢复事实来源。

验收：持锁 29 秒后 mutation 成功且浏览器不先失败；内层 40 秒前返回的错误被完整转发；普通 `/market/*` 仍使用原预算；GET 仍为 15 秒；超时后不自动重试，并可通过后续 status 查询确认是否提交。

### 修复单元 CC：route owner 只属于可证明的当前用户轮次

涉及：用户轮次 analyzer publish 失败后 scalar `pending_analyze_route_owner` 保留，下一次 proactive turn 没有覆盖它，导致无关分析被标成 `public_knowledge` 的 P2 评论。

- pending owner 从裸字符串改为至少包含 `request_id`/turn identity 与 owner 的结构；只有同一用户轮次的即时重试或其 session_end 收尾可以复用。
- 每次 turn_end 都显式赋值 dispatch owner。存在用户输入时从当前 message 规范化；真正 proactive turn 一律传 `None`，并清除不属于当前 turn identity 的旧 pending owner。
- publish 成功清除对应 pending；publish 失败只为同一 user turn 保留。新用户轮次、proactive turn、session rebuild 与 shutdown 都使旧 identity 失效。
- session_end 只有在 recent user evidence 与 pending turn identity 一致时携带 owner；不能仅因 scalar 非空就继承。

验收：public-knowledge user turn publish 失败后紧接 proactive turn，后者 owner 必为 `None`；同一 user turn 的 session_end 重试仍可携带 owner；新用户轮次、图片用户轮次、avatar-drop 跳过、callback turn 和 shutdown 均无跨轮污染。

### 修复单元 CD：legacy policy migration 离开服务构造

涉及：`KnowledgeService.__init__()` 每次看到数据库与 `packs.json` 都调用 migration，并取得 registry 跨进程写锁；聊天自动检索和管理 GET 因而可能等待 30 秒的 P2 评论。

- 删除构造函数内的 migration import、文件探测与调用。构造函数只规范路径并初始化内存字段，可在事件循环外或内被廉价调用，但都不产生 I/O。
- 在公共知识 runtime 启动/维护入口增加显式 `initialize_knowledge_runtime()` 阶段，并通过 `asyncio.to_thread()` 执行一次 legacy policy migration；脚本入口按需显式调用，不借普通查询隐式迁移。
- 进程内按 resolved database path 记录“成功完成”的 one-shot；并发初始化共享同一 future/lock。失败不写成功哨兵，可在下一次显式初始化重试，但普通 `open_knowledge()` 永不代为重试。
- 新安装 pack 已写完整 policy 元数据，不需要运行 legacy migration；运行期后来创建 registry 也不能触发每次查询迁移。
- migration 失败进入现有 health/degraded 诊断，不中断 BM25 只读可用性；不得为了 fail-soft 把迁移重新塞回构造函数。

验收：构造 `KnowledgeService` 不调用 `Path.is_file()`、SQLite 或 mutation lock；并发启动只迁移一次；失败后显式重试可成功；聊天 lookup 与管理 GET 在 registry 写锁被占用时不因 service construction 等待；legacy fixture 仍在启动阶段正确补 policy。

### 修复单元 CE：disabled 排除的候选开销固定有界

涉及：`candidate_limit = max(12, limit * 4) + len(disabled)`，当 disabled catalog 接近 20,000 时每次自动检索会物化大部分数据库的 P2 评论。

- candidate limit 不再加 disabled 总量。初始窗口仍由请求 limit 推导；过滤后不足时允许按固定倍数增大窗口，但 lexical/FTS/LIKE 每路都受同一硬上限与已有 deadline 约束。
- 第十轮硬上限先定为每路 128 个 row，作为 3 条自动上下文结果的防御预算；后续若有质量证据可改共享常量，但不能按 catalog 总量动态扩张。
- 每轮扩大前检查 deadline；达到上限后允许少返回或空返回，不在后台继续扫描。自动上下文的失败方向是“少知识”，不是阻塞后续 turn。
- dedupe、allowed source、material type 和 disabled `entry_key` 语义不变。管理搜索不复用自动检索的 128 上限，避免改变用户显式分页浏览。
- 若未来改为 SQL 侧排除 rowid，必须处理 SQLite 参数上限与 override revision；本轮不为性能评论引入临时表或第二份 disabled 身份缓存。

验收：20,000 个 disabled 条目时三类查询 limit 均不超过 128；大部分 disabled 不匹配时不增加工作；前 128 个候选全 disabled 时有界返回空；deadline 到期不继续 worker 扩窗；普通无 disabled 排序与 allowlist 回归保持。

### 修复单元 CF：exact recognition 优先于 substring

涉及：query 与 recognition term 完全相等，同时又是 title/alias 子串时，850/800 分支先于 recognition exact 900 返回的 P2 评论。

- 评分顺序固定为：title exact 1000、alias exact 950、recognition exact 900、title substring 850、alias substring 800、recognition substring 780、tag substring 700、FTS fallback。
- exact comparison 继续使用现有 Unicode folded/normalized surface，不新增第三套大小写或 NFKC 规则；只调整优先级，不改变分值。
- 一个 entry 命中多个条件时取上述最高优先级；小 limit 截断必须发生在正确评分之后。

验收：recognition exact 同时为 title/alias substring 时得 900；title/alias exact 仍分别保持 1000/950；recognition substring 仍低于 alias substring；大小写、NFKC、混合脚本和 limit=1 排名均有回归。

## 第十轮实施顺序与关闭条件

1. 首个提交只归档本节与设计索引，不提前回复或 resolve 任何线程。
2. 第一实现提交处理 BW，单独验证外部目录绝不被写删；该安全边界不与普通重构混交。
3. 第二实现提交处理 CA，统一异步 state 写锁与终态守恒；必须覆盖 cancel 竞争后再进入后续实现。
4. 第三实现提交处理 BX 与 CB：同一代理拓扑内一起修正可信 Origin 和四层 timeout，但安全 allowlist 与超时常量分别测试。
5. 第四实现提交处理 BY；两个重复线程共享一次实现提交和“取消→重订阅→再取消”反例。
6. 第五实现提交处理 BZ 与 CC：分别验证 session tool capability 和 analyzer route-owner 生命周期，不把语音 lookup 与路由所有权耦合成一个 helper。
7. 第六实现提交处理 CD；构造函数 I/O 清零与启动迁移 one-shot 必须同时完成，禁止留下临时双路径。
8. 第七实现提交处理 CE 与 CF；候选工作上限和评分优先级各自有独立回归，不借性能修复改变既有分值。
9. Python 回归只用项目 Python 3.11 的 `uv run pytest`，优先扩展现有 pack job、retrieval、service、market bridge、knowledge market、streaming/cross-server 测试文件；如确需新建 test 文件，实施前单独说明理由。同步运行相关 Ruff、前端 Knowledge API 测试、`vue-tsc --build` 与 `git diff --check`。

关闭条件：每个线程对应的实现已推送到 PR 远端，精确反例与相邻负例通过，线程回复包含提交和测试证据，并成功 resolve。BY 的两条重复线程必须分别回复和关闭。最终核对必须重新遍历全部 timeline/GraphQL 分页，确认 unresolved 为 0，并单独检查 CodeRabbit review body 的 outside-diff 评论；119/108/11 仅是本轮开始时快照。第十轮实施完成后再追加提交矩阵和最终回归结果，并把文档头部及索引改为“第一至第十轮均已实施”。

## 第十一轮：制品身份、回滚完整性与评测轮次隔离

第十轮实现推送后重新分页得到 11 个未解决线程：其中 6 个是第十轮尚待证据回复的原线程，1 个是已经由 BW 覆盖的 staging 根重复评论，另有 4 个新问题成立。本轮只处理新增边界，不重新打开已经通过回归的第十轮实现。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3853190946` | CG | staged artifact 只核对 chunk 数，完整容量身份未绑定，成立 |
| `discussion_r3853190949` | CH | registry source tag 未绑定 pack ID，成立 |
| `discussion_r3853190959` | BW duplicate | 当前分支已拒绝 staging root symlink/junction/reparse，重复评论 |
| `discussion_r3853190966` | CI | rollback snapshot 的宽松读取会把读取失败折叠为空快照，成立 |
| `discussion_r3853190974` | CJ | live 质量评测会接收无 request ID 的旁路轮次，成立 |

### 修复单元 CG：staged artifact 绑定完整容量身份

- `identity.json` 中的 `entries_total`、`chunks_total` 与 `content_bytes` 都是准入时不可变证据；处理前必须从当前 artifact 重新执行同一套 preflight 并逐项相等比较。
- 完整比较必须发生在构建 staging 数据库与激活之前；任何字段缺失、损坏或不一致都进入稳定拒绝终态，不得以重新计算值扩大既有准入额度。
- 保持现有 hash、pack ID 与订阅身份检查；容量身份是补充约束，不替代制品内容身份。

验收：只增加 entry、只增加 content bytes、保持 chunk 数不变的篡改均在首次数据库 mutation 前失败；未改制品仍可恢复处理。

### 修复单元 CH：registry source tag 必须由 pack ID 唯一导出

- 读取 `packs.json` 时逐项验证 key 是合法 pack ID，且 `source_tag` 严格等于 `source:community.<pack_id>`；不得信任任意 parseable dictionary。
- 损坏映射 fail closed，删除、policy mutation 与列表读取都不能借错误 tag 操作另一个包。
- 写入路径继续只使用 canonical source tag，读取校验与写入规则共享同一 helper，避免两套合法性定义漂移。

验收：A 的 registry 行指向 B 的 source tag 时，删除 A 与修改 A policy 均在数据库 mutation 前失败，B 的 entries、chunks、vectors 与 registry 字节保持不变。

### 修复单元 CI：替换前 rollback snapshot 必须完整可证明

- `_snapshot_source()` 使用 strict entries/vector 读取；malformed row、SQLite/transient read failure 与不完整向量记录必须显式失败，不能折叠成空集合。
- snapshot 成功是首次替换 mutation 的前置条件。不能证明旧来源完整状态时保留旧包并中止安装。
- rollback 仍可恢复真实空来源；只有 strict 读取成功得到的空集合才是可信空快照。

验收：entries 读取失败、vector 读取失败和 malformed row 都不调用 replace/import/registry write；旧包数据库与 registry 保持不变。正常替换后的后续失败仍按完整 snapshot 回滚。

### 修复单元 CJ：live 质量评测只消费目标 request

- 一旦 `expected_request_id` 已设置，文本响应、错误与完成事件都必须携带完全相同的 request ID；无 ID 事件不再作为兼容事件接收。
- proactive、callback 与其他并发请求不能追加目标 case 文本，也不能提前终止其 latency 计时。
- 非 live fixture 路径与尚未建立 expected ID 的握手阶段保持现状。

验收：目标响应前后插入无 ID proactive 文本/turn-end、错误 ID callback 与正确 ID 事件，最终只记录正确 ID 文本并由正确 completion 结束。

## 第十一轮实施与关闭条件

CG、CH/CI、CJ 分为三个原子实现提交；只扩展既有 pack job、pack registry 与 quality evaluator 测试文件。全部实现推送后，对 5 个新增线程分别回复对应提交和精确反例；BW duplicate 使用第十轮既有提交证据关闭。随后重新分页确认整个 PR unresolved 为 0，再记录最终提交矩阵和回归结果。

## 第十二轮：管理搜索与只读超时契约

第十一轮完成文档推送后，复审新增两个有效线程：管理端 `search_page()` 错误复用了自动检索的 128 候选硬上限，plugin→Main bridge 又把 GET timeout 标成 mutation timeout。两项由提交 `0e033249f` 一起修复：自动上下文继续使用 128 防御上限，管理搜索显式使用 10,101 候选上限以覆盖最大 offset；GET 返回 `knowledge_request_timeout`，只有 mutation 返回 `knowledge_mutation_timeout`，两者均不自动重试。

验收：管理端稳定窗口测试同时断言 result limit 与 candidate cap 为 10,101；自动 disabled/deadline 回归仍证明每路不超过 128；GET 与 POST timeout 分别返回正确 504 code。相关 service/store/hybrid/bridge 精确集合 87 项通过，随后统一相关集合仍为 278 passed、1 skipped。

## 第十至十二轮实施证据

| 提交 | 单元 | 实施结果 |
| --- | --- | --- |
| `182639596` | BW、CA | staging 根与 job reparse 全面 fail closed；可信根锁后复验；异步状态写锁内重读并保持终态 |
| `c96816ae1` | BX、CB | Main Server/插件双入口 Origin allowlist；30/40/45/50 秒 mutation 预算和稳定 504 |
| `bb39787d4` | BY | 只信任顺序与时间一致的最新订阅尝试，歧义回退 durable registry |
| `51f1fc2a5` | BZ | Offline 保持 sample-only，Realtime 按最终 session 类型获得 lookup/sample |
| `f50397fe8` | CC | analyzer owner 绑定用户 request ID，proactive/new turn 清除旧 owner |
| `4871b7cb6` | CD | 服务构造零迁移 I/O；显式 off-loop、共享、可重试 runtime initialization |
| `db432daeb` | CE、CF | 词法候选按 12/24/48/96/128 有界扩窗并贯穿 deadline；recognition exact 提前 |
| `324ea2493` | CG | prepare 与 activate 均重算完整 preflight 并核对 pack/capacity identity |
| `decb1d9a2` | CH、CI | registry key/source tag 强绑定；rollback snapshot 全部 strict 读取 |
| `2d10e7d89` | CJ | live evaluator 的 response/completion 必须精确匹配目标 request ID |
| `0e033249f` | CK、CL | 管理搜索使用独立深分页候选上限；GET 与 mutation timeout code 分离 |

最终相关回归使用项目 Python 3.11 执行 `uv run pytest`，结果为 278 passed、1 skipped；skip 仅因本机 Windows 无目录 symlink 权限，另有不依赖权限的 junction/reparse 模拟反例通过。前端 Knowledge API 13 项通过，`vue-tsc --build` 与相关 Python Ruff 均通过，`git diff --check` 通过。未新增测试文件，只扩展已有回归文件。

实现提交推送并逐条回复证据后，GraphQL 重新遍历 PR #2951 全部 reviewThreads 分页，最终 unresolved 为 0；第十二轮代码完成核对时远端 head 为 `0e033249f37c85f5d64604a477edb5e4b8c74d51`。该计数仍是 2026-08-25 的完成快照；任何后续复审新增线程必须重新分页处理。

## 第十三轮：首次导入、激活容量与市场订阅身份

第十二轮完成后，GraphQL 对 142 个 review conversations 完整分页得到 6 个未解决线程。核对远端 head `7ea7254bbb` 后，5 个问题成立；要求为缺少 `source_tag` 的旧注册表自动补值的评论与既有 CH、BE 发布边界冲突，不采纳其修复方向。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3853883535` | CM | 全新安装的 knowledge root 尚不存在时，首次 stage 在创建目录前失败，成立 |
| `discussion_r3853883550` | CN | ready-vector 快照与向量写回未共享同一提交锁，容量可竞态超限，成立 |
| `discussion_r3853883555` | CO | installing 阶段取消只中断 HTTP await，服务端 mutation 可迟到发布，成立 |
| `discussion_r3853883567` | CP | 暂存数据库宽松读取可把损坏快照当作零向量 hybrid，成立 |
| `discussion_r3853883571` | CQ | registry 允许重复 marketplace package identity，成立 |
| `discussion_r3853890854` | CR | 自动推断缺失 `source_tag` 会恢复未发布格式兼容并削弱 CH，不成立 |

### 修复单元 CM：首次写入创建并复验受信 knowledge root

- 只读路径继续把不存在的 knowledge root / staging root 视为空，不因 status 或 list 创建目录。
- `stage_pack()` 是首次允许创建目录的写入口：磁盘容量预检后，在任何 staging 锁文件或作业文件落盘前创建 knowledge root，并立即拒绝 symlink、junction、reparse、非目录或无法严格解析的路径。
- 创建后仍沿用 BW 的 staging 根复验：取得 registry/pack lock 前后都要求 `.staging` 是 knowledge root 的真实直接子目录；任何复验失败均不发布 job。
- runtime initialization 不为普通只读启动强制创建空知识目录；首次写入边界由 `stage_pack()` 单独拥有。

验收：全新空 app data 首次本地导入与 marketplace stage 成功；只读 list/status 不创建目录；knowledge root 或 staging root 是重解析路径时不向外部目标写入；并发首次 stage 只发布完整原子作业。

### 修复单元 CN：ready-vector 容量决策与写回共享数据库锁

- `ready_vector_chunks` 调用参数仅作为轮次调度提示，不再作为激活提交的最终事实；最终额度必须在 live knowledge database 的跨进程 mutation lock 内重新统计。
- hybrid 激活在同一 database lock 临界区中完成：严格读取 live ready 总量、减去被替换 source 的 ready 数、加入暂存完整向量数、选择 hybrid/BM25，并执行 `install_pack()`。容量判断与 prepared vector 写入之间不得释放该锁。
- 本地 embedding writeback、模型 stale/reset、维护脚本与激活统一使用同一 resolved database lock key。若 live 统计不可证明，激活失败关闭，不以旧轮次快照继续提交。
- 替换同 pack 仍只计算净增量；达到 20,000 可以提交，超过才降级 BM25。锁只覆盖 SQLite/registry commit，不覆盖模型推理或制品验证。

验收：19,999 ready 与并发一条本地 writeback 后，一向量 pack 不会提交为第 20,001 条；等量替换保持 hybrid；统计失败不提交；普通 BM25 激活和本地 batch 上限不变。

### 修复单元 CO：安装期取消等待 mutation 可观测完成

- Plugin Server 将 apply 请求作为独立、受强引用的 installation mutation task；取消外层订阅 worker 只停止用户任务，不取消或遗失该 mutation 的完成信号。
- `_cancel_active_subscription()` 在 `stage=installing` 时先取消 worker，再等待对应 apply 请求得到响应或稳定错误，随后才解析 durable ownership 并调用 `packs/remove`。
- 等待完成后不把订阅任务改回 completed，也不发送 subscribe report；remove 继续携带 provider/package/remote 三重身份，并依赖 Main Server pack-operation lock 取消刚发布的 staged job 或删除已激活 pack。
- pre-install 快捷取消保持现状；请求超时仍按“不确定是否提交”处理，但必须继续走 durable remove/reconcile，不能返回未安装成功假象。

验收：取消发生在请求已发送但 `stage_pack()` 尚未进入时，remove 必须在 apply 可观测完成后执行并清掉迟到 job；apply 成功、业务失败、连接失败与取消重复调用均无游离 task；错误 claimed identity 仍失败关闭。

### 修复单元 CP：暂存向量快照必须严格且完整

- accepted prebuilt job 激活前严格打开 staging `knowledge.db`；缺失、损坏、锁定、Schema 错误或读取失败不得折叠为零状态。
- 严格比较 `chunks_total`、`chunks_ready` 与 immutable identity / accepted index metadata；hybrid 要求三者一致且 ready vector records 数量完全相等。
- prepared records 必须继续由 strict importer 校验 content address、model、dimensions 和 bytes；任一缺口进入稳定 failed/degraded，不安装 hybrid，也不显示 100%。
- 明确的无预构建或已因额度拒绝路径仍可 BM25 激活；不得把“读取失败”解释成“确实没有向量”。

验收：删除、截断、锁定 staging database，以及伪造 accepted 后零行均不安装；完整快照仍 hybrid；明确 BM25 job 不要求读取向量。

### 修复单元 CQ：marketplace package identity 在 registry 内唯一

- `_load_registry()` 在逐 pack 完成结构、canonical source tag 与 subscription 字段校验时，收集非空 `(provider, provider_package_id)`；`plugin-market` 的同一 package ID 只能归属一个 pack ID。
- 重复身份使整个 registry fail closed，list、install/update、remove 与 policy mutation 都不得基于歧义数据继续。失败读取不改写原文件。
- 新订阅写入前也对候选 registry 做同一唯一性验证，防止不同 `pack_id` 的 marketplace 重订阅制造第二条歧义记录；同 pack 的版本更新不受影响。
- provider package ID 继续使用 ASCII 正整数规范；不同 provider 的相同字符串不冲突。

验收：两个 pack 使用同一 marketplace ID 时 list 返回既有降级结果、mutation 在数据库写入前失败且 registry 字节不变；同 pack 更新成功；不同 marketplace ID 和不同 provider 不误报。

### 修复单元 CR：拒绝未发布 registry source-tag 推断

- 维持 CH：registry key 是合法 pack ID，`source_tag` 必须严格等于 `source:community.<pack_id>`。缺失与错误值均是无法证明身份的损坏数据。
- 不从 key 自动补写 `source_tag`，不在只读 `_load_registry()` 中恢复本 PR 中间格式，也不新增迁移测试。开发期间旧数据通过删除并重新导入收敛。
- 现有同版本 metadata normalization 不能改变 source identity；若未来出现真实外部分发证据，沿用 BE 另立显式、离线、先备份迁移工具。

验收：缺失 `source_tag` 继续 fail closed，读取不改写文件；合法最终 registry 正常；远端评论回复引用 CH/BE 的发布证据并说明不采纳自动迁移。

## 第十三轮实施顺序与关闭条件

1. 先提交本节设计与索引，不用计划文本提前关闭线程。
2. 第一实现提交处理 CM、CN、CP：三者共同收紧 staged activation 的可信路径、严格快照和最终数据库提交锁，但分别保留精确反例测试。
3. 第二实现提交处理 CO、CQ：installation mutation 生命周期与 durable registry ownership 同时验证；不借取消修复放宽身份匹配。
4. 仅扩展现有 `test_knowledge_pack_jobs.py`、`test_knowledge_packs.py` 与 `plugin/tests/unit/test_knowledge_market.py`，不新建测试文件。
5. 使用项目 Python 3.11 的 `uv run pytest` 执行定向及相邻 knowledge 回归；运行相关 Ruff 与 `git diff --check`。本轮没有前端或用户文案变化，不需要 i18n 修改。
6. 实现与最终证据文档推送后，5 条成立评论分别回复提交和精确测试；CR 回复不采纳理由。随后 resolve 全部 6 条并重新分页核对。

关闭条件：CM 的全新安装成功且重解析负例不回退；CN 的最终 recount 与 install 位于同一 database lock；CO 的 remove 不可能先于已发出的 apply 完成；CP 的损坏 staging database 不产生 hybrid 安装；CQ 的重复 provider identity 在任何 mutation 前失败；CR 保持 CH 的严格身份边界。只有远端可见提交、通过证据和逐线程回复齐全后才能关闭。

## 第十三轮实施证据

| 提交 | 单元 | 实施结果 |
| --- | --- | --- |
| `9bc071d2f` | CM、CN、CP | 首次 stage 创建并复验受信 knowledge root；accepted staging database 使用 strict status/vector snapshot；live ready recount、净替换额度判断与 `install_pack()` 在同一 database mutation lock 内完成，旧轮次提示不再决定最终额度。 |
| `8e877fd56` | CO、CQ | apply 请求由独立 installation mutation task 持有，installing 取消等待其完成后才 remove；registry 读取与候选写入共用 marketplace package identity 唯一性校验，歧义在任何 source mutation 前失败关闭。 |

CR 没有代码迁移提交：缺失或错误 `source_tag` 继续由 CH 的 canonical identity 校验拒绝。该决定复用 BE 已核验的发布边界——旧 registry 只存在于本 PR 的未发布开发历史，开发数据应删除并重新导入，不进入生产自动迁移合同。

验证使用项目 `.venv` 的 Python 3.11.15 执行。CM/CN/CP 与 store 定向集合为 90 passed、1 skipped；CO/CQ 与 market/pack 定向集合为 81 passed；合并后的 pack job、pack registry、store、indexer、public service 与 market bridge 相邻集合为 208 passed、1 skipped。skip 仅因本机 Windows 无目录 symlink 权限，reparse marker 负例通过。相关 Ruff 与 `git diff --check` 均通过；没有新增测试文件、前端改动、用户文案或 i18n key。

## 第十四轮：最终向量额度、暂存订阅身份与管理操作闭环

第十三轮全部线程清零后，2026-08-26 的复审新增 5 个未解决线程。沿当前 PR head `7b3729acf` 的生产调用链核对后，5 个问题均成立；它们不是前一轮评论的重复项，而是已有边界在最终写回、失败语义和管理入口上的缺口。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3859036878` | CS | 本地推理与最终向量写回之间可被预构建激活占用额度，成立 |
| `discussion_r3859036882` | CT | disabled identity 解析失败被折叠为空集合，语义候选可能泄漏，成立 |
| `discussion_r3859036890` | CU | `subscription.json` 未绑定 immutable job identity，激活时可被删除或替换，成立 |
| `discussion_r3859036896` | CV | live 质量用例共享同一历史会话，答案与 latency 可被前序用例污染，成立 |
| `discussion_r3859036905` | CW | 管理页对订阅包调用普通删除，绕过 provider 退订与取消闭环，成立 |

### 修复单元 CS：本地向量写回执行锁内最终额度准入

- indexer 的轮次级 `ready_vectors` 只用于决定是否发起本地推理，不能授权最终写回；推理完成后的事实必须在数据库 mutation lock 内重新读取。
- ready 上限常量由 pack 激活与本地写回共享，禁止分别维护两个 `20_000`。写回取得与 prebuilt activation 相同的 resolved database lock 后，以 strict `chunk_status()` 计算剩余额度。
- 只写入 `min(有效返回向量数, 剩余额度)`。因最终额度不足而未提交的 chunk 保持 pending，不标为 failed，也不计为 stale writeback；下一轮在额度释放后仍可处理。
- 向量格式错误仍按既有 failed 语义记录；content hash 在推理期间变化仍按 stale writeback 处理。容量截断不能掩盖无效向量或改变其错误统计。
- strict recount 失败时不写任何向量并返回稳定 blocked/degraded 状态；不得使用推理前快照继续提交。

验收：19,999 ready 的本地 batch 在推理期间被另一条 prebuilt 向量占满后写入 0 条，总量保持 20,000；只剩一个额度的两向量 batch 只写一条，另一条 pending；额度释放后 pending 可继续；recount 失败、invalid vector 与 stale writeback 分别保持原失败语义。

### 修复单元 CT：disabled identity 解析错误必须失败关闭

- `entry_rowids_for_keys()` 必须能区分“成功解析且没有匹配”和“数据库、tags JSON 或规范化失败”；语义检索不能把两者都解释为空集合。
- 当 disabled 集合非空且 rowid 解析无法证明完整时，本次 semantic path 返回空结果，允许上层继续使用已经按 identity 过滤的 BM25，而不是泄漏禁用词条。
- 即使 rowid 预过滤成功，在 materialize candidate 后仍以规范化 `entry_key(entry)` 对 disabled 集合二次过滤，防止 snapshot/rowid 漂移或未来 resolver 退化绕过策略。
- allowed source tag resolver 的既有 fail-closed 方向保持不变；本单元不把检索错误升级成聊天请求异常。

验收：存在 disabled 条目时注入 SQLite、malformed tags 与 resolver failure，semantic 返回空且不包含禁用词条；可信空匹配仍可检索其他条目；正常 disabled rowid 在 top-K 前排除，并在候选加载后再次排除。

### 修复单元 CU：订阅元数据成为 staged job 的不可变身份组成

- staging 时先通过 `validate_subscription()` 生成 canonical subscription。`identity.json` 明确记录作业是否有订阅，并保存 canonical subscription bytes 的 SHA-256；市场作业还必须具有 `provider == plugin-market`、ASCII 正整数 `provider_package_id` 和非空 `remote_id`。
- 本地导入在 identity 中明确记录无订阅，不能通过后来添加 `subscription.json` 升格为市场作业；市场作业缺文件、不可读、格式损坏、摘要不一致或 immutable provider identity 改变均进入 degraded/orphan，不执行也不自动删除。
- prepare 与 activate 都重新验证 subscription 文件及 identity digest；激活只能使用已经验证并与 identity 绑定的 canonical dict，不能再次宽松读取原文件。
- `version`、`channel` 和 artifact/index digests 虽是可更新发布信息，但在单个 staged job 内同样不可变；更新必须创建新的 job，而不是修改已发布 job 目录。

验收：删除、截断、替换 subscription，改变 provider/package/remote、version 或 digest 都在 install 前失败；给本地 job 后加 subscription 不能改变归属；正常本地与 marketplace job 均可激活且 registry 保存 canonical metadata。

### 修复单元 CV：每个 live 质量用例使用独立新会话

- 每条 case 都建立自己的 websocket session，发送 `start_session` 且 `new_session=true`，等待 ready 后只发送该 case 的 request，再发送 `end_session` 并关闭连接。
- request ID 精确过滤继续复用 CJ；session 隔离解决对话历史污染，request ID 解决同一连接事件串扰，两者不能互相替代。
- 单个 case 启动、请求或结束失败只产生带 case identity 的稳定 evaluator 错误；不能静默复用上一 case 的 session。
- routing-only 与 direct-text-model 模式保持现状；本单元只改变 `--live` 的测量合同。

验收：两条 live case 产生两次 `new_session=true` start 和两次 end；第二条连接看不到第一条消息；request IDs 仍精确匹配；startup failure 不发送 case 数据。

### 修复单元 CW：订阅包删除必须经过 provider unsubscribe

- Knowledge Manager 根据已持久化 subscription 判断操作：本地包继续调用 `/packs/remove`；`plugin-market` 订阅包调用 Plugin Server `/market/knowledge/unsubscribe`，请求只携带数值 `provider_package_id` 和作为非权威提示的 `pack_id`。
- 前端 pack summary 类型补齐 `provider_package_id` 与 `remote_id`，但不自行用 `pack_id` 推断 provider package identity。缺失或非法 identity 时拒绝普通删除并展示已有稳定操作失败，不降级成不安全的本地删除。
- Main Server defense-in-depth：已安装 pack 含 subscription 时，未提供 expected provider identity 的普通 `/packs/remove` 必须拒绝；Plugin Server 的 unsubscribe 路径继续负责取消 installing worker、解析 durable ownership、携带三重身份删除并 best-effort 上报远端。
- 只有确认 Plugin Server unsubscribe 成功后前端才移除表格行；409/503/timeout 保留行并允许刷新事实状态，不自动重试可能已经提交的 mutation。

验收：本地包仍走普通 remove；订阅包只走 unsubscribe；缺失 provider package identity 不触发任一删除；直接调用通用 remove 删除订阅包被后端拒绝；installing、active、重复退订和 ownership mismatch 的既有市场回归继续通过。

## 第十四轮实施顺序与关闭条件

1. 首个提交只归档本节，不提前回复或 resolve 新线程。
2. 第一实现提交处理 CS、CT：共享容量常量、锁内 strict recount、容量截断 pending 语义和 disabled 双层 fail-closed 各有独立反例。
3. 第二实现提交处理 CU：不可变 job identity 绑定 canonical subscription，prepare 与 activate 共用一次严格解析结果。
4. 第三实现提交处理 CV、CW：live evaluator 每 case 新会话；前端按订阅类型路由，Main Server 增加防御性拒绝。两项分别测试，不共享业务 helper。
5. 只扩展现有 hybrid retrieval、pack job、quality evaluator、public router、Knowledge API/Manager 和 knowledge market 测试；不为本轮创建孤立测试文件。用户界面不新增文案 key，复用已有操作失败提示，因此无需 i18n 变更。
6. 使用项目 Python 3.11 的 `uv run pytest` 运行定向及相邻回归；运行 Ruff、前端 Vitest、`vue-tsc --build` 与 `git diff --check`。实现推送后逐条回复提交和精确测试证据，再 resolve 5 条线程并完整分页核对 unresolved。

关闭条件：最终 ready 向量数在所有 writer 竞态下不超过 20,000；disabled resolver 失败不泄漏；staged subscription 缺失或篡改不能改变归属；live case 没有跨用例历史；订阅包删除只能经过 provider unsubscribe。只有这些实现与回归在 PR 远端可见、线程内证据回复完成后，才允许关闭本轮评论。

## 第十四轮实施证据

| 提交 | 单元 | 实施结果 |
| --- | --- | --- |
| `359a2532e` | CS、CT | 本地 embedding writeback 在共享 database mutation lock 内 strict recount，超额向量保持 pending；disabled rowid resolver 使用 strict failure，并在 materialize 后按规范化 entry identity 二次过滤。 |
| `6eb28d494` | CU | staged identity 明确记录 subscription presence 与 canonical SHA-256；stage、prepare、activate 共用严格 subscription validation，缺失、损坏、替换或本地作业后加订阅均进入 degraded 隔离。 |
| `4cda7b874` | CV、CW | live evaluator 每 case 建立 `new_session=true` 的独立 websocket session；管理端本地包走 remove、Plugin Market 订阅包走 unsubscribe，Main Server 拒绝无 provider identity 的订阅包通用删除。 |

最终合并回归使用项目 `.venv` 的 Python 3.11.15 执行，indexer、hybrid retrieval、pack jobs、pack registry、store、public service/router、quality evaluator 与 Plugin Market 集合为 272 passed、1 skipped；skip 仍仅因本机 Windows 无目录 symlink 权限。相关 Ruff 与 `git diff --check` 通过。前端 Knowledge API Vitest 为 16 passed，`vue-tsc --build` 通过。没有新增测试文件或 i18n key。

### 第十四轮 outside-diff 补充：异步任务收尾与异常可观测性

完整检查 CodeRabbit review body 时另发现 2 条不会生成 review conversation 的 Nitpick。它们不影响前述 5 条线程的解决状态，但沿取消路径核对后均成立，因此作为 CX、CY 补充归档：

- CX：Plugin Market 测试的 autouse fixture 只调用 `Task.cancel()` 就清空 registry，未等待 worker 与 installation mutation 真正结束。fixture 改为 async，在 teardown 收集两个 registry 的任务、统一 cancel、`await asyncio.gather(..., return_exceptions=True)` 后再清空，确保测试退出没有 pending task。
- CY：`_installation_mutation_done()` 调用 `completed.exception()` 只消费返回值；该方法对任务内部异常不会抛出，取消 worker 后异常可能无人记录。callback 保留 cancelled early return，检查返回异常并以 traceback 记录 error；正常成功不产生日志，也不重复抛出。

验收：fixture teardown 能等待同时存在于两个 registry 的未完成任务并清空全部 ownership 状态；失败 installation mutation 的 callback 只记录一次 error 且移除 registry 引用；成功和 cancelled task 不记录错误。两项只修改现有 market route 与既有测试文件，不新增运行期状态、测试文件或用户文案。

CX、CY 由提交 `89b8a30d3` 完成；现有 Plugin Market 测试文件新增失败、成功与取消 callback 反例，并在 async autouse teardown 统一等待任务。该文件 27 passed，相关 Ruff 与 `git diff --check` 通过。

## 第十五轮：持久作业观察、校准过滤、维护失败与空分页

第十四轮完成后，2026-08-26 针对 PR head `2c031186d4` 的 Codex 复审新增 3 个未解决 review conversation；同日 CodeRabbit review body 另有 1 条 outside-diff 评论。完整分页统计为 150 个 conversations，其中 147 个已解决、3 个未解决。沿生产调用链核对后 4 个问题均成立。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3859329958` | CZ | 已持久 stage 的市场作业在一次状态轮询失败后被误报为订阅失败，成立 |
| `discussion_r3859329964` | DA | hybrid 校准语料包含 `catalog.override.json` 中已禁用词条，成立 |
| `discussion_r3859329966` | DB | rebuild 把 `inspect_database()` 的错误回退计数解释成完成，成立 |
| CodeRabbit review `5026316756` | DC | 空结果分页显示 `1–0` 或 `51–50`；建议 diff 只保护左边界，不能完整修复非零 offset，成立 |

### 修复单元 CZ：持久作业的观察失败不等于安装失败

- Main Server 返回 `job_id` 后，stage 已成为独立持久事实；Plugin Server 的轮询只观察该事实，单次网络、鉴权刷新或响应解析失败不能反向把作业改写成安装失败。
- `_wait_for_pack_job()` 只对稳定的作业终态作业务判断：`active` 成功，`cancelled`、`failed`、`degraded` 失败。`main_server_unavailable` 属于可重试观察错误，在原 24 小时总 deadline 内按既有轮询间隔继续查询，不提前结束 marketplace task。
- 可重试错误恢复后必须继续使用同一 `job_id`，不能重复 stage、重新下载制品或产生第二个订阅报告。最终观察到 `active` 后才写 completed 并 best-effort 上报市场。
- `job_not_found` 仍是稳定失败：一次可信 job list 已证明该 ID 不存在。用户退订触发的 worker cancel 继续由既有 durable remove/ownership 路径收敛，不在轮询 helper 内隐式删除作业。
- 24 小时 deadline 仍是等待上限；本轮不新增无限后台任务或进程重启后的 Plugin task 持久化。超时维持 `job_timeout`，但此前的瞬时观察失败不得缩短该边界。

验收：首次 `packs/jobs` 返回 `main_server_unavailable`、下一次返回同一 job `active` 时订阅完成且只报告一次；稳定 failed/degraded/not-found 仍立即失败；持续 pending 仍受 deadline 控制；取消行为不变。

### 修复单元 DA：校准语料与生产 disabled 策略一致

- real-model evaluator 在加载向量前读取与 `knowledge.db` 同目录的 `catalog.override.json`，使用生产 `load_disabled_entries()` 和规范化 entry identity，不另造宽松解析器。
- evaluator 的只读 SQL 同时取得 entry 的 tags；每个 entry 必须能唯一解析出 `source:*` identity，并与 title 构成 key。命中 disabled key 的全部 chunks 在组成矩阵前排除，避免高分禁用向量影响 positive rank 或 negative top score。
- override 不存在等价于空集合；文件损坏、不可读或 entry identity 无法可信解析时返回稳定 `EvaluationUnavailable`，不忽略策略继续给出阈值。该失败只影响离线校准，不修改数据库或 override 文件。
- `ready_vectors` 记录过滤后的可评估向量数，并增加 `disabled_vectors` 作为审计计数；过滤后为空仍返回 `ready_vectors_missing`，不生成无语料推荐。

验收：禁用 entry 的所有 chunks 不进入 corpus；未禁用 entry 正常保留；损坏 override 与歧义 source identity 失败关闭；override 不存在保持当前行为；数据库继续以只读方式打开。

### 修复单元 DB：维护命令不得用不可用状态证明完成

- `inspect_database()` 保留结构化 `error_type`，用于 `--status` 展示；所有 rebuild 决策点通过共享 guard 要求 inspection 没有错误后，才读取 pending/ready/failed 计数。
- rebuild 开始、每轮 eligible 计算、每轮写回后以及最终 completion 计算的任一 inspection 失败，都返回 `result_state=inspection_unavailable`、`complete=false`，命令最终非零退出。错误状态不得进入 `_eligible_chunk_count()`、容量预算或 `_completion_state()`。
- 数据库明确不存在仍沿用 `database_missing` skipped；本单元只收紧“文件存在但无法可信读取”。失败后不把零计数当成无工作，也不继续下一批 embedding。
- 已在 inspection 成功后完成的写回不回滚；结果必须保留已知动作计数并附带最新错误类型，方便安全重试。不得把暂时锁定升级为破坏性 reset 或自动删除。

验收：初始、循环前、循环后和最终 inspection 分别注入 SQLite 错误时均不报告 complete；初始错误不启动 reset/backfill/embedding；中途错误停止后续批次；正常完成、容量限制和 database missing 语义不变。

### 修复单元 DC：空页范围必须是自洽状态

- 分页范围作为一个整体渲染：`entries.length === 0` 时固定显示 `0–0`；非空时显示 `offset + 1` 到 `offset + entries.length`。不能只保护左边界，否则非零 offset 会得到 `0–50`。
- 保留上一页按钮在非零 offset 时可用，让用户能从因外部删除或筛选变化产生的空页返回；下一页继续由 `hasMore` 禁用。本轮不在缺少 total 的 API 上推断最后一页，也不加入可能循环回退的自动请求。
- 仅改变已有数字表达式，不新增文案或 i18n key，不改变搜索 reset、请求 gate 或 page size。

验收：首页空结果与 offset 50 空结果均显示 `0–0`；非空首页显示 `1–N`，非空第二页显示 `51–(50+N)`；previous/next 的既有行为不变。

## 第十五轮实施顺序与关闭条件

1. 首个提交只归档本节，不提前回复或 resolve 评论。
2. 后端实现提交处理 CZ、DA、DB，各自扩展现有 `test_knowledge_market.py`、`test_knowledge_hybrid_real_model_eval.py` 与 `test_rebuild_knowledge_index_script.py`，不新建测试文件。
3. 前端实现只调整 `KnowledgeManager.vue` 的范围表达式；使用现有前端类型检查和相关 Vitest 回归验证，不为单个模板插值引入新组件或 i18n。
4. Python 使用项目 Python 3.11 的 `uv run pytest`；运行相关 Ruff、前端 Vitest、`vue-tsc --build` 与 `git diff --check`。
5. 实现提交推送后，3 条 review conversations 分别回复提交和精确反例测试再 resolve；DC 是 review-body outside-diff，通过 PR 评论留下同等证据。
6. 最终重新遍历全部 reviewThreads 分页并复查最新 review body；只有远端可见实现、验证证据和评论处理全部完成后，才把文档头部改为“第一至第十五轮均已实施”。

关闭条件：瞬时轮询失败不制造本地已安装/市场失败分裂；校准矩阵不含禁用词条且策略损坏失败关闭；任何 inspection error 都不能导出 rebuild complete；所有空页只显示 `0–0`。150/147/3 是本轮开始快照，不代表实施后的最终状态。

## 第十五轮实施证据

提交 `ab899a225` 完成 CZ–DC：市场订阅轮询仅重试 `main_server_unavailable` 并继续观察同一持久 job；real-model corpus 使用 canonical catalog override 和规范化 entry identity 排除禁用 chunks；rebuild 在初始、轮次前后及最终 inspection 的任一错误处返回 `inspection_unavailable`/`complete=false`；Knowledge Manager 空页整体显示 `0–0`。

项目 `.venv` 的 Python 3.11.15 精确反例 6 passed；本轮三个相关文件除既有失配用例外的相邻集合为 62 passed、3 deselected。3 个 deselected 是当前 PR head 原已存在的 staged-job identity 测试夹具未包含第十四轮 CU 强制字段，与本轮代码路径无关，单独执行同样失败，未借本轮扩大修复范围。相关 Ruff 与 `git diff --check` 通过。前端 Knowledge API Vitest 为 16 passed，`vue-tsc --build` 与 8 个 locale 的 i18n 完整性检查通过；没有新增 i18n key。

本轮设计提交为 `98b192b7b`。设计与实现均已本地提交；首次远端 push 因受限环境无法取得 Git 凭据而失败，依仓库规则未在同轮重试。因此评论回复、resolve 和远端 unresolved 清零必须等待后续明确的推送步骤，不能用本地提交冒充远端关闭证据。

## 第十六轮：错误分类、暂存真实性与持久状态恢复

第十五轮提交推送并清零线程后，针对远端 head `ac84a9692` 的复审新增 6 个未解决 conversation。逐条追踪当前实现后全部成立；其中 DD 是对 CZ“只重试瞬时观察失败”的必要分类补充，DE–DI 是此前身份、容量、状态和迁移边界仍可构造的独立反例。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3859520407` | DD | `_main_request()` 把永久 4xx 和无效 JSON 都映射为可重试 unavailable，成立 |
| `discussion_r3859562970` | DE | job ID 没有与 immutable pack ID 精确绑定，可伪造 replacement 容量扣减，成立 |
| `discussion_r3859562976` | DF | accepted staging database 的向量字节可在 prepare 后被等长替换，成立 |
| `discussion_r3859562983` | DG | registry 仍有安装包但 database 缺失时 status 可重建空库并报告健康，成立 |
| `discussion_r3859562986` | DH | `CHUNKER_VERSION` 改变不会淘汰旧 chunks，成立 |
| `discussion_r3859562991` | DI | LIMIT 前缀中的损坏 entry 会永久阻塞后续合法 entry backfill，成立 |

### DD：轮询只重试网络与 Main Server 5xx

- `_main_request()` 将连接、读写、超时等 `RequestError` 与 HTTP 5xx 映射为 `main_server_unavailable`；CZ 只重试该稳定 code。
- HTTP 4xx 映射为 `main_server_rejected`，JSON 解码失败或非对象响应映射为 `main_server_invalid_response`，两者直接结束轮询，不等待 24 小时。
- 不在轮询层自行猜测 404 的 job 语义；路由不存在属于协议不匹配，不等价于可信 `packs/jobs` 列表中的 `job_not_found`。

验收：网络错误和 500 可恢复后继续同一 job；404、其他 4xx、无效 JSON 与非对象 JSON 均不调用 sleep/retry；既有 degraded/failed/active 处理不变。

### DE：job directory identity 必须由 pack identity 派生

- `_validated_identity_payload()` 除目录名和 `identity.job_id` 相等外，要求 job ID 严格等于 `<validated pack_id>-<12 lowercase hex>`；不能只分别验证两个字段。
- identity 与 state 同时被改成另一 pack ID 时，读取即进入 `degraded/invalid_job_identity`，不得进入 pending capacity 的 replacement keys。
- 正常 UUID 前 12 位生成方式不变；本轮不兼容本 PR 未发布的无绑定 staged job。

验收：同步篡改 identity/state pack ID 仍被隔离；合法含 `-` 的 pack ID 正常；隔离作业使新的 capacity mutation 失败关闭且不能借已安装大包额度。

### DF：激活安装的向量字节必须重新绑定发布制品

- hybrid activation 在读取 staging database 后，重新用 immutable subscription digests 验证仍在 job directory 的 canonical pack、manifest 和 vector artifacts。
- 将验证所得 expected records 与 `ready_embedding_records(strict=True)` 按 `(chunk_id, content_hash)` 比较 model、dimensions 和 embedding bytes；staging database 中任意等长、有限但不同的向量均拒绝。
- 安装只使用已经与可信 artifact 完全相等的内存 records；制品缺失、摘要变化、重复 key 或记录集合差异均进入稳定 failed，不降级成 trusted hybrid。

验收：prepare 后替换一个 BLOB 不安装；删除/替换 artifact 不安装；完整 accepted job 仍 hybrid；容量降级 BM25 仍先完成真实性验证，不把篡改数据写入 live database。

### DG：安装 registry 与 database 存在性必须一致

- `list_installed_packs()` 在 database 不存在时只读取 registry 并返回零 chunk status，不构造 `KnowledgeStore`，保证管理只读路径不创建空库。
- `get_status()` 使用启动时取得的 `database_exists` 事实；当 registry 含至少一个 installed pack 而 database 缺失时返回 `integrity_ok=false`、`schema_state=invalid_or_unavailable` 和稳定 `knowledge_database_missing`。
- 全新空 root、空 registry 和仅有尚未激活 staged job 继续健康且不创建 database；registry 损坏仍按 registry invalid 单独失败关闭。

验收：安装后删除 database，status 保留 pack 计数、entries/chunks 为零并明确 degraded，且调用后 database 仍不存在；全新空目录维持健康。

### DH：chunker 合同变化淘汰全部旧派生数据

- 初始化 Schema 时在写入默认 metadata 前读取 stored `chunker_version`；其缺失或不同与 `embedding_input_version` 不同使用同一个 invalidation 分支。
- 任一派生合同变化都删除全部 `knowledge_chunks`、只递增一次 `chunks_revision`，并原子写入当前两个版本。entries 与 FTS 保持不变，由后续 backfill 按当前 chunker 重建。
- prebuilt-only 旧 chunks 同样不能保留；它们在重新导入匹配当前 manifest 前只提供 BM25，不允许混用旧边界向量。

验收：只改变 stored chunker version、保持 embedding input version 不变时 chunks 清空、revision +1、metadata 更新；当前版本重开不变；两个版本同时变化也只递增一次。

### DI：损坏 entry 不得成为 backfill 游标屏障

- `backfill_missing_chunks(limit=N)` 的 limit 约束“成功处理数”，而不是 SQL 候选前缀。使用按 rowid 排序的惰性 cursor 扫描缺 chunks rows，跳过无法构造 `KnowledgeEntry` 的行，直到成功 N 条或候选耗尽。
- 不修改、删除或伪造损坏 entry/chunk；损坏行继续由 status 的 `entries_missing_chunks` 暴露。函数返回 0 只表示本轮没有任何可合法派生的 entry。
- 单次调用不把全部 rows `fetchall()` 到内存；生产 `limit=1` 可越过任意早期坏行处理下一条合法数据。

验收：rowid 最小的 entry JSON 损坏、第二条合法时，`limit=1` 为第二条生成 chunks 并返回 1；只剩损坏行时返回 0；正常 limit 和 embedding policy 行为不变。

## 第十六轮实施与关闭条件

1. 先提交并推送本节设计，不提前关闭线程。
2. DD 单独修改 market bridge error mapping 和既有 market 测试；DE、DF 共用 staged trust boundary 但保留两个独立反例。
3. DG、DH、DI 分别扩展既有 packs/service/chunks 测试，不新建测试文件，不引入用户文案或 i18n key。
4. 使用项目 Python 3.11 运行精确反例和相邻 market、pack jobs、packs、service、chunks 回归；运行 Ruff 与 `git diff --check`。本轮无前端改动。
5. 实现推送后逐条回复提交和精确测试证据并 resolve 6 条线程，再完整分页复核。

关闭条件：永久 Main Server 响应不进入 24 小时重试；job ID 不能冒充另一 pack；激活向量逐字节来自受信 artifact；installed registry 不会掩盖 database 丢失；chunker 升级不混用旧派生数据；坏 entry 不阻塞后续 backfill。只有远端实现和测试证据齐全后才把本轮标为已实施。

## 第十六轮实施证据

设计提交 `54f531673`、实现提交 `8612faa50` 已推送。DD 将 Main Server 网络/5xx、4xx 和无效响应分别映射为 unavailable、rejected 与 invalid response；DE 将 job ID 精确绑定 immutable pack ID；DF 在 activation 前重新验证三个 canonical artifacts，并逐 key 比较 staging database 的 model、dimensions 与 embedding bytes；DG 使 registry-only pack listing 不创建缺失 database，status 返回 `knowledge_database_missing`；DH 将 chunker 与 embedding input 合并为一次派生合同失效；DI 让 backfill limit 约束成功处理数并以惰性 rowid cursor 越过坏行。

项目 `.venv` Python 3.11.15 的本轮精确反例为 9 passed；market、pack jobs、packs、service、chunks 相邻集合为 217 passed、1 skipped；store、hybrid retrieval、public router 与 agent hardening 扩展集合为 85 passed。合计 302 passed、1 skipped，skip 仍是本机 Windows 目录 symlink 权限。相关 Ruff 与 `git diff --check` 通过；本轮无前端、i18n 或新测试文件变化。

## 第十七轮：取消结果收敛、未发布迁移撤销与有界物化

针对远端 head `290a532aba` 的复审新增 3 个未解决 conversation，并在 CodeRabbit review body 中新增 1 条 outside-diff 评论。逐条复核当前控制流后四条都成立，但策略迁移评论不按“继续完善迁移”处理：该迁移只兼容本 PR 的未发布中间注册表，沿用 BE 的发布事实结论，应删除兼容面而不是增加启动失败路径。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3859666690` | DJ | installing mutation 的业务拒绝结果被丢弃，退订可错误返回 `not_found`，成立 |
| `discussion_r3859666694` | DK | registry 读取失败被折叠为迁移成功并污染 initialized cache，现象成立；解决方向改为删除未发布迁移 |
| `discussion_r3859666698` | DL | `bool("false")` 把损坏的自动上下文标志规范化为启用，成立 |
| `pullrequestreview-5026823337` | DM | 同一连接的活动 SELECT 游标与 chunk 写入交错，结果集稳定性未定义，成立 |

### DJ：退订必须消费 installation mutation 的可证明结果

- `_cancel_active_subscription()` 取消外层 worker 后必须取得 installation mutation 的终态，不能只等待后丢弃 result/exception。
- Main Server 明确返回 `{ok:false}` 表示 apply 在原子 job 发布前被拒绝；此时记录 `preinstall_cancelled`，按既有幂等取消响应上报远端，不再调用必然 `not_found` 的 remove。
- 连接、超时或无效响应不能证明未发布，继续按“不确定提交”处理：等待 mutation 终止后使用三重持久身份调用 `packs/remove`。此时 mutation 已不可能再迟到发布；若可信 remove 明确返回 `not_found`，也收敛为幂等取消成功并执行远端 unsubscribe report。
- 其他 remove 业务拒绝仍保留原错误，不把身份冲突、registry 损坏或服务不可用伪装成成功。

验收：apply 明确 `{ok:false}` 时不调用 remove、退订成功并上报；apply 抛连接异常时仍调用 remove；异常后 remove 成功或明确 not-found 均不留下订阅，身份不匹配仍失败关闭。

### DK：删除最终格式之前的自动策略迁移

- 删除 `migrate_legacy_pack_index_policies()`、`initialize_knowledge_runtime()`、进程级 migration task/cache，以及 Main Server 启动时调用和公共 API 导出。
- 删除只验证缺少 `local_embedding_enabled` 等本 PR 中间 registry 字段的迁移测试；保留最终格式的 registry health、数据库 Schema guard、显式 pack mutation 和 indexer 当前策略读取。
- 启动不再因旧 registry 自动改写数据库、chunks 或 `packs.json`。当前格式损坏仍由 status/管理入口报告 degraded；旧开发数据由开发者清理后重新导入。
- `installed_source_embedding_policies()` 继续作为当前 registry 的只读策略映射供 backfill/indexer 使用，它不推断或持久化旧字段。

验收：Main Server 启动不调用任何 pack policy migration；知识公共 API 不再暴露 runtime migration；当前空目录、合法最终数据库和损坏 registry 的健康语义不回归。

### DL：自动上下文标志必须是严格布尔值

- `_load_registry()` 对每个 pack 要求 `auto_context` 是 `bool`；字符串、数字、null、容器或缺失字段一律抛 `KnowledgePackRegistryError`，不能 truthiness-coerce 后继续报告 ready。
- routing 快路径继续只接受 `is True`；material type 规范化不替代布尔字段校验，也不能因 corpus 旧 Schema 分支把类型损坏改成启用。
- 写入入口继续持久化规范布尔值；本轮不为未发布的旧 registry Schema 新增自动补字段兼容。

验收：`"false"`、`0`、null 与缺失值均使 registry invalid，自动会话不加载该来源；规范 true/false 的路由和管理切换不变。

### DM：backfill 先有界物化候选、再修改 chunks

- `backfill_missing_chunks()` 使用 `entries.rowid > last_rowid` 的有界分页；每页先 `fetchall()` 并结束该 SELECT 的 stepping，再调用 `_reconcile_chunks()`。
- `last_rowid` 按本页最后一个候选推进，而不是按成功行推进；损坏 entry 被稳定越过但保持缺 chunks 状态，不会重复扫描或成为游标屏障。
- `limit` 仍约束成功处理数。函数持续读页直到成功 `limit` 条或候选耗尽；单页大小固定有界，不恢复全表物化。
- embedding policy 的显式映射、community 默认 `prebuilt_only` 与内置来源默认 `local` 保持不变。

验收：早期坏行后合法行仍被处理；跨多页坏行不造成无限循环；每次 reconcile 时对应 SELECT 已物化完成；正常 limit 与策略选择不回归。

## 第十七轮实施与关闭条件

1. 先提交并推送本节设计，再实现 DJ–DM；设计提交不用于提前关闭评论。
2. 删除迁移时同步清理调用、导出与专用测试，避免留下不可达兼容代码。
3. 在既有 market、packs、service、chunks 测试文件中增加精确反例，不新增测试文件或用户文案/i18n key。
4. 使用项目 Python 3.11 运行精确反例和相邻回归，运行 Ruff 与 `git diff --check`。
5. 实现与测试证据推送后回复 3 个 conversation 并 resolve；outside-diff 只能在 review 正文下留实现证据，不能伪造 conversation resolution。最后重新分页收集全部未解决线程及最新 review body。

关闭条件：明确失败的 apply 可幂等退订；不确定 apply 仍先等待再清理；启动不再迁移未发布 registry；损坏自动上下文字段失败关闭；backfill 不在活动候选游标上写依赖表。只有远端代码和精确测试齐全后才把本轮标为已实施。

## 第十七轮实施证据

设计提交 `545b2bf01`、实现提交 `ea79d433f` 已推送。DJ 记录 installation mutation 的 accepted/rejected/failed/cancelled 终态：明确业务拒绝直接收敛为幂等取消，不确定失败仍执行身份约束 remove，且只在 mutation 已终止并得到可信 `not_found` 时转为成功。DK 删除 pack policy migration、启动调用、进程 cache、公共导出及其专用测试。DL 拒绝非当前 registry Schema 与非布尔 `auto_context`，同时保留未来 Schema 的稳定错误。DM 使用最多 256 行的 `rowid` 页先完整物化，再逐行 reconcile，并按页尾 rowid 越过损坏候选。

项目 `.venv` Python 3.11 的 Plugin Market 文件为 35 passed；packs/chunks 文件为 81 passed；完整知识相关宽回归为 378 passed、1 skipped、4 deselected。skip 是本机 Windows symlink 权限；4 个 deselected 均为远端 head 已存在且与本轮路径无关的夹具失配：3 个 staged job identity 夹具仍使用不合法 job ID/缺失强制 identity 字段，1 个 builder subscription 夹具缺失既有强制 `material_type`。相关 Python 文件 Ruff、compileall 与 `git diff --check` 均通过；本轮没有前端或 i18n 改动。

## 第十八轮：自动检索失败关闭、市场再认证与根路径信任

第十七轮实施后完整分页得到 4 条未解决 conversation：两条来自此前全 PR review，两条来自远端 head `e3dbba08f` 的最新复审；同一复审的 review body 另有 1 条直接涉及第十七轮代码的 outcome 分类重复 nitpick。本轮将四个实际边界与该低风险一致性整理一并收敛，不处理同一旧 review body 中与本轮无关的其他历史 nitpick。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3859734280` | DN | catalog override 损坏时 mention matcher 抛错，而 search 已失败关闭，成立 |
| `discussion_r3859734285` | DO | pack canonical encoder 仍接受非标准 NaN/Infinity，成立 |
| `discussion_r3859920312` | DP | 退订信任可解析但被改写的本地 provider package ID，可误删远端订阅，成立 |
| `discussion_r3859920320` | DQ | staging 校验在 resolve 前未拒绝 knowledge root 自身的 link/reparse，成立 |
| `pullrequestreview-5027046115` | DR | installation outcome 分类存在两份同构实现，成立但仅为一致性整理 |

### DN：禁用目录不可证明时 mention 必须返回空匹配

- `_get_cached_mention_matcher()` 对 `load_disabled_entries()` 的 `CatalogOverrideError` 使用与 `search()` 相同的 fail-closed 语义，返回当前 policy 下的空 matcher。
- 错误期间不得回用旧 matcher，因为旧 disabled 集合可能遗漏刚被禁用的 entry；也不把空 matcher 写入正常 cache，以便文件修复后下一次调用按当前 revision 与 disabled 集合重建。
- 顶层会话异常保护继续保留，但损坏 override 不再把本轮 route 记为内部 error；没有可信禁用状态时宁可不注入任何知识。

验收：损坏 override 时 `find_mentions()` 与 `match_turn()` 都为空且不抛；修复文件后同进程可恢复匹配；正常 cache/revision 行为不变。

### DO：知识包 canonical JSON 必须拒绝非有限数字

- `canonical_pack_bytes()` 调用 `json.dumps(..., allow_nan=False)`，与 prebuilt manifest 的规范编码器保持同一 JSON 合同。
- `load_canonical_pack_artifact()` 遇到包含 `NaN`、`Infinity` 或 `-Infinity` 的原始 JSON 时稳定返回 `ValueError`；不能在规范性比较前让 Python 扩展值进入 payload。
- 有限数字仍由后续 pack Schema 类型校验决定是否接受；本轮只收紧 JSON 标准性，不改变五字段 pack Schema。

验收：三个非有限常量在编码和加载入口均被拒绝；正常 canonical bytes、hash 和市场制品验证不变。

### DP：持久 registry 身份只作候选，市场 descriptor 才能授权退订

- 活跃内存任务继续使用其已经通过 `_fetch_version_descriptor()` 验证并绑定 package ID 的 resolved identity；pre-install 快捷取消不新增网络依赖。
- 从 `packs.json` 解析出的 installed subscription 不直接授权删除。使用 caller package ID、持久 version/channel 与 claimed pack ID 重新读取市场 descriptor；要求 descriptor 的 package、pack、remote、version、channel 与持久记录全部一致后，才返回 remove 所需 identity。
- 建议同时核对 material type 与 artifact SHA-256，防止同版本元数据被本地篡改后仍通过基础字段；缺失字段、Pydantic 校验失败、市场不可用或任一不一致统一失败关闭为 `subscription_ownership_unverifiable`。
- 验证必须发生在 Main Server `packs/remove` 和远端 unsubscribe report 之前。不得因本地 registry 的 package ID 语法有效、全局唯一就视为市场所有权证据。

验收：把 pack A 的 provider package ID 改成另一个合法未占用 ID 时，既不调用 remove 也不上报；匹配 descriptor 才可删除；active task 取消路径不额外 fetch；市场不可用保持本地数据。

### DQ：配置根自身必须在任何解析和锁之前可信

- `_validated_jobs_root()` 先对原始 `knowledge_root` 执行 `lstat`/reparse 检查；root 是 symlink、junction、Windows reparse、非目录、缺失或不可读时立即返回不可信。
- 只有 root 自身通过后才 resolve root、检查 `.staging`、创建 registry lock、遍历 job 或执行 prune/cancel/discard。只证明 resolved child 位于 resolved root 下不再足够。
- `stage_pack()` 继续由 `_create_trusted_knowledge_root()` 创建缺失目录；只读 list 对缺失 root 返回空，不创建任何路径。
- 既有 staging root/job dir 二级 reparse 校验继续保留，root 校验是额外的第一道边界。

验收：模拟 root reparse 时 list 为空、cancel/discard 为 false、prune 不运行，且 registry lock helper 未被调用；正常真实 root 和首次 stage 不回归。

### DR：installation outcome 使用单一分类函数

- 抽取只读 `_installation_outcome_of()`，统一 cancelled、failed、accepted、rejected 四态；callback 与 unsubscribe reconciliation 都调用该 helper。
- 日志仍使用真实 exception 对象，helper 不吞异常、不改变 task 生命周期；本单元不改变第十七轮已经冻结的幂等取消语义。

验收：四种 outcome 的 callback 与 unsubscribe 分类一致；现有取消顺序、业务拒绝和连接失败反例不变。

## 第十八轮实施与关闭条件

1. 先提交并推送本文，随后在既有 retrieval/subscription/market/pack-jobs 测试文件中增加反例，不新建测试文件。
2. DN、DO 是纯读取/编码边界；DP、DQ 涉及删除授权与路径删除，必须同时覆盖成功与失败负例。
3. 使用项目 Python 3.11 运行精确反例、四个受影响文件的相邻回归、知识相关宽回归、Ruff、compileall 与 `git diff --check`。
4. 实现和证据推送后逐条回复并 resolve 4 条 conversation；DR 只能通过 PR comment 留证据。最后再次完整分页，新增评论不与本轮混入。

关闭条件：损坏 override 不注入 mention；非标准 JSON 不进入 canonical artifact；持久 provider ID 未经市场再认证不能授权删除；linked knowledge root 不触发锁、遍历或删除；outcome 分类只有一份实现。只有远端代码与测试证据齐全后才标记第十八轮已实施。

## 第十八轮实施证据

设计提交 `d0663773a`、实现提交 `03f7c5167` 已推送。DN 在 catalog override 不可信时创建但不缓存空 matcher，阻断旧缓存注入并允许修复后自动恢复。DO 为 pack canonical encoder 启用 `allow_nan=False`。DP 将持久 registry 身份降为候选，删除前使用 package/version/channel/pack 重新获取市场 descriptor，并同时核对 remote、material type 与知识制品 SHA-256；任何缺失、离线或不一致均在 remove/report 前失败关闭。DQ 在 resolve 与 registry lock 前拒绝 knowledge root 自身的 symlink/reparse。DR 以 `_installation_outcome_of()` 统一 callback 与 unsubscribe 的四态分类。

项目 `.venv` Python 3.11 的受影响核心文件为 160 passed、1 skipped，Plugin Market 文件为 37 passed；完整知识相关宽回归为 383 passed、1 skipped、4 deselected。skip 是本机 Windows 目录 symlink 权限；4 个 deselected 与第十七轮相同，均为远端 head 已存在且与本轮路径无关的夹具失配。相关 Python 文件 Ruff、compileall 与 `git diff --check` 均通过；本轮没有前端或 i18n 改动。宽回归退出阶段的遥测目录写入被工作区沙盒拒绝，发生在 pytest 已报告全部通过后，不影响测试判定或产品文件。

## 第十九轮：当前注册表的素材声明必须失败关闭

远端 head `624f57ab3` 的新复审留下 1 条 conversation（`discussion_r3860237961`）：Schema v4 `packs.json` 的 `declared_material_type` 缺失或损坏时，`_load_registry()` 会静默改写为 `knowledge`，使注册表保持 `ready`。评论成立；这不是旧 Schema 迁移需求，而是当前正式 Schema 的信任根字段被错误容错。

### DS：声明类型与本地用途覆盖必须分层

- `declared_material_type` 是安装制品写入的持久身份，当前 Schema 每个 pack 都必须显式保存 `knowledge` 或 `corpus`。字段缺失、类型错误或值不在集合中时，`_load_registry()` 必须抛出 `KnowledgePackRegistryError`，不能默认成 `knowledge`。
- `material_type_override` 只是用户可撤销的本地用途策略，不能替代或修复损坏的声明。即使 override 本身有效，声明无效时仍将整个注册表标记为 `invalid`。
- `effective_material_type` 继续由可信 declaration 与合法 override 计算；不从损坏声明推断，不尝试回读 SQLite 内容猜测原始包类型，也不自动改写磁盘。
- 失败关闭后，`pack_registry_state()` 返回 `invalid`，依赖 `_load_registry()` 的 installed/routing 读取返回空，因此社区包不能进入自动 mention 或 conversation context。管理端仍可通过状态诊断损坏，用户修复或重新导入后恢复。

验收：对已安装 corpus 包分别删除 declaration，或写入空值、错误字符串、布尔值、数字和容器，注册表状态均为 `invalid`，routing metadata 与 enabled community source tags 均为空，自动 turn/conversation context 不命中；合法 knowledge/corpus 及合法 override 行为不变。

## 第十九轮实施与关闭条件

1. 只修改当前 Schema 注册表校验和既有 packs 测试，不引入旧格式兼容或数据库反推。
2. 使用项目 Python 3.11 运行精确反例、packs 与 public service 相邻回归、知识相关宽回归、Ruff、compileall 和 `git diff --check`。
3. 实现与证据推送后回复并 resolve `PRRT_kwDOPD8VW86cW46-`，再完整分页检查新增 conversation。

关闭条件：任何无可信 declaration 的 Schema v4 pack registry 都不得显示 `ready`，不得向知识自动路由发布社区 source；合法当前注册表无回归。只有远端实现与测试证据齐全后才标记第十九轮已实施。

## 第十九轮实施证据

设计提交 `d25ca94f1`、实现提交 `f11367626` 已推送。`_load_registry()` 现在只接受类型为字符串且精确属于 `knowledge`/`corpus` 的 `declared_material_type`；缺失、空值、错误字符串、布尔、数字和容器都会抛出 `KnowledgePackRegistryError`，有效 override 也不能掩盖无效声明。失败路径不会改写或删除 SQLite 词条。

`test_knowledge_packs.py` 新增 8 种损坏声明乘以 2 种 override 状态的 16 个反例，并验证 registry state 为 invalid、installed/routing 读取为空、自动 match/context 不命中且原始 corpus 词条仍存在。packs 文件为 77 passed，相邻 public service 文件为 28 passed；完整知识相关宽回归为 399 passed、1 skipped、4 deselected。skip 与 4 个 deselected 原因同第十八轮；Ruff、compileall 和 `git diff --check` 均通过，本轮没有前端或 i18n 改动。

## 第二十轮：检索来源、评估派生版本与外层上传边界

合并上游后的远端 head `f178dd024` 新增 3 条 conversation。逐条沿实际调用链复核后均成立：第十九轮已经让当前注册表字段严格失败关闭，但检索来源映射仍使用宽松 source registry 回退；离线评估只核对 embedding input 而未核对 chunker；市场代理的外层 multipart 路径又未进入 Main Server 的有界流式读取。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3860419827` | DT | 注册表不可用时社区 source 被默认归类为 knowledge，成立 |
| `discussion_r3860419842` | DU | 混合检索评估不校验 chunker version，成立 |
| `discussion_r3860419853` | DV | 市场外层订阅上传在内层守门前无界物化，成立 |

### DT：素材检索只接受可证明的来源类型

- `_source_material_types()` 不再通过宽松 `get_source()` 推断 `source:community.*`。内置 source 可按内置 registry 解析；社区 source 只有在严格 `_load_registry()` 成功、且 installed pack 提供合法 effective material type 时才进入可信映射。
- `_allowed_material_sources()` 对 knowledge-only、corpus-only 和同时允许两类的查询都返回显式 source allowlist；不得因两类均允许就返回 `None` 取消过滤。这样损坏、缺失或不可读 `packs.json` 时，SQLite 中残留的社区 rows 在所有素材组合下都失败关闭。
- 不从 SQLite 内容、source tag 名称或本 PR 的旧格式反推 material type，也不改写、删除残留数据。合法内置 source 与合法当前注册表继续可检索；注册表修复后同一服务实例可重新读取并恢复社区结果。
- 结果素材类型仍从同一可信映射派生。未进入 allowlist 的未知社区 row 不得先命中再用 `knowledge` 默认值补类型。

验收：安装 corpus 后分别删除、损坏和使注册表不可读，knowledge-only、corpus-only 与 knowledge+corpus 查询均不返回该社区 source；内置 knowledge 仍可返回；恢复合法注册表后社区 corpus 恢复且标签不被误报为 knowledge。

### DU：评估向量必须匹配完整派生合同

- 评估脚本同时导入当前 `EMBEDDING_INPUT_VERSION` 与 `CHUNKER_VERSION`，只读加载 metadata 时一次读取两个版本。
- 在读取 ready vectors 前要求 stored `chunker_version` 精确等于当前版本；缺失或不同统一返回稳定 `chunker_version_mismatch`，不能评估生产初始化会淘汰的旧 chunks。
- 既有 embedding input mismatch 保持独立错误；成功状态同时报告两个版本，便于评估制品追溯。

验收：只改变 stored chunker version、保持 embedding input version 正确时评估在向量读取前失败；两个版本都正确时既有 disabled、identity 和向量质量检查不回归。

### DV：市场订阅上传在最外层代理前有界

- Main Server 为知识订阅 multipart envelope 定义单一共享上限，并将 `/api/public-knowledge/subscriptions/apply` 与 `/market/knowledge/subscriptions/apply` 两个精确路径都注册到 `InboundBodySizeLimitMiddleware.streamed_path_limits`。
- 两条入口共享相同的 pack、manifest、vector 与 envelope overhead 预算；不复制第二个数值，避免内外路径漂移。
- 外层 middleware 继续同时拒绝过大的声明长度和实际 chunked body，并在线程中使用 bounded spool。`web_app.py` 只会物化已经通过上限的请求；本轮不另建一套代理流式协议。
- 其他 multipart 路径保持原有 router 自管策略，不扩大全局限制范围。

验收：两个精确路径的上限完全相同；外层市场路径在缺少 `Content-Length` 或声明不实但实际超限时均于代理前返回 413；合法 body 字节不变地重放；其他 multipart 路径不受影响。

## 第二十轮实施与关闭条件

1. 先提交并推送本节设计，再实现 DT–DV；设计提交不用于提前关闭线程。
2. 只扩展既有 hybrid retrieval、real-model evaluation 与 ASGI body limit 测试，不新增测试文件或用户文案/i18n key。
3. 使用项目 Python 3.11 运行三个精确反例、受影响测试文件和知识相关相邻回归；运行 Ruff、compileall 与 `git diff --check`。
4. 实现与测试证据推送后逐条回复并 resolve 3 个 conversation，再完整分页复核新增未解决线程。

关闭条件：无可信 registry 的社区 rows 在任何 material allowlist 组合下都不可检索；评估不使用 chunker 版本不匹配的向量；外层市场订阅上传在调用 `request.body()` 前已受实际体积上限保护。只有远端代码和精确测试证据齐全后才标记第二十轮已实施。

## 第二十轮实施证据

设计提交 `91f19288b`、实现提交 `608e09ce1` 已推送。DT 将社区 source 从宽松 display registry 解析中移除，只由严格 installed registry 发布其素材类型；所有 material 组合都使用显式 allowlist，检索融合与直接 entry 分类也不再把未知社区来源默认成 knowledge。DU 让评估器在读取向量前同时核对 chunker 与 embedding input version，并在成功状态中报告完整派生版本。DV 把订阅 envelope 上限收敛到 `knowledge.limits` 的单一常量，并让 Main Server 的内层 API 与外层市场代理两个精确路径都进入同一 bounded spool 守门。

三个受影响测试文件为 63 passed；知识、公共路由、市场桥与 ASGI 相邻宽回归为 458 passed、1 skipped、4 deselected。skip 仍是本机 Windows 目录 symlink 权限；4 个 deselected 是远端 head 已存在的 staged job identity 与 builder subscription 夹具失配，与本轮调用路径无关，未借本轮修改。相关 Python 文件 Ruff、compileall 与 `git diff --check` 均通过；本轮没有前端或 i18n 改动。

## 第二十一轮：注册表对象、评估身份与运行期收敛

第二十轮推送后的远端 head `942152329` 有 4 条新 inline conversation；最新 CodeRabbit review 正文（`PRR_kwDOPD8VW88AAAABK667fw`）另有 2 条 outside-diff 评论。沿当前调用链复核后六条都成立；outside-diff 没有可 resolve 的 review thread，只能随实现提交在 PR 留证据。

| 线程或 review | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3860600486` | DW | 非法 material type override 被静默清空，成立 |
| `discussion_r3860600496` | DX | 非对象或不完整 subscription 可绕过 ownership 校验，成立 |
| `discussion_r3860600502` | DY | 评估正例只比较 title，未绑定 source identity，成立 |
| `discussion_r3860600506` | DZ | float16 转换后未复验向量有效性，成立 |
| `PRR_kwDOPD8VW88AAAABK667fw` outside 1 | EA | 索引器首次启动失败后没有实际重试路径，成立 |
| `PRR_kwDOPD8VW88AAAABK667fw` outside 2 | EB | voice cleanup 取消会跳过剩余 shutdown，成立 |

### DW：本地素材覆盖必须是显式三态

- 当前 Schema 的 `material_type_override` 只接受 JSON `null`、`knowledge` 或 `corpus`。缺失字段继续等价于无覆盖；其他字符串、布尔、数字、数组和对象都使整个 registry invalid。
- `_load_registry()` 不得把非法持久值规范化成 `None`，也不得借合法 declaration 猜测用户原有策略。合法 override 仍用于计算 effective type，`auto_context` 不因损坏策略被重新解释。
- 失败关闭不改写磁盘；installed、routing、自动上下文与素材检索均看不到该社区包，修复文件后可恢复。

验收：非法 override 的各 JSON 类型都令 registry invalid、routing 为空且原始 registry bytes 不变；null、knowledge、corpus 和缺失字段的当前语义不回归。

### DX：subscription 必须是完整当前协议对象

- pack registry 的 `subscription` 只接受 null 或通过 `validate_subscription()` 的完整对象；非对象、未知字段、缺失必填字段、非法 digest/package ID/material type/trust 都抛 `KnowledgePackRegistryError`。
- `install_pack()` 在首次写 registry 前同样规范并校验 subscription，保证本进程不能写出下一次读取就 invalid 的数据。无订阅本地包继续保存 null。
- ownership 与 marketplace package uniqueness 只在规范 subscription 上运行；不能把损坏 subscription 当成普通本地包，从 generic remove 绕过远端退订协调。
- 本轮不兼容本 PR 早期的不完整 subscription 测试夹具；测试更新为当前协议字段，不为未发布格式增加推断。

验收：scalar、array 与损坏 object 均使 registry invalid 且 remove 前失败；合法 subscribed/local pack 可读写；重复 marketplace identity 检查保持。

### DY：校准正例绑定完整 entry identity

- real-model fixture 的每个 positive 同时声明 `expected_source_tag` 与 `expected_title`；当前 CHIME 正例固定为 `source:chime`。
- vector corpus rows 与 ranking 输出携带 source tag；expected rank/score 只在 source tag 和 title 同时匹配时命中。缺失或非法 expected source 使评估不可用，不能退回 title-only。
- top3 输出保留 source identity，便于人工审计同名条目；negative 逻辑与 threshold 计算不变。

验收：错误来源的同名条目排第一、目标来源排第二时 expected rank 必为 2；目标来源缺失时不得由同名条目代答；正式 fixture 的十个 positive 都有唯一 source identity。

### DZ：持久化前复验 float16 向量

- provider 输出先按既有规则验证 float32 dimensions 与 finiteness，再在受控 `np.errstate` 中转换为 little-endian float16。
- 转换结果必须仍全部有限，且以 float32 计算的 norm 大于零；overflow 为 infinity、整体 underflow 为零或其他非法结果统一标记 `invalid_embedding`，不得写 ready BLOB 或消耗 ready capacity。
- 合法的极小分量可下溢，只要整行仍有非零有限信息即可接受；本轮不改变规范化策略或模型维度合同。

验收：float32 有限但 float16 overflow、整行 underflow 两类结果均 failed 且 ready 为零；正常向量仍写入，容量与 stale writeback 计数不回归。

### EA：索引器启动拥有独立可取消重试

- runtime 主初始化完成与 knowledge indexer 启动分开记账。bind 或 `start_knowledge_indexer()` 抛错时保持 BM25 和主服务可用，同时创建一个受强引用的 retry task，按有界退避重试同一幂等启动步骤。
- 同一进程最多一个启动重试 task；成功（包括 indexer 已在运行）后结束重试。`_ensure_main_server_runtime_initialized()` 的 one-shot 不再是索引器恢复的唯一触发器。
- shutdown 入口先取消并观察尚未完成的启动重试，再请求实际 indexer stop，避免退出期间迟到创建后台任务。

验收：首次 bind/start 抛错、第二次成功时无需重跑主 runtime 即启动；并发调度不创建重复 retry；shutdown 取消 retry 后不再调用 start。

### EB：关闭取消在清理完成后恢复

- `close_voice_identity_runtime()` 抛出的 `asyncio.CancelledError` 单独暂存，不进入普通 Exception 日志，也不立即离开 `on_shutdown()`。
- 连接器、预加载与游戏任务、agent bridge、翻译、token、音乐、Cloud Save、memory server、HTTP pools 和 knowledge indexer finish 按既有顺序继续执行。
- 所有剩余关闭步骤完成后重新抛出原取消异常，保留调用方取消语义；普通 voice cleanup 错误继续只记录并放行。

验收：voice close 取消时后续 cleanup、Cloud Save 与连接池均被调用，最后调用者仍收到 CancelledError；普通关闭与非取消异常不回归。

## 第二十一轮实施与关闭条件

1. 先提交并推送本节设计，再实施 DW–EB；不提前回复或 resolve。
2. 扩展既有 packs、real-model eval、hybrid retrieval、cloudsave lifecycle 测试和正式评估 fixture，不新增测试文件或用户文案/i18n key。
3. 使用项目 Python 3.11 运行六项精确反例、受影响文件和知识/启动/关闭相邻回归；运行 Ruff、compileall 与 `git diff --check`。
4. 实现证据推送后逐条回复并 resolve 4 个 inline conversation；为两个 outside-diff 问题发布一条对应 review ID 的 PR 证据评论。最后重新分页收集 threads 和最新 review body。

关闭条件：当前 registry 的 override/subscription 损坏均失败关闭；评估正例不能被异源同名条目替代；ready 向量一定是有效 float16；索引器启动失败可独立恢复；voice cleanup 取消不会截断后续关闭。只有远端实现与精确测试齐全后才标记第二十一轮已实施。

## 第二十一轮实施证据

设计提交 `f2e1c406c`、实现提交 `11595cba1` 已推送。DW 令非法 `material_type_override` 直接使当前注册表失效且不改写原文件。DX 在安装写入与注册表读取两端统一执行完整 subscription 协议校验；缺字段、非对象或不完整对象都不能降级成无订阅本地包，合法对象统一规范化后再参与身份与唯一性判断。DY 为正式评估正例和排序结果补齐 source tag，并以来源和规范化标题的联合身份计算名次。DZ 在 float16 转换后再次检查有限性与非零范数，overflow 和整行 underflow 均记录为 `invalid_embedding`。

EA 将知识索引器启动拆成独立的、单实例强引用退避重试任务；首次启动失败不阻断主服务，成功后自动结束，关闭入口会先取消并观察未完成重试。EB 暂存 voice identity 清理产生的取消，在连接器、后台任务、翻译、Token、音乐、Cloud Save、HTTP 连接池和索引器清理全部执行后恢复抛出，兼顾完整收尾与调用方取消语义。

项目 `.venv` Python 3.11 的受影响回归为 320 passed、1 skipped；知识库与启动/关闭宽回归为 511 passed、1 skipped、3 deselected。skip 是本机 Windows 目录 symlink 权限；3 个 deselected 是上一轮已确认、且本轮未触碰的 staged-job identity 旧夹具。相关 Python 文件 Ruff、compileall、评估 fixture JSON 解析与 `git diff --check` 均通过；本轮没有前端或 i18n 改动。pytest 完成后的遥测写入告警来自工作区沙盒拒绝访问用户配置目录，不影响测试结果或产品文件。

## 第二十二轮：抽样的素材类型必须约束候选总体

第二十一轮推送后的远端 head `52d8d9c08` 新增 1 条 conversation（`discussion_r3860797003`）：`sample` 模式解析了 `material_type`，但 `sample_entries()` 仍从标签下所有来源抽样，随后只排除无法识别类型的结果。混合标签可能因此把 knowledge 请求返回成 corpus，反之亦然。问题成立；但仅在抽样后过滤仍会让错误类型占用有限名额，造成目标类型实际存在却返回空，因此必须在随机抽样前收窄候选总体。

### EC：按可信来源类型进行抽样

- `KnowledgeService.sample_entries()` 接受可选的精确素材类型，只允许 `knowledge`、`corpus` 或不指定；调用者不能传入其他持久或推断值。
- 服务先通过当前严格来源映射得到允许的 source tag。指定类型时只保留该类型来源；`auto`/`all` 则保留全部可证明来源。未知来源及损坏社区注册表不进入抽样总体。
- `KnowledgeStore.sample_entries_by_tag()` 在蓄水池计数和随机替换前检查允许来源，因此 `limit` 只由合格候选消耗；不能先混合抽样再丢弃错误类型。
- tool handler 将显式 `knowledge`/`corpus` 传入服务，并在渲染前再次核对解析类型；`auto`/`all` 保持接受两类的既有语义。

验收：同一允许标签同时包含 knowledge 与 corpus 时，两种显式请求都只返回对应类型，且 `limit=1` 时目标候选不会因另一类型先被抽中而产生假空结果；auto/all 仍可从两类抽样；未知来源失败关闭。

## 第二十二轮实施与关闭条件

1. 先提交并推送本节设计，再扩展既有 service/context/store 调用链及测试，不新增测试文件或用户文案/i18n key。
2. 使用项目 Python 3.11 运行混合标签精确反例、受影响文件与知识相关宽回归，并运行 Ruff、compileall 和 `git diff --check`。
3. 实现和证据推送后回复并 resolve `PRRT_kwDOPD8VW86cYT4U`，再完整分页确认是否有新增未解决评论。

关闭条件：显式素材类型在随机抽样的候选计数前生效，且渲染结果再次满足请求类型；目标类型存在时不会因异类候选占位而假空。只有远端实现和测试证据齐全后才标记第二十二轮已实施。

## 第二十二轮实施证据

设计提交 `54d170567`、实现提交 `da42116f1` 已推送。tool handler 现在把显式 `knowledge`/`corpus` 传给服务，`auto`/`all` 保持双类型语义，并在渲染前再次核对实际来源类型。服务只从内置来源表和严格当前注册表发布的社区 source 中构建抽样 allowlist；store 在蓄水池计数前过滤 allowlist，因此异类或未知来源既不能被返回，也不能占用 `limit` 造成假空结果。全局检索的既有扩展来源回退语义未被改变。

混合标签精确反例为 5 passed，context/service/store/packs 相邻回归为 166 passed，知识与市场相关宽回归为 459 passed、1 skipped、3 deselected。skip 是本机 Windows 目录 symlink 权限；3 个 deselected 是既有 staged-job identity 旧夹具。相关 Python 文件 Ruff、compileall 与 `git diff --check` 均通过；本轮没有用户文案或 i18n 改动。pytest 完成后的遥测日志告警仍来自工作区沙盒，未影响测试判定。

## 第二十三轮：状态输出与作业提交必须可证明

第二十二轮推送后的远端 head `c5b79157e` 新增 5 条 conversation。沿当前读取、轮询、激活与降级链路复核后全部成立。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3861038602` | ED | 未解析社区来源在状态计数中被默认成 knowledge，成立 |
| `discussion_r3861038607` | EE | mutable state 可伪造 active 且未与 live commit 对账，成立 |
| `discussion_r3861038612` | EF | container state 在集合判断处触发 TypeError，成立 |
| `discussion_r3861038621` | EG | source-aware 正例被旧 fixture schema 拒绝，成立 |
| `discussion_r3861038626` | EH | override identity 的非字符串值被强制转换后接受，成立 |

### ED：状态计数只统计已解析素材来源

- `knowledge_entries` 与 `corpus_entries` 只累计严格来源映射中精确解析为对应类型的 source；不得为缺失映射提供 `knowledge` 默认值。
- registry 损坏、缺失或不可读时，残留社区 rows 仍计入总 `entries` 和 source diagnostics，但不冒充任一可信素材类型；`integrity_ok` 与 registry state 继续明确降级。
- 内置来源和合法当前社区注册表的计数不变，不从 source tag 名称或 SQLite 内容反推类型。

### EE：active 是 live commit 的派生结论

- stage 时为规范化 pack 计算稳定内容摘要，并将其写入强制 identity；安装时把同一摘要写入当前 packs registry。两端都严格校验 SHA-256，不兼容本 PR 未发布的无摘要中间格式。
- `install_pack()` 仍是 durable commit point；只有它返回成功后，激活器才能原子发布独立 `activation.json` 收据，再把 mutable state 写为 active。收据绑定 job ID、pack ID、内容摘要、订阅摘要与实际 retrieval mode；写收据失败时不发布持久 active，后续按既有幂等路径重试。
- `_read_job()` 只有在 immutable identity、activation receipt 与 active state 三者精确一致时才接受 `active`。不能只验证 pack_id；同一 pack 的旧版本、相同条目数但不同内容、旧订阅版本或另一 job 的旧收据都不得替代当前 job。缺失、损坏或不一致时进入 `degraded`，不执行、不自动删除，只允许显式 discard。
- receipt 证明该 job 曾越过真实 durable commit，而不是声明包必须永远保持安装。正常退订后，7 天 TTL 内的历史 active job 仍保持成功记录；这避免把合法卸载误报为 staging registry 损坏。正常 payload 清理不删除 identity、state 或 receipt。

### EF：作业状态字段先验证再参与控制流

- state 必须是字符串且属于当前有限状态集合；数组、对象、布尔、数字、空值和未知字符串统一返回 `degraded/invalid_job_state`。
- 校验发生在任何 terminal membership、prune、capacity、indexer 或 API 状态判断之前，损坏值不得抛出 TypeError 或被字符串化成新状态。
- degraded 作业不自动清理，保持现有显式 discard 边界。

### EG：评估 fixture schema 与来源身份一致

- positive case 的精确字段集合加入 `expected_source_tag`；loader 在加载模型或向量前验证 id、query、expected title 与 expected source 都是非空字符串。
- expected source 必须是非空 `source:*` identity，title 必须可规范化；negative case 同样严格验证 id/query 类型与非空值。
- 默认正式 fixture 必须通过 loader；额外字段、缺字段、容器或非法来源失败关闭，不退回 title-only。

### EH：catalog override identity 禁止类型强制转换

- 持久 disabled row 的 `source` 与 `title` 必须原生为字符串，之后才允许 trim/标题规范化；数组、对象、数字、布尔和 null 都使整个 override invalid。
- 写入口同步要求字符串，避免内部调用生成下一次读取即不可解释的身份。
- override invalid 时检索与自动上下文继续失败关闭，文件不被静默改写；修复文件后可恢复。

## 第二十三轮实施与关闭条件

1. 先提交并推送本节设计，再实现 ED–EH；不提前关闭 conversation。
2. 扩展既有 public service、pack jobs、real-model eval、catalog boundary 测试，不新增测试文件或用户文案/i18n key。
3. 使用项目 Python 3.11 运行五组精确反例、受影响文件和知识/市场宽回归；运行 Ruff、compileall、fixture 解析与 `git diff --check`。
4. 实现与证据推送后逐条回复并 resolve 5 个 conversation，最后完整分页检查新增未解决评论。

关闭条件：未解析来源不再产生误导计数；伪造 active 无法代替真实安装提交；任意损坏 state 类型不会逃逸为系统异常；默认评估 fixture 可加载且来源身份严格；非字符串 override identity 失败关闭。只有远端实现和精确测试证据齐全后才标记第二十三轮已实施。

## 第二十三轮实施证据

设计提交 `78a748c0d`、边界细化提交 `130380a79`、实现提交 `66da99d7e` 已推送。ED 取消状态计数对未知来源的 knowledge 默认值。EE 为规范化 pack、staged identity、installed registry 和激活收据建立同一内容摘要链；只有真实安装提交后发布的收据与 active state 精确一致时才接受完成状态，同时保留正常卸载后的历史成功记录。相同容量但不同内容的 staged artifact 也会在执行前失败关闭。EF 在所有控制流前验证 state 的原生字符串类型和有限状态集合，激活收据中的容器型模式同样安全降级。EG 统一正式 fixture 与 loader 的来源感知 schema，并严格验证正负例文本身份。EH 禁止持久化和写入口把非字符串 source/title 强制转换成可接受身份。

项目 `.venv` Python 3.11 的最终受影响文件回归为 243 passed、1 skipped；知识库、公共路由和市场相邻宽回归为 493 passed、1 skipped、3 deselected。skip 是本机 Windows 目录 symlink 权限；3 个 deselected 是已归档的 staged-job identity 旧夹具，不为本 PR 未发布格式恢复兼容。相关 Python 文件 Ruff、compileall 与 `git diff --check` 均通过；本轮没有前端或 i18n 改动。pytest 退出后的遥测日志告警来自沙盒拒绝写入用户配置目录，不影响测试判定或产品文件。

## 第二十四轮：订阅声明必须绑定实际知识包

第二十三轮回复期间新增 1 条 conversation（`discussion_r3861220735`）：预构建索引端到端测试把 `corpus` 包与声明为 `knowledge` 的 subscription 一起直接传给 `stage_pack()`，却仍能安装成功。评论要求修正夹具；沿调用链继续审计后确认，单改测试会掩盖内部持久化入口缺少交叉对象校验的问题。外层市场和 Main Server 路由虽已验证类型，但 `KnowledgeService.stage_pack()` 和直接 `install_pack()` 仍可被维护代码、测试或后续调用者绕过路由使用。

### EI：pack 与 subscription 的素材类型在每个持久化入口一致

- 建立单一 binding helper：先按当前 subscription schema 完整规范化，再要求 `subscription.material_type` 精确等于 `KnowledgePack.material_type`。
- staged job 在创建知识目录、容量检查或写 identity/state/artifact 之前完成 binding 校验；不一致请求不留下 `.creating-*`、job 目录或数据库副作用。
- 直接安装入口在获取 registry/SQLite 写锁和创建数据库前使用同一 helper，避免内部调用写出“subscription 自身合法、但不属于该 pack”的当前注册表。
- artifact、manifest 与 vector 摘要仍按既有路由和预构建 sidecar 信任链验证；本轮不借 Minor 评论扩张摘要协议，也不把错误 subscription 降级为本地无订阅包。
- 将 builder 端到端正例的 subscription 类型修正为 `corpus`。另增类型不一致反例，分别覆盖 staging 与 direct install，证明守门不只存在于 HTTP 路由。

关闭条件：任何持久化入口都不能接受与实际 pack 素材类型不一致的 subscription；合法 corpus 预构建交接继续安装为 hybrid；失败请求不产生 staged job、registry 或知识数据库。实现、测试和远端证据齐全后回复并 resolve `PRRT_kwDOPD8VW86cZZBs`，再完整分页复核。

## 第二十四轮实施证据

设计提交 `3f3e57f62`、实现提交 `19bb013d9` 已推送。`validate_pack_subscription()` 现在统一执行当前 subscription schema 校验和 pack 素材类型绑定；staging 在容量检查及创建目录前调用，direct install 在获取 registry/SQLite 写锁前调用。类型不一致会直接失败，不创建 staged job、知识数据库或注册表，也不会被降级为无订阅本地包。builder 端到端正例已改为与实际 pack 一致的 `corpus`，并继续完成 hybrid 预构建索引安装。本轮没有扩张 artifact、manifest 或 vector 摘要协议。

项目 `.venv` Python 3.11 的受影响回归为 193 passed、1 skipped；知识库、公共路由和市场相邻宽回归为 497 passed、1 skipped、3 deselected。skip 是本机 Windows 目录 symlink 权限；3 个 deselected 是已归档的 staged-job identity 旧夹具。相关 Python 文件 Ruff、compileall 与 `git diff --check` 均通过；本轮没有前端或 i18n 改动。pytest 退出后的遥测日志告警来自沙盒拒绝写入用户配置目录，不影响测试判定或产品文件。

## 第二十五轮：提交证据与取消所有权不能停留在局部状态

第二十四轮关闭后完整分页发现 4 条 Codex conversation。逐条沿当前持久化、关闭、会话和市场任务调用链复核后全部成立。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3861346530` | EJ | activation receipt 与 state 同在可变 staging 目录，可一起伪造，成立 |
| `discussion_r3861346534` | EK | retry worker 清理会吞掉 shutdown 调用者取消，成立 |
| `discussion_r3861346541` | EL | 新用户输入在 turn-end 前不会清除旧 analyze owner，成立 |
| `discussion_r3861346548` | EM | 退订调用者取消会在安装 mutation 收敛前释放 package guard，成立 |

### EJ：active 必须引用 staging 目录外的提交日志

- 在知识根目录增加独立 `activation-commits.json`，不放入任何 job 目录。真实 `install_pack()` 返回成功后，激活器才原子记录绑定 job ID、pack ID、pack 摘要、订阅摘要、实际 retrieval mode 与提交时间的记录。
- 日志采用严格当前 schema 和精确记录结构；缺失、损坏或与 identity/receipt/state 不一致时，active job 进入 `degraded/active_job_commit_unverified`。仅在 staging 目录内复制 identity 并伪造 receipt/state 不能产生成功。
- 提交记录按时间和 job ID 确定性保留最近 100 条，与终态 job 数量上限一致，避免新增长期无界状态。日志损坏失败关闭，不静默覆盖。
- 正常卸载不删除提交记录，因此 7 天 TTL 内的历史 active 仍有效；这不要求当前 pack 永远安装。真正的终态目录仍按原 TTL/数量规则清理。

### EK：区分子任务取消与 shutdown 调用者取消

- `_cancel_task_if_running()` 捕获 `CancelledError` 时检查当前调用任务是否处于 cancelling；仅吞掉被清理子任务自身的取消，调用者取消必须向上传播。
- `on_shutdown()` 包住最前面的知识索引器 retry 清理；若调用者在此处取消，暂存异常并继续现有全部关闭步骤，最后与 voice cleanup 取消一样重新抛出。
- shutdown 已暂存取消后仍清理连接器、后台任务、Cloud Save、HTTP pools 与 indexer finish；不把调用者取消误记成普通子任务取消。

### EL：新普通用户输入立即失效旧 analyze owner

- 收到新的普通 transcript 或成功接收的用户图片时，立即按 request ID 核对 pending owner；只有明确属于同一 turn 的分片可保留，缺失 ID 或不同 ID 均清除旧 owner。
- mirror 输入不进入普通对话，不能干扰 ordinary owner；主动 assistant 消息也不继承 owner。
- session-end 只能复用仍与当前用户 turn 相同的 owner；新用户输入即使尚未收到 turn-end，也不能继承前一失败请求的 `public_knowledge` 路由。

### EM：退订 settlement 独立持有 package guard

- 退订入口先原子占用 package guard，再创建受强引用的 settlement task 执行取消 worker、等待 shielded installation mutation、解析持久归属并 remove。
- 调用者取消时立即保留取消语义，但 settlement task 继续持有 guard；完成或失败的回调消费结果、释放 guard 并移除强引用。期间同包 subscribe/unsubscribe 均返回稳定 conflict。
- guard 不能由已取消 worker 的 callback 代替；只有 installation mutation 与 removal 路径均结束后才能释放。正常退订结果、预安装幂等取消和其它 package 并发不变。

关闭条件：伪造 staging receipt/state 无外部提交记录时不能显示 active；shutdown 在 retry 清理处取消仍完成收尾并重新抛出；新普通输入在 session-end 前清除旧 owner；退订调用者取消后同包新订阅必须等待真实 settlement。实现和远端证据齐全后逐条回复并 resolve 4 个线程，再完整分页复核。

## 第二十五轮实施证据

设计提交 `615304adc`、实现提交 `89c4bdde4` 已推送。EJ 在知识根目录建立严格、原子且最多保留 100 条记录的 `activation-commits.json`；active 作业现在必须同时匹配 staging 内 identity/receipt/state 与 staging 外提交记录，伪造局部文件不能产生成功状态，正常卸载则保留历史提交证明。EK 令任务清理 helper 只吸收子任务自身取消，并让 shutdown 在 retry 或 voice 清理处收到调用者取消时暂存、完成剩余收尾后重新抛出。EL 在新普通 transcript 或图片到达时按 request ID 立即失效旧 analyze owner，仅同一 turn 分片可保留，mirror 路径不变。EM 以独立强引用 settlement task 持有退订 package guard，调用者取消不会中断安装收敛和 remove，也不会提前允许同包重新订阅。

项目 `.venv` Python 3.11 的四组精确反例及相邻回归为 135 passed、1 skipped；知识库、公共会话、启动关闭和市场宽回归为 553 passed、1 skipped、3 deselected。skip 是本机 Windows 目录 symlink 权限；3 个 deselected 是已归档的 staged-job identity 旧夹具。相关 Python 文件 Ruff、compileall 与 `git diff --check` 均通过；本轮没有前端或 i18n 改动。pytest 退出后的遥测日志告警来自沙盒拒绝写入用户配置目录，不影响测试退出码、判定或产品文件。

## 第二十六轮：文件身份、评估身份与来源身份必须复用生产边界

第二十五轮关闭期间完整分页又发现 4 条 conversation。沿暂存 SQLite、两条评估脚本和管理状态调用链复核后全部成立；其中注册表问题需要覆盖“注册表存在但漏记某个社区来源”的同类边界，不能只特判文件缺失。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3868058344` | EN | staging database 子文件可被链接到作业目录外，成立 |
| `discussion_r3868058348` | EO | response-quality 正例只验证任意 strong 命中，成立 |
| `discussion_r3868058353` | EP | hybrid evaluator 未复用生产 schema marker 契约，成立 |
| `discussion_r3868058358` | EQ | 社区 rows 缺少可信 registry 归属时健康状态仍可能 ready，成立 |

### EN：每次打开暂存 SQLite 前验证数据库文件族

- 建立单一 staging database path validator；数据库主文件及 SQLite 可能使用的 `-wal`、`-shm`、`-journal` 均必须是作业目录的直接子项，已有项必须是普通文件且不得为 symlink/reparse point。
- 首次构建允许这些文件尚不存在，但在创建 `KnowledgeStore` 前验证；`verifying_index` 恢复、严格向量快照和任何后续重新打开同样复用该守门，不因 durable state 跳过。
- 校验失败使作业进入稳定 degraded 状态，不打开链接目标、不清理链接目标，也不继续激活。作业目录本身仍需先通过现有 trusted-root 重验证。
- 清理只能 unlink 经目录边界确认的固定子项；本轮不承诺抵御拥有同等本机文件权限的进程在一次校验与 SQLite 系统调用之间持续竞争，但所有受支持入口均不得主动跟随已存在的链接。

### EO：response-quality strong 正例绑定预期条目

- strong fixture 必须额外声明非空 `expected_source_tag` 与 `expected_title`；none 用例不得携带这两个字段，保持“不得路由”的单一预期。
- preflight 使用生产 context 返回的 `source_tag` 和 `entry_title`，要求来源精确一致、标题按生产规范化规则一致；任一不符即 `route_pass=false`，即使 `actual_mode=strong`。
- `matched_term` 继续仅用于报告，不作为可信身份替代；fixture loader 对字段集合、来源格式和可规范化标题失败关闭。
- 现有 7 个 strong 用例绑定 `source:chime` 中的实际目标卡片，另增异源/异标题命中反例。

### EP：只读评估复用生产 schema marker 契约

- 将 `PRAGMA user_version` 与 metadata `schema_version` 的兼容判断提取为可复用、只读 helper；`KnowledgeStore` 与 evaluator 调用同一实现，避免复制后漂移。
- evaluator 在读取向量前执行该 helper；future version、两个 marker 不一致、非规范 metadata 版本以及有 user_version 却缺 metadata 均转换为稳定 `EvaluationUnavailable/index_schema_unavailable`。
- 评估仍以 SQLite read-only URI 打开，不构造或初始化 `KnowledgeStore`，因此不会迁移、建表或改写被测数据库；旧但生产可读的 schema 继续由 chunk/input 版本决定能否参与当前评估。

### EQ：健康状态要求每个社区来源都有可信注册身份

- 从持久 source counts 提取所有 `source:community.*`，并与严格当前 registry 解析出的 source/material 映射核对；任何无法解析的社区来源都令 `integrity_ok=false`。
- 这自然覆盖 packs.json 缺失、损坏以及合法 registry 漏记数据库残留来源；总 entries 和 source diagnostics 保留，但未知社区 rows 不计入 knowledge/corpus 分类。
- 全新空目录、仅内置来源、合法当前社区包仍保持健康；不得从 source tag 名称或 SQLite 内容反推包身份，也不得自动重建注册表。

关闭条件：所有 staging SQLite 打开点拒绝已存在的链接文件族；strong 评估只在预期来源与标题命中时通过；只读 hybrid 评估与生产使用同一 schema marker 判定；任一未注册社区来源都会使管理健康状态降级。实现与远端证据齐全后逐条回复并 resolve 4 个线程，再完整分页复核。

## 第二十六轮实施证据

设计提交 `de287704d`、实现提交 `cec305978` 已推送。EN 在首次构建、`verifying_index` 恢复和 hybrid 激活快照前统一验证 `knowledge.db`、`-wal`、`-shm` 与 `-journal`，已有项必须是作业目录内普通非 reparse 文件；失败作业进入 `degraded/knowledge_staging_database_invalid`，实际链接目标保持不变。EO 为 7 个 strong fixture 绑定 `source:chime` 与规范化目标标题，任意异源或异标题 strong 命中不再通过。EP 将生产 schema-marker 判断提取为只读 helper，评估器在读取向量前复用，future、marker 分歧、非规范版本和 user_version 缺 metadata 都稳定拒绝且不改写数据库。EQ 逐一核对持久社区来源与严格 registry 映射，注册表缺失或漏记来源都会使 `integrity_ok=false`，同时保留总数诊断且不反推身份。

项目 `.venv` Python 3.11 的受影响回归为 165 passed、3 skipped；知识库、公共会话、启动关闭和市场宽回归为 567 passed、3 skipped、3 deselected。3 个 skip 是本机 Windows 文件/目录 symlink 权限，四个 SQLite 文件名另由 reparse marker 反例完整覆盖；3 个 deselected 是已归档的 staged-job identity 旧夹具。相关 Python 文件 Ruff、compileall、fixture JSON 与 `git diff --check` 均通过；本轮没有用户文案或 i18n 改动。pytest 退出后的遥测日志告警来自沙盒拒绝写入用户配置目录，不影响测试退出码、判定或产品文件。

## 第二十七轮：有界提交日志不能裁掉正在发布的提交

第二十六轮关闭后 CodeRabbit 新增 1 条 conversation（`discussion_r3868155882`）：提交日志达到 100 条时按 `(committed_at, job_id)` 保留末尾记录；若同一秒内当前 job ID 的字典序较小，刚写入的当前记录会被裁掉，随后本地 active receipt 无法通过外部日志核验。问题成立。

### ER：裁剪必须无条件保留当前 activation

- `_record_activation_commit()` 先从候选中排除当前 job，再按既有 `(committed_at, job_id)` 顺序保留最多 99 条历史记录，最后把当前记录加入并确定性排序写回。
- 总数仍不得超过 `MAX_TERMINAL_JOB_DIRECTORIES`，旧记录的保留顺序不变；不引入新的 schema、序号或未发布格式兼容。
- 反例固定同一秒写满 100 个字典序更大的历史 job，再写入字典序最小的当前 job；断言当前记录存在、总数为 100、被淘汰的是历史集合中最旧排序项。

关闭条件：无论同秒 job ID 排序如何，刚完成 durable install 的当前 activation 都存在于原子写回后的外部提交日志，并能通过后续 active 核验。实现、测试和远端证据齐全后回复并 resolve `PRRT_kwDOPD8VW86crDYk`。

## 第二十七轮实施证据

设计提交 `26b732213`、实现提交 `cd5f1b09c` 已推送。提交日志裁剪现在先排除当前 job，从其它记录中按既有顺序保留最多 99 条，再无条件加入当前 activation 并确定性排序写回；总数仍为 100，不改变 schema。固定同一秒、当前 job ID 字典序最小的反例证明当前记录保留，淘汰的是历史集合中的最小排序项。完整 pack-jobs 回归为 97 passed、3 skipped；3 个 skip 仍是本机 Windows symlink 权限。Ruff、compileall 与 `git diff --check` 均通过。

## 第二十八轮：质量案例缺字段也必须走统一失败契约

第 27 轮最终分页发现 1 条更早插入的 CodeRabbit conversation（`discussion_r3868189551`）：response-quality loader 在确认必填字段存在前读取 `case["id"]` 和 `case["expected_mode"]`，缺字段会泄漏 `KeyError`，而不是该加载器对 schema 错误约定的 `ValueError`。问题成立。

### ES：先验证必填字段子集，再读取案例值

- 每个 case 确认为对象后，先要求基础 `required` 字段全部存在；缺失任一字段立即抛当前 documented-fields `ValueError`。
- 之后保留现有 ID 唯一性、expected mode、strong identity 与精确字段集合判断；不放宽额外字段，也不改变合法 fixture。
- 反例分别删除 `id` 与 `expected_mode`，证明二者都稳定返回 `ValueError` 而非 `KeyError`。

关闭条件：任意缺少基础必填字段的质量案例都由 loader 的 schema `ValueError` 拒绝，且合法 strong/none 案例行为不变。实现、测试和远端证据齐全后回复并 resolve `PRRT_kwDOPD8VW86crI0V`。

## 第二十八轮实施证据

设计提交 `e1201ee37`、实现提交 `a626d4b37` 已推送。loader 现在在读取 `id` 或 `expected_mode` 前验证基础 required 字段子集，之后仍执行既有精确字段集合和 strong identity 校验；缺 `id`、缺 `expected_mode` 两项反例均返回 documented-fields `ValueError`。完整 quality evaluator 回归为 9 passed；Ruff、compileall 与 `git diff --check` 均通过。

## 第二十九轮：直接入口、开发来源与崩溃暂存恢复必须闭环

测试夹具修正提交 `142ca9821` 触发的新一轮 Codex Review 给出 4 条 conversation。逐条沿独立脚本入口、Vite 代理和 staged job 恢复调用链复核后全部成立；其中 `.creating-*` 必须同时满足早期“orphan 只能显式清理”和 BL“普通 job ID 不得接受点目录”两项边界，不能通过放宽通用解析器修复。

| 线程 | 单元 | 结论 |
| --- | --- | --- |
| `discussion_r3868647212` | ET | Geng Guide 导入脚本依赖环境已安装项目才能解析 `knowledge`，成立 |
| `discussion_r3868647216` | ET | response-quality 评估器在项目导入后才计算仓库根且未加入搜索路径，成立 |
| `discussion_r3868647220` | EU | Vite 5173 代理保留浏览器 Origin，但路由守门未允许该开发源，成立 |
| `discussion_r3868647224` | EV | 崩溃 `.creating-<uuid>` 被 orphan 准入守门阻塞，却无法通过显式 discard 恢复，成立 |

### ET：独立知识脚本在项目导入前建立仓库根

- `import_geng_guide.py` 与 `evaluate_knowledge_response_quality.py` 都从自身 `__file__` 推导仓库根，并在任何 `knowledge.*` 导入前将其加入 `sys.path`；不得依赖启动 cwd、editable install 或 pytest 的导入环境。
- 已存在同一路径时不重复插入；默认案例路径继续从同一个仓库根计算，不增加第二套路径来源。
- 入口测试固定“路径引导早于项目导入”的源码顺序，并以两个脚本真实 `--help` 调用证明直接入口可启动。

### EU：只向明确的本地 Vite 开发源签发配对码

- 本地 Bridge 原有双重边界保持不变：TCP client 与 Host 都必须是 loopback；Origin 必须是纯 `http` loopback origin，不能含凭据、路径、查询或 fragment。
- 允许端口集合在实际 Plugin Server 端口和动态 Main Server 端口之外，只增加仓库 Vite 配置的 5173。由此前端开发代理保留的 `http://localhost:5173` 和 `http://127.0.0.1:5173` 可以取得 pair code。
- 不采用“任意 localhost 端口”或 Market CORS allowlist 作为 token 权限；相邻端口、HTTPS、本地带路径来源和远程域名继续返回 403。

### EV：崩溃创建目录使用独立的显式 discard 解析边界

- 普通 get、cancel、processor 与 job state mutation 继续只接受 `<pack_id>-<12 位小写十六进制>`，不允许 `.creating-*` 进入正式作业身份。
- discard 单独接受生成器真实格式 `\.creating-<32 位小写十六进制>`。解析后仍必须证明目标是可信 `.staging` 的真实直接子目录，且目标本身不是 symlink、junction 或 reparse point。
- discard 与 `stage_pack()` 共用 jobs-root 跨进程 mutation lock。仍在创建的目录会先完成并原子改名，随后旧临时名不再存在；只有创建进程已经退出并释放锁的崩溃残留能够被删除。
- 删除前 `_read_job()` 必须仍将目标识别为 degraded；后台、启动和容量检查绝不自动删除。非法后缀、大小写十六进制、路径片段和普通健康作业继续拒绝。
- 管理 API、Bridge、前端与维护 CLI 继续复用同一个 `discard_degraded_pack_job()`，不复制目录删除逻辑。成功清理后新的 staging 应立即恢复，而普通 cancel 对同一临时 ID 必须失败。

关闭条件：两个脚本不依赖 editable install 的路径副作用；Vite 5173 开发源可取得 pair code 且任意其它来源仍被拒绝；真实格式的崩溃临时目录可以经显式 discard 清理并恢复导入；普通 cancel、非法临时名称、路径穿越和重解析目标均不能扩大删除范围。实现、精确反例和远端检查齐全后回复并 resolve 4 条线程。

## 第二十九轮实施证据

实现提交 `7a57a0f10` 已完成：两个脚本在项目导入前建立仓库根；Bridge 只增加明确的本地 Vite 5173 来源；discard 通过专用临时目录解析器恢复崩溃残留，普通 job ID 与 cancel 边界不变。精确反例为 15 passed；作业生命周期、公共知识路由、Bridge、维护 CLI 和质量评估五个完整测试文件为 170 passed、3 skipped，skip 均为既有 Windows symlink 权限条件。远端 Plugin pytest 随后暴露一条仍把 5173 当作拒绝场景的旧集成断言；同步为 5173 成功、5174 拒绝后，完整 Market Bridge 集成文件为 98 passed。两个脚本的真实 `--help` 入口、相关 Ruff、compileall 与 `git diff --check` 均通过。本轮没有新增用户文案或 i18n 键。

## 第三十轮：关闭屏障、持久一致性与会话身份必须共享真实终态

第二十九轮实施后，PR #2951 已累计 478 条评论。通过 GraphQL 完整分页读取 review threads、关闭 11 条已有实现证据的 conversation 后，剩余 19 条全部指向当前提交 `cf6be823d`。第二次沿完整调用链复核得到 18 条真实问题和 1 条误报；相关问题不能按文件逐条堆补丁，而应收敛为关闭屏障、绝对期限、路径身份、跨文件提交、路由快照和会话身份等共享边界。

| 线程 | 单元 | 复核结论 |
| --- | --- | --- |
| `discussion_r3877428992` | EW | shutdown 后半段裸 `await` 可跳过后续清理，成立 |
| `discussion_r3878968996` | EW | 取消 indexer coordinator 不会停止 `to_thread` mutation worker，成立 |
| `discussion_r3877648029` | EX | HTTPX 分阶段 timeout 不是退订绝对墙钟期限，成立 |
| `discussion_r3877679300` | EX | 删除后的 report 可在总 deadline 外继续等待，成立 |
| `discussion_r3878672086` | EY | 浏览器 Bridge 暴露内部 `subscriptions/apply`，成立 |
| `discussion_r3878672093` | EZ | 存储迁移固定名单遗漏 `knowledge/`，成立 |
| `discussion_r3877428995` | FA | staging SQLite 校验后仍按路径重开，TOCTOU 成立 |
| `discussion_r3878672089` | FA | 创建 knowledge root 前未拒绝重定向祖先，成立 |
| `discussion_r3878968969` | FB | live `packs.json` 无界读取且可跟随特殊文件，成立 |
| `discussion_r3878968976` | FB | pack 删除跨 SQLite/registry 缺少崩溃协议，成立 |
| `discussion_r3878968987` | FC | activation commit 可早于对应终态目录被裁剪，成立 |
| `discussion_r3877679309` | FD | Store 把 routing 读取失败伪装成合法空集，成立 |
| `discussion_r3878657145` | FD | routing 刷新失败仍覆盖最后健康快照，成立 |
| `discussion_r3878093370` | FE | 自动检索在创建 task 后才检查容量，成立 |
| `discussion_r3878130342` | FE | cooldown 使用角色名而非真实会话身份，成立 |
| `discussion_r3878130346` | FE | 多卡上下文只记录第一张卡身份，成立 |
| `discussion_r3878130334` | FF | 工具查询没有传播当前 turn route owner，成立 |
| `discussion_r3870443115` | FG | 向量样式与文案从不同判断顺序推导，成立 |
| `discussion_r3878657157` | 无实现 | schema 6 fixture 被生产兼容契约接受，误报 |

### 第三十轮新增不变量

1. Clean shutdown 以最后一个 knowledge writer 真实返回为准；pack prepare/activate、backfill、vector writeback/reconcile、HTTP mutation、catalog/policy mutation 等必须共享一个 root barrier/admission 系统，coordinator task 结束、收到取消或 launcher 收到 shutdown-complete 都不能代替该事实。
2. 存储迁移只能读取已经关闭 mutation admission、排空进程内 writer 并取得跨进程知识根 barrier 的静止快照；launcher 还必须独立证明旧 Main 进程已经退出，barrier 只作纵深防御。
3. 一个 monotonic absolute deadline 覆盖退订的等待、删除和 best-effort report；phase timeout 只是内层保护，不得重置总预算。
4. canonical artifact leaf 与 live registry 的身份必须由同一次打开得到的文件句柄证明；knowledge root ancestor 本轮明确只防御检查时已经存在的 symlink/junction/reparse，连续同用户 namespace race 不在本轮威胁模型内，不宣称字符串路径链等价于完整 capability。
5. pack remove 采用 registry-first durable journal：先让 registry 不再发布目标包，再幂等删除 SQLite source；Python `except` rollback 不能替代崩溃恢复。除 remove 外不泛化为所有 SQLite/JSON mutation 均已 journaled。
6. routing 的合法空集合与加载失败是不同类型；失败既不能清除 dirty，也不能覆盖最后一次健康快照，disable/remove/policy mutation 还必须同步建立 visibility tombstone，禁止 last-good 继续暴露已撤销内容。
7. 自动上下文以 logical user session 和最终实际插入文本的每张卡为身份；容量必须在创建 coroutine/task 前准入，已取消但仍运行的底层任务继续占用容量。
8. Marketplace 信任只能由 Plugin Server 获取并验证 descriptor 后建立；浏览器 token 不授予构造 `trusted_market` subscription 的能力。
9. 单包展示状态只推导一次；概览是多包聚合，必须通过独立纯函数按显式优先级聚合单包状态与全局 fallback，CSS class、文案和概览 tone 不得各自重新解释原始计数。
10. 全局锁序固定为 `root barrier -> package guard -> jobs lock（若需要）-> registry lock -> SQLite transaction -> state/ledger`；正常路径、恢复、retention 与 retained-source cleanup 都不得反向获取。

### EW：统一 shutdown 取消屏障并跟踪真实 mutation worker

- `on_shutdown()` 中所有异步清理均通过接受 coroutine factory 的 `_run_shutdown_step(factory, *, deadline)`：helper创建独立强引用task并以`asyncio.shield()`观察，agent bridge、translation、music crawler、Cloud Save release/upload、memory server、两个 HTTP pool 和 knowledge finish 不再裸 `await`。
- 每一步有自己的 monotonic deadline，整个 shutdown 另有 global deadline。caller取消时只保存第一份 cancellation，继续 shield/观察当前cleanup直至该步骤deadline；只消费本helper捕获的那一次取消计数，不得用`uncancel()`清空其它并发取消。子任务自身取消按该步骤失败处理，最后重新抛出原caller cancellation。
- 步骤超时标记`clean=false`并继续后续cleanup；达到global deadline后由既有watchdog完成退出，不能无界等待，也不能报告clean shutdown。
- `_prepare_job()`、两条`_activate_job()`、backfill、vector writeback/reconcile、HTTP mutation route、catalog/policy mutation以及未来所有knowledge writer统一经过root admission与强引用worker registry。取消coordinator不取消内部worker；只有真实writer返回后才消费异常并移除记录。
- `request_knowledge_indexer_stop()` 关闭整个knowledge writer admission后再取消coordinator。`finish_knowledge_indexer_stop()` drain coordinator和全部已登记writer；超时只能返回“未排空”。
- 跨进程barrier lock不放在可能被迁移的knowledge目录内，而放在稳定anchor的`state/knowledge-root-locks/<canonical-root-id>.lock`；root ID优先取final path与volume/file identity，退化时至少是规范化、大小写归一的绝对根标识。storage migration和retained-source cleanup都取得同一barrier。
- launcher必须先用进程句柄/退出状态确认旧Main已死，再允许migration；不能把“拿到barrier”当成旧进程退出证明。
- 顺序固定为：关闭 admission → 请求 indexer stop → 等待真实 worker → 完成其它清理 → 旧进程退出 → migration 取得 barrier → snapshot/copy/verify/policy commit。不得用 sleep 或 coordinator done 猜测 writer 已退出。

验收：在每个剩余 shutdown await 注入caller cancellation和重复cancellation，后续哨兵仍执行、只消费目标取消并最终重抛；步骤/global deadline均无无界等待；分别阻塞prepare/activate/backfill/vector/HTTP writer，取消coordinator后finish不提前成功；旧Main存活时即使barrier可得也不迁移；worker持锁时migration与retained cleanup均不读取/删除source，释放后重试才成功；关闭admission后factory不再提交新mutation。

### EX：退订的删除与上报共享一个绝对期限

- reservation 建立后立即计算唯一 `deadline = loop.time() + total_budget`；取消安装、等待 mutation、解析 ownership、Main Server remove 和 report 均消费该期限，任何阶段不得重新获得完整预算。
- client 在调用 Main remove 前生成 `removal_operation_id`。Main 先持久化有界 remove intent/status，再把阻塞删除放入受强引用任务；提供按operation ID查询`pending/committed/failed/unknown`的内部status API。HTTP request取消或timeout不能被解释为服务端删除已停止。
- 增加 absolute-deadline await helper，以 `asyncio.timeout_at(deadline)` 或等价逻辑包住 ownership resolve、descriptor/OAuth、Main GET/remove/status与report；HTTPX connect/read/write/pool timeout继续按剩余预算 clamp，并为编码最终响应保留固定margin。
- package guard ownership可从settlement task显式转移给removal drain task；done callback只有在持有者真实结束或完成转移后才能释放，不能因外层请求timeout提前释放。
- deadline耗尽而Main终态未确认时返回`removal_pending`；请求是否到达Main都无法证明时返回`removal_operation_unknown`。drain继续持guard并只查询/完成同一operation，不能重复生成remove。只有确认`local_removed=true`后才允许远端unsubscribe report。
- `_report_unsubscribe_best_effort()` 接受同一deadline，OAuth refresh、DELETE、响应读取和client close全部受剩余预算约束。返回诊断明确区分`local_removed=true, remote_reported=false`；本轮远端状态仍定义为best-effort，若产品把它提升为一致性状态，必须先增加persistent outbox，不以后台裸task伪装可靠投递。

验收：模拟多个HTTP phase各自低于phase timeout但累计超过总预算，外层仍按absolute deadline终止；在Main收到remove前、提交intent后、SQLite提交后分别取消HTTP，按同一operation查询最终得到确定状态或明确unknown；remove已成功而report卡住时接口以`local_removed=true, remote_reported=false`准时返回；OAuth耗尽预算后不发送DELETE；settlement向drain转移期间package guard不提前释放，且同一operation只执行一次。

### EY：浏览器管理 Bridge 不拥有 Marketplace apply 能力

- 从浏览器知识管理路径 allowlist 删除 `subscriptions/apply`，并移除仅为该浏览器 envelope 保留的 body-limit 分支。浏览器调用在读取正文、创建下游 client 前返回 404。
- Main Server 的内部 `/api/public-knowledge/subscriptions/apply`、其 multipart ASGI 上界和 Plugin Server 内部 `_main_request(..., "subscriptions/apply")` 保持不变。
- 浏览器只提交 package/version/channel 给受信 Market 安装入口；Plugin Server 获取 descriptor，校验 package identity、URL、digest 和制品后，才调用内部 apply。
- `packs/import` 继续表示普通本地导入，不能产生 `provider=plugin-market` 或 `trusted_market` subscription。

验收：即使具备合法本地 Origin、bridge token 和 multipart，浏览器 apply 仍在消费正文前拒绝且不调用 Main Server；正常 Market subscribe 仍完成 descriptor 验证和内部 apply；本地 import 结果不带 Marketplace 归属。

### EZ：把完整 `knowledge/` 纳入有写入屏障的存储迁移

- 将 `knowledge` 加入 `MIGRATED_RUNTIME_ENTRY_NAMES`，但同步升级migration checkpoint schema；不能只改名单，否则旧v1 completed checkpoint会把从未复制过的source knowledge误判为可清理。
- 新checkpoint记录`copied_entries`以及每个entry经验证的manifest/digest。旧v1 completed checkpoint若source仍有knowledge而target没有对应证据，进入`knowledge_migration_repair_required`：先恢复复制与验证，cleanup保持阻塞；不能借用旧completed标记补写虚假证据。
- 迁移整个目录，包括 SQLite file family、`packs.json`、override、durable staged jobs、activation commit ledger、mutation journal及未来同根状态；迁移过程不解释或升级知识 schema。
- EW 的 mutation barrier 必须先于 source snapshot。仅靠文件数或字节数相同不能证明 SQLite 与 registry 来自同一时点。
- 目录树复制使用no-follow walker，拒绝symlink、junction/reparse及非普通文件/目录。manifest逐项记录relative path、type、size与SHA-256；SQLite family还须只读打开并做integrity/schema检查。
- 每个entry先复制到transaction staging directory，验证manifest后再原子发布；已有target先备份或保持不覆盖。`use_existing_target`按entry合并/修复，target有其它runtime内容不能成为跳过source knowledge的理由。
- cleanup只能删除该transaction在`copied_entries`中明确记录且digest复验通过的source entry。任一复制/验证/发布失败时旧policy和旧根保持有效，target不成为current root，retained source不得清理。
- barrier获取失败稳定返回`knowledge_mutation_busy`并保持pending/retryable，不写成泛化migration failed。

验收：source只有`knowledge/`时也被识别；数据库、registry、override、ledger和嵌套job完整迁移；v1 completed+source knowledge+无target证据时进入repair且不cleanup；已有target其它内容仍逐entry补knowledge；嵌套link/reparse/special file失败关闭；在stage/verify/publish各点崩溃均不覆盖健康target；只有本transaction已复制验证的entry可被retained cleanup删除。

### FA：staging 只消费 canonical artifacts，根目录创建固定真实祖先

- 不再把 `.staging/.../knowledge.db*` 作为处理或恢复输入。只要实现仍是“检查路径，再 `sqlite3.connect(path)`”，跨平台 TOCTOU 就仍存在；本轮选择移除该可重开派生缓存，而不是继续叠加路径检查。
- staging可信输入只包括canonical pack、manifest、vectors和subscription；使用单个稳定句柄流式读取，直接将prepared embeddings交给`validate_prebuilt_index()`并由`install_pack()`重建entries/FTS/chunks，不创建`:memory:` SQLite，也不同时保留raw bytes、完整tuple与第二份数据库。
- 设计预算必须覆盖最坏内存峰值：10 MiB pack、2 MiB manifest和约30 MiB/5000 chunks vectors叠加Python对象后可达70–100 MiB。实现应尽早释放raw buffer、按阶段记录峰值指标并设置取消点；超过明确上界稳定失败。
- durable state可以记录验证进度，但重启后仍重新读取并验证canonical artifacts。历史queued/verifying job若只剩旧`knowledge.db*`证据，保留证据并进入`degraded/knowledge_staging_database_legacy`，要求显式discard/re-import；不打开、不静默删除、不假装可恢复。
- canonical artifact leaf在Windows必须用原生`CreateFileW(..., FILE_FLAG_OPEN_REPARSE_POINT, ...)`取得handle后校验类型/final path并从同一handle读取；不能继续使用`lstat -> os.open`窗口。POSIX继续使用同一fd与no-follow语义。
- `_create_trusted_knowledge_root()`在`mkdir`前逐层拒绝已经存在的symlink/junction/reparse，并在创建后复验final path。这里明确采用较窄威胁模型：防御预先存在或静态重定向祖先；连续同用户攻击者在检查与创建之间替换namespace不在本轮保证内。若将来要覆盖主动竞争，需另建`RootCapability`并把mkdir/open/replace/lock全部改成dirfd/native handle相对操作。
- root与`.staging`在写identity/state、lock或临时job前都执行同一静态祖先检查；文档、错误名与测试不得声称其已提供完整active-race capability。

验收：在旧validation/connect窗口替换主库或sidecar时，新实现没有任何`.staging/.../knowledge.db` connect，外部sentinel不变；合法prebuilt job重启后仅凭canonical artifacts仍可安装；旧DB-only job保留证据并明确要求re-import；大向量包峰值受预算和指标约束；Windows leaf reparse在原生handle层拒绝；预先存在的祖先symlink/junction指向外部时外部不产生文件，普通真实祖先仍支持首次创建。

### FB：注册表有界句柄读取，删除使用 durable intent

- `packs.json`建立唯一strict reader：POSIX从同一fd、Windows从`CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)`同一handle证明普通文件、非link/reparse、可信final path和当前大小，再最多读取`MAX_PACK_REGISTRY_BYTES + 1`。缺失与invalid/unreadable是不同状态；所有读取、状态、路由和mutation复用该入口。
- 定义并记录兼容现有健康registry的固定`MAX_PACK_REGISTRY_BYTES`；上线前扫描/fixture证明既有合法最大值低于上界。writer在任何数据库mutation前生成canonical UTF-8 bytes并预检大小。
- writer 先生成 canonical UTF-8 bytes并检查同一上界，再 atomic replace；生成超限时旧 registry和 live rows均保持不变。读取期间增长、非法 UTF-8、JSON/schema 错误都失败关闭。
- SQLite与JSON无法组成单一事务，因此remove采用registry-first有界journal。intent绑定operation ID、pack/source identity、before/after registry digest与canonical target bytes；不增加容易形成第三份真相的SQLite marker table。
- 删除顺序固定为：strict-load registry并预检target bytes/digests → 写/fsync intent与父目录 → atomic publish/fsync registry-after（目标包先停止被服务）→ 单个SQLite事务幂等删除source → 复验删除 → 最后删除/fsync intent。失败时保留intent供recovery，不回写伪造旧状态。
- recovery在服务初始化和任意pack mutation前运行：registry==before时publication未提交，只有DB也未变才清理或重试intent；registry==after时幂等完成DB删除再清intent；第三digest、损坏intent或DB证据冲突进入`pack_registry_recovery_required`，不猜测重建。
- `install_pack()`拆成已持锁private入口，避免内部重新获取registry/jobs锁。全局锁序固定为`root barrier -> package guard -> jobs lock（若需要）-> registry lock -> SQLite transaction -> state/ledger`；所有pack mutation先完成recovery，并在intent未收敛时阻塞。
- journal的保证明确只覆盖本轮remove；其它SQLite+JSON mutation除非采用同一协议，不得被宣称具有跨文件崩溃原子性。

验收：registry是链接、目录、设备、FIFO、reparse或上界加一时不跟随、不无界读取且mutation不触碰SQLite；既有最大合法registry仍可读写；分别在intent fsync、registry-after publish、SQLite commit、delete verify和intent cleanup后崩溃，重启均收敛为旧状态完整保留或删除完整完成；第三digest/DB冲突稳定degraded；并发install/remove/recovery的锁序测试无反向获取。

### FC：live commit 后执行可重试 retention，不提前删除旧终态

- activation commit 是现存 active job 的提交证明；只要对应目录仍存在，就不能因第 101 次 activation 被 ledger 独立裁掉。
- retention允许受控瞬时101：先完成当前live install、当前job terminal目录、receipt/state与activation commit，确保新旧证据都完整，再在jobs lock下选择并删除最旧terminal目录。
- 删除顺序为：复验证明victim是可信直接子目录且terminal → 删除目录 → fsync staging parent → 删除对应旧commit并持久化ledger。任一步cleanup失败都保留第101份证据并标记`retention_pending`，后台/下一次mutation重试；不得回滚已live的新包或先删commit。
- 设置小的hard overflow门槛（初始建议102，最终常量需测试证明）：101/cleanup失败可继续恢复，达到门槛后才拒绝新的activation并返回`knowledge_job_retention_pending`，避免无界增长。
- `list_pack_jobs()`等read path只报告状态，不执行破坏性prune。reconciler可删除“无对应目录”的孤儿commit；可信active目录的commit永不删除；failed/cancelled目录没有commit且可独立prune。
- victim排除current、nonterminal、degraded、orphan或身份不可证明目录，排序使用可信terminal time加`job_id`稳定打破平局。

验收：100个有效terminal目录激活第101个job时先完成新job全部提交证据，再删除确定的最旧目录及其commit，最终保持100；注入rmtree/fsync/ledger失败时新旧101份证据均可验证并出现`retention_pending`；恢复后收敛到100；达到hard overflow才拒绝后续activation；list不删文件；目录删除后、ledger写回前崩溃只产生可安全清理的孤儿commit。

### FD：routing 读取失败明确传播，刷新只发布完整快照

- `KnowledgeStore.load_routing_entries()` 成功时返回同一读事务中的 revision与完整records；SQLite、revision转换、row JSON或identity解析任一失败均抛出，不得返回 `(0, ())` 或跳过坏row后宣称完整。
- 合法健康空库仍返回当前revision与空tuple。`_safe_load_records()` 是唯一把领域错误转换为 `None` 的自动路由边界，并记录不含用户数据的限频诊断。
- `KnowledgeRoutingState.refresh()`以single-flight执行，只在records完整加载且generation仍一致时原子发布新snapshot并清dirty；必须在赋值`_snapshot`之前复验generation。加载失败保持旧snapshot对象和dirty；首次失败且没有旧snapshot时只尝试一次并快速miss。
- 有last-good时reader立即返回经过visibility过滤的旧snapshot，并只调度一个重试。重试采用monotonic指数backoff加jitter与上限，generation变化只使当前结果失效并安排下一次重试，不能在一次refresh内自旋。
- disable/remove/policy mutation同步递增visibility generation并写入pack/source tombstone；旧snapshot在匹配时先应用tombstone/fail-closed，直到成功refresh发布包含该generation的新snapshot后才清理相应tombstone。因加载失败不得继续服务已撤销内容。
- 成功加载合法空集合允许清空旧路由；后台refresh失败时并发reader继续读取过滤后的旧immutable snapshot，不出现短暂空窗口，也不形成每次match都访问坏SQLite的重试风暴。

验收：空数据库是成功空快照；SQLite busy/损坏row/override损坏均保持dirty且不覆盖旧snapshot；100个并发match只产生一个refresh并遵守backoff；disable/remove后即使refresh持续失败也不再命中被撤销包；generation在加载期间变化时旧结果从未赋值且不自旋；输入恢复后原子切换，健康删除全部records可发布空快照并清对应tombstone。

### FE：自动上下文以真实会话和逐卡身份准入、过滤和记账

- task admission在锁内先清理done槽位，再检查同session single-flight，最后检查全局active count；只有容量未满时才调用factory并创建task。返回值区分 `started`、`session_busy`、`capacity_exhausted`，对应稳定diagnostics。
- opaque key表示logical user session，而不是角色名、request ID或transport session。hot-swap/prewarm创建的pending session在promote时继承logical key；只有真实会话结束后新建的session才轮换key。
- session teardown先撤销结果交付并取消async wrapper，但底层task仍被强引用、仍计入全局容量，直到真实done callback运行；不能通过从table删除仍运行task释放虚假槽位。容量满诊断包含deadline/oldest-active-age等有界信息，便于识别饥饿而不泄露用户内容。
- 建立规范 `KnowledgeCardIdentity(source_tag, normalized_title)`。`KnowledgeTurnContext`携带按最终渲染顺序排列的全部card identities，旧的首卡title/source只保留为兼容诊断。
- 检索前取得该session未过期identity集合，以keyword-only、默认空集合的`excluded_identities`传给service；在knowledge/corpus配额和总limit截断前排除，并补取足够候选，使冷却首卡不占名额、新卡和候补卡仍可交付。
- renderer返回“最终2000字符文本实际包含”的identity列表；整张被截掉或只存在于候选中的卡不进入context identity。只有该context确实交付给当前turn后才批量记录实际插入identity。
- `KnowledgeTurnContext`新增字段均有向后兼容默认值，调用者不被迫同步位置参数。miss、timeout、busy、capacity、异常、取消或已撤销delivery均不写cooldown；检索与渲染过程不持全局锁。

验收：32个未完成session后第33个请求在factory执行前被拒绝并带oldest-age诊断；teardown一个仍阻塞task后容量仍为32，真实done才释放；同角色两个logical session互不共享cooldown，hot-swap promote继续继承原key；A+B候选但B被2000字符截掉时只记录A；A冷却时补取B/C；旧调用者不传新字段仍可运行；真实end后的新session不继承旧窗口。

### FF：公共知识工具先产生同一 turn 的 provisional evidence

- tool执行结果增加仅进程内消费的内部evidence字段，默认空且serializer省略；由registry private wrapper或内建provider注入，remote/user metadata不能声明。公共知识handler成功时只产生provisional `knowledge_used`，不能直接把整个turn设为exclusive owner。
- offline/realtime在执行前捕获immutable session token与provider-captured host turn ID；await后只向同一仍存活session/turn提交evidence，绝不重新读取可变`_active_text_request_id`。迟到、结束、切换session/turn的结果直接丢弃。
- turn end仅在保守证明“纯公共知识问答、没有其它tool、没有task creation/analysis intent、且session/turn仍匹配”时，把provisional evidence提升为exclusive `public_knowledge` owner并跳过普通后台分析。
- compound请求（如“查X并创建任务”）、multi-tool、miss、timeout、error或无法可靠识别pure intent全部fail-open，继续普通分析。若当前路由层没有可靠pure-intent信号，本轮只能把`knowledge_used`作为nonexclusive hint，不得为了关闭评论强行设置owner。
- 正常、取消、异常和discard路径清理turn-scoped evidence/owner，不能污染下一轮；wire output不向模型或插件序列化内部字段。

验收：纯公共知识问答成功后对应turn end可提升为`public_knowledge`；“查X并创建任务”、公共知识+其它tool、miss/timeout/error均不取得exclusive owner且普通分析仍运行；旧turn结果在新turn活跃时不能借可变request ID写入；remote metadata伪造evidence无效；offline与realtime使用同一组反例。

### FG：向量状态由单一枚举映射颜色、文案和概览

- 建立纯函数`packVectorState(pack)`，先把非法/负数count clamp到非负且把ready clamp到total，再固定返回`none`、`complete`、`building`、`partial`或`waiting`：无chunks为none；ready达到total为complete；embedding enabled且ready不足为building（包括ready为0）；disabled但已有部分ready为partial；其余为waiting。
- 单包class与label只映射该enum，不得再次读取ready/total/enabled。概览是多包状态，不复用最后一个单包：新增纯函数`aggregateVectorState(states, globalSnapshot)`，用显式优先级聚合全部pack enum，并仅在没有可聚合pack时使用global fallback；overview tone/meta只映射聚合结果。
- 本轮沿用既有i18n协议；partial使用现有warning与未完成类文案，不新增翻译键。若产品要求“部分可用”新文案，另行作为用户文案变更处理。

验收：单包表驱动覆盖none、complete、zero-ready building、partial disabled、zero-ready waiting、负数和ready>total；多包表驱动覆盖状态优先级、输入顺序不变性与global fallback，断言class、label及aggregate overview一致；API字段和现有i18n key保持不变。

### `discussion_r3878657157`：schema 6 fixture 是误报

`tests/unit/test_knowledge_hybrid_real_model_eval.py::_write_vector_database()` 写入schema 6，随后 evaluator调用生产 `assert_supported_schema()`。当前schema为7，但生产契约明确接受不高于当前版本的旧schema；只拒绝future version、marker冲突/缺失和非规范marker。使用 `uv` 运行精确回归 `test_vector_corpus_excludes_disabled_entries` 得到 `1 passed in 2.82s`，证明不会在disabled-entry断言前因schema不兼容失败。本线程不修改fixture；回复该调用链和测试证据后直接resolve。

## 第三十轮实施顺序与关闭条件

1. 文档修订提交：本节、风险边界、状态机、锁序与评论矩阵。
2. 关闭屏障提交：EW 的deadline-aware cancellation helper、全部knowledge writer tracking、稳定anchor root barrier与launcher旧进程证明。
3. 迁移提交：EZ 仅在EW完成后升级checkpoint schema、修复v1 evidence、接入transaction staging/no-follow verify，再迁移`knowledge/`；两项顺序不可交换。
4. staging提交：FA 移除文件型SQLite，改为canonical artifact直接重建、内存预算、Windows native leaf handle和明确收窄的ancestor威胁模型。
5. 注册表提交：FB 的Windows/POSIX strict reader、registry-first remove journal、recovery gate及全局锁序重构。
6. retention提交：FC 采用post-commit受控101、`retention_pending`和hard overflow门槛；不得与FB锁序重构并行落地。
7. 路由提交：FD 严格错误信号、single-flight/backoff、last-good发布与visibility tombstone。
8. 会话提交：FE 的logical session lifecycle、真实task容量、渲染后card identity及兼容默认值。
9. 工具归属提交：FF 的session/turn token、provisional evidence与pure-intent提升规则。
10. 退订提交：EX 的operation ID、Main durable status、guard transfer和absolute deadline；依赖FB remove终态可查询。
11. 浏览器提交：EY 移除browser apply能力并更新route-order、文档与既有相邻测试；Plugin内部apply保持。
12. 前端提交：FG 单包enum、多包aggregate与count clamp。
13. 回归修正提交：只包含上述边界暴露的必要修正，不扩张无关协议。

每一步独立commit以便审查，但本轮全部实现、测试与回复证据完成后统一一次push，不逐个推送。Python测试只通过项目 `uv` 环境运行；扩展既有测试文件，不新建测试文件。前端运行现有Knowledge Manager/Vitest回归；同时执行受影响Ruff、compileall、构建/类型检查与`git diff --check`。

### 第三十轮本地实施证据

| 单元 | 提交 | 实施结果 |
| --- | --- | --- |
| EW | `5c027f664` | shutdown step、writer admission/drain、稳定 root barrier 与旧 Main 退出证明统一到真实 mutation 终态。 |
| EY | `9cc2458f4` | 浏览器 Bridge 移除 `subscriptions/apply`，可信 apply 仅保留在 Plugin 内部路径。 |
| FG | `d09f278a8` | 单包向量状态与多包概览分别通过纯函数推导，颜色、文案和聚合不再分叉。 |
| FD | `5737cd867` | routing 加载失败保留 dirty/last-good，visibility tombstone 阻断已撤销内容。 |
| FE | `23349295a` | 自动上下文按 logical session 准入，底层 task 真实结束前持续占槽，并逐张记录实际渲染卡片身份。 |
| FF | `0f0a1f790` | 工具结果只向捕获的 session/turn 写 provisional evidence，复合请求不错误取得 exclusive owner。 |
| EZ | `48bc34ad5` | `knowledge/` 进入 schema v2 逐项摘要迁移，采用 transaction staging、no-follow 验证、SQLite 检查和可恢复 retained cleanup。 |
| FA | `d1d4f0f85` | staging 仅从 canonical artifacts 重建，legacy DB-only job 明确 degraded；Windows leaf 使用 native handle，验证峰值受 128 MiB 预算约束。 |
| FB | `f1bfb6d18` | `packs.json` 统一 32 MiB strict handle reader；删除采用 registry-first intent、digest 判定和幂等恢复。 |
| FC | `daf43bf7b` | activation 证据先完整提交再执行 retention；失败保留受控 101 和 `retention_pending`，102 hard overflow 才阻止新激活。 |
| EX | `e2e42c099` | Plugin 使用单一 absolute deadline 与 operation ID；Main 持久化有界 remove status、强引用删除任务并提供查询；不确定结果把 package guard 转交同一 operation drain。 |
| schema 误报 | 无代码提交 | schema 6 继续由生产兼容契约接受，使用既有精确回归作为关闭证据。 |

统一推送前回归：受影响 Python 集合通过 `uv run pytest` 得到 `673 passed, 28 skipped`；Plugin Manager Vitest 得到 `40 files / 377 tests passed`；`vue-tsc --build`、Vite production build、受影响 Ruff、compileall 与 `git diff --check` 均通过。Windows 下无法替换已打开文件的 descriptor 竞态反例按平台能力 skip，同时结构反例继续证明 validated handle 与 shared descriptor reader 之间没有按路径重开。

统一推送后新增的 3 条 review thread 由 follow-up 提交 `5bd13b100` 一并处理：取消尚未安装完成的 update 时先查询 durable registry，只有证明不存在旧安装才返回 preinstall success；Main 的 removal status GET 会恢复持久化为 pending 但丢失进程内 task 的同一 operation ID；index-policy 改为 registry-first，并且 indexer 每轮在选择 embedding work 前以 registry 为权威修正 SQLite chunk policy。相关 indexer、packs、Plugin/Main unsubscribe 集合为 `224 passed, 1 skipped`，Ruff、compileall 与 `git diff --check` 通过。

关闭条件：18条有效conversation分别具备实现提交、精确反例和相邻宽回归后才回复并resolve；schema误报附上述1 passed证据单独关闭。最后再次使用GraphQL完整分页读取全部review threads，不能受100条限制影响。只有远端head包含全部实现、统一推送成功、CI与分页结果齐全后，才能把文首状态改为“第三十轮已实施”。

### 第三十轮远端复审追加：16 条 conversation 的边界复核

提交 `a6a6a9e44` 推送后，CodeRabbit 新增 16 条 conversation。逐项按当前实现、失败语义和第三十轮威胁模型复核后，14 条成立并由 `160e7b75e` 统一修复，2 条 Windows 路径放宽建议不成立：

- `discussion_r3886449367` 与 `discussion_r3886449374` 建议允许父目录 junction、映射路径或 8.3 别名与最终路径字符串不一致。FA/FB 的安全边界明确要求 canonical artifact leaf 与 registry leaf 的已声明路径不得经父目录重定向逃出可信根；拒绝合法但非 canonical 的别名是保守可用性取舍，不是身份绕过。仅比较文件名或对已经由该路径打开的同一 handle 再做 file-ID 自证，无法证明父目录仍位于可信根，故不放宽。
- removal operation registry 现在拒绝布尔时间戳；pending 最多 32 条，超过 24 小时的旧 pending 在下一次写入时转为带 `removal_operation_expired` 的明确 failed 终态，再按 terminal retention 管理。活跃 pending 不被静默删除，容量满时拒绝新增 operation。
- routing revision 与 entries 在显式 SQLite read transaction 中读取；迟到 discard 只有仍拥有共享输出时才能清工具证据。
- 所有改用 writer admission 的公共知识 mutation 将关闭状态统一映射为 `knowledge_mutation_stopping`；pack recovery-required 在设置与 resumed remove 中不再被 `ValueError` 分支误报为 not-found 或幂等成功；pending status 明确返回 `ok=false/removal_in_progress`。
- retained-source cleanup 删除完有证据条目后复查所有 runtime entry；仍有无证据条目时返回 409、保留路径和 checkpoint，不再落盘 `cleaned`。storage migration 在 copying/preflight 失败且尚未发布时 best-effort 删除 transaction staging；进入 verifying/committing 或 rollback-required 后不销毁恢复证据。
- Plugin 对明确的 Main 4xx `main_server_rejected` 直接返回 `subscription_removal_rejected`，只有 timeout、传输/5xx 与无效响应才查询同一 operation 的终态。
- 测试 teardown 会取消并等待遗留 removal tasks，shutdown writer 测试用 `finally` 恢复 admission，迁移 SQLite fixture 在目录替换前显式关闭连接。

精确回归通过 `259 passed, 8 skipped`；统一推送前扩大的知识库、路由、迁移、shutdown、turn 与 Plugin 集合通过 `668 passed, 14 skipped`。受影响文件 Ruff 与 compileall 通过，`git diff --check` 通过。上述 14 条成立 conversation 只有在远端 head 包含 `160e7b75e` 后才回复实现与测试证据并 resolve；两条不成立 conversation 回复威胁模型依据后 resolve。完成后仍须重新全量分页，因为审查机器人可在同一次 pending check 中继续新增线程。

### 第三十轮最终分页追评：4 个同步边界缺口

远端 head `f8818d13d` 的全量 GraphQL 分页复核新增 4 条有效 conversation；它们是已实施单元的同步边界遗漏，不改变 EZ → FA → FB → FC → EX 的总体设计：

- `discussion_r3886492693`：`_knowledge_query_candidates()` 在检索 deadline 之前处理用户文本，原实现对输入长度和候选数量无上限，并用 list membership 二次扫描。现在先把输入限制为 4096 字符，以 `set` 做常数时间去重，候选总数限制为 32；保留一个槽位给截断后的原始查询，使拆句优化不会吞掉基础检索表达。
- `discussion_r3886492696`：社区来源展示元数据原先直接 `Path.read_text()` 读取 `packs.json`，绕过 FB 的 32 MiB、regular-file、no-follow trust path。现在它复用 `_load_registry()`；超限、link/reparse、非 UTF-8、损坏 Schema 均失败关闭为 Unknown，不发布不可信来源元数据。
- `discussion_r3886492697`：Marketplace 等待 staged job 时，Main Server 的单次 timeout 与 unavailable 同属可重试观察故障。现在两者都只在既有 24 小时总 deadline 内继续轮询；4xx 拒绝、无效响应和稳定 job 终态仍立即失败，未扩大重试集合。
- `discussion_r3886492699`：rebuild 的 chunk 计数为零不能证明所有 entry 已成功 backfill。只要 `entries_missing_chunks > 0`，完成状态现在明确为 `backfill_incomplete`、`complete=false`，因此 malformed/无法派生的 entry 不会被静默报告为成功。

实现提交 `f4ec75a0f` 同时加入对应反例。四个受影响测试文件通过 `224 passed, 1 skipped`；知识、存储、shutdown、turn 与 Plugin Market 扩大回归在 UTF-8 模式下通过 `2021 passed, 16 skipped, 21362 deselected`。第一次使用 Windows 默认编码运行时，唯一失败是既有测试未指定编码而以 GBK 读取 UTF-8 源文件；相同集合用 `uv run python -X utf8 -m pytest` 重跑全绿，因此未把该环境问题混入本轮实现。受影响 Python 文件 Ruff、compileall 与 `git diff --check` 通过。关闭条件保持不变：该提交推送到远端后逐条回复实现与测试证据并 resolve，再对全部 review threads 完整分页，确认没有新未解决 conversation。

### 第三十轮第二次最终分页追评：状态原子性与 override 读取边界

远端 head `055b05210` 的下一次完整分页又新增 2 条有效 conversation：

- `discussion_r3886524194`：过期 pending removal 原先只在 `_write_operations()` 的局部映射中转成 failed。`begin_removal_operation()` 仍返回写入前的 pending 对象，状态查询也不会主动持久化过期，因此首次查询可能创建恢复任务并继续执行删除。现在 begin、complete、get 都在同一 `mutation_lock` 内通过 `_load_operations_for_access()` 读取；过期转换原地更新 payload、立即原子写回，并把 retention 后的实际持久集合回填给调用方。状态查询首次看到过期记录即返回 `removal_operation_expired`，不会创建恢复任务。
- `discussion_r3886528857`：`catalog.override.json` 原先用 `Path.read_text()` 无界跟随读取。现在读取复用 strict regular-file/no-follow handle reader，采用固定 32 MiB 上限；写入在 atomic publish 前用同一上限检查 canonical UTF-8 bytes，避免生产出下一次无法读取的合法文件。超限、symlink/reparse、特殊文件、非 UTF-8 与损坏 JSON 均保持 `CatalogOverrideError` 失败关闭语义。

实现提交 `77904dd0d` 加入“首次状态查询不恢复过期删除”、有效但超限 override 读取失败和 writer 发布前拒绝三个反例。精确回归 `76 passed`；加入 catalog override 的 lexical/vector/evaluator 主要消费者后扩大为 `155 passed`。Ruff、compileall 与 `git diff --check` 通过。该提交推送后与前 4 条一起回复并 resolve，随后再次对全部 review threads 完整分页。

第三次分页新增 `discussion_r3886562997`。该评论关于诊断优先级的部分成立：`failed_exhausted` 仍是最高优先级；其后只要 `entries_missing_chunks > 0` 就返回 `backfill_incomplete`，不能被可重试 chunk 的 `retry_scheduled` 掩盖。提交 `5e8b5fa55` 增加两个计数同时非零的组合反例，rebuild 脚本文件通过 `34 passed`，Ruff、compileall 与 `git diff --check` 通过。
