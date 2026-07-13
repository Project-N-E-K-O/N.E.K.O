# 猫咪 idle 状态机第一版实施文档

> 本文是 `cat-idle-state-machine-design.md` 的第一版实施拆解。它描述如何分阶段落地，不代表当前已经实现。实施时以当前代码和可复现运行结果为准；`cat-idle-state-machine-action-scoring.md` 是当前网页端已采用的数值基线，具体参数仍以代码和运行验证为准。

## 1. 第一版目标

第一版只做一件事：让猫形态拥有可观察、可调试、可逐步接管动作的 Cat Mind。

完成后应具备：

1. 猫形态进入后创建 Cat Mind 运行态。
2. 用户交互、窗口变化、tier 变化、动作完成或打断都能作为 observation 进入 Cat Mind。
3. 五维状态 `appetite / sleepiness / energy / social_need / stimulation_need` 有调试输出。
4. provider 能把当前耦合动作拆成可请求、可失败、可回报的能力。
5. selector 能在 hard gate / tier gate / provider gate 之后选择猫自己的自主表现。
6. return 前能生成结构化经历摘要。
7. NEKO-PC 只提供桌面 observation 和窗口安全能力，不复制 Cat Mind。

第一版不做：

1. 不接管 goodbye / return 主链路。
2. 不接管用户拖拽、hover、点击回来、tier 降级。
3. 不主动启动 walk-to-chat、compact top edge / mirror、chat idle-dock。
4. 不提前写死复杂公式、阈值和每分钟变化量。
5. 不把 NEKO-PC 做成第二套状态机。

## 2. 涉及仓库和文件边界

### 2.1 NEKO 主仓

主要职责：

1. Cat Mind 运行态。
2. observation normalizer。
3. action provider / runner 包装。
4. selector 和 scheduler。
5. return 记忆摘要。
6. cat greeting 扩展。

主要关注文件：

| 文件 | 作用 |
|---|---|
| `static/app/app-auto-goodbye.js` | 猫形态进入、tier 变化、拖拽降级、return 前事件 |
| `static/avatar/avatar-ui-buttons.js` | return ball 表现、气泡、吃、玩、睡眠声音、CAT1 journey |
| `static/app/app-react-chat-window.js` | chat minimized / compact / idle-dock observation |
| `static/app/app-interpage.js` | 跨窗口 observation 广播 |
| `static/app/app-websocket.js` | `cat_greeting_check` 前端发送 |
| `main_routers/websocket_router.py` | 后端接收 cat greeting payload |
| `main_logic/core/greeting.py` | 触发猫形态专属问候 |
| `config/prompts/prompts_proactive.py` | 问候 prompt 入口 |
| `main_routers/pages_router.py` | 静态资源版本/脚本加载相关检查 |

已新增文件：

| 文件 | 作用 |
|---|---|
| `static/app/app-cat-mind.js` | Cat Mind、observation normalizer、selector、debug API |
| `tests/unit/test_cat_idle_state_machine_static.py` | 静态契约测试 |

Cat Mind 保持为独立模块，位于 `static/app/`；`static/avatar/avatar-ui-buttons.js` 只提供 provider、adapter 与既有 runner，不能反向承载 Cat Mind 状态或 selector。

### 2.2 NEKO-PC

主要职责：

1. 保持桌面窗口链路稳定。
2. 将桌面端特有事实转成可消费 observation。
3. 保证 Pet / Chat / return ball / compact ball 的拖拽、层级、idle-dock 不被状态机污染。

主要关注文件：

| 文件 | 作用 |
|---|---|
| `src/preload/bridges/pet-input-region-bridge.js` | Pet 侧接收 chat minimized、return ball drag、CAT1 layer request |
| `src/preload/bridges/chat-compact-window-surface-bridge.js` | Electron chat idle-dock、compact surface、chat bounds |
| `src/main.js` | chat minimized state 转发、CAT1 companion layer、窗口层级 |
| `src/main/window-control-ipc.js` | return ball / chat window 原生拖拽 |
| `src/window-manager.js` | 独立毛线球窗口 |
| `test/react-chat-idle-dock-contract.test.js` | Electron idle-dock 现有契约测试 |

原则：

1. NEKO-PC 不保存 Cat Mind。
2. NEKO-PC 不决定猫要做什么动作。
3. NEKO-PC 只补充桌面 observation 的可靠性和窗口安全测试。

## 3. 阶段 0：实施前契约整理

目标：先把事件名、payload 和 debug 开关定下来，避免边写边猜。

### 3.1 NEKO

1. 建立事件命名约定：
   - `neko:cat-mind:observation`
   - `neko:cat-mind:state-change`
   - `neko:cat-mind:action-request`
   - `neko:cat-mind:action-result`
   - `neko:cat-mind:return-summary`

2. 定义 observation payload 基本结构：

```json
{
  "type": "drag_end",
  "source": "return-ball",
  "tier": "cat1",
  "timestamp": 0,
  "detail": {}
}
```

3. 只保留一个显示开关：
   - `neko.catMind.debug`

4. 明确第一版最终调度策略：
   - Cat Mind 的自主动作只由 selector 提议、adapter 执行；旧随机、timer、概率和点击派生调度不保留回退分支。
   - Cat Mind、selector 与动作池不提供运行时关闭或单动作灰度开关；它们始终按 hard gate、tier、provider 和评分运行。
   - `neko.catMind.debug` 只控制调试面板显示，不改变 observation、动作候选或既有用户直接交互。

### 3.2 NEKO-PC

1. 确认现有桌面事件是否都带足够字段：
   - `source`
   - `reason`
   - `timestamp`
   - `screenRect`
   - `minimized`
   - `visible`
   - `tier`

2. 如果字段不足，只补字段和测试，不改变窗口行为。

3. 明确 NEKO-PC 到 NEKO 的事实流：
   - chat minimized / moved
   - compact surface visible / moved
   - idle-dock entered / exited
   - return ball drag active / ended
   - CAT1 companion layer active / released

### 3.3 验证

1. 静态检查新增事件名只出现在状态机相关模块和桥接点。
2. 手动检查 debug 开关只影响调试面板，不改变现有猫形态动作。
3. NEKO-PC 现有 idle-dock 和 return ball drag 测试保持通过。

## 4. 阶段 1：只建立感知和 Cat Mind，不改动作触发

目标：猫开始“知道发生了什么”，但还不决定动作。

### 4.1 NEKO：Cat Mind 运行态

在 `static/app/app-cat-mind.js` 新增或整理 Cat Mind 能力：

1. 在 `live2d-goodbye-click` 后初始化 Cat Mind。
2. 记录入口来源：
   - 手动 goodbye -> `entry: manual`
   - 自动 idle goodbye -> `entry: auto`
3. 记录当前 tier：
   - `cat1`
   - `cat2`
   - `cat3`
4. 维护五维字段：
   - `appetite`
   - `sleepiness`
   - `energy`
   - `social_need`
   - `stimulation_need`
5. 维护 recent events：
   - 最近用户交互
   - 最近窗口变化
   - 最近自主动作结果
   - 最近打断原因
6. 暴露 debug API：
   - `window.nekoCatMind.getState()`
   - `window.nekoCatMind.getRecentEvents()`
   - `window.nekoCatMind.observe(payload)`
   - `window.nekoCatMind.reset(reason)`

此阶段不调度动作。

### 4.2 NEKO：接入 observation

从现有事件映射 observation：

| 来源 | Observation |
|---|---|
| `live2d-goodbye-click` | `cat_entered` |
| `neko:auto-goodbye:state-change` | `tier_changed` |
| `neko:return-ball-manual-move` drag-start / active / motion / end / cancel | `drag_start` / `drag_end` / `drag_cancelled` |
| 快速甩动判定 | `rapid_drag` |
| hover / click 态 GIF 被触发 | `cat_hover_reaction` |
| 气泡点击 pop | `thought_bubble_pop` |
| return click | `return_click` |
| CAT1 walk finish | `cat1_walk_done_near_chat` |
| CAT1 stretch finish | `cat1_stretch_done_near_chat` |
| compact top edge settled/drop | `cat1_compact_top_edge_done` / `cat1_compact_top_edge_drop` |
| chat minimized / compact / expanded | `chat_*` |
| sleep/social/eat/play runner 结果 | 暂无主动接管时先只记录现有结果 |

注意：

1. `thought_bubble_pop` 此阶段仍可保留当前吃东西后续，不立刻改行为。
2. PNGTuber return 需要单独核对。当前 `app-ui.js` 处理 `pngtuber-return-click`，但 `app-auto-goodbye.js` 的 cat greeting return 监听不包含 PNGTuber；第一版如果要覆盖 PNGTuber，应先补契约和测试。
3. observation normalizer 要去重，避免 BroadcastChannel 和 local event 重复写入。

### 4.3 NEKO-PC：桌面 observation 稳定化

此阶段 NEKO-PC 只做桥接验证，非必要不改代码。

需要确认：

1. Chat minimized state 能到 Pet：
   - `src/main.js`
   - `src/preload/bridges/pet-input-region-bridge.js`
   - `static/app/app-react-chat-window.js`
2. Electron idle-dock 进入/退出能被 NEKO 看到：
   - `neko:idle-return-ball-state`
   - `neko:idle-chat-minimized-state`
3. return ball 原生拖拽不会被 Cat Mind 误判为自主动作。
4. Wayland / niri 的 crop hold、input region 只作为拖拽事实，不进入动作候选。

如需改动，只补事件 payload：

```json
{
  "source": "neko-pc",
  "reason": "idle-dock-enter",
  "timestamp": 0,
  "screenRect": {}
}
```

### 4.4 阶段 1 验证

NEKO：

