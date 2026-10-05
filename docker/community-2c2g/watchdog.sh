#!/bin/bash
# Host-only recovery for the community 2C2G deployment.
# No instance key or account credential is needed for probes.
#
# Behavior notes (keep README §3.2 in sync):
#  * Startup grace: a freshly (re)started container is still booting Python on
#    slow 2C2G hardware, so we never count failures or restart while inside the
#    startup-grace window (default 600s; override via /opt/neko/grace-seconds).
#    This prevents a slow boot from being mis-recovered into a restart loop.
#  * Intentional stop/pause is never "recovered": stopped or paused containers
#    are left alone and their failure history is cleared.
#  * Racing an operator: if StartedAt changed while we were observing, someone
#    (re)started it; we stand down and let the fresh grace window win. A
#    dedicated restart.lock serializes docker restart with any external
#    coordinator (e.g. an operator script taking the same lock).
#  * A docker restart that merely times out (exit 124) is treated as still
#    completing, not as a hard failure, so it does not poison the counter.
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
STATE_DIR=/opt/neko
CONTAINER=neko
INSPECT_FMT='{{.Id}}|{{index .Config.Labels "org.neko.community-2c2g.watchdog"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Running}}|{{.State.Paused}}|{{.State.StartedAt}}'
RECHECK_FMT='{{.State.Running}}|{{.State.Paused}}|{{.State.StartedAt}}'
COUNT_FILE="$STATE_DIR/fail-count"
STARTED_FILE="$STATE_DIR/started-at"
GRACE_SECONDS=600

# State dir must be root-only and never a symlink.
[[ ! -L "$STATE_DIR" && -d "$STATE_DIR" ]] || exit 1
[[ $(stat -c '%u:%g:%a' "$STATE_DIR") == 0:0:700 ]] || exit 1
[[ ! -e "$STATE_DIR/disabled" ]] || exit 0
for dependency in docker curl timeout flock; do
    command -v "$dependency" >/dev/null || { echo "Missing $dependency" >&2; exit 1; }
done
for file in watchdog.lock fail-count started-at watchdog.log grace-seconds restart.lock; do
    [[ ! -L "$STATE_DIR/$file" ]] || { echo "Unsafe state file: $file" >&2; exit 1; }
done

exec 9>"$STATE_DIR/watchdog.lock"
flock -n 9 || exit 0

