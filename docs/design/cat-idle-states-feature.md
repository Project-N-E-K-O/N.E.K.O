# 猫娘空闲状态与 Cat Mind 功能说明

## 一、文档定位

本文是猫形态功能的当前总说明，统一描述玩家看到的功能、现有表现链、Cat Mind 状态机、桌面边界和 return 经历链路。它不是历史方案，也不记录逐次实施过程。

精确的五维初始值、自然变化、事件反馈、动作公式、阈值、冷却、测试包络和返回提示词统一收在另一份数值规则文档中，避免产品说明与调参细节互相覆盖。

## 二、产品目标

用户“请她离开”后，角色模型隐藏，原有回来入口变成可停留、可 hover、可拖拽、可点击回来的猫形象。长时间无有效交互时，系统复用既有 goodbye 链自动进入猫形态，并从清醒逐步过渡到打盹、睡觉。

猫形态不只是定时播放素材。Cat Mind 根据时间、用户互动、聊天窗状态、现有表现结果和自主动作结果维护一组内部需求，再在安全条件内选择少量既有动作。用户叫她回来时，系统最多把最近一段真实、已完成的猫形态经历压缩成结构化摘要，交给模型自然带入本次回归对话。

体验目标：

1. 从不或只有少量交互时，持续平均保持状态机接入前约 3–5 分钟一次的克制生命感，并由评分自然轮换动作。
2. 正常交互要比无/少交互更早出现回应、在一段时间内有更多 started；普通交互档位的差异来自五维累积，不把一次 hover 或拖拽直接翻译成固定动作。
3. 短时间高频 hover/拖拽会消耗精力，也会通过五维提高她当下想回应、想活动的驱动力；把毛线球从远处拖到猫旁或反复在猫旁递毛线，则会额外形成权重更明显、但仍短时衰减的玩毛线意图。在 gate 和 runner 可用时，大量交互应明显提前首响；高频输入停止后由动作反馈和自然流恢复普通节奏，毛线意图也会自行衰减。点掉气泡则满足当前回应，不能继续给下一次气泡加分。
4. 猫形态动作必须来自当前真实环境和既有 runner，不能为了显得聪明而虚构能力。
5. 回归对话只叙述可证明的完成经历，不复述事件流水账，也不混入午饭、时段等其他主动问候。

## 三、核心语义

| 概念 | 当前语义 |
|---|---|
| `CAT1` | 清醒猫形态；允许轻声回应、吃零食、小移动、玩毛线 |
| `CAT2` | 打盹猫形态；只允许小憩反馈 |
| `CAT3` | 熟睡猫形态；只允许熟睡反馈 |
| 点击猫 | 仍是“请她回来”，继续走既有 return 链 |
| 自动 idle | 自动触发既有 goodbye，不复制 goodbye 业务逻辑 |
| Cat Mind | 猫形态内部状态、异步调度、动作选择和经历归并 |
| renderer adapter | 唯一可以启动既有动作 runner 的层 |
| 短时意图证据 | 对当前明确互动目的的短暂、会衰减的选择器上下文；不是第六维，也不是动作命令 |
| return episode | 最近一个可信自然段的短结构化概括，不是长期记忆 |

必须保持：

1. `CAT1 / CAT2 / CAT3` 是猫形态视觉分层，不是新的会话状态。
2. return 继续使用现有 `live2d-return-click`、`vrm-return-click`、`mmd-return-click`。
3. 自动 idle 继续派发既有 goodbye 事件，由原链隐藏模型并显示回来入口。
4. 已进入猫形态后，普通鼠标、键盘、滚轮和聊天活动不自动唤醒角色，也不重置 tier。
5. Cat Mind 只在猫形态内运行；debug 是否显示不影响其运行。

## 四、职责边界

### 4.1 输入、状态、动作三层

```text
现有 UI / avatar / desktop 事实
  -> observation
  -> Cat Mind 更新五维与最近事件
  -> 下一轮异步 decision
  -> hard gate / tier gate / provider gate / score
  -> DOM-free action request
  -> renderer adapter
  -> 既有 runner
  -> accepted / started / done | failed | cancelled | interrupted
  -> action result observation
  -> 下一轮异步 decision
```

任何 wakeup、observation 或 action result 只排入下一轮判断，不能在旧入口的同步调用栈中立即选择和启动另一个动作。

### 4.2 Cat Mind

Cat Mind 负责：