1. 新增静态测试：Cat Mind 默认只观察，不改变已有触发。
2. 检查 `avatar-ui-buttons.js` 中气泡点击仍按旧行为工作。
3. 检查 `app-auto-goodbye.js` tier 变化能进入 Cat Mind。
4. 用浏览器/Playwright 验证：
   - 进入猫形态后 `window.nekoCatMind.getState()` 存在。
   - 拖拽 return ball 后 recent events 有 drag。
   - 点击气泡后 recent events 有 thought bubble pop。
   - return 前能生成 summary 草稿。

NEKO-PC：

1. 运行现有契约测试：
   - `node --test test/react-chat-idle-dock-contract.test.js`
   - 与 return ball drag / compact surface 相关的现有 node tests。
2. 手动或自动验证 Electron chat minimized / idle-dock 事件仍到达 Pet。

### 4.5 阶段 1 收口状态

当前阶段 1 已收口为“猫形态 observation runtime”。它只让猫开始知道发生了什么，不调度动作，不封口旧入口，不把 Cat Mind 扩散到 NEKO-PC。

已完成：

1. `static/app/app-cat-mind.js` 已作为独立 Cat Mind 入口加载到 `index.html` / `chat.html`。
2. `window.nekoCatMind.getState()`、`getRecentEvents()`、`getReturnSummaryDraft()`、`observe()`、`reset()` 已可用于调试。
3. `live2d-goodbye-click` 后初始化 Cat Mind，记录 `entry: manual/auto` 和初始 CAT1。
4. `neko:auto-goodbye:state-change` 的 visual tier 能进入 `tier_changed`，拖拽降级能进入 `tier_demoted_by_drag`。
5. return ball drag、rapid drag、hover / click 态 GIF、thought bubble pop、return click、CAT1 walk/stretch、compact top edge done/drop、edge peek 都能作为 observation 进入 recent events。
6. chat minimized / moved far / compact surface / expanded / idle-dock、return ball desktop state 已作为桌面 observation 接入。
7. observation normalizer 已做基础白名单、detail sanitize、重复 enter 保护、heartbeat 过滤、重复 drag-start 过滤和 bounded recent events。
8. `thought_bubble_pop` 保留旧的点击后吃东西行为，阶段 1 不改变用户可见动作。
9. NEKO-PC 没有保存 Cat Mind、没有 selector/action request 字段，也不决定猫做什么动作。

阶段 1 明确不包含：

1. 不实现 provider / runner。
2. 不生成真实动作结果，例如 `eat_done`、`play_done`、`sleep_feedback_done` 的生产者。
3. 不启用 selector、scheduler、hard gate 决策层。
4. 不统一封口旧随机 / timer / 概率 / 点击派生入口。
5. 不把 return summary 发送到 `cat_greeting_check` 或后端问候链路；这属于阶段 4。
6. 不让猫娘形态继续持有 Cat Mind。Cat Mind 只在猫形态期间运行，return 后最多保留一份短 summary draft。

当前验证：

1. `node --check static/app/app-cat-mind.js`
2. `node --check static/avatar/avatar-ui-buttons.js`
3. `tests/unit/test_cat_idle_state_machine_static.py`
4. `tests/unit/test_avatar_return_button_cat1_static.py`
5. `tests/unit/test_avatar_return_button_idle_tiers_static.py`
6. `tests/unit/test_react_chat_idle_dock_static.py`
7. NEKO-PC idle-dock / compact surface drag / return ball / Wayland input region 契约测试

仍建议补的非阻塞验证：

1. 浏览器或 Playwright 运行时观察：进入猫形态后 `window.nekoCatMind.getState()` 存在。
2. 运行时拖拽 return ball 后 recent events 有 drag。
3. 运行时点击 thought bubble 后 recent events 有 `thought_bubble_pop`，且旧吃东西行为不变。
4. PNGTuber return 的完整 UI 链路仍需单独运行验证；当前只保证 Cat Mind observation-only 覆盖。

## 5. 阶段 2：拆 provider，但不启用 selector 接管

目标：把当前耦合动作包装成可请求、可失败、可回报的能力。本节保留阶段 2 的历史实施顺序；阶段 3 收口后，旧触发不再保留为运行时回退。

### 5.0 阶段 2 开工边界

阶段 2 只做“动作能力拆分”，不让 Cat Mind 主动选择动作。

阶段 2 开工前提：

1. 阶段 1 observation runtime 默认启用且测试通过。
2. 阶段 2 尚未接入 selector。
3. 旧随机 / timer / 概率 / 点击派生入口仍保留原调度权。
4. 新 provider / runner 只能被显式调用或由旧入口调用，不能由 selector 自动触发。
5. 每个 runner 的结果必须回写 Cat Mind observation，但不能让 provider 拒绝也写成动作完成。

阶段 2 第一批实施顺序：

1. 先建立通用 provider / runner 结果契约：
   - `dryRun(context) -> { allowed, reason }`
   - `run(context, token) -> started / done / failed / cancelled / interrupted`
   - `restore` 完成后再写最终 action result。
2. 再包装 `cat1_eat_snack`：
   - 风险最小，当前入口集中在 thought bubble click 后。
   - 阶段 2 保留“气泡点击后吃东西”，但改成通过 runner 回报结果。
3. 再包装 `cat1_play_yarn`：
   - 必须保留 yarn 隐藏 / 恢复。
   - （阶段 2 历史行为）walk finish / pair move 的概率触发仍可调用 runner，但要回报结果；当前最终版本不沿用此规则。
4. 再包装 `cat1_social_ping`：
   - 保留声音成功后带出 thought bubble 和 compact mirror reaction。
   - 声音失败、气泡失败、mirror 不可用要能回报 failed 或 partial detail。
5. 最后包装 CAT2/CAT3 sleep feedback：
   - tier 变化、拖拽、return 都要能停止声音和 ZZZ。

阶段 2 不做：

1. 不统一封口旧入口。
2. （阶段 2 历史边界）不改变气泡点击、ambient timer、sleep timer、walk/pair move 概率触发的用户可见行为；阶段 3 的最终边界以第 6 节为准。
3. 不在阶段 2 启用 selector。
4. 不让 NEKO-PC 实现 provider 或保存动作状态。
5. 不接入 cat greeting 后端记忆。

### 5.1 通用动作生命周期契约

每个可接入动作都必须有清晰生命周期。状态机不能只“调用一个函数”，而要知道动作是否真的启动、运行中、完成、失败或被打断。

通用生命周期：

```text
candidate
  -> provider_allowed / provider_rejected
  -> requested
  -> started
  -> running
  -> done / failed / cancelled / interrupted
  -> restore
  -> observation feedback
```

通用规则：

1. Provider 只判断能不能做，不改 DOM、不播放声音、不移动窗口。
2. Runner 只有在真正启动成功后，才写该动作自己的 cooldown。
3. Runner 必须持有 token，旧 token 的异步回调不能覆盖新动作或恢复链。
4. Runner 必须回报明确结果：`done`、`failed`、`cancelled`、`interrupted`。
5. 被拖拽、return、tier 改变、页面隐藏、可见容器消失时，动作应进入 interrupted 或 cancelled。
6. restore 是生命周期的一部分，不是可选清理。例如恢复默认猫图、恢复 yarn、恢复 journey、停止音频、清掉气泡。
7. 结果回写 Cat Mind 时，只写真实发生的结果；provider 拒绝不写动作完成类反馈。

动作级生命周期要保留当前体验细节：

| 动作 | 必须保留的当前体验 |
|---|---|
| `cat1_social_ping` | 声音成功后才带出气泡；compact top edge 场景可同步 mirror reaction；声音/气泡失败要可观测 |
| `cat1_eat_snack` | 吃东西期间暂停/恢复 CAT1 journey；GIF 和音频都结束后才算完成；取消时恢复默认 art |
| `cat1_play_yarn` | 播放前隐藏 yarn；结束或取消必须恢复 yarn；不能破坏 chat 主链路恢复 |
| `cat2_nap_feedback` / `cat3_sleep_feedback` | 只在对应 tier 保持有效；睡眠声和 ZZZ 气泡绑定实际播放结果；拖拽/return/tier 变化时停止 |

### 5.2 NEKO：`cat1_social_ping`

当前事实：

```text
CAT1 ambient sound timer
  -> play random cat1 voice
  -> show thought bubble for sound
  -> compact top edge 时同步 mirror reaction
```

实施：

1. 提取 provider：
   - 检查 CAT1。
   - 检查 return ball 可见。
   - 检查无拖拽、无 active independent action。
   - 检查音频/气泡能力。
2. 提取 runner：
   - 播放一次轻声表达。
   - 成功后按实际结果显示气泡。
   - compact top edge 时仍允许 mirror reaction，但这是结果联动，不是独立动作。
3. 回报：
   - `social_ping_done`
   - `social_ping_failed`
   - `action_interrupted_by_drag`
   - `action_interrupted_by_return`

此阶段原 CAT1 ambient timer 可以继续调用这个 runner，但 selector 还不接管。

### 5.3 NEKO：`cat1_eat_snack`

当前事实：

```text
thought bubble click
  -> pop bubble
  -> _playNekoIdleCat1EatAction()
```

实施：

1. 把 `_playNekoIdleCat1EatAction()` 包装成独立 runner。
2. provider 检查：
   - CAT1。
   - return ball 可见。
   - 无拖拽。
   - 无 play/eat active。
   - art 可用。
3. runner 回报：
   - `eat_done`
   - `eat_cancelled`
   - `action_interrupted_by_drag`
   - `action_interrupted_by_return`
4. 此阶段可以保留气泡点击后调用吃东西，但同时发送 `thought_bubble_pop` observation。

第三阶段再切断“气泡点击直接吃东西”。

### 5.4 NEKO：`cat1_play_yarn`

当前事实：

```text
（阶段 2 历史）walk finish / pair move 某些分支
  -> 概率尝试 play action
```

