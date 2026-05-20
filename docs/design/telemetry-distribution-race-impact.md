# Telemetry `distribution` × `steam_user_id` race — impact assessment

**Draft / 仅评估阶段 — 此 PR 暂未含代码改动。修复 commit 待运维确认评估后再推。**

## TL;DR

`utils/token_tracker.py` 在 `_report_to_server()` 里连续调了两个相互独立的辅助函数 `_get_telemetry_distribution()` 和 `_get_telemetry_steam_user_id()`，二者各自向 Steamworks SDK 询问 `Users.GetSteamID()`。Steamworks 是异步 init —— 两次调用如果跨越 SDK login callback 边界，第一次拿 0、第二次拿 Steam64，就出现 `distribution='release' + steam_user_id=<非空 Steam64>` 的矛盾态。Server 端 UPSERT 把 `release` 当作合法值（非 sentinel）写入，且对 event_count=1 的用户没机会被后续 `steam` 上报覆写，故卡死。

## 1. Root cause

代码现状（worktree path：`utils/token_tracker.py`）：

- `_is_steam_sdk_engaged()` ([utils/token_tracker.py:523](utils/token_tracker.py)) 三个 OR 信号，首选 `sw.Users.GetSteamID() > 0`
- `_get_telemetry_steam_user_id()` ([utils/token_tracker.py:565](utils/token_tracker.py)) 独立又调一次 `sw.Users.GetSteamID()`
- `_get_telemetry_distribution()` ([utils/token_tracker.py:591](utils/token_tracker.py)) 调前者
- `_report_to_server()` 在 [utils/token_tracker.py:1142-1143](utils/token_tracker.py) 紧挨着两行连调

```python
telemetry_distribution = _get_telemetry_distribution()    # 内部 GetSteamID() #1 → 可能 0
telemetry_steam_user_id = _get_telemetry_steam_user_id()  # 内部 GetSteamID() #2 → 已就绪 Steam64
```

只要 Steamworks SDK 的 login callback 在两次调用之间 fire，就有矛盾态。

### 触发画像

最易复现的用户群是「首次启动的 Steam 版用户」：

1. `Users.GetSteamID()` #1 ⇒ 0（Steam 客户端登录回调还没回）
2. `Workshop.GetNumSubscribedItems()` ⇒ 0（新用户没订阅工坊）
3. `workshop_config.json` 不存在（首启从没写过）

→ `distribution='release'`

紧接着 `Users.GetSteamID()` #2 ⇒ Steam64（login callback 已 fire）

→ `steam_user_id='76561198xxxxxxxxx'`

矛盾态写入 server。

### 时间线

| 时刻 | PR | 影响 |
|---|---|---|
| 2026-05-13 08:47 | #1329 | `distribution` 字段上线 |
| 2026-05-13 10:59 | #1330 | `steam_user_id` 字段上线 — **此时起才可能出现矛盾态** |
| 2026-05-20（今日） | — | 距 #1330 约 7 天 |

## 2. Server 端 persistence 模型

参考 [local_server/telemetry_server/storage.py:207-222](local_server/telemetry_server/storage.py)：

```sql
ON CONFLICT(device_id) DO UPDATE SET
  distribution  = CASE WHEN excluded.distribution  = 'unknown' THEN devices.distribution  ELSE excluded.distribution  END,
  steam_user_id = CASE WHEN excluded.steam_user_id = ''        THEN devices.steam_user_id ELSE excluded.steam_user_id END,
  ...
```

**Sentinel 设计**：

- `distribution` sentinel = `'unknown'` → preserve historical
- `steam_user_id` sentinel = `''` → preserve historical
- `'release'` / `'steam'` / `'source'` 三者**都不是** sentinel，互相覆写

**含义**：

- 首次上报写入 `release` 后，**只要该 device 再来一次 `steam` 上报**（SDK 已 warm 起来），矛盾就被覆写消除。
- 受影响时间分布**集中在 event_count=1 cohort**（首次上报 race 后没再回来过）。
- event_count > 1 的矛盾态在 `devices` 当前快照里看不见，但 `events.payload` audit log 留底，可回放。

## 3. 可直接观测的污染（visible cohort）

### Q1. 矛盾态 device 总数

