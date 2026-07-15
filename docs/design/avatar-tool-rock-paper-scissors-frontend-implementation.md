# Avatar 猜拳道具前端与桌面端实施方案

本文把 `docs/design/avatar-tool-rock-paper-scissors-design.md` 的产品与协议要求，落实到当前 NEKO 和 NEKO-PC 已重构的道具交互链路。本文是实施计划与验收门禁，不代表 `rps` 已经实现，也不授权迁入旧 `guess` 代码。

## 文档职责与优先级

1. 用户在本轮最新确认的产品范围高于既有文档；发生冲突时按最新确认执行，并在本文显式记录覆盖关系。
2. 除下述 Full/Compact 入口范围外，产品规则、时序、payload 与体验目标以 `docs/design/avatar-tool-rock-paper-scissors-design.md` 为准。
3. 本文负责当前代码中的落点、实施顺序、文件边界和验证归属。
4. 早期迁移交接中的有效风险已经吸收到本文；旧分支、旧提交、旧资源位置和旧架构描述不能覆盖当前代码。
5. 当前代码、测试和可复现运行证据高于文档中的历史事实。
6. Host、Python、prompt 与 memory 的具体实现属于独立后端阶段；本文只写前端和桌面端必须遵守的边界及联合门禁。

### 产品入口范围

用户最新确认：猜拳及后续新道具只加入 Compact Chat 自己的道具库和三槽替换体系；Full Chat 不再更新新道具内容。这一决定覆盖早期方案中“Full 与 Compact 都显示 RPS”的旧条款，但不改变两端底层 profile、协议和桌面 runtime 的统一原则。后续应另行同步 design 的入口描述，本轮只更新本文。

当前 Full 实际显示三个道具，是因为正式 registry 目前也只有三个；代码上 `AVAILABLE_AVATAR_TOOLS` 仍被 Compact、Full 和通用表现查询共同消费，并不是独立固定列表。为落实最新范围且不做逐工具特判，正式注册前应一次性建立由现有三个定义组成的 Full UI 列表，让 `FullChatSurface` 改为消费该列表；Compact 的可替换库和 runtime/presentation 查询继续消费完整 registry。以后增加新道具只扩 Compact 库，不再修改 Full，也不新增 surface capability 或 `toolId === 'rps'` 过滤。

## 当前代码事实

### 仓库与实现状态

1. NEKO 当前工作分支为 `codex/avatar-tool-rock-paper-scissors`；工作区已有用户确认过的图片路径迁移、相关引用和测试修改，必须保留。
2. NEKO-PC 当前工作分支同为 `codex/avatar-tool-rock-paper-scissors`，当前工作区干净。
3. 两仓目前都没有 `rps` definition、round-choice controller、RPS payload 或桌面呈现确认实现；正式 registry 仍只有三个旧道具。
4. `docs/design/avatar-tool-interaction-design-and-maintenance.md` 仍是三个旧道具的维护基线；RPS 最终启用时再按真实实现同步，不能提前把计划写成已完成事实。

### 已准备但尚未接入的资源

六张 RPS 图片已位于统一图片根：

```text
/static/assets/avatar-tools/rps/rps_rock.png
/static/assets/avatar-tools/rps/rps_rock_cursor.png
/static/assets/avatar-tools/rps/rps_scissors.png
/static/assets/avatar-tools/rps/rps_scissors_cursor.png
/static/assets/avatar-tools/rps/rps_paper.png
/static/assets/avatar-tools/rps/rps_paper_cursor.png
```

当前工作区已把这六条路径加入 React Chat 资源版本闭包。实施时不得把图片放回 `static/icons`、在 NEKO-PC 复制一份，或产生带重复 `?v=` 的 URL。

工作区还存在三段未跟踪的 RPS 音频候选文件，但目前没有 catalog 引用、版本闭包或消费者接线。本文不授权移动、重命名或删除它们；只有确认采用、来源/授权和声明式 sound recipe 后，才能把它们接入实现。不能把“文件存在”写成“音效已经迁移完成”。

更大范围的旧道具图片整理属于当前已有工作，不应在本实施文档中声称所有图片都是原样移动，也不应借 RPS 实施再次重排无关资源。

### 当前真实链路

Web 单窗口：

```text
Compact 选择
  -> NEKO catalog / UI projection
  -> useAvatarToolRuntime
  -> shared pointer/bounds/click guard
  -> profile handler / session controller
  -> AvatarToolVisuals
  -> protocol payload
  -> Host
```

Electron 桌面：

```text
Compact 选择
  -> NEKO 只发布静态 descriptor + strict desktop contract
  -> PC surface lifecycle / owner lease
  -> Pet domain runtime（唯一桌面回合 owner）
  -> Pet inline 表现 + main visual overlay
  -> 实际 renderer 呈现确认回 Pet
  -> interaction output
  -> Host
```

