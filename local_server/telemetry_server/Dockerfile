FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# 数据目录（建议挂载 volume）
RUN mkdir -p /app/data

# 非 root 用户运行
RUN adduser --system --no-create-home appuser \
    && chown -R appuser /app/data

EXPOSE 8099

# 环境变量（部署时覆盖）：
#   TELEMETRY_HMAC_SECRET   - HMAC 签名密钥
#   TELEMETRY_ADMIN_TOKEN   - 管理端 API 鉴权 token
#   TELEMETRY_DB_PATH       - SQLite 数据库路径
#   TELEMETRY_ENABLE_DOCS   - 设为 "1" 启用 /docs 页面

ENV TELEMETRY_DB_PATH=/app/data/telemetry.db

USER appuser

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8099", "--workers", "1"]
