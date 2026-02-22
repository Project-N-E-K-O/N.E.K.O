# Docker デプロイ

## クイックスタート

```bash
# リポジトリをクローン
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O/docker

# 環境を設定
cp env.template .env
# .env を編集して API キーを設定

# 起動
docker-compose up -d
```

`http://localhost:48911` で Web UI にアクセスします。

## docker-compose.yml

```yaml
version: '3.8'

services:
  neko-main:
    image: ghcr.io/project-n-e-k-o/n.e.k.o:latest
    container_name: neko
    restart: unless-stopped
    ports:
      - "48911:80"
    environment:
      - NEKO_CORE_API_KEY=${NEKO_CORE_API_KEY}
      - NEKO_CORE_API=${NEKO_CORE_API:-qwen}
      - NEKO_ASSIST_API=${NEKO_ASSIST_API:-qwen}
      - NEKO_ASSIST_API_KEY_QWEN=${NEKO_ASSIST_API_KEY_QWEN:-}
      - NEKO_ASSIST_API_KEY_OPENAI=${NEKO_ASSIST_API_KEY_OPENAI:-}
      - NEKO_ASSIST_API_KEY_GLM=${NEKO_ASSIST_API_KEY_GLM:-}
      - NEKO_ASSIST_API_KEY_STEP=${NEKO_ASSIST_API_KEY_STEP:-}
      - NEKO_ASSIST_API_KEY_SILICON=${NEKO_ASSIST_API_KEY_SILICON:-}
      - NEKO_MCP_TOKEN=${NEKO_MCP_TOKEN:-}
    volumes:
      - ./N.E.K.O:/root/Documents/N.E.K.O
      - ./logs:/app/logs
    networks:
      - neko-network

networks:
  neko-network:
    driver: bridge
```

## 環境変数

テンプレートから `.env` ファイルを作成します：

```bash
# 必須
NEKO_CORE_API_KEY=sk-your-key-here
NEKO_CORE_API=qwen

# オプション
NEKO_ASSIST_API=qwen
NEKO_ASSIST_API_KEY_QWEN=sk-your-assist-key
```

完全なリファレンスは [環境変数](/ja/config/environment-vars) を参照してください。

## Nginx プロキシ

Docker コンテナにはリバースプロキシとして Nginx が含まれています：

- 内部ポートのメインサーバーへのプロキシ
- リアルタイムチャットのための WebSocket サポート
- 静的ファイルのキャッシュ（30 日間有効期限）
- `/health` でのヘルスチェック

## データ永続化

| マウント | コンテナパス | 用途 |
|-------|----------------|---------|
| `./N.E.K.O` | `/root/Documents/N.E.K.O` | 設定、キャラクター、メモリ |
| `./logs` | `/app/logs` | アプリケーションログ |

## プロバイダークイックスタート

**Qwen（推奨）:**
```bash
NEKO_CORE_API_KEY=sk-xxxxx
NEKO_CORE_API=qwen
```

**Free（キー不要）:**
```bash
NEKO_CORE_API_KEY=free-access
NEKO_CORE_API=free
```

**OpenAI:**
```bash
NEKO_CORE_API_KEY=sk-xxxxx
NEKO_CORE_API=openai
```

## トラブルシューティング

```bash
# ログを表示
docker logs neko

# コンテナに入る
docker exec -it neko bash

# 設定を確認
docker exec neko cat /root/Documents/N.E.K.O/core_config.json

# 環境変数を確認
docker exec neko env | grep NEKO_
```
