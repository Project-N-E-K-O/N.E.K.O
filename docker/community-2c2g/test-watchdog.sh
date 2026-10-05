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

# Formats must match the literal strings used in watchdog.sh.
INSPECT_FMT='{{.Id}}|{{index .Config.Labels "org.neko.community-2c2g.watchdog"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Running}}|{{.State.Paused}}|{{.State.StartedAt}}'
RECHECK_FMT='{{.State.Running}}|{{.State.Paused}}|{{.State.StartedAt}}'

cat > "$ROOT/bin/docker" <<EOF
#!/bin/bash
set -eu
INSPECT_FMT='$INSPECT_FMT'
RECHECK_FMT='$RECHECK_FMT'
case "\$1" in
    inspect)
        [[ \${INSPECT_FAIL:-0} == 0 ]] || exit 1
        if [[ "\$3" == "\$RECHECK_FMT" ]]; then
            echo "\${RECHECK_RUNNING:-true}|\${RECHECK_PAUSED:-false}|\${RECHECK_STARTED:-\$STARTED_AT}"
        elif [[ "\$3" == "\$INSPECT_FMT" ]]; then
            echo "\${CONTAINER_ID:-id-1}|\${LABEL:-enabled}|neko-main|\${RUNNING:-true}|\${PAUSED:-false}|\${STARTED_AT:-2024-01-01T00:00:00Z}"
        else
            echo "unexpected fmt" >&2; exit 99
        fi ;;
    exec) exit "\${BACKEND_EXIT:-0}" ;;
    restart) echo "\$2" >> "\$TEST_ROOT/restarts"; exit "\${RESTART_EXIT:-0}" ;;
    *) exit 99 ;;
esac
EOF
cat > "$ROOT/bin/curl" <<'EOF'
#!/bin/bash
printf '%s' "${HTTP_CODE:-401}"
exit "${CURL_EXIT:-0}"
EOF
chmod 700 "$ROOT/bin/"*

# Only paths are adapted. Real Bash, stat, flock, date and atomic writes are used.
sed -e "s|STATE_DIR=/opt/neko|STATE_DIR=$ROOT/state|" \
    -e "s|export PATH=.*|export PATH=$ROOT/bin:/usr/bin:/bin|" \
    "$SOURCE/watchdog.sh" > "$ROOT/watchdog.sh"
bash -n "$SOURCE/watchdog.sh"
sh -n "$SOURCE/install-watchdog.sh"
run() { bash "$ROOT/watchdog.sh"; }
no_restart() { [[ ! -e "$ROOT/restarts" ]]; }
reset() { rm -f "$ROOT/state/fail-count" "$ROOT/state/started-at" "$ROOT/restarts"; }
NOW=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# --- baseline: healthy -> no counter, no restart ---------------------------------
run; no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
HTTP_CODE=200 run; no_restart

# --- two consecutive failures trigger one restart --------------------------------
reset
CURL_EXIT=28 run; no_restart; grep -q 'id-1 1' "$ROOT/state/fail-count"
CURL_EXIT=28 run; [[ $(cat "$ROOT/restarts") == id-1 ]]
[[ ! -e "$ROOT/state/fail-count" ]]

# --- backend failure path (docker exec fails) ------------------------------------
reset
BACKEND_EXIT=22 run; BACKEND_EXIT=22 run
[[ $(cat "$ROOT/restarts") == id-1 ]]
reset

# --- counter bound to container id: id change resets ----------------------------
HTTP_CODE=500 run
CONTAINER_ID=id-2 HTTP_CODE=500 run; no_restart
grep -q 'id-2 1' "$ROOT/state/fail-count"
reset

# --- intentionally stopped / mismatched label -> never touched ------------------
RUNNING=false run; no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
LABEL=other HTTP_CODE=500 run; no_restart
reset

# --- maintenance pause via disabled file ----------------------------------------
touch "$ROOT/state/disabled"
HTTP_CODE=500 run; no_restart
rm "$ROOT/state/disabled"
reset

# --- started-at grace window: recent boot -> no failure counting ----------------
reset
STARTED_AT="$NOW" CURL_EXIT=28 run
no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
reset

# --- a restart changes StartedAt -> counter reset + new grace -------------------
CURL_EXIT=28 run; CURL_EXIT=28 run
[[ $(cat "$ROOT/state/restarts") == id-1 ]]
reset
STARTED_AT="$NOW" CURL_EXIT=28 run
no_restart; [[ ! -e "$ROOT/state/fail-count" ]]
reset

# --- paused container is never restarted and counter is cleared -----------------
PAUSED=true HTTP_CODE=500 run; no_restart
[[ ! -e "$ROOT/state/fail-count" ]]
reset

# --- pause during recheck -> stand down, clear counter --------------------------
HTTP_CODE=500 run                       # counter 1
RECHECK_PAUSED=true HTTP_CODE=500 run; no_restart
[[ ! -e "$ROOT/state/fail-count" ]]
reset

# --- start-time changed during recheck (operator raced) -> stand down -----------
HTTP_CODE=500 run                       # counter 1
RECHECK_STARTED="2025-01-01T00:00:00Z" HTTP_CODE=500 run; no_restart
[[ ! -e "$ROOT/state/fail-count" ]]
reset

# --- restart timed out (exit 124) -> counter cleared, not retained --------------
HTTP_CODE=500 run                       # counter 1
RESTART_EXIT=124 HTTP_CODE=500 run
[[ $(cat "$ROOT/state/restarts") == id-1 ]]
[[ ! -e "$ROOT/state/fail-count" ]]
reset

# --- hard restart failure retains the counter -----------------------------------
HTTP_CODE=500 run
if RESTART_EXIT=1 HTTP_CODE=500 run; then exit 1; fi
grep -q 'id-1 2' "$ROOT/state/fail-count"
reset

# --- restart.lock contention -> watchdog stands down without restarting --------
HTTP_CODE=500 run                       # counter 1
flock -x "$ROOT/state/restart.lock" -c "HTTP_CODE=500 bash '$ROOT/watchdog.sh'"
no_restart
grep -q 'id-1 2' "$ROOT/state/fail-count"
reset

# --- hardened state handling ----------------------------------------------------
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

# --- run the actual installer against disposable host directories ---------------
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
echo 'PASS: grace window, pause, race stand-down, restart timeout, restart loop protection, counter and installer permissions'
