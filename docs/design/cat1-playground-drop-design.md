# CAT1 Playground Drop

> **文档性质：current implementation record + external integration boundary。** 本仓库已经实现 CAT1 playground 的网页侧状态、物理和聊天镜像；桌面原生窗口移动/命中属于 N.E.K.O-PC，未在本仓库验证时不能当作当前事实。

本文维护普通与重力共用行为的当前开放范围、Playground 生命周期与外部集成合同。产品表现见[猫形态功能说明](/design/cat-idle-states-feature)，评分、互动反馈和结果协议见 [Cat Mind 规则](/design/cat-idle-state-machine-rules)；不在本文件复制数值或增加另一套调度。

当前重力会话覆盖三个阶段的默认/拖拽表现、hover、自然声音、普通气泡/ZZZ、各阶段既有原地动作，以及满足条件的 CAT1 单猫小移动、走向球、玩球和猫球共同小移动。compact 上缘/跟随/mirror、CAT2/CAT3 idle-dock 表现、屏幕半隐藏及桌面窗口演出继续关闭；普通模式保持原有交互和阶段限制。

## 当前行为

CAT1 问号方块可进入长生命周期的 playground：猫和关联物体落到底部，之后保持可拖拽/可点击，直到用户恢复角色或通过明确的退出入口结束。它不是固定十秒的临时动画。

实现入口：

- `static/avatar/avatar-ui-buttons/idle-playground.js`：playground 状态、物理和退出；
- `static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js`：拖拽与点击区分；
- `static/avatar/avatar-ui-buttons/idle-assets-and-question.js`：问号方块与素材；
- `static/app/app-react-chat-window/minimize-and-idle-dock.js`：Compact/Minimized 镜像；
- `tests/unit/test_avatar_return_button_cat1_static.py` 与 `tests/unit/test_react_chat_idle_dock_static.py`：静态合同。

## 生命周期合同

```text
idle cat1
  -> enter playground
  -> falling
  -> settled / draggable
  -> explicit exit or restore avatar
  -> cleanup and restore previous UI
```

