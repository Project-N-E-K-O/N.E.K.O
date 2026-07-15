# 猫娘空闲状态与 Cat Mind 功能说明

## 一、文档定位

本文是猫形态功能的当前总说明，统一描述玩家看到的功能、现有表现链、Cat Mind 状态机、桌面边界和 return 经历链路。它不是历史方案，也不记录逐次实施过程。

精确的五维初始值、自然变化、事件反馈、动作公式、阈值、冷却、测试包络和返回提示词统一收在另一份数值规则文档中，避免产品说明与调参细节互相覆盖。

## 二、产品目标

用户“请她离开”后，角色模型隐藏，原有回来入口变成可停留、可 hover、可拖拽、可点击回来的猫形象。长时间无有效交互时，系统复用既有 goodbye 链自动进入猫形态，并从清醒逐步过渡到打盹、睡觉。

猫形态不只是定时播放素材。Cat Mind 根据时间、用户互动、聊天窗状态、现有表现结果和自主动作结果维护一组内部需求，再在安全条件内选择少量既有动作。用户叫她回来时，系统最多把最近一段真实、已完成的猫形态经历压缩成结构化摘要，交给模型自然带入本次回归对话。

体验目标：

1. 不交互时仍有缓慢、克制的生命感。
2. 少量、正常和大量交互会留下不同的五维变化，但不会把一次交互直接翻译成固定动作。
3. 短时间高频 hover/拖拽会消耗精力、增加困意，也会提高她当下想回应、想活动的驱动力，因此这段时间自主动作比无/少交互更密；点掉气泡则满足当前回应，不能继续给下一次气泡加分。
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
- hard gate、tier gate、provider 结果与动作计分；
- pending request、active action、统一节奏分和动作自身软 cooldown；
- 严格动作结果驱动的 return episode 归并；
- return 时生成一次性结构化摘要。

Cat Mind 不写 DOM、不直接播放素材、不直接操作窗口，也不保存到长期记忆。

### 4.3 renderer adapter 与既有 runner

adapter 读取当前 renderer 事实，判断动作是否真的可执行，并把 action request 翻译成既有 runner 调用。runner 仍拥有 GIF、音频、DOM class、目标跟随、取消、恢复和结束时机。

只有 runner 真实进入 `started` 后，Cat Mind 才写 cooldown 并重置共享节奏分。provider 拒绝或启动前失败不得写 cooldown、不得伪造 `done / failed / result`。音频动作只有 `audio.play()` 成功后才能报告 started，不能在仅创建 `Audio` 时提前开始冷却。所有已接入动作统一使用连续恢复的软 cooldown；气泡动作没有额外硬间隔，是否重复仍由需求、节奏和高倍率冷却后的总分决定。

### 4.4 NEKO-PC

桌面端只提供：

- 聊天窗最小化、compact、展开、移动、idle-dock 等 observation；
- 跨窗口坐标和可见性同步；
- 毛线球临时隐藏与恢复；
- 桌面窗口命中、遮挡和层级安全。

NEKO-PC 不保存 Cat Mind，不选动作，不发 action request，不生成或发送 return episode。

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

用户互动包括 hover 反应、拖拽开始/结束/取消、rapid drag 和点击活动思考气泡。它们只更新现有五维、recent events 和章节边界，再排入下一轮判断。hover/拖拽会小幅提高社交/刺激驱动力，使短时密集互动能通过统一评分更快得到多个自主回应，同时真实消耗精力；点掉气泡表示一次回应已经完成，会降低社交和刺激需求。

它们不直接映射或启动吃、回应、玩球或移动，也没有“进入高互动模式”的特判。正需求余量统一封顶，所有动作再经过共享节奏和自身高倍率软 cooldown 竞争；因此高互动可以更密，但不能随 observation 数量线性连发，也不能绕过 hard gate 和 provider。

### 6.3 聊天窗和桌面

