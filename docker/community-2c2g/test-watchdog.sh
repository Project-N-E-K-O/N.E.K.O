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
cat > "$ROOT/bin/docker" <<'EOF'
#!/bin/bash
set -eu
case "$1" in
    inspect)
        [[ ${INSPECT_FAIL:-0} == 0 ]] || exit 1
        if [[ "$3" == '{{.State.Running}}' ]]; then
            echo "${RECHECK_RUNNING:-true}"
        else
            echo "${CONTAINER_ID:-id-1} ${LABEL:-enabled} neko-main ${RUNNING:-true}"
        fi ;;
    exec) exit "${BACKEND_EXIT:-0}" ;;
    restart) echo "$2" >> "$TEST_ROOT/restarts"; exit "${RESTART_EXIT:-0}" ;;
    *) exit 99 ;;
esac
EOF
cat > "$ROOT/bin/curl" <<'EOF'
#!/bin/bash
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
reset() { rm -f "$ROOT/state/fail-count" "$ROOT/restarts"; }
run; no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
HTTP_CODE=200 run; no_restart
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

# Run the actual installer against disposable host directories.
sed -e "s|/host-opt|$ROOT/opt|g" -e "s|/host-cron.d|$ROOT/cron|g" \
    -e "s|/source/watchdog.sh|$SOURCE/watchdog.sh|g" \
    "$SOURCE/install-watchdog.sh" > "$ROOT/install.sh"
sh "$ROOT/install.sh"
[[ $(stat -c '%u:%g:%a' "$ROOT/opt/neko") == 0:0:700 ]]
[[ $(stat -c '%u:%g:%a' "$ROOT/opt/neko/watchdog.sh") == 0:0:700 ]]
[[ $(stat -c '%u:%g:%a' "$ROOT/cron/neko-watchdog") == 0:0:644 ]]
cmp "$SOURCE/watchdog.sh" "$ROOT/opt/neko/watchdog.sh"
touch "$ROOT/opt/neko/disabled"
sh "$ROOT/install.sh"
[[ -e "$ROOT/opt/neko/disabled" ]]
rm "$ROOT/opt/neko/watchdog.sh"
ln -s "$ROOT/victim" "$ROOT/opt/neko/watchdog.sh"
if sh "$ROOT/install.sh"; then exit 1; fi
[[ ! -e "$ROOT/victim" ]]
chmod 777 "$ROOT/opt/neko"
if sh "$ROOT/install.sh"; then exit 1; fi
echo 'PASS: authorization 401, timeouts, backend failure, restart, lifecycle, identity, counter, lock and installer permissions'
