# STS2 Autoplay 插件测试文档

## 快速验证

优先执行自动化回归测试，再进行需要游戏/本地服务的手动验证。

```shell
python -m pytest plugin/plugins/sts2_autoplay/tests -q
```

当前插件测试集应覆盖核心决策、动作参数校验、Neko 指令、实况短评、策略解析与 autoplay 循环。期望结果：全部通过；若出现 `pytest.ini` 中异步配置项未识别的 warning，可先记录为环境警告，不视为本插件功能失败。

建议分层执行：

| 场景 | 命令 | 适用时机 |
| --- | --- | --- |
| 插件全量单测 | `python -m pytest plugin/plugins/sts2_autoplay/tests -q` | 每次提交前 |
| 动作参数校验 | `python -m pytest plugin/plugins/sts2_autoplay/tests/test_action_execution.py -q` | 修改 `action_execution.py` 后 |
| 决策/最大化/绝望模式 | `python -m pytest plugin/plugins/sts2_autoplay/tests/test_decisioning.py -q` | 修改 `decisioning.py` 后 |
| 实况短评 | `python -m pytest plugin/plugins/sts2_autoplay/tests/test_live_commentary.py -q` | 修改短评/汇报逻辑后 |

***

## 环境准备

1. 确认本地游戏服务已启动，并且 Slay the Spire 2 正在运行且连接成功。
   - 期望结果：服务日志显示游戏连接正常，无连接超时或断开提示。
2. 启动兰兰/猫娘系统，并确认前端通知通道可用。
   - 期望结果：系统可接收插件汇报事件，且不会阻塞 autoplay 流程。
3. 检查 `plugin.toml` 中所有 `neko_*` 配置项。
   - 当前配置项包括：`neko_reporting_enabled`、`neko_report_interval_steps`、`neko_commentary_enabled`、`neko_commentary_probability`、`neko_commentary_min_interval_seconds`、`neko_critical_commentary_always`、`neko_guidance_max_queue`、`neko_auto_low_hp_threshold`、`neko_auto_safe_hp_threshold`、`neko_auto_dangerous_attack_threshold`、`neko_auto_resume_after_low_hp`、`neko_desperate_enabled`、`neko_desperate_hp_threshold`、`neko_maximize_enabled`、`neko_synergy_enabled`。
   - 必填项存在且格式正确：开关类配置使用布尔值，概率/阈值类配置为 0~1 浮点数，间隔/队列/伤害阈值类配置为正数。
   - `neko_commentary_probability`、`neko_commentary_min_interval_seconds` 与当前测试期望一致；若需要稳定触发实况短评，可临时将概率设为 `1.0` 且降低冷却间隔。
   - URL、端口等连接配置与当前运行环境一致；当前 `neko_*` 配置项不包含 URL、端口、token。
4. 若任一前置条件失败，先停止测试并修复环境问题，再执行后续 autoplay 测试。

***

## 一、基础功能测试

### 1.0 自动化测试基线

每次修改插件代码后，先确认以下自动化测试通过：

```shell
python -m pytest plugin/plugins/sts2_autoplay/tests -q
```

重点关注以下回归点：

- 未知 `requires_index=True` 动作不能盲填 `index=0`，只有解析到真实候选时才暴露或回填 `index`。
- `play_card` 决策必须具备合法 `card_index`；模型只给 `target_index` 时应先尝试 fallback 补齐，补不齐则拒绝。
- maximize 在 `0` 能量或能量刚好用尽后仍应继续评估 `0` 费可打牌。
- `hp=0`、`energy=0`、`turn=0` 等合法零值不能被 `or` fallback 吃掉。


### 1.1 启动与停止

| 步骤 | 操作                        | 预期结果                                      |
| -- | ------------------------- | ----------------------------------------- |
| 1  | 调用 `sts2_start_autoplay`  | autoplay 启动，`_autoplay_state` = `running` |
| 2  | 调用 `sts2_pause_autoplay`  | autoplay 暂停，`_paused` = `True`            |
| 3  | 调用 `sts2_resume_autoplay` | 后台任务仍存在时恢复，`_paused` = `False`；后台任务不存在时返回 `idle` 且不隐式重启 |
| 4  | 调用 `sts2_stop_autoplay`   | autoplay 停止并清除半自动任务，`_autoplay_state` = `idle` |

