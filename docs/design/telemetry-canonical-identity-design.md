# Telemetry canonical identity — device ⟷ steam_id 多对多聚合设计

**状态：定稿 / 交接运维实现。** 决策已拍板（见 §6），实现与后续归属交由运维。本文是实现规格，不含代码。客户端无需改动（见 §2）。

**两条边界先讲清楚：**
- **公开 repo 只保留"收数据"那一段**（上报接收 + 数据模型 + "我们收什么"的透明说明）。canonical 归并、看板、指标这些是**内部分析**，不进公开 repo —— 它们是已收数据的纯消费方，没有暴露给用户的必要。
- **归并不碰 ingest**：边由**内部 job 事后扫 `events` 产出**，不在上报路径里产边。公开的收数据代码一行都不用动。

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

- **不改客户端**。客户端每次上报本来就带 `device_id` + 本 session 当前登录的 `steam_user_id`，这条配对已落进 `events.payload`，是边的来源。改的全在内部分析侧。
- **不改公开 repo 的收数据代码**。ingest / models 不动。边由内部 job 扫 `events` 产出（见 §3.1），归并、指标、看板全在内部分析侧，不进公开 repo。
- 不删现有 device 级查询。canonical 是叠加层，device 维度保留（"这台机器这次怎么启动"仍有意义）。
- 不引入 PII / 实名。仍只存 Steam64（Steam 公开 ID，非实名）+ 匿名 device_id。

## 3. 数据模型

### 3.1 edge 表（append-only 观测事实）

```sql
CREATE TABLE IF NOT EXISTS device_steam_edges (
    device_id      TEXT    NOT NULL,
    steam_user_id  TEXT    NOT NULL,        -- 归一化十进制 Steam64（沿用 server.py ingest 归一化）
    first_seen     TEXT,                       -- 该 steam 边被观测到的最早事件时间（events.received_at）；NULL = 时间未知（纯连通兜底边，见附录源 1）
    last_seen      TEXT,                       -- 同上，最晚观测时间
    observe_count  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (device_id, steam_user_id)
);
CREATE INDEX IF NOT EXISTS idx_edge_steam ON device_steam_edges(steam_user_id);
```

**边由内部 job 扫 `events` 产出，不在 ingest 路径里产边。** 这样公开 repo 的收数据代码完全不动，归并逻辑全在内部分析侧。job 增量处理新 `events`（按 `received_at` / `id` 游标推进），对每条 payload 里的 `steam_user_id` 跑归一化 + denylist 过滤（见附录），合法才 UPSERT：

```sql
-- 时间戳必须用事件观测时间 events.received_at（第 3/4 个参数），绝不用 job
-- 的 wall-clock now —— 否则全量回填会把所有历史边盖上同一个回填运行时间，
-- §4 按 canonical first_seen 算 cohort/留存会全部塌缩进回填窗口、彻底失真。
-- 仅当归一化后非空且不在 denylist 才产边；空值不携带新关联信息，跳过。
INSERT INTO device_steam_edges (device_id, steam_user_id, first_seen, last_seen, observe_count)
VALUES (?, ?, ?, ?, 1)                              -- 第 3、4 参数 = 该事件 received_at
ON CONFLICT(device_id, steam_user_id) DO UPDATE SET
    -- COALESCE 包一层做 NULL-safe：SQLite min(NULL,x)=NULL，若撞上源 1 的纯连通
    -- NULL 边会把真实时间抹掉。COALESCE(min(a,b), a, b) 保证真实值恒胜 NULL。
    first_seen    = COALESCE(MIN(device_steam_edges.first_seen, excluded.first_seen), device_steam_edges.first_seen, excluded.first_seen),
    last_seen     = COALESCE(MAX(device_steam_edges.last_seen,  excluded.last_seen),  device_steam_edges.last_seen,  excluded.last_seen),
    observe_count = device_steam_edges.observe_count + excluded.observe_count;
```