Electron Chat 不运行本地猜拳 controller，不向 Pet 转发普通 Mac/Windows pointer，也不携带 live presentation revision。Full 与 Compact 的 renderer session 不需要共享同一个 React 实例；Compact Web 使用共享实现，桌面唯一状态属于 Pet。

## 完成标准

实施完成必须同时满足：

1. Compact library 可以发现 `rps`，用户可以用它替换三个槽位中的任意一个；默认三槽不变。
2. Full Chat 不作新功能改造；现有三个入口及其行为只作为回归基线保留。
3. Web 由 NEKO runtime 执行；Electron Chat 只发布 descriptor，Pet runtime 执行唯一桌面回合。
4. 用户实际看到的 confirmed 手势与 down 快照、胜负、本地表现和 payload 完全一致。
5. 首帧及每个新 proposal 未被真实 renderer 确认前都不能开局；图片失败时 fail closed，声音失败只静音。
6. 启动或重载后首次选择立即开始资源准备；资源 ready 后靠近模型立即放大且不穿透，不依赖十几秒预热恢复。
7. Mac/Windows 系统光标始终可见；跟随图片只是伴随视觉，overlay 不捕获鼠标。
8. Linux/Niri 只沿用现有受校验的 owner/host boundary；不把平台私有逻辑灌入通用 controller。
9. 取消、切换、handoff、失活和销毁不会留下 timer、音频、DOM、proposal、ack、press 或旧 generation 回调。
10. 棒棒糖、猫爪、锤子的规则、视觉、声音、payload、默认槽位和桌面行为不退化。

## 目标分层与文件职责

### NEKO：事实源、Web 执行与桌面投影

| 文件 | 实施职责 |
|---|---|
| `frontend/react-neko-chat/src/avatar-tools/catalog.ts` | 扩展 NEKO ID 闭集、reserved fields、round-choice profile、真实 round-reveal effect 与 RPS definition；最后才加入正式 registry。 |
| `frontend/react-neko-chat/src/avatar-tools/profileInterpreter.ts` | 显式、穷尽地解释 profile discriminant；只提供无状态规则或 session controller factory，不保存回合状态。 |
| `frontend/react-neko-chat/src/avatar-tools/roundChoice.ts` | 允许新增的唯一通用领域模块；实现纯回合、proposal/confirm、down snapshot、result、streak 与 phase，不依赖 DOM、React、IPC 或具体页面。 |
| `frontend/react-neko-chat/src/avatar-tools/interaction.ts` | 保留共享 policy、bounds、UI exclusion、pointer identity、严格 `>6px` drag 和 fresh release guard；不复制 RPS 命中体系。 |
| `frontend/react-neko-chat/src/avatar-tools/runtime.ts` | Web session owner；注入 clock/random/scheduler，连接共享 click guard、round controller、present ack、commit 与 disposer。Electron Chat 继续只发布 descriptor。 |
| `frontend/react-neko-chat/src/avatar-tools/presentation.tsx` | 图片预热、稳定视觉节点、head/reveal/live region、Web load/decode/stable-frame ack 和声明式 effect 执行；不计时、不随机、不判胜负。 |
| `frontend/react-neko-chat/src/styles.css` | 固定 contain 外框、稳定 anchor、结果层、reduced motion 与不拦截输入的视觉样式；构建产物不得手改。 |
| `frontend/react-neko-chat/src/avatar-tools/protocol.ts` | 从 profile/definition 派生 RPS exact payload 校验；在一次实际 commit 中原子生成唯一 interaction id。 |
| `frontend/react-neko-chat/src/avatar-tools/desktopContract.ts` | 严格投影 round-choice、round-reveal、资源闭包和 fingerprint；未注册阶段可直接投影 RPS fixture。 |
| `frontend/react-neko-chat/src/avatarTools.ts` | 保留完整 registry 投影供 Compact/runtime 使用，并增加由现有三个定义组成的稳定 Full UI 投影；处理 Compact 三槽持久化，不增加逐工具 surface 判断。 |
| `frontend/react-neko-chat/src/App.tsx` | Compact 的选择、装备、清除和共享 runtime 接线；不保存玩法状态。 |
| `frontend/react-neko-chat/src/FullChatSurface.tsx` | 只做一次列表来源切换，消费稳定的旧三 Full UI 投影；不接 RPS 视觉、controller、状态或后续新道具。 |
| `static/avatar/avatar-reaction-bubble.js` | 现有公开 head anchor API 的提供者；RPS 只消费，不修改其生命周期。 |
| `main_routers/pages_router.py` | React Chat 资源版本闭包；只维护实际使用的同源资源路径。 |

`message-schema.ts` 若只需暴露类型，应继续重导出 `protocol.ts` 的事实，不建立第二套 schema。

### NEKO-PC：strict consumer、Pet domain 与真实 renderer ACK

