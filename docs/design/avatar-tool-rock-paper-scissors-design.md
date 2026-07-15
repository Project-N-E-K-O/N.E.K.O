# Avatar 猜拳道具设计方案

本文定义 Avatar tools 新道具“猜拳”的目标产品语义和跨端一致性要求。它是设计约束，不是实施状态或交接记录，也不代表当前代码已经支持 `rps`。

本设计对齐以下代码基线：

1. NEKO `codex/avatar-tool-rock-paper-scissors` 当前代码。
2. NEKO-PC `codex/avatar-tool-rock-paper-scissors` 当前代码。
3. `docs/design/avatar-tool-interaction-design-and-maintenance.md` 中既有三道具的统一架构与交互不变量；本设计只对其“暂不包含猜拳”的范围作第四道具扩展，不改变旧三道具语义。
4. 当前代码、测试和真实运行证据。

旧 `guess` 分支只作为素材、玩法和验收语义的参考。旧分支里的 `App.tsx` 专属状态、旧 runtime 和已删除的 PC 模块都不是实施基线；但其按道具域归档图片的目录结构已经成为本设计的资源基线：`static/assets/avatar-tools/{sugar,claw,hammer,rps,ui}/`，不得退回 `static/icons/` 混放。

## 设计目标

猜拳是一种轻量 Avatar 互动，不是独立小游戏窗口。用户选择道具后，通过当前实际显示的石头、剪刀或布与角色完成一局，立即看到本地揭晓，并允许角色随后围绕同一局事实自然回应。

核心体验：

1. 用户从 Full Chat 或 Compact Chat 的 Avatar tools 入口选择“猜拳”。
2. 道具跟随系统光标，并循环显示石头、剪刀、布。
3. 进入 Avatar 有效范围后，循环减速并出现角色准备手势。
4. 用户完成一次有效点击，当前真实显示的手势成为用户本局手势。
5. 前端或桌面 Pet runtime 本地决定角色手势和胜负。
6. 本地立即揭晓、播放音效并进入短冷却，不等待后端。
7. 同一局事实通过 `avatar_interaction` 发送给 Host 和后端。

猜拳应保持轻快、短促和低打扰。它不建立积分压力，不要求用户阅读规则，也不能阻断聊天主流程。

## 与统一道具架构的关系

猜拳必须作为新的声明式道具接入当前统一架构：

1. NEKO catalog 是 React 道具定义、资源、能力、interaction profile 和桌面 descriptor 投影的唯一事实源；Host 与 Python 保留各自严格 wire contract，并由同一组合法/非法 parity fixtures 约束，不能靠手写 tool-id 分支自行解释玩法。
2. Full Chat 与 Compact Chat 共享同一套 profile 解释、runtime 实现和表现组件；两个 renderer 可以拥有各自的页面 session，但不得各写一套猜拳规则或状态机。
3. Web 由 NEKO runtime 执行规则。
4. Electron 多窗口模式下，Chat 只发布 descriptor；唯一桌面回合状态属于 Pet domain runtime，半局不在 Full/Compact surface 之间迁移。
5. PC main overlay 只负责 Mac/Windows 跟随视觉、平台坐标和呈现确认转交，不合成命中、不运行计时器、不随机、不决定胜负也不提交。
6. Host 和 Python 只接收、验证并描述已发生的回合事实，不重新决定结果。

猜拳使用新的声明式 round-choice profile。注册表和 profile 本身保持不可变；解释器只编译规则或创建 controller factory，可变回合状态必须属于当前 runtime session，不能挂在共享 registration singleton 上。

NEKO-PC 保持现有分层：strict contract 只解码和校验；domain runtime 持有命中、click、clock、random、scheduler 与回合 controller；interaction output 只从不可变 round snapshot 派生视觉、声音和提交命令；Pet adapter 只接真实输入、渲染 Pet 内表现、播放声音和转交提交。玩法不得下沉到 adapter 或 main。