实施：

1. 包装 `_playNekoIdleCat1PlayAction()` 为 runner。
2. provider 检查：
   - CAT1。
   - near chat。
   - yarn 可隐藏和恢复。
   - 无拖拽、return、chat transition 冲突。
3. runner 保持现有隐藏/恢复 yarn 的逻辑。
4. 回报：
   - `play_done`
   - `play_cancelled`
   - `action_interrupted_by_drag`
   - `action_interrupted_by_return`

此阶段（历史）walk finish / pair move 的概率触发可以继续存在，但需要通过 runner 回报结果；阶段 3 最终版本仅保留 walk finish 的 25% journey 内部表现二选一，且不回报 Cat Mind 结果。

### 5.5 NEKO：CAT2/CAT3 sleep feedback

当前事实：

```text
CAT2/CAT3 sleep sound timer
  -> play sleep sound
  -> show sleeping ZZZ bubble
```

实施：

1. 包装 sleep sound + ZZZ 为 provider/runner。
2. provider 检查：
   - 当前 tier 是 CAT2 或 CAT3。
   - 无拖拽。
   - 音频或 ZZZ 能力可用。
3. runner 回报：
   - `sleep_feedback_done`
   - `sleep_feedback_failed`
   - `action_interrupted_by_drag`
   - `action_interrupted_by_return`

此阶段原 sleep timer 可以继续调用 runner。

### 5.6 NEKO-PC

阶段 2 原则上不需要 NEKO-PC 实现动作 provider。

只需要：

1. 确认 play yarn 隐藏/恢复独立毛线球窗口时，NEKO-PC 现有 `setCompactChatBallTemporarilyHidden` 链路没有变化。
2. 确认 idle-dock / compact surface 的事件在动作 runner 执行期间不会被误当作动作失败。
3. 如果 runner 需要知道桌面 yarn ball 是否可隐藏，NEKO-PC 只提供 capability observation，不直接决定动作。

### 5.7 阶段 2 验证

1. 单独调用每个 provider 的 dry-run，确认可返回 allowed / rejected / reason。
2. 单独调用 runner，确认 done / failed / interrupted 都有回报。
3. 旧随机 timer / 概率触发仍工作。
4. 拖拽和 return 能打断 runner，且不补播。
5. NEKO-PC 的 chat minimized / idle-dock / return ball drag 测试保持通过。

## 6. 阶段 3：启用 selector 接管第一批动作

目标：把第一批动作从随机 timer / 概率触发改为 Cat Mind + gates + selector 触发。

### 6.0 接管前统一入口封口

本轮要接管的动作范围一旦确定，必须先把这个范围内所有旧直接入口统一封口，再允许 selector 发起任何动作 request。不能一边接管某个动作，一边让同一批待接管动作里的其它旧随机 / timer / 概率入口继续直接启动 runner，否则调试时无法判断动作到底来自状态机还是旧入口。

封口后删除旧调度分支；本版本已不保留旧 timer 作为 scheduler wakeup。未来若有新的事实型时间信号，最多只能转成 observation 或异步 scheduler wakeup，不能调用 runner。

封口原则：

1. 先确定本轮接管范围，再统一封口该范围内所有旧直接入口。
2. 封口完成前，selector 不得请求任何待接管动作。
3. 封口完成后，完整动作池只由 hard gate、tier、provider 和评分决定；旧入口不再参与调度。
4. 运行时不提供动作池或 selector 的开关。
5. 调试 selector 时，日志里应能确认待接管动作来源只有 `cat_mind`，现有入口只记录 observation / wakeup / skipped reason。

第一批动作的封口清单：

| 动作 | 接管前必须封口的现有入口 | 封口后的作用 |
|---|---|---|
| `cat1_social_ping` | CAT1 ambient timer / 声音反馈入口 | 只发送 ambient / timer observation，或唤醒 scheduler |
| `cat1_eat_snack` | thought bubble click 派生的吃东西入口 | 只发送 `thought_bubble_pop` observation，保留 pop 反馈 |
| `cat1_play_yarn` | pair move 后的概率触发入口；walk finish 的既有 journey 二选一 | pair move 只发送 completed observation；walk finish 保留既有 25% 玩球 / 否则伸展本地表现 |
| `cat2_nap_feedback` | CAT2 sleep timer 直接播放入口 | 只发送 sleepiness / timer observation，或唤醒 scheduler |
| `cat3_sleep_feedback` | CAT3 sleep timer 直接播放入口 | 只发送 sleepiness / timer observation，或唤醒 scheduler |

实施顺序：

1. provider / runner 先具备 dry-run、execute、result feedback。
2. 为本轮接管范围补齐所有现有入口清单和 source 标记。
3. 统一让本轮接管范围内所有现有直接入口进入 observation-only / wakeup-only。
4. 确认没有旧入口能直接启动待接管 runner 后，再打开 selector。
5. 所有已接入动作都通过同一 selector 候选流程，不存在未开放动作回到旧入口直触发的路径。
6. 如需排查，使用只读 debug 面板；不得恢复旧入口的直接调度权。

### 6.0.1 阶段 3 开工准备

阶段 3 的第一刀不是“先写会播放动作的 selector”，而是先把调度来源收窄到一个地方。

本阶段按三个小切片实施：

1. **封口切片。**
   - 先确定单一 selector 调度链，再删除旧入口的调度分支。
   - 本批旧入口全部失去直接调度权，并删除其回退分支。
   - 旧入口只发送 observation 或 scheduler wakeup，日志/事件里保留 source。
   - 此切片完成前，selector 不得发起 action request。
2. **选择器切片。**
   - selector 只读取 Cat Mind、hard gate、tier gate 和 provider dry-run。
   - selector 输出 `quiet`、`stay_idle` 或一个候选动作建议。
   - provider 拒绝只记录 reason，不强行换动作。
   - 第一版只做方向性打分，暂不照搬 action scoring 文档里的完整公式和阈值。
3. **执行切片。**
   - selector 接入后，才允许 action request 到 adapter。
   - 所有已接入动作进入同一候选池。
   - runner 真正启动成功后才写该动作 cooldown。
   - runner 完成、失败或被打断后，必须 restore 完成再写 action result。

阶段 3 开工前需要确认的旧入口：

| 动作 | 当前旧入口 | 阶段 3 封口后 |
|---|---|---|
| `cat1_social_ping` | `_scheduleNekoIdleCat1AmbientSoundInterval()` timer 直接调用 `_playNekoIdleCat1AmbientSound()` | timer 已删除；Cat Mind clock 与真实 observation 提供判断机会 |
| `cat1_eat_snack` | thought bubble click 后 `_playNekoIdleCat1EatAction(button)` | 只保留 pop 反馈和 `thought_bubble_pop` observation |
| `cat1_play_yarn` | pair move 完成后的概率 `_playNekoIdleCat1PlayAction(button)`；walk finish 的既有表现二选一 | pair move 概率派生已删除；walk finish 保留 25% 玩球 / 否则伸展，并仅上报 walk observation |
| `cat2_nap_feedback` | sleep timer 直接 `_playNekoIdleSleepSound(tier, token)` | timer 已删除；tier / clock observation 提供判断机会 |
| `cat3_sleep_feedback` | sleep timer 直接 `_playNekoIdleSleepSound(tier, token)` | timer 已删除；tier / clock observation 提供判断机会 |

阶段 3 开工前需要补强的 provider gate：

1. `cat1_play_yarn` 不能只复用 CAT1 通用按钮 gate；还要确认 near-chat、yarn 可隐藏/恢复、无 compact surface dragging、无 chat transition 冲突。
2. `cat1_social_ping` 要在 compact surface dragging、return pending、transition active 时安静。
3. `cat2_nap_feedback` / `cat3_sleep_feedback` 要继续保证拖拽、return、tier 变化中不会残留声音或 ZZZ 气泡。
4. 所有被 gate 拦住的动作都不能写 cooldown，也不能写完成类 action result。

阶段 3 的调试信号至少要能回答：

1. 这次判断是由 tick、observation、wakeup 还是 action result 触发。
2. hard gate 是否拦截，拦截 reason 是什么。
3. 哪些候选被 tier gate / provider gate 移除。
4. 最终选择了哪个动作或为什么 quiet。
5. 动作来源是否只有 `cat_mind`，没有旧入口并行直触发。

### 6.0.2 阶段 3 接管前封口切片收口（历史切片）

本节记录已废止的“接管前”历史切片；当前以 6.0.5 及之后的最终单一调度权为准。

已完成：

1. 历史上曾用临时接管开关验证统一封口；最终版本已移除该开关及其回退语义。
2. 该历史切片当时曾将旧入口降级为 wakeup；最终版本进一步删除了 ambient / sleep / pair-move timer 及 walk finish 的动作派生 wakeup。walk finish 仍保留既有 25% 玩球 / 否则伸展的 journey 内部表现二选一：它不进入 selector，也不写 Cat Mind result、cooldown 或五维。现在只保留：
   - thought bubble click 的 pop 与 `thought_bubble_pop` observation；不再直接吃东西。
   - walk、drag、compact、idle-dock 等现有表现的事实 observation；它们不携带任何动作派生调度权。
3. 最终版本不保留单动作灰度；所有已接入动作只由 selector 候选池决定，旧入口不会恢复直跑。
4. provider gate 已补强：
   - CAT1 通用 provider 会挡 return pending、transition active、拖拽、active independent action、不可见 return ball。
   - `cat1_social_ping` 会额外挡 compact surface dragging。
   - `cat1_play_yarn` 已拆成专属 provider，检查 near-chat、yarn hide/restore 能力、compact surface dragging 和 chat transition。
   - CAT2/CAT3 sleep feedback 会挡 return pending、transition active、拖拽、tier mismatch、缺音频和不可见 return ball。

