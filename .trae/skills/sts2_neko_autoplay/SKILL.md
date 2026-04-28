---
name: "sts2_neko_autoplay"
description: "Design and implement neko-supervised autoplay for sts2_autoplay plugin. Invoke when user wants to add neko oversight, guidance injection, or full-step reporting to the Slay the Spire 2 autoplay plugin."
---

# STS2 猫娘监督自动游玩方案

## 背景目标

背景 LLM 全自动玩尖塔的过程中，定期上报情况给猫娘，猫娘根据需要下指令。猫娘作为"监督者/指挥官"，背景 LLM 保持高自主性。

## 核心架构：方案 C（全量每步推送 + 猫娘 LLM 过滤）

```
背景 LLM (sts2_autoplay)
  ├── 每步执行完 → 推送完整报告给猫娘
  │     ├── 当前状态快照
  │     ├── LLM 本步 reasoning 摘要
  │     ├── 决策结果
  │     └── 可用的猫娘指令通道状态
  │
猫娘 (外部 LLM / 兰兰系统)
  ├── 收到每步报告
  ├── 用自己的 LLM 判断是否干预
  └── 需要干预时 → 通过 guidance 通道发回软指令

背景 LLM 收到 guidance
  └── 注入到下一轮 LLM context 中
```

## 猫娘看到的报告格式（每步推送）

```python
{
    "step": 42,
    "screen": "combat",
    "floor": 3, "act": 1,
    "player_hp": 65, "max_hp": 80,
    "gold": 156,
    "in_combat": True,
    "turn": 2,
    "energy": 2, "block": 0,
    "hand": [
        {"name": "Strike", "playable": True, "cost": 1},
        {"name": "Defend", "playable": True, "cost": 1},
        {"name": "Beam", "playable": True, "cost": 1},
    ],
    "potions": [
        {"name": "Fire Potion", "can_use": True, "can_discard": False},
        {"name": "Swift Potion", "can_use": False, "can_discard": True},
    ],

    "enemies": [
        {
            "name": "Cultist",
            "hp": 48, "max_hp": 48,
            "block": 0,
            "intent": "attack",
            "intent_value": 18,
            "buffs": [{"id": "strength", "name": "力量", "stacks": 2}],
            "debuffs": [],
        },
        {
            "name": "Jaw Worm",
            "hp": 20, "max_hp": 24,
            "block": 6,
            "intent": "attack",
            "intent_value": 12,
            "buffs": [],
            "debuffs": [],
        }
    ],

    "tactical_summary": {
        "incoming_attack_total": 18,
        "current_block": 0,
        "remaining_block_needed": 18,
        "lethal_targets": [],
        "should_prioritize_defense": True,
    },

    "llm_reasoning": {
        "situation_summary": "正在和祭祀场僵尸战斗，对方下回合会攻击18点",
        "primary_goal": "防止本回合被打死，优先打出防御牌",
        "candidate_actions": ["play_card:defend", "play_card:strike", "end_turn"],
        "chosen_action": "play_card:defend",
        "reason": "当前 block=0，需要抵御18点攻击"
    },

    "decision_source": "half-program-llm",
    "last_action": "play_card",
    "catgirl_guidance_injected": False,
    "catgirl_guidance_pending": 0,  # 猫娘发来但尚未消费的 guidance 数量
}
```

## 猫娘发回 guidance 的格式

```python
{
    "step": 42,
    "type": "soft_guidance",
    "content": "对面是祭祀场僵尸，血量不高，可以激进一点抢血斩杀"
}
```

## 指令分层

| 类型 | 约束力 | 通道 | 说明 |
|------|--------|------|------|
| pause / resume / stop | 硬约束 | 现有 plugin entry | 控制 autoplay 状态，LLM 无需知道 |
| speed / mode 调整 | 硬约束 | 现有 plugin entry | 参数调整，LLM 无需知道 |
| 软指导 (guidance) | 软约束 | 新增 `sts2_send_guidance` | 猫娘用自然语言写，LLM 自行决定采纳程度 |

## 实现改动清单

### 1. service.py 改动

- `_autoplay_loop`：每步执行完后调用 `_report_full_step`
- `_report_full_step`：构建完整报告，通过 `_push_frontend_notification` 推送
- `_catgirl_guidance_queue`：新增，`Deque` 存储待消费的猫娘 guidance
- `_inject_catgirl_guidance`：从队列中取出所有 guidance 注入 LLM context
- `_build_llm_decision_payload`：增加 `catgirl_guidance` 字段
- `_select_action_with_llm` / `_select_action_full_model`：调用 `_inject_catgirl_guidance`

### 2. llm_strategy.py 改动

- `_build_llm_decision_payload` 或调用处：接收并处理 `catgirl_guidance` 参数

### 3. __init__.py 改动

- 新增 `sts2_send_guidance` plugin entry：接收猫娘的 guidance 入队

### 4. plugin.toml 改动

- `catgirl_reporting_enabled`：bool，是否开启猫娘汇报
- `catgirl_report_interval_steps`：int，每 N 步强制汇报（方案 C 建议 N=1）
- `catgirl_guidance_max_queue`：int，guidance 队列最大长度
- `llm_frontend_output_probability`：建议调高或设为 1.0（确保每步都推）

## 关键设计决策

- **全量推送**：背景 LLM 每步都推送完整报告，猫娘用自己的 LLM 过滤
- **软约束优先**：猫娘 guidance 全部作为软约束，LLM 自行决定采纳程度
- **硬约束独立**：pause/resume/speed/mode 走现有通道，不走 guidance 队列
- **队列滑动**：guidance 队列按 step 匹配，过期 guidance 自动丢弃