```sql
SELECT COUNT(*) AS contradicted
FROM devices
WHERE distribution = 'release' AND steam_user_id != '';
```

解读：直接命中污染的 device 数。绝对量 + 在 `release` 总数中的占比一起看。

### Q2. distribution 分类对照表（带 Steam ID 比例）

```sql
SELECT
  distribution,
  COUNT(*)                                              AS devices,
  SUM(CASE WHEN steam_user_id != '' THEN 1 ELSE 0 END)  AS with_steam_id,
  ROUND(100.0 * SUM(CASE WHEN steam_user_id != '' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_with_steam_id
FROM devices
GROUP BY distribution
ORDER BY devices DESC;
```

解读：

- 健康基线 = `steam` 行 `pct_with_steam_id` 应接近 100%
- `release` 行 `pct_with_steam_id` **应趋近 0%**（独立发行版理论上不带 Steamworks）
- `release` 行此值显著 > 0% 即定量定性此 race 的污染程度
- `source` 行可能有少量 Steam ID（开发者在本地跑且 Steam 客户端开着），**这不是 bug 而是 by-design**，仅作参考基线

### Q3. 矛盾态的 event_count 分布

```sql
SELECT
  CASE
    WHEN event_count = 1 THEN '1 (single-shot)'
    WHEN event_count BETWEEN 2 AND 5 THEN '2-5'
    WHEN event_count BETWEEN 6 AND 20 THEN '6-20'
    ELSE '21+'
  END AS event_count_bucket,
  COUNT(*) AS devices
FROM devices
WHERE distribution = 'release' AND steam_user_id != ''
GROUP BY event_count_bucket
ORDER BY MIN(event_count);
```

解读：若 `1 (single-shot)` 占比 > 80%，验证「首启 race 后没回来」假说成立 → 这批用户多半是流失的，业务影响相对可控但 cohort 标签错。`21+` 桶不应该存在（按 server 覆写逻辑，多次上报就该被改正） —— 若不为 0，说明该 device 多次 race，需要额外排查（譬如 Steamworks 在该机型上 init 异常慢）。

### Q4. 矛盾态的 first_seen — last_seen 时间间隔分布

```sql
SELECT
  CASE
    WHEN (julianday(last_seen) - julianday(first_seen)) * 86400 < 60 THEN '<1 min'
    WHEN (julianday(last_seen) - julianday(first_seen)) * 86400 < 3600 THEN '1-60 min'
    WHEN (julianday(last_seen) - julianday(first_seen)) * 86400 < 86400 THEN '1-24 h'
    WHEN (julianday(last_seen) - julianday(first_seen)) < 7 THEN '1-7 days'
    ELSE '7+ days'
  END AS session_span,
  COUNT(*) AS devices,
  SUM(event_count) AS total_events
FROM devices
WHERE distribution = 'release' AND steam_user_id != ''
GROUP BY session_span
ORDER BY MIN(julianday(last_seen) - julianday(first_seen));
```

解读：与 Q3 互证。`<1 min` 桶 + `event_count=1` 的高度重叠 ⇒ 这些用户首次报告后立刻流失。

### Q5. 矛盾态 first_seen 按日分布（发现突变）

```sql
SELECT
  DATE(first_seen) AS first_seen_date,
  COUNT(*) AS contradicted_devices,
  -- 同日所有 release 设备数做对照
  (SELECT COUNT(*) FROM devices d2
   WHERE d2.distribution = 'release' AND DATE(d2.first_seen) = DATE(d1.first_seen)) AS all_release_same_day,
  ROUND(100.0 * COUNT(*) /
        NULLIF((SELECT COUNT(*) FROM devices d2
                WHERE d2.distribution = 'release' AND DATE(d2.first_seen) = DATE(d1.first_seen)), 0), 2) AS pct
FROM devices d1
WHERE distribution = 'release' AND steam_user_id != ''
GROUP BY first_seen_date
ORDER BY first_seen_date;
```

解读：

- 2026-05-13 之前应**无矛盾态**（`steam_user_id` 字段还没上线）—— 若有，说明此报告对时间线的理解有误，需进一步查
- 按日 `pct` 列若稳定（譬如每天都是 ~X%），说明 race 概率与 SDK init 时长相关、与版本无关
- 若某日 `pct` 突变（如某次 Steamworks 库升级 / 客户端更新），是定向回归的信号