> `first_seen`/`last_seen` 无 DEFAULT、可空。源 2（events 扫描）这条主路径**总是显式传 `received_at`**，永远非空；只有附录源 1 的纯连通兜底边才留 NULL（时间未知）。绝不用 `now` —— 见上方注释。

- 观测事实表，不删（除 §7 GDPR 删号）
- 首次跑 = 全量扫历史 `events`（即回填）；之后增量扫新 `events`。**回填和实时产边是同一份代码**，不存在两套规则漂移。
- `devices.steam_user_id` 单列**保留不动**（公开 repo 写路径不变），canonical 解析不依赖它，改读边表

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

> 实施注意：反复合并会形成长链（A→B→C→D），解析旧 ID 要多跳。每次重指时顺手 **path compression**——把所有指向旧 survivor 的 alias 行直接改指最终 canonical（`UPDATE canonical_alias SET new_canonical_id = <最终> WHERE new_canonical_id = <旧 survivor>`），保持 alias 表恒为一跳。当前量级多跳成本可忽略，但写进规格免得将来踩。

物化策略见 §6.1（已定：落表定期重算）。

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

## 5. 与 device_id legacy fold 的关系（已定：合一套）

`device_id_legacy`（算法升级导致同一台机器有新旧两个 device_id）本质也是身份归并 —— 是一条 **device⟷device 边**。**决定：两条线合成一套**，不各搞一套（否则两套身份图会打架）。

- 加一张 `device_alias_edges(device_id_a, device_id_b, first_seen, last_seen)`，边源 = `events.payload.device_id_legacy`（同样内部 job 扫 events 产出）。
- canonical 解析的 union-find **同时吃两类边**：`device_steam_edges`（device⟷steam）+ `device_alias_edges`（device⟷device）。一个连通分量里可以同时有新旧 device_id 和多个 steam 账号，全归一个 canonical。
- 运维原来单独搞的 legacy fold 不再需要独立逻辑，变成喂给统一框架的一类边。

归属与上线时序由运维定（本文是交接规格）。

## 6. 决策（已拍板）

1. **canonical 物化 = 落表定期重算（A）**。建 `canonical_map(entity_type, entity_id, canonical_id)`，轻量 union-find job 定期重算（规模就几千边，全量重算毫秒级）。落表后所有指标查询变普通 JOIN，不背图遍历成本。不走查询期递归 CTE。

2. **legacy fold 合进同一套（见 §5）**。`device_alias_edges` + `device_steam_edges` 两类边喂同一个 union-find，运维原 legacy fold 收编为一类边，不另搞身份图。

3. **看板不进公开 repo**。公开 repo 只对用户透明"收什么数据、怎么收"；DAU/MAU/canonical/留存这些是内部分析工具，放内部，不暴露细节。内部看板里 device 口径（装机量）和 canonical 口径（真实用户）并列，呈现方式由运维定。

4. **回填认栽，但当前可全量覆盖**。原则上 `events` 只留 180 天（`prune_old_events`），更早的边随事件清理丢失、不硬补。**但 telemetry 上线至今仅 90 天 < 180 天 retention，一条都没被 prune**，所以现在回填能覆盖全部历史、无缺口 ——"补不回"的限制是为将来超 180 天后准备的。

5. **edge 不记 distribution-at-observation**。边只管"谁和谁关联过"。渠道历史已在 `events.payload`，要审计去那查，不在边表冗余。

## 7. 隐私

- 仍零 PII：Steam64 是 Steam 公开 ID（非实名），device_id 是匿名哈希
- `canonical_id` 是内部代理键，不外泄、不下发客户端

**GDPR / 删号必须防"删后复活"**：只删边 + 重算不够 —— 源数据里还有两处会让被删 Steam 标识重新产边：`devices.steam_user_id` 单列、以及 `events.payload` JSON（180 天内）。任一回填 / 重建任务都会把它捞回来。完整流程：

