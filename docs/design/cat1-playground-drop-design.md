# CAT1 Playground Drop

> **文档性质：current implementation record + external integration boundary。** 本仓库已经实现 CAT1 playground 的网页侧状态、物理和聊天镜像；桌面原生窗口移动/命中属于 N.E.K.O-PC，未在本仓库验证时不能当作当前事实。

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

- 进入时只允许一个 active playground session；重复入口幂等。
- 物理循环使用 session/token 守卫，旧 frame 不得更新已退出的对象。
- 拖拽期间暂停对应物体的重力；释放后从当前位置继续。
- 落地后的毛线球与桌面毛线球镜像使用独立的水平阻尼：以 60Hz 为基准每帧保留 `0.90`，并按真实 `dt` 归一化。该参数不直接修改猫、问号方块、空中抛掷、垂直弹跳或碰撞公式；毛线球更早减速后，与其他物体后续发生碰撞的次数和轨迹可以随之变化。
- 旋转由通用角度、角速度、抛掷和渲染核心承载，形状行为通过每个 body 独立的 `rotationBehaviors` 组合：圆形毛线球只启用按可见半径和实际水平位移计算的 `circularRolling`，问号方块只启用落地归正到固定角度步长的 `steppedRest`。形状策略不能按 body id 隐式生效，也不能共享可变状态。
- Playground 使用独立的无尾、正方形透明毛线球素材，并且只旋转图片层；DOM 图标的素材所有权仍在聊天模块，Playground 会话通过 `yarnEnabled` 声明专用皮肤和桌面毛线球桥接状态，物理模块只持有本轮图片的旋转样式。切换到只有猫的 Visit Cat World 时必须重新发布 `yarnEnabled: false`：聊天模块恢复原素材，N.E.K.O-PC 解除毛线球隐藏和交互锁，但保留入口最小化的恢复所有权。桌面模式只旋转 renderer 镜像，不向 N.E.K.O-PC 增加第二套旋转或物理逻辑。普通最小化聊天、Visit Cat World 和其他毛线球表现继续使用原素材。
- 毛线球落地或被水平拖动时按 `Δθ = Δx / r` 滚动；离地后由通用抛掷角速度和角阻尼继续自由旋转。地面、墙面和滚动半径使用素材实际可见圆球边界；body-body 仍使用改造前的 AABB inset，滚动不改变线速度、既有阻尼、质量或弹性。
- 碰撞形变由每个 body 的 `collisionDeformationProfile` 显式启用，默认值为 `null`，未配置的组件绝不形变。`strength` 在 `0..1` 内独立缩放该 body 的形变幅度；配置在创建 body 时复制并冻结，运行状态按各自渲染元素隔离。当前猫显式使用 `strength: 1` 保持既有表现，网页内毛线球和桌面毛线球镜像统一使用 `strength: 0.5`，问号方块默认不启用。
- 墙面、地面、天花板和 body-body 碰撞都只向通用形变入口报告接触方向与速度变化；入口不能判断组件 id。两个 body 首次形成接触时，双方分别收到相反接触方向并按各自 profile 决定是否形变；持续支撑接触不重复报告新的撞击，分离后再次接触才产生下一次撞击。位置层、形变层和旋转图片层分离，形变不得覆盖毛线球滚动或问号方块归正；退出、移除单个 body 和减少动态效果都只清理对应渲染目标。形变到期清理必须独立于物理 RAF，即使组件在碰撞帧后立即落稳也不能残留缩放样式。
- click 与 drag 通过移动阈值/状态区分，不能一次手势触发两条退出路径。
- 毛线球短点击与拖拽分开：短点击只发出恢复聊天框的语义事件并移除毛线球 body；猫 body 继续属于当前 Playground，拖拽/投掷仍走物理释放路径。
- 猫的手动拖动由 Playground 独占，起点、经过路径、当前窗口边界均不限制拖动；松手时仅以最终位置决定是否将猫交给某个桌面窗口 gravity。窗口内释放后，抛掷受该窗口边界约束；窗口外释放后，后续抛掷不会在飞行途中跨入窗口。场景更新、焦点变化或猫恰好完全落在窗口内都不能自动认领窗口。
- 退出时统一清理事件、pointer capture、audio、timer、animation frame 和镜像状态。
- Compact Chat 的猫只是同一业务状态的显示/位置镜像，不是第二个 playground owner。
- 缺失素材或 reduced motion 时可直接落到稳定位置，但必须保留退出能力。

## 桌面边界

本仓库可以产生 compact layout、playground 镜像和交互 relay。N.E.K.O-PC 如何移动透明窗口、同步 native bounds、处理跨显示器坐标，需要在外部仓库和真实 Electron 环境验证。网页测试通过不等于桌面窗口行为通过。桌面 Pet 的 minimized 目标几何只接受 N.E.K.O-PC 经 preload 回送的有效 native/self-ball 状态；页面间 BroadcastChannel 的 Web shell 几何只服务 Web，不得在桌面端形成第二个尺寸与生命周期权威。

跨仓库职责固定为两段：N.E.K.O 只持有 Playground body、拖拽手势和窗口 gravity owner；N.E.K.O-PC 只持有原生窗口枚举、场景快照、Electron 窗口锁和对话框恢复转发。任何一方都不能再创建第二个物理循环、第二个窗口准入 owner 或第二套毛线球恢复路径。

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

Playground 会话与毛线球激活事务是两个生命周期。同一 `activationId` 的 BroadcastChannel 与 Electron IPC 重复送达只能恢复一次；确认成功只移除这一代毛线球 body 并清理激活事务，猫、问号方块和 Electron desktop bridge lock 继续属于当前 Playground。聊天再次最小化后，由权威 surface/minimized 状态创建新一代毛线球，旋转、形变和拖拽状态从零开始；上一代迟到的 ACK 不能删除新一代。renderer 重载、转发失败或有界超时只回滚当次激活，不能释放整个会话或让原生球与 Playground 镜像同时可交互。只有点击猫、点击问号方块、tier 变化、来源销毁或页面卸载等最终出口释放会话锁。

进入前已经最小化的聊天在 Playground 最终退出后保持最小化；若最小化由本次 Playground 入口触发，只要该所有权尚未被一次成功的毛线球展开消费，入口取消、创建失败或任何最终出口都恢复进入前 surface。成功展开后用户再次手动最小化属于新选择，后续退出不得反向展开。猫点击仍走原模型 return 语义。每个 body 自带可重复调用的 disposer，单独销毁毛线球不得清理猫或问号方块，也不得向会话级数组累积已失效的闭包。

## 验证

```bash
uv run pytest tests/unit/test_avatar_return_button_idle_tiers_static.py tests/unit/test_avatar_return_button_cat1_static.py tests/unit/test_react_chat_idle_dock_static.py -q
node --test tests/frontend/desktop_window_gravity.test.cjs tests/frontend/desktop_window_sensing_cat1.test.cjs
node --test ../N.E.K.O.-PC/test/desktop-window-sensing-active.test.js ../N.E.K.O.-PC/test/idle-cat-question-mark-layer-contract.test.js
```

手工测试应覆盖高 DPI、窗口缩放、屏幕边缘、快速进入/退出、拖拽后点击、聊天 surface 切换和模型恢复。

## 剩余工作与验证边界

未来小游戏必须复用这一生命周期和清理合同；不要在新的小游戏里复制另一套重力循环或桌面窗口状态机。当前自动测试覆盖状态和 IPC 合同；真实 Electron 仍需在 macOS/Windows、DPI 缩放、跨显示器、窗口关闭/重载和 renderer 重启条件下手工复核，自动测试不能替代原生窗口视觉验收。
