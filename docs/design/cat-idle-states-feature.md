# 猫娘空闲状态分层 - 功能说明

> **文档状态：历史功能基线。** 本文保留 Cat Mind 接入前的猫形态功能说明，不是当前状态机或 return summary 的实施规范。当前行为以 [`cat-idle-state-machine-design.md`](cat-idle-state-machine-design.md) 和 [`cat-idle-state-machine-v1-implementation.md`](cat-idle-state-machine-v1-implementation.md) 为准；若冲突，以可复现代码链路和测试为准。

## 一、目标

“请她离开”之后，模型隐藏，原有回来入口变成可停留、可拖拽、可点击回来的猫形象。

长时间没有有效交互时，系统自动复用现有 goodbye 链路，并让猫形象从清醒逐步过渡到打盹、睡觉。功能目标是降低前台打扰，同时保留轻量陪伴感。

当前实现还包含两类延展：

1. `CAT1` 会和聊天窗形成轻量联动：走向最小化聊天球、停下伸懒腰、玩毛线球、随机小移动；在 compact 聊天框场景下可贴到聊天框上缘。
2. 点击猫回来时，可按“作为猫咪停留的时长 + 当前猫行为 + 自动/手动入口”触发一次猫咪专属问候。

## 二、核心语义

当前功能只引入视觉层分档和表现编排，不引入新的会话状态机。

| 概念 | 当前语义 |
|------|----------|
| `CAT1` | goodbye 后的基础回来入口，表示刚离开但仍在待机 |
| `CAT2` | 更久 idle 后的打盹形态 |
| `CAT3` | 最久 idle 后的睡觉形态 |
| 点击猫 | 仍然是“请她回来”，继续走现有 return 链 |
| 自动 idle | 只是自动触发一次现有 goodbye，不复制 goodbye 业务逻辑 |
| 变回问候 | return 后的独立主动文本机会，不改变 return 语义 |

必须保持的语义：

1. `CAT1 / CAT2 / CAT3` 不是会话状态，不自行改变 `_goodbyeClicked`。
2. return 仍使用现有 `live2d-return-click` / `vrm-return-click` / `mmd-return-click`。
3. 自动 idle 仍派发 `live2d-goodbye-click`，由原 goodbye 链隐藏模型和显示 return 入口。
4. 不把恢复改成 `returnSessionButton -> start_session`。
5. 已进入 goodbye 后，普通鼠标、键盘、滚轮、拖拽不自动唤醒，也不重置当前 tier。
6. 猫咪专属问候只在回来后尝试触发，不应阻塞模型恢复，也不替代普通 `greeting_check`。

主要实现入口：

| 能力 | 文件 |
|------|------|
| 自动 idle 计时、tier 推进、变回问候事件 | `static/app/app-auto-goodbye.js` |
| return-ball 变猫、GIF、hover、拖拽、CAT1 子动作 | `static/avatar/avatar-ui-buttons/*.js` |
| goodbye/return 主 UI 恢复链路 | `static/app/app-ui.js` |
| 首页和桌面聊天窗联动 | `static/app/app-react-chat-window.js`、`static/app/app-interpage.js` |
| 变回问候 WebSocket 与后端投递 | `static/app/app-websocket.js`、`main_routers/websocket_router.py`、`main_logic/core.py`、`config/prompts/prompts_proactive.py` |
| 静态资源版本指纹 | `main_routers/pages_router.py` |

## 三、主流程

### 3.1 手动“请她离开”

```text
用户点击“请她离开”
  -> 派发 live2d-goodbye-click
  -> 原 goodbye 链设置 _goodbyeClicked
  -> 隐藏 Live2D / VRM / MMD 模型与浮动按钮
  -> 显示当前模型对应的 return-ball 容器
  -> return-ball 内部图片同步为 CAT1
  -> 根据离开后的累计时间继续推进到 CAT2 / CAT3
```

手动 goodbye 不标记为 auto-goodbye，但视觉层仍从 `CAT1` 开始。

### 3.2 自动 idle goodbye

```text
最后一次有效交互开始计时
  -> 达到 AUTO_GOODBYE 阈值且无阻断
  -> 设置 visualTier = CAT1
  -> 派发现有 live2d-goodbye-click
  -> 原 goodbye 链显示 CAT1 return-ball
  -> 达到 CAT2 阈值后显示 CAT2
  -> 达到 CAT3 阈值后显示 CAT3
```

当前发布阈值：

| 阶段 | 发布值 |
|------|--------|
| 自动 goodbye / `CAT1` | 10min |
| `CAT2` | 15min |
| `CAT3` | 18min |

自动 idle 的阻断条件包括页面不适用、核心 UI 未就绪、教程/接管态、录音或语音会话、任务队列中存在 running/queued 工作、聊天 grace window 等。阻断解除后需要重新经过完整 idle window，不会立即 goodbye。

### 3.3 点击猫回来

```text
点击 CAT1 / CAT2 / CAT3
  -> 取消当前 hover / 拖拽 / CAT1 子动作
  -> 派发当前模型前缀的 *-return-click
  -> 原 return 链清除 _goodbyeClicked
  -> 隐藏 return-ball，恢复模型、聊天区和浮动按钮
  -> 清空 visualTier
  -> 记录新的 idle 基线
  -> 若猫咪停留时长达标，尝试触发 cat_greeting_check
```