后续执行切片已完成。长期约束仍然是：任何事实 observation 只能异步排队到下一轮判断，不能在现有表现函数栈内同步发 action request；NEKO-PC 仍只提供 observation / 窗口安全契约，不保存 Cat Mind，也不决定猫要做什么。

当前验证：

1. `node --check static/app/app-cat-mind.js`
2. `node --check static/avatar/avatar-ui-buttons.js`
3. `tests/unit/test_cat_idle_state_machine_static.py`，其中覆盖气泡点击只 pop、旧 ambient / sleep / pair-move timer 已删除，且旧入口不 dispatch action request / action result。
4. `tests/unit/test_avatar_return_button_cat1_static.py`
5. `tests/unit/test_avatar_return_button_idle_tiers_static.py`
6. `tests/unit/test_react_chat_idle_dock_static.py`
7. NEKO-PC idle-dock / compact surface drag / return ball / Wayland input region / activity signal 契约测试。

### 6.0.3 selector 只读切片收口（历史切片）

已完成：

1. `neko:cat-mind:observation` 成为实际 observation bridge；每次有效 observation 会发布 `state-change`，return 后发布本地 `return-summary`。本历史切片当时尚未接入后端问候；当前第 7 节已完成一次性 episode 附着。
2. `neko.catMind.debug` 可通过 `localStorage['neko.catMind.debug']='true'` 在刷新后开启中文前端调试浮层，显示五维、recent event 数量、scheduler 状态，并将当前六个动作分别显示理论基础分、冷却扣分、理论分、当前可用分、动作阈值、候选结果与 provider 拒绝原因/条件依据；没有情境调整或 recent-bubble 加减分。
   - `neko.catMind.debug` 可由自身同源 localStorage 控制；Electron Pet 不依赖网页窗口的临时全局变量。
3. selector 接入后，observation / wakeup / action result 只会异步合并成下一轮只读判断；它读取 Cat Mind、桌面 runtime gate、tier gate 和 provider dry-run，输出 `quiet`、`stay_idle` 或候选建议。
4. provider rejected 只保留在候选 debug reason，不写 cooldown、action result 或失败结果；`cat1_play_yarn` 仍只在 provider 已确认 near-chat 后作为候选。
5. return 后清理 Cat Mind 运行态；本历史切片只保留结构化 summary draft，当前第 7 节再由网页 return 原子消费它，避免猫娘形态继续持有五维、recent events 或上一轮 decision。

该历史切片当时仍未派发 `neko:cat-mind:action-request`、不调用 runner、不写 cooldown；这些已由后续 6.0.5 网页执行切片接管。仍不新增周期性 selector tick，也不让 selector 主动启动 walk-to-chat、compact top edge / mirror 或 chat idle-dock。

### 6.0.4 范围校正与后续处理

1. 现阶段的验收与后续实现优先限定在网页 renderer。桌面 Pet 的入口封口、发布配置和具体触发链不能只由代码阅读判定；保留现有桌面 observation / 窗口安全职责，等实际运行验证后再按事实处理，不能为此在 NEKO-PC 新增 Cat Mind 或动作选择。
2. `cat1_play_yarn` 现有桌面毛线球的 hide / restore 表现正常时，缺少 Pet 到桌面的原生成功回执只表示调用为 fire-and-forget，不构成显示故障，也不是当前阶段的阻塞项。桌面实施时先实际观察 hide、restore、drag、return；只有出现真实不一致才补最小能力证据或恢复链路，不能预先臆造 ACK 协议。
3. `neko:cat-mind:observation` bridge、`state-change` / 本地 `return-summary` 与 debug 的 localStorage 解析均已审计保留；网页 renderer 的消费者边界见 6.0.3 与 6.0.5。它们不能作为桌面端已完成入口封口或动作接管的依据，后续也不得据此扩展桌面端消费者。
4. 网页端执行切片须保持以下顺序：先固定 action request 的 payload / 路由约束；runner 真正启动后才写 cooldown；restore 完成后才写 action result；每项都补回归测试。具体已实现契约见 6.0.5。

### 6.0.5 网页端执行切片收口

本切片只在 NEKO 网页 renderer 落地；不改 NEKO-PC，不新增桌面端动作控制或桌面 ACK。

1. selector 在本轮已有合法候选时进入异步 request 流程。request 只携带 `requestId`、`actionId`、`source: "cat_mind"`、`tier`、`timestamp` 与 `{ triggerTypes, score }`；不携带 button、DOM、audio 或桌面对象。
2. `static/avatar/avatar-ui-buttons.js` 是网页端唯一 action-request consumer。它会先再次调用已有 provider dry-run，复验 selector 的全局硬锁（包括 return、猫咪拖拽、紧凑聊天窗口拖拽、转场、独立动作 / Cat Mind 音频）以覆盖“请求已排队、runner 尚未启动”间的竞态；provider 或执行前 guard 拒绝时，只通过 `window.nekoCatMind.acknowledgeActionRequest({ status: "rejected", ... })` 清掉 pending request 并写 debug reason，不写 cooldown、不写 action result、不回退旧入口。
   - 上游 CAT1 playground drop 是用户主动进入的独立长生命周期；其 events/state 不成为 Cat Mind observation、candidate、score 或 result，Cat Mind 只依赖既有 `activeIndependentAction` 硬锁静默。
3. adapter 复用既有 eat / play / ambient / sleep runner，不新写表现。它先以 `accepted + runId` 绑定 request 与既有 runner；仅在 runner 真正 started 时以同一 `runId` 确认 `started`：eat/play 以已进入 visual active 的同步结果为证据，social/sleep 以现有 audio play success callback 为证据。只有 `started` 才写 cooldown 和 active action。
4. runner 的既有唯一 action result 继续负责 done / failed / cancelled / interrupted；adapter 不重复发 result。adapter 启动的 result 必须是 `source: "cat_mind"`，并在 detail 透传同一 `requestId` 和 `runId`。Cat Mind 只消费匹配当前 run 的 terminal result；过期、伪造或非 terminal result 只留 debug state，不改五维或 recent events。
5. eat/play 正常完成时会在恢复默认 art、journey 与 yarn（play）后回写 result。drag、return、tier 或 container interruption 保持既有主链恢复视觉，不由 Cat Mind 覆盖；runner 在停止自身状态、class / yarn 清理后回写 interrupted。social/sleep 在停止音频并清理气泡后回写 result；social 普通气泡不能按 audio identity 过滤清理。result 只排队下一轮判断；该轮先输出一次 `stay_idle`，避免动作完成后立即串播。
6. cooldown 是 started 后记录的软降权，不是 hard gate。早期执行切片曾使用同动作 `0.5` 降权且不新增 Cat Mind tick；该临时规则已由 6.0.6 的有限、随时间恢复 cooldown 与自主时间流覆盖。无论何种评分规则，都不得改回旧入口直跑。
7. 当前未为永不 resolve / reject 的 audio play promise 臆造 timeout；它会保留 accepted pending state，作为后续真实运行观察项。只有出现实际悬挂证据后，才单独定义其 timeout 语义。
8. 网页端 Cat Mind 始终拥有自主动作调度权，不保留全局、selector 或单动作运行时开关；唯一保留的 `neko.catMind.debug` 只影响调试面板。该边界不扩展到 NEKO-PC。

### 6.0.6 阶段 3.1：自主时间流与评分收口

本切片覆盖早期“只做方向性评分、不新增 tick”的实施边界。数值基线参考 `cat-idle-state-machine-action-scoring.md`，并在完成 provider / lifecycle 证明后扩展到 `cat1_small_move`；不恢复旧随机调度，也不把 walk-to-chat、compact/mirror、idle-dock、drag 等表现流程变成候选。

1. Cat Mind 在猫形态期间自持 30 秒低频 clock：进入猫形态时启动，return / reset 时清理。每次 clock 使用实际 `elapsedMs` 折算五维变化，检查间隔不是动作频率；浏览器后台延迟不会丢失经过时间，也不会因多次 tick 重复结算。
2. clock 真实产生 `cat_elapsed`、`inactive_elapsed`、`since_last_action` 三种时间 observation，并只异步排入下一轮 selector。三者共享同一段 elapsed；只有 `cat_elapsed` 结算五维，且时间 observation 不写入 recent events，避免淹没互动和动作经历。
3. 五维继续以 `0–1` 保存，但 entry 初值、按 tier 的每分钟变化和已接入动作反馈采用评分文档的 `0–100` 基线等比例换算；评分输出仍使用 `0–100` 分数。当前动作池包含 `cat1_small_move`，但仍不支持 CAT1 nap、walk-to-chat、compact/mirror、idle-dock、drag；后五类只能作为 observation 或 hard gate，绝不评分请求。
4. hard / tier / provider gate 仍先执行。只有 provider 已允许且动作分数达到自己的阈值，才进入排序和 action request；全部低于阈值时输出 `stay_idle`。因此单次 hover / 气泡点击只改变五维和增加判断机会，不拥有动作直触发权。
5. 当前映射为：`cat1_social_ping` 阈值 45、冷却 180 秒；`cat1_small_move` 54 / 70 秒；`cat1_eat_snack` 52 / 180 秒；`cat1_play_yarn` 54 / 240 秒；`cat2_nap_feedback` 50 / 240 秒；`cat3_sleep_feedback` 45 / 300 秒。小移动从 55 下调到 54，是移除情景额外加分后，以 60 分钟 fake-clock 循环验证得出的最小阈值修正：55 不会入选，54 会偶尔入选，53 则会明显偏多。cooldown 只在 runner `started` 后开始，按剩余时间独立软扣分，到期自动恢复；provider reject 和 started 前的 failed / cancelled / interrupted 不创建 cooldown。若动作已经 started，之后的 terminal result 不额外写入、也不清除已合法开始的 cooldown。
6. 同分使用固定顺序（social → sleep feedback → small move → eat → play），不引入随机扰动。small move / play yarn 的 near-chat 仍是 provider 硬条件，状态机绝不为满足它主动发起 walk-to-chat。
7. debug 面板需对每个动作分别展示理论基础分、冷却扣分、理论分、阈值、本轮可用分、gate / provider 移除原因和 provider 条件依据。provider 拒绝时必须先展示实际短路的拒绝原因；其余未满足项只能标为“现场还不满足”，不能冒充该轮已逐项执行的条件。小移动还要显示 hover、紧凑顶边、落位、目标、容器、几何和空间事实。只对已通过 provider 的动作计算本轮可用分；被 gate 拦住时不得把理论分称为可执行分。面板每 500ms 读取只读 snapshot，使持续时间、冷却与当前计算分实时更新；五维仅在显示层换算成整数 `0–100`，内部仍保存 `0–1`。
8. 桌面聊天窗口在 Cat Mind normalizer 边界按最小化状态与窗口几何签名去重。首次、状态变化或几何变化可生成一条窗口 observation；原生 IPC、BroadcastChannel 或本地 UI 的同状态/同 rect 必须在 reducer 前合并，不更新五维、recent events 或 scheduler。`idle-dock-enter` 作为独立落位事实只记录一次，离开后才允许再次记录。