- 进入时只允许一个 active playground session；重复入口保留原 body、token、速度和拖拽状态。物理对象创建失败时释放会话，并沿共用阶段表现入口恢复普通猫形态、声音资格与原有 journey 条件。
- 物理循环使用 session/token 守卫，旧 frame 不得更新已退出的对象。
- 基础交互按当前猫的稳定条件准入：落稳可 hover、自然声音与气泡/ZZZ，其他物件仍动不阻塞；pending 保留原回应，真实拖动/失稳取消，恢复资格不补播。未适配动作和普通位置入口继续禁止。气泡 pointerdown/click 不认领猫 body，不获取 capture；桌面只扩对应活动气泡的有限命中区域。
- 原地动作已复用既有 runner：CAT1 吃零食、伸懒腰/聊天哈气，以及三个阶段现有视觉动作。重力中等图片实际加载/入场后开始原周期；进食保留动画与声音双完成，伸懒腰保留末帧停留，OIIA 保留三轮及原声音偏移。原随机组、低互动 sad 与气泡后续沿原入口和节奏，不新增动作候选。动作换图/恢复同步 hull 和脚底，不恢复普通 journey、compact 或窗口位置行为；拖动、失稳、切阶段和退出沿原取消链结束，迟到回调不能复活。具体准入、周期与归还规则见下文“共用行为与动作周期”。
- CAT1 自主小移动复用原 runner，安全支撑上水平移动；无合适配对球时保持单猫，近球且双方可控时共同移动。两体目标先做只读约束，合法后经原 setter 同批提交，body、窗口状态和 DOM 同步；拒绝不绕过 owner。持按冻结进度，真实拖动、失稳、球换代/激活或外部位移取消，只结算猫已完成的自主路程一次。
- 走向球和玩球复用原 journey 与 runner，只接受当前会话同一代真实物理球、相同支撑高度和水平可达路径；球物理目前落稳在工作区地面，高处猫不跨空隙。保留原延迟、到达后 25% 玩球/其余伸懒腰选择。玩球等实际首帧开始原周期，临时隐藏/暂停同一球，取消或结束归还，不移除 body、不展开聊天；catOnly 无球不造球。旧 dock 的入口、异步完成及恢复必须让出 Playground，具体接管合同见下文桌面边界；这项保护不开放 dock 表现。
- 拖拽期间暂停对应物体的重力；释放后从当前位置继续。
- 猫手势共用普通模式轴向 `>5px` 门槛、pending/active、素材缓存、拖拽声音、rapid 和清理；只为猫发布带稳定 `activityId` 的既有手动移动事实。有效释放沿用三阶段降级，取消不成功降级，真实路程与互动由 Cat Mind 结算一次；毛线球、问号方块保留原移动门槛和物件语义。
- 猫手势事实标明 `source: cat1-playground`，旧窗口重力监听与普通桌面桥接不重复处理位置；松手后的普通边缘探头、compact rearm 和普通 journey 恢复副作用不接入。模式退出和规范容器清理会取消未结束的手势，阻止旧 rapid timer 回写或补播。
- 落地后的毛线球与桌面毛线球镜像使用独立的水平阻尼：以 60Hz 为基准每帧保留 `0.90`，并按真实 `dt` 归一化。该参数不直接修改猫、问号方块、空中抛掷、垂直弹跳或碰撞公式；毛线球更早减速后，与其他物体后续发生碰撞的次数和轨迹可以随之变化。
- 旋转由通用角度、角速度、抛掷和渲染核心承载，形状行为通过每个 body 独立的 `rotationBehaviors` 组合：圆形毛线球只启用按可见半径和实际水平位移计算的 `circularRolling`，问号方块只启用落地归正到固定角度步长的 `steppedRest`。形状策略不能按 body id 隐式生效，也不能共享可变状态。
- Playground 使用独立的无尾、正方形透明毛线球素材，并且只旋转图片层；DOM 图标的素材所有权仍在聊天模块，Playground 会话通过 `yarnEnabled` 声明专用皮肤和桌面毛线球桥接状态，物理模块只持有本轮图片的旋转样式。切换到只有猫的 Visit Cat World 时必须重新发布 `yarnEnabled: false`：聊天模块恢复原素材，N.E.K.O-PC 解除毛线球隐藏和交互锁，但保留入口最小化的恢复所有权。桌面模式只旋转 renderer 镜像，不向 N.E.K.O-PC 增加第二套旋转或物理逻辑。普通最小化聊天、Visit Cat World 和其他毛线球表现继续使用原素材。
- 毛线球落地或被水平拖动时按 `Δθ = Δx / r` 滚动；离地后由通用抛掷角速度和角阻尼继续自由旋转。地面、墙面和滚动半径使用素材实际可见圆球边界；body-body 仍使用改造前的 AABB inset，滚动不改变线速度、既有阻尼、质量或弹性。
- 碰撞形变由每个 body 的 `collisionDeformationProfile` 显式启用，默认值为 `null`，未配置的组件绝不形变。`strength` 在 `0..1` 内独立缩放该 body 的形变幅度；配置在创建 body 时复制并冻结，运行状态按各自渲染元素隔离。当前猫显式使用 `strength: 1` 保持既有表现，网页内毛线球和桌面毛线球镜像统一使用 `strength: 0.5`，问号方块默认不启用。
- 墙面、地面、天花板和 body-body 碰撞都只向通用形变入口报告接触方向与速度变化；入口不能判断组件 id。两个 body 首次形成接触时，双方分别收到相反接触方向并按各自 profile 决定是否形变；持续支撑接触不重复报告新的撞击，分离后再次接触才产生下一次撞击。位置层、形变层和旋转图片层分离，形变不得覆盖毛线球滚动或问号方块归正；退出、移除单个 body 和减少动态效果都只清理对应渲染目标。形变到期清理必须独立于物理 RAF，即使组件在碰撞帧后立即落稳也不能残留缩放样式。
- click 与 drag 通过移动阈值/状态区分，不能一次手势触发两条退出路径。
- 毛线球短点击与拖拽分开：短点击只发出恢复聊天框的语义事件并移除毛线球 body；猫 body 继续属于当前 Playground，拖拽/投掷仍走物理释放路径。
- 猫的手动拖动由 Playground 独占，起点、经过路径、当前窗口边界均不限制拖动；松手时仅以最终位置决定是否将猫交给某个桌面窗口 gravity。窗口内释放后，抛掷受该窗口边界约束；窗口外释放后，后续抛掷不会在飞行途中跨入窗口。场景更新、焦点变化或猫恰好完全落在窗口内都不能自动认领窗口。
- 退出时统一清理事件、pointer capture、audio、timer、animation frame 和镜像状态。规范容器移除与 manager cleanup 同时取消 pending 入口、清理问号 clone 并释放会话；旧帧不得复活对象。
- 关闭重力设置后，原本活动的会话恢复当前阶段的普通表现，沿用原有图片过渡周期；普通猫不因此重播。页面卸载只清理，不重新启动表现或声音。
- Compact Chat 的猫只是同一业务状态的显示/位置镜像，不是第二个 playground owner。
- 缺失素材或 reduced motion 时可直接落到稳定位置，但必须保留退出能力。