点击与拖拽通过位移阈值区分；拖拽不会误触发 return。

## 四、视觉层和交互

### 4.1 基础 GIF

每个 tier 都有默认 GIF、hover/click GIF 和拖拽 GIF。tier 切换时会清理旧 hover token、旧 timer 和过渡图，避免串图。

hover 规则：

1. 鼠标进入猫形象时，切到当前 tier 对应的 `*-click.gif`。
2. 鼠标离开后，不立即切回默认态，而是等待该 click GIF 自身一轮播放完成。
3. GIF 时长来自帧延迟解析，失败时使用 fallback。
4. 反复进入 / 离开同一个 tier，不重复设置相同 `src`，避免 GIF 一直从第一帧重播。

### 4.1.1 思考气泡 GIF

return-ball 内部会附带一个 `neko-idle-thought-bubble` 容器。容器里分两层：`thought-items/cloud-thought-bubble.gif` 作为气泡背景 GIF，独立内容图作为 `neko-idle-thought-bubble-item` 叠在大气泡中心。气泡默认不参与点击、hover 或拖拽命中；只有 `is-thought-bubble-active` 显示期间，气泡容器本身作为独立点击区域开启命中，图片层仍保持不接事件，避免遮挡猫 GIF 的正常交互。

当前气泡不是 tier 期间常驻显示，而是借用随机声音事件临时显示：

1. `CAT1` 随机环境声播放成功后，触发气泡显示。
2. `CAT2 / CAT3` 随机睡眠声播放成功后，触发气泡显示。
3. 若声音播放入口没有返回可播放 audio 实例，则不显示气泡。
4. 每次触发给当前 tier 的 return button 加 `is-thought-bubble-active`。
5. 普通气泡显示时长为 `5000ms`，之后自动移除 active class；ZZZ 气泡按本次实际播放的睡眠音频时长显示。
6. CSS 默认使用 `opacity: 0` 和 `visibility: hidden` 隐藏，active 时切到可见；显隐过渡使用 `opacity 360ms ease`，隐藏时延迟切 `visibility`，形成渐显渐隐。
7. 气泡内容图位于 `static/assets/neko-idle/thought-items/`，当前候选为 `catnip-pouch.png`、`fish-cookie.png`、`toy-mouse.png`；每次显示气泡时随机选择一张，并在候选数大于 1 时避免连续重复上一张。内容图位置按大气泡主体计算，不按整张 GIF 计算；后续更换内容时只调整该目录内的独立 PNG 资源和候选列表，不需要改动背景 GIF。
8. `CAT2` 每次显示有 `1/3` 概率把背景 GIF 换成 `thought-items/sleeping-zzz.gif`，`CAT3` 概率为 `2/3`。选中 ZZZ 背景时隐藏内容图，显示时长跟随这次实际播放的睡眠音频；普通气泡仍为 `5000ms`。
9. 每次显示气泡时会重启背景 GIF，让背景从第一帧重新播放；普通气泡的内容图动画只在 `is-thought-bubble-active` 期间启动，按约 `3600ms` 周期做约 `1px` 的小幅上下浮动，尽量和气泡播放起点保持一致。
10. `sleeping-zzz.gif` 素材自身已裁掉大面积透明边，只保留必要留白；显示尺寸仍由同一个气泡容器控制，避免为了放大 ZZZ 单独改容器位置。
11. 点击 active 气泡时，不触发 return button 点击或拖拽，NEKO-PC 原生拖拽 capture 路径也必须忽略气泡命中；背景 GIF 切到 `thought-items/cloud-thought-bubble-pop.gif` 并从第一帧播放一次，内容图隐藏；pop GIF 可见帧结束后进入透明尾帧，隐藏流程必须在透明尾帧期间启动，且 fade 期间保留 popping 标记，避免 GIF 循环点闪回和内容图恢复。点击消失会派发 `neko:thought-bubble-pop` 事件；当前如果气泡属于 `CAT1`，会同时尝试触发 CAT1 吃东西动作。

位置规则：

1. `CAT2 / CAT3` 使用默认右上角位置：`right: calc(var(--neko-idle-bubble-size) * -0.18)`，`top: calc(var(--neko-idle-bubble-size) * -0.45)`。
2. `CAT1` 因主体构图不同，使用独立位置：`right: calc(var(--neko-idle-bubble-size) * -0.42)`，`top: calc(var(--neko-idle-bubble-size) * -0.72)`。

隐藏优先级：

1. `data-neko-idle-tier="none"` 时不显示。
2. 长按等待拖拽判定的 `is-drag-action-pending` 时不显示。
3. 实际拖拽的 `is-drag-action` 时不显示。
4. `CAT1` 走路、伸懒腰、玩毛线球和走路/伸懒腰 hover 暂停期间不显示。
5. 如果气泡显示期间 tier 已变化，会清理旧气泡状态，避免从旧 tier 串到新 tier。

### 4.2 CAT1 子动作