| 文件 | 实施职责 |
|---|---|
| `src/desktop-avatar-tools/contract.js` | 增加 round-choice/effect/reserved field 的 exact 校验；PC 的 tool id 继续是 opaque 合法标识，禁止新增 RPS 白名单。 |
| `src/desktop-avatar-tools/runtime.js` | 桌面唯一回合 owner；复用范围、press/release 和 session lifecycle，贯通 injected clock/random/scheduler。 |
| `src/desktop-avatar-tools/interaction-output.js` | 从 immutable round snapshot 派生 live visual proposal、payload、声音和 effect；不重新随机或判胜负。 |
| `src/desktop-avatar-tools/surface-lifecycle.js` | 保持通用 lease/handoff/reload 语义；原则上不写 RPS 分支，只补必要回归。 |
| `src/preload/bridges/pet-avatar-tool-adapter.js` | 接真实 Pet 输入，创建稳定 head/reveal 节点、预热资源、播放输出并桥接实际呈现 ACK；不拥有玩法 timer/random/result。 |
| `src/preload/bridges/pet-input-region-bridge.js` | 最小扩展 typed geometry port，归一化公开 head anchor；不把玩法塞进这个大型 bridge。 |
| `src/ipc-channels.js` | 增加 main→Pet 的语义化 presentation state 通道，承载 `pending/invalidated/confirmed` 或等价状态；overlay renderer→service 继续使用 service-local IPC，不使用版本化文件名。 |
| `src/main.js` | 接 overlay service 的已验证回调，重验当前 descriptor identity 与 Pet target，再把 presentation state 转给 Pet；不直接承担 renderer sender/display 映射，也不运行玩法。 |
| `src/avatar-tool-visual-overlay-service.js` | 保留 render identity；负责把 renderer `event.sender` 映射到当前 overlay entry/display，校验 exact token，并在 display/owner 变化时先发 invalidated，再通过 configure callback 通知 main。 |
| `src/preload/entries/avatar-tool-visual-overlay.js` | `<img>` load/decode、可见样式与 transform 应用、稳定帧后回传 exact revision/token；错误或被替换状态不得确认。 |
| `src/ipc-router.js` | 复用当前 visual state/lease 身份验证；不增加 RPS 玩法分支。 |
| `src/main/avatar-tool-visual-ownership.js` | 这是 Niri/Wayland 专属 visual lease；只有 Niri 真 owner ACK 需要时才最小扩展，不能承载 Mac/Windows 通用 overlay ACK。 |
| `src/avatar-tool-wayland-overlay-helper.js`、`src/main/avatar-tool-wayland-overlay-bridge.js` | 仅当 Niri 的真实 render owner 需要等价 ACK 时隔离扩展；否则该路径 fail closed。 |

Chat 的 avatar-tool bridge、Full/Compact surface bridge 已是通用 descriptor/lease 链路，默认只做回归，不加 RPS 私有消息或 pointer 转发。

## 文件与模块边界

1. “每个道具独立”指 definition、profile、资源引用和领域语义独立，不是每个道具或手势一个文件。
2. NEKO 最多新增一个通用 `roundChoice.ts` 及其直接测试；不建 `rps.ts`、`tools/rps/*`、页面 Hook 或版本化 contract 文件。
3. NEKO-PC 优先在现有 `contract/runtime/interaction-output/surface-lifecycle` 四层内实现；只有现有文件确实无法保持可读时，才考虑一个通用 round-choice 模块，不能建 `tools/rps` 目录。
4. 页面和 adapter 只接线。timer、random、relation table、streak 和 round snapshot 不能散落在 `App.tsx`、`FullChatSurface.tsx`、Pet adapter 或 main。
5. 不迁入旧 `guess` runtime、Hook、页面状态机、PC tools 目录或旧测试套件。
6. `main_logic/activity/activity_guess_gate.py` 是上游“用户活动推测”模块，与猜拳道具无关，不得当作旧猜拳残留删除。

## 数据契约

### Round-choice profile

最终字段按现有 catalog strict 风格落地，语义至少完整表达：

```ts
type RoundChoiceProfile = {
  kind: 'round-choice-v1';
  actionId: 'play';
  choices: readonly [
    { id: 'rock'; variant: 'primary'; beats: 'scissors' },
    { id: 'scissors'; variant: 'secondary'; beats: 'paper' },
    { id: 'paper'; variant: 'tertiary'; beats: 'rock' },
  ];
  cycle: {
    outsideMs: 240;
    inRangeMs: 1200;
    opponentPreviewMs: 200;
  };
  round: {
    revealMs: 1000;
    cooldownMs: 1500;
  };
  streak: {
    windowMs: 5000;
    maxCount: 99;
    rapidFrom: 2;
    burstFrom: 4;
    resetPolicy: {
      sessionReset: readonly [
        'reselect', 'clear', 'tool-switch', 'tutorial-takeover',
        'surface-handoff', 'destroy', 'model-unavailable', 'identity-change',
      ];
      transientCancel: readonly ['blur', 'hidden'];
    };
  };
  result: {
    userWin: 'user_win';
    nekoWin: 'neko_win';
    draw: 'draw';
  };
  payload: {
    playerGestureField: 'playerGesture';
    nekoGestureField: 'nekoGesture';
    resultField: 'roundResult';
    streakField: 'streakCount';
  };
  feedback: {
    confirmSound: string;
    userWinSound: string;
    otherResultSound: string;
    revealEffect: string;
  };
};
```

