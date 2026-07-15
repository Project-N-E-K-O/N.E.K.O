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
    # Image version is selectable via env vars (latest = newest release)
    image: ${NEKO_IMAGE:-docker.gh-proxy.org/ghcr.io/project-n-e-k-o/n.e.k.o:${NEKO_IMAGE_VERSION:-latest}}
    container_name: neko
    restart: unless-stopped
    ports:
      - "48911:80"    # HTTP
      - "48912:443"   # HTTPS
    volumes:
      - ./N.E.K.O:/home/neko/.local/share/N.E.K.O
      - ./logs:/app/logs
      - ./ssl:/home/neko/ssl
    networks:
      - neko-network

networks:
  neko-network:
    driver: bridge
```

設定は上で作成した `.env` ファイルから供給されます（`cp env.template .env`）。`entrypoint.sh` が起動時に `NEKO_*` 変数を読み込みます。完全な一覧は [環境変数](#環境変数) を参照してください。

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
| `./N.E.K.O` | `/home/neko/.local/share/N.E.K.O` | 設定、キャラクター、メモリ |
| `./logs` | `/app/logs` | アプリケーションログ |
| `./ssl` | `/home/neko/ssl` | SSL 証明書 |

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
docker exec neko cat /home/neko/.local/share/N.E.K.O/core_config.json

# 環境変数を確認
docker exec neko env | grep NEKO_
```
