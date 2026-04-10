# 雀魂陪伴与讲解插件实施方案（基于当前 N.E.K.O 架构的安全版）

> 目标：把“雀魂相关能力”落成一个贴合当前仓库的长期插件方案，优先复用现有 `plugin/`、`agent_server.py`、`main_logic/`、TTS 与 Avatar 基座。
>
> 这份文档不以“伪装外挂”或“规避检测”为目标，而是收敛为屏幕感知、局内讲解、情绪陪伴、赛后复盘，以及在用户显式开启后的有限辅助操作。

---

## 1. 文档目标

本文只做五件事：

- 明确当前仓库里已经存在、可以直接复用的能力边界。
- 把原始方案里不贴合现有项目的部分，改写成真实可落地的模块拆分。
- 明确产品红线：不做内存读取、注入、抓包、反检测；允许在用户显式开启后提供有限脚本辅助，但不把它设计成规避封禁的能力。
- 保持多游戏适配与模块分离，把新增优化也设计成可复用的通用策略层，而不是雀魂专属硬编码。
- 定义一套可替换的感知 / 决策 / 表达契约，保证后续能扩展到其他游戏陪伴插件。
- 给出符合当前仓库演进节奏的分阶段实施路径。

---

## 2. 当前项目基线

这一节只写当前仓库里已经存在的事实。

### 2.1 插件基线

- 项目已经有完整的插件系统，插件运行在独立进程中，通过 `plugin/server/` 管理。
- 插件可以通过 `@plugin_entry` 暴露入口，通过 `@lifecycle` 启停，通过 `register_static_ui()` 提供独立控制面板。
- 插件可以用 `push_message(message_type="proactive_notification")` 主动把内容注入 N.E.K.O. 的主交互链路。
- 插件可以通过 `finish(reply=True)` 让宿主走正常说话链路，也可以 `reply=False` 做静默更新。

### 2.2 Agent 与电脑控制基线

- 当前 `agent_server.py` 已有 `computer_use_enabled`、`browser_use_enabled`、`user_plugin_enabled` 等总开关。
- 当前 `brain/computer_use.py` 已实现通用的截图 + VLM + `pyautogui` 执行链路。
- `ComputerUseAdapter` 已经有平滑鼠标移动包装，但它本质上仍是面向“低频 GUI 任务”的通用 Agent，不适合直接承担长时、持续、高频的游戏状态循环。
- 当前 `pyautogui.screenshot()` 与 `pyautogui` 依赖已在项目中存在；`dxcam`、`mss`、`opencv`、`onnxruntime`、`ultralytics`、麻将算法库等并未成为当前仓库的既有基础。

### 2.3 语音、角色与陪伴表达基线

- `main_logic/core.py`、`main_logic/omni_realtime_client.py`、`main_logic/tts_client.py` 已经承担角色说话、TTS、打断与主动消息投递。
- 项目已经具备 Live2D / VRM / MMD 三种 Avatar 形态，适合承接“陪伴感”和“情绪表达”。
- 仓库内已经有“人格化 / 人味”评测框架，可用于后续评估讲解文案是否像 N.E.K.O. 在说话，而不是像冷冰冰的报牌器。

### 2.4 前端基线

- 当前项目不是纯 Vue 3 单体前端。
- 实际结构是：主站页面以 `templates/` 为主，另有 `frontend/plugin-manager/` 的 Vue 面板和 `frontend/react-neko-chat/` 的 React 聊天组件。
- 因此本插件的 v1 面板更适合走“插件静态 UI”或宿主已有页面能力，而不是预设成必须新起一个 Vue 3 BFF 子系统。

### 2.5 对本方案最重要的现实约束

- `TaskExecutor` 和现有 Agent 评估链路偏“按用户请求触发”，并不适合承担一局麻将期间持续运行的主循环。
- 真正适合这个插件的运行模型，不是“把它塞进一次次 Agent task”，而是“做成一个可启停的长生命周期插件会话”。

---

## 3. 产品边界与红线

这一节是方案的硬约束。

### 3.1 明确不做的事

