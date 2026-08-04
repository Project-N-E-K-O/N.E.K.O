# Docker Deployment

The maintained Compose file is `docker/docker-compose.yml`. It runs N.E.K.O. behind Nginx and publishes HTTP on host port 48911 and HTTPS on 48912.

## Start a published image

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O/docker
cp env.template .env
# Review .env and keep only values supported by current code.
docker compose up -d
```

Open `http://127.0.0.1:48911`. The checked-out Compose file defines the registry/proxy default. Pin `NEKO_IMAGE` or `NEKO_IMAGE_VERSION` for reproducibility. `latest` is the standard-image alias; `latest-full` is the full-image alias.

::: warning Initial configuration
The entrypoint generates `/app/config/core_config.json` only when absent or when `NEKO_FORCE_ENV_UPDATE` is set. API environment variables are initialization inputs, not a live universal override. Confirm effective values in the Web UI.
:::

## Persistent mounts

| Host path | Container path | Purpose |
| --- | --- | --- |
| `./neko-home` | `/home/neko` | User configuration, characters, memories, feature data, TLS certificate and private key, OpenFang runtime state |
| `./logs` | `/app/logs` | Logs |

Back up the first mount before upgrades. Never expose the data or private-key directories through a web server.

::: danger Upgrading from the two-mount layout
Earlier versions mounted `./N.E.K.O` and `./ssl` separately. Pulling a new image without migrating leaves the container with an **empty** data directory: it starts normally and API keys are regenerated from the environment, so nothing looks wrong, but characters, memories and plugins are all missing. The old data is not deleted — it is simply no longer mounted.

Order matters: `docker compose down` **removes** the container, and some state exists nowhere else.

```bash
# 1. Export what lives only inside the container — before it is removed.
#    OpenFang's workspace was never mounted under the old layout. And if the host
#    N.E.K.O/ directory is empty, you followed the old README quickstart, whose
#    mount target (/root/Documents/N.E.K.O) never matched where the services
#    actually write — the application data is in there too.
#    The trailing /. copies directory *contents*, avoiding a nested N.E.K.O/N.E.K.O.
mkdir -p neko-home/.local/share/N.E.K.O neko-home/ssl neko-home/.openfang
# "No such container:path" here just means OpenFang was never initialised — ignore it.
# Everything else (daemon down, permissions, disk full) must NOT be ignored, so no `|| true`.
# Nothing to export once the container already mounts neko-home at /home/neko:
# source and destination would be the same bind mount, and the old container that
# held the only copy is long gone anyway.
if docker inspect neko --format '{{range .Mounts}}{{println .Destination}}{{end}}' 2>/dev/null | grep -qx /home/neko; then
  echo "Container already uses the new layout — nothing to export."
else
  docker cp neko:/home/neko/.openfang/. ./neko-home/.openfang/
  # The application data is inside the container only when the host directory is empty.
  if [ -z "$(ls -A N.E.K.O 2>/dev/null)" ]; then
    docker cp neko:/home/neko/.local/share/N.E.K.O/. ./neko-home/.local/share/N.E.K.O/
  fi
fi
```

**Only continue once step 1 succeeded.** `docker compose down` removes the container, which for an empty host `N.E.K.O/` is the only copy of that data — if the export failed on permissions, a full disk or an unreachable daemon, stop here and fix that first.

```bash
# 2. Stop the container, then merge the host-side directories by content. If the
#    new layout has been started once, the destinations already exist (plus a
#    freshly generated self-signed certificate) and `mv` would nest them one level
#    deeper. Same-named files resolve in favour of the old data.
docker compose down
# The container's neko user is pinned to uid/gid 1000, matching the first regular
# user on most distributions, so ownership usually already lines up. Only needed
# when your host account is not 1000.
[ "$(id -u)" = 1000 ] || sudo chown -R "$(id -u):$(id -g)" neko-home
cp -a N.E.K.O/. neko-home/.local/share/N.E.K.O/ && rm -rf N.E.K.O
cp -a ssl/.     neko-home/ssl/                 && rm -rf ssl

# 3. Start again
docker compose up -d
```

`./logs` is unaffected. The application user inside the container is pinned to uid/gid **1000** — the first regular user on most Linux distributions — so `neko-home/` is owned by you on the host and needs no `sudo` to back up or edit.
:::

## Build locally

The Compose service declares `image:`, not `build:`. Build from the repository root explicitly:

```bash
docker build -f docker/Dockerfile -t neko-local:standard .
docker build -f docker/Dockerfile.full -t neko-local:full .
```

Set `NEKO_IMAGE=neko-local:standard` or `neko-local:full` before `docker compose up`. `docker compose build` does nothing useful here unless a reviewed `build:` definition is added.

## Proxy and diagnostics

The entrypoint starts the Python services and container-local OpenFang, then configures Nginx and WebSocket routes. Its generated certificate is self-signed, not public-trust TLS. Supply a managed certificate or terminate TLS at a trusted proxy for real remote deployment.

```bash
docker compose ps
docker logs neko
docker exec -it neko bash
curl -f http://127.0.0.1:48911/health
```

See [Environment Variables](/config/environment-vars) for variables verified in current code.