约束：

1. 三个 choice、三个 variant 和 beats 环必须 exact、唯一且闭合；result 只允许 `user_win/neko_win/draw`。
2. `feedback.revealEffect` 必须引用 definition `effects` 中真实可执行的 round-reveal recipe；不能为过 strict closure 添加假 effect。
3. sound/effect 的具体 ID 是 definition 内声明，不在 runtime 写路径。
4. profile/controller 不预生成 `interactionId`。
5. mutable controller 必须由每个 runtime session 创建，不能挂在 module-load 时建立的 registration singleton。

### Presentation state 与 round snapshot

```text
requestedVariant + requestedRevision
  -> actual render owner load/decode + applied visible state + stable frame
  -> confirmedVariant + confirmedRevision
```

每次最多一个 proposal in flight。controller 至少区分：

- `phase`: `ready | round_reveal | round_cooldown`
- 当前 range mode 与下次周期时间
- requested/confirmed variant 与 revision
- proposal 是否在途
- down 时冻结的 confirmed presentation snapshot
- immutable round snapshot
- 本 session 的 streak 时间与饱和计数

round snapshot 只包含双方手势、结果、streak、intensity、点击坐标/必要上下文，不保存第二套 `interactionId`。Web 的 `protocol.ts` 在 build/emit 时生成一次 ID；PC 沿用当前 output/adapter 提交边界，一次 consume 只传入一个新 ID。

### Interaction payload

```json
{
  "interactionId": "avatar-int-example",
  "toolId": "rps",
  "actionId": "play",
  "target": "avatar",
  "intensity": "normal",
  "pointer": { "clientX": 320, "clientY": 240 },
  "timestamp": 1730000000000,
  "playerGesture": "rock",
  "nekoGesture": "scissors",
  "roundResult": "user_win",
  "streakCount": 1
}
```

必须联合校验：

1. `playerGesture`、`nekoGesture`、`roundResult`、`streakCount` 对 `rps/play` 必填，对旧三道具禁止。
2. RPS 禁止 `touchZone/touch_zone`、`rewardDrop/reward_drop`、`easterEgg/easter_egg`；即使值为 `false`、空字符串或 `null`，字段存在就拒绝。
3. `streakCount` 是数值上为整数的有限 JSON number，范围 `1..99`；拒绝布尔、字符串、1.5、NaN 和 Infinity，`1.0` 与 `1` 等价。
4. `1 -> normal`、`2..3 -> rapid`、`4..99 -> burst` 是联合不变量。
5. 九种手势组合各自只有一个合法结果；十八种矛盾结果全部拒绝，不自动纠正。
6. 四个 RPS 字段及 snake_case 对应名进入 NEKO、PC、Host/Python 的 reserved field 规则；旧 chance profile 不得占用。
7. 不借此改变旧三个道具已有的兼容输出。

### 版本策略

PC 当前 wire/definition/policy v1 可以继续使用的前提是：只增加安全 fail-closed 的 profile/effect discriminant 和 reserved fields，不改变既有 exact record 的必填形状，并用旧消费者拒绝 fixture 证明。若实现必须改变公共必填结构，则只提升受影响版本并补齐消费者兼容测试。

版本只作为 wire 数据 discriminant；禁止创建 `contract-v1.js`、`runtime-v1.js` 等版本化文件。

## 关键运行时语义

### 周期与真实呈现确认

1. 激活时一次预热六张图片；循环只切换缓存资源。
2. 范围外每 `240ms`、范围内每 `1200ms` 提出下一用户手势；角色准备手势范围内每 `200ms` 切换。
3. 进入/离开范围时保留最后 confirmed 手势并重新计时，不立即跳到用户未看到的新手势。
4. 同一 variant 从 pointer 切到 in-range icon 也必须产生新 revision，因为实际资源和显示形态已经改变。
5. pending proposal、图片错误、owner/display 切换或 revision 不一致时，down 必须 fail closed；不能退回上一 confirmed 手势开局。
6. in-range scale 只在 definition 表达一次 `0.6`；不得同时缩 display box 和 transform 造成二次缩放。

Web ACK：`presentation.tsx` 先处理缓存命中（`img.complete && img.naturalWidth > 0`），再在可用时等待 `decode()`，确认当前 src/transform/visible 状态已应用并跨过至少一个稳定 RAF 后回调 runtime。未缓存图片走正常 load/decode 路径。回调必须校验 session generation 和 revision；unmount、错误、src 被替换或迟到回调不确认。

桌面 ACK 与现有 descriptor watchdog 是两件事：