## 共用行为与动作周期

### 准入、表现与输入所有权

普通和重力模式复用同一阶段控制器、资源选择、动作 runner、Cat Mind 候选及结果协议。行为能否执行由现有 provider 和 runner 读取当前事实决定；不按模式复制动作池、冷却或恢复流程。Playground 的能力策略是单一来源，`disabledCapabilities` 从该策略派生；无参生命周期查询继续保护输入与未适配入口，未知能力保持关闭，不能把“猫落稳”解释成整个会话已经空闲。素材与声音依赖仍由资源表的 `actionDependencies` 管理，运行条件由 provider/runner 检查，不在资源表另维护模式准入。

基础交互与原地动作只检查当前猫的连接、可见性、支撑、速度、碰撞形变、手势和过渡，不要求其他物件停止运动。provider 查询只读，启动、首帧和异步续步重新检查相同事实。失稳时按原取消链结束，落稳只恢复可调度资格，不创建请求、写冷却或立即补播。自然声音在物理帧末、手势终态、阶段/音频开关变化及有效模式退出后同步当前阶段资格；真实拖动期间不能被这些同步重新启用，return 或卸载后不能复活。

Playground 始终独占位置、pointer capture 和物理释放；普通 DOM、VRM 及原生拖拽不会因开放动作而重新接管。猫的真实拖动采用 `abs(dx) > 5 || abs(dy) > 5`，斜向 `(4, 4)` 和恰好 `5px` 都不激活；毛线球、问号方块仍按原距离门槛处理。每次猫手势只有一个稳定 `activityId` 和一次终态，同一手势的 rapid 反馈至多结算一次；仅按下保留已有回应，真实拖动才打断，取消不触发成功降级，物理释放与窗口准入各执行一次。松手后的普通边缘探头、journey、compact rearm 和旧桌面位置副作用继续关闭。

阶段切换与换图复用共用表现入口，保留物理会话、速度和持有中的指针；脚底及可见 hull 按新素材同步。物理只在落地/离地事实改变时通知表现，不能逐帧覆盖合法动作或 hover。换图后再次核对支撑与续帧条件，不能用旧素材的落稳判断停帧。关闭重力设置后恢复当前阶段的普通表现；问号退出先恢复起点并释放物理，再恢复普通猫形象。

气泡从 pointerdown 起独占该次命中，不认领猫 body 或 capture；按住期间气泡隐藏也不能让松手变成点击猫。模式退出后保留该次指针到终态，由既有误点击抑制收尾，下一次真实点击仍可立即使用。桌面仅增加活动气泡自身的有限输入区域，隐藏、popping 或不可交互时不占区域，不扩整窗。

### 原地动作

| 内容 | 共用周期与终态 |
| --- | --- |
| CAT1 进食 | 保留原素材选择；实际图片加载后开始原动画与声音，二者均完成才结束，加载等待不计播放时长 |
| CAT1 伸懒腰/聊天哈气 | 保留原素材、入场及末帧停留；重力中实际图片入场后播放，普通模式原时序不变 |
| 三阶段视觉动作 | 实际首帧/入场提交后才报告 started 并按原时长结束；CAT2/CAT3 back 的自然降级仍交既有阶段控制器 |
| OIIA | 作为一个整体播放三轮，每轮声音保留原 `510ms` 偏移，沿同一首帧时钟运行 |
| 气泡后续动作 | 先按原 `540ms` pop/淡出收尾，再检查进食、抓鼠或猫薄荷后续条件；两种猫薄荷动作仍各占一半 |

随机组、低互动 sad、本地聊天和显式 presentation API 保留原入口、阶段限制与互斥，不因开放视觉 API 把全部动作加入随机池。仅持按可保留已有原地动作，新动作仍拒绝；允许已有动作续播的同源入场过渡，不能给新动作放行。减少动态效果或同源图片跳过渐变时，也必须等主图实际加载成功；赋值 `src` 或过渡回调不能冒充首帧成功。

