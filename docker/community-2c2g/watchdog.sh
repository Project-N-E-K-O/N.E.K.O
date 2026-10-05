#!/bin/bash
# Host-only recovery. No instance key or account credential is needed for probes.
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
STATE_DIR=/opt/neko
CONTAINER=neko
# Report pre-lock failures without writing through unsafe paths.
early_fail() {
    local message="watchdog: $*"
    printf '%s\n' "$message" >&2
    if [[ ! -L "$STATE_DIR" && -d "$STATE_DIR" ]] &&
       [[ $(stat -c '%u:%g:%a' "$STATE_DIR" 2>/dev/null) == 0:0:700 ]] &&
       [[ ! -L "$STATE_DIR/watchdog.log" ]] &&
       { [[ ! -e "$STATE_DIR/watchdog.log" ]] ||
         { [[ -f "$STATE_DIR/watchdog.log" ]] &&
           [[ $(stat -c '%u:%g:%a' "$STATE_DIR/watchdog.log") == 0:0:600 ]]; }; }; then
        printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$message" >> "$STATE_DIR/watchdog.log" || true
    fi
    if command -v logger >/dev/null; then
        logger -t neko-watchdog -- "$message" || true
    fi
    exit 1
}
[[ ! -L "$STATE_DIR" && -d "$STATE_DIR" ]] || early_fail "Unsafe state directory"
[[ $(stat -c '%u:%g:%a' "$STATE_DIR") == 0:0:700 ]] || early_fail "State directory must be root:root 0700"
[[ ! -e "$STATE_DIR/disabled" ]] || exit 0
STARTUP_GRACE_SECONDS=${NEKO_WATCHDOG_STARTUP_GRACE_SECONDS:-900}
[[ "$STARTUP_GRACE_SECONDS" =~ ^[0-9]{1,6}$ ]] || early_fail "Invalid startup grace: use integer seconds"
STARTUP_GRACE_SECONDS=$((10#$STARTUP_GRACE_SECONDS))
for dependency in docker curl timeout flock; do
    command -v "$dependency" >/dev/null || early_fail "Missing $dependency"
done
for file in watchdog.lock fail-count restart-count exhaustion-reported stopped-reported watchdog.log; do
    [[ ! -L "$STATE_DIR/$file" ]] || early_fail "Unsafe state file: $file"
done
exec 9>"$STATE_DIR/watchdog.lock"
flock -n 9 || exit 0
log() { printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$STATE_DIR/watchdog.log"; }
fail() { log "$*"; exit 1; }
COUNT_FILE="$STATE_DIR/fail-count"
RESTART_FILE="$STATE_DIR/restart-count"
EXHAUSTED_FILE="$STATE_DIR/exhaustion-reported"
STOPPED_FILE="$STATE_DIR/stopped-reported"
METADATA_FORMAT='{{.Id}} {{index .Config.Labels "org.neko.community-2c2g.watchdog"}} {{index .Config.Labels "com.docker.compose.service"}} {{.State.Running}} {{.State.Paused}} {{.State.Restarting}} {{.State.StartedAt}}'

# Do not transfer recovery authority to an unrelated container with the same name.
# Docker's unless-stopped policy handles exits; preserve intentional stops/removal.
if ! metadata=$(timeout 10 docker inspect --type container -f "$METADATA_FORMAT" "$CONTAINER" 2>/dev/null); then
    # Distinguish intentional removal from an unavailable daemon/failed inspect.
    containers=$(timeout 10 docker ps -a --filter "name=^/${CONTAINER}$" --format '{{.ID}}' 2>/dev/null) || fail "Cannot query containers; no restart attempted"
    [[ -z "$containers" ]] || fail "Container inspect failed; no restart attempted"
    rm -f "$COUNT_FILE"
    exit 0
fi
read -r container_id enabled service running paused restarting started_at <<< "$metadata" || fail "Invalid container metadata"
# A prior recovery attempt plus a stopped container needs operator attention.
# This cannot distinguish a failed start from a later intentional stop: report,
# but never acquire authority to start a stopped container.
if [[ "$enabled" == enabled && "$service" == neko-main && "$running" == false && "$paused" == false && "$restarting" == false && -e "$RESTART_FILE" ]]; then
    previous=$(cat "$RESTART_FILE") || fail "Cannot read restart budget"
    read -r restart_id restart_count extra <<< "$previous" || fail "Invalid restart budget"
    [[ "$restart_count" =~ ^[0-3]$ && -z "$extra" ]] || fail "Invalid restart budget"
    if [[ "$restart_id" == "$container_id" && "$restart_count" != 0 ]]; then
        reported=
        if [[ -e "$STOPPED_FILE" ]]; then
            reported=$(cat "$STOPPED_FILE") || fail "Cannot read stopped notification"
        fi
        [[ "$reported" != "$container_id $started_at" ]] || exit 1
        temporary=$(mktemp "$STATE_DIR/.stopped-reported.XXXXXX") || fail "Cannot create stopped notification"
        trap 'rm -f "$temporary"' EXIT
        printf '%s %s\n' "$container_id" "$started_at" > "$temporary" || fail "Cannot write stopped notification"
        mv -f "$temporary" "$STOPPED_FILE" || fail "Cannot publish stopped notification"
        fail "Container stopped after recorded automatic recovery; inspect startup failure or intentional stop; no start attempted (use disabled for maintenance)"
    fi
fi
if [[ "$enabled" != enabled || "$service" != neko-main || "$running" != true || "$paused" != false || "$restarting" != false ]]; then
    rm -f "$COUNT_FILE"
    exit 0
fi
rm -f "$STOPPED_FILE"
started_epoch=$(date -d "$started_at" +%s) || fail "Invalid container start time"
now=$(date +%s)
if (( now - started_epoch < STARTUP_GRACE_SECONDS )); then
    rm -f "$COUNT_FILE"
    exit 0
fi

healthy=false
probe_failure='host HTTP status unavailable'
# Log only phase/status/exit code; never response bodies or raw error output.
if code=$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 http://127.0.0.1:48911/ 2>/dev/null); then
    if [[ "$code" == 200 || "$code" == 401 ]]; then
        # Expand the configured backend port in the container, not on the host.
        if timeout 15 docker exec "$container_id" sh -c '
            port=${NEKO_MAIN_SERVER_PORT:-48911}
            case "$port" in ""|*[!0-9]*) exit 64 ;; esac
            [ "${#port}" -le 5 ] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || exit 64
            exec curl --noproxy "*" -fsS --connect-timeout 5 --max-time 10 "http://127.0.0.1:$port/health"
        ' >/dev/null 2>&1; then
            healthy=true
        else
            probe_failure="main-service probe exit $? (64=invalid port, 124=timeout)"
        fi
    elif [[ "$code" =~ ^[0-9]{3}$ ]]; then
        probe_failure="host HTTP status $code"
    fi
else
    probe_failure="host curl exit $?"
fi
if [[ "$healthy" == true ]]; then
    rm -f "$COUNT_FILE" "$RESTART_FILE" "$EXHAUSTED_FILE"
    exit 0
fi

count=0
if [[ -e "$COUNT_FILE" ]]; then
    previous=$(cat "$COUNT_FILE") || fail "Cannot read failure counter"
    read -r previous_id previous_count previous_start <<< "$previous" || fail "Invalid failure counter"
    [[ "$previous_count" =~ ^[0-2]$ ]] || fail "Invalid failure counter"
    # Older two-field counters are discarded; restart/recreation begins a new run.
    if [[ "$previous_id" == "$container_id" && "$previous_start" == "$started_at" ]]; then
        count=$previous_count
    fi
fi
old_count=$count
count=$((count + 1))
(( count <= 2 )) || count=2
temporary=$(mktemp "$STATE_DIR/.fail-count.XXXXXX") || fail "Cannot create failure counter"
trap 'rm -f "$temporary"' EXIT
printf '%s %s %s\n' "$container_id" "$count" "$started_at" > "$temporary" || fail "Cannot write failure counter"
mv -f "$temporary" "$COUNT_FILE" || fail "Cannot publish failure counter"
if (( count != old_count )); then log "Health probe failed ($count/2): $probe_failure"; fi
if (( count >= 2 )); then
    # Maintenance must hold this same lock while setting disabled (see README).
    [[ ! -e "$STATE_DIR/disabled" ]] || exit 0
    current=$(timeout 10 docker inspect --type container -f "$METADATA_FORMAT" "$container_id" 2>/dev/null) || fail "Cannot recheck container"
    [[ "$current" == "$metadata" ]] || { rm -f "$COUNT_FILE"; exit 0; }
    # Persist attempts across startup grace and StartedAt changes.
    restart_count=0
    if [[ -e "$RESTART_FILE" ]]; then
        previous=$(cat "$RESTART_FILE") || fail "Cannot read restart budget"
        read -r restart_id restart_count extra <<< "$previous" || fail "Invalid restart budget"
        [[ "$restart_count" =~ ^[0-3]$ && -z "$extra" ]] || fail "Invalid restart budget"
        [[ "$restart_id" == "$container_id" ]] || restart_count=0
    fi
    if (( restart_count >= 3 )); then
        reported=
        if [[ -e "$EXHAUSTED_FILE" ]]; then
            reported=$(cat "$EXHAUSTED_FILE") || fail "Cannot read exhaustion notification"
        fi
        [[ "$reported" != "$container_id" ]] || exit 1
        temporary=$(mktemp "$STATE_DIR/.exhaustion-reported.XXXXXX") || fail "Cannot create exhaustion notification"
        printf '%s\n' "$container_id" > "$temporary" || fail "Cannot write exhaustion notification"
        mv -f "$temporary" "$EXHAUSTED_FILE" || fail "Cannot publish exhaustion notification"
        fail "Automatic recovery exhausted (3 attempts); inspect service and clear restart-count under maintenance lock"
    fi
    rm -f "$EXHAUSTED_FILE"
    temporary=$(mktemp "$STATE_DIR/.restart-count.XXXXXX") || fail "Cannot create restart budget"
    printf '%s %s\n' "$container_id" "$((restart_count + 1))" > "$temporary" || fail "Cannot write restart budget"
    mv -f "$temporary" "$RESTART_FILE" || fail "Cannot publish restart budget"
    if timeout 120 docker restart -t 30 "$container_id" >/dev/null 2>&1; then
        rm -f "$COUNT_FILE"
        log "Restart succeeded"
    else
        # CLI timeout does not cancel a daemon restart. Confirm the new lifecycle.
        current=$(timeout 10 docker inspect --type container -f "$METADATA_FORMAT" "$container_id" 2>/dev/null) || fail "Cannot confirm restart; counter retained"
        read -r current_id current_enabled current_service current_running current_paused current_restarting current_start <<< "$current" || fail "Invalid restart metadata"
        if [[ "$current_id" == "$container_id" && "$current_enabled" == enabled && "$current_service" == neko-main ]] &&
           [[ "$current_restarting" == true || ( "$current_running" == true && "$current_start" != "$started_at" ) ]]; then
            rm -f "$COUNT_FILE"
            log "Restart observed after CLI failure; next run honors startup grace"
        else
            fail "Restart not confirmed; counter retained"
        fi
    fi
fi
