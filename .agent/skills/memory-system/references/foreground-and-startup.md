# 前台不阻塞、启动链路不阻塞

这两条是全项目的原则，不只管记忆。记忆服务是最常违反它们的地方，所以放在这里。

## 1. 前台不阻塞

原则：**无论什么情况，后台的慢操作都不能卡住正在进行或正要开始的对话。** 落到记忆服务上是三句话：

1. **冷启动**（开会话、Offline VLM 交接）可以等记忆服务，但记忆服务必须能**打断**占着锁的慢操作，让 `/new_dialog` 先过。
2. **热重置**时记忆服务超时，不可以影响正在进行的对话。
3. ⚠️ **「`/new_dialog` 超时就先不带记忆开会话、后台再补注入」这种降级是被拒绝的方案。** 不要再提，也不要在评审建议下加回来。要的是前台路径对后台慢操作有结构上的免疫，而不是牺牲记忆上下文。

设计这类修复时的方向：前台请求可以抢占后台持锁的工作，被打断的工作稍后重做；热重置失败就保留旧会话继续。只修某一个占锁来源是治标。

### 现在是怎么做的（#3601）

问题形态：`/renew`、`/settle`、`/process` 持着每角色的 settle 锁跑摘要模型，带重试可以跑几分钟；`/new_dialog` 要等这把锁；主服务这边的记忆写入又是逐条发的，最新几轮可能排在那次摘要后面。

冷启动一侧（`main_logic/core/lifecycle.py`）：

1. `_flush_memory_writes` 先 `POST /preempt_summary/{角色}`：打断该角色正在跑的摘要，并开一个窗口（`PREEMPT_SUMMARY_WINDOW_S`），窗口内新开始的摘要一上来就是被打断状态。
2. 往同步队列放一个 `memory flush` 屏障，等连接器把已排队的写入发出去，最多 `_MEMORY_FLUSH_WAIT_S`。尽力而为：超时只意味着开场上下文可能缺最近几轮，这几轮之后照常入库。
3. `GET /new_dialog?preempt_summary=true`。前台调用的超时很短；超时或失败就是开会话失败并报错，**不降级**。

记忆服务一侧（`app/memory_server/runtime.py`、`routes.py`、`memory/recent.py`）：

- 三个会产出摘要的端点用 `runtime._summary_preemption(角色)` 包住摘要调用，把得到的 future 传给 `update_history(preempted=...)`。future 一完成，进行中的摘要调用被取消。
- **被打断按「摘要失败」处理**：消息本身已经落盘，快照经 `on_compress_done` 交给已有的后台压缩去补。排在冷启动前面、刚拿到锁的摘要也直接跳过模型调用。
- 冷启动的 `/new_dialog` 结束时关窗口。打断只影响同一个角色。
- 摘要模型调用本来就在文件锁之外跑；不要把它挪进任何临界区。失败回调也必须在所有临界区之外（它会同步去拿同一把不可重入的文件锁）。

热重置一侧：

- 准备热重置的 `/new_dialog`（`_new_dialog_request_kwargs(foreground=False)`）**不打断**摘要，改用更长的超时 `_HOT_SWAP_NEW_DIALOG_TIMEOUT_S`：旧会话还在服务，新会话应该从做完的摘要开始。超时只是丢掉这次准备好的会话，下一轮再试，不弹错误提示。
- 这些常量放在 `main_logic/core/_shared.py`（core 下各 mixin 模块的形状由 `scripts/check_core_contracts.py` 管着）。

主服务到记忆服务的写入（`main_logic/cross_server.py::run_sync_connector`）：

- 每个角色一条串行的 `memory_lane`，所有记忆写入（cache / renew / settle / process / 屏障回调）都进它，转发循环本身不等记忆服务。
- 回合结束的 `/cache` 被拒时，内容放进 `memory_carry`，随下一次写入重发。
- 连接器停止时先等 `memory_lane` 排空，最多 `MEMORY_LANE_DRAIN_TIMEOUT_S`，之后取消；还没发出的写入放弃。

守护测试：`tests/unit/test_settle_summary_preemption.py`、`tests/unit/test_cold_start_memory_flush.py`。

### 同类评审意见

