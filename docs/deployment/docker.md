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
# Decide from what the container actually mounts, not from what the host directory
# contains: the old README mounted ./N.E.K.O at /root/Documents/N.E.K.O, a path the
# services never write to, so that host directory can hold files you put there while
# the real data still lives only in the container's writable layer.
MOUNTS=$(docker inspect neko --format '{{range .Mounts}}{{println .Destination}}{{end}}' 2>/dev/null)

# Needed in step 4: data recovered from the container must not be overwritten by the host copy.
EXPORTED_APP_DATA=""

if [ -z "$MOUNTS" ]; then
  echo "Container neko is gone (already removed?) - skipping export."
elif printf '%s\n' "$MOUNTS" | grep -qx /home/neko; then
  echo "Container already uses the new layout — nothing to export."
else
  # Application data first; this is the part that cannot be recovered later. If the
  # container does not mount the data directory, that data exists nowhere else.
  if ! printf '%s\n' "$MOUNTS" | grep -qx /home/neko/.local/share/N.E.K.O; then
    docker cp neko:/home/neko/.local/share/N.E.K.O/. ./neko-home/.local/share/N.E.K.O/
    EXPORTED_APP_DATA=1
  fi
  # OpenFang state second, and non-fatal: a container that never initialised it has
  # no such directory, and `docker cp` fails on a missing SRC_PATH — that must not
  # abort the run after the application data above already succeeded.
  docker cp neko:/home/neko/.openfang/. ./neko-home/.openfang/ \
    || echo "(no .openfang in the container, or its export failed — application data above is unaffected)"
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
# Only merge the host copy when step 1 did NOT recover the data from the container:
# under the old README layout that directory was mounted at a path the services never
# wrote to, so anything in it would overwrite the only correct copy.
if [ -n "$EXPORTED_APP_DATA" ]; then
  echo "Application data came from the container in step 1 - not merging the host N.E.K.O/"
elif [ -d N.E.K.O ]; then
  cp -a N.E.K.O/. neko-home/.local/share/N.E.K.O/ && rm -rf N.E.K.O
fi
[ -d ssl ] && cp -a ssl/. neko-home/ssl/ && rm -rf ssl

# 3. Start again
docker compose up -d
```

`./logs` is unaffected. The application user inside the container is pinned to uid/gid **1000** — the first regular user on most Linux distributions — so `neko-home/` is owned by you on the host and needs no `sudo` to back up or edit.
:::

## Build locally

The Compose service declares `image:`, not `build:`. Build from the repository root explicitly:

```bash
uv run python scripts/prepare_speaker_model.py
docker build -f docker/Dockerfile -t neko-local:standard .
docker build -f docker/Dockerfile.full -t neko-local:full .
```

The preparation step downloads and verifies the pinned CAM++ weight on the
native host. Both Dockerfiles intentionally re-verify it with `--offline`; this
keeps emulated multi-architecture builds and image layers free of model network
fallbacks. Re-run the preparation step after the speaker-model manifest changes.

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
