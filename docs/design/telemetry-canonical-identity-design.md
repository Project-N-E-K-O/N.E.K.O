# Telemetry canonical identity — device ⟷ steam_id 多对多聚合设计

**状态：design doc / 征求决策。** 本文只定模型与开放问题，代码待 reviewer 拍板后另推 server PR。客户端无需改动（见 §3）。

## TL;DR

现状 telemetry 把 `device_id` 当唯一身份单位，DAU/WAU/MAU/留存全是 `COUNT(DISTINCT device_id)`。但真实身份是**多对多**：一台 device 可登录多个 Steam 账号、一个 Steam 账号可跨多台 device，这些应聚合成一个 canonical 身份（一个真人 / 一个账号簇）。当前 `devices.steam_user_id` 单列存不下多对多的边，导致：

- 同一真人多设备 → 多个 device → **DAU/MAU 高估、留存低估**
- 运维诊断的 **Cohort C（steam + 空 steam_user_id）占 34.72%**：首启 Steam 记下 ID，后续非 Steam 启动上报空值，单列模型无法把"这台 device 曾关联过某 Steam 账号"这条边留住
- 运维另一条线的 **device_id legacy fold**（算法升级导致同机两个 device_id）本质也是身份归并，应统一进同一框架

方案：建 `device_steam_edges` 边表（append-only 观测），canonical_id = device⟷steam 二部图的连通分量。distribution / "是不是 Steam 用户" 改为 canonical 级派生。

## 1. 背景与问题

### 1.1 现状身份模型

| 表 | 身份字段 | 说明 |
|---|---|---|
| `devices` | `device_id` (PK) | 唯一身份单位，单列 `steam_user_id`（只写不读） |
| `daily_aggregates` | `device_id` | 所有指标 GROUP BY / DISTINCT 的维度 |
| `events` | `device_id` + `payload` JSON | payload 里还藏着 `device_id_legacy`（Pydantic 不声明、被 ignore） |

`storage.py` 里 `get_user_metrics()` 的 DAU/WAU/MAU/留存全部 `COUNT(DISTINCT device_id)`。**没有任何查询读 `devices.steam_user_id`**——它目前是死字段，ingest 归一化后写入就再没被消费。

### 1.2 多对多的现实

- **一 device → 多 steam_id**：一台机器上多个 Steam 账号轮流登录（家庭共享、二手机器、多账号党）
- **一 steam_id → 多 device**：同一玩家在台式机 + 笔记本 + 公司机都装了
- 这些通过共享端点连成一个簇：device A↔steamX、device B↔steamX → A、B 是同一人；device B↔steamY → Y 也并进来

单列 `devices.steam_user_id` 表达不了：它只能记一个 ID，第二个账号登录要么覆盖要么丢，**恰好抹掉连通分量需要的边**。

### 1.3 Cohort C 的真正成因

运维诊断 steam + 空 steam_user_id = 544/1567 = 34.72%。根因不是 workshop 残留噪声，而是：device 首启 Steam 时 `GetSteamID()` 拿到 ID 上报过一次，后续启动没开 Steam 客户端（或换账号），上报空值。在边表模型里这台 device 仍保有那条历史边；在单列模型里这关联只剩个孤零零的 `steam` 标记、ID 没了。

## 2. 目标与非目标

**目标**

- 把 `device_id` 和 `steam_user_id` 都当身份"端点"，多对多边聚合成 `canonical_id`
- DAU/MAU/留存提供 **canonical 口径**（按真人去重），与现有 device 口径并存
- distribution / "Steam 用户占比" 改为 canonical 派生：簇内有任一 Steam 边 → Steam 用户

**非目标**

- **不改客户端**。客户端每次上报本来就带 `device_id` + 本 session 当前登录的 `steam_user_id`，这就是一条边的来源。改的全在 server。
- 不删现有 device 级查询。canonical 是叠加层，device 维度保留（"这台机器这次怎么启动"仍有意义）。
- 不引入 PII / 实名。仍只存 Steam64（Steam 公开 ID，非实名）+ 匿名 device_id。

## 3. 数据模型

### 3.1 edge 表（append-only 观测事实）

```sql
CREATE TABLE IF NOT EXISTS device_steam_edges (
    device_id      TEXT    NOT NULL,
    steam_user_id  TEXT    NOT NULL,        -- 归一化十进制 Steam64（沿用 server.py ingest 归一化）
    first_seen     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')),
    last_seen      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours')),
    observe_count  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (device_id, steam_user_id)
);
CREATE INDEX IF NOT EXISTS idx_edge_steam ON device_steam_edges(steam_user_id);
```

ingest 时（在 `store_event` 内、`steam_user_id` 归一化之后）：

