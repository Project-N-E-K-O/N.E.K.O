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
- click 与 drag 通过移动阈值/状态区分，不能一次手势触发两条退出路径。
- 毛线球短点击与拖拽分开：短点击只发出恢复聊天框的语义事件并移除毛线球 body；猫 body 继续属于当前 Playground，拖拽/投掷仍走物理释放路径。
- 猫的手动拖动由 Playground 独占，起点、经过路径、当前窗口边界均不限制拖动；松手时仅以最终位置决定是否将猫交给某个桌面窗口 gravity。窗口内释放后，抛掷受该窗口边界约束；窗口外释放后，后续抛掷不会在飞行途中跨入窗口。场景更新、焦点变化或猫恰好完全落在窗口内都不能自动认领窗口。
- 退出时统一清理事件、pointer capture、audio、timer、animation frame 和镜像状态。
- Compact Chat 的猫只是同一业务状态的显示/位置镜像，不是第二个 playground owner。
- 缺失素材或 reduced motion 时可直接落到稳定位置，但必须保留退出能力。

## 桌面边界

本仓库可以产生 compact layout、playground 镜像和交互 relay。N.E.K.O-PC 如何移动透明窗口、同步 native bounds、处理跨显示器坐标，需要在外部仓库和真实 Electron 环境验证。网页测试通过不等于桌面窗口行为通过。

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
  -> release desktop bridge lock
```

窗口 scene 的 `pending/busy` 才表示“原生枚举尚未完成”，不能被 renderer 当作空桌面；此时保留松手时的最终位置，等这次枚举给出结果。真实 `unavailable/no-window` 表示当前没有可容纳窗口：释放时的失败请求必须清掉，不能在未来窗口出现时自动重放。手动拖动可跨入、跨出窗口且不发生碰撞或准入；抛掷不在释放后重新寻找窗口。已经在窗口内释放的 body 才按该窗口边界碰撞。

毛线球短点击是一次性事务：同一 `activationId` 的 BroadcastChannel 与 Electron IPC 重复送达只能恢复一次；毛线球 body 只在短点击路径移除，猫 body 仍留在 Playground。Electron 桌面锁在恢复确认后释放，renderer 重载时由有界超时兜底，不能因为转发失败而永久锁住，也不能在尚未恢复时提前放行。

## 验证

```bash
uv run pytest tests/unit/test_avatar_return_button_idle_tiers_static.py tests/unit/test_avatar_return_button_cat1_static.py tests/unit/test_react_chat_idle_dock_static.py -q
node --test tests/frontend/desktop_window_gravity.test.cjs tests/frontend/desktop_window_sensing_cat1.test.cjs
node --test ../N.E.K.O.-PC/test/desktop-window-sensing-active.test.js ../N.E.K.O.-PC/test/idle-cat-question-mark-layer-contract.test.js
```

手工测试应覆盖高 DPI、窗口缩放、屏幕边缘、快速进入/退出、拖拽后点击、聊天 surface 切换和模型恢复。

## 剩余工作与验证边界

未来小游戏必须复用这一生命周期和清理合同；不要在新的小游戏里复制另一套重力循环或桌面窗口状态机。当前自动测试覆盖状态和 IPC 合同；真实 Electron 仍需在 macOS/Windows、DPI 缩放、跨显示器、窗口关闭/重载和 renderer 重启条件下手工复核，自动测试不能替代原生窗口视觉验收。