`CAT1 / CAT2 / CAT3` 仍是唯一对外 visual tier。走路、伸懒腰、玩毛线球、吃东西、贴 compact 上缘、hover 交互、目标跟随、随机小移动都属于 `CAT1` 内部子动作，不新增 `CAT4` tier。

当前 `CAT1` 注册的 return subaction profile 是 `cat1-chat-follow`。profile 维护 tier、子状态名、资源、CSS class、目标距离阈值、移动速度、完成动作停留时间和目标监听器。

#### 4.2.1 走向最小化聊天球

```text
CAT1 idle
  -> chat minimized 且距离超过阈值
  -> pending walk delay
  -> walking-to-chat
  -> 25% play-near-chat / 75% stretch-near-chat
  -> settled CAT1
```

触发条件：

1. 当前 visual tier 是 `CAT1`。
2. 聊天框处于最小化球状态。
3. 猫与聊天球之间的屏幕距离超过进入阈值。
4. 当前没有 return 点击、拖拽、tier 切换、CAT2/CAT3 idle-dock 或 CAT1 compact 上缘接管进行中。

移动规则：

1. 首次触发后按随机权重等待 `0s`、短延迟、中延迟或少量长延迟；等待期间保持 CAT1 默认 GIF。
2. 计时结束时重新读取聊天框位置，再决定是否开始走。
3. 走路使用 `cat-idle-cat4-1.gif`，return-ball 容器沿屏幕坐标实际移动。
4. 目标点在聊天球旁边，视觉主体不与球冲突；容器矩形允许少量重叠，用来抵消 GIF 透明安全区带来的空边。
5. 聊天球在猫左侧时使用默认朝左素材；在右侧时水平翻转，并实际向右移动。
6. 基础速度为约 `82px/s`；当前走路 GIF 每帧约 `30ms`，对应实际位移约 `2.46px/frame`，使脚步动画和容器移动更接近。到达阈值收紧到约 `14px`，避免最后一段直接吸附到聊天目标上。如果途中距离因用户移动变大，速度最高提升到 `1.5x`。
7. 同一倍率会写入走路 art 的 playback rate，并通过运行时 GIF delay patch 让 GIF 本身同步加速。
8. 到达最小化聊天球旁后，如果目标确认为最小化球侧边，有 `0.25` 概率进入玩毛线球动作；否则播放 `cat-idle-cat4-2.gif` 伸懒腰。伸懒腰播完一轮后额外保持约 `700ms`，再回到 CAT1 默认 GIF。
9. 桌面独立聊天窗折叠为小球时，会发布自己的屏幕矩形；pet 页转成当前窗口坐标后复用同一套寻路逻辑。

#### 4.2.1.1 玩毛线球动作

玩毛线球是 `CAT1` 走向最小化聊天球后的独立动作模块，不改变 visual tier，也不新增业务状态。

规则：

1. 只在 `CAT1`、非拖拽、return-ball 可见且当前没有 play 动作进行中时启动。
2. 触发入口是走向最小化聊天球完成后的分支，当前概率为 `0.25`；未命中或启动失败时仍走 `stretch-near-chat`。
3. 动作 GIF 使用 `cat-idle-cat-play-1.gif`；动作时长按该 GIF 解析出的一轮播放时长决定，不绑定声音时长。
4. 动作开始时同时播放 `cat1-voice3.mp3`，声音只作为伴随反馈，不决定动作结束点。
5. 动作期间给 return button / return container 加 `is-cat1-playing`，用于隐藏思考气泡并同步镜像表现。
6. 动作期间临时隐藏当前最小化毛线球：网页端通过 `data-neko-cat1-play-hidden="true"` 隐藏 minimized shell；桌面端通过 `idle_cat1_play_yarn_visibility` 消息让 `/chat` 小球临时透明并关闭命中。
7. 动作结束、取消、tier 变化、拖拽或 return 打断时，必须恢复毛线球显示并清理 `is-cat1-playing`；恢复只撤销 play 的临时隐藏，不改变聊天窗原本 minimized / compact / bounds 流程。
8. 如果 play 触发前存在正在进行的 CAT1 寻路状态，会先暂停；play 正常结束后按原目标链路恢复评估。

#### 4.2.1.2 吃东西动作

吃东西是 `CAT1` 思考气泡点击后的独立动作模块，不改变 visual tier，也不新增业务状态。

规则：

1. 只在 `CAT1`、非拖拽、return-ball 可见且当前没有 eat 动作进行中时启动。
2. 触发入口是点击 active 思考气泡后的 pop 流程；`CAT2 / CAT3` 气泡点击只做 pop，不进入吃东西动作。
3. 动作 GIF 使用 `cat-idle-cat1-eat.gif`；声音使用 `cat1-voice-eat.mp3`。
4. 动作结束要求 GIF 一轮播放完成，并且声音播放结束；如果没有可用 audio 实例，则只等待 GIF。
5. 动作期间给 return button / return container 加 `is-cat1-eating`，并同步镜像表现。
6. eat 与 play 互斥：启动 eat 会取消 play，启动 play 也会取消 eat，避免两个独立动作同时写同一张猫 GIF。
7. 如果 eat 触发前处于 CAT1 走路或伸懒腰链路，会先暂停；动作正常结束后按原目标链路恢复评估。