- 「打断应该计入摘要的失败退避」：不采纳。打断不是摘要模型的失败；退避只在后台压缩自己失败时计数。
- 「冷启动读不到还在排队的写入」：先打断、再等屏障（上面第 1、2 步）解决的是「写入还排在队列里没发」。屏障完成只表示排在它前面的写入都发出过一次，不表示都已入库；屏障超时则连发都未必发完。两种情况开场都可能缺最近几轮，是已知限制，评审提到时按此回复，不靠延长等待去修。发出去之后失败的写入怎么处理，各条路径不一样：回合结束的 `/cache` 失败会放回 `memory_carry`，随下一次写入重发；热重置和会话结束的结算失败只记日志，不重发。以 `main_logic/cross_server.py` 里各个写入函数为准。
- 「超时后无记忆开场」：见上，拒绝。

### 新加记忆服务端点或后台任务时

- 会持 settle 锁、角色锁跑模型调用的新路径，先想清楚冷启动的 `/new_dialog` 撞上它会等多久；等得久就接入打断，或者把模型调用挪到锁外、锁内只做读盘和拼接。
- 回复前的检索类调用要有硬预算，超时就丢（被动注入共用 1 秒预算是现成例子）。不要为了「结果更全」让回复等。
- 后台任务不要现场占用回复路径共用的资源（例：合成前查重只读已经回填好的向量，不现场算 embedding）。
- 入队等锁之类发生在请求路径上的旁路操作，要有短超时并允许放弃（例：`FACT_NEAR_DUP_ENQUEUE_TIMEOUT_SECONDS`）。

## 2. 启动链路不阻塞

原则：**从双击到能对话这段路，不许被次要子系统拖慢；任何 PR 都不能让启动变慢。** 这类回归是静默的，什么都不报错，只是变慢。

要知道的事实：

- 分发版是合并单进程模式：`launcher_core/runtime.py::run_merged_servers` 在任何端口绑定之前**串行** import 三个 app 模块。所以模块顶层 import 进来的一切，都算在每次启动的关键路径上。三个子系统共用事件循环（见 neko-guide），判断「这一步慢不慢」不能只看它属于哪个服务。
- 重 SDK 一律「函数内延迟 import + 后台预热」：在首次用到的函数里 import，并登记进 `utils/module_warmup.py`，由服务就绪后的后台线程预先 import。`scripts/check_startup_import_lazy.py`（CI）禁止名单里的模块回到模块顶层。模块顶层的重计算同理（例：大批正则改成首次使用时编译，`tests/unit/test_startup_lazy_directive_patterns.py`）。
- **插件生命周期（全量元数据扫描加逐个自启动）不在启动链路上。** `plugin/server/http_app.py::build_plugin_server_app(manage_lifecycle=False)` 默认不认领生命周期；嵌在 agent 进程里时由 `user_plugin_enabled` 开关在外部驱动，只有独立入口才传 `True`。⚠️ 这个参数默认值的方向是有意的：反过来错只是「独立服务里插件不自启」，一眼能看出来；这一侧错了，全量扫描回到端口绑定之前，没有任何报错。守护：`plugin/tests/unit/server/test_plugin_lifecycle_ownership.py`。不要再用「import 时读环境变量」来决定这件事，那依赖两处相距很远的代码的先后顺序。
- 区域探测（`awarmup_region_check`）：主服务和记忆服务在放行请求之前等它（有上限；用自定义 API 的用户不探测、零等待），因为会话一开始路由就定了。agent 进程里它是后台任务，不挡启动。
- agent 进程启动时只登记 NekoClaw 宿主，不构建任何东西；`mount_nekoclaw_host` 必须没有 I/O。
- 记忆服务启动：事件日志的对账是**等到完成**的（它必须在 outbox 重放之前，理由见事件日志文档）；outbox 重放只派生任务、不等；其余周期任务靠 `app/memory_server/gates.py` 里错开的 `_INITIAL_DELAY_*` 在启动之后才第一次跑。

往启动路径上加东西之前：

- 这一步是不是「能对话」所必需的？不是就放后台任务（`create_task`、带初始延迟的循环），并保证它失败只记日志、不挡启动。
- 新的周期任务加一个 `_INITIAL_DELAY_*`，和现有的错开，不要在启动钩子里 `await` 第一轮。
- 启动路径上会抛的探测（建目录、探路径、读配置）放进 try 里：迁移做不了是「跳过一次迁移」，不是「启动失败」。
- 怎么查：`NEKO_DEBUG_ASYNC=1` 看慢回调告警（neko-guide）；事件循环卡顿看门狗在 `utils/event_loop_watchdog.py`；`uv run python scripts/check_startup_import_lazy.py`。