- descriptor watchdog 只证明 Pet 通过 `PET_CHANNELS.AVATAR_TOOL_VISUAL_STATE` 对匹配 generation/lease 发布过 visual state，保持现状。
- presentation state 必须由实际 render owner 推进。Mac/Windows overlay renderer 回传 exact render identity；overlay service 负责校验 renderer sender、entry、display 和 token，main 只重验当前 tool/fingerprint/desktop generation/surface lease 与 Pet target，再转给 Pet controller。
- main→Pet 使用同一语义状态链传递 `pending/invalidated/confirmed` 或等价事件。overlay service 检测 display/renderer owner 变化时必须先 invalidated，使 Pet 立即撤销旧 confirmed，然后才等待新 entry 对当前 proposal 确认。
- Pet inline 视觉先处理缓存命中，再在 load/decode/稳定帧完成后本地确认。
- Linux/Niri 如果实际由 helper/native overlay 显示，必须由该 owner 给等价 ACK；未接通时 fail closed，Pet 不能用“状态已发送”冒充“已经显示”。
- active display 或 render owner 改变时，旧确认立即失效，并等待新 owner 对当前 proposal 重新确认。

### 完整 click

1. 只有 `phase=ready`、在 Avatar 有效范围、`requestedRevision === confirmedRevision` 且无 proposal in flight 时，主 pointer down 才成立。
2. down 只冻结当时 confirmed 的手势、revision、pointer/button、tool/session generation 与起点，不提交。
3. up 必须属于同一 pointer/button/generation，严格移动距离 `>6px` 才是 drag，并重新读取 fresh bounds、UI exclusion 和 Avatar hit。
4. 有效 up 只消费 down snapshot，调用 random 一次生成角色手势，纯规则结算，形成 immutable snapshot 并提交一次。
5. 无效 up、drag-out、UI release、pointercancel 只丢弃 press，不随机、不提交。`blur/hidden` 还要取消 pending proposal 和临时表现，恢复时重新建立 proposal 并从 confirmed 手势计时。
6. reveal/cooldown 约 `1000ms + 1500ms`，期间拒绝新局；Host 接受、拒绝或超时都不改本地结果。

### Streak 与生命周期

1. 5 秒内完成下一局才累加，payload 在 99 饱和。
2. 显式再次选择、清除槽位、切换道具、教程接管、确认模型不可用、surface handoff、session/generation/lease 重建或销毁时，round/proposal/press/reveal/cooldown/streak 全部清理。
3. 普通 `blur` / `hidden` 取消未完成 press、在途 proposal 和临时表现，但保留用户选择，不因它们单独发布 inactive；恢复后建立新的可确认 proposal。
4. 已经提交的回合不因 blur/hidden 撤销；可保留该 session 已完成 streak 事实，是否继续累计仍受 5 秒窗口约束。
5. 模型 bounds 短暂缺失遵循统一 missing grace；fresh release 仍必须有真实几何。grace 到期或模型身份失效才重置 session。
6. 只有 fingerprint、desktop generation、surface lease、render owner 或 model identity 等会话身份变化才全量 reset。完全重复 descriptor 或不改变这些身份的 metadata replay 不重置回合；也不得改变旧三个道具当前 effect replay 语义。

## 表现层实施

### 固定图片外框与系统光标

六张源图画布比例不同。Web、Pet inline 和 main overlay 使用同一固定 display box、稳定 hotspot/rendered anchor 和 `object-fit: contain`，避免切换时位置或大小跳动。系统光标全程保留，图片不作为原生 cursor 替换物。

### Head anchor、准备层与揭晓层

1. 通过 typed geometry provider 调用现有 `window.avatarReactionBubble.getActiveAvatarBubbleAnchor()`，归一化 bounds/head 数据。
2. `presentation.tsx` 与 Pet adapter 不扫描 model manager、Chat DOM 或 title 猜位置/角色名。
3. 角色准备层、双方揭晓层和结果 live region 使用稳定节点，`pointer-events: none`，不参与命中。
4. 与 reaction bubble 同时出现时只做局部避让，不修改其生命周期或 baseline。
5. round-reveal effect 由 definition recipe 与 snapshot 驱动；adapter 不另建第二套 reveal timer。
6. 第一版结果文案使用 8 locale 的通用“角色”，不写死 `NEKO`，也不从标题或消息作者猜角色名。
7. 快速循环图是非朗读装饰；稳定 live region 每局只宣布一次结果。
8. `prefers-reduced-motion` 保留结果和时长语义，仅弱化碰撞位移、闪动等动效。

## 分阶段实施与退出门禁

### Gate 0：保护当前基线与资源闭包

实施：

1. 保留当前图片迁移与用户工作区，不重新整理无关文件。
2. 核对六张 RPS 图片的路径、版本参数、透明通道和固定 contain 外框参数。
3. 音频候选只核实，不移动/删除；未确认采用前不写入 catalog。
4. 记录旧三道具测试基线，确认正式 registry 仍为三个。

退出条件：旧图片路径在实际消费者中为零；六张 RPS 图进入版本闭包；RPS 仍不可见。