重触发规则：

1. 用户移动聊天框或拖动猫导致距离再次变远，可以重新触发“走向聊天框 -> 玩毛线球或伸懒腰”。
2. 使用进入阈值和退出阈值避免来回抖动。
3. pending walk 期间目标变化只影响后续判定，不重新抽随机等待。
4. walking 期间目标变化只更新目标点，不重启 GIF、不插入新等待。
5. 聊天框从最小化切到展开时，当前目标暂时不可用；回到当前 tier 默认表现，后续再次最小化仍可重判。

#### 4.2.2 坐到 compact 聊天框上缘

当聊天框处于 compact surface 场景，CAT1 可以贴到 compact 聊天框上缘，而不是只停在最小化球旁。

这条链路有两层表现：

1. pet 页 return-ball 发出 `neko:idle-cat1-layer-request`，请求当前 CAT1 进入 higher layer，并用 heartbeat 维持；释放时延迟回到默认层。
2. `/chat` 或 compact surface 消费 `idle_cat1_compact_mirror_state`，创建 `neko-idle-cat1-compact-mirror` 镜像元素，把 CAT1 画在 compact surface 上缘；原 pet return-ball 在镜像激活时隐藏自身 art，避免双猫。

边界：

1. compact 上缘跟随只属于 CAT1 子动作，不改变 tier。
2. 镜像层有 timeout 和 inactive 通知；目标失效、退出 CAT1、return、拖拽或进入 CAT2/CAT3 时必须释放。
3. 从上缘脱离时会播放短暂 drop 动效，再回到默认 return-ball 层。
4. 普通拖动聊天框时 CAT1 应稳定跟随；只有连续出现明显快速位移时才触发 drop，把猫从上缘甩下，避免单帧速度尖峰造成误脱离。
5. 该机制用于跨窗口/跨 surface 视觉对齐，不应把聊天框或 return 主语义改成新的业务状态。

#### 4.2.3 随机小移动

CAT1 settled 后，会按 `5s` 到 `5min` 的加权随机间隔触发一次轻量随机事件：`95%` 执行随机小移动，`5%` 直接触发 play 动作。

规则：

1. 只在无 pending walk / walking / stretch / play / hover / drag / return / tier change 时启动。
2. 抽中随机小移动时，使用走路 GIF，按屏幕内可用空间随机选择短距离方向，结束后停在新位置，不回原位。
3. 抽中 play 时，复用 `4.2.1.1` 的 play 动作模块；这次事件不再执行随机小移动。
4. 如果聊天框是最小化球，随机小移动会让猫和聊天球保持相对距离一起移动。
5. 如果聊天框展开或桌面独立聊天窗已展开，随机小移动只移动猫，不带动聊天框。
6. 如果调度时发现 hover/click GIF 仍挂在当前猫图上，会先让该 GIF 按生命周期播完并清理 token，再同步 CAT1 子动作。

### 4.3 hover 与打断

1. 鼠标移到走路或伸懒腰阶段时，使用 `cat-idle-cat4-3.gif`。
2. 进入交互态时，自动移动暂停，猫停在当前屏幕位置播放交互态 GIF。
3. 鼠标移出后，仍等待交互态 GIF 播完一轮。
4. 交互态播放完后，如果仍满足距离阈值，则从当前位置继续朝当前目标移动。
5. 交互态播放期间聊天框移动，只更新目标点，不移动猫，也不重启交互态 GIF。
6. 用户拖拽猫会立即取消自动走路，并把当前位置作为新的猫位置。
7. 用户点击猫回来优先走 return 链，取消所有 CAT1 子状态。
8. tier 进入 `CAT2 / CAT3` 时，取消 CAT1 子状态，由 CAT2/CAT3 表现和聊天窗停靠接管。

### 4.4 拖拽

猫形象仍是原 return-ball 容器。

拖拽规则：