### Q6. 与 event_count=1 cohort 的重合度

```sql
WITH e1 AS (
  SELECT COUNT(*) AS total FROM devices WHERE event_count = 1
),
e1_contradicted AS (
  SELECT COUNT(*) AS total FROM devices
  WHERE event_count = 1 AND distribution = 'release' AND steam_user_id != ''
),
all_contradicted AS (
  SELECT COUNT(*) AS total FROM devices
  WHERE distribution = 'release' AND steam_user_id != ''
)
SELECT
  (SELECT total FROM e1)                AS event_count_1_total,
  (SELECT total FROM e1_contradicted)   AS event_count_1_and_contradicted,
  (SELECT total FROM all_contradicted)  AS all_contradicted,
  ROUND(100.0 * (SELECT total FROM e1_contradicted) /
                NULLIF((SELECT total FROM all_contradicted), 0), 2) AS pct_of_contradicted_in_e1,
  ROUND(100.0 * (SELECT total FROM e1_contradicted) /
                NULLIF((SELECT total FROM e1), 0), 2)               AS pct_of_e1_contradicted;
```

解读：

- `pct_of_contradicted_in_e1`：矛盾态用户里有多少是 event_count=1（验证「首启 race 后没回来」假说）。预期 > 80%。
- `pct_of_e1_contradicted`：event_count=1 用户里有多少被 race 污染。是个独立的"标签可信度"指标。

## 4. 隐藏 cohort —— Q1 看不到的受影响用户

> **运维注意**：只盯 `release + 非空 steam_user_id` 这一个 symptom 会**显著低估**真实受影响人数。下面三类用户都受同一 race 影响但 Q1 抓不到。

### Cohort B：曾经被错标，后续上报已覆写

事件：用户首启 race → 写入 `release`。同一用户后续再启动 → SDK warm 起来 → 上报 `steam` → server 覆写 `devices.distribution` 为 `steam`。

当前 `devices` 表已"自愈"，但 **`events.payload` 留底**。

```sql
-- 找出至少有一次 race-tainted 上报、但当前 devices.distribution != 'release' 的设备
SELECT
  COUNT(DISTINCT device_id) AS recovered_devices
FROM events e
WHERE event_date >= '2026-05-13'
  AND json_extract(e.payload, '$.distribution') = 'release'
  AND COALESCE(json_extract(e.payload, '$.steam_user_id'), '') != ''
  AND device_id IN (SELECT device_id FROM devices WHERE distribution = 'steam');
```

解读：这部分用户**当前标签正确**，但历史 cohort 分析（比如 "5-13 当天新增的 release 用户" 这种 join `first_seen` 切片）仍会被污染 —— 因为 `first_seen` 的当天他们被错标过。统计回溯时需注意。

> ⚠️ `events` 表 180 天清理，目前 5-13 起距今 7 天，留底完整。后续做归档时建议把这批 race-tainted 记录单独导出留作历史 reference。

### Cohort A：Steam 用户但**两次** GetSteamID 都失败

事件：Steamworks SDK 完全没起来（Steam 客户端没开 / 网络异常 / DLL 加载失败 / 用户未登录 Steam 账号）。两个函数都拿不到 Steam ID。

- `_is_steam_sdk_engaged()` 三信号全失败 → distribution=`release`
- `_get_telemetry_steam_user_id()` → `''`
- 结果：`release + ''`

这与「真正下载 release 渠道独立发行版」的用户**在数据上完全无法区分**。Q1 抓不到，**任何 SQL 都抓不到**。

兜底估计法：

```sql
-- 假设 release 真实人群中"无 Steam ID"也应近似为 0%（独立发行版本不带 Steamworks），
-- 那么 release 总数减掉 Q1 抓到的矛盾态，剩下的"纯 release"里，有多少可能是 cohort A，
-- 唯一可估法是看其 first_seen 是否在工坊订阅历史或 workshop_config.json 兜底信号被绕过的
-- 时间窗 —— 没有进一步信号可用，只能用占比上界估算。

SELECT
  COUNT(*)                                                         AS pure_release,
  (SELECT COUNT(*) FROM devices
   WHERE distribution = 'release' AND steam_user_id != '')         AS contradicted_release,
  -- 上界估算：假设 cohort A 与 contradicted 同源于同一 race 概率，
  -- 二者比例约等于「无 Steam 客户端开着 / 有 Steam 客户端开着」的用户比
  -- 这个比无独立数据源，只能由运维结合渠道发行量估算
  '⚠ cohort A 无独立信号识别，需结合渠道发行量估算上界' AS note
FROM devices
WHERE distribution = 'release' AND steam_user_id = '';
```