### Gate 1：未注册的 NEKO schema 与纯 controller

实施：

1. 在 `catalog.ts` 扩展 RPS ID、reserved fields、round-choice 与真实 round-reveal 类型/validator，先导出未注册 RPS definition fixture。
2. 把所有现有“前两种分支后默认当第三种”的代码改成显式 discriminant、穷尽分派或 unknown fail closed，包括：
   - `profileInterpreter.ts:createAvatarToolProfileHandlers`
   - `catalog.ts` 的 interaction/resource reference validator
   - `protocol.ts` 的 facts 类型映射与 runtime schema 派生
   - `desktopContract.ts` 的 profile/effect schema、引用收集与投影
   - `presentation.tsx:createAvatarToolEffectExecution`
3. 新增唯一的 `roundChoice.ts`，用 injected clock/random/scheduler 测纯状态与 snapshot。
4. 不把 RPS 加入 `AVATAR_TOOL_REGISTRY`，不为测试增加长期 feature flag。

退出条件：controller 的 cycle、proposal/ack、down snapshot、9 种规则、phase、streak 99、reset 和 stale generation 测试通过；正式 Compact/Full UI 都仍只有旧三道具。

### Gate 2：NEKO protocol、desktop projection 与 Web 通用构件

实施：

1. `protocol.ts` 增加 RPS exact facts 和联合校验；旧工具拒绝 RPS 字段，RPS 拒绝旧字段。
2. `desktopContract.ts` 直接投影未注册 RPS fixture，校验资源闭包、fingerprint、未知 kind 和版本策略。
3. `runtime.ts` 注入 scheduler，创建 session controller，复用现有 pointer/range/release guard。
4. `presentation.tsx`/`styles.css` 实现图片预热、Web render ACK、head/reveal/live region 和真实 effect。
5. 未注册阶段只验证纯 controller、protocol、projection、presentation coordinator 和旧 runtime 回归；当前 runtime 通过正式 registry 查询 definition，不能为 RPS 另造长期注入 seam、测试 registry 或 feature flag。

退出条件：240/1200/200/1000/1500/5000ms、proposal/confirm、图片缓存/失败、down snapshot 和取消语义都在纯构件层可确定化验证；旧三个 runtime 测试不变。RPS 的完整 React runtime E2E 延后到 Gate 6 正式注册后执行。

### Gate 3：PC strict contract 与 domain runtime

实施：

1. `contract.js` 增加 profile/effect/reserved fields exact 校验，保持 PC opaque tool ID。
2. `runtime.js` 贯通 top-level `random` 与 scheduler；现有 interaction engine 已支持 random 注入，不能让参数在上层丢失。
3. 在现有 domain 层创建每 session controller，复用 range coordinator 和 fresh-release guard。
4. `interaction-output.js` 从 snapshot 派生四个事实、强度、声音、effect 和 visual proposal，不重新 random/rejudge。
5. fingerprint/generation/lease/owner/model identity 变化清 RPS session；完全重复 descriptor 或身份未变的 metadata replay 不清理，也不改变旧工具 effect 的既有语义。

退出条件：新 consumer 接受合法 fixture；未知 profile/effect 与旧 consumer安全拒绝；9/18 规则矩阵、streak/intensity、clock/random、lifecycle 通过，旧三 fixture 不变。

### Gate 4：Pet、main 与真实 renderer ACK

实施：

1. live visual state 加入 presentation revision/token 及完整 render identity，并提供 `pending/invalidated/confirmed` 的反向状态路径。
2. Pet adapter 接 head/reveal/预热与本地/远端 ACK，不维护玩法计时器。
3. overlay preload 同时处理缓存命中与正常 load/decode，在应用状态、稳定帧后回执；error、旧 src、迟到 token 不回执。
4. overlay service 校验 renderer sender→entry/display/current token；display/owner 切换先 invalidated。main 通过 service callback 收到状态后再验证 current descriptor 与 Pet target，并经 `PET_CHANNELS` 转发。
5. 保持 350ms descriptor liveness watchdog 原义，绝不把它改名或复用成 presentation ACK。
6. Niri 真正 render owner 接等价 ACK；尚未接通的平台路径 fail closed。

退出条件：正确 owner 的当前 ACK 才能开局；延迟、错误、跨 display/owner/generation/lease ACK 都不能锁手；owner 切换使旧确认失效；overlay passthrough 且系统光标可见。

### Gate 5：Host/Python 联合门禁

在独立后端阶段完成 Host/Python strict validation、prompt、即时回复和 memory。本文只要求：

1. 9 种合法组合在 Web、PC、Host、Python 得到同一结果。
2. 18 种矛盾结果、非法 streak/intensity、跨工具字段污染共同拒绝。
3. 后端不重随机、不改判、不回滚本地揭晓。
4. structured memory 只记录发生过猜拳及必要节奏，不记录手势、单局结果、比分或长期战绩。

退出条件：前后端 parity fixtures 全部通过，旧三道具 Host/Python 兼容不变。