### 6.0.7 阶段 3.2：小移动、视觉间距与动作循环校准

1. `cat1_small_move` 是 CAT1 的第六个正式候选，复用既有 pair move 表现而非新写一套动画。它只在已 settled near chat、非 compact top edge、无 journey / hover / drag / active action、且 provider 证明有可用移动空间时出现；球远时只保留 `near_chat_unavailable` debug reason，selector 不得启动 walk-to-chat。
2. 旧 pair-move timer 已删除，不保留 per-action wakeup；Cat Mind 的低频 clock 与真实 observation 统一异步提供下一轮判断机会。adapter 再次 dry-run 后才启动既有 pair move，并以同一个 `requestId + runId` 完成 `accepted → started → cooldown`；最终坐标、class 和 idle art 恢复后才回写 `small_move_done`。drag、return、tier change 在恢复后回写对应 interrupted result；普通条件失效才是 cancelled。
3. 小移动完成反馈采用 `stimulation -8`、`energy -3`、`sleepiness +1`（内部按 `0–1` 等比保存）。它只适度满足“想动”；下一轮只按五维、动作自身 cooldown 和真实 gate 决定，不另加 recent-action 分数。
4. 轻声回应不应成为“无聊”的默认出口：保留三分钟独立 cooldown，完成后 `social -18 / energy -1`；评分中 stimulation 仅作 `0.08` 小权重。用户 hover、点气泡、普通拖拽和快速拖拽都通过既有五维提高后续互动倾向：轻互动主要提高社交/刺激，强拖拽同时消耗精力、增加困意。玩毛线、吃和睡眠完成不统一加社交；它们各自只适度回写真实的消耗/恢复维度。这样 near-chat 可用时会形成玩耍→食欲/休息等有限循环，near-chat 不可用时允许 `stay_idle`，绝不为填满空档主动 walk-to-chat。
5. 站位使用 24px 容器间距，右侧不再向球内偏移。计算侧目标后必须再次验证猫框与球框之间仍有此间距：视口 clamp 使任一侧重叠时改选另一侧；两侧都不可用则不启动该侧 walk 或小移动。pair move 对猫和球应用同一位移，因此会保持已验证的相对间距。
6. 验收至少覆盖：manual / auto 的时间流在 provider 允许时能轮换小移动、玩毛线、吃与社交；60 分钟 fake-clock 仿真同时覆盖 near-chat 与非 near-chat：前者至少进入 play、eat 和 small move，后者不启动 play / small move 且大部分轮次保持 `stay_idle`；social 完成后的 6 分钟内不会机械重复；small move 的 reject 不写 cooldown/result、started 后才有 cooldown、done/interrupted 后只写一次 terminal result；边缘 clamp 不产生重叠站位。


### 6.1 NEKO：Decision Runtime

实现内容：

1. Hard gate：
   - return pending
   - drag pending / dragging
   - transition active
   - active independent action
   - return ball invisible
   - invalid page / character
2. Tier gate：
   - CAT1：`cat1_social_ping`、`cat1_small_move`、`cat1_eat_snack`、`cat1_play_yarn`
   - CAT2：`cat2_nap_feedback`
   - CAT3：`cat3_sleep_feedback`
3. Provider gate：
   - 调 provider dry-run。
   - provider 拒绝时记录 reason，不强行换动作。
4. Utility selector：
   - 第一版只用方向性评分。
   - 不采用固定复杂公式。
   - action cooldown 是软降权，不是硬拒绝。
5. Scheduler：
   - 可以由 tick、observation、动作完成后触发下一次判断。
   - 用户交互可以提高判断机会，但必须受 hard gate 和打扰成本约束。

### 6.2 Hard gate 生命周期

Hard gate 本身也有生命周期。实现时不能只在 selector 一瞬间读一次布尔值，而要明确每个硬条件何时进入、何时退出、退出后是否需要重新调度。

| Hard gate | 进入条件 | 退出条件 | 退出后处理 |
|---|---|---|---|
| `returnPending` | return click、cat-to-model transition、return cleanup 开始 | return 链恢复完成或 Cat Mind reset | 不补播被跳过动作，只生成/提交 summary |
| `dragPending` | return ball pointer down / drag pending | drag active、drag cancel、drag end | 若取消，记录轻交互；若 active，转入 dragging |
| `dragging` | return ball drag active / motion | drag end / drag cancel | 记录 drag observation，允许之后重新调度 |
| `transitionActive` | model-to-cat 或 cat-to-model 转场开始 | 转场 promise 完成、overlay cleanup 完成 | 只同步状态，不立即抢播动作 |
| `activeIndependentAction` | provider runner `started` | runner done / failed / cancelled / interrupted 且 restore 完成 | 写 action result，再由 scheduler 决定是否重新判断 |
| `returnBallInvisible` | return ball hidden、container 不可见、页面不在猫形态 | return ball visible 且 tier 有效 | 只恢复 observation，不主动补播 |
| `chatSurfaceDragging` | compact surface drag start / native drag active | drag finish / cancel / settled | 记录窗口变化，动作重新判断需等 settled |

Hard gate 约束：

1. 拖拽 pending 也必须挡动作，不能等到真正 moved 才挡。
2. gate 退出不等于马上播放动作，只能触发一次“可以重新判断”的机会。
3. 被 gate 阻止的动作不写 cooldown。
4. 被 gate 打断的动作要等 restore 完成后才能清 `activeIndependentAction`。
5. NEKO-PC 的 native drag / Wayland input region 只能影响 gate 和 observation，不得直接触发动作。

### 6.3 NEKO：替换旧随机入口

旧调度分支删除或封口完成后，selector 始终按 hard gate、tier、provider 与评分生成候选：

1. `cat1_social_ping`
   - CAT1 ambient timer 不再直接决定播放；如仍保留时间信号，只能异步唤醒 scheduler。

2. `cat1_eat_snack`
   - selector 开启后，气泡点击只发送 `thought_bubble_pop`。
   - 吃东西由 `appetite` 和 provider 决定。

3. `cat1_play_yarn`
   - pair move 的概率触发不再直接调用 play，完成只发送 observation。
   - walk finish 的既有 25% 玩球 / 否则伸展只属于 journey 内部表现；它不发 action request/result，walk 本身只发送 observation。

4. `cat2_nap_feedback` / `cat3_sleep_feedback`
   - sleep timer 不再直接决定播放；如仍保留时间信号，只能异步唤醒 scheduler。

切换要求：

1. 旧直接入口必须先按接管范围统一关闭调度权。
2. 动作是否可选只由 hard gate、tier、provider 和评分决定。
3. 任一动作出现问题时，修正对应 provider / runner / 生命周期；不得让它回到旧入口直触发。
4. runner 始终只由 adapter 调用。

### 6.4 NEKO-PC

阶段 3 需要关注桌面端观测和安全边界：

1. selector 判断过程中不得请求 NEKO-PC 移动窗口。
2. chat idle-dock 仍由 `app-react-chat-window.js` 和 NEKO-PC bridge 按现有规则执行。
3. return ball 原生拖拽期间，NEKO 的 hard gate 必须看到 dragging。
4. compact surface 正在拖动时，`cat1_social_ping` 和 `cat1_play_yarn` 应被 provider 或 hard gate 拦住。
5. Wayland / niri input region 事件只能作为 dragging observation，不触发自主动作。

如需新增 NEKO-PC 测试：

1. 验证 idle-dock payload 不被状态机字段污染。
2. 验证 Cat Mind 在 Pet renderer 持续收到 return ball drag observation；Pet preload / niri 只消费该事实维护 input shape，不改变其语义或把它变成动作请求。
3. 验证 play yarn 隐藏/恢复独立毛线球窗口的契约不变。

### 6.5 当前体验细节保留清单

Selector 接管后，以下体验不能丢：

1. 气泡点击仍有 pop 反馈；只是不能再直接触发吃东西。
2. 吃东西结束后要恢复当前 tier 的默认猫图，并继续原本可恢复的 CAT1 journey。
3. 玩毛线必须隐藏/恢复 yarn，不能出现两个 yarn 或 yarn 消失不回。
4. CAT1 compact top edge / mirror 仍由现有表现流程控制，状态机只能消费结果。
5. CAT1 walk-to-chat / stretch 仍由现有表现流程控制，状态机不能主动启动。
6. CAT2/CAT3 sleep feedback 不能在拖拽、return、tier 变化中残留声音或 ZZZ 气泡。
7. return ball 拖拽降级规则不变：CAT2 拖拽降 CAT1；CAT3 按现有释放次数规则降 CAT2。
8. Electron chat idle-dock、独立毛线球窗口、Wayland/niri input region 不因状态机接管而改变窗口行为。