### 1.2 日志确认

启动 autoplay 后观察日志，确认以下 tag 出现：

```
[sts2_autoplay] autoplay started
[sts2_autoplay][neko-auto] autonomous action: slow_down  (boss/危险战斗)
[sts2_autoplay][neko-auto] autonomous action: pause      (低HP)
[sts2_autoplay][maximize] energy=X sequence=[...]        (利益最大化)
[sts2_autoplay][desperate] selected attack card=...      (绝望模式)
```

***

## 二、Neko 汇报功能测试

### 2.1 开关测试

```toml
# plugin.toml
neko_reporting_enabled = true   # 开启汇报
neko_reporting_enabled = false  # 关闭汇报
```

### 2.2 汇报内容验证

当 `neko_reporting_enabled=true` 时，每步检查：

1. `_frontend_notifier` 是否收到调用（metadata 中 `event_type = "neko_report"`）
2. 报告包含完整字段：

```python
{
    "step": int,
    "screen": str,           # "combat" / "map" / "shop" 等
    "floor": int,
    "act": int,
    "player_hp": int,
    "max_hp": int,
    "gold": int,
    "in_combat": bool,
    "turn": int,
    "energy": int,
    "block": int,
    "hand": [                # 每张牌详情
        {"name": str, "playable": bool, "cost": int},
        ...
    ],
    "potions": [
        {"name": str, "can_use": bool, "can_discard": bool},
        ...
    ],
    "enemies": [              # 每个敌人详情
        {
            "name": str,
            "hp": int, "max_hp": int, "block": int,
            "intent": str, "intent_value": int,
            "buffs": [{"id": str, "name": str, "stacks": int}],
            "debuffs": [{"id": str, "name": str, "stacks": int}],
        },
        ...
    ],
    "tactical_summary": {
        "incoming_attack_total": int,
        "remaining_block_needed": int,
        "should_prioritize_defense": bool,
        "should_prioritize_lethal": bool,
    },
    "llm_reasoning": {
        "situation_summary": str,
        "primary_goal": str,
        "candidate_actions": [str],
        "chosen_action": str,
        "reason": str,
    },
    "neko_guidance_injected": bool,
    "neko_guidance_pending": int,
}
```

### 2.3 Guidance 注入测试

调用 `sts2_send_neko_guidance(content="对面虚弱了，可以rush")`

- 确认返回值 `{"status": "ok", "queue_size": N}`
- 下一轮 LLM 决策时，查看 LLM prompt 中是否包含该 guidance

***

## 三、自主风险判断测试

### 3.1 低 HP 暂停

```toml
neko_auto_low_hp_threshold = 0.3   # 30% HP 触发
neko_auto_resume_after_low_hp = true
neko_auto_safe_hp_threshold = 0.5  # 恢复阈值
```

| 步骤 | 操作               | 预期                                            |
| -- | ---------------- | --------------------------------------------- |
| 1  | 将玩家 HP 打到 30% 以下 | 日志出现 `autonomous action: pause reason=low_hp` |
| 2  | HP 恢复至 50% 以上    | 日志出现 `autonomous action: resume`              |

### 3.2 Boss/危险战斗减速

```toml
neko_auto_dangerous_attack_threshold = 20
```

| 步骤 | 操作                        | 预期                                                     |
| -- | ------------------------- | ------------------------------------------------------ |
| 1  | 进入 boss 战斗（Act1 floor≥12） | `autonomous action: slow_down reason=boss_combat`      |
| 2  | 敌方意图攻击 ≥ 20 且护甲不足         | `autonomous action: slow_down reason=dangerous_combat` |
| 3  | 减速期间观察                    | `action_interval_seconds` 临时变为 3.0                     |

### 3.3 日志关键词

```
[sts2_autoplay][neko-auto] autonomous action: pause    reason=low_hp
[sts2_autoplay][neko-auto] autonomous action: slow_down reason=boss_combat
[sts2_autoplay][neko-auto] autonomous action: slow_down reason=dangerous_combat
[sts2_autoplay][neko-auto] autonomous action: resume   reason=hp_recovered
```

