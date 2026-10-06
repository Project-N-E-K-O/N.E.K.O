#!/bin/bash
# Linux-only isolated regression harness. Run as root for real ownership checks.
# Docker/curl are mocks; no host deployment or cron directory is modified.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run this isolated harness as root' >&2; exit 1; }
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(mktemp -d)
trap 'rm -rf -- "$ROOT"' EXIT
mkdir "$ROOT/bin" "$ROOT/state" "$ROOT/opt" "$ROOT/cron"
chmod 700 "$ROOT/state"
export TEST_ROOT="$ROOT"
cat > "$ROOT/bin/logger" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "$TEST_ROOT/syslog"
EOF
chmod 700 "$ROOT/bin/logger"
cat > "$ROOT/bin/docker" <<'EOF'
#!/bin/bash
set -eu
[[ ${RAW_ERROR:-0} == 0 ]] || echo private-daemon-error >&2
case "$1" in
    inspect)
        [[ "$2 $3" == '--type container' ]] || exit 99
        shift 2
        [[ ${INSPECT_FAIL:-0} == 0 && ${CONTAINER_ABSENT:-0} == 0 ]] || exit 1
        running=${RUNNING:-true}
        paused=${PAUSED:-false}
        restarting=${RESTARTING:-false}
        started=${STARTED_AT:-2000-01-01T00:00:00Z}
        if [[ "$4" != neko ]]; then
            running=${RECHECK_RUNNING:-$running}
            paused=${RECHECK_PAUSED:-$paused}
            started=${RECHECK_STARTED_AT:-$started}
            if [[ -e "$TEST_ROOT/restart-attempt" ]]; then
                started=${POST_RESTART_STARTED_AT:-$started}
                restarting=${POST_RESTART_RESTARTING:-$restarting}
            fi
        fi
        echo "${CONTAINER_ID:-id-1} ${LABEL:-enabled} neko-main $running $paused $restarting $started" ;;
    ps) [[ ${INSPECT_FAIL:-0} == 0 ]] || exit 1
        [[ ${CONTAINER_ABSENT:-0} == 1 ]] || echo id-1 ;;
    exec)
        [[ "$3 $4" == 'sh -c' ]] || exit 99
        env -i PATH="$TEST_ROOT/bin:/usr/bin:/bin" TEST_ROOT="$TEST_ROOT" BACKEND_PROBE=1 \
            RAW_ERROR="${RAW_ERROR:-0}" BACKEND_EXIT="${BACKEND_EXIT:-0}" NEKO_MAIN_SERVER_PORT="${MOCK_CONTAINER_PORT:-48911}" /bin/sh -c "$5" ;;
    restart)
        [[ "$2 $3" == '-t 30' ]] || exit 99
        echo "$4" >> "$TEST_ROOT/restarts"
        touch "$TEST_ROOT/restart-attempt"
        if [[ ${RESTART_DELAY:-0} == 1 ]]; then
            touch "$TEST_ROOT/restart-begun"
            while [[ ! -e "$TEST_ROOT/restart-release" ]]; do sleep 0.05; done
        fi
        exit "${RESTART_EXIT:-0}" ;;
    *) exit 99 ;;
esac
EOF
cat > "$ROOT/bin/curl" <<'EOF'
#!/bin/bash
[[ "$1 $2" == '--noproxy *' ]] || exit 99
[[ ${RAW_ERROR:-0} == 0 ]] || echo private-curl-error >&2
if [[ ${BACKEND_PROBE:-0} == 1 ]]; then
    printf '%s\n' "${@: -1}" > "$TEST_ROOT/backend-url"
    exit "${BACKEND_EXIT:-0}"
fi
printf '%s' "${HTTP_CODE:-401}"
exit "${CURL_EXIT:-0}"
EOF
chmod 700 "$ROOT/bin/"*
# Only paths are adapted. Real Bash, stat, flock and atomic state writes are used.
sed -e "s|STATE_DIR=/opt/neko|STATE_DIR=$ROOT/state|" \
    -e "s|export PATH=.*|export PATH=$ROOT/bin:/usr/bin:/bin|" \
    "$SOURCE/watchdog.sh" > "$ROOT/watchdog.sh"