### 6.6 阶段 3 验证

NEKO：

1. 静态测试：
   - Cat Mind 是唯一自主动作调度源；旧直触发入口和旧 timer 调度均已删除。
   - 保留的现有表现流程只上报 observation；气泡点击只发送 `thought_bubble_pop` observation。
2. 运行时测试：
   - CAT1 中互动后 social/eat/play 倾向变化可见。
   - CAT2/CAT3 睡眠反馈不会在拖拽中播放。
   - return 期间所有 runner 停止。
3. 回归：
   - `tests/unit/test_app_auto_goodbye_phase1.py`
   - `tests/unit/test_avatar_return_button_cat1_static.py`
   - `tests/unit/test_avatar_return_button_idle_tiers_static.py`
   - `tests/unit/test_react_chat_idle_dock_static.py`
   - `tests/unit/test_auto_goodbye_goodbye_return_contract.py`

NEKO-PC：

1. `node --test test/react-chat-idle-dock-contract.test.js`
2. return ball drag / compact surface drag / input region 相关现有 node tests。
3. Windows / Linux / Wayland 或 niri 场景至少做一次人工或自动运行观察。

## 7. 阶段 4：return 会话摘要和后端问候（v0.1 代码与自动回归已收口）

目标：猫娘回来时能自然带着刚刚作为猫的**一段可信经历**说一两句。它不是从长会话里随便挑两条日志，更不是把 return 瞬间的五维翻译成人格判断；它只压缩最后一个有意义的“活动 / 休息”自然段。

阶段 4 的一句话原则：

> 猫形态内把严格验证过的行为归并为一段一次性会话经历；猫娘只消费这段结构化经历，并在它存在时把它作为本次猫形态经过的事实场景，随后它立即消失。

### 7.0 实施前基础与已替换的旧草稿

以下基础已经存在，阶段 4 必须复用：

1. `static/app/app-cat-mind.js` 已在 return 时生成 `returnSummaryDraft`、清掉猫形态运行态，并派发 `neko:cat-mind:return-summary`。
2. 严格动作生命周期已能确认 `source: "cat_mind"`、`actionId + requestId + runId` 匹配的 terminal result；只有匹配的 `done` 才是真实完成事实。
3. `static/app/app-auto-goodbye.js` 已在 return 时派发 `neko:cat-greeting-check`，携带停留时长、visual tier 与 manual / auto 入口。
4. `static/app/app-websocket.js → main_routers/websocket_router.py → main_logic/core/greeting.py:trigger_cat_greeting()` 是既有且唯一的猫娘 return 问候投递链；它已有静默、socket、语音、takeover、并发和失败不阻塞恢复的语义。
5. `config/prompts/prompts_proactive.py` 已按 tier × 时长 × manual / auto 生成短、自然、无思考过程的环境提示。

实施前草稿不能直接扩展，必须替换：

1. `getSummaryEventTags()` 只扫描最多 40 条 `recentEvents`，会在长会话或高频互动中丢失早期事实，也不包含严格完成的已接管动作。
2. 它还会把窗口、near-chat、compact、edge peek 等 observation 变成可说标签；这些是状态机的环境事实，不是猫娘应讲述的经历。
3. `dominant_state` 是 return 瞬间的内部驱动。长会话、tier 升降或拖拽降级后它可能失真，不得跨 return 作为语气或关系解释。
4. 当时只有只读 `getReturnSummaryDraft()`，没有“读取并清除”的原子消费；重复 return 可能误用上一轮草稿。
5. 当时 Cat Mind 已监听 PNGTuber return，但现有猫问候 handler 只监听 Live2D / VRM / MMD；覆盖范围仍是实施前决策门。

当前实施结果：

1. `returnEpisodeAccumulator` 已替换 raw `recentEvents` / `dominant_state` 投影；它只在严格完成动作和既有六类明确互动 observation 处更新，debug 仅只读展示 accumulator 与 preview。
2. 支持的网页 return（Live2D / VRM / MMD）通过 `consumeReturnSummaryDraft()` 原子附着到既有 `cat_greeting_check`；短时静默、无 socket 和发送失败均不建立跨 return 缓存。Cat Mind 缺失或读取异常时保留原问候 payload / 恢复链。
3. router 已把顶层 duration / tier / was_auto 规范为 canonical 值，只将安全的 `episode` enum 作为一次性参数传进 `trigger_cat_greeting(...)`；不写数据库、memory 或 Cat Mind 以外的状态。
4. `get_cat_greeting_episode_scene()` 已用服务端 locale 表把 enum 映为事实场景；有效 scene 走通用的 episode return prompt，tier × 时长 × entry 只提供回归语气。缺失或非法 episode 时，既有等待问候模板保持原样，仍通过现有 `prompt_ephemeral` 投递。
5. PNGTuber 仍明确排除在经历问候之外：它保留既有 return observation / `return-summary` 事件，但不保存 `returnSummaryDraft`，因此不会把无人消费的经历遗留到下一次网页 return。

### 7.1 范围与非目标

阶段 4 做：

1. 在猫形态内把长会话归并为一条有界、可审计的 `episode`。
2. 只把这一次 return 的 `episode` 附着到现有 `cat_greeting_check`。
3. 有可信 episode 时，让它成为猫娘问候中“刚才作为猫经历了什么”的事实场景；tier × 时长 × entry 只调等待、被叫回来和回归的语气。无 episode 时完全保留原等待问候。

阶段 4 不做：

1. 不让猫娘形态继续持有五维、recent events、cooldown、selector、episode accumulator 或 Cat Mind。
2. 不把五维、动作分数、recent events、原始时间线、动作次数、坐标、DOM、窗口状态、动作 ID、开放文本或模型生成摘要跨边界传递。
3. 不新增 LLM “总结调用”、重试队列、独立问候通道或长期记忆。
4. 不让 NEKO-PC 保存、选择、解释或发送会话摘要。
5. 不因摘要绕过 `< 180s` 静默、goodbye silent、语音/takeover、会话忙或用户抢占等现有 guard。
6. 不向猫形态 return 注入普通主动问候的 `get_time_of_day_hint()`、餐食/节日/话题提示或其它跨功能上下文；本链只消费本次 return 的入口、时长、tier 和可选 episode。

结构化摘要及临时 prompt instruction 不进入 conversation history；猫娘最终可见的问候仍按现有 `prompt_ephemeral` 默认语义持久化。

### 7.2 猫形态内的有界经历归并

阶段 4 采用四层分离，而不是“事件数组 → prompt”：

| 层 | 内容 | 边界 |
|---|---|---|
| 证据层 | 严格完成动作、明确用户互动、tier 变化 | 只记录来源事实；不写语言、不推断用户意图。 |
| 归并层 | 当前活动段 `activeChapter` 与最近休息段 `lastRest` | 仅在 Cat Mind active 时存在；不参与五维、selector、cooldown。 |
| return 投影 | 一条可选 `episode` | 固定 enum；不含次数、分数、时间线或原始事件。 |
| 表达层 | 现有猫问候 prompt | 有效 `episode` 渲染为唯一的猫形态事实场景；无 episode 才走旧等待问候。 |

归并层是一个固定大小的 `returnEpisodeAccumulator`，进入猫形态时初始化，return/reset 时与运行态一起清掉：

```text
activeChapter:
  interactionSeen: boolean
  activityKinds: Set<cat1_social_ping | cat1_small_move | cat1_eat_snack | cat1_play_yarn>

lastRest:
  hadActivityBeforeRest: boolean
  highlight: optional activity kind
```

实现规则：

1. 严格匹配的 `done` 才能写入 accumulator：CAT1 动作写入 `activityKinds`；CAT2/CAT3 sleep feedback 写入 `lastRest`。重复同类动作只保留一个 kind，不累计次数；`failed / cancelled / interrupted` 不写入。
2. `drag_start`、`drag_end`、`drag_cancelled`、`rapid_drag`、`cat_hover_reaction`、`thought_bubble_pop` 只把当前活动段的 `interactionSeen` 置为真。它们不带原始 drag/hover/bubble 文本出猫形态，`return_click` 更不能算作可叙述互动。
3. `lastRest` 的证据只是严格完成的 `cat2_nap_feedback` / `cat3_sleep_feedback` 表现，不是对真实休息时长的断言。tier × 时长只调回归语气，不能覆盖 episode 的实际经过；`tier_changed`、最终 tier、drag demotion、窗口变化、走向聊天球、compact/mirror、idle-dock 都不能单独形成“睡过”或“被弄醒”的叙述。
4. sleep feedback 到来时：当前活动段有成功 CAT1 动作，则用它封成新的“活动后休息”、覆盖旧 `lastRest` 并清空 `activeChapter`；当前活动段只有 interaction，则建立/覆盖一个没有活动前置关系的 `rested` 并清空 `activeChapter`；只有当前活动段完全为空时，后续 CAT2/CAT3 feedback 才延续已有 rest。这样不会复用旧章节的 highlight，也不会把 interaction 后的新休息误说成旧的“活动后休息”。
5. `tier_changed`、drag demotion 和最终 tier 继续保留为既有 observation/debug 事实，但不单独生成 `episode`，也不被解释成苏醒或用户行为的因果。
6. 旧 journey walk finish 内部的 25% 本地玩球不经过严格 Cat Mind lifecycle；它、桌面 poll、near-chat、坐标、窗口层级与调试事件都不得写入 accumulator。
7. 互动发生在 `lastRest` 之后但没有新的成功动作时，会开启一个更新的非叙述活动段；return 不得再把旧 `lastRest` 当作最新经历。只有没有任何后续活动证据时，旧 rest 才可被投影。
8. 现有 `neko.catMind.debug` 可展示本地 accumulator 和当前 episode preview，供验证归并；关闭调试仍不得改变归并、selector 或 return 行为。

