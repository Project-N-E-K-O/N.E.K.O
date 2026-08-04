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

```bash
docker compose down
mkdir -p neko-home/.local/share
mv N.E.K.O neko-home/.local/share/N.E.K.O
mv ssl     neko-home/ssl
docker compose up -d
```

If the host `N.E.K.O/` directory is empty, you followed the old README quickstart, whose mount target (`/root/Documents/N.E.K.O`) never matched where the services actually write. That data only exists inside the container, so export it before removing the old container: `docker cp neko:/home/neko/.local/share/N.E.K.O ./neko-home/.local/share/N.E.K.O`.

`./logs` is unaffected. Ownership is corrected automatically on the next start.
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
