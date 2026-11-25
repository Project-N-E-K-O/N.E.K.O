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

### 从旧版 `./data` 绑定挂载升级到命名卷 `telemetry-data`

新版 `docker-compose.yml` 将持久化目录从宿主机 `./data:/app/data` 切换为 Docker 命名卷 `telemetry-data:/app/data`。**全新部署**无需任何操作，直接 `docker compose up -d` 会自动初始化空卷；**已有宿主机 `./data/telemetry.db` 的旧部署**必须在启动新容器前先把历史库迁入命名卷，否则服务会以空库启动：

```bash
cd local_server/telemetry_server

# 1. 先停止旧服务，保证 SQLite（含 -wal/-shm）处于一致状态
docker compose down

# 2. 若本地尚未构建最新镜像，先构建
docker compose build

# 3. 一次性将宿主机 ./data/telemetry.db* 复制进命名卷 telemetry-data（不覆盖卷内已有库）
docker compose run --rm --no-deps --user root \
  -v "$PWD/data:/legacy-data:ro" \
  --entrypoint sh telemetry -c '
    set -eu
    if [ -e /app/data/telemetry.db ]; then
      echo "Refusing to overwrite existing /app/data/telemetry.db in volume" >&2
      exit 1
    fi
    if [ ! -e /legacy-data/telemetry.db ]; then
      echo "No legacy /legacy-data/telemetry.db found; nothing to migrate."
      exit 0
    fi
    cp -a /legacy-data/telemetry.db /app/data/telemetry.db
    for suffix in -wal -shm; do
      [ -e "/legacy-data/telemetry.db$suffix" ] && cp -a "/legacy-data/telemetry.db$suffix" "/app/data/telemetry.db$suffix"
    done
    chown -R appuser /app/data
  '

# 4. 启动升级后的服务并核对历史数据仍在
docker compose up -d
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

```text
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

```text
客户端每个 server 进程：

record()             即时写入内存，零 I/O
    ↓
save() [每 60s]      本地 JSON 落盘 → 然后调用 _report_to_server()
    ↓
_report_to_server()  检查距上次上报是否 ≥ 60s（1分钟）
    ├── 否 → 累积到 _unsent_daily，跳过
    └── 是 → POST /api/v1/telemetry
              ├── 成功 → 清除 _unsent，更新时间戳
              └── 失败 → 放回 _unsent，下次重试

∴ 每进程最多 1 req / 1min
  3 个 server 进程 = 180 req/h/device 上限（实际由服务端 120 req/h 削顶，超出返回 429 累积到下次）
  20k DAU × 180 × 8h ≈ 28.8M req/day ≈ 333 req/s 日均（活跃窗口峰值 ~1000 req/s）
  SQLite WAL ~500 write/s → 日均够用
```

## 管理端 API

全部需要 Header: `Authorization: Bearer YOUR_ADMIN_TOKEN`（或 `?token=` 查询参数）。**返回 JSON，公开 repo 不内置 HTML 看板**——可视化由内部工具基于这些 JSON / 直查 DB 自建。

```bash
TOKEN="YOUR_ADMIN_TOKEN"

# 全局统计
curl -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/stats?days=30

# 活跃设备
curl -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/devices?days=7

# instrument 埋点：top counters + histogram p50/p95（D2-D7 流失诊断数据）
curl -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/instruments?days=7

# 用户指标（device 口径 + canonical 口径并列）
curl -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/canonical/metrics?days=30

# 手动触发：扫 events 产边 + 重算 canonical（平时每 5 分钟自动跑）
curl -X POST -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/canonical/rebuild

# 删号：Steam64 入 denylist（防复活）+ 脱敏 + 删边 + 重算
# steam_user_id 走查询参数，传完整 17 位 Steam64（下例为占位，请替换为实际 ID）
curl -X POST -H "Authorization: Bearer $TOKEN" "http://服务器:8099/api/v1/admin/canonical/denylist?steam_user_id=76561198000000001"

# 导出 CSV（按日汇总）
curl -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/export/daily.csv?days=90 -o daily.csv

# 导出 CSV（按模型汇总）
curl -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/export/model.csv?days=90 -o model.csv

# 清理旧事件（保留聚合）
curl -X POST -H "Authorization: Bearer $TOKEN" http://服务器:8099/api/v1/admin/prune?max_days=180
```

### 直接查 SQLite

```bash
# Docker Compose 部署使用命名卷 telemetry-data（镜像基于 python:3.11-slim，未安装 sqlite3 CLI，使用内置 sqlite3 模块查询）：
docker compose exec telemetry python -c "import sqlite3; c = sqlite3.connect('/app/data/telemetry.db'); print(c.execute(\"SELECT * FROM daily_aggregates WHERE stat_date = date('now') AND model = '_total'\").fetchall())"
# 直接运行 python server.py 时数据库位于本地 ./data/telemetry.db：
# sqlite3 data/telemetry.db

-- 今日总量
SELECT * FROM daily_aggregates WHERE stat_date = date('now') AND model = '_total';

-- 最热门模型
SELECT model, SUM(total_tokens) as tt FROM daily_aggregates
WHERE model != '_total' GROUP BY model ORDER BY tt DESC LIMIT 10;

-- 活跃设备数
SELECT COUNT(*) FROM devices WHERE last_seen >= datetime('now', '-7 days');
```