- 不读取游戏内存。
- 不注入 DLL、脚本或 Hook。
- 不拦截网络包。
- 不以“模拟人类操作”“反检测”“防封”为卖点或设计目标。
- 不承诺“绝对不会被封”；文档中只能写“低风险”或“尽量降低风险”。
- 不把插件包装成规避规则的工具。

### 3.2 v1 的安全定位

v1 应定位为：

- 屏幕感知驱动的雀魂陪伴与讲解插件。
- 局内建议、牌理解释、风险提醒、情绪陪伴。
- 赛后复盘、教学模式、回放模式。
- 用户显式触发或显式开启后的有限辅助操作，例如打开回放、切换讲解模式、菜单导航，以及受限场景下的半自动点击协助。

### 3.3 关于“主动操作”

- 可以保留“辅助操作层”，但必须默认关闭，并要求用户在设置中显式开启。
- 文档建议把输入辅助拆成三级能力：
  - `off`：只讲解，不操作。
  - `assist`：菜单导航、回放控制、确认弹窗、非对局关键操作。
  - `semi_auto`：用户已授权前提下，对局内的有限协助操作，例如代点已明确建议的按钮或执行用户刚确认过的动作。
- v1 不建议直接做“全自动代打”；如果要做，也应明确列为后续实验能力，而不是默认产品能力。
- 如需输入层，应优先限定在回放模式、练习模式、自定义房间，再逐步评估正式对局中的有限协助。
- 现有 `computer_use` 的平滑移动能力可以作为输入层复用件，但不能被定义为反检测方案。

### 3.4 授权与风险表达

- 所有辅助操作都必须有总开关，且默认关闭。
- 涉及局内点击的能力，至少需要：
  - 首次风险提示
  - 模式说明
  - 一键关闭
  - 操作日志
- 对外文案不应写“不会封号”，只能写：
  - 基于屏幕读取
  - 不读内存、不注入
  - 由用户主动开启
  - 风险需用户自行判断

---

## 4. 推荐产品定位

建议把这个方向正式命名为：

`N.E.K.O. 通用游戏陪伴框架：雀魂讲解与复盘特化版`

对外卖点应聚焦：

- 她会看局。
- 她会讲牌。
- 她会安抚、吐槽、鼓励。
- 她会记住你的讲解偏好和练习阶段。
- 她能在回放里陪你复盘，而不是帮你偷偷打牌。

这样既贴合 N.E.K.O. 的核心产品气质，也更符合当前仓库已经成熟的语音、Avatar、主动对话能力。

---

## 5. 面向当前仓库的推荐架构

### 5.1 总体原则

- 核心实现应优先作为一个独立插件落在 `plugin/plugins/` 下。
- 感知、决策、表达做契约解耦，但 v1 不必先拆成多个 FastAPI 微服务。
- 新增优化应优先做成通用策略模块，例如 `FrameChangeGate`、`SpeechPolicy`、`HumanOverrideGuard`、`CompanionViewModel`、`MemoryBridge`，避免写死成雀魂专属逻辑。
- 只有当性能或依赖隔离真的成为瓶颈时，再把某些重模块拆为本地 sidecar worker。

### 5.2 推荐分层

```text
Mahjong Companion Plugin
├── Session Orchestrator        # 插件主循环 / 会话状态机
├── Capture Provider            # 截图与窗口定位
├── Frame Change Gate           # 画面哈希变动检测 / 节流降耗
├── Perception Pipeline         # ROI / 识别 / 状态结构化
├── Decision Engine Adapter     # 算法库 / 本地模型 / 规则引擎
├── Companion ViewModel         # 陪伴态 UI 视图模型
├── Narration Adapter           # 把结果变成 N.E.K.O. 口吻
├── Input Safety Guard          # 用户抢鼠标中断 / 安全刹车
├── Review & Memory Bridge      # 牌谱片段、关键节点、复盘摘要、长期记忆桥
└── Plugin UI                   # 控制面板、调试页、标注页
```

### 5.3 为什么不建议一开始就做“三个网络微服务”