这避免了极长会话的两个错误：不扩大 `recentEvents` 造成无界日志，也不让很早的玩球或一次 hover 污染最后一句。

### 7.3 return 投影：只选最后一个自然段

`buildReturnSummaryDraft()` 应由 accumulator 生成如下 shape，替代旧的 `dominant_state + events[]`：

```json
{
  "duration_seconds": 1260,
  "entry": "auto",
  "final_tier": "cat1",
  "episode": {
    "kind": "rest_after_activity",
    "highlight": "played_yarn"
  }
}
```

`episode` 可省略；存在时只允许：

| `kind` | 产生条件 | `highlight` |
|---|---|---|
| `rest_after_activity` | 当前无新成功 CAT1 动作，且最后一次可信休息前有活动段 | 可选；仅活动段只有一种动作时保留。 |
| `rested` | 当前无新成功 CAT1 动作，且有可信休息但此前无活动段 | 无或保留空。 |
| `activity` | 当前活动段有成功 CAT1 动作 | 可选；互动不改变这个可说类型。 |

投影顺序固定：

1. 当前 `activeChapter` 有成功 CAT1 动作时，优先输出 `activity`，旧 `lastRest` 不再抢占；互动只作为章节边界，不产生额外可说类型。
2. 当前活动段只有后续互动而没有成功动作时，不输出 `episode`；这段更新证据会阻止旧 `lastRest` 被误当作最新经历。
3. 否则有 `lastRest` 时，输出 `rest_after_activity` 或 `rested`。
4. 只有窗口、tier 变化或噪声时不输出 `episode`，继续原问候。
5. 内部 action ID 必须经唯一映射才可形成 transport enum：`cat1_play_yarn → played_yarn`、`cat1_eat_snack → ate_snack`、`cat1_small_move → small_move`、`cat1_social_ping → social_ping`。同一活动段只有一种动作 kind 时才保留 `highlight`；混合动作一律省略它，由 prompt 使用泛化的“活动了一会”而非挑一个冒充主线。

因此：

```text
活动 → CAT2 feedback → CAT3 feedback → drag demotion → return
```

仍投影为 `rest_after_activity`；而：

```text
活动 → 休息 → 新活动 → 休息 → return
```

只保留最后一个“新活动 → 休息”自然段。它压缩的是最近可信篇章，不是整段会话的动作清单。

### 7.4 前端：同一次 return 的原子附件

1. `app-cat-mind.js` 在严格 action-result 处理点更新 accumulator，不在 `recentEvents` 扫描、公开 `observe()` 或桌面桥接处猜测动作成功。
2. `finishCatMindReturn()` 先构造 `episode` summary、清掉猫形态运行态，再派发既有 `neko:cat-mind:return-summary`；仅 Live2D / VRM / MMD return 把它存为可消费 draft，该事件本身不发送 websocket。PNGTuber 只保留 observation / 事件，不保留 draft。
3. 新增 `window.nekoCatMind.consumeReturnSummaryDraft()`：原子 clone 后清除 draft。保留只读 getter 仅供调试，不能承担附件职责。
4. `app-auto-goodbye.js` 的既有 `handleReturn` 是唯一 consumer：构造 `neko:cat-greeting-check` detail 时 best-effort 调用 consume 并附为 `catMemorySummary`。即使短时静默、无 socket 或发送失败，草稿也已属于这一次 return，不能滞留给下一次。
5. 当前脚本顺序下 Cat Mind 的 return listener 先于 `handleReturn` 建 draft；必须用回归测试锁住。读取失败、Cat Mind 未激活或 draft 缺失时，原 return payload 与恢复链不变。
6. `app-websocket.js` 只在原本会发送 `cat_greeting_check` 的条件下透传 `cat_memory_summary`；不建立跨 return 缓存。前端可做 shape 缩减，但后端才是安全边界。

### 7.5 后端：enum allowlist、canonical 值与一次性使用

1. 在 `main_routers/websocket_router.py` 为 `cat_memory_summary` 增加专用 sanitizer。它只接受对象；未知 key、数组、开放文本、次数、时间、坐标、分数、非有限数和错误类型一律丢弃。
2. 允许的 transport 字段只有：
   - `duration_seconds`：非负有限数；
   - `entry`：`manual / auto`；
   - `final_tier`：`cat1 / cat2 / cat3`；
   - 可选 `episode.kind`：`rest_after_activity / rested / activity`；
   - 可选 `episode.highlight`：`played_yarn / ate_snack / small_move / social_ping`。
3. `episode` 必须做组合校验：`rested` 禁止 `highlight`；其余 kind 只可使用上述可选 highlight。未知 kind、错误组合或非对象 episode 一律整体丢弃，不允许部分猜测修复。
4. 现有顶层 `cat_duration_seconds / tier / was_auto` 是本次 return 的 canonical 输入，必须先严格规范化：duration 仅接受有限数并继续限制在 `0–7 天`；tier 只接受 `cat1 / cat2 / cat3`，否则回退为空 tier；`was_auto` 只有布尔 `true` 才代表 auto，字符串如 `"false"` 不能被 Python truthiness 误判。随后覆盖 `cat_duration_seconds → duration_seconds`、`tier → final_tier`、`was_auto: true → entry: auto`、其他值 → `entry: manual`。
5. `episode` 缺失或非法时只丢掉 `episode`，仍按原 cat greeting 继续；它不能成为拒绝 return 或问候的理由。
6. sanitize 后将可选 episode 传给 `main_logic/core/greeting.py:trigger_cat_greeting(...)`。它只活到本次调用结束，不写数据库、长期 memory、角色设定或后续 proactive state。

### 7.6 Prompt：一段经历的回归表达

1. 保持现有 `prompt_ephemeral(instruction)`、completion mode、可见回复持久化和完成生命周期；不要改成 `create_response`、`prime_context`、独立 LLM 调用或 `persist_response=False`。`trigger_cat_greeting()` 不得调用普通问候的 `get_time_of_day_hint()`，避免午饭/深夜等跨功能话题污染 return。
2. 在 `config/prompts/prompts_proactive.py` 保留服务端拥有的 enum → `get_cat_greeting_episode_scene()` 映射；它只接收 sanitizer 后的 enum，绝不 stringify JSON。
3. 有可信 episode 时，最终 instruction 必须把 scene 作为本次“刚才作为猫经历了什么”的唯一事实，而不是可忽略背景。通用 scene wrapper 不按动作另起问候通道：它只插入 `{cat_form_scene}`，tier × 时长 × entry 继续只决定等待、被叫回来和回归的语气/措辞。旧模板中会断言“全程等待 / 打盹 / 熟睡”的猫形态事实不得与有效 scene 同时出现。
   - `rest_after_activity` 必须保持“先活动、后来休息”的真实顺序；
   - `rested` 只能表达已严格完成的休息；
   - `activity` 只能表达当前活动段；
   - 无 episode 或非法 episode 时，必须完整保留既有 tier × 时长 × entry 的等待问候。
4. 等待和被叫回来仍可自然提到，但不能遗漏、弱化或用“全程只有等待 / 什么也没做”替代 scene。一个 scene 只表达这一个自然段；`highlight` 缺失时用泛化表述，不逐项报动作，不报次数、概率、坐标、分数、窗口状态或五维。
5. 提示词目标是 1–2 句、短、口语、符合人设、无思考过程。第一版不新增输出裁剪器，测试验证 instruction 约束而非断言模型永远不会超过两句。
6. scene、scene wrapper 和按 tier × 时长取值的回归语气都必须覆盖当前 prompt locale 归一后的语言键并保留英文 fallback；沿用 `{master}` 模板与反物化护栏，并为空名与花括号格式化补回归。
7. scene 不得把活动或互动解释成“和你一起”“你让我做了什么”，更不得推演用户意图、缺席原因或关系结论；不能责备、审问或说“你让我等太久”。它不新增长期记忆、重试或 LLM 总结调用。
8. `rested` / `rest_after_activity` 只可表达严格完成的休息，不得声称睡了多久、睡得多沉、刚刚醒来；final tier 与时长只决定回归语气，不能伪造经历事实。

### 7.7 模型与桌面边界

1. return 继续由既有网页 renderer 主链处理。NEKO-PC 只维持窗口/跨窗桥接、桌面 observation 与窗口安全；它不保存、发送、解释 `episode`，也不拥有 return summary、selector 或后端调用。
2. Electron / Web 的同一 return 都复用网页前端附件链，桌面端不新增 ACK 或状态。
3. PNGTuber 当前明确排除：它不监听既有 `handleReturn`，因此不发送经历问候，也不保存可被下一次 return 消费的 draft。若产品要求四种 avatar 都有经历问候，必须先以同一既有 `handleReturn` 接入 PNGTuber 并补回归；在此之前不能声称所有 return 已覆盖。

### 7.8 实施顺序

1. 已完成：`returnEpisodeAccumulator` 替换 `getSummaryEventTags()` 的 raw events / `dominant_state` 投影；只在严格 action `done` 和既有用户 interaction observation 处更新它。
2. 已完成：活动段、CAT2 后 CAT3 feedback 延续同一休息段、动作后新活动覆盖旧休息，以及 window/presentation/legacy play 不入账的行为测试。
3. 已完成：`consumeReturnSummaryDraft()`、Cat Mind → `handleReturn` 脚本/事件顺序、单次消费与缺失/失败 fallback 回归。
4. 已完成：websocket router sanitizer、`kind × highlight` 组合校验与顶层 canonical 规范化；只向 `trigger_cat_greeting` 传 request-local episode。
5. 已完成：本地化 episode scene 与通用 scene wrapper；有效 episode 以 `cat_form_scene` 成为猫形态事实，tier × 时长 × entry 仅取回归语气；无/非法 episode 保留旧等待问候。
6. 已验证：silent / socket failure / send failure / voice-takeover / SM guard 仍按原语义生效；不为了让猫娘说经历而强行发问候。