***

## 四、绝望模式测试

```toml
neko_desperate_enabled = true
neko_desperate_hp_threshold = 0.2  # 20%
```

### 4.1 HP 触发

| 步骤 | 操作               | 预期                              |
| -- | ---------------- | ------------------------------- |
| 1  | 将玩家 HP 打到 20% 以下 | 日志出现 `decision: desperate-mode` |
| 2  | 查看选出的牌           | 应为攻击牌（最高伤害）                     |

### 4.2 必然死亡触发

| 步骤 | 操作                  | 预期                |
| -- | ------------------- | ----------------- |
| 1  | 敌方意图伤害 > 玩家 HP + 护甲 | 进入绝望模式            |
| 2  | 确认选牌                | 绕过 LLM，直接选最高伤害攻击牌 |

### 4.3 日志关键词

```
[sts2_autoplay][desperate] selected attack card=XXX damage=N target=X
```

***

## 五、利益最大化/配合出牌测试

```toml
neko_maximize_enabled = true
neko_synergy_enabled = true
```

### 5.1 基础利益最大化

**测试场景**：普通战斗，手牌有 Strike/Defend/Skill

| 步骤 | 预期                                |
| -- | --------------------------------- |
| 1  | 利益最大化层在 LLM 之前拦截                  |
| 2  | 日志出现 `decision: maximize-benefit` |
| 3  | 贪心序列正确：防御够用先防，不够用先攻               |

### 5.2 配合出牌检测

**测试场景**：手牌包含 Setup 牌 + Attack 牌

| 组合                  | 预期行为                     |
| ------------------- | ------------------------ |
| Weaken + Strike     | 先出 Weaken（得分高），再出 Strike |
| Inflame + Strike    | 先充力量，再攻击                 |
| Vulnerable + Strike | 先上易伤，再攻击                 |
| Draw + Strike       | 先抽卡，再攻击                  |

**验证日志**：

```
[sts2_autoplay][maximize] energy=3 sequence=[('Weaken', None), ('Strike', 0)]
  lethal:False def:False incoming:12 block:0 str:1 weak:1 vuln:0
```

### 5.3 关键词匹配测试

测试 `_detect_card_synergy_type` 对不同卡牌的分类：

| 卡牌数据                                                                | card\_type | 预期分类             |
| ------------------------------------------------------------------- | ---------- | ---------------- |
| `{"name": "Strike", "card_type": "attack"}`                         | attack     | `attack`         |
| `{"name": "Defend", "card_type": "skill", "description": "获得5点护甲"}` | skill      | `block`          |
| `{"name": "Weaken", "card_type": "skill"}`                          | skill      | `weaken`         |
| `{"name": "Inflame", "card_type": "power"}`                         | power      | `strength_boost` |
| `{"name": "Dualcast", "card_type": "skill"}`                        | skill      | `orb_evoke`      |
| `{"name": "Zap", "card_type": "skill"}`                             | skill      | `orb_channel`    |

### 5.4 能量约束与 0 费牌

**测试场景 A**：3 能量，手牌 4 张（各 1 费）

| 预期             |
| -------------- |
| 贪心选出 3 张得分最高的牌 |
| 第 4 张因能量不足跳过   |

**测试场景 B**：当前能量为 0，手牌包含 0 费攻击牌和 1 费高伤害牌

| 预期 |
| --- |
| maximize 仍会评估 0 费牌 |
| 1 费牌因能量不足被 `_calc_marginal_benefit` 过滤 |
| 日志中 `sequence` 至少包含可执行的 0 费牌 |

**测试场景 C**：先打出一张刚好耗尽能量的牌，剩余手牌仍有 0 费牌

| 预期 |
| --- |
| maximize 不因 `sim_energy == 0` 提前停止 |
| 后续 0 费牌仍会进入贪心序列评估 |

对应自动化用例：

```shell
python -m pytest plugin/plugins/sts2_autoplay/tests/test_decisioning.py -q
```

***

## 六、Neko 实况短评测试

