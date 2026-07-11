# 小剧场测试剧情：雨窗边的约定

> 历史说明：对应测试剧本 JSON 已因内容质量不达标而删除，本文仅保留为历史设计记录，不再代表当前可运行内容。

## 历史状态

对应的 `config/theater/stories/rainy_window_test_story.json` 已删除。本文档只记录当时用于验证故事选择和 Story Package NSN v2 图路由的设计，不属于当前故事列表，也不能再通过 API 启动。

历史用途：

1. 验证 `/theater` 页面能展示多条故事。
2. 验证用户在开始前切换故事后，前端会更新 story summary 和 `story_id`。
3. 验证 `/api/theater/session/start` 会按选中的 `story_id` 启动对应初始 scene。
4. 验证第二条故事仍遵守 `setup -> escalation -> convergence -> ending` 的 MVP 阶段约束。
5. 验证雨窗故事不再携带旧 `event_pool`，剧情推进只走 `narrative_nodes / edges`。
6. 验证用户拒绝落幕后不会强制结束，也不会回落旧续演事件。

## 剧情概览

故事 ID：

```text
rainy_window_test_story
```

标题：

```text
雨窗边的约定
```

一句话简介：

```text
雨夜停电后，用户和猫娘在窗边寻找备用灯，并决定要不要一起守住一个小小约定。
```

核心体验：

- 用不同于“初相遇”的停电雨夜场景测试故事切换。
- 场景更偏陪伴和安抚，便于观察 Persona 回复是否随故事变化。
- 分支由 NSN v2 节点和边约束雨夜陪伴、边界冲突和小剧场内约定。

## 历史 Story Package 设计

已删除的 JSON 当时覆盖：

1. `background`：限定故事只发生在小剧场内的雨夜停电情境。
2. `theme`：不安不需要被戳破，也可以被一起守住。
3. `narrative_questions`：用户先解决环境问题，还是先照顾猫娘情绪。
4. `key_turning_points`：灯灭、寻找备用灯、备用灯亮起后的约定。
5. `possible_endings`：`quiet_rain_promise` 与 `rain_distance`。
6. `restrictions`：不写成现实停电，不替用户行动，不自动写普通长期记忆。
7. `style_settings`：中等旁白密度、短对白、柔和情绪、第三人称限制视角。
8. `initial_state`：setup 阶段起步，结局吸引为空。

## 历史 Event Pool 迁移记录

该 JSON 删除前的最后版本已不再携带 `event_pool`，旧事件含义当时已拆进 NSN 节点：

1. `rain_lamp_search` → `node_rain_lamp_search`。
2. `quiet_rain_promise` → `node_quiet_rain_promise`。
3. `rain_boundary_pressure` → `node_rain_boundary_pressure`。
4. 拒绝落幕后继续演绎不再使用 `rain_continue_after_refusal` 事件；v2 graph 无候选时返回显式暂停，不回落旧事件池。

## 阶段设计

### setup：雨声忽然靠近

开场文本：

```text
窗外的雨声忽然压过了房间里的安静。灯灭下去的一瞬间，猫娘站在窗边没有动，只是耳朵轻轻抖了一下。她小声说：“只是停电而已，我才没有怕。”
```

建议按钮：

- 先找备用灯
- 陪她站在窗边听雨
- 轻声问她是不是不安

### escalation：黑暗里的小动作

阶段文本：

```text
你们在桌边摸索备用灯。她嘴上说自己看得很清楚，手却不小心碰到了你的袖口，又很快缩回去。
```

建议按钮：

- 把备用灯递给她
- 假装没发现她紧张
- 提议一起等灯亮

### convergence：共用一盏灯

阶段文本：

```text
备用灯终于亮了。小小的光把窗边照出一圈暖色，她看了看雨，又看了看你，像是在等你先说那个约定。
```

建议按钮：

- 约定下次停电也一起等
- 让她先拿着灯
- 说雨停前都陪着她

### ending：安静的约定

阶段文本：

```text
雨声慢慢变轻。她把备用灯放到你们中间，声音也放低了一点：“那就说好了……只是今晚而已。”
```

建议按钮：

- 认真答应她
- 轻轻说晚安
- 把灯留在窗边

## Anchor 设计

本故事使用与 MVP Anchor Engine 兼容的五类意图：

1. `support_rainy_window`：用户安抚猫娘的不安，给她稳定感。
2. `exploration_rainy_window`：用户寻找停电原因、备用灯或窗边线索。
3. `intimacy_rainy_window`：用户提出陪伴、约定或一起等待。
4. `avoidance_rainy_window`：用户选择离开、独自处理或保持距离。
5. `conflict_rainy_window`：用户嘲笑、强迫或否定猫娘的不安。

## 已停用的历史验收点

以下项目仅说明该测试剧本删除前曾覆盖的范围，不再作为当前版本验收入口：故事切换、雨窗摘要、初始 Scene、普通记忆隔离、内部字段隐藏、NSN v2 字段加载和图路由按钮生成。当前验收只以正式剧本及其自动化测试为准。