temporary=""
cleanup() { if [[ -n "$temporary" && -e "$temporary" ]]; then rm -f "$temporary"; fi; }
trap cleanup EXIT HUP INT TERM
log() { printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$STATE_DIR/watchdog.log"; }
fail() { log "$*"; exit 1; }

# Optional grace override (root-only file).
if [[ -e "$STATE_DIR/grace-seconds" ]]; then
    g=$(cat "$STATE_DIR/grace-seconds") || true
    [[ "$g" =~ ^[0-9]+$ ]] && GRACE_SECONDS="$g"
fi

# Metadata: identity + watchdog label + compose service + running + paused + boot time.
if ! metadata=$(timeout 10 docker inspect -f "$INSPECT_FMT" "$CONTAINER"); then
    fail "Container absent or inspect failed; no restart attempted"
fi
IFS='|' read -r container_id enabled service running paused started_at <<< "$metadata" || fail "Invalid container metadata"

# Not our deployment or intentionally stopped: leave alone, forget failures.
if [[ "$enabled" != enabled || "$service" != neko-main || "$running" != true ]]; then
    rm -f "$COUNT_FILE"
    exit 0
fi

# Intentionally paused (operator): do not "recover", forget failures.
if [[ "$paused" == true ]]; then
    rm -f "$COUNT_FILE"
    exit 0
fi

started_epoch=$(date -d "$started_at" +%s 2>/dev/null || echo 0)
now_epoch=$(date +%s)
[[ "$started_epoch" -gt 0 ]] || fail "Container running but has no valid start time"

# Detect a (re)start: StartedAt changed since the last check means a fresh boot,
# so clear any failure history to never mis-recover a slow startup. This also
# resets the line after every restart (docker restart keeps the same container id).
last_start=""
[[ -e "$STARTED_FILE" ]] && last_start=$(cat "$STARTED_FILE") || true
if [[ "$last_start" != "$started_at" ]]; then
    started_tmp=$(mktemp "$STATE_DIR/.started-at.XXXXXX") || fail "Cannot create started-at temp"
    temporary="$started_tmp"
    printf '%s\n' "$started_at" > "$started_tmp" || fail "Cannot write started-at"
    mv -f "$started_tmp" "$STARTED_FILE" || fail "Cannot publish started-at"
    rm -f "$COUNT_FILE"
fi

# Startup grace: within GRACE_SECONDS of boot the backend may not be up yet.
if (( started_epoch + GRACE_SECONDS > now_epoch )); then
    log "Container (re)started, still within ${GRACE_SECONDS}s startup grace; skipping health check"
    exit 0
fi

# Health: host probes the private HTTP root AND the real main service inside the
# container. A complete anonymous 401 is normal under #3289; a curl failure must
# never pass. (Neither raw /dev/tcp connectivity nor nginx's static /health is
# sufficient on its own to prove the Python backend is alive.)
healthy=false
if code=$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 http://127.0.0.1:48911/); then
    if [[ "$code" == 200 || "$code" == 401 ]]; then
        if timeout 15 docker exec "$container_id" curl -fsS --connect-timeout 5 --max-time 10 http://127.0.0.1:48911/health >/dev/null 2>&1; then
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
counter_tmp=$(mktemp "$STATE_DIR/.fail-count.XXXXXX") || fail "Cannot create failure counter"
temporary="$counter_tmp"
printf '%s %s\n' "$container_id" "$count" > "$counter_tmp" || fail "Cannot write failure counter"
mv -f "$counter_tmp" "$COUNT_FILE" || fail "Cannot publish failure counter"
log "Health probe failed ($count/2)"

if (( count >= 2 )); then
    [[ ! -e "$STATE_DIR/disabled" ]] || exit 0
    # Recheck the same id immediately before restart: still running, still not
    # paused, and still on the exact same boot. If any of these changed while we
    # were observing, an operator acted and we stand down instead of fighting.
    current=$(timeout 10 docker inspect -f "$RECHECK_FMT" "$container_id") || fail "Cannot recheck container"
    IFS='|' read -r cur_running cur_paused cur_started <<< "$current" || fail "Invalid recheck metadata"
    if [[ "$cur_running" != true ]]; then
        log "Container is no longer running; standing down"
        exit 0
    fi
    if [[ "$cur_paused" == true ]]; then
        log "Container was paused meanwhile; standing down"
        rm -f "$COUNT_FILE"
        exit 0
    fi
    if [[ "$cur_started" != "$started_at" ]]; then
        log "Container restarted meanwhile; standing down"
        rm -f "$COUNT_FILE"
        exit 0
    fi
    # Serialize the actual restart against any external coordinator (e.g. an
    # operator running the same restart.lock). If someone else holds it, we
    # stand down instead of racing a concurrent restart.
    exec 8>"$STATE_DIR/restart.lock"
    if ! flock -n 8; then
        log "Another process holds the restart lock; standing down"
        exit 0
    fi
    rc=0
    timeout 60 docker restart "$container_id" >> "$STATE_DIR/watchdog.log" 2>&1 || rc=$?
    if [[ $rc -eq 0 ]]; then
        rm -f "$COUNT_FILE"
        log "Restart succeeded"
    elif [[ $rc -eq 124 ]]; then
        # timeout(1) exit 124 means the restart exceeded 60s on slow hardware, not
        # that it failed. Clear the counter; the fresh StartedAt grace window
        # re-arms recovery so we never double-restart.
        rm -f "$COUNT_FILE"
        log "Restart exceeded 60s (still completing); counter cleared"
        exit 0
    else
        fail "Restart failed; counter retained"
    fi
fi
