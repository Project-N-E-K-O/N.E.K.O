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

EXPOSE 8100

# 环境变量（部署时覆盖）：
#   SURVEY_HMAC_SECRET   - HMAC 签名密钥（与客户端 utils/survey_client.py 一致）
#   SURVEY_ADMIN_TOKEN   - 管理端 API 鉴权 token
#   SURVEY_DB_PATH       - SQLite 数据库路径
#   SURVEY_ENABLE_DOCS   - 设为 "1" 启用 /docs 页面

ENV SURVEY_DB_PATH=/app/data/survey.db

USER appuser

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