```toml
neko_commentary_enabled = true
neko_commentary_probability = 0.65
neko_commentary_min_interval_seconds = 4
neko_critical_commentary_always = true
```

### 6.1 开关与概率

| 步骤 | 操作 | 预期 |
| -- | -- | -- |
| 1 | 设置 `neko_commentary_enabled=false` | `neko_report` 仍推送，但 `live_commentary.should_speak=false` |
| 2 | 设置 `neko_commentary_enabled=true` 且 `neko_commentary_probability=1.0` | 普通场景短评稳定触发 |
| 3 | 设置 `neko_commentary_probability=0.0` | 非关键场景短评被概率抑制 |

### 6.2 冷却与关键场景

| 步骤 | 操作 | 预期 |
| -- | -- | -- |
| 1 | 连续触发同一低优先级场景，间隔小于 `neko_commentary_min_interval_seconds` | 后续短评被冷却抑制 |
| 2 | 低 HP、斩杀、敌方高伤等高/关键优先级场景，且 `neko_critical_commentary_always=true` | 可绕过概率限制触发短评 |
| 3 | 战斗结束、获得关键遗物、选择路线等事件场景 | 同一楼层同类事件只短评一次 |

***

## 七、Guidance 队列测试

```toml
neko_guidance_max_queue = 50
```

### 7.1 入队

| 步骤 | 操作                 | 预期                                                           |
| -- | ------------------ | ------------------------------------------------------------ |
| 1  | 连续发送 10 条 guidance | 每条返回 `queue_size` 递增                                         |
| 2  | 发送第 51 条           | 队列满，最早一条被挤出                                                  |
| 3  | 空 content 发送       | 返回 `{"status": "error", "message": "guidance.content 不能为空"}` |

### 7.2 消费

| 步骤 | 操作                         | 预期                      |
| -- | -------------------------- | ----------------------- |
| 1  | 发送一条 guidance              | guidance 进入队列           |
| 2  | 执行下一步 autoplay             | 队列被 drain，注入 LLM prompt |
| 3  | 查看 `neko_guidance_pending` | 变为 0                    |

***

## 八、动作参数与边界值回归测试

### 8.1 `play_card` 参数完整性

| 场景 | 预期 |
| --- | --- |
| LLM 返回 `play_card` 且 `kwargs={"target_index": 0}` | 校验层尝试用当前可打牌 fallback 补齐 `card_index` |
| fallback 得到的 `card_index` 在 `allowed_kwargs.card_index` 内 | 决策通过，并保留原始 `target_index` |
| fallback 不可用或越界 | 决策被拒绝，不执行半残 `play_card` |
| LLM 返回非法参数名 | 决策被拒绝 |
| LLM 返回越界 `card_index` / `target_index` | 决策被拒绝 |

对应自动化用例：

```shell
python -m pytest plugin/plugins/sts2_autoplay/tests/test_action_execution.py -q
```

### 8.2 未知索引动作

| 场景 | 预期 |
| --- | --- |
| 未知动作 `requires_index=true` 且没有候选 | 不暴露 `index=[0]`，也不回填 `index=0` |
| 未知动作 `requires_index=true` 且 `raw` 中存在通用候选 | 只暴露解析到的真实候选索引 |
| 执行补参需要索引时 | 只在真实候选存在时选择第一个候选 |

### 8.3 合法零值不能被 fallback 覆盖

| 字段 | 测试位置 | 预期 |
| --- | --- | --- |
| `player.hp = 0` | 绝望模式、LLM payload、Neko 汇报、Neko 自主样本、地图摘要 | 保留 `0`，不能回退到 `run.current_hp` / `run.hp` |
| `player.energy = 0` | 汇报、Neko 自主样本、maximize | 保留 `0`，且 maximize 仍评估 0 费牌 |
| `turn = 0` | payload、汇报、自主样本 | 保留 `0`，除非字段确实缺失 |
| `floor = 0` / `act = 0` | 地图摘要、模型归一化 | 保留 `0`，不回退到旧字段 |

***

## 九、配置项汇总

