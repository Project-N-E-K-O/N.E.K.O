# N.E.K.O Telemetry Server

匿名 LLM Token 用量收集服务。

## 快速部署

```bash
cd local_server/telemetry_server

# Docker（推荐）
# 修改 docker-compose.yml 中的 TELEMETRY_ADMIN_TOKEN
docker-compose up -d

# 或直接运行
pip install -r requirements.txt
python server.py --port 8099 --admin-token YOUR_SECRET_TOKEN
```

部署好后，在 `utils/token_tracker.py` 顶部修改：
```python
_TELEMETRY_SERVER_URL = "http://你的服务器IP:8099"
```

## 安全机制

| 机制 | 实现 | 说明 |
|------|------|------|
| 防篡改 | HMAC-SHA256 | 秘钥硬编码在源码中（与 vLLM 相同策略） |
| 防重放 | ±5min 时间戳窗口 | 拒绝过期请求 |
| 匿名化 | SHA256(machine_id + salt) | 不可逆设备指纹 |
| 数据最小化 | 仅 token 计数 | 零对话内容、零 PII |
| 不可篡改 | Append-only SQLite | 原始事件只追加 |
| 防滥用 | 120 req/h/device | 滑动窗口限流 |
| Opt-out | `DO_NOT_TRACK=1` | 用户可自行关闭 |

## 收集的数据

```
✅ 收集                             ❌ 不收集
────────────────────────            ────────────────────
· prompt_tokens（含 cached）         · 对话内容
· cached_tokens（缓存命中）          · 用户名 / 用户 ID
· completion_tokens（生成）          · IP 地址
· 模型名称                          · API Key
· 调用类型（conversation 等）        · 硬件序列号
· 调用次数 / 错误次数                · 地理位置
· 匿名设备指纹                      · 任何 PII
```

## 节流设计

```
客户端每个 server 进程：

record()             即时写入内存，零 I/O
    ↓
save() [每 60s]      本地 JSON 落盘 → 然后调用 _report_to_server()
    ↓
_report_to_server()  检查距上次上报是否 ≥ 600s（10分钟）
    ├── 否 → 累积到 _unsent_daily，跳过
    └── 是 → POST /api/v1/telemetry
              ├── 成功 → 清除 _unsent，更新时间戳
              └── 失败 → 放回 _unsent，下次重试

∴ 每进程最多 1 req / 10min
  3 个 server 进程 = 18 req/h/device
  20k DAU × 18 × 8h ≈ 2.88M req/day ≈ 33 req/s peak
  SQLite WAL ~500 write/s → 单实例够用
```

## 管理端

### 仪表盘

浏览器访问：`http://服务器:8099/api/v1/admin/dashboard?days=30`

需要 Header: `Authorization: Bearer YOUR_ADMIN_TOKEN`

（提示：可以用浏览器扩展如 ModHeader 添加 Authorization Header）

### API

```bash
TOKEN="YOUR_ADMIN_TOKEN"

# 全局统计
curl -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/stats?days=30

# 活跃设备
curl -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/devices?days=7

# 导出 CSV（按日汇总）
curl -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/export/daily.csv?days=90 -o daily.csv

# 导出 CSV（按模型汇总）
curl -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/export/model.csv?days=90 -o model.csv

# 清理旧事件（保留聚合）
curl -X POST -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/prune?max_days=180
```

### 直接查 SQLite

```bash
sqlite3 data/telemetry.db

-- 今日总量
SELECT * FROM daily_aggregates WHERE stat_date = date('now') AND model = '_total';

-- 最热门模型
SELECT model, SUM(total_tokens) as tt FROM daily_aggregates
WHERE model != '_total' GROUP BY model ORDER BY tt DESC LIMIT 10;

-- 活跃设备数
SELECT COUNT(*) FROM devices WHERE last_seen >= datetime('now', '-7 days');
```
