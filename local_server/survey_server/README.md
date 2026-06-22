# N.E.K.O Survey Server

匿名应用内问卷收集服务。与 `telemetry_server` 同构（HMAC 签名 + 时间戳防重放 +
限流 + batch 幂等去重 + SQLite/WAL），但收的是问卷答案而非 token 统计。

问卷**题目定义**不在这里 —— 它跟着版本进主仓库 `config/surveys/<version>.json`，由主程序后端
`GET /api/survey` 下发；本服务只负责**收答卷**。当前版本若存在对应问卷定义，前端会在
changelog 确认弹窗走完后向老玩家弹出，用户填完点提交或点跳过，两种动作都会上报一条记录
（`action=submit` / `action=skip`），用来算弹出量 / 跳过率 / 完成率漏斗。

## 上报链路

```
前端 (问卷 modal)
  └─ POST /api/survey/submit  →  主程序后端 (system_router)
                                   └─ utils/survey_client  (HMAC 签名)
                                        └─ POST /api/v1/survey  →  本服务
```

HMAC 密钥与端口和 telemetry **故意不同**：两条上报通道互不背书。客户端密钥见
`utils/survey_client.py` 的 `_SURVEY_HMAC_SECRET`，服务端见 `security.py` 的
`DEFAULT_HMAC_SECRET`，发版前两边一并改。

## 部署

```bash
pip install -r requirements.txt
python server.py --port 8100 --admin-token YOUR_TOKEN

# 或 Docker
docker-compose up -d   # 记得改 docker-compose.yml 里的 SURVEY_ADMIN_TOKEN
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

## 数据最小化

只存一个匿名 device id + 各题答案，零对话内容、零 PII。`device_id` / `device_id_legacy`
与 telemetry 同源（`utils/token_tracker` 的匿名设备 ID），便于跨表 JOIN 同一个人；
但本服务不做 canonical 身份聚合（问卷量级小，无此必要）。