- 五维内部状态：食欲、困意、精力、社交需求、刺激需求；
- 30 秒自主时钟和按真实经过时间结算；
- observation 规范化、去重、分类和有界 recent events；
- 对明确互动目的维护会衰减、会饱和的短时动作意图证据；
- hard gate、tier gate、provider 结果与动作计分；
- pending request、active action、统一节奏分和动作自身软 cooldown；
- 严格动作结果驱动的 return episode 归并；
- return 时生成一次性结构化摘要。

Cat Mind 不写 DOM、不直接播放素材、不直接操作窗口，也不保存到长期记忆。

### 4.3 renderer adapter 与既有 runner

动作 adapter 读取当前 renderer 事实，判断动作是否真的可执行，并把 action request 翻译成既有 runner 调用。runner 仍拥有 GIF、音频、DOM class、目标跟随、取消、恢复和结束时机。

avatar 侧另有只读 observation adapter：它只汇总已有的毛线拖拽阶段、坐标和猫/毛线矩形，生成完整手势事实；不写 journey 状态、不选动作，也不启动 runner。输入适配和动作执行因此仍是两个方向明确、职责分离的边界。

只有 runner 真实进入 `started` 后，Cat Mind 才写 cooldown 并重置共享节奏分。provider dry-run 必须只读；provider 拒绝、adapter 接受后启动失败或终态先于 started 都不得写 cooldown、不得伪造完成反馈或经历。未确认的 request 最多占用 `5s`，已经 accepted 但没有 started 的 request 最多占用 `12s`，超时只释放调度租约并记录协议失败，避免 selector 永久卡住。音频动作只有 `audio.play()` 成功后才能报告 started，不能在仅创建 `Audio` 时提前开始冷却。所有已接入动作统一使用连续恢复曲线；cooldown 不从实际动作分数中扣除，只产生 `0–1` 的独立排序位。有多个合法候选时，更久没执行的动作优先；只有一个动作达到资格时，新鲜 cooldown 也不会阻止它执行。没有额外的连续重复 gate、负分或 idle 禁止结果。

动作进行中到达的用户 observation 不会丢失，也不会在旧回调内同步连播。Cat Mind 会把同类触发合并为一次 deferred reevaluation，在 runner 终态和表现恢复后异步重新计分。明确毛线意图暂时被 provider 拒绝时，后续既有 walk/stretch 完成可以作为“条件已变化”的唤醒事实；它们仍不是主动候选，也不直接指定玩球。

### 4.4 NEKO-PC

Web 主页面与 NEKO-PC 的 Pet renderer 页面运行同一套页面内 Cat Mind。NEKO-PC 桌面壳（main、preload 和 native bridge）只提供：

- 聊天窗最小化、compact、展开、移动、idle-dock 等 observation；
- 毛线球用户拖拽阶段、矩形和坐标空间等原始桌面事实；
- 跨窗口坐标和可见性同步；
- 毛线球临时隐藏与恢复；
- 桌面窗口命中、遮挡和层级安全。

桌面壳不持有 Cat Mind 运行状态，不维护五维、短时意图、cooldown、pending/active action 或 return episode，不运行 selector，也不发 action request。它只把真实桌面事实提交为 observation，并提供窗口、坐标与命中安全；后续计分、选择、request、runner 结果和 return 摘要仍由 Pet renderer 内的同一条 Cat Mind 链处理。

### 4.5 后端

后端只接收 allowlist 后的短结构化摘要，把枚举映射为服务端持有的自然语言 scene，再走独立的 `cat_greeting_check`。摘要只在本次调用中使用，不写数据库、长期 memory 或角色设定。

## 五、进入、分层与回来

### 5.1 手动进入

用户点击“请她离开”后，原 goodbye 链隐藏当前模型和浮动按钮，显示相应模型的 return-ball，视觉从 `CAT1` 开始。手动进入和自动进入使用不同的五维初始值和回归原因语气，但后续状态机相同。

### 5.2 自动进入与 tier

用户把启动默认形态设为猫时，启动链会以 `startup-default-form` 进入 CAT1，并按自动进入使用五维初始值和回归原因；它仍复用同一 Cat Mind、goodbye/return 和一次性摘要链，不另建启动专用状态机。

当前发布阈值：

| 阶段 | 累计 idle 时间 |
|---|---:|
| 自动 goodbye / `CAT1` | 10 分钟 |
| `CAT2` | 15 分钟 |
| `CAT3` | 18 分钟 |

