# 猫娘空闲状态分层 - 功能说明

> 本文档描述当前已收敛的目标功能和行为边界，不再保留早期未采用的方案分支。

## 一、目标

“请她离开”之后，模型隐藏，原有回来入口变成可停留、可拖拽、可点击回来的猫形象。

长时间没有有效交互时，系统自动复用现有 goodbye 链路，并让猫形象从清醒逐步过渡到打盹、睡觉。这个功能的核心目标是：降低前台打扰，同时保留轻量陪伴感。

## 二、核心语义

当前功能只引入视觉层分档，不引入新的业务状态机。

| 概念 | 当前语义 |
|------|----------|
| `CAT1` | goodbye 后的基础回来入口，表示刚离开但仍在待机；聊天框最小化且距离较远时会走向聊天框旁并伸懒腰 |
| `CAT2` | 更久 idle 后的打盹形态 |
| `CAT3` | 最久 idle 后的睡觉形态 |
| 点击猫 | 仍然是“请她回来”，继续走现有 return 链 |
| 自动 idle | 只是自动触发一次现有 goodbye，不复制 goodbye 业务逻辑 |

必须保持的语义：

1. `CAT1 / CAT2 / CAT3` 不是会话状态，不改变 `_goodbyeClicked`。
2. return 仍使用现有 `live2d-return-click` / `vrm-return-click` / `mmd-return-click`。
3. 不把恢复改成 `returnSessionButton -> start_session`。
4. 已进入 goodbye 后，普通鼠标、键盘、滚轮、拖拽不自动唤醒，也不重置当前 tier。

## 三、当前流程

### 3.1 手动“请她离开”

```text
用户点击“请她离开”
  -> 走现有 goodbye 链路
  -> 隐藏 Live2D / VRM / MMD 模型
  -> 显示 return 入口
  -> return 入口同步为 CAT1
  -> 继续根据离开后的累计时间切到 CAT2 / CAT3
```

手动 goodbye 不标记为 auto-goodbye，但视觉层仍从 `CAT1` 开始。

### 3.2 自动 idle goodbye

```text
最后一次有效交互开始计时
  -> 达到 AUTO_GOODBYE 阈值且无阻断
  -> 自动派发现有 live2d-goodbye-click
  -> 显示 CAT1
  -> 达到 CAT2 阈值后显示 CAT2
  -> 达到 CAT3 阈值后显示 CAT3
```

当前代码使用发布阈值：

| 阶段 | 发布值 |
|------|--------|
| 自动 goodbye / `CAT1` | 10min |
| `CAT2` | 15min |
| `CAT3` | 18min |

## 四、交互规则

### 4.1 点击与回来

点击 `CAT1 / CAT2 / CAT3` 都是同一个主语义：请她回来。

点击后：

1. 清除当前 visual tier。
2. 走现有 return 链恢复模型和 UI。
3. 重置 idle 基线。

### 4.2 hover / 点击态 GIF

每个 tier 都有默认 GIF 和点击态 GIF。

当前交互口径：

1. 鼠标进入猫形象时，切到当前 tier 对应的 `*-click.gif`。
2. 鼠标离开后，不立即切回默认态，而是等待该 click GIF 自身一轮播放完成。
3. GIF 时长来自对 GIF 帧延迟的解析，失败时使用 fallback。
4. 反复进入 / 离开同一个 tier，不重复设置相同 `src`，避免 GIF 一直从第一帧重播。
5. tier 切换会清掉旧 hover token 和旧 timer，避免串图。

### 4.2.1 CAT1 第一阶段走路 / 伸懒腰

第一阶段已增加一组更完整的猫猫动作，用来扩展当前 `CAT1` 默认/点击资源口径。这里的“走路”不是原地循环，而是猫实际朝聊天框方向移动。它仍属于 `CAT1` 的表现子状态，不新增 `CAT4` tier。

资源：