```toml
# ====== Nekoneko 自主监督 ======
neko_reporting_enabled = true           # 是否推送每步报告
neko_report_interval_steps = 1          # 汇报间隔（步）
neko_commentary_enabled = true           # 是否生成/推送每步实况短评
neko_commentary_probability = 0.65       # 普通场景短评触发概率（0~1）
neko_commentary_min_interval_seconds = 4 # 同类短评最小间隔（秒）
neko_critical_commentary_always = true   # 高/关键优先级短评是否绕过概率限制
neko_guidance_max_queue = 50             # guidance 队列最大长度

# ====== 自主风险判断 ======
neko_auto_low_hp_threshold = 0.3         # 低HP暂停阈值
neko_auto_safe_hp_threshold = 0.5       # 自动恢复血量阈值
neko_auto_dangerous_attack_threshold = 20 # 危险攻击阈值
neko_auto_resume_after_low_hp = true     # 低HP后是否自动恢复

# ====== 绝望模式 ======
neko_desperate_enabled = true
neko_desperate_hp_threshold = 0.2       # 绝望模式触发阈值

# ====== 利益最大化 ======
neko_maximize_enabled = true
neko_synergy_enabled = true
```

***

## 十、日志分析指南

### 10.1 决策来源识别

```
decision: desperate-mode     → 绝望模式（绕过LLM）
decision: maximize-benefit   → 利益最大化（绕过LLM）
decision: heuristic          → 启发式（full-program）
decision: half-program-llm   → 半程序+LLM
decision: full-model         → 全模型LLM
decision: half-program-heuristic-fallback  → LLM失败回退
```

### 10.2 maximize 日志字段说明

```
[sts2_autoplay][maximize]
  energy=N          # 本回合总能量
  sequence=[(...)]   # 贪心选出的出牌序列
  lethal:bool        # 是否优先击杀
  def:bool           # 是否优先防御
  incoming:N         # 即将受到的伤害
  block:N            # 当前护甲
  str:N              # 力量层数（出牌后）
  weak:N             # 虚弱层数（出牌后）
  vuln:N             # 易伤层数（出牌后）
```

### 10.3 neko-auto 日志字段说明

```
[sts2_autoplay][neko-auto]
  autonomous action: pause       reason=low_hp hp_ratio=0.2
  autonomous action: slow_down  reason=boss_combat floor=12
  autonomous action: slow_down  reason=dangerous_combat incoming_attack=25 remaining_damage=15
  autonomous action: resume     reason=hp_recovered hp_ratio=0.55
  slow_down: interval 1.5 -> 3.0
  resume: interval restored to 1.5
```

***

## 十一、回归测试

每次代码更新后，确认以下功能不受影响：

| 功能 | 验证方式 |
| --- | --- |
| 基础 autoplay 循环 | 启动后能持续执行 step |
| LLM 决策（half/full-model） | mode=full-model 时 LLM 被调用 |
| 暂停/恢复 | pause → resume 后继续执行 |
| 奖励卡牌预判 | 商店/地图事件时不出错 |
| 汇报推送 | `neko_reporting_enabled=false` 时不推送 |
| 未知索引动作 | 无候选时不盲填 `0`，有真实候选时才使用候选 |
| `play_card` 参数 | 缺 `card_index` 时补齐或拒绝，不能执行半残参数 |
| 0 费牌 maximize | 0 能量时仍能选择 0 费牌，能量耗尽后仍继续评估 0 费牌 |
| 0 HP / 0 energy | 汇报、payload、地图摘要、自主样本均保留合法零值 |

***

## 十二、已知局限

1. **充能球（Orb）叠层未追踪**：`Dualcast + Lightning Orb` 的协同收益估算不精确
2. **敌方 Buff 层数未精确计算**：只判断存在性，不区分 1 层/2 层
3. **遗物效果未考虑**：Kunai、Shuriken 等依赖攻击次数的遗物
4. **多目标 AOE 卡牌**：伤害收益计算为单体，未汇总对所有敌人的总伤害
5. **Boss 变身阶段**：Hexaghost 等boss的变身血量线未特别处理

如遇到实际问题，可通过日志中的 `maximize` 和 `desperate` 输出定位具体哪一层决策出错。
