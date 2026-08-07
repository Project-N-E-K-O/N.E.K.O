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
# 判断は「host 側 directory の中身」ではなく「container が実際に何を mount しているか」で
# 行います：旧 README は ./N.E.K.O を /root/Documents/N.E.K.O に mount しており、そこは
# サービスが書き込まない path なので、その host directory に自分で置いた file があっても
# 実データは container の writable layer にしか存在しません。
MOUNTS=$(docker inspect neko --format '{{range .Mounts}}{{println .Destination}}{{end}}' 2>/dev/null)

# 手順 4 で使用：container から救出した実データを host 側の旧 directory で上書きさせないため
EXPORTED_APP_DATA=""

if [ -z "$MOUNTS" ]; then
  echo "container neko が存在しません（削除済み？）。export を skip します。"
elif printf '%s\n' "$MOUNTS" | grep -qx /home/neko; then
  echo "container は既に新レイアウトです。export するものはありません。"
else
  # application data を先に。失われると復旧できないのはこちらです。container が data
  # directory を mount していない場合、その data は他のどこにも存在しません。
  if ! printf '%s\n' "$MOUNTS" | grep -qx /home/neko/.local/share/N.E.K.O; then
    docker cp neko:/home/neko/.local/share/N.E.K.O/. ./neko-home/.local/share/N.E.K.O/
    EXPORTED_APP_DATA=1
  fi
  # OpenFang state はその次で、致命的ではありません：一度も初期化していない container
  # には該当 directory がなく、docker cp は存在しない SRC_PATH で失敗します。上の
  # 重要な export が済んだ後にそれで中断させてはいけません。
  docker cp neko:/home/neko/.openfang/. ./neko-home/.openfang/ \
    || echo "（container に .openfang がないか export に失敗しました。上の application data には影響しません）"
fi
```

**手順 1 が成功したことを確認してから次へ進んでください。** `docker compose down` は container を削除しますが、host 側 `N.E.K.O/` が空の場合その container が data の唯一の複製です。権限・disk full・daemon 停止などで export が失敗した場合は、ここで止めて先にそちらを解決してください。

```bash
# 2. container を停止し、host 側の旧 directory を内容単位で merge。新レイアウトで
#    一度でも起動していると宛先 directory は既に存在し（新しい自己署名証明書付き）、
#    mv では一階層深くネストされます。同名 file は旧 data を優先します。
docker compose down
# container の neko は uid/gid 1000 に固定されており、多くの distribution の最初の
# 一般ユーザーと一致するため通常は所有者が既に揃っています。host 側の account が
# 1000 でない場合だけ必要です。
[ "$(id -u)" = 1000 ] || sudo chown -R "$(id -u):$(id -g)" neko-home
# host 側の copy が権威なのは「手順 1 で container から救出しなかった」場合だけです：
# 旧 README はその directory をサービスが書き込まない path に mount していたため、
# 中身をそのまま被せると唯一正しい copy を上書きしてしまいます。
if [ -n "$EXPORTED_APP_DATA" ]; then
  echo "application data は手順 1 で container から export 済み。host 側の N.E.K.O/ は merge しません"
elif [ -d N.E.K.O ]; then
  cp -a N.E.K.O/. neko-home/.local/share/N.E.K.O/ && rm -rf N.E.K.O
fi
[ -d ssl ] && cp -a ssl/. neko-home/ssl/ && rm -rf ssl

# 3. 再起動
docker compose up -d
```

`./logs` は影響を受けません。container 内の application user は uid/gid **1000**（多くの Linux distribution で最初の一般ユーザーの番号）に固定されているため、host 側で `neko-home/` の所有者は自分自身になり、backup や編集に `sudo` は不要です。
:::

Compose には `build:` がありません。Repository root で明示します。

```bash
docker build -f docker/Dockerfile -t neko-local:standard .
docker build -f docker/Dockerfile.full -t neko-local:full .
```

Generated certificate は self-signed で public-trust TLS ではありません。診断は `docker compose ps`、`docker logs neko`、`curl -f http://127.0.0.1:48911/health`。