| 用途 | 资源 |
|------|------|
| 猫猫走路 | `static/assets/neko-idle/cat-idle-cat4-1.gif` |
| 停下伸懒腰 | `static/assets/neko-idle/cat-idle-cat4-2.gif` |
| 鼠标移上前两个动作时的交互态 | `static/assets/neko-idle/cat-idle-cat4-3.gif` |

状态：

```text
CAT1 idle
  -> chat minimized 且距离超过阈值
  -> CAT1 walking-to-chat
  -> 到达聊天框旁边
  -> CAT1 stretch-near-chat
```

触发条件：

1. 当前 visual tier 是 `CAT1`。
2. 聊天框处于最小化球状态。
3. 猫与聊天框最小化球之间的屏幕距离超过阈值。
4. 当前没有 return 点击、拖拽、tier 切换、CAT2/CAT3 idle-dock 进行中。

移动规则：

1. 首次触发后不一定立刻走，会按随机权重等待 `0s`、几秒到十几秒、几十秒或少量几分钟后再开始；大多数情况仍是立刻走或短时间等待。
2. 等待期间保持当前 CAT1 默认表现，不提前播放走路 GIF；计时结束时重新读取当前聊天框位置再决定是否开始走。
3. 触发后播放猫猫走路 GIF，并让 return-ball 容器沿屏幕坐标实际移动。
4. 目标点是聊天框最小化球旁边，而不是与球重叠；需要保留一段视觉间距。
5. 如果聊天框在猫左侧，猫向左走，直接使用默认朝左素材。
6. 如果聊天框在猫右侧，猫向右走，必须对走路 GIF 水平翻转，并且容器实际向右移动。
7. 基础移动速度为 `101px/s`；如果走路途中猫与聊天球距离因为用户移动聊天框或猫而变大，后续移动速度会随距离增长逐步加快，最高到基础速度的 `1.5x`。
8. 同一倍率会同步写到走路 art 的 `data-neko-gif-playback-rate` / `--neko-idle-gif-playback-rate`，并通过运行时 GIF delay patch 生成加速后的 Blob URL，让走路 GIF 本身随倍率加速。
9. 到达目标点后，切到停下伸懒腰 GIF；伸懒腰 GIF 按自身帧时长播完一轮后，额外保持收尾姿态约 `700ms`，再通过短暂过渡缓冲回到最初 `CAT1` 默认猫 GIF，并保持在聊天球旁边。
10. 桌面独立聊天窗会在折叠为小球时发布自己的屏幕矩形；pet 页把这个 screen rect 转成当前窗口坐标后复用同一套 CAT1 寻路逻辑。
11. CAT1 已 settled 后，会按 `5s` 到 `5min` 的加权随机间隔触发一次“猫随机小移动”；移动时猫使用走路 GIF，按屏幕内可用空间随机选择一个短距离方向移动，结束后停在新位置，不回原位。若聊天框是最小化球，则猫和聊天球保持相对距离一起移动；若同页聊天框或桌面独立聊天窗已展开，则只移动猫，不带动聊天框。

重触发规则：

1. 后续如果用户移动聊天框，导致最小化聊天框再次离猫较远，可以再次触发“走向聊天框 -> 停下伸懒腰”。
2. 后续如果用户拖动猫，把猫移动到离最小化聊天框较远的位置，拖拽结束后也会重新评估距离并触发“走向聊天框 -> 停下伸懒腰”。这条同样适用于已经回到 `CAT1` 默认猫 GIF 后再被移动的情况。
3. 为避免来回抖动，需要使用两个阈值：超过较大阈值才触发，回到较小阈值内视为已贴近；已经伸完懒腰并回到 `CAT1` 默认图后，不会在原地重复播放伸懒腰。
4. 如果还在等待首次开走，聊天框或猫位置变化只更新后续判定，不重新抽随机等待。
5. 一次走路尚未完成时，只更新目标点，不重复从第一帧重启 GIF，也不插入新的随机等待。
6. 如果聊天框从最小化切到展开，目标点会暂时不可用；此时只回到当前 tier 的默认表现，不拆聊天框 observer。后续聊天框再次最小化时，仍应重新触发距离判断。
7. “猫随机小移动”只在 CAT1 settled、无 pending walk / walking / stretch / hover / drag / return / tier change 时启动；它是一次性自动编排，不复用普通拖拽生命周期，编排中抑制自身导致的距离重判。聊天框最小化时带最小化球一起移动；聊天框展开时只移动猫。
8. 如果小移动调度时发现 hover / click GIF 仍挂在当前猫图上，会先让该 GIF 按自身生命周期播完并清理 hover token，再重新同步 CAT1 子动作，避免真实点击返回后重新进入 CAT1 时调度链卡死。