- 当前仓库已经有插件进程隔离，先天就是一个比较好的边界。
- 这个功能的第一阶段主要难点是识别正确率、讲解时机、文案体验，不是服务编排。
- 过早拆成多个 HTTP 服务，会引入更多部署、日志、状态同步与恢复复杂度。
- 更适合的做法是先在一个插件里把契约稳定下来，再决定是否拆 worker。

---

## 6. 核心模块设计

### 6.1 会话编排层 `Session Orchestrator`

职责：

- 启停游戏陪伴会话。
- 管理当前模式：观战 / 回放 / 教学 / 静默建议。
- 维护局内状态机：大厅、配桌、对局中、结算、回放。
- 做节流、去重和触发时机判断。
- 维护“开口阈值”和“情绪冷却时间”。

建议：

- 这个主循环应由插件自己管理，不要复用 `TaskExecutor` 的一次性任务模型。
- 控制入口通过插件 UI 和 `plugin_entry` 暴露，例如：
  - `start_session`
  - `stop_session`
  - `set_mode`
  - `capture_debug_frame`
  - `run_replay_review`

建议在这一层内置通用 `SpeechPolicy`：

- 常规牌：默认走 `silent_ui`，只更新面板，不播报。
- 关键牌：当向听前进、进张显著改善、出现高价值路线时，按概率触发鼓励型 `voice`。
- 高危牌：当系统明确判断“用户即将做出高风险操作”时，无视普通冷却时间，直接触发警告型 `voice`。
- 连续两次普通语音之间应有冷却时间，建议默认 `15-20s`，并允许按游戏 profile 调整。

建议把这套策略抽成独立配置，而不是写死在雀魂逻辑里：

```json
{
  "speech_policy": {
    "normal_channel": "silent_ui",
    "key_play_voice_probability": 0.3,
    "normal_voice_cooldown_sec": 18,
    "danger_override_cooldown": true
  }
}
```

### 6.2 截图层 `Capture Provider`

建议先定义接口，而不是先绑定 `dxcam`：

```python
class CaptureProvider(Protocol):
    def locate_window(self) -> WindowInfo: ...
    def capture_frame(self) -> FramePacket: ...
```

`FramePacket` 建议至少包含：

```json
{
  "timestamp_ms": 0,
  "window_title": "",
  "width": 0,
  "height": 0,
  "image_path": "",
  "source": "pyautogui"
}
```

实现策略建议：

- v0 / v1 调试阶段：优先复用现有依赖，用 `pyautogui.screenshot()` + 活跃窗口裁切跑通链路。
- Windows 性能优化阶段：再按可选依赖接入 `dxcam`。
- 如果未来需要跨平台更稳的窗口截图，再补 `mss` 或平台专用 provider。

关键判断：

- 雀魂是回合制，不需要 30 FPS 的重型实时视觉栈。
- v1 完全可以做成低频采样或“状态变化触发”采样，先以稳定和简单为主。

建议在 `Capture Provider` 和 `Perception Pipeline` 之间增加一个通用 `FrameChangeGate`。

职责：

- 对一小块或几小块关键 ROI 做极轻量图像哈希。
- 如果哈希未变化，直接丢弃当前帧，不进入 OCR、模板匹配或分类器。
- 只在检测到实质变化时，才触发状态更新。

为什么要单独抽象：

- 它本质上是“感知前门控层”，不属于雀魂专属逻辑。
- 换到别的回合制或半静态 UI 游戏时，只需替换 ROI 配置与 hash 策略。

建议契约：

```python
class FrameChangeGate(Protocol):
    def should_process(self, frame: FramePacket) -> bool: ...
```

建议配置：

```json
{
  "frame_change_gate": {
    "enabled": true,
    "watch_regions": ["action_buttons", "center_table"],
    "hash_method": "dhash",
    "min_change_distance": 3,
    "stable_skip_limit": 300
  }
}
```

对雀魂的直接收益：

- 静默待机时几乎不耗额外算力。
- 非用户回合和无按钮变化阶段不必反复跑识别。
- 更适合长期后台陪伴。

### 6.3 感知层 `Perception Pipeline`

这里要和原方案做一个重要收敛：

