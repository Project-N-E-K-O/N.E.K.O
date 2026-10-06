# Low-Spec Cloud Server (2C2G)

This page is for 2-vCPU / 2 GB cloud servers with small disks and metered bandwidth. It adds host-level memory, disk, self-healing, security, and traffic settings on top of the official Docker deployment; no separate Compose file is needed.

> Adapted from the 2C2G guide by community contributor 烨儿不会飞 (GitHub [@csy-11](https://github.com/csy-11)), originally proposed in [#3295](https://github.com/Project-N-E-K-O/N.E.K.O/pull/3295). If it helps you, you can support the author on [afdian](https://afdian.com/a/chensye).

::: warning Scope
Commands assume an Ubuntu-family host and have not been verified on every cloud provider. The official Compose file sets no memory limit, so whether 2 GB is enough depends on your workload; the watchdog is not OOM protection. Validate peak memory, latency, and a rollback path with a representative workload before going live.
:::

## 1. Deploy

Install as described in [Docker Deployment](./docker); the Compose file is `docker/docker-compose.yml`. On small hosts:

- **Use the full image**: set `NEKO_IMAGE_VERSION=latest-full` in `docker/.env`. It ships Chromium, so first start does not download a browser inside the container, at the cost of about 1 GB more disk.
- **Pin a version**: `latest` and `latest-full` are rolling tags. Pin a verified tag or digest with `NEKO_IMAGE`, and confirm it includes instance authorization (#3289) and HTTP pairing (#3299).
- **Own domain**: set these in `docker/.env`; the official Compose file passes them to the container:

```dotenv
SSL_DOMAIN=your-domain.example
NEKO_TRUSTED_HOSTS=your-domain.example
NEKO_TRUSTED_ORIGINS=https://your-domain.example:48912
```

Pairing over `http://<server-ip>:48911` is allowed by default and the page warns that it is unencrypted; the pairing key and session cookie travel in clear text, so do not enter credentials this way on untrusted networks. Set `NEKO_REQUIRE_HTTPS=1` to require HTTPS/WSS.

If you want a container memory limit, set it in an override file following the [Docker resource constraints docs](https://docs.docker.com/engine/containers/resource_constraints/) and size it from measurements.

## 2. External TLS gateway: bind upstream to loopback

The official Compose file publishes 48911/48912 on all interfaces. When a gateway on the same host terminates HTTPS, bind the upstream to loopback with `docker/compose.gateway.yaml` (ignored by git):

```yaml
services:
  neko-main:
    ports: !override
      - "127.0.0.1:48911:80"
      - "127.0.0.1:48912:443"
```

Persist the file set in `docker/.env` so every `docker compose` command without `-f` loads both files:

```dotenv
COMPOSE_FILE=docker-compose.yml:compose.gateway.yaml
```

`!override` requires Docker Compose **2.24.4 or newer**. Check the final port bindings with `docker compose config` before each recreate. Set both `NEKO_INSTANCE_PUBLIC_ORIGIN` and `NEKO_TRUSTED_ORIGINS` to the public origin browsers actually use (for example `https://your-domain.example`). The gateway must keep Host and a correct `X-Forwarded-For` chain, proxy WebSockets, and either close public HTTP or redirect it to HTTPS. See [Community remote access](/design/security/community-remote-access).

## 3. Memory: ZRAM and swap

```bash
sudo apt update
sudo apt install zram-tools
```

In `/etc/default/zramswap` set `ALGO=lz4`, `PERCENT=50`, `PRIORITY=100`, then run `sudo systemctl restart zramswap` and check `swapon --show`. Keep a 2–4 GB disk swapfile as a last resort.

There is no universal `vm.swappiness`. With ZRAM as the primary swap, evaluate values around 100 under real load (the [kernel docs](https://www.kernel.org/doc/html/latest/admin-guide/sysctl/vm.html#swappiness) allow values above 100 for in-memory swap); low values such as 10 only make sense when minimizing disk swap I/O. No value guarantees avoiding OOM.

## 4. Disk: logs and images

- The official Compose file caps the main container's Docker log (`docker logs`) at 10m × 3.
- Application file logs in `docker/logs/` are not covered; rotate them with logrotate or similar.
- To apply the same cap to other containers, merge `"log-driver": "json-file"` and `"log-opts": {"max-size": "10m", "max-file": "3"}` into `/etc/docker/daemon.json`, then restart Docker.
- After upgrades, check usage with `docker system df` and remove dangling images with `docker image prune`.

## 5. Optional: host self-healing watchdog

Docker's `unless-stopped` restarts a container only when its process exits; a process that is alive but hung (more likely under memory pressure) is not handled. `docker/watchdog/` provides an optional host watchdog for that case. It installs a root cron job, so only use it on Linux hosts where you trust these scripts.

**Prerequisites**: `bash`, `curl`, `timeout`, `flock`, a running `cron` service, Docker Engine from the official apt repository (snap Docker is not supported because cron's PATH excludes `/snap/bin`), and a pullable `alpine:3.20` for the installer.

**Behavior**: cron runs `/opt/neko/watchdog.sh` every 5 minutes. It only acts on the container named `neko` with label `org.neko.watchdog=enabled` and Compose service `neko-main`; stopped, paused, restarting, or removed containers are never started. Health requires a 200/401 response from `http://127.0.0.1:48911/` on the host and a successful in-container `/health` request to the main server. After a 15-minute startup grace period, two consecutive failures trigger `docker restart`. Each container gets at most three consecutive automatic restarts; after that it logs an error and waits for an operator. State, lock, and log (`/opt/neko/watchdog.log`, not rotated) live in root-private `/opt/neko/`. Changing the host port or publishing HTTPS only breaks the probe; pause the watchdog and edit the probe address in `watchdog.sh` first. Loopback binding from section 2 is fine.

**Install** (from `docker/`, after reviewing both scripts):

```bash
docker run --rm --network none \
  -v /etc/cron.d:/host-cron.d -v /opt:/host-opt \
  -v "$PWD/watchdog:/source:ro" \
  alpine:3.20 sh /source/install-watchdog.sh
```

For slower starts, add `NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=1800` above the job line in `/etc/cron.d/neko-watchdog` (seconds; 0 disables). Reinstalling preserves it.

**Maintenance and removal**:

```bash
sudo flock /opt/neko/watchdog.lock touch /opt/neko/disabled                      # pause
sudo flock /opt/neko/watchdog.lock rm -f /opt/neko/fail-count /opt/neko/disabled # resume
sudo flock /opt/neko/watchdog.lock rm -f /opt/neko/restart-count                 # reset restart budget
sudo rm -f /etc/cron.d/neko-watchdog                                             # uninstall (after pausing)
sudo flock /opt/neko/watchdog.lock rm -f /opt/neko/watchdog.sh
```

`docker compose down` does not remove the cron job, and reinstalling does not clear `disabled`. `sudo bash docker/watchdog/test-watchdog.sh` runs the real scripts against mocked docker/curl in a temporary directory; it does not replace on-host acceptance.

## 6. Network security

- Restrict sources in the cloud security group and verify from outside. Docker-published ports can bypass ufw and host `INPUT` rules ([Docker firewall docs](https://docs.docker.com/engine/network/firewall-iptables/)).
- Use key-only SSH (`PasswordAuthentication no`).
- CrowdSec can block brute-force sources: `curl -s https://install.crowdsec.net | sudo sh`, then `sudo apt install crowdsec crowdsec-firewall-bouncer-iptables`. With Docker's iptables backend, add `DOCKER-USER` next to `INPUT` in the bouncer's `iptables_chains` ([CrowdSec docs](https://docs.crowdsec.net/docs/bouncers/firewall/)) and verify blocking from an external host. It does not replace instance credentials or HTTPS.

## 7. Traffic and DNS

- Metered-traffic discounts (for example Alibaba Cloud CDT) have account-wide quotas and eligibility rules; compare your monthly egress against fixed-bandwidth pricing before switching.
- If the public IP changes, a dynamic DNS service such as DuckDNS works; set the domain variables from section 1 and a matching certificate.

## 8. Data and backups

Memory data is mostly text, so the local SQLite stores stay small; move long-term raw images and audio to cheaper object storage. Back up `docker/neko-home/` off-host regularly; it contains instance credentials and TLS keys, so keep backups private.

## 9. Migrating from the community-2c2g layout

If you deployed with the former `docker/community-2c2g/` files, the Compose file is gone after updating but the data directories remain (git-ignored):

1. Pause the watchdog if installed: `sudo flock /opt/neko/watchdog.lock touch /opt/neko/disabled`.
2. Stop and remove the old container: `docker stop neko && docker rm neko`.
3. Make sure `docker/neko-home` and `docker/logs` do not exist yet, then copy as root, preserving ownership: `sudo cp -a docker/community-2c2g/neko-home docker/community-2c2g/logs docker/`.
4. Move needed settings from `docker/community-2c2g/.env` to `docker/.env`; a gateway override becomes `docker/compose.gateway.yaml` with `COMPOSE_FILE=docker-compose.yml:compose.gateway.yaml`.
5. From `docker/`, check `docker compose config`, then `docker compose up -d` and confirm credentials, characters, and memories are intact.
6. **Reinstall the watchdog** (section 5): the old script only recognizes the old label. Resume it once the service is healthy.