教程或接管态、录音或语音会话、运行中的任务、聊天 grace window、核心 UI 未就绪等会阻断自动 goodbye。阻断解除后重新经过完整 idle window，不立即进入猫形态。

tier 变化会给五维施加与表现一致的边界：CAT2/CAT3 更困、更低能量；拖拽降回 CAT1 时恢复最低活动精力。tier 本身不直接证明她完成过休息动作。

### 5.3 点击回来

点击与拖拽通过位移阈值区分。点击回来时先取消 hover、拖拽和当前 CAT1 子动作，再派发当前模型的既有 return 事件。原链恢复模型、聊天区和浮动按钮；Cat Mind 同时冻结本轮摘要并清空运行态。

摘要只属于这一次 return：前端消费者读取后立即清除。无 socket、发送失败、短时静默或重复 return 都不能把旧摘要留给下一次。

## 六、Observation

### 6.1 时间

自主时钟每 30 秒产生一次 `cat_elapsed`，但五维按实际经过毫秒数结算。因此浏览器后台暂停后恢复时不会把每个漏掉的 tick 当成新事件，也不会少算真实时间。

`inactive_elapsed` 和 `since_last_action` 可作为判断触发事实，但不另造第六个需求字段。

### 6.2 用户互动

用户互动包括 hover 反应、猫形态本地文字、拖拽开始/结束/取消、rapid drag 和点击活动思考气泡。它们只更新现有五维、recent events 和章节边界，再排入下一轮判断。hover 很容易因指针经过而触发，因此只给低权重背景反馈；每条首次接受的本地文字给社交与刺激各 `0.10` dose，不读取或保存文字内容；普通拖拽更偏刺激与真实物理负荷；rapid drag 再显著提高短时玩耍/走动驱动力。这些观察仍只通过统一评分影响多个自主回应，同时真实物理活动才消耗精力；点掉气泡表示一次回应已经完成，会按当前剩余需求的比例降低社交和刺激，不再用固定扣分制造负值或反向刺激。自动声音反应即使复用 hover 素材，也不算用户 hover observation。

一段拖拽按完整手势结算：start 一次、rapid 至多一次、terminal 一次。普通结束与 rapid 结束使用不同的 terminal 反馈，不能把每个 move 帧、rapid 帧和 drag end 当成多次完整互动线性叠加；同一 `activityId` 的重复/别名 terminal 对交互剂量和物理负荷都只结算一次。社交/刺激证据按剩余空间饱和合并，高频真实互动仍能积累出明显梯度，但输入越密，每一笔新增的绝对空间越有限。

它们不直接启动吃、回应、玩球或移动，也没有“进入高互动模式”的特判。普通 hover、本地文字、拖拽和 rapid drag 不生成动作意图，只通过已有五维进入统一评分；有界需求贡献、共享节奏和可选短时意图共同决定动作资格，cooldown 只在已经达到资格的候选之间调整顺序。因此高互动可以更密，但不能随 observation 数量线性连发，也不能绕过 hard gate 和 provider。本地文字本身不生成 return episode；之后严格完成的既有动作仍按原规则进入一次性 summary，并继续服从统一的 180 秒回归问候门槛。

CAT1 本地文字另有固定 `5%` 的哈气回复彩蛋。它不是 observation 到动作的映射，也不进入 selector；回复执行时只通过哈气专用的窄表现入口请求既有独立伸懒腰 runner。runner 仍执行 CAT1、可见性、贴边、拖拽、return、transition、playground 和其他动作等全部限制，拒绝时回退普通猫叫。runner 确认启动后，同一次回复显示哈气文字和专用对话表情包，并播放服从猫形态音频开关的本地哈气音效；音效播放失败不撤销已经启动的视觉表现。该条文字仍只结算原有一次互动反馈，彩蛋不另写 cooldown、严格 action result、return episode 或 near-chat 伸展完成 observation。

### 6.3 毛线意图与物理活动事实

用户主动把毛线球从远处拖到猫旁，是较强的“想让猫玩球”证据；已经在猫旁仍有明显路径的递球是较弱证据，反复递球通过饱和合并逐步增强。avatar 的只读 observation adapter 统一接收桌面、自带毛线球、Wayland 和嵌入式 minimized chat 的拖拽阶段，在完整拖拽结束、既有 journey 完成几何同步后才发送一条结构化事实。正常路径等待双 RAF；后台页面暂停 RAF 时由带序列保护的短 fallback 释放 settling。拖拽 active 和紧随其后的 settling 都是 hard gate，原始 move 帧不会直接制造动作请求，也不能永久锁住选择器。

