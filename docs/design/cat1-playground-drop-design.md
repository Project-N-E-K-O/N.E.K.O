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
- 退出时统一清理事件、pointer capture、audio、timer、animation frame 和镜像状态。
- Compact Chat 的猫只是同一业务状态的显示/位置镜像，不是第二个 playground owner。
- 缺失素材或 reduced motion 时可直接落到稳定位置，但必须保留退出能力。

## 桌面边界

本仓库可以产生 compact layout、playground 镜像和交互 relay。N.E.K.O-PC 如何移动透明窗口、同步 native bounds、处理跨显示器坐标，需要在外部仓库和真实 Electron 环境验证。网页测试通过不等于桌面窗口行为通过。

## 验证

```bash
uv run pytest tests/unit/test_avatar_return_button_idle_tiers_static.py tests/unit/test_avatar_return_button_cat1_static.py tests/unit/test_react_chat_idle_dock_static.py -q
```

手工测试应覆盖高 DPI、窗口缩放、屏幕边缘、快速进入/退出、拖拽后点击、聊天 surface 切换和模型恢复。

## 剩余工作

未来小游戏必须复用这一生命周期和清理合同；不要在新的小游戏里复制另一套重力循环或桌面窗口状态机。