```sql
-- 仅当本次上报 steam_user_id 非空才产边；空值不携带新关联信息，不插
INSERT INTO device_steam_edges (device_id, steam_user_id)
VALUES (?, ?)
ON CONFLICT(device_id, steam_user_id) DO UPDATE SET
    last_seen = strftime('%Y-%m-%dT%H:%M:%f+08:00', 'now', '+8 hours'),
    observe_count = observe_count + 1;
```

- 观测事实表，不删（除 §7 GDPR 删号）
- `devices.steam_user_id` 单列**保留不动**（不破坏现有写路径），但 canonical 解析不再依赖它，改读边表

### 3.2 canonical 解析

`canonical_id` = device⟷steam 二部图的连通分量代表元：

- 节点 = 所有出现过的 `device_id` ∪ 所有出现过的 `steam_user_id`
- 边 = `device_steam_edges` 每行一条
- 一个连通分量 = 一个 canonical（一个真人 / 账号簇）

规模：当前约 16k device、1.5k steam、边数千。并查集（union-find）在这个量级是毫秒级，全量重算完全可接受。

#### 代表元选择必须确定性（否则重算后 canonical_id 抖动）

光说"代表元"不够——若 union-find 合并顺序不同导致同一张图算出不同 `canonical_id`，看板/缓存/外部引用会无意义 churn。固化规则：

- 节点先按命名空间区分，避免 device_id 哈希与 Steam64 串空间碰撞：steam 节点记为 `s:<steam64>`、device 节点记为 `d:<device_id>`。
- **代表元 = 分量内最小 steam 节点**（字典序），因为 Steam 账号比 device_id 稳定（device 重装会换 ID，Steam 账号长存）；分量内**无 steam 节点**时（纯 device 簇，如 release/source 没登录过 Steam）退化为最小 device 节点。
- 这条规则对固定边集是确定的，重算不抖。

**合并不可避免的 churn 要可追溯**：两个分量因新边并成一个时，survivor 的 `canonical_id` 取并集后的最小 steam 节点，败方成员被重指。这是"两个身份被发现是同一人"的语义必然，但下游外部引用需要能跟随——加一张 `canonical_alias(old_canonical_id, new_canonical_id, merged_at)` 历史表，重指时写一条，外部引用可顺着 alias 链解析到当前 canonical。

两种物化策略见 §6 决策点 1。

### 3.3 distribution 派生

- "是不是 Steam 用户" 不再看 `devices.distribution` 单列，而是：**该 canonical 的连通分量里是否存在 ≥1 条边**（即旗下任一 device 曾上报过非空 steam_user_id）
- `devices.distribution` 列保留，语义降级为"这台 device 这次启动的渠道"（描述单次启动，不再当身份判定）
- 好处：彻底绕开 #1443 修的那个 race ——即使某次上报渠道判定抖动，只要历史上产过一条 Steam 边，canonical 永远算 Steam 用户

## 4. canonical 口径指标

与现有 device 级指标**并存**，dashboard 提供口径切换：

- **canonical DAU** = 当日有任一旗下 device 活跃的 canonical 数（`COUNT(DISTINCT canonical_id)`）
- **留存**：cohort 锚点用 canonical first_seen（旗下最早 device 的 first_seen）
- device 级指标不动，作为"安装量 / 设备数"口径继续存在

这直接修正 §1.2 的高估/低估：多设备真人折叠成一个 canonical 后 DAU/MAU 下修、留存上修。

## 5. 与 device_id legacy fold 的关系

`device_id_legacy`（算法升级导致同一台机器有新旧两个 device_id）本质也是身份归并 —— 是一条 **device⟷device 边**。两种收编方式见 §6 决策点 2。核心是：legacy fold 不该独立于 canonical 另搞一套，否则两套身份图会打架。需跟运维对齐这条线归属与时序。

## 6. 决策点（请 reviewer 拍板）

> 以下为待定项，本文不预设结论，定了再写代码。

1. **canonical 物化策略**
   - A. 落表 `canonical_map(entity_type, entity_id, canonical_id)`，轻量 union-find job 定期/增量重算。查询快，但有 staleness + 需调度。
   - B. 查询期递归 CTE 现算。实时无 staleness，但每次指标查询都要跑图遍历。
   - **倾向 A**：规模小（边数千），全量重算毫秒级，落表后所有指标查询变成普通 JOIN，dashboard 不背图遍历成本。可在每次 ingest 批后或定时（如每 5 分钟）重算。