猜拳不能复用任一旧道具作为 fallback，也不能把专属 timer、声音或 DOM 状态重新写进 `App.tsx`、`FullChatSurface.tsx` 或 Pet preload 主文件。“每个道具独立”指 definition、profile 和领域边界独立，不要求每个道具或手势拆成独立文件；应继续沿用现有分层，最多增加一个通用回合控制模块。

NEKO registry 会自动投影为可选道具目录。只有 Web、PC、Host/Python 和资源契约都完成后，才能把 `rps` definition 加入正式 registry；在此之前用未注册 fixture 验证，不为中间态增加长期 feature flag。

## 用户路径

### 选择与取消

1. 用户打开 Avatar tools 菜单或快捷栏。
2. 选择“猜拳”后，菜单收起，道具进入活动状态。
3. 再次选择、清除槽位、切换道具、教程接管、surface handoff 或页面销毁时，当前回合、连续局和全部临时表现立即清理。
4. 普通 `blur` / `hidden` 取消未锁定 click 和临时表现，但不自行清除用户的道具选择；只有既有 surface lifecycle 判定窗口关闭、handoff 或失活时才发布 inactive。
5. 取消只结束本地状态并发布 inactive，不产生 interaction，也不迁移半局到其它 surface。

### 准备阶段

范围外：

1. 道具使用小型 pointer 形态。
2. 石头、剪刀、布每 `240ms` 循环一次。
3. 不显示角色头顶准备手势。
4. 范围外按下或松开都不成立回合。

进入 Avatar 有效范围：

1. 道具切换为 in-range icon 形态。
2. in-range 视觉使用定义尺寸的 `60%`，不沿用旧实现中的 `50%` 偏差。
3. 用户手势每 `1200ms` 循环一次，便于观察和抓取时机。
4. 角色头顶准备手势每 `200ms` 循环一次，只表示角色正在准备。
5. 进入范围本身不发送 Host 或后端事件。

离开有效范围后恢复范围外状态；UI exclusion、Chat 窗口和其它受管窗口应视为强制离开。

快慢循环切换时保留最后已确认手势，并从该手势重新计时；不能因为切换周期而立即推进一个用户尚未看到的手势。冷却结束后也按同一规则恢复。

### 有效出拳

一局只在完整 click 成立：

1. 左键或主 pointer 在 Avatar 有效范围内按下。
2. 松开必须属于同一 pointer、同一 button、同一 tool generation。
3. 移动距离不得超过统一 runtime 的 click/drag 阈值；当前策略为严格大于 `6px` 才判定 drag，RPS 不复制第二套阈值。
4. 松开时必须重新读取新鲜 bounds、UI exclusion 和 Avatar hit。
5. `pointercancel`、失焦、页面隐藏、拖出范围或释放到 UI 都只取消。

`pointerdown` 只快照并冻结当时最后已确认的手势，不提交；只有同一次 press 的有效 `pointerup` 才用该快照完成回合并提交。无效 release 丢弃快照并恢复循环，不能在按住期间悄悄改成另一手势。

用户手势必须取自用户最后实际看到的 variant，而不是另一套独立 timer 值。视觉层、runtime 和 payload 不能各自维护“当前手势”。

必须把“准备显示”与“已经显示”拆成带 revision 的两段状态：

```text
requestedVariant + revision
  -> presentation ready + stable frame
  -> confirmedVariant + revision
```

`requestedVariant + revision` 只驱动候选视觉请求，包括 Web pointer/icon、Pet inline 或桌面 overlay；实际渲染 owner 完成确认后，`confirmedVariant + revision` 才驱动 pointerdown snapshot、round snapshot 和 interaction payload。每次最多一个 proposal in flight，ack 后 requested 与 confirmed 收敛，再开始下一次周期。

Web 图片只有在资源 load/decode 成功、可见状态和 transform 已应用并跨过至少一个稳定帧后才能确认。Mac/Windows 桌面链路必须由 overlay renderer 回传 revision，main 校验当前 display、tool、contract fingerprint、desktop generation 与 surface lease 后转给 Pet runtime；现有 descriptor watchdog 只证明 Pet 发布过视觉状态，不能当作图片呈现确认。

