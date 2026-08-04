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

順序が重要です：`docker compose down` は container を**削除**しますが、container 内にしか存在しない state があります。

```bash
# 1. container 内にしかないものを先に export（削除前に必ず実行）。
#    旧レイアウトでは OpenFang の workspace を mount していませんでした。また host 側の
#    N.E.K.O/ が空の場合は旧 README の quickstart のケースで、その mount 先
#    （/root/Documents/N.E.K.O）はサービスの実際の書き込み先と一致していなかったため、
#    アプリケーション data も container 内にあります。
#    末尾の /. は directory の中身をコピーする指定で、N.E.K.O/N.E.K.O のようなネストを防ぎます。
mkdir -p neko-home/.local/share/N.E.K.O neko-home/ssl neko-home/.openfang
# ここで "No such container:path" が出るのは OpenFang を一度も初期化していないだけなので無視して構いません。
# それ以外（daemon 停止・権限・disk full）は無視してはいけないため `|| true` は付けません
docker cp neko:/home/neko/.openfang/. ./neko-home/.openfang/
# data が container 内にあるのは host 側 directory が空の場合だけです。条件を command に落とします：
# 新レイアウトで既に再起動済みの環境では container が neko-home 自体を mount しているため、
# 無条件に実行すると directory を自分自身へコピーすることになります
if [ -z "$(ls -A N.E.K.O 2>/dev/null)" ]; then
  docker cp neko:/home/neko/.local/share/N.E.K.O/. ./neko-home/.local/share/N.E.K.O/
fi
```

**手順 1 が成功したことを確認してから次へ進んでください。** `docker compose down` は container を削除しますが、host 側 `N.E.K.O/` が空の場合その container が data の唯一の複製です。権限・disk full・daemon 停止などで export が失敗した場合は、ここで止めて先にそちらを解決してください。

```bash
# 2. container を停止し、host 側の旧 directory を内容単位で merge。新レイアウトで
#    一度でも起動していると宛先 directory は既に存在し（新しい自己署名証明書付き）、
#    mv では一階層深くネストされます。同名 file は旧 data を優先します。
docker compose down
# container が neko-home の所有者を内部の neko（system uid）に変更済みのため、host 側の
# 一般ユーザーでは書き込めません。一度取り戻します（次回起動時に container が戻します）。
sudo chown -R "$(id -u):$(id -g)" neko-home
cp -a N.E.K.O/. neko-home/.local/share/N.E.K.O/ && rm -rf N.E.K.O
cp -a ssl/.     neko-home/ssl/                 && rm -rf ssl

# 3. 再起動
docker compose up -d
```

`./logs` は影響を受けません。所有者は次回起動時に自動修正されます。
:::

Compose には `build:` がありません。Repository root で明示します。

```bash
docker build -f docker/Dockerfile -t neko-local:standard .
docker build -f docker/Dockerfile.full -t neko-local:full .
```

Generated certificate は self-signed で public-trust TLS ではありません。診断は `docker compose ps`、`docker logs neko`、`curl -f http://127.0.0.1:48911/health`。