- 不建议一上来就把 v1 前提写成“必须训练 YOLOv10 专属模型”。
- 当前仓库没有现成 CV 训练 / 推理基座，也没有相关依赖链。
- 雀魂 UI 相对规则，MVP 更适合先走“固定 ROI + 模板匹配 / OCR / 小分类器”的路线。
- 在进入 OCR / 分类前，应优先经过 `FrameChangeGate` 节流，避免静态画面重复分析。

推荐分三档：

1. `P0`：固定分辨率或校准后 ROI 截取，先识别局面阶段、自己的手牌区、按钮区。
2. `P1`：针对牌面做轻量分类器或模板库，解决常见牌识别。
3. `P2`：当布局鲁棒性不足时，再引入 YOLO / ONNX 推理模块。

感知输出必须是纯结构化状态，不夹带策略：

```json
{
  "scene": "match_turn",
  "confidence": 0.94,
  "round_wind": "east",
  "seat_wind": "south",
  "hand_tiles": ["1m", "2m", "3m"],
  "melds": [],
  "dora_indicators": ["5p"],
  "buttons": ["discard", "chi", "pon", "riichi"],
  "riichi_players": ["west"],
  "raw_detections": []
}
```

### 6.4 决策层 `Decision Engine`

这一层必须是可热替换接口，且与表达层彻底分开。

建议契约：

```python
class DecisionEngine(Protocol):
    def suggest(self, state: PerceivedGameState) -> DecisionResult: ...
```

`DecisionResult` 建议至少包含：

```json
{
  "version": "engine-v1",
  "recommendations": [
    {
      "type": "discard",
      "tile": "5p",
      "confidence": 0.81,
      "reason_codes": ["ukeire_best", "riichi_defense_ok"]
    }
  ],
  "risk_level": "medium",
  "teaching_points": [
    "这手优先保留两面搭子"
  ],
  "engine_meta": {
    "engine": "rule_based",
    "latency_ms": 22
  }
}
```

实现顺序建议：

- v1：先接规则 / 算法引擎，不依赖外部在线模型。
- v2：允许接本地模型服务。
- v3：再评估是否切自研模型。

这能满足“未来换成自研模型或麻将算法、且不依赖别人”的方向，但不会让 v1 被训练成本卡死。

### 6.5 表达层 `Narration Adapter` 与 `Companion ViewModel`

这里也建议收掉 `DSPy` 前置。

原因：

- 当前仓库已有成熟 prompt 体系和角色说话链路。
- v1 需要的是“稳定口吻 + 可配置讲解深度 + 合适介入时机”，不是先引入新的提示词编排框架。

推荐做法：

- 在插件内部维护少量可版本化模板：
  - 简短提示
  - 教学解释
  - 情绪安抚
  - 复盘总结
- 再把 `DecisionResult` 映射到 N.E.K.O. 风格的话术。
- 真正发声时复用宿主的主动消息 / TTS / Avatar 动作能力。

建议输出层区分三种通道：

- `voice`: 允许猫娘出声。
- `subtitle`: 只显示简短提示。
- `silent_ui`: 只更新插件面板，不打断用户。

同时建议把“原始决策结果”和“用户实际看到的陪伴界面”明确拆开。

- `DecisionResult` 是硬逻辑输出，供日志、调试、策略层消费。
- `CompanionViewModel` 是陪伴式 UI 输出，强调猫娘怎么看待局面，而不是把冷冰冰指标全部直接拍给用户。

建议 `CompanionViewModel` 只默认展示：

- 当前心情态
- 一两句核心提示
- 是否建议保守 / 进攻
- 是否需要主人留意风险

而把向听、进张、胜率、危险度等硬核数据折叠到 `调试 / 详情` 面板。

建议契约：

```json
{
  "mood_state": "nervous",
  "headline": "这巡先别贪，外面有点危险哦。",
  "posture": "defense",
  "detail_collapsed": true
}
```

### 6.6 行动层 `Input Action Adapter`

这一层在本方案里是受限模块，但可以作为高阶卖点预留。

建议边界：