hover 与打断：

1. 鼠标移到走路或伸懒腰阶段时，切到统一交互态 GIF。
2. 进入交互态时，自动走路移动暂停，猫停在当前屏幕位置播放交互态 GIF。
3. 鼠标移出后，仍遵守“交互态 GIF 播完一轮再恢复”的规则。
4. 交互态播放完后，如果仍满足最小化聊天框距离阈值，则从当前位置继续朝当前目标移动。
5. 如果交互态播放期间聊天框移动，只更新目标点，不移动猫，也不重启交互态 GIF。
6. 如果用户拖拽猫，立即取消自动走路，并把当前位置作为新的猫位置。
7. 如果用户点击猫回来，优先走 return 链，取消所有 CAT1 子状态。
8. 如果 tier 进入 `CAT2 / CAT3`，取消 CAT1 走路/伸懒腰子状态，由 CAT2/CAT3 自己的表现和聊天窗停靠接管。

当前这组资源已经接入 `static/avatar-ui-buttons.js`、`static/css/index.css`、`static/app-ui.js` 和 `main_routers/pages_router.py`，并由静态测试锁住资源、子状态、右向翻转、hover 暂停/恢复和拖拽取消语义。

实现结构：

1. `CAT1 / CAT2 / CAT3` 仍是唯一对外 visual tier。
2. 走路、伸懒腰、hover 交互、目标跟随属于 tier 内部子动作，不再直接散落为独立业务分支。
3. 内部子动作由 return subaction profile 描述：tier、子状态名、资源、CSS class、目标距离阈值、移动速度、完成动作停留时间和目标监听器都在 profile / 子动作状态中维护。
4. `CAT1` 当前注册的 profile 是 `cat1-chat-follow`；后续如果其他 tier 也需要“走向目标 -> 播放完成动作 -> 回默认态”，应新增 profile 或复用通用子动作控制器，不要把新动作硬编码进三大 tier 判断里。

### 4.3 拖拽

猫形象仍是原 return-ball 容器。

拖拽规则：

1. 拖拽 CAT3 前两次保持睡觉态；第三次及以后从 CAT3 回退到 CAT2。
2. 拖拽 CAT2 一次回退到 CAT1。
3. 拖拽 CAT1 不改变 tier；后续仍按原时间推进到 CAT2 / CAT3。
4. 松手不会刷新用户 idle 基线；回退只重置视觉 tier 的后续推进计时。CAT2 回退到 CAT1 后，CAT1 到 CAT2 要重新等待完整的 CAT1 阶段时间。
5. 点击和拖拽通过位移阈值区分。
6. 桌面端拖拽时，会把当前屏幕坐标同步给桌面聊天窗，使聊天窗跟随猫移动。
7. 越过拖拽阈值后切到当前 tier 对应的拖拽 GIF，占位资源是 `cat-idle-cat-move-1.gif` / `cat-idle-cat-move-2.gif` / `cat-idle-cat-move-3.gif`。
8. 拖拽临时态不改变当前 tier；拖拽结束后才根据当前 tier 和次数决定是否回退。
9. CAT1 走路或伸懒腰期间拖拽 return-ball，会取消当前自动移动；松手后先恢复当前真实 tier，再由 return-ball 位置变化重新评估是否需要再次走向聊天框。