毛线意图只影响 `cat1_play_yarn` 的短时分数，并在保鲜期后持续衰减。它不是第六个需求字段，不修改五维，不把毛线 observation 翻译成 runner 调用，也不能越过 near-chat、毛线可隐藏或其他 provider 硬条件。它足以明显提高玩球的资格分；刚真实启动过的玩球会留下较高 cooldown 排序位，在还有其他合法候选时优先让位，但不会因此失去已经达到的动作资格。provider reject 和 `accepted` 都保留证据；对应 Cat Mind runner 真实 `started`，或既有 journey 局部玩球真实 `done`，才消费。

猫自身的真实位移则作为物理活动事实结算。return-ball 拖拽、既有 walk、compact 上缘落位和自主 small move 在完成或取消终态报告非空稳定 `activityId`、实际路径和持续时间；Cat Mind 用同一负荷曲线把它们换算成食欲、精力和困意的五维变化。同一个 `activityId` 只结算一次，start、move、rapid 等中间事件不重复收费；取消只结算已经发生的路程，不领取成功到达或动作完成反馈。没有稳定 ID 或没有实际正路径就不结算。物理负荷描述“猫实际动了多少”，短时意图描述“用户此刻想让她做什么”，两者不能混为一个状态。

### 6.4 聊天窗和桌面

聊天窗最小化可见、移动较远、compact 表面、idle-dock、重新展开和桌面层级变化都只是环境 observation。它们为 provider、near/far 和安全判断提供事实，不直接给五维加分。Native IPC、BroadcastChannel 和本地 UI 可能重复送达同一状态；Cat Mind 按最小化状态和矩形去重，同一状态、同一 rect 的心跳不重复记录或制造判断机会。

### 6.5 既有表现结果

走到聊天球旁、journey 请求的伸懒腰结束、compact 上缘落位/掉落、拖拽后边缘探头等是已有表现的结果。它们可以反馈五维，但不是 selector 主动候选。

### 6.6 自主动作结果

只有与当前 `actionId + requestId + runId` 匹配的 adapter/runner 结果才能结束 active action。`done` 回写动作完成反馈；`cancelled / failed / interrupted` 按各自语义处理。公开 observation 不能冒充严格动作结果写入 return episode。

## 七、选择器与动作池

每轮按以下顺序判断：

1. Hard Gate：return、猫拖拽准备/进行、毛线拖拽 active/settling、CAT1 贴边半隐藏、tier transition、其他独立动作、不可见 return-ball、无效猫运行态、聊天窗拖拽等情况输出 `quiet`。
2. Tier Gate：只保留当前 tier 合法动作。
3. Provider Gate：adapter 根据实时 renderer/DOM/目标条件确认可执行性。
4. 统一评分：每个动作先计算“五维基础分－自身阈值”的需求余量，再加距上次真实 started 连续恢复的共享节奏分和该动作当前的短时意图贡献，形成基础资格分；需求和意图都使用有界曲线。
5. cooldown 不改动作分数或资格。通过资格的候选先按独立 cooldown 排序位从小到大排列，再按资格分从大到小排列；两者都相同时按固定顺序选择。cooldown 不能单独产生 `stay_idle`。

这套评分覆盖 CAT1/CAT2/CAT3 的全部动作。它没有动作配额、连续两次硬禁止、气泡硬冷却或“到 5 分钟强行选一个动作”；一两次重复和高互动时更快回应都必须从同一套分数自然产生。

当前主动动作池：

| tier | 动作 | 既有 runner 语义 | 主要 provider 条件 |
|---|---|---|---|
| CAT1 | `cat1_social_ping` | 轻声/环境回应与气泡 | 可见、音频可播放、无表现锁 |
| CAT1 | `cat1_eat_snack` | 吃零食 | eat runner 可用 |
| CAT1 | `cat1_small_move` | 最小化聊天球可用时成对移动；展开/compact 时只移动猫 | journey settled、对应 surface 可用、有空间 |
| CAT1 | `cat1_play_yarn` | 玩毛线球 | 已在聊天球附近、毛线球可隐藏/恢复 |
| CAT2 | `cat2_nap_feedback` | 小憩声音/气泡反馈 | 睡眠反馈 runner 可用 |
| CAT3 | `cat3_sleep_feedback` | 熟睡声音/气泡反馈 | 睡眠反馈 runner 可用 |

