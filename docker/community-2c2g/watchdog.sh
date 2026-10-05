#!/bin/bash
# Host-only recovery. No instance key or account credential is needed for probes.
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
STATE_DIR=/opt/neko
CONTAINER=neko
[[ ! -L "$STATE_DIR" && -d "$STATE_DIR" ]] || exit 1
[[ $(stat -c '%u:%g:%a' "$STATE_DIR") == 0:0:700 ]] || exit 1
[[ ! -e "$STATE_DIR/disabled" ]] || exit 0
for dependency in docker curl timeout flock; do
    command -v "$dependency" >/dev/null || { echo "Missing $dependency" >&2; exit 1; }
done
for file in watchdog.lock fail-count watchdog.log; do
    [[ ! -L "$STATE_DIR/$file" ]] || { echo "Unsafe state file: $file" >&2; exit 1; }
done
exec 9>"$STATE_DIR/watchdog.lock"
flock -n 9 || exit 0
log() { printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$STATE_DIR/watchdog.log"; }
fail() { log "$*"; exit 1; }
COUNT_FILE="$STATE_DIR/fail-count"

# Do not transfer recovery authority to an unrelated container with the same name.
# Docker's unless-stopped policy handles exits; preserve intentional stops/removal.
if ! metadata=$(timeout 10 docker inspect -f '{{.Id}} {{index .Config.Labels "org.neko.community-2c2g.watchdog"}} {{index .Config.Labels "com.docker.compose.service"}} {{.State.Running}}' "$CONTAINER"); then
    fail "Container absent or inspect failed; no restart attempted"
fi
read -r container_id enabled service running <<< "$metadata" || fail "Invalid container metadata"
if [[ "$enabled" != enabled || "$service" != neko-main || "$running" != true ]]; then
    rm -f "$COUNT_FILE"
    exit 0
fi

healthy=false
# /health on nginx is a static 200, so probe the root AND the real main service.
# A complete anonymous 401 is normal under #3289; curl failure must never pass.
if code=$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 http://127.0.0.1:48911/); then
    if [[ "$code" == 200 || "$code" == 401 ]]; then
        if timeout 15 docker exec "$container_id" curl -fsS --connect-timeout 5 --max-time 10 http://127.0.0.1:48911/health >/dev/null; then
            healthy=true
        fi
    fi
fi
if [[ "$healthy" == true ]]; then
    rm -f "$COUNT_FILE"
    exit 0
fi

count=0
if [[ -e "$COUNT_FILE" ]]; then
    previous=$(cat "$COUNT_FILE") || fail "Cannot read failure counter"
    read -r previous_id previous_count <<< "$previous" || fail "Invalid failure counter"
    [[ "$previous_count" =~ ^[0-2]$ ]] || fail "Invalid failure counter"
    [[ "$previous_id" != "$container_id" ]] || count=$previous_count
fi
count=$((count + 1))
(( count <= 2 )) || count=2
temporary=$(mktemp "$STATE_DIR/.fail-count.XXXXXX") || fail "Cannot create failure counter"
trap 'rm -f "$temporary"' EXIT
printf '%s %s\n' "$container_id" "$count" > "$temporary" || fail "Cannot write failure counter"
mv -f "$temporary" "$COUNT_FILE" || fail "Cannot publish failure counter"
log "Health probe failed ($count/2)"
if (( count >= 2 )); then
    # Recheck the same ID immediately before restart, including a maintenance pause.
    [[ ! -e "$STATE_DIR/disabled" ]] || exit 0
    current=$(timeout 10 docker inspect -f '{{.State.Running}}' "$container_id") || fail "Cannot recheck container"
    [[ "$current" == true ]] || exit 0
    if timeout 60 docker restart "$container_id" >> "$STATE_DIR/watchdog.log" 2>&1; then
        rm -f "$COUNT_FILE"
        log "Restart succeeded"
    else
        fail "Restart failed; counter retained"
    fi
fi