### Gate 6：最后注册，只开放 Compact

前五个 gate 全部通过后：

1. 先在 `avatarTools.ts` 建立由现有三个 definition 投影的稳定 Full UI 列表，让 `FullChatSurface.tsx` 只切换一次列表来源，并通过旧三回归；不写 RPS 判断。
2. 再把 RPS definition 加入正式 `AVATAR_TOOL_REGISTRY`，让 Compact 的可替换库继续消费完整 registry。
3. Compact library 变为四个可选道具，但 quickbar 仍最多三槽，默认仍是旧三个。
4. 注册后执行真实 Web runtime E2E：首帧/pending 不可 down，正确 ACK 后 down 冻结、有效 up 仅提交一次，首次激活无暖机空窗。
5. Full 仍为旧三个；以后新增道具不再修改 Full，也不新增 surface capability 或逐工具过滤。
6. 同步 8 locale，并另行把 design 与正式三道具维护文档的入口/registry 描述更新为最终事实。

退出条件：Compact 可完整使用 RPS；Full 只完成一次旧三列表来源固定且行为无回归；没有临时 feature flag、surface 可用性协议或页面专属玩法分支。

### Gate 7：构建与桌面真机验收

先完成自动测试和真实构建，再在 macOS、Windows 验收。Linux/Niri 只验证隔离和已支持 owner 路径，不污染主平台。

退出条件：启动/重载后的首次选择、DPI、多屏、display 切换、Compact handoff、失焦、reload、退出和资源错误全部符合完成标准；日志、事件次数和 payload 是主要证据，截图仅辅助。

## 测试归属：避免重复套件

### NEKO

| 测试文件 | 唯一职责 |
|---|---|
| `avatar-tools/roundChoice.test.ts` | cycle、proposal/ACK、down snapshot、9 种关系、phase、streak 99/reset、stale callback；核心矩阵只放这里。 |
| `avatar-tools/catalog.test.ts` | ID/reserved/profile/effect/资源闭包；未注册阶段锁定 registry=3，最终注册后锁定 registry=4。 |
| `avatar-tools/protocol.test.ts` | exact payload、18 种矛盾结果、streak-intensity、旧字段与跨工具污染、一次 interaction ID。 |
| `avatar-tools/desktopContract.test.ts` | 未注册 definition 的 strict projection、resource closure、fingerprint、版本/未知 kind fail closed。 |
| `avatar-tools/interaction.test.ts` | 共享 click/bounds/UI exclusion 与 typed geometry adapter；不复制胜负矩阵。 |
| `avatar-tools/presentation.test.ts` | 纯图片预热/ACK coordinator、缓存命中、error/stale cleanup、固定 contain 参数与 round-reveal recipe；不承担 React DOM/live region。 |
| `avatar-tools/runtime.test.tsx` | `AvatarToolVisuals` 的真实 DOM/load/decode/RAF ACK、live region、首帧/pending fail closed、确认后 down/up、一次提交、blur/hidden/generation/disposer、Electron descriptor-only。 |
| `App.test.tsx` | Compact library=4、默认/最大三槽、选择/清除；保留既有 Full 三道具回归，不新增 RPS 专属 Full 场景。页面测试不重复 9 宫格。 |
| `tests/unit/test_i18n_locale_keys.py` | `en/es/ja/ko/pt/ru/zh-CN/zh-TW` key 一致、JSON 可解析、通用“角色”与结果文案存在。 |
| `tests/test_agent_rewrite_regression.py` | 延续现有图片版本闭包检查；不另建 RPS static-contract 重复套件。 |

`AvatarToolQuickbar.tsx` 和 `AvatarToolItemManager.tsx` 应继续是通用组件；只有实际通用投影回归需要时才改测试，不写 RPS 分支。

### NEKO-PC

| 测试文件 | 唯一职责 |
|---|---|
| `test/desktop-avatar-tool-contract.test.js` | exact profile/effect/resources/reserved/unknown fail closed，并锁定 opaque tool ID。 |
| `test/desktop-avatar-tool-runtime.test.js` | deterministic clock/random、proposal/ACK、down snapshot、phase、streak 99。 |
| `test/desktop-avatar-tool-runtime-lifecycle.test.js` | cancel 与 fingerprint/generation/lease/owner identity reset；重复 descriptor/普通 metadata replay 不误清理，旧 effect 语义不退化。 |
| `test/desktop-avatar-tool-domain.test.js` | snapshot 到 output/payload/sound/effect 的唯一派生。 |
| `test/pet-avatar-tool-adapter.test.js` | 首次/重载 pending、inline/remote ACK、稳定 head/reveal 节点、error cleanup、一次 submit。 |
| `test/avatar-tool-visual-overlay-contract.test.js` | 用 fake DOM/VM 验证缓存/load/decode/RAF exact token、renderer sender→entry/display、invalidated、旧 src/error 不 ACK 与 `object-fit: contain`。 |
| `test/storage-window-display-contract.test.js` | descriptor watchdog 与 presentation state 分离、service callback、current descriptor/Pet/generation/lease 转发校验。 |
| `test/avatar-tool-visual-ownership.test.js` | 仅在 Niri 真 owner ACK 需要时验证其专属 lease；不承载 Mac/Windows 通用 display ACK。 |
| `test/avatar-tool-desktop-refactor-contract.test.js` | main/adapter 无玩法、Chat 不转普通 pointer、系统光标不隐藏；不增加 RPS 专属 Full 契约。 |
| `test/preload-bridge-lifecycle.test.js` | listener/timer/session cleanup 与 Full/Compact bridge 既有对称性。 |
| `test/avatar-tool-wayland-overlay-bridge.test.js`、`avatar-tool-niri-xwayland-handoff.test.js` | 只验证真实 Niri owner ACK 与平台隔离，不复制通用规则。 |
| `test/integration/avatar-tool-cross-repo.test.js` | NEKO 真实 contract/descriptor 驱动 PC；最终注册后覆盖 RPS 与旧三回归。 |

