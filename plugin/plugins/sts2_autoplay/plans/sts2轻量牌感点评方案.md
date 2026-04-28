# STS2 轻量牌感点评方案

## 背景

当前 `sts2_autoplay` 已经支持猫娘观察、实况解说、软指导、单卡推荐、单卡代打和半自动游玩，但对用户类似“我牌打得怎么样呢？”这类陪玩式复盘表达，当前自然语言入口会偏保守地落入未知意图。

本方案目标不是做严格的手动牌序审计，也不是还原最优解，而是增加一个低成本、低风险、有陪玩感的“轻量牌感点评”能力：猫娘根据最近可见局面和粗略变化，温柔地夸奖、吐槽并给出下一步建议。

## 目标

1. 用户说“我牌打得怎么样呢？”、“评价一下刚才这手”、“吐槽一下我刚才的出牌”等时，插件能自然响应。
2. 响应必须安全：不操作游戏、不需要确认、不误触发出牌或自动游玩。
3. 不追求完整还原用户手动牌序，只做基于最近快照的轻量猜测。
4. 输出风格偏猫娘陪玩：简短、温柔、允许吐槽，但不要严厉判分。
5. 日志和上下文保持精简，不把完整 `raw_state` 长期存入内存或上报前端。

## 非目标

1. 不做复杂动作序列搜索。
2. 不做完整手动牌序还原。
3. 不要求证明最优打法。
4. 不把猫娘设计成严格裁判或分数评测系统。
5. 不默认把完整快照日志推送到前端。

## 用户体验示例

用户：

```text
我牌打得怎么样呢？
```

猫娘上报给主程序的轻量观察：

```text
轻量牌感观察：我只能根据最近看到的局面粗略判断。玩家这几手整体偏进攻，敌人血线压得不错，节奏感较好；但如果敌人本回合仍有较高来袭伤害，当前防御余量可能偏薄。建议主程序用陪玩口吻反馈：先肯定压血线做得好，再温和提醒下次先估算防御缺口，再决定是否全力打伤害。
```

日志不足时上报：

```text
轻量牌感观察：最近连续局面不足，无法可靠判断完整牌序。只能粗略看出当前局面尚可；如果敌人来袭伤害偏高，优先补防会更稳。建议主程序向用户说明“猫娘刚开始观察，还需要再多看几手”，不要给出确定性复盘结论。
```

复杂变化看不清时上报：

```text
轻量牌感观察：刚才手牌、血量或状态变化较快，猫娘没有稳定看清每一张牌。只能按血量、格挡和敌人状态推测：本轮节奏偏激进，压血线效果不错，但防守可能需要更稳。建议主程序把该结论包装成轻量参考，而不是严格牌序审计。
```

## 总体设计

新增一个“轻量牌感点评”流程：

```text
轮询/刷新状态
  -> 保存精简快照到 recent_snapshot_log
用户触发牌感点评
  -> 读取最近若干快照
  -> 生成粗略观察 rough_observations
  -> 规则模板或 LLM 生成猫娘点评
  -> 返回 review 结果并可推送前端
```

## 数据结构

### 最近快照队列

在服务对象中新增内存队列：

```python
self._recent_snapshot_log: Deque[Dict[str, Any]] = deque(maxlen=60)
```

建议配置项：

```toml
neko_review_snapshot_log_max_entries = 60
neko_review_recent_snapshot_count = 8
neko_review_llm_enabled = false
```

MVP 可以先不加配置，直接写死默认值，后续再暴露到 `plugin.toml`。

### 精简快照字段

每次发布快照时，只保留轻量摘要：

```json
{
  "time": 1710000000.0,
  "step": 12,
  "screen": "combat",
  "act": 1,
  "floor": 5,
  "turn": 2,
  "in_combat": true,
  "hp": [42, 80],
  "block": 5,
  "energy": 2,
  "hand": ["打击", "防御", "电击"],
  "enemies": [
    {"name": "史莱姆", "hp": 12, "max_hp": 20, "intent": "attack", "intent_value": 8}
  ],
  "tactical": {
    "incoming_attack": 8,
    "need_block": 3,
    "lethal": false,
    "def": true
  }
}
```

不要保存完整 `raw_state`，避免内存和 token 压力。

## 意图识别

新增方法：

```python
def _is_neko_review_text(self, text: str) -> bool:
    return self._neko_text_has_any(text, [
        "打得怎么样",
        "打的怎么样",
        "牌打得怎么样",
        "牌打的怎么样",
        "评价一下",
        "吐槽一下",
        "复盘一下",
        "刚才打得如何",
        "刚才是不是打错",
        "有没有打错",
        "更优打法",
    ])
```

在 `neko_command` 中，建议放在 advice 判断之前：

```python
if normalized_scope == "review" or (normalized_scope == "auto" and self._is_neko_review_text(text)):
    result = await self.review_recent_play_lightly(objective=raw_command)
    return self._wrap_neko_command_result("review", "review_recent_play_lightly", result, executed=False)
```

注意：该入口永远 `executed=False`。

## 服务方法设计

新增方法：

```python
async def review_recent_play_lightly(self, objective: Optional[str] = None) -> Dict[str, Any]:
    snapshots = self._select_recent_review_snapshots()
    observations = self._build_light_review_observations(snapshots)
    review = await self._render_light_review(objective=objective, snapshots=snapshots, observations=observations)
    await self._notify_neko_review_event(review, observations=observations)
    return {
        "status": "ok",
        "message": review["summary"],
        "summary": review["summary"],
        "review": review,
        "observations": observations,
        "snapshot_count": len(snapshots),
        "executed": False,
    }
```

