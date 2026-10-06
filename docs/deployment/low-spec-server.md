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

On an existing deployment, changing `SSL_DOMAIN` does not reissue the certificate: the entrypoint keeps reusing the self-signed pair in `docker/neko-home/ssl/`. To get a new one, stop the container, move `N.E.K.O.crt` and `N.E.K.O.key` out of that directory as a backup, and start again; with your own certificate, replace those two files.

Do not make `docker/neko-home` or `docker/logs` a symlink to a shared directory: Docker mounts the link target and the entrypoint then changes the mount root's owner to uid 1000. To keep data on another disk, point it at a dedicated empty directory or write the real path in an override file.

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
- Application file logs live in `docker/neko-home/.local/share/N.E.K.O/logs/` (`docker/logs/` is only a fallback). They are not covered by the Docker cap, but the application rotates them itself (10 MB per file, 5 backups, files older than 30 days removed). Look there first when diagnosing.
- The entrypoint aligns the logs mount to uid 1000 only while it is empty. If Docker created it as root earlier and files have been written there since, DEBUG and fallback logs may fail to write. First read the container's actual mount source and check its path and owner:

  ```bash
  LOGS_SRC=$(docker inspect neko --format '{{range .Mounts}}{{if eq .Destination "/app/logs"}}{{.Source}}{{end}}{{end}}')
  echo "$LOGS_SRC"; stat -c '%u:%g %F' "$LOGS_SRC"
  ```

  Only once you have confirmed it is a directory dedicated to this deployment (normally the absolute path of `docker/logs`, not something other services use such as `/var/log`), fix only the directory itself (not recursively, and skipped if it is itself a mount point):

  ```bash
  [ -n "$LOGS_SRC" ] && [ -d "$LOGS_SRC" ] && ! mountpoint -q "$LOGS_SRC" && sudo chown --no-dereference 1000:1000 "$LOGS_SRC" && echo logs-dir-ok
  ```

  If it is a shared directory, leave its owner alone and mount a dedicated empty directory at `/app/logs` in `compose.local.yaml` instead. Fix old root-owned files inside it one by one as described in section 9, step 4.
- To apply the same cap to other containers, merge `"log-driver": "json-file"` and `"log-opts": {"max-size": "10m", "max-file": "3"}` into `/etc/docker/daemon.json`, then restart Docker. This only applies to containers created afterwards; existing containers keep their old logging options, so recreate them (for example `docker compose up -d --force-recreate` in each project) and confirm:

  ```bash
  docker inspect --format '{{.HostConfig.LogConfig}}' <container>
  ```
- After upgrades, check usage with `docker system df` and remove dangling images with `docker image prune`.

## 5. Optional: host self-healing watchdog

Docker's `unless-stopped` restarts a container only when its process exits; a process that is alive but hung (more likely under memory pressure) is not handled. `docker/watchdog/` provides an optional host watchdog for that case. It installs a root cron job, so only use it on Linux hosts where you trust these scripts.