1. 拖拽 CAT3 第一次保持睡觉态；第二次及以后从 CAT3 回退到 CAT2。
2. 拖拽 CAT2 一次回退到 CAT1。
3. 拖拽 CAT1 不改变 tier；后续仍按原时间推进到 CAT2 / CAT3。
4. 松手不会刷新用户 idle 基线；回退只重置视觉 tier 的后续推进计时。
5. CAT2 回退到 CAT1 后，CAT1 到 CAT2 要重新等待完整 CAT1 阶段时间。
6. 越过拖拽阈值后切到当前 tier 对应拖拽 GIF；每次进入实际拖拽态时随机二选一，本次拖拽期间复用同一张，避免拖拽过程中跳图。
7. 拖拽临时态不改变当前 tier；拖拽结束后才根据当前 tier 和次数决定是否回退。
8. CAT1 自动走路或伸懒腰期间拖拽 return-ball，会取消当前自动移动；松手后恢复真实 tier，再由新位置重新评估是否需要走向聊天框。
9. 桌面端拖拽时，会把当前屏幕坐标同步给桌面聊天窗，使聊天窗跟随猫移动。
10. CAT1 同一次按住拖拽中，如果在短时间内出现多次快速来回甩动，才会临时进入独立拖拽反应态：当前判定要求约 `1100ms` 内至少 `6` 次有效方向反转，每次反转需要足够位移；反转使用连续有效移动向量的夹角/点积判断，夹角至少约 `90°`，对应点积阈值约 `0`，不使用单轴正负切换；最终按整段甩动窗口的累计路径计算平均速度，当前要求约 `800px/s`，避免单个相邻 move 事件的瞬时速度尖峰误触发。触发后 GIF 切到 `cat-idle-cat-move-5.gif`，声音切到 `cat1-voice-funny.mp3`，声音使用独立 rapid drag 播放状态并与普通拖拽音互斥；持续约 `5s` 后仍在拖拽则回到本次普通 CAT1 拖拽 GIF；松手或取消拖拽会立即结束该反应态。
11. CAT1 快速甩动反应只属于当前拖拽表现层，不新增 tier，不改变 CAT1 的拖拽降级规则，不影响 `CAT2 / CAT3` 的拖拽回退语义。
12. CAT1 拖拽松手时，如果最终位置距离屏幕任一边小于约 `2.5%` 个猫宽/高，会进入贴边半藏表现；拖拽过程中不实时旋转或半藏，下一次按住拖拽会先恢复到屏幕内再进入正常拖拽。
13. CAT1 贴边半藏只属于表现层：约 `40%` 个猫身在屏幕外、更多主体保留在屏幕内。左边缘使用约 `60°`，右边缘使用约 `-60°`；上边缘倒转 `180°`；下边缘不加角度。
14. CAT1 贴到下方角落时使用独立的下左角/下右角落点，角度按左右侧边规则处理；贴到上方角落时先倒转 `180°`，再按左右方向额外倾斜约 `60°`，其中上左角与上右角的倾斜方向相反。上下边缘触发时，如果猫中心接近左右侧边约一个猫宽内，则吸附到对应角落，避免角落附近落成单边吸附；贴边后不触发 CAT1 自动走路或随机小移动，直到用户再次拖拽把猫从贴边状态拉出；该表现不新增 tier，不改变 CAT1/CAT2/CAT3 拖拽回退语义，不影响思考气泡和点击 return 语义。

## 五、聊天窗联动

### 5.1 首页 React chat host

`CAT2 / CAT3` 下进入 idle dock：

1. 如果聊天框已最小化，则保存原位置，并把最小化球停靠到猫左侧。
2. 如果聊天框未最小化，则先走原始 `setMinimized(true)`，等最小化完成后再停靠。
3. tier 离开 `CAT2 / CAT3` 或点击回来时，恢复停靠前位置。
4. 若这次最小化是 idle dock 主动触发，退出时恢复展开。
5. idle dock 不改 `setMinimized()` 内部语义，只作为独立编排层调用和观察它。

`CAT1` 方向相反：聊天窗发布自己的 minimized rect，pet 页消费它，让猫走向聊天球。这条链路不复用 `CAT2 / CAT3` 的 return-ball dock 事件，避免互相驱动。

### 5.2 桌面端 Electron 聊天窗

桌面端当前只覆盖 `/chat` 的 compact / minimized 链路。`/chat_full` 是旧 full 方案的独立隔离窗口，不参与本功能的 `CAT2 / CAT3` idle-dock，不消费 return-ball dock 状态，也不作为当前验收范围。

桌面端 `/chat` 跟随 `CAT2 / CAT3`：

1. 主窗口发布 return-ball 的 `visible / tier / screenRect`。
2. `/chat` Electron 窗口只消费这些状态，不发布自己的 return-ball 状态。
3. 进入 `CAT2 / CAT3` 时，桌面聊天窗先折叠为 `neko-e-collapsed` 小球，再移动到猫左侧。
4. 拖拽猫时，桌面聊天窗按最新屏幕坐标跟随。
5. 退出或点击回来时，取消 pending 折叠 / retry，并恢复原 bounds。
6. 如果进入前处于桌面 `compact`，预加载层会先临时切到现有 React `minimized` surface，复用原最小化小球 DOM；同时用 busy guard 阻止这次 surface 切换触发第二套原生折叠。
7. 如果进入前是桌面 `compact`，折叠时必须保留 compact 之前的展开 bounds，不能把 compact 载体窗口尺寸保存为后续恢复尺寸。
8. 正常退出 `CAT2 / CAT3` idle-dock 时恢复进入前的 `compact` surface；如果进入前本来就是 minimized，则恢复进入前的 minimized bounds。
9. 拖拽降级或拖拽结束要求保留当前位置时，只提交折叠后的 bounds 并清掉待恢复 surface，聊天球继续停在拖拽结束位置。

竞态收口：

1. 聊天窗自己 resize 后不能广播“return-ball 不可见”导致刚折叠又展开。
2. 拖拽中旧的异步定位结果不能覆盖新坐标。
3. compact idle-dock 折叠时，旧 compact relayout、独立 compact 球或错误保存的 compact 载体尺寸不能覆盖 `neko-e-collapsed` 小球。
4. 当前实现通过“聊天窗只消费不发布”、generation token、rAF 合并、position sequence、compact 折叠前冻结 relayout 和恢复模式一次性消费来收口。

