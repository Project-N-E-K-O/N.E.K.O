# N.E.K.O Survey Server

匿名应用内问卷收集服务。与 `telemetry_server` 同构（HMAC 签名 + 时间戳防重放 +
限流 + batch 幂等去重 + SQLite/WAL），但收的是问卷答案而非 token 统计。

问卷**题目定义**不在这里 —— 它跟着版本进主仓库 `config/surveys/<version>.json`，由主程序后端
`GET /api/survey` 下发；本服务只负责**收答卷**。当前版本若存在对应问卷定义，前端会在
changelog 确认弹窗走完后向老玩家弹出，用户填完点提交或点跳过，两种动作都会上报一条记录
（`action=submit` / `action=skip`），用来算弹出量 / 跳过率 / 完成率漏斗。

## 上报链路

```text
前端 (问卷 modal)
  └─ POST /api/survey/submit  →  主程序后端 (system_router)
                                   └─ utils/survey_client  (HMAC 签名)
                                        └─ POST /api/v1/survey  →  本服务
```

HMAC 密钥与端口和 telemetry **故意不同**：两条上报通道互不背书。客户端密钥见
`utils/survey_client.py` 的 `_SURVEY_HMAC_SECRET`，服务端见 `security.py` 的
`DEFAULT_HMAC_SECRET`，发版前两边一并改。

## 部署

与 telemetry 互不依赖，可同机共存（不同端口 8100）也可单独一台。三种方式任选：

```bash
# 1) 直接跑
pip install -r requirements.txt
python server.py --port 8100 --admin-token YOUR_TOKEN

# 2) Docker（数据存命名卷 survey-data；admin API 默认禁用，
#    需要查询/导出时在 docker-compose.yml 取消注释 SURVEY_ADMIN_TOKEN 并改强随机值）
docker-compose up -d

# 3) systemd 一键（Linux）
cd deploy && ./setup.sh   # 自动建 venv + 装服务 + 生成 admin token
```

环境变量：

| 变量 | 说明 |
| --- | --- |
| `SURVEY_HMAC_SECRET` | HMAC 签名密钥（与客户端一致） |
| `SURVEY_ADMIN_TOKEN` | 管理端 API 鉴权 token；不设则管理端禁用 |
| `SURVEY_DB_PATH` | SQLite 路径（默认 `./data/survey.db`） |
| `SURVEY_ENABLE_DOCS` | 设为 `1` 启用 `/docs` |

## 接口

公开：

- `POST /api/v1/survey` —— 上报答卷（HMAC 验签）
- `GET /health`

管理端（需 admin token，URL `?token=` 或 `Authorization: Bearer`）：

- `GET /api/v1/admin/summary?survey_version=` —— 漏斗：提交/跳过/去重设备数
- `GET /api/v1/admin/responses?survey_version=&limit=` —— 原始答卷 JSON（不含跳过）
- `GET /api/v1/admin/export/responses.csv?survey_version=` —— 导出 CSV
- `POST /api/v1/admin/prune?max_days=365` —— 清理过期答卷

## Steam-only + 字段

问卷只发给 **Steam 用户**：客户端下发口 `GET /api/survey` 以 `distribution=='steam'`
判定——Steam 版正常通过 Steam 启动、客户端在跑，`GetSteamID()` 实时即得；客户端没开时
也有 workshop 订阅 / workshop_config.json 磁盘兜底证明跑过 Steam 版。非 Steam / source
构建一律 `has_survey:false`，不弹也不上报。

上报字段：`device_id` / `device_id_legacy`（与 telemetry 同源同函数，可跨表 JOIN 同一个人）、
`steam_user_id`（实时 Steam64；客户端没开时可为空，与 telemetry 的 "steam + 空 id" 语义一致）、
`app_version` / `survey_version` / `locale` / `branch` / `distribution`、`action`（submit/skip）、`answers`。

## 数据最小化

只存匿名 device id + Steam64 + 各题答案，零对话内容。本服务不做 canonical 身份聚合
（问卷量级小，无此必要）。
