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

Persistent mounts は `./neko-home` → `/home/neko`（設定、データ、TLS 証明書と秘密鍵、OpenFang runtime state）、`./logs` → `/app/logs`。更新前に backup し、data/private key を公開しません。

::: danger 旧 2 マウント構成からの移行
旧版は `./N.E.K.O` と `./ssl` を別々に mount していました。移行せずに新しい image を pull すると、container は**空の** data directory で起動します。サービスは正常に立ち上がり API key も環境変数から再生成されるため一見問題なく見えますが、キャラクター・記憶・plugin が全て存在しない状態です。旧 data は削除されておらず、mount されなくなっただけです。

```bash
docker compose down
mkdir -p neko-home/.local/share
mv N.E.K.O neko-home/.local/share/N.E.K.O
mv ssl     neko-home/ssl
docker compose up -d
```

host 側の `N.E.K.O/` が空の場合は旧 README の quickstart に従っていたケースです。その mount 先（`/root/Documents/N.E.K.O`）はサービスの実際の書き込み先と一致していなかったため、data は container 内部にしか存在しません。旧 container を削除する前に export してください：`docker cp neko:/home/neko/.local/share/N.E.K.O ./neko-home/.local/share/N.E.K.O`。

`./logs` は影響を受けません。所有者は次回起動時に自動修正されます。
:::

Compose には `build:` がありません。Repository root で明示します。

```bash
docker build -f docker/Dockerfile -t neko-local:standard .
docker build -f docker/Dockerfile.full -t neko-local:full .
```

Generated certificate は self-signed で public-trust TLS ではありません。診断は `docker compose ps`、`docker logs neko`、`curl -f http://127.0.0.1:48911/health`。