bash -n "$SOURCE/watchdog.sh"
sh -n "$SOURCE/install-watchdog.sh"
run() { bash "$ROOT/watchdog.sh"; }
no_restart() { [[ ! -e "$ROOT/restarts" ]]; }
reset() { rm -f "$ROOT/state/fail-count" "$ROOT/state/restart-count" "$ROOT/state/exhaustion-reported" "$ROOT/state/stopped-reported" "$ROOT/restarts" "$ROOT/restart-attempt" "$ROOT/restart-begun" "$ROOT/restart-release"; }
reset
RAW_ERROR=1 CURL_EXIT=28 run 2> "$ROOT/raw-error"; no_restart
[[ ! -s "$ROOT/raw-error" ]]
reset
RAW_ERROR=1 BACKEND_EXIT=22 run 2> "$ROOT/raw-error"; no_restart
[[ ! -s "$ROOT/raw-error" ]]
reset
RAW_ERROR=1 HTTP_CODE=502 run 2> "$ROOT/raw-error"
if RAW_ERROR=1 HTTP_CODE=502 RESTART_EXIT=1 run 2> "$ROOT/raw-error"; then exit 1; fi
[[ ! -s "$ROOT/raw-error" ]]
if grep -q 'private-daemon-error\|private-curl-error' "$ROOT/state/watchdog.log"; then exit 1; fi
reset
run; no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
HTTP_CODE=200 run; no_restart
NEKO_MAIN_SERVER_PORT=59999 MOCK_CONTAINER_PORT=50001 run; no_restart
[[ $(cat "$ROOT/backend-url") == http://127.0.0.1:50001/health ]]
for port in invalid 0 65536 '48911/path'; do
    reset
    rm -f "$ROOT/backend-url"
    MOCK_CONTAINER_PORT="$port" run; no_restart
    [[ ! -e "$ROOT/backend-url" ]]
    grep -q 'main-service probe exit 64' "$ROOT/state/watchdog.log"
done
reset
HTTP_CODE=502 run; no_restart
grep -q 'host HTTP status 502' "$ROOT/state/watchdog.log"
reset
CURL_EXIT=28 run; no_restart
grep -q 'host curl exit 28' "$ROOT/state/watchdog.log"
reset
BACKEND_EXIT=22 run; no_restart
grep -q 'main-service probe exit 22' "$ROOT/state/watchdog.log"
reset
http_proxy=http://127.0.0.1:1 ALL_PROXY=http://127.0.0.1:1 run
no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
CURL_EXIT=28 run; no_restart; grep -q 'id-1 1' "$ROOT/state/fail-count"
CURL_EXIT=28 run; [[ $(cat "$ROOT/restarts") == id-1 ]]
[[ ! -e "$ROOT/state/fail-count" ]]
reset
BACKEND_EXIT=22 run; BACKEND_EXIT=22 run
[[ $(cat "$ROOT/restarts") == id-1 ]]
reset
HTTP_CODE=500 run
CONTAINER_ID=id-2 HTTP_CODE=500 run; no_restart
grep -q 'id-2 1' "$ROOT/state/fail-count"
RUNNING=false run; no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
LABEL=other HTTP_CODE=500 run; no_restart
touch "$ROOT/state/disabled"
log_size=$(stat -c %s "$ROOT/state/watchdog.log")
syslog_size=$(stat -c %s "$ROOT/syslog" 2>/dev/null || echo 0)
NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=1800s HTTP_CODE=500 run; no_restart
[[ $(stat -c %s "$ROOT/state/watchdog.log") == "$log_size" ]]
[[ $(stat -c %s "$ROOT/syslog" 2>/dev/null || echo 0) == "$syslog_size" ]]
HTTP_CODE=500 run; no_restart
rm "$ROOT/state/disabled"
HTTP_CODE=500 run
RECHECK_RUNNING=false HTTP_CODE=500 run; no_restart
reset
HTTP_CODE=500 run
if RESTART_EXIT=1 HTTP_CODE=500 run; then exit 1; fi
grep -q 'id-1 2' "$ROOT/state/fail-count"
reset
printf 'invalid\n' > "$ROOT/state/fail-count"
if HTTP_CODE=500 run; then exit 1; fi
no_restart
reset
mkdir "$ROOT/state/fail-count"
if HTTP_CODE=500 run; then exit 1; fi
no_restart
rmdir "$ROOT/state/fail-count"
HTTP_CODE=500 run
cat > "$ROOT/bin/mv" <<'EOF'
#!/bin/bash
exit 1
EOF
chmod 700 "$ROOT/bin/mv"
if HTTP_CODE=500 run; then exit 1; fi
no_restart
grep -q 'id-1 1' "$ROOT/state/fail-count"
rm "$ROOT/bin/mv"
reset
ln -s "$ROOT/victim" "$ROOT/state/fail-count"
if run; then exit 1; fi
[[ ! -e "$ROOT/victim" ]]
rm "$ROOT/state/fail-count"
if INSPECT_FAIL=1 run; then exit 1; fi
no_restart
# A held flock excludes manual/cron overlap.
flock "$ROOT/state/watchdog.lock" bash -c 'CURL_EXIT=28 bash "$1"' _ "$ROOT/watchdog.sh"
[[ ! -e "$ROOT/state/fail-count" ]]
chmod 777 "$ROOT/state"
if run; then exit 1; fi
chmod 700 "$ROOT/state"

# Startup grace applies to initial boot and restarts of the same ID.
reset
HTTP_CODE=500 run
STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ) HTTP_CODE=500 run
no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
STARTED_AT=$(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ) HTTP_CODE=500 run
no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
STARTED_AT=$(date -u -d '16 minutes ago' +%Y-%m-%dT%H:%M:%SZ) HTTP_CODE=500 run
no_restart; grep -q 'id-1 1' "$ROOT/state/fail-count"
NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=0 STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ) HTTP_CODE=500 run
no_restart; grep -q 'id-1 1' "$ROOT/state/fail-count"
if NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=invalid run; then exit 1; fi
reset
HTTP_CODE=500 run
STARTED_AT=2001-01-01T00:00:00Z HTTP_CODE=500 run
no_restart; grep -q 'id-1 1 2001-' "$ROOT/state/fail-count"
PAUSED=true HTTP_CODE=500 run; no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
HTTP_CODE=500 run
RECHECK_PAUSED=true HTTP_CODE=500 run; no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
HTTP_CODE=500 run
RECHECK_STARTED_AT=2001-01-01T00:00:00Z HTTP_CODE=500 run
no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
RESTARTING=true HTTP_CODE=500 run; no_restart
HTTP_CODE=500 run
log_size=$(stat -c %s "$ROOT/state/watchdog.log")
CONTAINER_ABSENT=1 run; CONTAINER_ABSENT=1 run
no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
[[ $(stat -c %s "$ROOT/state/watchdog.log") == "$log_size" ]]
# CLI failure can still mean the daemon completed/accepted the restart.
for result in started restarting; do
    reset
    HTTP_CODE=500 run
    if [[ "$result" == started ]]; then
        RESTART_EXIT=124 POST_RESTART_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ) HTTP_CODE=500 run
    else
        RESTART_EXIT=124 POST_RESTART_RESTARTING=true HTTP_CODE=500 run
    fi
    [[ $(cat "$ROOT/restarts") == id-1 && ! -e "$ROOT/state/fail-count" ]]
