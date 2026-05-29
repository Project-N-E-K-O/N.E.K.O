# 猫娘大乱斗 PR #1454 — 改动总览

> 编写时间：2026-05-28（2026-05-29 更新：探险五类落点交互已实装，见末尾「十二、探险交互实装进展」）
> 适用分支：`pr/1454`（相对 `main` 的新增内容）
> 本 PR 的**主旨**就是引入「猫娘大乱斗（Neko Brawl Arena）」这套独立卡牌战斗 + 卡牌探险原型；其余如主动投递框架属于伴随合入，详见 [docs/branch-pr1454-changes.md](../branch-pr1454-changes.md)。

本文档是大乱斗模块的入口说明：把分散在 `battle-arena/`、`local_server/battle_arena_server/`、`app/main_server.py`、`static/`、根目录启动脚本、`docs/neko-brawl/` 的全部相关改动串成一张图，方便后续接入「真实羁绊」「真实自身故事系统」「联机匹配」等真实数据源。

---

## 目录

- [一、PR 主旨](#一pr-主旨)
- [二、整体架构](#二整体架构)
- [三、提交时间线](#三提交时间线)
- [四、前端：`battle-arena/`](#四前端battle-arena)
  - [4.1 工程骨架](#41-工程骨架)
  - [4.2 顶层壳 `BattleArena.jsx`](#42-顶层壳-battlearenajsx)
  - [4.3 `neko-brawl/` 子组件目录](#43-neko-brawl-子组件目录)
  - [4.4 数据层](#44-数据层)
  - [4.5 音效系统](#45-音效系统)
  - [4.6 静态资源](#46-静态资源)
  - [4.7 持久化（localStorage）](#47-持久化localstorage)
- [五、后端：`local_server/battle_arena_server/`](#五后端local_serverbattle_arena_server)
  - [5.1 设计原则](#51-设计原则)
  - [5.2 HTTP 接口](#52-http-接口)
  - [5.3 「当前猫娘」解析（`active_neko_context.py`）](#53-当前猫娘解析active_neko_contextpy)
  - [5.4 奇遇铸造机 / `forge_facts`](#54-奇遇铸造机--forge_facts)
  - [5.5 卡牌故事生成（`forge_story_generator.py`）](#55-卡牌故事生成forge_story_generatorpy)
  - [5.6 匹配队列 / Dummy 对手](#56-匹配队列--dummy-对手)
- [六、主进程桥接（头像同步）](#六主进程桥接头像同步)
- [七、启动 / 停止脚本](#七启动--停止脚本)
- [八、探索模式规则文档](#八探索模式规则文档)
- [九、端到端数据流](#九端到端数据流)
- [十、已知占位与待办](#十已知占位与待办)
- [十一、文件清单速查](#十一文件清单速查)
- [十二、探险交互实装进展（2026-05-29）](#十二探险交互实装进展2026-05-29)

---

## 一、PR 主旨

把「猫娘大乱斗」做成 NEKO 主项目旁边一个**松耦合**的卡牌玩法原型：

- **前端**：单独的 Vite + React 子项目，独立端口 5173，不挂进 NEKO 主前端
- **后端**：单独的 FastAPI 服务，端口 3001，只**读** NEKO 的 `facts.json` / 复用 NEKO 主服务的 LLM 配置，**不写**主仓库的 `memory_server` / `memory/` / `main_server`
- **跨进程通信**：仅通过 NEKO 主服务上新增的两条 HTTP 端点 `/battle-arena/avatar` 同步头像，不嵌入 NEKO 主聊天 WebSocket
- **数据源**：所有内容（基础卡、Adventure Deck、临时事件、Forged 卡）都以前端硬编码 / `localStorage` 形式落地，标注 `TODO: [真实 XXX 接入]` 等待替换

设计目标是：**先把玩法和 UI 跑通，再分别接入真实羁绊 / 真实自身故事 / 真实联机匹配**。当前所有「玩家羁绊」「对手数据」「奇遇事件池」都是占位。

---

## 二、整体架构

```
┌──────────────────────────────────────────────────────┐
│  NEKO 主服务 (launcher.py / app/main_server.py)        │
│  port 48911                                            │
│                                                        │
│  POST /battle-arena/avatar  ← 由 NEKO 内 chat 头像     │
│  GET  /battle-arena/avatar/{side}                      │
│       ↑ syncAvatarToBattleArena() in app-chat-avatar.js│
└──────────┬───────────────────────────────────────────┘
           │ 头像 dataUrl
           ▼
┌──────────────────────────────────────────────────────┐
│  Battle Arena 前端 (Vite dev / build)                 │
│  port 5173                                             │
│                                                        │
│  battle-arena/src/components/BattleArena.jsx          │
│   ├─ Home（待匹配 / 入口 / 教程）                       │
│   ├─ DeckBuilderPanel（组卡）                          │
│   ├─ DeckLibraryPanel（卡组管理）                      │
│   ├─ CardGamePanel（旧版直接 Boss 战）                 │
│   └─ NewBattleDuelUI（新版双方对战）                   │
└──────────┬───────────────────────────────────────────┘
           │ POST /arena/join     /arena/forge-facts
           │ GET  /arena/status   POST /arena/forge-card-story
           ▼
┌──────────────────────────────────────────────────────┐
│  Battle Arena 后端 (uvicorn server.py)                │
│  port 3001                                             │
│                                                        │
│  匹配队列 (waiting_room / matched)                     │
│  Dummy 对手 (3s 无人就配虚拟猫娘)                       │
│                                                        │
│  奇遇铸造机：                                           │
│   active_neko_context.py → 解析当前猫娘                 │
│   _resolve_facts_path() → memory_dir/<猫娘>/facts.json  │
│   _select_forge_facts() → 抽 5 条候选                   │
│                                                        │
│  卡牌故事生成：                                          │
│   forge_story_generator.generate_forge_card_story()    │
│   → utils.llm_client.create_chat_llm()                 │
│   → 复用 NEKO 主服务的 summary 模型 + 当前猫娘人格 prompt│
└──────────────────────────────────────────────────────┘
                          │
                          │ 只读
                          ▼
                {memory_dir}/{当前猫娘}/facts.json
```

---

## 三、提交时间线

| Commit | 标题 | 含义 |
|--------|------|------|
| `5dc24ce4` | NekoBattleArenaV2 | 初版骨架（+9620 行）：前端 React 子项目 + 匹配服务器 + 启动脚本 |
| `9c2b35dd` | feat: update neko brawl arena prototype | 资源（Boss 立绘 / BGM / SFX）+ `neko-brawl/` 子组件目录拆分 + `nekoBrawlAudio.js` 音效模块 |
| `e9d359c7` | fix: make deck builder card collection scrollable | 组卡器卡池可滚动 |
| `718f0b8f` | fix: limit forged cards to single deck copy | Forged 卡每副牌限 1 张 |
| `bd1331b1` | fix: refine forged card story prompt perspective | 锁定「第三人称叙事 + 末句第一人称台词」格式 |
| `b308b56a` | feat: expand neko brawl arena prototype | 加入 `nekoBrawlAdventureDeck.js`（40 张探索牌组）+ 结算 UI + 教程面板 + 停服脚本 |
| `c84df695` | docs: add neko brawl exploration rules | [exploration-rules.md](exploration-rules.md)（1155 行规则文档） |

---

## 四、前端：`battle-arena/`

### 4.1 工程骨架

[battle-arena/package.json](../../battle-arena/package.json) — 完全独立的 npm 工程：

| | 版本 / 作用 |
|---|---|
| `react` / `react-dom` | 18.3.1 |
| `framer-motion` | 11.11 — 入场/退场/卡牌翻转动画 |
| `lucide-react` | 0.454 — 图标库（用于属性、操作按钮） |
| `vite` | 6.0.1 + `@vitejs/plugin-react` 4.3.4 |
| `tailwindcss` | 3.4 + `postcss` 8.4 + `autoprefixer` 10.4 |

构建/配置文件：

- [index.html](../../battle-arena/index.html)（13 行）
- [src/main.jsx](../../battle-arena/src/main.jsx)（10 行，挂载 root）
- [src/App.jsx](../../battle-arena/src/App.jsx)（5 行，仅渲染 `<BattleArena />`）
- [src/index.css](../../battle-arena/src/index.css)（88 行，Tailwind 基础 + 全局样式）
- [vite.config.js](../../battle-arena/vite.config.js) / [tailwind.config.js](../../battle-arena/tailwind.config.js) / [postcss.config.js](../../battle-arena/postcss.config.js)

scripts：

```
dev:     vite --host 127.0.0.1 --strictPort
build:   vite build
preview: vite preview
```

### 4.2 顶层壳 `BattleArena.jsx`

[src/components/BattleArena.jsx](../../battle-arena/src/components/BattleArena.jsx)（~2393 行）— 全游戏入口，承担：

- **场景状态机**：在 Home（待匹配）/ DeckBuilder（组卡）/ DeckLibrary（牌库）/ 旧版直接 Boss 战（`CardGamePanel`）/ 新版双方对战（`NewBattleDuelUI`）之间切换
- **匹配流程**：调用后端 `/arena/join`，开始轮询 `/arena/status/{playerId}`，匹配成功后注入对手快照到右侧 `NEKO_RIGHT`
- **奇遇铸造机 UI**：`FORGE_EVENT_POOL` 临时事件池（20 条占位）、`FORGE_ENCHANTMENTS`（6 种附魔）、`FORGE_CARD_ATTRIBUTES`（搞笑/温馨/逆天/小丑/傲娇）+ `FORGE_ATTRIBUTE_COUNTERS` 克制关系
- **Forged 卡持久化**：`loadForgedBrawlCards()` / `saveForgedBrawlCards()` 读写 `localStorage`
- **场景 BGM**：通过 `playNekoBrawlSceneBgm(scene)` 在场景切换时换曲（home / deckBuilder / deckLibrary / battle）
- **判定动画**：`JUDGING_FLAVOR_LINES`（贿赂评委 / 偷瞄结果 / 翻档案 / 交换眼神）+ `WAITING_IDLES` 多个等待 GIF 循环
- **占位常量**：`MY_BONDS_PLACEHOLDER`（5 条羁绊）、`NEKO_LEFT` / `NEKO_RIGHT_DEFAULT`、`RANKING`（排行榜全是 `???`）、`MAX_DAILY = 10`、`SHOW_LEGACY_FORGE_PANEL = false`

> 文件顶部有显眼的 `TODO: [羁绊列表接入]` 和 `TODO: [头像接入]` 注释 — 整套占位等待 NEKO 主应用「羁绊记录系统」落定。

### 4.3 `neko-brawl/` 子组件目录

第二阶段（commit `9c2b35dd`）开始把新版玩法的组件从顶层 `components/` 拆到 `components/neko-brawl/`，区分「旧版 V1」（`CardGamePanel` / `DeckBuilderPanel` 在外层老路径，部分已不用）和「V2 重构件」：

| 文件 | 行数 | 作用 |
|------|------|------|
| [NewBattleDuelUI.jsx](../../battle-arena/src/components/neko-brawl/NewBattleDuelUI.jsx) | ~1704 | 新版双方对战 UI 主体。导入 `nekoBrawlAdventureDeck.js` 的 `advanceAdventureRun` / `calculateAdventureSteps` 等纯函数推进探险；正则驱动战斗日志高亮（造成 N 点 / 回复 / 护盾 / Combo / 抽 N / 封锁 / 弱化）；战斗背景图 `/neko-brawl/Background_forest.png` |
| [DeckBuilderPanel.jsx](../../battle-arena/src/components/neko-brawl/DeckBuilderPanel.jsx) | ~706 | 组卡器。常量 `DECK_SIZE=18` / `MAX_CARD_COPIES=3` / `FORGED_CARD_COPIES=1`；卡池可滚动（fix `e9d359c7`）；Forged 卡单副限 1 张（fix `718f0b8f`）；属性筛选/类型筛选/费用筛选；导出 `localStorage` keys：`neko-brawl-deck` / `neko-brawl-deck-library` / `neko-brawl-favorite-cards` |
| [DeckLibraryPanel.jsx](../../battle-arena/src/components/neko-brawl/DeckLibraryPanel.jsx) | ~509 | 卡组库。多套卡组保存/读取/导入导出，与 DeckBuilder 共享存储 key |
| [BattleResultOverlay.jsx](../../battle-arena/src/components/neko-brawl/BattleResultOverlay.jsx) | ~214 | 战斗结算覆盖层（胜/负/平 + 重启） |
| [CardInspectModal.jsx](../../battle-arena/src/components/neko-brawl/CardInspectModal.jsx) | ~162 | 单卡放大检视弹窗（含 Forged 卡完整故事正文） |
| [BattleTutorialPanel.jsx](../../battle-arena/src/components/neko-brawl/BattleTutorialPanel.jsx) | ~104 | 战斗教程 |
| [DeckBuilderTutorialPanel.jsx](../../battle-arena/src/components/neko-brawl/DeckBuilderTutorialPanel.jsx) | ~71 | 组卡教程 |
| [NekoCardBack.jsx](../../battle-arena/src/components/neko-brawl/NekoCardBack.jsx) | ~41 | 卡背图形 |
| [nekoBrawlAudio.js](../../battle-arena/src/components/neko-brawl/nekoBrawlAudio.js) | ~224 | 见 [4.5](#45-音效系统) |
| [README.md](../../battle-arena/src/components/neko-brawl/README.md) | 13 | 子目录用途说明 |

外层（顶层 `components/`）老组件，仍被部分场景沿用：

- [BattleArena.jsx](../../battle-arena/src/components/BattleArena.jsx) — 顶层壳（永久）
- [CardGamePanel.jsx](../../battle-arena/src/components/CardGamePanel.jsx)（~1896 行）— 旧版「直接 Boss 战」面板，新探索模式默认不进战斗，只有探索事件需要时才激活
- [NekoCard.jsx](../../battle-arena/src/components/NekoCard.jsx)（166 行） / [NekoAvatar.jsx](../../battle-arena/src/components/NekoAvatar.jsx)（47 行）— 卡牌与头像基础组件
- [BattleLog.jsx](../../battle-arena/src/components/BattleLog.jsx)（46 行） / [BottomTicker.jsx](../../battle-arena/src/components/BottomTicker.jsx)（41 行） / [ScoreBar.jsx](../../battle-arena/src/components/ScoreBar.jsx)（26 行）— 战斗 UI 子件

### 4.4 数据层

#### 4.4.1 基础卡池（C001–C013）

[src/data/forgedBrawlCards.js](../../battle-arena/src/data/forgedBrawlCards.js) — 13 张基础卡。

**四大属性**：

| ID | 名称 | 主元素 |
|----|------|--------|
| `passion` | 热情 | 火 |
| `gentle` | 温柔 | 心 |
| `cool` | 高冷 | 冰 |
| `natural` | 天然 | 风 |

**基础卡（节选）**：

| Code | 名称 | 主属性 | 费用 | 类型 | 效果 |
|------|------|--------|------|------|------|
| C001 | 午后扑抱 | 热情 | 1 | 攻击 | 对Boss造成1点伤害；Combo +1 |
| C002 | 亮晶晶眼神 | 温柔 | 1 | 回复 | 回复生命最低的己方玩家1点；Combo 自身回复1 |
| C003 | 尾巴在说话 | 高冷 | 1 | 防御 | 自身获得1点护盾；Combo 队友1点护盾 |
| C004 | 云朵经过的三秒 | 天然 | 1 | 抽牌 | 抽1张；Combo 额外抽1张 |
| C005 | 还没认输呢 | 热情 | 2 | 攻击 | 对Boss造成2点；Combo +1 |
| C006 | 怀中心跳 | 高冷 | 2 | 防御 | 自身受 Boss 伤害-2；Combo 队友-2 |
| C007 | 熬夜到头秃 | 高冷 | 2 | 强化 | 下回合造成伤害+2；Combo 获得2点护盾 |
| C008 | 拂面微风 | 天然 | 2 | 回复 | 双方各回1；Combo 双方各1点护盾 |
| C009 | 纸箱里的秘密计划 | 温柔 | 2 | 控制 | 对Boss造成1点，Boss 下次攻击-1；Combo +1伤 |
| C010 | 屋顶上的晚安 | 高冷 | 3 | 回复 | 双方各回2；Combo 清1个负面状态 |
| C011 | 生人勿近 | 天然 | 3 | 防御 | 对Boss造成2点，双方各1点护盾；Combo 本回合Boss伤害-1 |
| C012 | 用尽全力奔向你 | 温柔 | 3 | 攻击 | 对Boss造成4点；Combo +2伤 |
| C013 | 完全⭐奇迹 | 热情 | 4 | 控制 | 对Boss造成3点 + 封锁Boss下回合；Combo 自身2点护盾 |

> 同一份卡数据在 `DeckBuilderPanel.jsx` / `DeckLibraryPanel.jsx` 内被分别复制（不全是从 `forgedBrawlCards.js` 导出）— 这是当前原型妥协，后续接入正式 API 时建议收敛到单一 source of truth。

#### 4.4.2 临时事件池 + Forged 工具函数

`forgedBrawlCards.js` 同文件下：

- `TEMP_FORGED_CARD_EVENTS` — 6 条硬编码事件（练习室 / 便利店 / 屋檐 / 贩卖机 / 地铁站 / 手电光）
- `createForgedBrawlCard(event)` — 从基础卡 C001-C013 随机选效果 + 拼上「(Forged)」后缀 + `storyLead`
- `composeForgedCardStory()` — 把后端返回的 `story` 写入卡牌
- `loadForgedBrawlCards()` / `saveForgedBrawlCards()` — 读写 `localStorage` 的 `neko-brawl-forged-cards`
- `normalizeForgedBrawlCard()` — 反序列化兼容
- `deleteForgedBrawlCard(id)` — 删除单张

文件顶部 35 行明确写出当前规则限制（节选）：

> 1. 基础卡效果从 C001-C013 中选择；name、cost、type、主属性、主效果、Combo 效果跟随基础卡编号。
> 2. Forged 卡名保留 "(Forged)" 后缀。
> 3. Combo 属性目前随机；后续可改为由 facts 内容、LLM 评估或规则表决定。
> 4. storyLead 是 fact 抽取出的「故事引子」；story 是卡牌专属小故事。**storyLead 单独保存供鉴赏查看，不再把原始引子硬拼到故事正文里，避免破坏「第三人称叙事 + 末句第一人称台词」的格式。**
> 5. 接入 LLM 后只用 storyLead + 已 Roll 出的主属性作为提示词；卡名/羁绊名/事件标题/编号/费用/类型/效果/Combo 属性只用于规则展示，**不能参与故事生成**。

#### 4.4.3 探索牌组（Adventure Deck）

[src/data/nekoBrawlAdventureDeck.js](../../battle-arena/src/data/nekoBrawlAdventureDeck.js)（597 行）— **40 张探索牌组的纯前端规则**，**不接 UI**。

| 常量 | 值 |
|------|---|
| `ADVENTURE_DECK_SIZE` | `40` |
| `ADVENTURE_HAND_TARGET` | `6` |
| `SIDE_ADVENTURE_MIN_SIZE` / `MAX_SIZE` | `5` / `10` |

卡类型：

| 类型 | 数量 |
|------|------|
| `REST`（休息） | 6 |
| `EVENT`（事件） | 28 |
| `BATTLE`（战斗触发） | 3 |
| `ENCOUNTER`（奇遇 offer） | 2 |
| `END`（终点） | 1 |
| 合计 | **40** |

触发器：`rest-trigger` / `story-event-trigger` / `battle-trigger` / `encounter-offer-trigger` / `end-trigger`

事件类型 `ADVENTURE_EVENT_KINDS`：`CHOICE` / `CHECK` / `REWARD` / `PENALTY` / `CARD_APPRECIATION` / `RESOURCE`

事件蓝图 `EVENT_BLUEPRINTS`：每条声明 `decisionMode`（auto-resolve / choose-played-card / attribute-check）、`requirement`、`success` / `failure` 效果（draw-card / discard-random-card / gain-action-point / minor-hp-loss 等）。

导出的纯函数（被 `NewBattleDuelUI` 调用）：

- `createAdventureRun()` — 按分布抽样 + 钉重要节点位（10/20/30/40）
- `calculateAdventureSteps(playedCards)` — 双方本回合打出牌的行动力**平均值**（不是总和！见规则文档 §4）
- `advanceAdventureRun(run, steps)` — 推进位置 + 收集**经过**的重要节点 + 落点
- `describeAdventureReveal(card)` — 生成揭示文案
- `getCardActionPoint(card)` — 取 `cost` 字段作为行动力

### 4.5 音效系统

[src/components/neko-brawl/nekoBrawlAudio.js](../../battle-arena/src/components/neko-brawl/nekoBrawlAudio.js)（224 行）— 文件顶部明确：

> 当前 BGM / SFX 均为暂时占位实装用声音，**不是最终结果**。后续替换为正式版音效或正式版 BGM 时，请在对应常量或场景旁明确注明「正式版音效」或「正式版 BGM」。

导出：

- `NEKO_BRAWL_AUDIO` — 所有音频文件路径常量
- `NEKO_BRAWL_BGM_OPTIONS` — 可在 UI 切换的 BGM 选项（home / deckBuilder / deckLibrary / battle 等）
- `NEKO_BRAWL_BGM_SCENES` — 默认场景配置（src + volume）
- `playNekoBrawlSceneBgm(scene)` / `stopNekoBrawlBgm()` — 场景切换 API
- `playNekoBrawlCardSfx(kind)` — `attack` / `heal` / `shield` / `draw` / `support` / `combo`

### 4.6 静态资源

全部新增到 [battle-arena/public/](../../battle-arena/public/)：

**通用 GIF / JPG**：
- `Simple_design_judging.gif`（2.1 MB） — 判定动画
- `celebration.gif`（1.6 MB） / `cry.gif`（2.6 MB）— 胜负反应
- `waiting.gif` / `waiting_idle{1-4}.gif`（合计 ~9 MB） — 等待匹配 idle
- `background_twisted.jpg` — 背景

**`public/neko-brawl/`**：
- `Background_forest.png`（9.3 MB） — 战斗背景
- `Boss_normal_transparent.png` / `Boss_attack_transparent.png` / `Boss_damagetaken_transparent.png` / `Boss_WeakDamageTaken_transparent.png`（合计 ~18 MB）

**`public/neko-brawl/audio/`**：
- BGM：`bgm_home_brightlands_night.mp3`、`bgm_home_loop.mp3`、`bgm_deck_builder_loop.mp3`、`bgm_deck_library_loop.mp3`、`bgm_answer_quickly.mp3`、`bgm_battle_loop.mp3`
- SFX：`sfx_card_attack.mp3`、`sfx_card_heal.mp3`、`sfx_card_shield.wav`、`sfx_card_draw.wav`、`sfx_card_support.wav`、`sfx_card_combo.wav`

> 注意：本 PR 把这些大文件直接提交进 git 树，仓库体积明显增长。如果未来要替换为正式资源，建议同步评估是否走 Git LFS / 外链 CDN。

### 4.7 持久化（localStorage）

| Key | 写入方 | 用途 |
|-----|--------|------|
| `neko-brawl-forged-cards` | `forgedBrawlCards.js` | Forged 卡列表 |
| `neko-brawl-deck` | `DeckBuilderPanel` | 当前编辑中的卡组 |
| `neko-brawl-deck-library` | `DeckBuilderPanel` / `DeckLibraryPanel` | 多套卡组库 |
| `neko-brawl-favorite-cards` | `DeckBuilderPanel` | 收藏卡 |

> 未来若改为角色/账户级持久化，所有 4 个 key 都要迁移；同时需要决定「同一 fact 是否允许重复铸造」。

---

## 五、后端：`local_server/battle_arena_server/`

### 5.1 设计原则

[local_server/battle_arena_server/README.md](../../local_server/battle_arena_server/README.md) 明确写出（节选）：

> 本目录为 **Battle Arena 副产物**：可单独迭代；**不修改** N.E.K.O 的 `main_server`、`memory_server`、`memory/` 等核心模块。奇遇铸造机用 facts 时仅 **只读** 本机 JSON 或可选 HTTP，与 FactStore 落盘的 `facts.json` schema 一致。

工程文件：

- [server.py](../../local_server/battle_arena_server/server.py)（843 行） — FastAPI 应用 + CORS + 路由 + 匹配 + facts 读盘 + 故事接口
- [forge_story_generator.py](../../local_server/battle_arena_server/forge_story_generator.py)（404 行） — 卡牌故事提示词 + LLM 调度
- [active_neko_context.py](../../local_server/battle_arena_server/active_neko_context.py)（102 行） — 当前猫娘解析
- [__init__.py](../../local_server/battle_arena_server/__init__.py)（空）
- [requirements.txt](../../local_server/battle_arena_server/requirements.txt) — `fastapi` / `uvicorn[standard]` / `pydantic` / `httpx>=0.27.0`

> README 「方案变体」段落给出**无 httpx 的回退方案 B**：删 `_fetch_facts_from_url` + `requirements.txt` 删 `httpx`，路由只走本机文件。

### 5.2 HTTP 接口

| 方法 | 路径 | 处理函数 | 用途 |
|------|------|----------|------|
| `POST` | `/arena/join` | `join_arena` | 上传 `JoinRequest`（nekoName / ownerName / avatar / bonds），加入匹配队列；2 人立即配对，否则 3s 后派 dummy |
| `GET`  | `/arena/status/{player_id}` | `arena_status` | 轮询：返回 `{matched: true, opponent: {...}}` 或 `{matched: false}` |
| `POST` | `/arena/leave/{player_id}` | `arena_leave` | 离开房间（同时清 `waiting_room` / `matched`） |
| `GET`  | `/arena/forge-facts` | `arena_forge_facts` | 奇遇铸造机：从当前猫娘的 active facts 抽 5 条候选 |
| `POST` | `/arena/forge-card-story` | `arena_forge_card_story` | 用 NEKO 核心 LLM 把 `storyLead` 生成卡牌专属小故事 |
| `GET`  | `/health` | `health` | 健康检查 |

CORS：`allow_origins=["*"]`，`allow_methods=["*"]`，`allow_headers=["*"]`。

### 5.3 「当前猫娘」解析（`active_neko_context.py`）

文件顶部 doctring：

> The forge machine must follow the catgirl currently selected by NEKO itself: that is the catgirl who invited the player into Neko Brawl, and therefore the only correct memory source for facts and generated card stories.

`ActiveNekoContext`（frozen dataclass）字段：

| 字段 | 含义 |
|------|------|
| `master_name` | 主人名 |
| `lanlan_name` | 当前猫娘名 |
| `memory_dir` | NEKO 配置里的记忆根目录 |
| `facts_path` | `memory_dir / lanlan_name / facts.json` |
| `lanlan_prompt` | 当前猫娘的人格 prompt（用于故事生成 system prompt） |
| `source` | `neko-config` / `env-facts-json` / `runtime-character-hint` / `unresolved` |

**关键约束**：

- `safe_character_segment()` 校验：去空格、长度 ≤ 80、禁止 `/` `\` `..` `\x00`，防路径穿越
- `character` 参数只在 `NEKO_BRAWL_ALLOW_CHARACTER_OVERRIDE=1` 时才能覆盖当前猫娘（生产路径默认忽略）
- 优先级：`runtime_character_hint > character_override > config_manager.get_character_data()[1]`
- `memory_dir` 优先级：`$NEKO_MEMORY_DIR > config_manager.memory_dir`
- `facts_path` 优先级：`$NEKO_FACTS_JSON（单文件） > memory_dir/<猫娘>/facts.json`
- `lanlan_prompt` 从 `config_manager.get_character_data()[5][lanlan_name]` 提取，并 `replace {LANLAN_NAME} {MASTER_NAME}`
- 用 `asyncio.to_thread(_build_context, ...)` 避免阻塞事件循环

### 5.4 奇遇铸造机 / `forge_facts`

`GET /arena/forge-facts` 查询参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `character` | — | 仅 debug；默认被忽略 |
| `min_importance` | `5` | facts 最低 importance |
| `include_absorbed` | `true` | 是否含已 absorbed 事实 |
| `limit` | `5` | 抽取候选数（铸造机固定 5） |
| `exclude_fact_ids` | — | 逗号分隔，排除已铸造过的 id |
| `exclude_hashes` | — | 逗号分隔，排除已铸造过的 hash |

环境变量：

| 变量 | 作用 |
|------|------|
| `NEKO_FACTS_JSON` | 单文件直接路径 |
| `NEKO_MEMORY_DIR` | 记忆根目录覆盖（调试/迁移用） |
| `NEKO_BRAWL_ALLOW_CHARACTER_OVERRIDE` | 设为 `1` 才允许 `character` 覆盖当前猫娘 |
| `NEKO_FORGE_FACTS_URL` | 可选 HTTP 数据源；支持 `{character}` 占位；失败回退读盘 |

抽样链：

1. `_resolve_facts_path()` → 用 `active_neko_context` 拿当前猫娘 → 拼 `facts.json`
2. `_load_facts_json()` → 读盘、parse、过滤（`absorbed` / `min_importance` / `exclude_fact_ids` / `exclude_hashes`）
3. `_select_forge_facts_with_stats()` → 按 importance 加权随机抽 `limit` 条；带统计字段返回
4. （新增）`_select_archive_distant_fact()` — 候选「久远档案」型 fact 的特殊路径

`_weighted_pick()` 使用 `_importance_weight()`（importance 平滑映射）；`_fact_memory_datetime()` 解析 fact 的时间字段，用于在 UI 上展示「多久以前」。

响应 schema：

```json
{
  "character": "当前猫娘",
  "factsSource": "neko-config",
  "facts": [
    { "id", "text", "importance", "entity", "tags", "created_at", "hash" }
  ],
  "requestedLimit": 5,
  "returnedCount": 5
}
```

无数据时含 `"error": "facts_source_not_configured"` / `"facts_path_not_found"` 等枚举。

**隐私**：README 警告「facts 含个人化内容；请勿暴露到公网；日志不打印完整 `text`」。`_forge_route_log()` / `_log_value(limit=4000)` / `_clip()` 等辅助函数确保日志中 text 被截断。

### 5.5 卡牌故事生成（`forge_story_generator.py`）

入口 `async def generate_forge_card_story(payload) -> ForgeStoryResult`：

1. `_forge_request_id()` 生成请求 ID（用于日志关联）
2. `resolve_active_neko_context(runtime_character_hint=...)` 确定猫娘
3. `build_forge_story_prompt(payload)` → `(system_prompt, user_prompt)`
4. `_configured_llm_targets(config_manager)` 拿到 NEKO 配置的多个候选 LLM target（按优先级尝试）
5. `create_chat_llm(...)` + `[SystemMessage, HumanMessage]` 调模型
6. 失败处理：`asyncio.wait_for(timeout=FORGE_STORY_TIMEOUT_SECONDS=25s)`、`max_tokens=240`
7. 输出清洗：`_strip_code_fence` / `_repair_utf8_mojibake` / `_clean_story` / 字符长度截断
8. `set_active_character()` / `set_call_type()` 让 token 计数器把这次调用归到对应猫娘

**Prompt 设计要点**（这是 PR 的灵魂之一）：

System prompt：
- 锁定身份「猫娘大乱斗的记忆小故事写作者」+ 当前猫娘名 + 主人名
- 「保留原有事实关系与情绪基调，**不要新增现实中不存在的重大事实**」
- 主属性映射性格：温柔=体贴/安抚/认真倾听，热情=主动明亮，高冷=克制清冷，天然=轻快直觉
- **禁用词列表** `FORGE_STORY_FORBIDDEN_GAME_TERMS`：Combo / 连携 / 费用 / 行动力 / 效果 / 伤害 / 护盾 / 防御 / 力场 / Boss / 抽牌 / 回合 / 对局 / 战斗 / 攻击 / 卡牌机制
- **格式硬约束**：前面**第三人称**叙事一到三句 + 最后一小句中文引号 `""` 包住的**第一人称**台词

> commit `bd1331b1` 专门加强了这条「人称分段」约束（之前模型偶尔会通篇第一人称）。

User prompt（节选 11 条硬性要求）：
- 只输出故事正文，不要标题/JSON/解释
- 80–140 中文字
- **不要原样粘贴 `storyLead`，改写成自然叙事**
- 不要编造新地点/人物关系/长期承诺
- 第三人称用「她」「猫娘」「{猫娘名}」，叙事句不要用「我」「我们」做主语
- 最后一句必须中文引号 `""` 包住第一人称台词
- 主属性 Roll 出什么就贴什么气质
- **不要因为卡名 / 羁绊名 / 事件标题 / 编号 / 费用 / 类型 / 效果 / Combo 属性改变故事方向；本请求不提供这些信息，即使模型猜到也不要使用**

返回 `ForgeStoryResult`（frozen dataclass）：

```python
@dataclass(frozen=True)
class ForgeStoryResult:
    story: str
    provider: str
    model: str
    source_fact_id: str | None = None
```

失败时 `raise ForgeStoryGenerationError(...)`，路由层 catch 后返回 `success: false`；前端会把该卡标记为 `failed`，**不写入伪 LLM 故事**。

### 5.6 匹配队列 / Dummy 对手

内存存储：

```python
waiting_room: dict[str, dict] = {}  # player_id -> JoinRequest 快照
matched: dict[str, dict] = {}       # player_id -> opponent snapshot
```

匹配逻辑：

- `try_match()` — 等待室 ≥ 2 人则取前两个互相配对
- `schedule_dummy_match(player_id)` — `await asyncio.sleep(3)`；3 秒后若仍在 `waiting_room`，随机派一个 `DUMMY_OPPONENTS`（4 个虚拟猫娘），用 `_state["last_dummy_name"]` 确保**连续两次不重名**

占位数据：

- `PLACEHOLDER_BONDS` — 5 条占位羁绊（与前端 `MY_BONDS_PLACEHOLDER` 重复）
- `DUMMY_OPPONENTS` — 「迷路的猫娘」/「傲娇大猫猫」/「困困小猫咪」/「社恐猫猫」

文件头 `TODO: [羁绊列表接入]` / `TODO: [虚拟对手]` 注释明确等待真实数据。

---

## 六、主进程桥接（头像同步）

NEKO 主服务 [app/main_server.py](../../app/main_server.py) 新增内容里**仅一处**与大乱斗相关（其余是主动投递框架）：

```python
# 顶部 import
from fastapi.middleware.cors import CORSMiddleware

# app = FastAPI() 之后
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# 两条端点
_battle_arena_avatars: dict = {}   # side -> {dataUrl, name}

@app.post('/battle-arena/avatar')
async def set_battle_avatar(payload: dict): ...

@app.get('/battle-arena/avatar/{side}')
async def get_battle_avatar(side: str): ...
```

配套前端：[static/app-chat-avatar.js](../../static/app-chat-avatar.js) 新增 `syncAvatarToBattleArena(dataUrl)`，在 4 个时机调用：

1. `applyPreviewResult()`（主流程：从画布提取头像后）
2. 初始化时从 `localStorage` 取到 stored 头像（`source: 'storage'`）
3. 外部 IPC 接收（Electron `chat.html` 桥到主窗口的头像）
4. `externalAvatarDataUrl` 注入（其它入口）

任意失败仅 `console.warn`，不影响主流程。后端无持久化，进程重启即清空（这是占位行为，未来若要跨重启保留需要落盘）。

---

## 七、启动 / 停止脚本

仓库根新增：

| 文件 | 用途 |
|------|------|
| [start-battle-arena.bat](../../start-battle-arena.bat)（61 行） | Windows 一键启动：先 `server.py`，再 `vite dev`；附带等待健康检查 |
| [start_battle_arena.py](../../start_battle_arena.py)（95 行） | Python 跨平台启动器：开 3 个 PowerShell 窗口分别跑 NEKO 主服务（48911）、匹配服务（3001）、Vite 前端（5173） |
| [stop-battle-arena.bat](../../stop-battle-arena.bat)（16 行） | 关停占用 3001 / 5173 端口的进程 |
| [stop-battle-arena.ps1](../../stop-battle-arena.ps1)（50 行） | PowerShell 版关停（含端口检测 + 进程 kill 错误隔离） |

`start_battle_arena.py` 的工作流程（节选）：

```
[1/3] 开 N.E.K.O 主服务窗口 (port 48911)  → uv run .\launcher.py
      sleep 3
[2/3] 开匹配服务窗口      (port 3001)   → uv run server.py
      sleep 2
[3/3] 开前端窗口          (port 5173)   → npm run dev

URLs:
  battle-arena: http://localhost:5173
  N.E.K.O main: http://localhost:48911
  Match server: http://localhost:3001/health
```

---

## 八、探索模式规则文档

[docs/neko-brawl/exploration-rules.md](exploration-rules.md) — **1155 行规则基准文档**，本次 PR 一并提交（commit `c84df695`）。25 个一级章节：

| 章节 | 主题 |
|------|------|
| 1 | 模式定位（从「直接 Boss 战」改为「卡牌探险」） |
| 2 | 探索牌组基础结构（40 张，第 10/20/30/40 为重要节点） |
| 3 | 探索位置 |
| 4 | 行动力与探索步数（**双方平均值**，非总和） |
| 5 | 双方确认规则 |
| 6 | 基础揭示规则 |
| 7 | 重要节点卡规则 |
| 8 | 落点卡与重要节点重合 |
| 9 | 一次经过多个重要节点 |
| 10 | 已揭示事件卡浏览 |
| 11 | 探索记录数据建议 |
| 12 | UI 交互规则 |
| 13 | 战斗触发规则 |
| 14 | 支线奇遇规则 |
| 15 | 后续更新要求 |
| 16 | 术语表 |
| 17 | 探索结算算法 |
| 18 | 完整结算示例 |
| 19 | 探索卡组点击浏览界面 |
| 20 | 探索事件触发顺序 |
| 21 | 事件处理队列 |
| 22 | 探索牌组显示状态 |
| 23 | 当前已实现与未实现 |
| 24 | 当前代码对照（指向 `nekoBrawlAdventureDeck.js` / `NewBattleDuelUI.jsx`） |
| 25 | 规则变更记录 |

文档开头明确：「**后续每次调整探索规则、事件揭示规则、重要节点规则、支线规则或 UI 交互规则时，都应同步更新本文档**」。

---

## 九、端到端数据流

**奇遇铸造一张 Forged 卡的完整链路**：

```
玩家在前端点「奇遇铸造」按钮
   ↓
BattleArena.jsx 调 GET http://127.0.0.1:3001/arena/forge-facts?limit=5
   ↓
server.py: arena_forge_facts()
   ├─ active_neko_context.resolve_active_neko_context()
   │    → 从 NEKO config_manager 取 memory_dir + 当前猫娘
   │    → 拼 {memory_dir}/{猫娘}/facts.json
   ├─ _load_facts_json(facts_path)
   ├─ 过滤 absorbed / min_importance / exclude_*
   └─ _select_forge_facts_with_stats(limit=5)
   ↓
前端在 5 条 facts 里选 1 条作为 storyLead
   ↓
前端调 POST /arena/forge-card-story
        body: { storyLead, sourceFactId, card }
   ↓
server.py: arena_forge_card_story()
   ↓
forge_story_generator.generate_forge_card_story(payload)
   ├─ resolve_active_neko_context() 再次解析当前猫娘
   ├─ build_forge_story_prompt() 拼 system + user prompt
   │    (锁定第三人称叙事 + 末句第一人称台词)
   ├─ _configured_llm_targets() 取候选 LLM
   ├─ create_chat_llm().ainvoke([SystemMessage, HumanMessage])
   │    timeout=25s, max_tokens=240
   └─ _clean_story() → 字符长度截断 → ForgeStoryResult
   ↓
返回 { success: true, story, provider, model, sourceFactId }
   ↓
前端 createForgedBrawlCard() + composeForgedCardStory()
   ↓
saveForgedBrawlCards() 写入 localStorage['neko-brawl-forged-cards']
```

**头像同步链路**：

```
NEKO chat 页面提取头像 (app-chat-avatar.js)
   ↓ syncAvatarToBattleArena(dataUrl)
   ↓ POST http://localhost:48911/battle-arena/avatar
   ↓ {side: 'left', dataUrl: '...', name: 当前猫娘}
   ↓
app/main_server.py: set_battle_avatar()
   ↓ 内存存到 _battle_arena_avatars[side]
   ↓
battle-arena 前端轮询 GET /battle-arena/avatar/left
   ↓ {dataUrl, name}
   ↓ 显示在 NekoAvatar 组件
```

---

## 十、已知占位与待办

直接抄自代码 `TODO:` + README：

| 类别 | 占位项 | 替换建议 |
|------|--------|----------|
| **羁绊** | `PLACEHOLDER_BONDS` (server.py) / `MY_BONDS_PLACEHOLDER` (BattleArena.jsx) / `JoinRequest.bonds` 无校验 | 待 NEKO 主应用「羁绊记录系统」落定 |
| **虚拟对手** | `DUMMY_OPPONENTS` (server.py) | 真实联机后可移除或保留为 Bot |
| **Forged 卡效果** | 从 C001-C013 基础卡随机选效果 | 后续规则表 / LLM 评估生成专属效果 |
| **Forged 卡 Combo 属性** | 随机 | facts 内容 / LLM / 规则表决定 |
| **Forged 卡持久化** | `localStorage` | 是否升级到角色/账户级；是否允许同一 fact 重复铸造 |
| **临时事件池** | `TEMP_FORGED_CARD_EVENTS` (6 条) | 替换为真实「自身故事系统」 |
| **音效 / BGM** | `nekoBrawlAudio.js` 全部标注「占位」 | 替换正式版时旁注「正式版音效」「正式版 BGM」 |
| **资源体积** | Boss 立绘 / GIF / BGM 直接进 git，仓库膨胀显著 | 评估 Git LFS / CDN |
| **基础卡数据复制** | `forgedBrawlCards.js` / `DeckBuilderPanel.jsx` / `DeckLibraryPanel.jsx` 各有一份 C001-C013 | 收敛到单一 source of truth |
| **头像后端存储** | `_battle_arena_avatars` 仅内存 | 跨重启需落盘 |
| **奇遇铸造机数据源未配置** | 返回空 `facts` + `error` | 前端会回退到硬编码事件池 |
| **真实联机匹配** | 两人 `try_match()` 已能 work，但无房间号 / 重连 / 心跳 / 反作弊 | 联机时需要补 |
| **探索规则文档第 23 章「已实现/未实现」** | 文档自带 | 持续对齐 `nekoBrawlAdventureDeck.js` |
| **事件奖惩作用到状态** | 事件检定结果当前只显示文本（2026-05-29 实装的交互） | 把 effects 接到 hp/手牌/行动力（需父组件回调） |
| **数值累加检定数据** | 判定逻辑已就位，卡牌无命名检定数值（金钱/热心等） | 设计卡牌 `checkValues` 字段体系后启用 value 模式 |
| **终点结算后端 LLM** | 端点已加，但需 `:3001` + NEKO 主服务 LLM 配置才生效 | 起服务后验证；否则前端模板保底 |

> 注：探险五类落点交互（事件/休息/奇遇/终点；战斗已移除）已于 2026-05-29 实装，详见第 12 节与 [exploration-rules.md §23](exploration-rules.md)。

---

## 十一、文件清单速查

```
battle-arena/                                    # 前端 Vite + React 子项目
├── index.html
├── package.json / package-lock.json
├── postcss.config.js / tailwind.config.js / vite.config.js
├── public/                                       # 大量 GIF / JPG / 立绘 / BGM / SFX
│   └── neko-brawl/{Background_forest.png, Boss_*.png, audio/*}
└── src/
    ├── App.jsx / main.jsx / index.css
    ├── components/
    │   ├── BattleArena.jsx          # 顶层壳
    │   ├── CardGamePanel.jsx        # 旧版直接 Boss 战
    │   ├── BattleLog.jsx / BottomTicker.jsx / ScoreBar.jsx
    │   ├── NekoAvatar.jsx / NekoCard.jsx
    │   └── neko-brawl/              # V2 重构件
    │       ├── NewBattleDuelUI.jsx
    │       ├── DeckBuilderPanel.jsx
    │       ├── DeckLibraryPanel.jsx
    │       ├── BattleResultOverlay.jsx
    │       ├── CardInspectModal.jsx
    │       ├── BattleTutorialPanel.jsx
    │       ├── DeckBuilderTutorialPanel.jsx
    │       ├── NekoCardBack.jsx
    │       ├── nekoBrawlAudio.js
    │       └── README.md
    └── data/
        ├── forgedBrawlCards.js       # 基础卡 C001-C013 + Forged 工具
        └── nekoBrawlAdventureDeck.js # 40 张探索牌组规则

local_server/battle_arena_server/                 # 后端 FastAPI 服务
├── __init__.py
├── README.md
├── requirements.txt
├── server.py                         # 路由 + 匹配 + facts 读盘
├── forge_story_generator.py          # 卡牌故事 LLM 调度 + prompt
└── active_neko_context.py            # 当前猫娘解析

app/main_server.py                                # +CORS + 两条 /battle-arena/avatar 端点
static/app-chat-avatar.js                         # +syncAvatarToBattleArena()

start-battle-arena.bat                            # Windows 一键启动
start_battle_arena.py                             # 跨平台 Python 启动器
stop-battle-arena.bat                             # 关停 .bat
stop-battle-arena.ps1                             # 关停 .ps1

docs/neko-brawl/
├── exploration-rules.md              # 1155 行探索规则
└── pr-1454-overview.md               # 本文档
```

文件总计：**约 60 个新增 / 改动**（不含构建产物 / lock 文件），其中前端约 30，后端约 5，主进程接入约 2，启动脚本 4，文档 2，其余资源约 20。

---

## 十二、探险交互实装进展（2026-05-29）

本节记录在原型骨架之上、把"探险五类落点的交互"逐个接通的一轮改动。规则层细节同步在 [exploration-rules.md](exploration-rules.md) §13/§14.1/§23/§25。

### 12.1 战斗触发卡移除

- [nekoBrawlAdventureDeck.js](../../battle-arena/src/data/nekoBrawlAdventureDeck.js) `MAIN_DECK_DISTRIBUTION`：`BATTLE 3 → 0`，并入 `EVENT 28 → 31`，保持主牌组 40 张
- `sideDistribution`：2 张战斗换成事件
- 两处都留「恢复战斗改回这里」注释

### 12.2 抽牌动画 + 牌堆视觉

- 双方确认后播发牌动画：从牌堆逐张飞出、**叠放**到牌堆左侧同一槽位（`AdventureDealOverlay`），落点牌入场后 3D 翻面揭示
- 左侧持久「已抽 N」卡堆（`AdventureDealtPile`）+ 右侧未探索牌堆顶「剩余 N 张」浮标
- 牌堆静态呈现（抽牌期不"洗牌"）；支线推进同样有发牌动画
- 右上角「探索回合：第 N 回合」计数

### 12.3 事件卡交互（`kind: 'event'`）

- 数据层：事件 `check` 字段（属性模式按 index 轮换推荐属性；数值累加模式留接口）；`resolveEventCheck(check, 牌[])` 通用判定（属性匹配 / 多张累加 ≥ 阈值）；`pickBetterEventOutcome` 取双方较好
- 流程：揭示事件 → 玩家点手牌选响应牌 → 「完成事件」判定 → AI 队友象征性完成（优先匹配推荐属性）→ 取较好结果 → 成功/失败文本 → 继续
- **数值累加检定**判定逻辑已就位，但卡牌还没有命名检定数值字段（如金钱/热心），暂只跑属性检定

### 12.4 休息卡交互（`kind: 'rest'`）

- 落点是休息卡 → 「确认休息」→ 显示恢复 → 继续
- 恢复效果（heal-all / refill-hand）留 `restEffects` 空档，后期"特殊休息处"可扩展；当前只显示文本不改状态

### 12.5 奇遇卡交互（`kind: 'encounter'`）

- 双方确认「进入支线 / 跳过」（玩家选 → AI 队友附议）
- 进入 → `enterSideAdventure` → 后续推进自动走支线牌堆；支线内事件/休息卡复用上述交互；翻到支线终点回主线
- **步数清0**：跨支线边界的溢出步数作废（数据层"每次推进只在单个牌堆内、跨边界截断"天然保证）

### 12.6 终点卡结算（`kind: 'ending'`）

- 探险历程记录 `adventureLog`（`buildAdventureLogEntry`）累积每个落点结果
- 主线终点 → 结算页：**前端模板**保底（`buildAdventureEndingStory`）+ **后端 LLM** 生成（`forge_story_generator.generate_adventure_ending_story` + `server.py` 路由 [`/arena/adventure-ending`](../../local_server/battle_arena_server/server.py)），LLM 失败回退模板
- 故事按历程统计（成败/支线/休息）+ 当前猫娘人格生成

### 12.7 Bug 修复

- 4 费行动卡无法放入行动区：移除 [CardGamePanel.jsx](../../battle-arena/src/components/CardGamePanel.jsx) `setPreviewCard` 里误用的 `cardCost > playerEnergy` 拦截（探险模式 cost 是行动力、非能量消耗）

### 12.8 仍未实现（接力点）

- 事件奖惩**真正作用到玩家状态**（血量/手牌/行动力）——当前只显示结果文本
- 数值累加检定的**卡牌数值数据**
- 重要节点强制揭示、已揭示事件浏览界面、一回合多事件队列
- 真人队友联机（当前队友环节为 AI 象征性完成）

---

> 如需了解本分支**非大乱斗**部分的改动（主动投递框架 PR #1545、Minecraft Agent Prompt 微调等），见 [../branch-pr1454-changes.md](../branch-pr1454-changes.md)。