## 五、聊天窗联动

### 5.1 网页端首页

首页 React chat host 在 `CAT2 / CAT3` 下进入 idle dock：

1. 如果聊天框已最小化，则保存原位置，并把最小化球停靠到猫左侧。
2. 如果聊天框未最小化，则先走原始 `setMinimized(true)`，等最小化完成后再停靠。
3. tier 离开 `CAT2 / CAT3` 或点击回来时，恢复停靠前位置。
4. 若这次最小化是 idle dock 主动触发，退出时会恢复展开。

### 5.2 桌面端 Electron 聊天窗

桌面端也要跟随 `CAT2 / CAT3`：

1. 主窗口发布 return-ball 的 `visible / tier / screenRect`。
2. `/chat` Electron 窗口只消费这些状态，不发布自己的 return-ball 状态。
3. 进入 `CAT2 / CAT3` 时，桌面聊天窗先折叠为 `neko-e-collapsed` 小球，再移动到猫左侧。
4. 拖拽猫时，桌面聊天窗按最新屏幕坐标跟随。
5. 退出或点击回来时，取消 pending 折叠 / retry，并恢复原 bounds。
6. 如果桌面聊天窗进入 `CAT2 / CAT3` 前处于 `full` 或 `compact`，预加载层会先临时切到现有 React `minimized` surface，复用原本的最小化小球 DOM；同时用 busy guard 阻止这次 surface 切换触发第二套原生折叠，再由桌面端套用 `neko-e-collapsed` 和原生窗口折叠。
7. 如果进入前是桌面 `compact`，折叠时必须保留 compact 之前的展开 bounds，不能把 compact 载体窗口尺寸保存为后续恢复尺寸。
8. 正常退出 `CAT2 / CAT3` idle-dock 时恢复进入前的 `full / compact` surface；拖拽降级或拖拽结束要求保留当前小球位置时，只提交折叠后的 bounds 并清掉待恢复 surface，聊天球继续停在拖拽结束位置。

`CAT1` 的方向相反：桌面聊天窗发布自己的 minimized screen rect，pet 页消费它，让猫走向聊天小球；这条链路不复用 `CAT2 / CAT3` 的 return-ball dock 事件，避免两边互相驱动。

桌面端必须防止两个问题：

1. 聊天窗自己 resize 后广播“return-ball 不可见”，导致刚折叠又展开。
2. 拖拽中旧的异步定位结果覆盖新坐标，导致抖动或回跳。
3. compact idle-dock 折叠时，旧的 compact relayout、独立 compact 球或错误保存的 compact 载体尺寸覆盖 `neko-e-collapsed` 小球。

当前实现已通过“聊天窗只消费不发布”、generation token、rAF 合并、position sequence、compact 折叠前冻结 relayout 和恢复模式一次性消费来收口这些竞态。

## 六、资源规范

当前资源统一使用 GIF。

| 状态 | 默认资源 | 点击态资源 |
|------|----------|------------|
| `CAT1` | `cat-idle-cat1.gif` | `cat-idle-cat1-click.gif` |
| `CAT2` | `cat-idle-cat2.gif` | `cat-idle-cat2-click.gif` |
| `CAT3` | `cat-idle-cat3.gif` | `cat-idle-cat3-click.gif` |

拖拽动作使用独立占位 GIF，不新增 visual tier：

| 状态 | 拖拽态资源 |
|------|------------|
| `CAT1` | `cat-idle-cat-move-1.gif` |
| `CAT2` | `cat-idle-cat-move-2.gif` |
| `CAT3` | `cat-idle-cat-move-3.gif` |

第一阶段新增的 `cat4` 资源：

| 用途 | 资源 |
|------|------|
| CAT1 走路 | `cat-idle-cat4-1.gif` |
| CAT1 停下伸懒腰 | `cat-idle-cat4-2.gif` |
| CAT1 hover 交互态 | `cat-idle-cat4-3.gif` |