done
reset
# The documented maintenance lock waits for an in-flight restart before pausing.
HTTP_CODE=500 run
RESTART_DELAY=1 HTTP_CODE=500 run & watchdog_pid=$!
for ((attempt=0; attempt<100; attempt++)); do
    [[ ! -e "$ROOT/restart-begun" ]] || break
    sleep 0.05
done
[[ -e "$ROOT/restart-begun" ]]
flock "$ROOT/state/watchdog.lock" touch "$ROOT/state/disabled" & maintenance_pid=$!
sleep 0.1
[[ ! -e "$ROOT/state/disabled" ]]
touch "$ROOT/restart-release"
wait "$watchdog_pid"; wait "$maintenance_pid"
[[ -e "$ROOT/state/disabled" ]]
HTTP_CODE=500 run; [[ $(wc -l < "$ROOT/restarts") == 1 ]]
rm "$ROOT/state/disabled"
reset
# Budget survives lifecycle changes and grace; health resets it.
reset
for cycle in 1 2 3; do
    STARTED_AT="2000-01-0${cycle}T00:00:00Z" HTTP_CODE=500 run
    STARTED_AT="2000-01-0${cycle}T00:00:00Z" HTTP_CODE=500 run
done
[[ $(wc -l < "$ROOT/restarts") == 3 ]]
STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ) HTTP_CODE=500 run
grep -q 'id-1 3' "$ROOT/state/restart-count"
HTTP_CODE=500 run
if HTTP_CODE=500 run; then exit 1; fi
[[ $(wc -l < "$ROOT/restarts") == 3 ]]
grep -q 'Automatic recovery exhausted' "$ROOT/state/watchdog.log"
log_size=$(stat -c %s "$ROOT/state/watchdog.log")
if HTTP_CODE=500 run; then exit 1; fi
[[ $(stat -c %s "$ROOT/state/watchdog.log") == "$log_size" ]]
run; [[ ! -e "$ROOT/state/restart-count" ]]
reset
printf 'id-old 3\n' > "$ROOT/state/restart-count"
HTTP_CODE=500 run; HTTP_CODE=500 run
[[ $(wc -l < "$ROOT/restarts") == 1 ]]
reset
if NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=30m run; then exit 1; fi
grep -q 'Invalid startup grace' "$ROOT/state/watchdog.log"