1. **tombstone / denylist**：被删的 `steam_user_id` 进一张 `steam_id_denylist` 表。边构建 job（无论全量回填还是增量）都先查 denylist，命中即跳过，绝不产边。
2. **源数据脱敏**：清掉 `devices.steam_user_id`（置空）；`events.payload` 里的字段按 events 表 180 天 retention 自然过期，或对该 Steam64 主动 scrub（JSON 改写）。
3. **重算**：删边后重跑受影响连通分量的 union-find。

denylist 是防复活的硬约束，retention 过期是兜底，两者都要，不能只靠其一。

## 8. 实施阶段（运维执行）

1. `device_steam_edges` 表 + 边构建 job（扫 `events` 产边；首跑即全量回填，之后增量）
2. union-find 重算 + `canonical_map` / `canonical_alias` 落表（§3.2、§6.1）
3. canonical 口径指标（内部看板，device + canonical 并列，§4、§6.3）
4. `device_alias_edges` + legacy fold 收编进同一 union-find（§5、§6.2）
5. `steam_id_denylist` + GDPR 删号流程（§7）

阶段 1 的边构建 job 本身就是回填（同一份代码全量扫一遍历史），无单独回填阶段。全程不碰公开 repo 的收数据代码、不改现有 device 级指标语义（canonical 是叠加，不是替换）。

## 附：边构建 / 回填参考（归一化是关键）

边构建 job 的主路径是**源 2**（扫 `events`）；源 1（`devices` 单列）只是补充兜底。两者归一化状态不同，务必注意：

> ⚠️ **两个数据源的归一化状态不同**：`devices.steam_user_id` 是 ingest 时**已归一化**写入的（`server.py` 137-146：纯数字 + `len<=20` + `0 < int < 2^64` + `str(int())` 去前导零、排除 `0`/`00` 哨兵），可直接产边。但 `events.payload` 存的是**原始客户端 payload**（`server.py:112` 为 HMAC 验签保留原文），其中 `steam_user_id` **未经归一化**——`"00076561198..."`、超 u64 界值、`"0"` 哨兵都可能在里面。直接 `json_extract` 回填会把同一身份拆成多个节点、或把哨兵/垃圾混进图，污染连通分量。

```sql
-- 源 1：仅作 graph 连通性兜底，针对将来 events 被 prune 后、devices 单列仍保有
-- 最后一个 steam_user_id 的 device。
-- ⚠️ 边时间戳留 NULL，绝不从 devices.first_seen/last_seen 取 —— 那是 device
--    生命周期，不是这个 steam_user_id 在该 device 上被观测的时间。device 先跑几周
--    release 才登录 Steam / 切号的话，会把边 first_seen 倒填到 device 出生时刻，
--    再被 §3.1 的 MIN() 锁死，污染 cohort/留存时间语义。NULL = 时间未知，纯连通边。
-- ⚠️ 必须在源 2（events 扫描，带真实 received_at）跑完之后再跑：INSERT OR IGNORE
--    只补源 2 没覆盖到的 device，绝不覆盖已有真实时间戳。
INSERT OR IGNORE INTO device_steam_edges (device_id, steam_user_id, first_seen, last_seen, observe_count)
SELECT device_id, steam_user_id, NULL, NULL, 1
FROM devices
WHERE steam_user_id != '';
```

> 当前 telemetry 上线 90 天 < 180 天 retention、events 一条没 prune，源 2 已覆盖全部历史，**源 1 此刻完全冗余、暂不需要跑**；它是为将来超 180 天后准备的连通性兜底。cohort/留存按 §4 锚 `device.first_seen`，NULL 时间戳的连通边不参与时间度量，不造成偏差。

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

> 因为边构建 job 全量首跑 = 回填、之后增量用同一份代码，归一化只有一处实现，天然没有规则漂移。`_normalize_steam_id` 与公开 repo `server.py` ingest 的归一化规则保持一致（值校验口径相同），denylist 过滤在同一步生效。