以下不是主动候选：drag、hover、return、tier demotion、walk-to-chat、compact top edge、mirror、chat idle-dock、edge peek，以及原 journey 到达聊天球后的局部尾动作。

Cat Mind 不主动 walk-to-chat。玩毛线要求已经 settled near-chat；小移动不借此接近聊天球，但会沿用既有两种 runner 形态：最小化聊天球 near-chat 时移动猫与球，聊天窗已展开或 compact surface 可用时只移动猫。没有对应 surface 或表现条件不满足时，provider 拒绝动作。

## 八、现有表现功能

### 8.1 基础 GIF、hover 与思考气泡

每个 tier 有默认、hover/click 和拖拽素材。hover 离开后等待当前 GIF 一轮播完再恢复，tier 或动作切换会清理旧 token 和 timer。

思考气泡由音频真实开始后临时显示。CAT1 使用普通内容气泡；CAT2 有 `1/3`、CAT3 有 `2/3` 概率改用睡眠 ZZZ。普通气泡显示 `5s`；ZZZ 跟随本次实际音频剩余时长，无法取得时使用 `8s` fallback。活动气泡有独立点击命中，点击播放 `540ms` pop 并产生 observation，同一次点击在 `800ms` 内去重，不触发 return、拖拽或吃东西。气泡在拖拽 pending/进行、tier 切换、walk、stretch、play、eat、hover 暂停等冲突阶段隐藏。

### 8.2 拖拽与边缘反馈

长按进入拖拽准备，真实移动后才进入拖拽。仅 pointer-down/pending 不取消正在运行的子动作或 journey；确认发生真实位移后，才以统一的 `return-ball-drag-active` 原因中断互斥 runner、journey 和音频，避免轻点或长按误伤正在发生的回应。结束后按真实结果恢复或取消。拖拽 CAT3 第一次仍保持 CAT3，第二次降为 CAT2；拖拽 CAT2 一次降为 CAT1；CAT1 不降级。CAT3 降到 CAT2 后把推进时钟重放到累计 `15m`，再过 `3m` 才回 CAT3；CAT2 降到 CAT1 后重放到累计 `10m`，再过 `5m` 才回 CAT2。拖拽不刷新用户 idle 基线。

CAT1 同一次按住拖拽中，约 `1100ms` 内至少 6 次有效方向反转、夹角至少约 `90°` 且整段平均速度约 `800px/s` 时，进入约 `5s` 的快速甩动反馈；它只改变本次拖拽表现和五维 observation。CAT1 松手靠近屏幕边缘时可以进入半隐藏/角落表现；该表现是 hard gate，直到再次拖出、切 tier 或 return cleanup 前不启动自主动作。

CAT1 拖拽结束后如果进入屏幕边缘探头/半隐藏状态，renderer 会报告 `edgePeekActive`，Cat Mind 保持 `quiet`，provider 也会二次拒绝 CAT1 动作。下一次拖拽开始、退出 CAT1 或 return cleanup 清除边缘状态后才恢复判断；CAT2/CAT3 不受 CAT1 边缘 class 误锁。

### 8.3 走向最小化聊天球

CAT1 与聊天球距离达到当前代码的进入阈值 `180px` 后，既有 journey 可以等待后走向聊天球；停止阈值当前为 `14px`，基础速度约 `82px/s`。目标移动时沿用既有追踪和加速规则。

到达后仍由 journey 做一次局部结果选择：`25%` 请求局部玩球，否则请求独立的伸懒腰 runner。journey 只负责选择和接收终态；伸懒腰自身的素材、计时、互斥、取消、恢复和结束回调由独立 runner 管理，不作为 journey 子状态。两种结果都不进入 selector，不写 Cat Mind cooldown，也不写严格 return episode。局部玩球真实完成时可以结算与玩球相同的五维完成反馈，并视为已经满足尚未消费的毛线邀请；取消则不领取完成反馈，也不消费邀请。由 journey 请求的伸懒腰只有真实完成时才产生既有表现反馈，取消不冒充完成。selector 的 `cat1_play_yarn` 是另一条受评分和 near-chat provider 约束的自主请求。

同一个独立伸懒腰 runner 也可由 CAT1 本地回复的 `5%` 哈气彩蛋请求。该入口不要求 near-chat；因此它的完成不冒充 journey 的 `cat1_stretch_done_near_chat`，本次互动数值已经由对应的 `cat_local_text_received` 结算。runner 拒绝时只回退普通猫叫；确认启动后，专用音效附着在本次伸懒腰状态上，随既有结束、中断和全局猫形态音频关闭链清理。哈气文字和表情包是本次猫形态对话的消息，保留到当前猫形态聊天周期结束，不随短动作提前移除。