## 粗略观察规则

MVP 先用规则，不依赖 LLM。

可生成的观察：

1. `low_hp`：当前血量低。
2. `greedy_attack`：敌人来袭高、格挡缺口大、敌人血量下降明显。
3. `good_pressure`：敌人血量下降明显。
4. `good_defense`：格挡增加且来袭伤害被覆盖。
5. `hand_used_well`：手牌数量减少、能量也消耗，说明牌使用较充分。
6. `unclear_sequence`：相邻快照变化过大，无法看清具体牌序。
7. `possible_lethal`：战术摘要显示斩杀机会。
8. `safe_position`：血量健康、防御压力不大。

示例观察结构：

```json
{
  "kind": "greedy_attack",
  "severity": "medium",
  "text": "敌人还有来袭压力，但当前格挡偏低，看起来可能稍微贪输出。",
  "confidence": 0.62
}
```

## 输出策略

### 模板版 MVP

按观察选择 2~4 句：

- 先夸一句。
- 再轻吐槽一句。
- 最后给一个建议。

示例模板：

```text
我按最近看到的局面粗略猜一下喵：{positive}。不过 {issue}。下次可以 {tip}。
```

### LLM 增强版

后续可把 `observations` 和最近快照摘要交给 LLM，要求输出 JSON：

```json
{
  "summary": "...",
  "positive": "...",
  "tease": "...",
  "tip": "...",
  "confidence_note": "..."
}
```

Prompt 限制：

1. 只基于给定观察和快照。
2. 必须使用“看起来、可能、我猜”等软表达。
3. 不允许编造具体牌序。
4. 不要求用户确认。
5. 不调用任何游戏动作。
6. 风格温柔、有陪玩感。

## 前端推送事件

新增事件类型：

```text
neko_card_review
```

metadata 建议：

```json
{
  "plugin_id": "sts2_autoplay",
  "event_type": "neko_card_review",
  "observation_only": true,
  "not_task_completion": true,
  "review": {
    "summary": "...",
    "positive": "...",
    "tease": "...",
    "tip": "..."
  },
  "snapshot_count": 8
}
```

前端/TTS 可以播报 `summary`，但不需要展示完整快照。

## 插件入口

在插件类中新增入口：

```python
@plugin_entry(
    id="sts2_review_recent_play_by_neko",
    name="猫娘牌感点评",
    description="当用户询问我牌打得怎么样、帮我复盘、评价一下、吐槽一下刚才出牌时调用。只做轻量点评，不操作游戏。",
    llm_result_fields=["summary"],
    input_schema={"type": "object", "properties": {"objective": {"type": "string"}}},
)
async def sts2_review_recent_play_by_neko(self, objective: Optional[str] = None, **_):
    ...
```

同时 `sts2_neko_command` 自动路由到该能力。

## 单元测试计划

### 意图识别测试

新增用例：

```python
def test_neko_command_review_does_not_execute(service):
    result = run(service.neko_command("我牌打的怎么样呢？"))
    assert result["intent"] == "review"
    assert result["executed"] is False
```

### 模板生成测试

构造最近快照：

1. 敌人来袭高、格挡低、敌人血量下降：应输出“有点贪输出/防御少”。
2. 格挡足够、血量健康：应输出“打得稳”。
3. 快照不足：应输出“刚开始看/只能粗略猜”。
4. 变化过大：应输出“没完全看清牌序”。

## 实施步骤

### 第一阶段：MVP

1. 在服务初始化中增加 `_recent_snapshot_log`。
2. 在 `_publish_snapshot` 中追加精简快照。
3. 增加 `_build_light_review_observations`。
4. 增加 `review_recent_play_lightly`。
5. 增加 `_is_neko_review_text`。
6. 修改 `neko_command` 路由。
7. 增加插件入口 `sts2_review_recent_play_by_neko`。
8. 增加单元测试。
9. 更新 README 的普通用户推荐说法。

### 第二阶段：LLM 润色

1. 增加 `neko_review_llm_enabled` 配置。
2. 构造专用 prompt。
3. 使用现有 LLM 调用能力生成更自然的猫娘吐槽。
4. LLM 失败时回退模板版。

### 第三阶段：更细推断，但保持轻量

1. 对相邻快照生成低成本变化摘要。
2. 标记高/中/低置信度。
3. 只让猫娘使用高/中置信度观察。
4. 不做严格牌序重建。

## 风险与约束

1. 手动出牌没有真实事件流，所以点评只能是猜测。
2. 快照间隔较大时，可能漏掉多张牌操作。
3. 同名牌、抽牌、弃牌、消耗、生成牌会让具体牌序不可判定。
4. 需要在输出里避免绝对表达，例如“你一定打错了”。
5. 该能力必须保持 `executed=False`，避免破坏安全边界。

## 最终判断

该功能应定位为“陪玩式轻量反馈”，而不是“牌序审计”。

用户问“我牌打得怎么样呢？”时，猫娘只需要做到：

1. 接住话题。
2. 基于最近局面给粗略判断。
3. 轻轻吐槽或夸奖。
4. 给一个下一步可执行建议。
5. 明确不操作游戏。

这样能显著提升陪玩体验，同时不会给主程序和猫娘增加过高负担。