聊天窗最小化可见、移动较远、compact 表面、idle-dock、重新展开和桌面层级变化都只是环境 observation。它们为 provider、near/far 和安全判断提供事实，不直接给五维加分。Native IPC、BroadcastChannel 和本地 UI 可能重复送达同一状态；Cat Mind 按最小化状态和矩形去重，同一状态、同一 rect 的心跳不重复记录或制造判断机会。

### 6.4 既有表现结果

走到聊天球旁、伸懒腰结束、compact 上缘落位/掉落、拖拽后边缘探头等是已有表现的结果。它们可以反馈五维，但不是 selector 主动候选。

### 6.5 自主动作结果

只有与当前 `actionId + requestId + runId` 匹配的 adapter/runner 结果才能结束 active action。`done` 回写动作完成反馈；`cancelled / failed / interrupted` 按各自语义处理。公开 observation 不能冒充严格动作结果写入 return episode。

## 七、选择器与动作池

每轮按以下顺序判断：

1. Hard Gate：return、拖拽准备/进行、CAT1 贴边半隐藏、tier transition、其他独立动作、不可见 return-ball、无效猫运行态、聊天窗拖拽等情况输出 `quiet`。
2. Tier Gate：只保留当前 tier 合法动作。
3. Provider Gate：adapter 根据实时 renderer/DOM/目标条件确认可执行性。
4. 统一评分：每个动作先计算“五维基础分－自身阈值”的需求余量，正余量统一封顶，再加距上次真实 started 连续恢复的共享节奏分，最后减本动作连续恢复的软 cooldown。
5. 只保留最终效用不小于 0 的动作，按效用而不是 raw 基础分排序；同分按固定顺序选择，无候选则 `stay_idle`。

这套评分覆盖 CAT1/CAT2/CAT3 的全部动作。它没有动作配额、连续两次硬禁止、气泡硬冷却或“到 5 分钟强行选一个动作”；一两次重复和高互动时更快回应都必须从同一套分数自然产生。

当前主动动作池：

| tier | 动作 | 既有 runner 语义 | 主要 provider 条件 |
|---|---|---|---|
| CAT1 | `cat1_social_ping` | 轻声/环境回应与气泡 | 可见、音频可播放、无表现锁 |
| CAT1 | `cat1_eat_snack` | 吃零食 | eat runner 可用 |
| CAT1 | `cat1_small_move` | 猫与聊天球的小幅成对移动 | 已在聊天球附近、settled、有空间 |
| CAT1 | `cat1_play_yarn` | 玩毛线球 | 已在聊天球附近、毛线球可隐藏/恢复 |
| CAT2 | `cat2_nap_feedback` | 小憩声音/气泡反馈 | 睡眠反馈 runner 可用 |
| CAT3 | `cat3_sleep_feedback` | 熟睡声音/气泡反馈 | 睡眠反馈 runner 可用 |

以下不是主动候选：drag、hover、return、tier demotion、walk-to-chat、compact top edge、mirror、chat idle-dock、edge peek，以及原 journey 到达聊天球后的局部尾动作。

Cat Mind 不主动 walk-to-chat。小移动和玩毛线都要求已经 near-chat；距离不满足时只能等待既有表现链把猫带近。

## 八、现有表现功能

### 8.1 基础 GIF、hover 与思考气泡

每个 tier 有默认、hover/click 和拖拽素材。hover 离开后等待当前 GIF 一轮播完再恢复，tier 或动作切换会清理旧 token 和 timer。

思考气泡由音频真实开始后临时显示。CAT1 使用普通内容气泡；CAT2 有 `1/3`、CAT3 有 `2/3` 概率改用睡眠 ZZZ。普通气泡显示 `5s`；ZZZ 跟随本次实际音频剩余时长，无法取得时使用 `8s` fallback。活动气泡有独立点击命中，点击播放 `540ms` pop 并产生 observation，同一次点击在 `800ms` 内去重，不触发 return、拖拽或吃东西。气泡在拖拽 pending/进行、tier 切换、walk、stretch、play、eat、hover 暂停等冲突阶段隐藏。