这些距离同时受 GIF 透明边、目标矩形和视觉停止点影响。修改前必须重新以当前代码、静态测试和真实桌面画面共同确认，不能只改文档数值。

### 8.4 自主小移动、吃零食与玩球

小移动复用同一个既有 runner：最小化聊天球处于 settled near-chat 时让猫与聊天球一起短距离移动；聊天窗已展开或 compact surface 可用而没有最小化目标时，只让猫在可用空间内小幅移动。两种形态都不接管 walk-to-chat。小移动和既有 walk 在真实终态上报路径、持续时间与稳定 `activityId`，由 Cat Mind 一次性结算物理负荷。吃和玩球复用既有 GIF/音频 runner，动作互斥，并在结束、取消、拖拽、tier change 和 return 时恢复临时 DOM/窗口状态。

玩球开始时可临时隐藏网页或桌面最小化毛线球，结束后只撤销本动作的临时隐藏，不改变聊天窗原本 minimized/compact/bounds 状态。

### 8.5 compact 上缘、mirror 与 idle-dock

CAT1 可由既有表现链贴到 compact 聊天框上缘，并随聊天窗移动；跟随距离上限为 `200px`，与上缘视觉重叠 `28px`，左右安全 padding 为 `12px`，脱离后需回到 `100px` 内才重新武装。连续 3 次快速移动才触发掉落：速度超过 `1100px/s`，或单步超过 `210px` 且间隔不超过 `240ms`；掉落 `52px / 360ms`，cooldown `900ms`。跨 surface 时 mirror 负责呈现，原 return art 隐藏，避免双猫。

CAT2/CAT3 的 idle-dock 与桌面侧窗口落位保持原职责。Web 端若聊天框未最小化，先调用原最小化，再停到猫左侧；退出时恢复原位置，只在本次 dock 主动最小化时重新展开。Electron `/chat` 保存进入前 surface、bounds、折叠状态，完成折叠后落位；失败回滚，退出时也只展开由 dock 触发的折叠。拖拽降级要求保留当前落点时，不恢复旧 bounds。`/chat_full` 与 legacy-full 是独立隔离窗口，不参与 idle-dock。

compact、mirror 与 idle-dock 都不是 Cat Mind 主动动作，也不允许 Cat Mind 直接移动桌面窗口。

### 8.6 问号入口与 playground

CAT1 默认态监听 `↑ ↑ ↓ ↓ ← ← → → B A B A`；输入焦点在可编辑控件时忽略。完整输入后在猫上方显示问号 `10s`。点击问号进入独立 physics playground：猫、毛线球、问号方块等成为可碰撞/拖拽的表现对象，并暂时接管 return-ball 拖拽、journey、hover、edge、compact、dock 和 Cat Mind runner 的表现能力，避免多个系统同时写位置或素材。

playground 是独立表现模式，不是 selector 候选，不写动作 cooldown 或 return episode。点击猫退出并走原 return；点击问号方块退出并恢复进入前猫形态。退出、tier 变化、容器消失或 return 时必须释放临时对象和能力锁，恢复原位置、素材和现有链路。

### 8.7 Web 与 Electron

首页 React chat host 与 Electron `/chat` 可以使用不同桥接入口，但发布给猫形态的核心事实一致：模式、屏幕矩形、可见性和目标变化。桌面桥接失败时应退化为不联动，不得阻塞 return。

### 8.8 资源与双向过渡

| 用途 | 当前资源 |
|---|---|
| CAT1/2/3 默认 | `cat-idle-cat1.gif` / `cat-idle-cat2.gif` / `cat-idle-cat3.gif` |
| CAT1/2/3 hover | `cat-idle-cat1-click.gif` / `cat-idle-cat2-click.gif` / `cat-idle-cat3-click.gif` |
| 拖拽与快速甩动 | `cat-idle-cat-move-1.gif` 至 `cat-idle-cat-move-5.gif` |
| journey 走路/hover | `cat-idle-cat4-1.gif` / `cat-idle-cat4-3.gif` |
| CAT1 独立伸懒腰 | `cat-idle-cat4-2.gif` |
| 玩毛线/吃零食 | `cat-idle-cat-play-1.gif` / `cat-idle-cat1-eat.gif` |
| 气泡 | `cloud-thought-bubble.gif` / `cloud-thought-bubble-pop.gif` / `sleeping-zzz.gif` 与独立内容 PNG |
| 双向模型切换遮罩 | `cat_model_change.gif` |