## 六、变回猫娘专属问候

点击猫回来时，前端按“猫咪停留时长 + 当前 tier + 自动/手动入口”尝试发送一次 `cat_greeting_check`。

流程：

```text
进入 goodbye
  -> app-auto-goodbye 记录 goodbyeEnteredAt / goodbyeWasAuto
点击猫回来
  -> return 事件清空 visual tier 前读取当前 tier
  -> 派发 neko:cat-greeting-check
  -> app-websocket 发送 cat_greeting_check
  -> websocket_router 调用 trigger_cat_greeting
  -> core 按 tier 映射行为并选择 prompt
  -> 可用时拉起 text session 投递一次专属问候
```

行为映射：

| tier | 行为 |
|------|------|
| `CAT1` | `awake`，清醒等待 |
| `CAT2` | `nap`，打盹 |
| `CAT3` | `sleep`，熟睡 |

时长规则：

1. 停留少于 `180s` 静默，不触发。
2. `awake` 的 long 门槛是 `900s`。
3. `nap` / `sleep` 的 long 门槛是 `1800s`。
4. 自动 idle 与手动 goodbye 使用不同 reason hint。

边界：

1. 变回问候与普通 `greeting_check` 对偶但独立计时，不看 last conversation gap。
2. 变回问候不发送 agent intent restore，因为点击回来不是首次进入会话。
3. 若 WebSocket 未连接则静默放弃；普通 greeting 后续仍可按重连和对话 gap 兜底。
4. 首页教程 greeting guard 生效时会阻止该问候。
5. 语音 session 正在启动/已活跃、session takeover 活跃、SM proactive claim 失败时跳过。
6. 触发失败不应影响模型恢复、return-ball 隐藏或 idle tier 清理。

## 七、资源规范

当前猫资源统一使用 GIF。

| 状态 | 默认资源 | 点击态资源 | 拖拽态资源 |
|------|----------|------------|------------|
| `CAT1` | `cat-idle-cat1.gif` | `cat-idle-cat1-click.gif` | `cat-idle-cat-move-1.gif` / `cat-idle-cat-move-2.gif` |
| `CAT2` | `cat-idle-cat2.gif` | `cat-idle-cat2-click.gif` | `cat-idle-cat-move-2.gif` / `cat-idle-cat-move-3.gif` |
| `CAT3` | `cat-idle-cat3.gif` | `cat-idle-cat3-click.gif` | `cat-idle-cat-move-3.gif` / `cat-idle-cat-move-4.gif` |

CAT1 快速甩动拖拽反应资源：

| 用途 | 资源 |
|------|------|
| CAT1 同次拖拽中快速来回甩动 | `cat-idle-cat-move-5.gif` |

CAT1 子动作资源：

| 用途 | 资源 |
|------|------|
| CAT1 走路 | `cat-idle-cat4-1.gif` |
| CAT1 停下伸懒腰 | `cat-idle-cat4-2.gif` |
| CAT1 走路/伸懒腰 hover 交互态 | `cat-idle-cat4-3.gif` |
| CAT1 玩毛线球 | `cat-idle-cat-play-1.gif` |
| CAT1 吃东西 | `cat-idle-cat1-eat.gif` |

聊天窗资源：

| 用途 | 资源 |
|------|------|
| 最小化聊天球 | `chat-minimized-yarn-ball.png` |

气泡资源：

| 用途 | 资源 |
|------|------|
| 随机声音触发的思考气泡背景 | `thought-items/cloud-thought-bubble.gif` |
| 点击气泡后的 pop 背景 | `thought-items/cloud-thought-bubble-pop.gif` |
| 睡眠态 ZZZ 背景 | `thought-items/sleeping-zzz.gif` |
| 思考气泡内容图候选 | `thought-items/catnip-pouch.png` / `thought-items/fish-cookie.png` / `thought-items/toy-mouse.png` |

模型/猫过渡资源：

| 用途 | 资源 |
|------|------|
| 模型隐藏后进入猫形态、猫点击返回模型前的中间遮罩动画 | `cat_model_change.gif` |

声音资源：

| 用途 | 资源 |
|------|------|
| CAT1 拖拽/点击反馈 | `cat1-voice-click.mp3` |
| CAT1 快速甩动拖拽反馈 | `cat1-voice-funny.mp3` |
| CAT1 环境声 | `cat1-voice1.mp3` / `cat1-voice2.mp3` / `cat1-voice3.mp3` |
| CAT1 玩毛线球反馈 | `cat1-voice3.mp3` |
| CAT1 吃东西反馈 | `cat1-voice-eat.mp3` |
| CAT2 睡眠声 | `cat2-sleep1.mp3` / `cat2-sleep2.mp3` 随机 |
| CAT3 睡眠声 | `cat3-sleep1.mp3` / `cat3-sleep2.mp3` 随机 |

美术交付要求：