真实拖动、失稳、切阶段、return 或退出沿原取消链清理加载监听、预加载、入场过渡、timer 和声音，即使不恢复图片也须取消该动作持有的异步提交。同步结束回调若开始新 run，旧终态只清自己；迟到回调不得覆盖新状态。最终退出中 `released` 已置位但会话仍在收尾时，入口继续拒绝新动作，清理完成后才恢复普通资格。动作结束只归还表现，不释放整个 Playground 或恢复聊天窗。

### 自主位移、走球与玩球

自主位移仅限既有 CAT1 行为，复用原距离、速度、缓动、延迟和冷却。目标查询只读支撑、窗口/屏幕边界、露出平台、障碍与物件占用，不创建 journey、抽随机、改 hull、写坐标或排帧。位置入口交给当前 owner，经 body setter 同步物理和 DOM；owner 拒绝时不回退直接写 DOM。窗口原点与 owner 边界尚未同步时拒绝，不能混用新旧坐标。

单猫小移动在行走首帧成功并同步几何后，按当前 body 重新锚定原已选方向和目标，再请求真实 started、启动 RAF；失败不写冷却，失效请求不在松手后补执行。目标被夹取、支撑失效或外部位移时取消，保留已经走过的自主部分，不追旧起点、不按计划距离报完成。

走球只接受当前会话同一代真实毛线球：猫和球须落稳、球可控制、处于相同支撑高度且整段水平可达。当前球落稳于工作区地面，高处猫不能跨空隙走向它。保留原启动延迟、速度、跟踪及到达后 `25%` 玩球、其余伸懒腰的局部选择；局部选择只做一次，真实完成才给完成反馈。持按冻结，真实拖动、目标消失/换代、失稳、切阶段或退出取消。临时不可达随条件恢复重新准入；资源失败或主动取消后不因物理帧立即重试。行走首帧加载中可沿原 hover 周期接管，清掉旧加载等待，回应结束后在同一 approach 重新建立首帧等待，旧回调不能清新等待。

玩球由原 runner 管理素材、声音和周期，仅临时隐藏并暂停同一物理球的物理、碰撞和输入，不移除 body、不展开聊天、不取得普通/native 球的隐藏所有权。准备期每次同步清理或用户回调后复核原 run、session 和球代际；实际首帧后才 started，启动确认失败即取消。完成、取消及最终 released 收尾都归还本次写过的样式，不能复活旧球或把普通聊天球留成隐藏。仅持按时延后换猫 hull，松手同步最新几何；真实拖动照常更新。

近球且双方安全可控时，小移动沿原 provider/runner 生成 pair；没有合适球时保持 solo。两体目标同时受猫的支撑/窗口边界、球的工作区及其他 body 约束，保持各自高度和相对位移。两体逻辑坐标提交后、首个可能同步外发的球 setter 前就记录猫的实际路程，球同帧被移除也只结算已提交的自主部分；回调造成的被动位移不追加。

持按猫或参与移动的球时，立即同步原暂停字段并冻结进度；无 RAF 的等待也不计主动时长。加载、hover 和持按从实际自主活动中扣除。真实拖动、碰撞/被动位移、失稳、球激活/换代、切阶段或退出取消，只结算已经完成的自主路程一次；恢复当前合法阶段/拖拽/空中形象，不启动普通 compact、mirror 或窗口演出。

### Visit catOnly

`catOnly` 只改变物件组成，本机猫仍使用相同生命周期、准入与 runner；没有球时不创建共享物件，对方猫不在接收端重复执行 selector。远端只展示既有槽位映射与 allowlist 可同步的结果，未映射素材走原回退姿态，不承诺与本机全量同图，也不增加任意素材地址或远程行为权限。

## 桌面边界

本仓库可以产生 compact layout、playground 镜像和交互 relay。N.E.K.O-PC 如何移动透明窗口、同步 native bounds、处理跨显示器坐标，需要在外部仓库和真实 Electron 环境验证。网页测试通过不等于桌面窗口行为通过。桌面 Pet 的 minimized 目标几何只接受 N.E.K.O-PC 经 preload 回送的有效 native/self-ball 状态；页面间 BroadcastChannel 的 Web shell 几何只服务 Web，不得在桌面端形成第二个尺寸与生命周期权威。