- 默认只开启 `assist` 级能力：菜单导航、回放控制、面板快捷操作。
- `semi_auto` 级能力可以预留接口，但必须受总开关、场景白名单和确认策略控制。
- 如需复用现有能力，可封装 `brain/computer_use.py` 里的平滑输入包装。
- 不把任何输入模拟描述为“防封策略”。

在此基础上，建议增加物理级安全刹车 `HumanOverrideGuard`：

- 当插件执行平滑鼠标移动或短时自动点击时，临时开启全局鼠标监听。
- 如果在这 `1-2s` 的执行窗口内检测到真实用户的物理鼠标位移，立刻中断当前自动化动作。
- 中断后可触发轻量反馈，例如字幕或语音：“啊，主人要自己点吗？那本猫让给你~”。

这层的价值不只是安全：

- 它是最后一道误操作防线。
- 它能显著减少“脚本抢鼠标”的体验问题。
- 它同样是通用游戏陪伴基座里可复用的安全策略模块。

建议增加输入策略配置：

```json
{
  "action_mode": "assist",
  "require_user_opt_in": true,
  "require_first_run_warning": true,
  "allowed_contexts": ["menu", "replay", "custom_room"],
  "allow_ranked_match_actions": false,
  "operation_log_enabled": true,
  "human_override_abort": true
}
```

推荐接口：

```json
{
  "action": "open_replay_next",
  "requires_user_confirmation": true,
  "allowed_contexts": ["replay", "menu"]
}
```

### 6.7 复盘层 `Review & Memory Bridge`

复盘层不应只把结果留在插件日志里。

建议拆成两层：

- `Review Logger`：保存局内关键节点、建议、用户实际操作与结果。
- `MemoryBridge`：把跨局有价值的摘要标签注入 N.E.K.O. 主记忆系统。

适合注入长期记忆的内容：

- 用户是否长期偏进攻或偏保守
- 是否经常在高危险局面贪大牌
- 是否对某类讲解更有反应
- 最近几局的典型失误或高光

建议只注入“低频、高价值、可概括”的标签化摘要，而不是整局流水。

示例：

```json
{
  "memory_bridge": {
    "enabled": true,
    "max_memories_per_day": 3,
    "summary_tags": ["mahjong_style", "risk_preference", "recent_mistake_pattern"]
  }
}
```

这样做的直接收益是：

- N.E.K.O 可以跨局记住用户的麻将习惯。
- 第二天闲聊时，她可以自然提到前一晚的打法问题或亮眼表现。
- 产品形态从“报牌工具”进一步靠近“长期陪伴者”。

---

## 7. 与现有 N.E.K.O. 模块的映射关系

| 需求 | 当前可复用模块 | 建议做法 | 注意点 |
| --- | --- | --- | --- |
| 插件生命周期 | `plugin/` | 直接做独立插件 | 不要走一次性 Agent task |
| 控制面板 | `register_static_ui()` | 插件自带静态 UI | 不必预设 Vue 3 |
| 感知前节流 | 插件内轻量模块 | 新增 `FrameChangeGate` | 应做成 profile 驱动，不绑雀魂 |
| 主动讲话 | `push_message()` / `finish(reply=True)` | 用现有说话链路 | 需要节流，避免刷屏 |
| 说话策略 | 插件内策略层 | 新增 `SpeechPolicy` | 要和业务决策解耦 |
| Avatar 表达 | 宿主现有 Live2D / VRM / MMD | 作为最终输出容器 | 插件不重复造角色层 |
| 陪伴式面板视图 | 插件 UI + 宿主 Avatar | 新增 `CompanionViewModel` | 默认少数据、多情绪态 |
| 辅助操作输入层 | `brain/computer_use.py` 的现有输入包装 | 先做 `assist`，后留 `semi_auto` 接口 | 默认关闭，必须用户显式开启 |
| 物理安全刹车 | 插件内输入保护层 | 新增 `HumanOverrideGuard` | 用户抢鼠标应立刻中断 |
| 人设语气 | `config/prompts_*` 体系 | 插件局部模板 + 宿主口吻 | v1 不强依赖 DSPy |
| 跨局记忆 | 宿主记忆系统 | 新增 `MemoryBridge` | 只写摘要，不写流水噪声 |
| 人味评估 | `tests/utils/human_like_judger.py` 等 | 评测讲解文案 | 可直接复用测试思路 |