1. 默认态和点击态都使用 GIF，不再混用 PNG。
2. 背景透明，主体放在正方形安全区中央。
3. 主体尺度、朝向、落点尽量一致，减少 tier 切换时跳动。
4. 默认态是低频短循环，点击态是轻反馈，不做夸张变身或完全换构图。
5. 不把“请她回来”等文字画进资源。
6. `CAT2 / CAT3` 左侧会停靠聊天球，猫左侧轮廓不要过度外扩。
7. 拖拽态资源只是当前 tier 的临时动作，不画成新的睡眠阶段，也不改变点击回来语义。
8. `cat_model_change.gif` 是双向切换遮罩，不是串行动画：`model-to-cat` 时优先取当前模型屏幕矩形作为锚点，模型退出和猫出现照常推进，GIF 盖在模型/猫上层；`cat-to-model` 时 GIF 盖在猫身上，原返回事件立即派发，让猫隐藏和模型恢复在遮罩后面发生。
9. 猫初始出现位置必须是模型原处；模型恢复位置也必须以猫当前位置为基准。goodbye 按钮位置只允许作为模型 bounds 不可用时的兜底。
10. goodbye 退出动效应为模型原处缩小渐隐，不再向右滑出；缩放原点来自当前模型屏幕矩形中心。退出末端模型尺度约 `0.38`，缩小速度仍约 `400ms`；Live2D / VRM / MMD 都必须走 `playModelGoodbyeExit`，不允许某一类模型只做单独 canvas 淡出。`playModelGoodbyeExit` 不接管缩小，只额外让模型视觉层约 `240ms` 内进入全透明，使模型在 `model-to-cat` 遮罩烟雾散开前就不可见。该视觉层透明度控制必须可重复调用：如果后续 `hideLive2d` / reset 链路再次进入退出逻辑，不能清掉或重置已经开始的提前透明。
11. return 恢复动效应应为模型从猫当前位置原处放大渐显；缩放原点来自点击时猫的屏幕矩形中心，入场起始尺度同样约 `0.38`，淡入约 `1120ms`，只作用于猫形态返回模型这条链。`cat-to-model` 遮罩位置以猫为中心，但遮罩尺寸必须使用模型原始矩形或当前模型矩形，不能退回猫球尺寸。
12. 过渡 GIF 要能遮住模型/猫切换焦点，并让切换不突兀；显示尺寸按锚点矩形约 `0.86` 倍取值，范围约 `260-680px`。素材后半不能突然掉到深灰调。GIF 格式不支持真正半透明渐变边缘，显示层需要用圆形 `mask-image` 和 `border-radius: 50%` 给过渡图做边缘羽化，避免 640x640 方形边界暴露。如果后续发现大模型退出过程中仍显得过大，先在消失阶段把模型缩到遮罩能覆盖的尺寸，恢复阶段反向处理，再回到原模型大小。
13. 模型/猫双向切换必须全局互斥：`model-to-cat` 未完成时忽略后续 goodbye/return，`cat-to-model` 未完成时也忽略后续 return/goodbye；同向和反向点击都不能清掉当前遮罩、重复派发状态事件或重启 GIF。遮罩可见时长可以提前避开 GIF 循环边界，但状态完成点和互斥锁释放不能因此提前。
14. 实现结构应保持分层：过渡参数集中定义；`reserve/release/isNekoModelCatTransitionActive` 只管互斥；`playNekoModelCatTransition` 只管遮罩生命周期；`playModelGoodbyeExit` / `playModelReturnEnter` 只管模型自身入退场；模型退出的视觉层提前透明、圆形羽化和模型恢复初始态使用 helper 统一写入，避免分支重复。

## 八、边界场景