跨仓库职责固定为两段：N.E.K.O 只持有 Playground body、拖拽手势和窗口 gravity owner；N.E.K.O-PC 只持有原生窗口枚举、场景快照、Electron 窗口锁和对话框恢复转发。任何一方都不能再创建第二个物理循环、第二个窗口准入 owner 或第二套毛线球恢复路径。

旧聊天 dock 的保护须同时覆盖 Web 和 Electron。Web 按本 renderer 可见的入口 pending、活动及 released 收尾清理旧观察器/fallback。隔离的聊天 renderer 以收到或可读取的 main 接管事实为准：既有 lock 消息中的 `sessionActive` 表示会话所有权，与毛线球显示/交互的 `active` 分开，因此 catOnly 也会阻止旧 dock。监听注册前或 renderer 重载时可通过同步 getter 读取当前事实，ready 同步按会话补发；跨窗口不转发入口 pending。

收到或读到接管事实后，Electron 旧进入、落位、退出代际及重试必须失效；每个 bridge 异步返回处复核当前 owner，旧 dock 不得恢复旧 bounds、展开聊天、继续落位或重试。main 在 Playground 会话已进入时拒绝旧 dock 的原生落位提交。Pet 端停止发送旧位置状态不能替代这两端检查。接管只清旧 dock 自己的状态、观察器、重试及位置帧，保留 Playground 当前 surface 事务和入口恢复所有权；退出 Playground 不自动续跑旧 dock。普通模式的停靠和恢复继续按原有效事件及代际流程执行。跨窗口保证需要提供会话事实及提交保护的配套桌面版本，不能仅凭网页更新宣称旧客户端已经覆盖。

```text
renderer pointer drag
  -> Playground body position (unconstrained by desktop windows)
  -> pointer release position / release velocity
  -> explicit gravity admission (at release only)
  -> NekoDesktopWindowSensingContext scene
  -> shared gravity owner and window borders

yarn short click
  -> activationId
  -> local web event or Electron IPC
  -> compact chat restore
  -> RESTORE_COMPLETE / bounded timeout
  -> dispose this yarn body / clear activation transaction
  -> keep Playground session and desktop bridge lock
  -> next minimized fact creates a fresh yarn body
```

窗口 scene 的 `pending/busy` 才表示“原生枚举尚未完成”，不能被 renderer 当作空桌面；此时保留松手时的最终位置，等这次枚举给出结果。真实 `unavailable/no-window` 表示当前没有可容纳窗口：释放时的失败请求必须清掉，不能在未来窗口出现时自动重放。手动拖动可跨入、跨出窗口且不发生碰撞或准入；抛掷不在释放后重新寻找窗口。已经在窗口内释放的 body 才按该窗口边界碰撞。

Playground 会话与毛线球激活事务是两个生命周期。同一 `activationId` 的 BroadcastChannel 与 Electron IPC 重复送达只能恢复一次；确认成功只移除这一代毛线球 body 并清理激活事务，猫、问号方块和 Electron desktop bridge lock 继续属于当前 Playground。聊天再次最小化后，由权威 surface/minimized 状态创建新一代毛线球，旋转、形变和拖拽状态从零开始；上一代迟到的 ACK 不能删除新一代。renderer 重载、转发失败或有界超时只回滚当次激活，不能释放整个会话或让原生球与 Playground 镜像同时可交互。CAT1/CAT2/CAT3 之间切换保留会话、物理对象和会话锁，默认形象跟随当前阶段。点击猫、点击问号方块、退出猫形态、来源销毁或页面卸载等最终出口释放会话锁。

进入前已经最小化的聊天在 Playground 最终退出后保持最小化；若最小化由本次 Playground 入口触发，只要该所有权尚未被一次实际展开消费，入口取消、创建失败或任何最终出口都恢复进入前 surface。待入口切换为 Visit 时直接转交所有权，不先广播退出回滚。毛线球激活成功或 Visit 中普通聊天球展开都会消费所有权；用户之后再次手动最小化属于新选择，后续退出不得反向展开。猫点击仍走原模型 return 语义，聊天是否展开仅取决于恢复所有权。重力皮肤退回普通 Web 毛线球时按普通球位置规则夹入视口。每个 body 自带可重复调用的 disposer，单独销毁毛线球不得清理猫或问号方块，也不得向会话级数组累积已失效的闭包。

## 验证

自动回归必须证明整个生命周期，而不只是入口可调用：