确认必须来自实际渲染 owner：Pet inline 可以本地确认；如果 Linux/Niri 由 helper 或 native overlay 真正显示，则由该 owner 提供等价 ack，再经既有平台链转给 Pet，Pet 不能根据“状态已发送”自行猜测。无法确认时本次 click fail closed。

只有 `requestedRevision === confirmedRevision` 且不存在 in-flight proposal 时，`pointerdown` 才能从 `confirmedVariant` 建立 press snapshot；pending revision 期间即使已有上一 confirmed 也不得开局，避免用户已经看到新图但 runtime 仍锁旧手势。release 只能使用 down 时的 snapshot；错误、超时、迟到或跨 generation/surface/display 的 ack 都不得推进手势或改写回合。

## 回合状态机

```text
ready
  -> round_reveal
  -> round_cooldown
  -> ready
```

### `ready`

1. 根据当前范围执行快循环或慢循环。
2. 允许开始一次有效 click。
3. 范围内显示角色头顶准备手势。

### `round_reveal`

有效松开后立即进入：

1. 锁定用户手势。
2. 使用当前 session 的 random provider 生成角色手势。
3. 由纯规则计算 `user_win`、`neko_win` 或 `draw`。
4. 锁定双方手势、结果、连续局数和强度，形成不可变 round snapshot。
5. 停止用户手势和角色准备手势循环。
6. 播放出拳确认音并开始约 `1000ms` 的碰撞/揭晓动画。
7. runtime 在同一次 commit 中从 snapshot 原子构建一次 payload 并生成 interaction id；profile/controller 不预生成或另存一套 id。
8. 此阶段拒绝新的猜拳 click。

### `round_cooldown`

1. 显示结果约 `1500ms`。
2. 用户获胜播放胜利音；角色获胜或平局播放轻量逗趣音。
3. 后端是否接受、拒绝或超时都不改变本地结果。
4. 冷却结束后根据最新范围恢复快循环或慢循环。

本地完整周期约 `2500ms`。所有 timer 必须属于当前 session disposer；旧 generation 的回调不得修改新回合。

## 猜拳规则

手势枚举：

```text
rock / scissors / paper
```

规则：

1. `rock` 胜 `scissors`。
2. `scissors` 胜 `paper`。
3. `paper` 胜 `rock`。
4. 双方相同为 `draw`。

结果枚举：

```text
user_win / neko_win / draw
```

variant 映射固定为：

| variant | 手势 |
|---|---|
| `primary` | `rock` |
| `secondary` | `scissors` |
| `tertiary` | `paper` |

该映射属于 profile 契约，Web 和 PC 必须共同校验，不能在不同端各写一张表。

## 连续局与强度

短时连续局只表示互动节奏，不记录长期战绩。

1. 第一局：`streakCount=1`、`intensity=normal`。
2. 5 秒内再次完成有效回合：连续数累加。
3. 第 2 至 3 局：`streakCount=2..3`、`intensity=rapid`。
4. 第 4 局及以后：`intensity=burst`；协议字段在 `99` 饱和，不产生 `100` 或越界 payload。
5. `streakCount` 与 `intensity` 是联合不变量，任何端都必须拒绝 `1 + burst`、`2 + normal` 等矛盾组合。
6. 超过 5 秒、切换/取消道具、窗口销毁、session/generation/lease 重建或确认模型不可用时归零。

离开 Avatar 范围本身不应清除已经锁定的揭晓或连续计数；冷却结束后再按最新范围恢复。模型 bounds 的短暂缺失先遵循统一 missing grace，只有 grace 到期或模型身份明确失效才视为“模型不可用”。reset policy 属于 round-choice profile 契约，不能散落在页面代码。

## 本地表现

### 道具视觉

需要三套 icon 和三套 pointer 图片，并在激活时预加载：

1. rock icon / pointer。
2. scissors icon / pointer。
3. paper icon / pointer。

规范路径固定为：

