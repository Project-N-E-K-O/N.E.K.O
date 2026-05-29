# 猫娘空闲状态分层 - 功能说明

> 代码级参考见 [cat-idle-states-reference.md](./cat-idle-states-reference.md)。
> 本文档描述当前已收敛的目标功能和行为边界，不再保留早期未采用的方案分支。

## 一、目标

“请她离开”之后，模型隐藏，原有回来入口变成可停留、可拖拽、可点击回来的猫形象。

长时间没有有效交互时，系统自动复用现有 goodbye 链路，并让猫形象从清醒逐步过渡到打盹、睡觉。这个功能的核心目标是：降低前台打扰，同时保留轻量陪伴感。

## 二、核心语义

当前功能只引入视觉层分档，不引入新的业务状态机。

| 概念 | 当前语义 |
|------|----------|
| `CAT1` | goodbye 后的基础回来入口，表示刚离开但仍在待机 |
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

当前代码仍使用联调阈值：

| 阶段 | 当前联调值 | 正式目标值 |
|------|------------|------------|
| 自动 goodbye / `CAT1` | 5s | 20min |
| `CAT2` | 10s | 30min |
| `CAT3` | 15s | 40min |

正式发版前需要把阈值切回正式目标值。

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

### 4.3 拖拽

猫形象仍是原 return-ball 容器。

拖拽规则：

1. 拖拽 CAT2 / CAT3 不会把状态退回 CAT1。
2. 松手也不会刷新 idle 基线。
3. 点击和拖拽通过位移阈值区分。
4. 桌面端拖拽时，会把当前屏幕坐标同步给桌面聊天窗，使聊天窗跟随猫移动。

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

桌面端必须防止两个问题：

1. 聊天窗自己 resize 后广播“return-ball 不可见”，导致刚折叠又展开。
2. 拖拽中旧的异步定位结果覆盖新坐标，导致抖动或回跳。

当前实现已通过“聊天窗只消费不发布”、generation token、rAF 合并和 position sequence 收口这两类竞态。

## 六、资源规范

当前资源统一使用 GIF。

| 状态 | 默认资源 | 点击态资源 |
|------|----------|------------|
| `CAT1` | `cat-idle-cat1.gif` | `cat-idle-cat1-click.gif` |
| `CAT2` | `cat-idle-cat2.gif` | `cat-idle-cat2-click.gif` |
| `CAT3` | `cat-idle-cat3.gif` | `cat-idle-cat3-click.gif` |

美术交付要求：

1. 默认态和点击态都使用 GIF，不再混用 PNG。
2. 背景透明，主体放在正方形安全区中央。
3. 主体尺度、朝向、落点尽量一致，减少 tier 切换时的跳动。
4. 默认态是低频短循环，点击态是轻反馈，不做夸张变身或完全换构图。
5. 不把“请她回来”等文字画进资源。
6. `CAT2 / CAT3` 左侧会停靠聊天球，猫左侧轮廓不要过度外扩。

## 七、边界场景

| 场景 | 预期 |
|------|------|
| 活跃态长时间无有效交互且无阻断 | 自动复用 goodbye，进入 CAT1 |
| 已处于 goodbye 后继续闲置 | 继续推进到 CAT2 / CAT3 |
| 已处于 CAT2 / CAT3 时拖拽猫 | 状态保持，不回到 CAT1，桌面聊天窗跟随 |
| hover 猫后马上移出 | click GIF 播完一轮再恢复默认态 |
| 反复 hover 同一 tier | 不反复重置 GIF 第一帧 |
| tier 切换时仍在 hover | 清理旧 hover 状态，按新 tier 显示 |
| 点击 CAT1 / CAT2 / CAT3 | 走现有 return 链回来 |
| 桌面聊天窗 bridge 繁忙 | 短暂 retry；退出事件可取消 retry |
| 退出时折叠还在进行 | 旧进入链路失效，并尽力展开回滚 |
| 关闭重开 | 不持久化 idle tier，回到活跃态 |

## 八、剩余待办

当前功能侧剩余事项：

1. 把联调阈值切回正式 `20min / 30min / 40min`。
2. 替换正式 GIF 资源。
3. 对网页端和桌面端做最终肉眼验收，重点看 CAT2 / CAT3 停靠、拖拽跟随、hover 播放完整度。