| 场景 | 预期 |
|------|------|
| 活跃态长时间无有效交互且无阻断 | 自动复用 goodbye，进入 CAT1 |
| 已处于 goodbye 后继续闲置 | 继续推进到 CAT2 / CAT3 |
| 手动 goodbye | 进入 CAT1，不标记 auto-goodbye |
| 普通鼠标、键盘、滚轮、拖拽发生在 goodbye 后 | 不自动唤醒，不重置当前 tier |
| 点击 CAT1 / CAT2 / CAT3 | 走现有 return 链回来 |
| 点击回来且猫咪停留少于 180s | 不发送专属问候 |
| 点击回来且猫咪停留达标 | 按 tier × 时长 × auto/manual 尝试专属问候 |
| 已处于 CAT3 时拖拽猫 | 第一次保持 CAT3，第二次及以后回退到 CAT2，桌面聊天窗跟随 |
| 已处于 CAT2 时拖拽猫 | 一次拖拽后回退到 CAT1，聊天球保留拖拽结束位置 |
| CAT1 时聊天框最小化且离猫较远 | 猫慢速走向聊天框旁，到达后按 `0.25` 概率玩毛线球，否则伸懒腰；结束后回到 CAT1 默认猫 |
| CAT1 走路途中聊天框继续移动 | 更新目标点，不重播走路 GIF 第一帧 |
| CAT1 走路途中用户拖拽猫 | 取消自动走路，保留用户拖拽后的新位置 |
| CAT1 玩毛线球期间 | 临时隐藏毛线球和思考气泡，动作结束或被打断后恢复毛线球显示 |
| CAT1 settled 随机事件触发 | `95%` 执行随机小移动，`5%` 直接触发 play 动作 |
| CAT1 settled 且聊天框是最小化球并抽中随机小移动 | 随机小移动保持猫和最小化球相对距离一起移动 |
| CAT1 settled 且聊天框已展开并抽中随机小移动 | 随机小移动只移动猫，不带动展开聊天框 |
| CAT1 随机环境声播放成功 | 显示思考气泡，5s 后渐隐 |
| CAT1 active 思考气泡被点击 | 气泡播放 pop GIF 并尝试触发吃东西动作；不触发 return 或拖拽 |
| CAT2 / CAT3 随机睡眠声播放成功 | 显示思考气泡；普通气泡 5s 后渐隐，ZZZ 气泡跟随本次睡眠音频播放时长 |
| 长按猫但还未超过拖拽阈值 | 不显示思考气泡，不进入普通 5s / ZZZ 音频同步的显示时长，不扩大容器影响猫位置 |
| 拖拽猫期间 | 不显示思考气泡，不影响拖拽命中 |
| CAT1 贴到 compact 聊天框上缘 | 使用镜像层/高层级请求表现，不改变 tier 和 return 语义 |
| CAT1 compact 上缘目标失效 | 释放镜像层，回到默认 return-ball 层 |
| hover 猫后马上移出 | click GIF 播完一轮再恢复默认态 |
| 反复 hover 同一 tier | 不反复重置 GIF 第一帧 |
| CAT1 走路或伸懒腰阶段 hover | 使用 `cat-idle-cat4-3.gif`，暂停自动移动；移出后等交互态播完再恢复 |
| tier 切换时仍在 hover | 清理旧 hover 状态，按新 tier 显示 |
| 桌面 `/chat` 从 compact 进入 CAT2 / CAT3 | 先复用最小化 surface 形成 `neko-e-collapsed` 小球，并保存进入前真实展开模式 |
| 桌面 `/chat_full` 处于 full 旧方案 | 作为独立隔离窗口，不参与 CAT2 / CAT3 idle-dock |
| 桌面 idle-dock 拖拽降级并要求保留当前位置 | 保留拖拽结束的小球 bounds，不恢复到进入前 bounds，也不误恢复展开 surface |
| 桌面聊天窗 bridge 繁忙 | 短暂 retry；退出事件可取消 retry |
| 退出时折叠还在进行 | 旧进入链路失效，并尽力展开回滚 |
| 关闭重开 | 不持久化 idle tier，回到活跃态 |

## 九、验证与维护

已由静态测试锁住的核心边界包括：

1. 自动 goodbye 只复用现有 `live2d-goodbye-click` 链路。
2. return-ball 回来仍走 `*-return-click` 和 `handleReturnClick`，不改成 `start_session`。
3. CAT1/CAT2/CAT3 资源、点击态、拖拽态和声音资源存在。
4. CAT1 走路/伸懒腰/play/eat profile、右向翻转、hover 暂停/恢复、拖拽取消语义存在。
5. `CAT2 / CAT3` idle-dock 只消费 visual tier 和 return-ball state，不让 `/chat` 发布自己的 return-ball 状态。
6. `app-auto-goodbye.js` 只注入首页/角色页，不注入 `/chat`。
7. 猫咪专属问候 prompt 经过反物化文本护栏，少于 180s 静默。
8. 思考气泡在长按 pending / 拖拽 / CAT1 走路 / 伸懒腰 / 玩毛线球期间隐藏，并且只由随机声音播放成功触发渐显渐隐；普通气泡显示 5s，ZZZ 气泡跟随本次睡眠音频播放时长；active 气泡可点击切到 pop GIF 后，在透明尾帧期间按正常隐藏流程消失。
9. CAT1 走向最小化聊天球完成后，play 分支概率为 `0.25`；CAT1 settled 随机事件中 play 分支概率为 `0.05`，其余 `0.95` 执行随机小移动。play 命中时动作时长跟随 `cat-idle-cat-play-1.gif` 一轮播放，期间临时隐藏网页/桌面毛线球，结束或取消后恢复。

后续修改要求：

1. 新增 tier 前先确认是否真的需要业务级扩展；普通动作优先作为现有 tier 的 subaction profile。
2. 新增用户可见文案时同步 i18n；纯 prompt 模板按 prompt 语言表维护。
3. 修改聊天窗联动时同时检查网页首页、桌面 `/chat`、compact/minimized、拖拽和 return 恢复；`/chat_full` 作为旧 full 隔离方案，只检查不会被 idle-dock 误驱动。
4. 修改资源时同步 `main_routers/pages_router.py` 的静态版本路径和相关静态测试。
5. 修改变回问候时同时检查前端事件、WebSocket action、后端 guard、prompt 阈值和普通 greeting 链路隔离。

当前仍建议做运行时肉眼验收：

1. CAT1 走路/伸懒腰/play/eat、hover 播放完整度、右向翻转、速度加速和临时隐藏恢复。
2. CAT1 compact 上缘镜像层进入、释放和 drop 效果。
3. CAT2 / CAT3 网页和桌面 idle-dock、拖拽跟随、降级后保留位置。
4. 点击回来后少于 180s 静默、超过 180s 触发一次专属问候，且不影响模型恢复。