---

## 8. 推荐目录结构

```text
plugin/plugins/mahjong_companion/
├── __init__.py
├── plugin.toml
├── contracts.py
├── orchestrator.py
├── session_state.py
├── capture/
│   ├── base.py
│   ├── pyautogui_capture.py
│   └── dxcam_capture.py          # 可选，后续再加
├── gating/
│   └── frame_change_gate.py
├── perception/
│   ├── pipeline.py
│   ├── roi_specs.py
│   ├── tile_classifier.py
│   └── scene_detector.py
├── decision/
│   ├── base.py
│   ├── rule_engine.py
│   └── local_model_adapter.py
├── narration/
│   ├── companion_view_model.py
│   ├── speech_policy.py
│   ├── templates.py
│   └── formatter.py
├── action/
│   ├── input_adapter.py
│   └── human_override_guard.py
├── review/
│   ├── logger.py
│   ├── memory_bridge.py
│   └── summarizer.py
├── static/
│   ├── index.html
│   ├── main.js
│   └── style.css
└── data/
    ├── config.json
    ├── calibration.json
    └── fixtures/
```

---

## 9. 分阶段实施建议

### 阶段 0：先定红线与契约

- 先把“不做反检测、不承诺不封”写进产品与技术文档。
- 定义 `FramePacket`、`PerceivedGameState`、`DecisionResult` 三套契约。
- 定义输入辅助分级：`off / assist / semi_auto`。
- 搭插件骨架、控制面板和日志面板。

完成标志：

- 插件能启动。
- UI 能开关会话与模式。
- 能保存基础配置与校准信息。

### 阶段 1：打通低频截图与调试闭环

- 用现有 `pyautogui` 依赖跑通窗口定位、截图、裁切和调试保存。
- 同时接入 `FrameChangeGate`，先用最小可行的 ROI 哈希门控降低空转功耗。
- 做“手动抓一帧并分析”的 debug 页面。
- 不急着上高帧率。

完成标志：

- 可以从插件 UI 一键采集当前雀魂窗口截图。
- 可以保存样本供后续标注与单元测试使用。
- 静态局面下大部分帧会被门控层直接跳过。

### 阶段 2：先做规则化感知，不先做 YOLO 大跃进

- 先识别局面阶段、自己手牌区、操作按钮区。
- 优先解决“什么时候该说话”和“识别结果是否稳定”。
- 只在布局鲁棒性不足时再引入目标检测模型。

完成标志：

- 对固定测试样本能稳定输出结构化状态。
- 识别错误可通过日志和样本回放定位。

### 阶段 3：接本地算法决策

- 接入规则 / 算法引擎，先支持最基础的出牌建议与风险提示。
- 输出统一的 `DecisionResult`，不要让讲解模板直接依赖底层库的私有返回值。

完成标志：

- 能对若干标准局面输出稳定建议。
- 决策引擎可以被 mock 替换。

### 阶段 4：把结果变成“像 N.E.K.O. 在陪你”

- 建立简短提示、教学模式、安抚模式三套话术层。
- 建立 `SpeechPolicy`，明确开口阈值、关键牌概率播报与危险牌强提醒。
- 建立 `CompanionViewModel`，让默认面板优先展示情绪态与核心提示，而不是裸数据。
- 通过 `push_message(message_type="proactive_notification")` 或 `finish(reply=True)` 接入现有说话链路。
- 复用现有字幕 / 语音 / Avatar 表达，不单独造一套播报系统。

完成标志：

- 能在不刷屏的前提下说出建议。
- 语气风格接近当前角色设定，而不是工具播报。
- 默认 UI 呈现更像“猫娘陪看局”，而不是调试面板。

### 阶段 4.5：接入有限辅助操作