模型到猫、猫到模型共用全局互斥过渡，不能被同向或反向重复点击重启。猫初始位置取模型屏幕矩形；模型恢复位置取点击时猫的矩形；goodbye 按钮只可在模型 bounds 不可用时兜底。遮罩只负责掩盖切换，既有 goodbye/return 事件仍立即推进业务恢复。Live2D、VRM、MMD、PNGTuber 使用同一真实猫 return、一次性摘要和回归问候语义。

## 九、动作生命周期

标准链路：

```text
request -> accepted -> started -> done | failed | cancelled | interrupted
```

- `accepted`：adapter 接受，并绑定唯一 runId；尚不写 cooldown。
- `started`：runner 已实际起效；此时写本动作 cooldown、重置共享节奏并消费对应动作意图。
- `done`：runner 在恢复完成后报告；此时结算五维完成反馈，并可写 return episode。
- `rejected`：provider 或 adapter 未启动；不写 cooldown、结果或经历。
- `cancelled / failed / interrupted`：不得当成完成经历；small move 或既有 walk 取消时只结算已经发生且可去重的物理路程。中断原因是生命周期元数据，本身不再追加一份五维反馈；拖拽、tier 或 return 的源 observation 已各自拥有唯一的状态反馈，避免同一事实双计。

一个 active action 未终结时，selector 不再启动另一个动作。动作进行期间的高频用户触发会合并保留；动作结果只释放调度并排入一次下一轮判断，不在结束回调里同步连播。严格结果必须匹配 `actionId + requestId + runId`；终态早于 started 只作为协议失败释放 request，不能结算五维、cooldown 或 episode。

与 Cat Mind 动作对应的短时意图只在 `started` 时消费；journey 局部玩球则只在真实 `done` 时消费同一毛线意图。provider reject、`accepted` 或启动前失败都保留它，让暂时不满足 near-chat、音频播放或表现锁的真实意图可以在后续合法判断中继续参与评分；它仍会按时间自然衰减，不能永久挂起。

## 十、结构化经历与回归对话

### 10.1 经历归并

Cat Mind 不扫描 recent events 写故事，而是维护一个有界 accumulator：

- CAT1 四个自主动作只有严格 `done` 才进入当前活动段；
- 同一活动段重复同类动作只保留一次类型；混合动作使用泛化活动，不选假主线；
- CAT2/CAT3 严格睡眠反馈结束当前活动段，形成“活动后休息”或单独“休息”；
- 休息后的新活动覆盖旧休息，休息后的用户互动会阻止旧休息被误用于本次 return；
- window、presentation、tier、legacy journey play、失败/取消/打断都不写经历。

最终只可能输出：`activity`、`rest_after_activity`、`rested`，以及可选的单一 highlight。

### 10.2 return 附件

摘要包含停留时长、进入方式、最终 tier；有可信完成经历时再带 episode。后端会用本次 return 的顶层时长、tier 和自动/手动事实覆盖摘要同名字段。

少于 3 分钟时统一静默，无论动作是否 started 或 done。动作结果只在达到门槛后通过严格 done episode 补充真实经历，不能缩短或绕过等待。

### 10.3 独立路由

猫形态问候使用专用触发和 prompt 路由，`time_hint` 留空，不调用普通时段/餐食提示。它不替代模型恢复，不绕过 voice/takeover/busy/session guard，也不与午饭等 greeting 拼接。

## 十一、调试入口

debug 默认不显示，仅影响观察：

- 临时打开：URL 加 `?cat_mind_debug=1`；
- 临时关闭：URL 加 `?cat_mind_debug=0`；
- 持久调试：localStorage `neko.catMind.debug=true`；
- 面板内“隐藏”只隐藏本次页面的面板和刷新，不停止 Cat Mind。

URL 参数优先于全局变量和 localStorage，方便单次排查后用 `0` 明确关闭。关闭 debug 时不派发完整 snapshot/state-change 调试数据，动作运行语义不变。

## 十二、验证与完成标准

### 12.1 自动验证