: > "$ROOT/syslog"
log_size=$(stat -c %s "$ROOT/state/watchdog.log")
chmod 755 "$ROOT/state"
if run; then exit 1; fi
[[ $(stat -c %s "$ROOT/state/watchdog.log") == "$log_size" ]]
grep -q 'State directory must' "$ROOT/syslog"
chmod 700 "$ROOT/state"
# This case must never fall back to a real Docker executable in /usr/bin.
mkdir "$ROOT/missing-tools"
for tool in stat date timeout flock; do
    ln -s "$(command -v "$tool")" "$ROOT/missing-tools/$tool"
done
sed "s|export PATH=.*|export PATH=$ROOT/bin:$ROOT/missing-tools|" "$ROOT/watchdog.sh" > "$ROOT/missing-docker.sh"
mv "$ROOT/bin/docker" "$ROOT/bin/docker.hidden"
if bash "$ROOT/missing-docker.sh"; then exit 1; fi
grep -q 'Missing docker' "$ROOT/state/watchdog.log"
mv "$ROOT/bin/docker.hidden" "$ROOT/bin/docker"
reset
HTTP_CODE=500 run
for attempt in 1 2 3 4; do
    if RESTART_EXIT=1 HTTP_CODE=500 run; then exit 1; fi
done
[[ $(wc -l < "$ROOT/restarts") == 3 ]]
reset
printf 'id-1 invalid\n' > "$ROOT/state/restart-count"
HTTP_CODE=500 run
if HTTP_CODE=500 run; then exit 1; fi
no_restart
reset
# Stopped containers with a matching recovery record require diagnosis, not start.
reset
printf 'id-1 1\n' > "$ROOT/state/restart-count"
if RUNNING=false run; then exit 1; fi
no_restart; grep -q 'Container stopped after recorded automatic recovery' "$ROOT/state/watchdog.log"
grep -qx 'id-1 1' "$ROOT/state/restart-count"
log_size=$(stat -c %s "$ROOT/state/watchdog.log")
if RUNNING=false run; then exit 1; fi
[[ $(stat -c %s "$ROOT/state/watchdog.log") == "$log_size" ]]
touch "$ROOT/state/disabled"
RUNNING=false run; no_restart
[[ $(stat -c %s "$ROOT/state/watchdog.log") == "$log_size" ]]
rm "$ROOT/state/disabled"
CONTAINER_ID=id-new RUNNING=false run; no_restart
[[ $(stat -c %s "$ROOT/state/watchdog.log") == "$log_size" ]]
reset
# Run the actual installer against disposable host directories.
sed -e "s|/host-opt|$ROOT/opt|g" -e "s|/host-cron.d|$ROOT/cron|g" \
    -e "s|/source/watchdog.sh|$SOURCE/watchdog.sh|g" \
    "$SOURCE/install-watchdog.sh" > "$ROOT/install.sh"