```text
/static/assets/avatar-tools/rps/rps_rock.png
/static/assets/avatar-tools/rps/rps_rock_cursor.png
/static/assets/avatar-tools/rps/rps_scissors.png
/static/assets/avatar-tools/rps/rps_scissors_cursor.png
/static/assets/avatar-tools/rps/rps_paper.png
/static/assets/avatar-tools/rps/rps_paper_cursor.png
```

NEKO catalog 保存不带 query 的同源路径；asset-version 与 desktop projection 只追加一次非空 `?v=`。六张图片必须全部进入 React Chat 资源版本闭包。NEKO-PC 不复制、不重命名也不硬编码图片，只消费 descriptor 中的带版本同源 URL。

六张源图画布并不相同：icon 分别约为 `239×240`、`177×240`、`240×230`，pointer 分别约为 `80×80`、`59×80`、`80×77`。Web、Pet inline 与 main overlay 必须使用相同的固定 display box、稳定 anchor/hotspot 和 `object-fit: contain` 语义，不能按每张原图宽高拉伸而造成切帧跳动。`60%` 只由 definition 的 in-range scale 表达一次，不能同时缩 display box 再重复缩放。

系统光标必须始终可见。猜拳图片只是伴随视觉，不能隐藏或替换系统光标。

声音继续作为 catalog/profile 的声明式资源，由 Web 或 Pet runtime 按 round snapshot 选择；PC 不复制音频。正式发布前必须确认图片和声音的原创性或再分发授权，不能仅以仓库总许可证代替素材来源证明。

### 角色头顶准备层

1. 位置通过 typed geometry provider 复用公开的 `window.avatarReactionBubble.getActiveAvatarBubbleAnchor()`，并归一化为 bounds/head 数据；表现组件和 Pet adapter 不扫描 model manager 或 DOM。
2. 展示层独立于 `avatarReactionBubble` 生命周期。
3. 与表情气泡同时存在时自动上移或侧移避让。
4. `pointer-events: none`，不能改变 Avatar、按钮或 Chat 的命中。
5. 模型 bounds 暂时丢失时遵循统一 bounds grace；真实 release 仍必须使用新鲜几何。

### 揭晓层

1. 用户手势从点击位置短暂停留。
2. 角色手势定格在 head anchor 附近。
3. 两个手势轻碰或同时亮起。
4. 结果文字使用 i18n。
5. 失败一侧可降低饱和度，但不改变资源本身。
6. 动画节点保持稳定，使用 transform/opacity，不在高频 move 中重复创建 DOM。
7. definition 的 `effects` 必须声明真实的 round-reveal effect recipe/kind，round-choice profile 的 feedback 只引用该 effect id；Web 与 PC strict contract 共同校验引用闭包，不能把 recipe 塞进 profile，也不能为满足闭包加入不会执行的假 effect。

## Interaction 事件

```json
{
  "interactionId": "avatar-int-example",
  "toolId": "rps",
  "actionId": "play",
  "target": "avatar",
  "intensity": "normal",
  "pointer": {
    "clientX": 320,
    "clientY": 240
  },
  "timestamp": 1730000000000,
  "playerGesture": "rock",
  "nekoGesture": "scissors",
  "roundResult": "user_win",
  "streakCount": 1
}
```

要求：