1. 修改 Cat Mind 或 avatar split 后，所有相关脚本通过 `node --check`。
2. Cat Mind 静态 Node harness 覆盖 observation、去重、异步 scheduler、provider、生命周期、cooldown、五维反馈、短时意图、物理活动一次性结算、动作分数与 return episode；avatar harness 另覆盖毛线阶段兼容、只读几何、重复 terminal、active/settling gate、后台 RAF fallback，以及各拖拽/走路/取消终态的稳定物理事实。
3. 短时矩阵必须按生产事实覆盖从不交互、一次 hover、普通拖拽、单次 rapid drag 和短时多轮交互；fake clock 与事件时间一致，每轮拖拽分别经过 drag-pending、drag-active、return-pending 和 settled 步骤，并覆盖 settled near-chat / far-chat、真实各 runner 时长、拖拽对 active runner 的取消以及 started 前终态。它不替代真实桌面几何验收，也不把所有 runner 伪装成统一时长。
4. 交互梯度按可验证区间验收：从不/少量交互持续平均约 `3–5` 分钟一次，正常交互在短时和一段时间内比无/少更早且更多；短时间大量交互在 gate/provider 可用时应在数秒到数十秒内出现首个真实 started，并在前几分钟拉开数量和动作种类。高频输入停止后逐步回到普通节奏，不能留下永久高频。最小化聊天球 near-chat、展开/compact 的 solo small move、以及确实没有移动/玩球能力的场景必须分开验证；测试只验证相对包络，不锁死具体动作顺序、精确分钟序列或单一真人节奏。
5. return 测试覆盖大量交互并完成动作、正常互动并完成动作、无用户交互但有自主完成动作、只有互动没有完成动作、短时 started/done 仍静默、失败/取消/打断、一次性消费及无 socket/send failure。
6. 后端测试覆盖 enum allowlist、canonical 覆盖、非法组合、独立 cat greeting 路由和 prompt 格式化。

### 12.2 运行时验证

至少观察：

1. 默认页面不显示 debug；query 打开后面板可见且可隐藏。
2. 真实 runner 的 accepted、started、done 顺序；音频 autoplay 拒绝时不能出现 started/cooldown。
3. Web 和 Electron 各进入猫形态一次，确认页面内 Cat Mind 都产生五维状态，并在 provider 合法时走同一条 action request；Electron 桌面壳只产生 observation/窗口桥接，不存在第二套 selector 或 request producer。两端各验证一次有完成 episode 的回归、无 episode 的正常回归，以及不受 started/done 影响的短时统一静默。
4. 在桌面真实拖拽、聊天窗移动、compact/dock、毛线球隐藏恢复下确认窗口和命中安全；分别验证 far→near 强邀请、猫旁重复递球、拖离清除，以及 active/settling 期间不抢跑。

浏览器页面测试可以确认 debug、事件和普通 Web 链，但不能代替 Electron 原生窗口、透明覆盖层和 Live2D canvas 拖拽验收。

## 十三、实施位置

| 能力 | 当前实现位置 |
|---|---|
| Cat Mind、评分、episode | `static/app/app-cat-mind.js` |
| 猫形态本地文字接收、request 去重、哈气彩蛋选择与 observation 提交 | `static/app/app-react-chat-window/cat-local-chat.js` |
| 普通猫叫与哈气彩蛋词元 | `static/app/app-react-chat-window/cat-local-chat-lexicon.js` |
| debug 面板 | `static/app/app-cat-mind-debug.js` |
| avatar 只读毛线 observation adapter | `static/avatar/avatar-ui-buttons/idle-cat-mind-observations.js` |
| renderer adapter 与 CAT1/CAT2/CAT3 runner | `static/avatar/avatar-ui-buttons/` 的拆分脚本 |
| 自动 goodbye 与 return 附件消费 | `static/app/app-auto-goodbye.js` |
| WebSocket 发送 | `static/app/app-websocket.js` |
| 后端规范化 | `main_routers/websocket_router.py` |
| 独立猫问候调用 | `main_logic/core/greeting.py` |
| return prompt 与 scene | `config/prompts/prompts_proactive.py` |

avatar 实现已经拆为 `core.js`、`idle-assets-and-question.js`、`idle-playground.js`、`idle-actions-and-audio.js`、`idle-drag-and-subactions.js`、`idle-journey-and-presentation.js`、`idle-cat-mind-observations.js`、`methods-setup.js`、`methods-buttons.js`、`methods-return.js`、`methods-state-and-cleanup.js`；后续修改继续落在对应职责文件，不恢复旧大文件，也不恢复旧数字前缀文件名。
