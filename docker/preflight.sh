#!/bin/sh
# Host-side preflight for the bind-mounted data directories. Optional, but run it
# (as root) before the first `docker compose up`, after migrating data, and whenever
# a mount directory was created by Docker as root.
#
#   sudo sh docker/preflight.sh [NEKO_HOME_DIR [LOGS_DIR]]
#
# Defaults: neko-home/ and logs/ next to this script, i.e. the sources used by
# docker-compose.yml. Pass the real paths when an override file mounts others.
#
# Docker resolves a host symlink before mounting it, so the container cannot tell
# that /home/neko or /app/logs is really a shared host directory. The entrypoint
# therefore only chowns /app/logs while it is empty. This script runs where the
# symlink is still visible: it rejects symlinked mount sources, creates missing
# directories, and sets the owner of each mount root (never recursively) to the
# container user. Data inside neko-home is aligned by the entrypoint on every start.
set -eu

NEKO_UID=1000
NEKO_GID=1000

fail() { echo "preflight: $*" >&2; exit 1; }

case "${1:-}" in
    -h|--help) sed -n '2,9s/^# \{0,1\}//p' "$0"; exit 0 ;;
esac
[ "$#" -le 2 ] || fail "too many arguments (see --help)"

script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
home_dir=${1:-$script_dir/neko-home}
logs_dir=${2:-$script_dir/logs}

check_dir() {
    # $1: label, $2: path
    [ -n "$2" ] || fail "$1: empty path"
    if [ -L "$2" ]; then
        fail "$1: $2 is a symlink (-> $(readlink -- "$2")).
  Docker would mount its target and the container would take ownership of it.
  Write the real directory into an override file (e.g. compose.local.yaml)
  instead of linking, after confirming it is dedicated to N.E.K.O."
    fi
    if [ -e "$2" ] && [ ! -d "$2" ]; then
        fail "$1: $2 exists but is not a directory"
    fi
}

fix_dir() {
    # $1: label, $2: path
    if [ ! -d "$2" ]; then
        mkdir -p -- "$2" || fail "$1: cannot create $2"
        echo "preflight: $1: created $2"
    fi
    # Re-check after mkdir: a symlink could have appeared in between.
    [ ! -L "$2" ] || fail "$1: $2 became a symlink"
    owner=$(stat -c '%u:%g' -- "$2") || fail "$1: cannot stat $2"
    if [ "$owner" = "$NEKO_UID:$NEKO_GID" ]; then
        echo "preflight: $1: $2 ok ($owner)"
        return
    fi
    # -h: never follow a link. Only the directory itself, never its contents.
    chown -h "$NEKO_UID:$NEKO_GID" -- "$2" \
        || fail "$1: cannot chown $2 to $NEKO_UID:$NEKO_GID (run with sudo)"
    echo "preflight: $1: $2 owner $owner -> $NEKO_UID:$NEKO_GID"
}

# Validate both before touching either, so a bad second path changes nothing.
check_dir neko-home "$home_dir"
check_dir logs "$logs_dir"
fix_dir neko-home "$home_dir"
fix_dir logs "$logs_dir"
echo "preflight: done"
