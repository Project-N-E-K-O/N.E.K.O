# Docker デプロイ

保守対象 Compose は `docker/docker-compose.yml`。Nginx を前段にして host 48911=HTTP、48912=HTTPS です。

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O/docker
cp env.template .env
# current code が読む値だけ残す
docker compose up -d
```

`http://127.0.0.1:48911` を開きます。再現性には `NEKO_IMAGE` / `NEKO_IMAGE_VERSION` を pin。`latest` は standard、`latest-full` は full alias です。

Entrypoint は `/app/config/core_config.json` がない時、または `NEKO_FORCE_ENV_UPDATE` 指定時だけ初期 config を生成します。API env は live universal override ではありません。

Persistent mounts は `./N.E.K.O` → `/home/neko/.local/share/N.E.K.O`、`./logs` → `/app/logs`、`./ssl` → `/home/neko/ssl`。更新前に backup し、data/private key を公開しません。

Compose には `build:` がありません。Repository root で明示します。

```bash
docker build -f docker/Dockerfile -t neko-local:standard .
docker build -f docker/Dockerfile.full -t neko-local:full .
```

Generated certificate は self-signed で public-trust TLS ではありません。診断は `docker compose ps`、`docker logs neko`、`curl -f http://127.0.0.1:48911/health`。