2. **device_id legacy fold 收编**
   - A. 统一进 `device_steam_edges` 之外再加一张 `device_alias_edges(device_id_a, device_id_b)`，canonical 解析同时吃两类边。
   - B. legacy fold 作为 canonical 解析**前**的 device_id 规整（pre-merge 成规范 device_id），再在规范 device 上跑 steam 边。
   - 需运维确认这条线现在归谁、时序如何。

3. **指标默认口径**：dashboard 默认展示 device 级、canonical 级、还是并列双栏？

4. **回填范围**：edge 表从历史回填，但 `events` 表只留 180 天（`prune_old_events`），更早的边已随事件清理丢失。可接受吗？还是先从 `devices.steam_user_id` 现有非空值兜一批底（虽只剩每 device 最后一个 ID）？

5. **edge 是否记 distribution-at-observation**：边只管"谁和谁关联过"，还是顺便记每次观测时的 distribution（便于审计渠道判定历史）？倾向只管关联，渠道历史已在 `events.payload`。

## 7. 隐私

- 仍零 PII：Steam64 是 Steam 公开 ID（非实名），device_id 是匿名哈希
- `canonical_id` 是内部代理键，不外泄、不下发客户端

**GDPR / 删号必须防"删后复活"**：只删边 + 重算不够 —— 源数据里还有两处会让被删 Steam 标识重新产边：`devices.steam_user_id` 单列、以及 `events.payload` JSON（180 天内）。任一回填 / 重建任务都会把它捞回来。完整流程：

1. **tombstone / denylist**：被删的 `steam_user_id` 进一张 `steam_id_denylist` 表。ingest 产边和所有回填脚本都先查 denylist，命中即跳过，绝不产边。
2. **源数据脱敏**：清掉 `devices.steam_user_id`（置空）；`events.payload` 里的字段按 events 表 180 天 retention 自然过期，或对该 Steam64 主动 scrub（JSON 改写）。
3. **重算**：删边后重跑受影响连通分量的 union-find。

denylist 是防复活的硬约束，retention 过期是兜底，两者都要，不能只靠其一。

## 8. 实施阶段（拍板后）

1. `device_steam_edges` 表 + ingest 产边（纯增量，不动现有写/读路径）
2. 历史回填 job（范围见决策点 4）
3. canonical 解析（物化策略见决策点 1）
4. canonical 口径指标 + dashboard 口径切换
5. device_id legacy fold 收编（跟运维对齐后，见决策点 2）

每阶段独立可上线，前一阶段不阻塞客户端、不改现有指标语义（canonical 是叠加，不是替换）。

## 附：回填 SQL 草稿（决策点 4 定了再用）

> ⚠️ **两个数据源的归一化状态不同**：`devices.steam_user_id` 是 ingest 时**已归一化**写入的（`server.py` 137-146：纯数字 + `len<=20` + `0 < int < 2^64` + `str(int())` 去前导零、排除 `0`/`00` 哨兵），可直接产边。但 `events.payload` 存的是**原始客户端 payload**（`server.py:112` 为 HMAC 验签保留原文），其中 `steam_user_id` **未经归一化**——`"00076561198..."`、超 u64 界值、`"0"` 哨兵都可能在里面。直接 `json_extract` 回填会把同一身份拆成多个节点、或把哨兵/垃圾混进图，污染连通分量。

```sql
-- 源 1：从 devices 已归一化的非空 steam_user_id 兜底（每 device 仅最后一个 ID，聊胜于无）。
-- 这一支可纯 SQL，因为 devices 列写入时已归一化。
INSERT OR IGNORE INTO device_steam_edges (device_id, steam_user_id, first_seen, last_seen, observe_count)
SELECT device_id, steam_user_id, first_seen, last_seen, 1
FROM devices
WHERE steam_user_id != '';
```

```python
# 源 2：从 events.payload 回填完整观测边（180 天内）。必须复用 ingest 的归一化，
# 不能纯 SQL —— u64 范围检查、去前导零无法在 SQLite 里可靠表达。用 server 侧脚本：
def _normalize_steam_id(raw: str) -> str:
    """与 server.py ingest 同一套规则；不合法返回 ''（不产边）。"""
    if raw and raw.isdigit() and len(raw) <= 20 and 0 < int(raw) < (1 << 64):
        return str(int(raw))
    return ""

# 遍历 events，json 解析 payload，对 steam_user_id 跑 _normalize_steam_id，
# 非空且不在 denylist（见 §7）才 UPSERT 进 device_steam_edges，
# first_seen/last_seen 取 received_at 的 MIN/MAX、observe_count 累加。
```

> 回填和实时 ingest 必须共用同一个 `_normalize_steam_id`（抽成共享函数），否则两条写路径规则漂移又会拆裂身份。denylist 过滤也要在这一步生效。