- 普通/重力两模式、三个阶段及 Visit catOnly：阶段切换不丢会话、速度、owner 或持按；各阶段使用自己的合法资源，普通流程保持原语义。
- 移除支撑、资源或物件控制能力时，对应动作拒绝且不写 started、冷却或完成经历，其他已适配动作仍可执行；恢复资格不补播。猫落稳而其他物件仍运动时，不得被整场 RAF 封禁。
- pending、真拖动、rapid、end/cancel 和气泡按住后隐藏/退出：输入不串用，重复终态不重复降级、记互动或结算负荷，下一次真实手势可正常开始。
- 慢图、图片失败、音频关闭、同步结果回调重入、切阶段、return、卸载和 released 收尾：旧回调不写新状态，不留下声音、隐藏球、输入锁或幽灵动作。
- 单猫/走球/pair：只读查询无副作用，owner 拒绝不绕过，首帧前不计路程，加载/hover/无 RAF 持按不计负荷；同帧移除球只结算已提交的猫自主路程，下一物理帧不跳回旧坐标。
- Web observer/fallback 和 Electron 已排队的 bounds/collapse/落位/退出重试，在 Playground 接管及退出后均不能复活；catOnly 与有球会话都覆盖，同时保留普通 dock 的取消回滚、位置保留和原重试。
- 未适配的 compact/dock、屏幕边缘与桌面窗口演出保持关闭；catOnly 不生成物件，未映射的远端素材沿合法槽位回退。

```bash
uv run pytest tests/unit/test_avatar_return_button_idle_tiers_static.py tests/unit/test_avatar_return_button_cat1_static.py tests/unit/test_react_chat_idle_dock_static.py -q
node --test tests/frontend/desktop_window_gravity.test.cjs tests/frontend/desktop_window_sensing_cat1.test.cjs
node --test tests/frontend/cat_playground_drag_semantics.test.cjs tests/frontend/cat_playground_legacy_consumers.test.cjs tests/frontend/cat_playground_basic_interactions.test.cjs
node --test tests/frontend/cat_playground_stationary_actions.test.cjs tests/frontend/cat_eat_action_variants.test.cjs tests/frontend/cat1_idle_random_scheduler.test.cjs tests/frontend/cat_mind_scheduler_recheck.test.cjs
node --test tests/frontend/cat_playground_small_move.test.cjs tests/frontend/cat_small_move_provider_purity.test.cjs tests/frontend/cat_physical_activity_facts.test.cjs
node --test tests/frontend/cat_playground_yarn_movement.test.cjs tests/frontend/cat_playground_yarn_action.test.cjs tests/frontend/step6_dock_boundary.test.cjs
uv run pytest -c /dev/null --confcutdir=tests/frontend tests/frontend/test_cat_playground_basic_interactions.py tests/frontend/test_cat_playground_stationary_actions.py tests/frontend/test_cat_playground_small_move.py tests/frontend/test_cat_playground_yarn_movement.py tests/frontend/test_cat_playground_yarn_action.py --browser chromium -q
node --test ../N.E.K.O.-PC/test/desktop-window-sensing-active.test.js ../N.E.K.O.-PC/test/idle-cat-question-mark-layer-contract.test.js
node --test ../N.E.K.O.-PC/test/idle-cat-playground-chat-restore.test.js ../N.E.K.O.-PC/test/idle-cat-playground-bubble-input.test.js ../N.E.K.O.-PC/test/react-chat-idle-dock-contract.test.js ../N.E.K.O.-PC/test/desktop-compact-layout-contract.test.js
```

网页 CI 入口为 `.github/workflows/unit-test-shards.yml` 的 `frontend-node` 与 `cat-basic-interactions-browser`：前者显式运行相关 Node 行为回归，后者使用锁定依赖运行以上五份真实 Chromium 套件，不依赖后端初始化。桌面侧合同在 N.E.K.O-PC 验证；本地通过不代表远端 CI 已执行，更不能代替原生验收。

手工测试应覆盖高 DPI、窗口缩放、屏幕边缘、快速进入/退出、拖拽后点击、聊天 surface 切换和模型恢复。

## 剩余工作与验证边界

未来小游戏必须复用这一生命周期和清理合同；不要在新的小游戏里复制另一套重力循环或桌面窗口状态机。当前自动测试覆盖状态和 IPC 合同；真实 Electron 仍需在 macOS/Windows、DPI 缩放、跨显示器、窗口关闭/重载和 renderer 重启条件下手工复核，自动测试不能替代原生窗口视觉验收。