- 先接 `assist` 级能力，不直接上 `semi_auto`。
- 增加设置页总开关、首次风险提示、上下文白名单和操作日志。
- 增加 `HumanOverrideGuard`，确保用户一抢鼠标就能中断插件动作。
- 先在回放 / 自定义房间 / 菜单场景打通“建议 -> 确认 -> 执行”闭环。

完成标志：

- 用户可以显式开启或关闭辅助操作。
- 所有输入动作都有日志可查。
- 受限场景下可以稳定完成有限点击协助。
- 用户物理输入可以稳定触发中断，不会被脚本抢夺控制权。

### 阶段 5：赛后复盘与回放模式

- 沉淀局内关键节点日志。
- 输出复盘摘要、高光时刻、风险回顾。
- 将低频高价值的打法标签通过 `MemoryBridge` 注入宿主长期记忆。
- 先把回放 / 教学模式做强，再考虑更激进的实时能力。

完成标志：

- 一局结束后可以自动生成一段复盘。
- 关键节点与建议可以回看。
- 跨局闲聊时可以引用用户近期麻将风格与典型失误。

### 阶段 6：抽象成通用游戏陪伴基座

- 把与雀魂无关的部分抽成 `CaptureProfile`、`PerceptionProfile`、`NarrationProfile`。
- 未来拓展到其他游戏时，只替换 profile、识别器和决策引擎。

---

## 10. 测试与验收建议

### 10.1 单元测试

- `FrameChangeGate` 的哈希稳定性与误判率测试。
- 固定截图样本测试感知输出。
- 决策契约测试。
- Narration 模板测试，避免输出过硬、过长或风格跑偏。
- `SpeechPolicy` 的开口阈值、冷却时间和危险牌强提醒测试。

### 10.2 插件测试

- 插件入口契约测试。
- 静态 UI 可用性测试。
- 会话启停、配置保存、日志导出测试。
- 输入辅助开关、场景白名单、风险提示与操作日志测试。
- `HumanOverrideGuard` 的物理输入中断测试。
- `MemoryBridge` 的写入频控与摘要质量测试。

### 10.3 人格化评测

- 复用现有 `human_like_judger` 思路，评估：
  - 是否像陪伴式讲解
  - 是否太像“报牌器播音”
  - 是否会过度打扰
  - 是否能在逆风 / 顺风场景下稳定保持角色感

### 10.4 手动验收

优先验收顺序：

1. 回放模式
2. 教学模式
3. 观战模式
4. 菜单辅助

不建议把“实时对战辅助”作为第一验收目标。

---

## 11. 关键风险

- 最大难点不是“说得像猫娘”，而是“感知结果是否稳定到足以值得说”。
- 如果识别错误率高，陪伴体验会立刻退化成噪声。
- 如果 `FrameChangeGate` 过于激进，可能漏掉关键局面变化，造成状态滞后。
- 如果介入时机不做节流，角色会显得碎嘴和抢话。
- 如果 `SpeechPolicy` 调得太保守，用户会觉得她存在感太弱；调得太激进，又会变回播音员。
- 如果输入辅助边界不清，功能会快速滑向高风险区域。
- 如果长期记忆注入不做摘要筛选，宿主记忆会被麻将流水噪声污染。
- 如果一开始就上 YOLO、模型训练、输入模拟、多服务拆分，项目复杂度会远超当前仓库的增量承受范围。
- 如果把方案继续往“伪装外挂”方向推，产品风险和合规风险都会迅速上升。

---

## 12. 最终建议

基于当前 N.E.K.O 仓库，最合理的落地路径不是：

- 先做高频视觉微服务
- 先做全自动代打
- 先做反检测输入模拟

而是：

1. 先做一个长生命周期插件。
2. 先跑通低频截图、`FrameChangeGate`、状态结构化、建议生成、陪伴表达。
3. 先把回放 / 教学 / 复盘、`SpeechPolicy`、`CompanionViewModel` 和 `assist` 级辅助操作做好。
4. 再按契约逐步替换截图实现、感知模型、决策引擎、更高阶输入层与长期记忆桥。

这样既能保住“雀魂陪伴与讲解”这个亮点，也真正贴合当前项目已有的插件、语音、Avatar 和 Agent 能力。