**Prerequisites**: `bash`, `curl`, `timeout`, `flock`, a running `cron` service, Docker Engine from the official apt repository (snap Docker is not supported because cron's PATH excludes `/snap/bin`), and a pullable `alpine:3.20` for the installer.

**Behavior**: cron runs `/opt/neko/watchdog.sh` every 5 minutes. It only acts on the container named `neko` with label `org.neko.watchdog=enabled` and Compose service `neko-main`; stopped, paused, restarting, or removed containers are never started. Health requires a 200/401 response from `http://127.0.0.1:48911/` on the host and a successful in-container `/health` request to the main server. After a 15-minute startup grace period, two consecutive failures trigger `docker restart`. Each container gets at most three consecutive automatic restarts; after that it logs an error and waits for an operator. State, lock, and log (`/opt/neko/watchdog.log`, not rotated) live in root-private `/opt/neko/`. Loopback binding from section 2 is fine. Changing the host port or publishing HTTPS only breaks the probe and leads to three needless restarts of a healthy container: pause the watchdog, edit the probe address in the source file `docker/watchdog/watchdog.sh`, reinstall, confirm the probe succeeds, then resume. Editing only the installed `/opt/neko/watchdog.sh` is lost on the next reinstall; keep the source change as a local patch and recheck it after each `git pull`.

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

**Verify**: the script exits silently while paused, during the grace period, or when the target does not match, so exit code 0 or a clean log alone does not prove the watchdog protects the container. After installing, check each item:

```bash
ls -l /etc/cron.d/neko-watchdog          # owned by root, mode 644
test ! -e /opt/neko/disabled && echo not-paused
docker inspect neko --format '{{index .Config.Labels "org.neko.watchdog"}} {{index .Config.Labels "com.docker.compose.service"}} {{.State.Running}}'
# expect: enabled neko-main true
curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' --max-time 10 http://127.0.0.1:48911/
# expect 200 or 401
docker exec neko sh -c 'curl --noproxy "*" -fsS --max-time 10 "http://127.0.0.1:${NEKO_MAIN_SERVER_PORT:-48911}/health" > /dev/null' && echo health-ok
```

`docker compose down` does not remove the cron job, and reinstalling does not clear `disabled`. From the repository root, `sudo bash docker/watchdog/test-watchdog.sh` (or `sudo bash watchdog/test-watchdog.sh` from `docker/`) runs the real scripts against mocked docker/curl in a temporary directory; it does not replace on-host acceptance.

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

If you deployed with the former `docker/community-2c2g/` files, the Compose file is gone after updating but the data directories remain (git-ignored). Unless a step says otherwise, run the commands from the **repository root** (`cd` into the N.E.K.O directory that contains `docker/`):

1. Pause the watchdog if installed: `sudo flock /opt/neko/watchdog.lock touch /opt/neko/disabled`.
2. **Before removing the container**, read the actual data mount sources from it. Run steps 2–4 in the same bash session; later commands use these variables:

   ```bash
   HOME_SRC=$(docker inspect neko --format '{{range .Mounts}}{{if eq .Destination "/home/neko"}}{{.Source}}{{end}}{{end}}')
   LOGS_SRC=$(docker inspect neko --format '{{range .Mounts}}{{if eq .Destination "/app/logs"}}{{.Source}}{{end}}{{end}}')
   CFG_DIR=$(realpath -m docker/community-2c2g)    # where .env and the gateway override live (resolves even if absent)
   echo "home=$HOME_SRC logs=$LOGS_SRC cfg=$CFG_DIR"
   ```

   The sources are normally `docker/community-2c2g/neko-home` and `docker/community-2c2g/logs`; customized mounts show their real paths. If either variable is empty or a path is not what you expect, stop and keep the container.

   The old deployment may also have received configuration from `--env-file`, shell variables, `COMPOSE_FILE`, or `-f` override files. Whatever the source, the values in effect can be read from the container. Before removing it, save them as a root-only snapshot for the comparison in step 6:

   ```bash
   SNAP=$(docker inspect neko --format 'image={{.Config.Image}}' &&
     docker inspect neko --format 'ports={{json .HostConfig.PortBindings}}' &&
     docker inspect neko --format '{{printf "memory=%v\n" .HostConfig.Memory}}{{printf "memory_swap=%v\n" .HostConfig.MemorySwap}}{{printf "nano_cpus=%v\n" .HostConfig.NanoCpus}}{{printf "read_only=%v\n" .HostConfig.ReadonlyRootfs}}{{printf "cap_add=%v\n" .HostConfig.CapAdd}}{{printf "cap_drop=%v\n" .HostConfig.CapDrop}}{{printf "security_opt=%v\n" .HostConfig.SecurityOpt}}{{printf "tmpfs=%v\n" .HostConfig.Tmpfs}}{{printf "extra_hosts=%v\n" .HostConfig.ExtraHosts}}{{printf "restart=%v\n" .HostConfig.RestartPolicy.Name}}' &&
     docker inspect neko --format '{{range .Config.Env}}{{println .}}{{end}}') &&
   printf '%s\n' "$SNAP" | sudo sh -c 'umask 077; cat > /root/neko-2c2g-effective.txt' && echo snapshot-ok
   ```

   If any `docker inspect` call fails, no snapshot is written; only `snapshot-ok` means success (same in step 6). Besides the environment, the snapshot records memory/CPU limits, read-only root filesystem, capabilities, security options, tmpfs, extra_hosts, and the restart policy. It contains secrets such as the instance key; never paste it into issues, chats, or logs.
3. Stop the container and, as root with ownership preserved, back up those actual sources plus `.env` and the gateway override if present; then verify the archive:

   ```bash
   docker stop neko && [ "$(docker inspect -f '{{.State.Running}}' neko)" = false ] && echo stopped-ok
   BACKUP=("${HOME_SRC#/}" "${LOGS_SRC#/}")
   for f in .env compose.gateway.yaml; do [ -f "$CFG_DIR/$f" ] && BACKUP+=("${CFG_DIR#/}/$f"); done
   # Write outside the repository, root-only (umask 077), with absolute paths so rollback restores in place
   sudo sh -c 'umask 077; tar -czpf "$0" -C / "$@"' /root/neko-2c2g-backup.tar.gz "${BACKUP[@]}" && echo tar-ok
   # Read the whole archive first: tar fails on truncation or corruption and archive-ok is not printed
   sudo tar -tzf /root/neko-2c2g-backup.tar.gz > /dev/null && echo archive-ok
   # Then confirm every actual source is in the archive
   LIST=$(sudo tar -tzf /root/neko-2c2g-backup.tar.gz)
   for p in "${BACKUP[@]}"; do printf '%s\n' "$LIST" | grep -qxF -e "$p" -e "$p/" && echo "ok /$p" || echo "MISSING /$p"; done
   ```

   You must see `stopped-ok`, `tar-ok`, then `archive-ok`, and only `ok` lines, no `MISSING`. The backup contains instance credentials and TLS keys; never copy it into the repository or anywhere public. Only after confirming it is complete, remove the container: `docker rm neko`.
4. Make sure `docker/neko-home` and `docker/logs` do not exist yet, then copy from the actual sources as root, preserving ownership: `sudo cp -a "$HOME_SRC" docker/neko-home && sudo cp -a "$LOGS_SRC" docker/logs`.

   No manual `chown -R` is needed afterwards: on every start the entrypoint, running as root, aligns the top of `neko-home` and everything under `.local/share/N.E.K.O` (memory, characters, config) to uid/gid 1000, and aligns the `logs` mount point to 1000 only while it is empty (so a `./logs` symlink pointing elsewhere cannot change another host directory's owner); it never recurses into it. A `logs` copied with `cp -a` keeps its original owner, usually already 1000; fix any old root-owned log files one by one if needed, e.g. `sudo chown --no-dereference 1000:1000 -- docker/logs/some.log`; avoid `chown -R` and wildcards so other mounted host paths are not touched. If the `docker/logs` directory itself is not owned by 1000, fix only the directory with the commands in section 4.
5. Carry over **all** effective configuration, not only `docker/community-2c2g/.env` but also any `--env-file`, shell variables, `COMPOSE_FILE`, and `-f` override files used to start it. Put the values in `docker/.env`; a gateway override becomes `docker/compose.gateway.yaml` with `COMPOSE_FILE=docker-compose.yml:compose.gateway.yaml`. Plain `docker compose` commands without `-f` should then produce the complete configuration, without relying on ad-hoc shell variables.

   If the old override also set non-environment options such as `mem_limit`, `read_only`, `cap_drop`, `tmpfs`, `extra_hosts`, `devices`, or `ulimits`, carry them into `docker/compose.local.yaml` under `neko-main` as well. Options the snapshot does not record (such as `devices` and `ulimits`) must be checked by hand against the old override file.

   Note that the official Compose file passes through only a fixed list of variables (see `docker/CONFIG_REFERENCE.md`); others such as `DISABLE_SSL` do not reach the container from `.env`. If the snapshot has such variables, put them in `docker/compose.local.yaml` (ignored by git) and add it to `COMPOSE_FILE`:

   ```yaml
   services:
     neko-main:
       environment:
         - DISABLE_SSL=${DISABLE_SSL:-}
   ```

   ```dotenv
   COMPOSE_FILE=docker-compose.yml:compose.local.yaml   # with a gateway: docker-compose.yml:compose.gateway.yaml:compose.local.yaml
   ```

   Before starting, list the variable names that will reach the container from `docker/` (names only, no values) and confirm every application variable from the snapshot is present:

   ```bash
   docker compose config --format json | python3 -c 'import json,sys; print("\n".join(sorted(json.load(sys.stdin)["services"]["neko-main"]["environment"])))'
   ```
6. From `docker/`, check mounts and ports with `docker compose config`, then `docker compose up -d` and confirm credentials, characters, and memories are intact. Back at the repository root, compare the new container's effective configuration with the step 2 snapshot (the previous new-container snapshot is removed first, so a failed collection makes the comparison print `COMPARE FAILED` instead of `identical` from a stale file):

   ```bash
   sudo rm -f /root/neko-official-effective.txt
   SNAP=$(docker inspect neko --format 'image={{.Config.Image}}' &&
     docker inspect neko --format 'ports={{json .HostConfig.PortBindings}}' &&
     docker inspect neko --format '{{printf "memory=%v\n" .HostConfig.Memory}}{{printf "memory_swap=%v\n" .HostConfig.MemorySwap}}{{printf "nano_cpus=%v\n" .HostConfig.NanoCpus}}{{printf "read_only=%v\n" .HostConfig.ReadonlyRootfs}}{{printf "cap_add=%v\n" .HostConfig.CapAdd}}{{printf "cap_drop=%v\n" .HostConfig.CapDrop}}{{printf "security_opt=%v\n" .HostConfig.SecurityOpt}}{{printf "tmpfs=%v\n" .HostConfig.Tmpfs}}{{printf "extra_hosts=%v\n" .HostConfig.ExtraHosts}}{{printf "restart=%v\n" .HostConfig.RestartPolicy.Name}}' &&
     docker inspect neko --format '{{range .Config.Env}}{{println .}}{{end}}') &&
   printf '%s\n' "$SNAP" | sudo sh -c 'umask 077; cat > /root/neko-official-effective.txt' && echo snapshot-ok
   # Lists only the names that differ (< old container, > new container), never the values
   sudo bash -c '[ -s "$0" ] && [ -s "$1" ] || { echo "COMPARE FAILED: snapshot missing or empty"; exit 2; }
     out=$(diff <(sort "$0") <(sort "$1")); rc=$?
     [ "$rc" -le 1 ] || { echo "COMPARE FAILED"; exit 2; }
     [ "$rc" -eq 0 ] && { echo identical; exit 0; }
     printf "%s\n" "$out" | sed -n "s/^\([<>]\) \([^=]*\)=.*/\1 \2/p"; exit 1' \
     /root/neko-2c2g-effective.txt /root/neko-official-effective.txt
   ```

   Only `identical` means the configurations match; `COMPARE FAILED` means the comparison did not run and must not be treated as a pass. Make sure `NEKO_REQUIRE_HTTPS`, `NEKO_INSTANCE_ACCESS_KEY`, `NEKO_INSTANCE_PUBLIC_ORIGIN`, `NEKO_TRUSTED_HOSTS`, `NEKO_TRUSTED_ORIGINS`, `SSL_DOMAIN`, the image, and the ports were not lost or changed. To see a specific value, check it alone with `sudo grep '^NAME=' file` instead of printing the whole file. On an unexpected difference, run `(cd docker && docker compose down)`, fix `docker/.env` or the override, and start again. Delete both snapshot files once the migration is confirmed.

    To roll back to the old deployment if migration fails, run from the repository root:

   ```bash
   (cd docker && docker compose down)       # stop and remove the new container; no -v
   sudo tar -xzpf /root/neko-2c2g-backup.tar.gz -C /   # restores data and config to their original paths
   # The old Compose file was removed from the repository; restore it from the #3295 merge commit
   mkdir -p docker/community-2c2g   # may no longer exist if data and overrides lived outside the repository
   git show 5161fba:docker/community-2c2g/docker-compose.yaml > docker/community-2c2g/docker-compose.yaml
   # Check port bindings and mount sources before starting: with an external gateway both ports
   # must be 127.0.0.1, and sources must match what docker inspect showed in step 2
   (cd docker/community-2c2g && docker compose config | grep -E 'host_ip|published|source:')
   (cd docker/community-2c2g && docker compose up -d)
   ```

   A `COMPOSE_FILE` entry in the restored `.env` loads the gateway override automatically. If the old deployment used `-f` overrides, an `--env-file`, or shell variables, pass them the same way to both `config` and `up`; do not start it if the bindings or mount sources are wrong. After it starts, create `/root/neko-rollback-effective.txt` with the same snapshot commands as step 6 (including the leading `rm -f`, with the file name changed) and compare it against `/root/neko-2c2g-effective.txt` with the comparison script; only `identical` means the old configuration was fully restored.

   The retrieved old Compose file extends the current official one, so the rolled-back container carries both the old and the new label and is recognized by either the old watchdog or one reinstalled in step 7. Once the old service is healthy, lift the step 1 pause and clear the pre-pause failure count: `sudo flock /opt/neko/watchdog.lock rm -f /opt/neko/fail-count /opt/neko/disabled`.

   In a shallow clone that lacks the commit, run `git fetch --unshallow` first. The restored `docker-compose.yaml` is git-ignored and for local use only; delete it once a later migration succeeds.
7. **Reinstall the watchdog** (section 5): the old script only recognizes the old label. Resume it once the service is healthy.