跨仓 emitter 当前从正式 registry 枚举 definition。未注册阶段使用 strict fixture；不能为了让 integration 方便而提前开放 RPS。最终集成 helper 必须先模拟/完成 presentation ACK，才允许发送 down/up。

## 验证命令

实现阶段按 gate 运行目标测试，最终至少执行：

NEKO React：

```powershell
cd frontend/react-neko-chat
npm run typecheck
npm test
```

NEKO Host/Python 与构建：

```powershell
python -m pytest tests/unit/test_avatar_interaction_payload_contract.py tests/unit/test_avatar_interaction_memory_contract.py tests/unit/test_i18n_locale_keys.py tests/test_agent_rewrite_regression.py
bash build_frontend.sh
git diff --check
```

NEKO-PC：

```powershell
npm run lint
npm test
$env:NEKO_WEB_REPO_PATH='<NEKO 仓库绝对路径>'
npm run test:integration
git diff --check
```

`npm test` 只包含 PC unit + contract，不包含 integration，因此跨仓测试必须单独执行。构建只通过项目命令生成 `static/react/neko-chat`；禁止手改生成产物。

## 真机验收清单

1. Compact 首次打开 library 可以找到 RPS，默认 quickbar 仍为旧三，可替换任一槽位并持久化。
2. Full 保持当前三个入口和旧行为；本轮不为它新增 RPS 选择、表现或专属 handoff 逻辑。
3. 启动/重载后第一次选择 RPS，图片 ready 后立刻显示；靠近模型立刻切换 60% in-range 形态并保持模型命中，不出现十几秒穿透。
4. 范围外 240ms、范围内 1200ms、角色准备 200ms；快慢切换不跳过未确认手势。
5. pending revision 不可 down；确认后 down 锁定实际可见手势，按住期间不变，有效 up 只提交一次。
6. 覆盖 rock/scissors/paper、九种组合、四局连续强度、99 饱和、冷却中重复点击。
7. drag、UI release、cancel、blur/hidden、模型暂失、切道具、清槽、handoff、reload、退出均无错误提交或残留。
8. Web、Pet inline、Mac/Windows overlay 的固定 contain 外框和 anchor 一致，切图不跳动，系统光标始终可见。
9. 多屏/DPI/display owner 切换后旧 ACK 失效，只有新 owner ACK 才能继续。
10. Host 拒绝或超时不回滚本地结果；旧三个道具全链路无变化。

## 完成前审查清单

1. 正式 UI 由 Compact 的可替换库暴露 `rps`；没有新增 surface capability、`toolId === 'rps'` 过滤或 Full 玩法分支。
2. 只有一个通用 round-choice controller；registration singleton、页面、adapter、main 都不持有可变回合状态。
3. 所有 profile/effect 分派显式穷尽，未知 kind fail closed，不会误落入 hammer/locked-impact。
4. Web 与桌面都有真实 render-owner ACK；descriptor watchdog 未被冒充为 presentation ACK。
5. pending、迟到、错误或跨 owner/display/generation/lease ACK 均不能开局。
6. interaction 只从 down snapshot/round snapshot 派生，九种规则与 streak/intensity 联合不变量一致。
7. 六张 RPS 图使用统一资源根、单一版本参数、固定 contain 外框；PC 不复制资源。
8. 音频候选是否采用有明确结论；未采用时不得留下假 catalog 引用，采用时有来源/授权与真实 recipe。
9. 所有 timer、RAF、Audio、DOM、effect、proposal/ack 和旧 generation 都有 disposer/session 清理证据。
10. 没有迁入旧 `guess` 代码或重复测试，也没有误删无关的 `activity_guess_gate.py`。
11. Mac/Windows 真机通过；Linux/Niri 未支持的 render owner 明确 fail closed，且不污染主平台。
12. `.agent` 文件保持工作记忆属性，除非用户另行明确要求，否则不进入功能提交。