美术交付要求：

1. 默认态和点击态都使用 GIF，不再混用 PNG。
2. 背景透明，主体放在正方形安全区中央。
3. 主体尺度、朝向、落点尽量一致，减少 tier 切换时的跳动。
4. 默认态是低频短循环，点击态是轻反馈，不做夸张变身或完全换构图。
5. 不把“请她回来”等文字画进资源。
6. `CAT2 / CAT3` 左侧会停靠聊天球，猫左侧轮廓不要过度外扩。
7. 拖拽态资源只是当前 tier 的临时动作，不画成新的睡眠阶段，也不改变点击回来语义。

## 七、边界场景

| 场景 | 预期 |
|------|------|
| 活跃态长时间无有效交互且无阻断 | 自动复用 goodbye，进入 CAT1 |
| 已处于 goodbye 后继续闲置 | 继续推进到 CAT2 / CAT3 |
| 已处于 CAT3 时拖拽猫 | 前两次保持 CAT3，第三次及以后回退到 CAT2，桌面聊天窗跟随 |
| 已处于 CAT2 时拖拽猫 | 一次拖拽后回退到 CAT1，聊天球保留拖拽结束位置，不恢复到拖拽开始位置 |
| CAT1 时聊天框最小化且离猫较远 | 猫实际慢速走向聊天框旁边，到达后伸懒腰，收尾姿态稍作停留后带缓冲回到 CAT1 默认猫 |
| CAT1 走路途中聊天框继续移动 | 更新目标点，不重播走路 GIF 第一帧 |
| CAT1 走路途中用户拖拽猫 | 取消自动走路，保留用户拖拽后的新位置；如果松手后仍离聊天框较远，会再次触发走路 |
| CAT1 默认猫或伸懒腰 settled 后，用户移动默认猫或聊天框造成距离变远 | 重新触发走路到聊天框旁边 |
| CAT1 settled 且聊天框已展开 | 随机小移动仍可触发，但只移动猫，不带动展开聊天框 |
| CAT1 settled 且聊天框是最小化球 | 随机小移动会保持猫和最小化球的相对距离一起移动 |
| hover 猫后马上移出 | click GIF 播完一轮再恢复默认态 |
| 反复 hover 同一 tier | 不反复重置 GIF 第一帧 |
| CAT1 走路或伸懒腰阶段 hover | 使用 `cat-idle-cat4-3.gif`，暂停自动移动；移出后等交互态播完再从当前位置恢复当前阶段 |
| tier 切换时仍在 hover | 清理旧 hover 状态，按新 tier 显示 |
| 点击 CAT1 / CAT2 / CAT3 | 走现有 return 链回来 |
| 桌面聊天窗从 compact/full 进入 CAT2 / CAT3 | 先复用最小化 surface 形成 `neko-e-collapsed` 小球，并保存进入前真实展开模式用于恢复 |
| 桌面 idle-dock 拖拽降级并要求保留当前位置 | 保留拖拽结束的小球 bounds，不恢复到进入前 bounds，也不误恢复展开 surface |
| 桌面聊天窗 bridge 繁忙 | 短暂 retry；退出事件可取消 retry |
| 退出时折叠还在进行 | 旧进入链路失效，并尽力展开回滚 |
| 关闭重开 | 不持久化 idle tier，回到活跃态 |

## 八、剩余待办

当前功能侧剩余事项：

1. 对 CAT1 第一阶段 `cat-idle-cat4-1.gif` / `cat-idle-cat4-2.gif` / `cat-idle-cat4-3.gif` 做网页端和桌面端肉眼验收。
2. 替换正式 GIF 资源。
3. 对网页端和桌面端做最终肉眼验收，重点看 CAT1 走路/伸懒腰、CAT2 / CAT3 停靠、拖拽跟随、hover 播放完整度。