### 8.2 拖拽与边缘反馈

长按进入拖拽准备，真实移动后进入拖拽。拖拽期间暂停当前子动作和气泡；结束后按真实结果恢复或取消。拖拽 CAT3 第一次仍保持 CAT3，第二次降为 CAT2；拖拽 CAT2 一次降为 CAT1；CAT1 不降级。CAT3 降到 CAT2 后把推进时钟重放到累计 `15m`，再过 `3m` 才回 CAT3；CAT2 降到 CAT1 后重放到累计 `10m`，再过 `5m` 才回 CAT2。拖拽不刷新用户 idle 基线。

CAT1 同一次按住拖拽中，约 `1100ms` 内至少 6 次有效方向反转、夹角至少约 `90°` 且整段平均速度约 `800px/s` 时，进入约 `5s` 的快速甩动反馈；它只改变本次拖拽表现和五维 observation。CAT1 松手靠近屏幕边缘时可以进入半隐藏/角落表现；该表现是 hard gate，直到再次拖出、切 tier 或 return cleanup 前不启动自主动作。

CAT1 拖拽结束后如果进入屏幕边缘探头/半隐藏状态，renderer 会报告 `edgePeekActive`，Cat Mind 保持 `quiet`，provider 也会二次拒绝 CAT1 动作。下一次拖拽开始、退出 CAT1 或 return cleanup 清除边缘状态后才恢复判断；CAT2/CAT3 不受 CAT1 边缘 class 误锁。

### 8.3 走向最小化聊天球

CAT1 与聊天球距离达到当前代码的进入阈值 `180px` 后，既有 journey 可以等待后走向聊天球；停止阈值当前为 `14px`，基础速度约 `82px/s`。目标移动时沿用既有追踪和加速规则。

到达后的 `25%` 玩球、否则伸懒腰，是 journey 自己的局部尾动作，不进入 selector，不写 Cat Mind cooldown，也不写严格 return episode。selector 的 `cat1_play_yarn` 是另一条受评分和 near-chat provider 约束的自主请求。

这些距离同时受 GIF 透明边、目标矩形和视觉停止点影响。修改前必须重新以当前代码、静态测试和真实桌面画面共同确认，不能只改文档数值。

### 8.4 自主小移动、吃零食与玩球

小移动只在 settled near-chat 状态下让猫与聊天球一起做短距离移动；它不接管 walk-to-chat。吃和玩球复用既有 GIF/音频 runner，动作互斥，并在结束、取消、拖拽、tier change 和 return 时恢复临时 DOM/窗口状态。

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
| journey 走路/伸展/hover | `cat-idle-cat4-1.gif` / `cat-idle-cat4-2.gif` / `cat-idle-cat4-3.gif` |
| 玩毛线/吃零食 | `cat-idle-cat-play-1.gif` / `cat-idle-cat1-eat.gif` |
| 气泡 | `cloud-thought-bubble.gif` / `cloud-thought-bubble-pop.gif` / `sleeping-zzz.gif` 与独立内容 PNG |
| 双向模型切换遮罩 | `cat_model_change.gif` |

模型到猫、猫到模型共用全局互斥过渡，不能被同向或反向重复点击重启。猫初始位置取模型屏幕矩形；模型恢复位置取点击时猫的矩形；goodbye 按钮只可在模型 bounds 不可用时兜底。遮罩只负责掩盖切换，既有 goodbye/return 事件仍立即推进业务恢复。Live2D、VRM、MMD 使用同一过渡语义；PNGTuber 目前只接收 observation/reset，没有现行猫回归摘要消费者，不能让草稿残留到下一种 avatar。

## 九、动作生命周期

标准链路：

```text
request -> accepted -> started -> done | failed | cancelled | interrupted
```