1. `playerGesture`、`nekoGesture`、`roundResult` 为必填。
2. `streakCount` 必须是 `1..99` 内有限且数值上为整数的 JSON number（`Number.isInteger` 语义）；拒绝 `1.5`、布尔值、数字字符串、NaN 和 Infinity。JSON 中 `1.0` 与 `1` 等价，不作词法区分。
3. `intensity` 与 `streakCount` 必须满足 `1 -> normal`、`2..3 -> rapid`、`4..99 -> burst`。
4. `playerGesture`、`nekoGesture`、`roundResult`、`streakCount` 必须加入 NEKO/PC profile schema 的 reserved payload fields；旧 chance profile 不得把这些名称声明为动态布尔字段。
5. 不携带 `touchZone`、`rewardDrop` 或 `easterEgg`；这些 camelCase/snake_case 字段即使为 `false`、空字符串或 `null`，只要出现就拒绝。旧三道具也必须拒绝 RPS 四个事实字段及 snake_case 别名，但不得借机改变旧工具现有的兼容输出。
6. 九种手势组合都必须由同一 relation 规则验证；每个组合对应的两个错误结果，共十八种矛盾事实，一律拒绝而不是修正。
7. Host 和 Python 接受 camelCase/snake_case，但 RPS 的规范 snake_case 输出只包含本工具声明的事实，不补入旧道具的 `touch_zone`、`reward_drop` 或 `easter_egg` 占位字段。
8. Host/Python 应验证结果与双方手势一致；不一致时拒绝，不能覆盖前端已经展示的结果。
9. 本地提交和本地表现从同一个 round snapshot 派生；每局只生成并提交一个 interaction id。

### 版本兼容

PC strict consumer 对未知 profile/effect fail closed。若 RPS 只新增 profile/effect discriminator、将四个当前未被旧三道具使用的名称加入 reserved fields，并且不改变既有 v1 exact-record 的必填形状，可以继续使用现有 wire/definition/policy 版本，并用旧消费者拒绝测试证明安全；若必须给既有公共结构增加必填字段，则升级对应版本。无论采用哪种版本，旧 PC 未支持前都不能提前把 `rps` 注册进可见 UI。

## Host、回应与记忆语义

后端收到的是已完成回合的客观事实。以下只是允许的用户体验空间，不是 prompt 中固定情绪、语气或 emotion seed：

1. 用户赢：角色可以惊讶、不服或邀请再来。
2. 角色赢：可以轻微得意，但不重度嘲讽。
3. 平局：可以表达默契或再次挑战。
4. 连续局：只体现“又玩了一局/连续几局”的节奏。

约束：

1. 不让后端重新随机或重新判胜负。
2. 不在 Host 新增固定 emotion seed；角色表现继续由既有 assistant 响应链路决定。
3. 不让 prompt 复述字段名、坐标、图片或前端动画。
4. 不因 ack 拒绝撤销本地揭晓。
5. Host 既有 pending-turn gate 保持不变；本地可以完成下一局，但被 gate 拒绝的回合不强制排队。
6. 结构化 memory note 只记录发生过猜拳及必要的节奏级别，不记录双方手势、单局结果、比分或长期胜负历史。
7. 普通 assistant 即时回复可以围绕本局事实自然回应，并沿用现有通用回复持久化语义；不得为 RPS 新增长期战绩、专属结果表或多条“替换旧记录”的假语义。

## Full、Compact 与桌面一致性

1. Full 菜单显示 registry 中的全部可用道具；Compact item manager/library 显示 `rps`，quickbar 只有用户装备后才显示。
2. Compact 保持最多三个槽位，默认仍为 `lollipop`、`fist`、`hammer`；新增 RPS 不扩成四槽，也不静默替换旧默认道具。
3. Full 与 Compact 的选择、取消、状态发布和回合规则完全一致，页面布局和菜单入口继续各自维护，不复制玩法逻辑。
4. Electron 下只有当前可见且选中的 surface 可以发布 descriptor。
5. Pet runtime 生成桌面当前手势和回合结果；Mac/Windows Chat 不参与 pointer。Linux/Niri 只沿用既有、受校验的 host-boundary / ownership relay，不新增 RPS 私有 pointer 链。
6. Mac/Windows main overlay 全程 visual-only、ignore mouse，只显示 Pet runtime 请求的 variant 并回传真实呈现确认；系统光标始终可见。
7. Linux/Niri 可以继续使用既有 native overlay、input region、ownership handoff 和经 main 校验后转给 Pet 的平台边界事件，但平台层仍不得命中、随机、判胜负或提交，也不能反向改变 Mac/Win 语义。

## 性能与生命周期