sh "$ROOT/install.sh"
[[ $(stat -c '%u:%g:%a' "$ROOT/opt/neko") == 0:0:700 ]]
[[ $(stat -c '%u:%g:%a' "$ROOT/opt/neko/watchdog.sh") == 0:0:700 ]]
[[ $(stat -c '%u:%g:%a' "$ROOT/cron/neko-watchdog") == 0:0:644 ]]
cmp "$SOURCE/watchdog.sh" "$ROOT/opt/neko/watchdog.sh"
sed -i '3iNEKO_WATCHDOG_STARTUP_GRACE_SECONDS=1800' "$ROOT/cron/neko-watchdog"
touch "$ROOT/opt/neko/disabled"
sh "$ROOT/install.sh" > "$ROOT/install-output"
grep -q 'recovery remains PAUSED' "$ROOT/install-output"
[[ -e "$ROOT/opt/neko/disabled" ]]
grep -qx NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=1800 "$ROOT/cron/neko-watchdog"
cp "$ROOT/cron/neko-watchdog" "$ROOT/cron-before"
printf 'NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=30m\n' >> "$ROOT/cron/neko-watchdog"
if sh "$ROOT/install.sh" 2> "$ROOT/install-error"; then exit 1; fi
grep -q 'duplicate startup grace settings' "$ROOT/install-error"
grep -qx NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=30m "$ROOT/cron/neko-watchdog"
cp "$ROOT/cron-before" "$ROOT/cron/neko-watchdog"
# Cron allows spaces and tabs around assignments; normalize on reinstall.
sed -i 's/^NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=.*/\t NEKO_WATCHDOG_STARTUP_GRACE_SECONDS \t= \t300 \t/' "$ROOT/cron/neko-watchdog"
sh "$ROOT/install.sh"
grep -qx NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=300 "$ROOT/cron/neko-watchdog"
printf ' NEKO_WATCHDOG_STARTUP_GRACE_SECONDS = 600\n' >> "$ROOT/cron/neko-watchdog"
cp "$ROOT/cron/neko-watchdog" "$ROOT/cron-duplicate"
if sh "$ROOT/install.sh" 2> "$ROOT/install-error"; then exit 1; fi
grep -q 'duplicate startup grace settings' "$ROOT/install-error"
cmp "$ROOT/cron/neko-watchdog" "$ROOT/cron-duplicate"
cp "$ROOT/cron-before" "$ROOT/cron/neko-watchdog"
sed -i 's/^NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=.*/ NEKO_WATCHDOG_STARTUP_GRACE_SECONDS = 30m /' "$ROOT/cron/neko-watchdog"
if sh "$ROOT/install.sh" 2> "$ROOT/install-error"; then exit 1; fi
grep -q 'invalid existing startup grace' "$ROOT/install-error"
cp "$ROOT/cron-before" "$ROOT/cron/neko-watchdog"
# Read errors must not be diagnosed as duplicate assignments.
cp "$ROOT/cron/neko-watchdog" "$ROOT/cron-read-before"
cp "$ROOT/opt/neko/watchdog.sh" "$ROOT/script-read-before"
cat > "$ROOT/bin/grep" <<'EOF'
#!/bin/sh
exit 2
EOF
chmod 700 "$ROOT/bin/grep"
if PATH="$ROOT/bin:$PATH" sh "$ROOT/install.sh" 2> "$ROOT/install-error"; then exit 1; fi
rm "$ROOT/bin/grep"
grep -q 'cannot read existing cron settings' "$ROOT/install-error"
cmp "$ROOT/cron/neko-watchdog" "$ROOT/cron-read-before"
cmp "$ROOT/opt/neko/watchdog.sh" "$ROOT/script-read-before"
# Fail during cron preparation after script copy; neither published file changes.
# Make the installed script differ from the source so premature publication fails.
printf '# previous installed version\n' > "$ROOT/opt/neko/watchdog.sh"
cp "$ROOT/opt/neko/watchdog.sh" "$ROOT/script-read-before"
cat > "$ROOT/bin/sed" <<'EOF'
#!/bin/sh
if [ "$1" = -i ]; then exit 2; fi
exec /usr/bin/sed "$@"
EOF
chmod 700 "$ROOT/bin/sed"
if PATH="$ROOT/bin:$PATH" sh "$ROOT/install.sh" 2> "$ROOT/install-error"; then exit 1; fi
rm "$ROOT/bin/sed"
cmp "$ROOT/cron/neko-watchdog" "$ROOT/cron-read-before"
cmp "$ROOT/opt/neko/watchdog.sh" "$ROOT/script-read-before"
# Normalize one matching quote pair, and reject malformed or nonnumeric values.
for value in '"1800"' "'300'"; do
    cp "$ROOT/cron-before" "$ROOT/cron/neko-watchdog"
    sed -i "s/^NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=.*/NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=$value/" "$ROOT/cron/neko-watchdog"
    sh "$ROOT/install.sh"
    expected=${value:1:${#value}-2}
    grep -qx "NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=$expected" "$ROOT/cron/neko-watchdog"
done
for value in '"30m"' '"300' "'300\"" '"'"'300'"'"'; do
    cp "$ROOT/cron-before" "$ROOT/cron/neko-watchdog"
    sed -i "s/^NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=.*/NEKO_WATCHDOG_STARTUP_GRACE_SECONDS=$value/" "$ROOT/cron/neko-watchdog"
    cp "$ROOT/cron/neko-watchdog" "$ROOT/cron-invalid"
    if sh "$ROOT/install.sh" 2> "$ROOT/install-error"; then exit 1; fi
    grep -q 'invalid existing startup grace' "$ROOT/install-error"
    cmp "$ROOT/cron/neko-watchdog" "$ROOT/cron-invalid"
done
cp "$ROOT/cron-before" "$ROOT/cron/neko-watchdog"
# Reject directories and FIFOs before publishing either destination.
rm "$ROOT/opt/neko/watchdog.sh"
for kind in directory fifo; do
    if [[ "$kind" == directory ]]; then mkdir "$ROOT/opt/neko/watchdog.sh"; else mkfifo "$ROOT/opt/neko/watchdog.sh"; fi
    if sh "$ROOT/install.sh" 2> "$ROOT/install-error"; then exit 1; fi
    grep -q 'watchdog destination is not a regular file' "$ROOT/install-error"
    cmp "$ROOT/cron/neko-watchdog" "$ROOT/cron-before"
    if [[ "$kind" == directory ]]; then
        [[ -z $(ls -A "$ROOT/opt/neko/watchdog.sh") ]]
        rmdir "$ROOT/opt/neko/watchdog.sh"
    else rm "$ROOT/opt/neko/watchdog.sh"; fi
done
sh "$ROOT/install.sh"
# A failed second mktemp and TERM during copying must leave no temporary files.
rm "$ROOT/opt/neko/watchdog.sh" "$ROOT/cron/neko-watchdog"
cat > "$ROOT/bin/mktemp" <<'EOF'
#!/bin/bash
[[ "$1" != "$TEST_ROOT/cron/"* ]] || exit 1
exec /usr/bin/mktemp "$@"
EOF
chmod 700 "$ROOT/bin/mktemp"
if PATH="$ROOT/bin:$PATH" sh "$ROOT/install.sh"; then exit 1; fi
[[ -z $(find "$ROOT/opt/neko" "$ROOT/cron" -name '.*watchdog.*' -print) ]]
rm "$ROOT/bin/mktemp"
cat > "$ROOT/bin/cp" <<'EOF'
#!/bin/bash
kill -TERM "$PPID"
EOF
chmod 700 "$ROOT/bin/cp"
if PATH="$ROOT/bin:$PATH" sh "$ROOT/install.sh"; then exit 1; fi
[[ ! -e "$ROOT/opt/neko/watchdog.sh" && ! -e "$ROOT/cron/neko-watchdog" ]]
[[ -z $(find "$ROOT/opt/neko" "$ROOT/cron" -name '.*watchdog.*' -print) ]]
rm "$ROOT/bin/cp"
sh "$ROOT/install.sh"
rm "$ROOT/opt/neko/watchdog.sh"
ln -s "$ROOT/victim" "$ROOT/opt/neko/watchdog.sh"
if sh "$ROOT/install.sh"; then exit 1; fi
[[ ! -e "$ROOT/victim" ]]
chmod 777 "$ROOT/opt/neko"
if sh "$ROOT/install.sh"; then exit 1; fi
echo 'PASS: probes, startup grace, pause/removal, restart confirmation, maintenance lock, counters, installer cleanup and permissions'