- `accepted`：adapter 接受，并绑定唯一 runId；尚不写 cooldown。
- `started`：runner 已实际起效；此时写本动作 cooldown，并允许短时 return 投递。
- `done`：runner 在恢复完成后报告；此时结算五维完成反馈，并可写 return episode。
- `rejected`：provider 或 adapter 未启动；不写 cooldown、结果或经历。
- `cancelled / failed / interrupted`：不得当成完成经历；拖拽中断有独立五维反馈，return/tier 中断不补完成收益。

一个 active action 未终结时，selector 不再启动另一个动作。动作结果只触发下一轮判断，不在结束回调里同步连播。

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

摘要包含停留时长、进入方式、最终 tier；runner 真正 started 时可带一个只用于短时投递的布尔位；有可信完成经历时再带 episode。后端会用本次 return 的顶层时长、tier 和自动/手动事实覆盖摘要同名字段。

少于 3 分钟且没有真实 started runner 时保持静默。真实 started 只说明曾启动动作，不说明完成；若没有严格 done episode，短时问候只能中性表达“已经回来”，不能猜动作结果。

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
2. Cat Mind 静态 Node harness 覆盖 observation、去重、异步 scheduler、provider、生命周期、cooldown、五维反馈、动作分数与 return episode。
3. 平衡测试必须分别覆盖从不交互、少量、正常和前 12 分钟短时大量交互，并区分 near-chat/far-chat：无/少交互验证任意完整 15 分钟有 3–5 次 started 和动作轮换；短时大量交互验证前 12 分钟比其他 profile 产生更多真实 started、首次输入后 90 秒内回应且覆盖 4 类 CAT1 动作。它是可复现压力包络，不宣称某一固定节奏代表所有真人使用。
4. return 测试覆盖大量交互并完成动作、正常互动并完成动作、无用户交互但有自主完成动作、只有互动没有完成动作、短时 started、失败/取消/打断、一次性消费及无 socket/send failure。
5. 后端测试覆盖 enum allowlist、canonical 覆盖、非法组合、独立 cat greeting 路由和 prompt 格式化。

### 12.2 运行时验证

至少观察：

1. 默认页面不显示 debug；query 打开后面板可见且可隐藏。
2. 真实 runner 的 accepted、started、done 顺序；音频 autoplay 拒绝时不能出现 started/cooldown。
3. Web 和 Electron 各验证一次有完成 episode 的回归、无 episode 的正常回归、无 started 的短时静默。
4. 在桌面真实拖拽、聊天窗移动、compact/dock、毛线球隐藏恢复下确认窗口和命中安全。

浏览器页面测试可以确认 debug、事件和普通 Web 链，但不能代替 Electron 原生窗口、透明覆盖层和 Live2D canvas 拖拽验收。

## 十三、实施位置

| 能力 | 当前实现位置 |
|---|---|
| Cat Mind、评分、episode | `static/app/app-cat-mind.js` |
| debug 面板 | `static/app/app-cat-mind-debug.js` |
| renderer adapter 与 CAT1/CAT2/CAT3 runner | `static/avatar/avatar-ui-buttons/` 的拆分脚本 |
| 自动 goodbye 与 return 附件消费 | `static/app/app-auto-goodbye.js` |
| WebSocket 发送 | `static/app/app-websocket.js` |
| 后端规范化 | `main_routers/websocket_router.py` |
| 独立猫问候调用 | `main_logic/core/greeting.py` |
| return prompt 与 scene | `config/prompts/prompts_proactive.py` |

avatar 实现已经拆为 `core.js`、`idle-assets-and-question.js`、`idle-playground.js`、`idle-actions-and-audio.js`、`idle-drag-and-subactions.js`、`idle-journey-and-presentation.js`、`methods-setup.js`、`methods-buttons.js`、`methods-return.js`、`methods-state-and-cleanup.js`；后续修改继续落在对应职责文件，不恢复旧大文件，也不恢复旧数字前缀文件名。