### 7.9 阶段 4 验证

前端与归并：

1. 只有匹配 `source: cat_mind`、action/request/run 三元组和 `done` 才写入 accumulator：CAT1 动作进入 `activeChapter`，CAT2/CAT3 sleep feedback 进入 `lastRest`；伪造 observation、failed/cancelled/interrupted、旧 journey 25% play 都不写入。
2. 超过 `recentEvents` 上限的窗口/hover 噪声不改变 episode；重复同类动作不膨胀，混合动作清除 highlight。
3. `CAT1 activity → CAT2 feedback → CAT3 feedback → tier demotion → return` 保留一个 `rest_after_activity`，且后续 CAT3 feedback 不丢前段活动。
4. `activity → rest → new activity → rest → return` 只保留最后一个自然段；休息后新活动但未再休息时优先输出新活动。休息后的 interaction-only 会话不得复用旧 rest。
5. interaction-only、tier-only、window-only 和 presentation-only 会话不产生 episode；互动只形成章节边界，prompt 不得表达共同完成或因果。
6. 休息后的 interaction-only 会暂时抑制旧 rest；首次或后续 interaction 后若又收到严格 sleep feedback，则建立新的 `rested`，不复用旧活动前置关系。
7. Cat Mind 先建 draft，`handleReturn` 单次 consume；短时静默、无 socket、发送失败或重复 return 不会缓存/挪用上一轮 episode。
8. debug 可看见活动段、最近休息段与最终 preview；关闭 debug 后三者的归并结果相同。
9. 单独严格 sleep feedback 投影为 `rested`；有或没有明确互动的单一 CAT1 done 都投影为 `activity`。
10. 重复同类动作仍保留该 highlight；新章节混合动作必须重新计算并清掉旧章节 highlight。
11. `rested + highlight`、未知 `kind/highlight` 组合、`was_auto: "false"`、非 enum tier、非有限 duration 都由 sanitizer 覆盖。

后端与 prompt：

1. 非对象、开放文本、未知 `kind/highlight`、数组、错误类型和额外字段被 sanitizer 丢弃；非法 episode 不妨碍原问候。
2. 顶层 canonical 值覆盖 summary：`cat_duration_seconds → duration_seconds`、`tier → final_tier`、`was_auto → entry`。
3. 最终 instruction 不含原始 JSON、DOM/坐标、动作 ID、次数、五维、未知文本或未展开占位符。
4. 每个 locale 的 episode scene 与 scene wrapper 都可格式化；`rest_after_activity` 保持真实前后顺序，混合动作不假装某一项是主线。有效 scene 的 instruction 必须自然包含该经历，且不得残留与它冲突的旧“全程等待 / 打盹 / 熟睡”猫形态事实；无/非法 episode 的 instruction 与旧问候精确一致。
5. 继续通过反物化、短时静默、现有 cat greeting 与可见问候持久化回归；为 router sanitizer 和 `GreetingMixin.trigger_cat_greeting` 参数传递补行为单测，而非只做静态字符串断言。

运行时：

1. 当前生产阈值为 180 秒：猫形态 < 180 秒 return 仍静默；>= 180 秒时有 episode 必须自然表达经历（可同时带等待语境），无 episode 保持原问候。
2. voice / takeover / busy / user preempt / session 启动失败时继续静默，模型恢复和猫娘 return 都不被 episode 阻塞。
3. Web 与 Electron 已支持的 return 不回归；PNGTuber 按 7.7 的明确产品决策验收。

### 7.10 设计依据

1. 事件、反思与规划应分层，避免把日志直接当作自由叙事来源；参考 [Generative Agents](https://arxiv.org/abs/2304.03442)。
2. 固定长度下应优先少量显著、连贯事实而非堆叠流水账；参考 [Chain of Density](https://arxiv.org/abs/2309.04269)。
3. 摘要必须可回溯到来源事实，避免合理但不真实的补写；参考 [Maynez et al., On Faithfulness and Factuality in Abstractive Summarization](https://arxiv.org/abs/2005.00661)。
4. schema 只保证形状，不保证语义，仍需 enum allowlist 与 canonical 覆盖；参考 [OpenAI Structured Outputs](https://openai.com/index/introducing-structured-outputs-in-the-api/)。
5. 这里的 episode 是单次短期上下文，不是跨会话长期记忆；参考 [LangChain Memory 概念说明](https://docs.langchain.com/oss/python/concepts/memory)。

### 7.11 v0.1 收口状态

1. 阶段 4 的代码边界与自动回归已收口：严格 `done` 归并、一次性 draft 附件、失败不遗留、router allowlist、request-local prompt、普通时段/餐食提示隔离，以及前后端一致的 180 秒静默门槛均有回归覆盖。
2. `test_cat_idle_state_machine_static.py` 的 Node harness 覆盖归并、listener 顺序、单次消费、短时静默、无 socket、发送失败、Cat Mind 缺失与 PNGTuber 不存 draft；router、prompt 与主动问候 guard 另有 Python 单测覆盖。
3. 这不等同于已完成实机文案验收：发布前仍需重启实际后端，在 Web / Electron 各观察一次“有 episode 的 >= 180 秒 return”与一次“< 180 秒静默 return”，并从日志确认 `summary_object → episode → trigger_cat_greeting` 闭环。
4. 下列事项明确不属于阶段 4 v0.1：PNGTuber 经历问候、NEKO-PC 保存或发送 episode、为 WebSocket 重连产生的独立普通问候做优先级重排、LLM 输出裁剪器，以及任何长期记忆。

## 8. 调试策略

最终架构不保留 Cat Mind、selector 或单动作的运行时停用开关。Provider / runner 是最终架构的一部分；旧触发 wrapper 不是。处理实现错误应精确修改对应入口，不以多轨回退掩盖问题。

`neko.catMind.debug` 是唯一保留开关：关闭时只隐藏调试显示，不改变调度、observation 或动作执行。

## 9. 测试矩阵

### 9.1 NEKO 静态/单元测试

优先补或扩展：

1. `tests/unit/test_app_auto_goodbye_phase1.py`
2. `tests/unit/test_auto_goodbye_goodbye_return_contract.py`
3. `tests/unit/test_avatar_return_button_cat1_static.py`
4. `tests/unit/test_avatar_return_button_idle_tiers_static.py`
5. `tests/unit/test_react_chat_idle_dock_static.py`
6. 新增 `tests/unit/test_cat_idle_state_machine_static.py`

覆盖点：

1. 发布时 Cat Mind 统一接管第一批动作；不保留运行时动作或 selector 开关，也不恢复旧行为。
2. observation event 名称和 payload 稳定。
3. hard gate 存在且优先。
4. 禁止动作不会靠低分保留。
5. 气泡点击与吃东西解绑后有测试覆盖。
6. 相同桌面窗口状态/几何不论来自 `poll`、原生 IPC、BroadcastChannel 还是本地 UI，都不重复写入五维、recent events 或 selector 判断机会；状态或窗口几何变化才生成 observation。`idle-dock-enter` 在一次落位中仅记录一次。
7. 阶段 4：supported 网页 return 的 summary 只能单次消费；无 socket / 发送失败 / 短时静默均不遗留到下一次 return，PNGTuber 不保存 draft。

### 9.2 NEKO 运行时验证

至少覆盖：

1. 手动 goodbye -> CAT1 -> return。
2. 自动 idle -> CAT1 -> CAT2 -> CAT3。
3. CAT1 拖拽、快速甩动、edge peek。
4. CAT1 hover / click 态 GIF observation。
5. CAT1 气泡点击。
6. CAT1 play yarn 隐藏/恢复 chat yarn。
7. CAT2/CAT3 sleep feedback。
8. chat minimized / compact / idle-dock。
9. return 后模型恢复和问候不阻塞。

### 9.3 NEKO-PC 契约测试

优先跑：

1. `node --test test/react-chat-idle-dock-contract.test.js`
2. `node --test test/react-chat-compact-surface-drag-contract.test.js`
3. `node --test test/pet-hidden-return-ball-contract.test.js`
4. `node --test test/wayland-input-region-backend.test.js`
5. return ball drag、compact ball、activity signal 相关测试。

### 9.4 桌面端人工/自动观察

至少覆盖：

1. Windows return ball 原生拖拽。
2. Linux X11 / Wayland 或 niri input region。
3. Electron chat minimized ball idle-dock。
4. compact surface 拖动期间状态机保持安静。
5. 多窗口层级：toast/settings/full chat 不被猫动作顶掉。

## 10. 第一版完成定义

第一版完成时，应满足：

1. 代码中有单一 Cat Mind 来源，不在 NEKO-PC 复制状态。
2. 进入猫形态后能看到五维状态和 recent events。
3. 现有用户/窗口触发流程只作为 observation，不被 selector 调度。
4. 第一批 provider 都可 dry-run、可执行、可回报。
5. selector 可在统一封口后从完整动作池中产生候选。
6. 每个动作只有一个最终调度入口；实施期旧入口不作为长期双轨保留。
7. 阶段 4 已把一条有界 episode 单次接入既有问候链；后端只消费 enum，有 episode 时以它作为猫形态事实场景、无 episode 时保留旧问候，临时 scene 不进入长期 memory，PNGTuber 仍明确排除。
8. NEKO 和 NEKO-PC 现有拖拽、idle-dock、return、goodbye 关键链路不回归。