解读：**这部分无法精确，只能给上界**。如果运维知道 release 渠道实际分发量，可用「server 上 release 设备数 - 实际分发量」估出多余的、本应是 Steam 的部分。

### Cohort C：反向 race（distribution=`steam` + 空 `steam_user_id`）

事件：`_is_steam_sdk_engaged()` 第二或第三信号触发（workshop 订阅 > 0 或 workshop_config.json 存在）→ distribution=`steam`，但 `_get_telemetry_steam_user_id()` 调用 GetSteamID 拿到 0 → `steam_user_id=''`。

这其实**有 legitimate use case**：用户之前跑过 Steam 版（workshop_config.json 留底）但本次会话 Steam 客户端没开。**不算 bug**，但和 race 触发的 cohort C 在数据上无法区分。

```sql
SELECT
  COUNT(*) AS steam_without_id,
  (SELECT COUNT(*) FROM devices WHERE distribution = 'steam') AS all_steam,
  ROUND(100.0 * COUNT(*) / NULLIF((SELECT COUNT(*) FROM devices WHERE distribution = 'steam'), 0), 2) AS pct
FROM devices
WHERE distribution = 'steam' AND steam_user_id = '';
```

解读：作为参考量。如果占比 > 10% 偏高，值得追一下 Workshop API 异常或文件兜底误触发；若 < 5% 可视为 by-design 的合理尾巴。

## 5. 修复后会发生什么

**客户端修复（任务二，待批准后开工）**：合并两个函数为单一调用，确保 `distribution` 和 `steam_user_id` 同源同观测点。修复后：

- **新装用户**：再不会进入 race。`release` 始终 `''`，`steam` 始终带 ID。
- **Cohort B**：他们本来就在覆写自愈，修复后不会再被错标，下次上报继续覆写到 `steam`。
- **Cohort A**：客户端代码逻辑不变其结果（SDK 真挂了仍报 `release + ''`）—— 这是真的 release 还是真的 Steam 但 SDK 挂了，本来就**信息不足**，客户端无法区分。
- **Cohort C**：客户端修复改成 anchor on first signal，若 `Users.GetSteamID()` 为 0 但 workshop signal 真，仍是 `steam + ''` —— 是 legitimate signed state，不需改。

**Server 端数据修复**：明确**不在本 PR scope 内**。运维如要追溯历史矛盾态记录，建议：

1. 用 Q1 锁定当前矛盾态 device_id 列表，UPDATE devices SET distribution='steam' WHERE 上述 ID
2. Cohort B 已经自愈，不动
3. Cohort A 信息不足，认了

## 6. 期望运维回报的字段

跑完上面 Q1–Q6 后，麻烦把结果贴回本 PR。重点关注：

- Q1 矛盾态总数（绝对量 + 占 release 比例）
- Q3 event_count 桶分布（验证 single-shot 假说）
- Q5 按日分布（发现 spike → 客户端版本 / Steamworks 库升级回归）
- Q6 与 event_count=1 重合率

如果矛盾态 < 100 个、且全是 event_count=1，业务影响低，按计划推客户端修复 + 可选 server 一次性 UPDATE 即可。如果矛盾态 ≥ 1000 个或 event_count>1 桶非零，需要重新审视 Steamworks init 时序是否还有别的回归。

## 7. 不在本 PR scope

- ❌ server 端 storage / dashboard 改动
- ❌ device_id legacy fold（另事另办）
- ❌ 时区错位修复（另事另办）
- ❌ 其它 telemetry 代码顺手清理

---

**Next step**：等运维跑完 Q1–Q6 + 在本 PR 留 comment 反馈数据 → 据此决定客户端修复优先级 → 推任务二的 commit 到本 branch。