1. 只有 `rps` active 且 phase 为 `ready` 时运行手势周期。
2. 每个活动 runtime session 最多一个回合 controller，不创建第二套 range/session；Electron Chat 不建 controller，Pet session 唯一持有桌面回合。
3. clock、scheduler 与 random provider 由 domain runtime 注入，测试使用 fake clock / deterministic random；adapter、页面和 main 不直接调用随机或维护 timer。
4. 图片与音频激活时一次预热，循环中只切换已缓存资源；首次选择也必须在资源 ready 与首帧确认后才允许成局。
5. 高频 pointer move 继续由现有 RAF/poller 合并。
6. 头顶层和揭晓层使用稳定节点；timer tick 不重新扫描模型 DOM。
7. descriptor generation、surface lease、contract fingerprint、Pet session generation 或 renderer ownership 任一变化，都取消 proposal/ack、press、reveal/cooldown 和 streak；旧回调不得恢复半局。

## 无障碍与多语言

React 用户界面 8 个 locale：

```text
en / es / ja / ko / pt / ru / zh-CN / zh-TW
```

Python prompt 使用现有后端 locale 约定 `en / es / ja / ko / pt / ru / zh / zh-TW`；前后端中文 key 不得互相误写。

至少包含：

1. 道具名“猜拳”。
2. 石头、剪刀、布的可访问标签。
3. 用户胜、角色胜、平局结果。
4. 第一版默认使用本地化通用“角色”称谓；如果以后提供 typed `roleDisplayName`，可以安全插值。不得把 Chat `title`、最后一条消息作者或 DOM 扫描结果当角色名，也不写死 `NEKO`。

快速循环图片应使用非朗读装饰属性；结果文字由一个稳定、低打扰的 live region 宣布一次，不能每次准备手势切换都触发朗读。

## 不做事项

第一版不做：

1. 独立 mini-game 窗口。
2. 计分板、排行榜或长期战绩。
3. 多人猜拳。
4. 背包、消耗、购买或解锁。
5. 后端决定胜负。
6. 语音实时会话专属协议。
7. 接管通用表情气泡状态机。
8. 捕获第三方窗口点击。
9. 为 `rps` 复制 Web/PC 两套私有规则表。
10. 按道具或按手势拆出大量文件、页面 hook 或 tool-id 分支。
11. 用假 effect、旧道具 fallback 或 Host 修正来掩盖缺失能力。

## 验收标准

1. Full 菜单和 Compact library 都能选择、装备和取消猜拳；Compact 默认三个槽位保持旧三道具。
2. 范围外以 240ms 循环小手势；范围内以 1200ms 循环 60% 大手势；角色头顶以 200ms 循环准备手势。
3. 首帧或任一 requested revision 尚未确认时不能开局；只有 requested/confirmed revision 一致且无 in-flight proposal 才允许 down，错误或迟到 ack 不得污染新 session。
4. down 快照并冻结用户实际看到的 confirmed 手势但不提交；有效 up 只用该快照提交一次。
5. drag-out、UI release、cancel、blur/hidden、教程接管、surface handoff 和 generation 变化均不提交。
6. 揭晓与结果约 2.5 秒完成，不等待后端；冷却中重复点击无效。
7. Web、PC、Host 和 Python 对九种合法组合得到同一结果，并共同拒绝十八种矛盾结果、错误 streak/intensity 和 RPS 旧字段污染。
8. 启动或重载后的首次选择立即可见；资源 ready 后靠近模型立即放大且不穿透，不能依赖十几秒预热恢复。
9. 六张 RPS 图片使用统一资源根、版本参数和固定 contain 外框；加载失败时 fail closed，不提交不可见手势。
10. 旧三道具的规则、视觉、声音、payload、默认槽位和桌面行为无变化。
11. Mac/Windows 系统光标始终可见，overlay visual-only 且不抢点击；Linux/Niri 适配不污染该语义。
12. 所有 timer、音频、DOM、effect、proposal/ack 和旧 generation 在取消/销毁后无残留。
13. 结构化 memory note、8 个 locale 与 live region 符合本设计，结果只宣布一次。
