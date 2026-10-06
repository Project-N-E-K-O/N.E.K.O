#!/bin/sh
# Host-side preflight for the bind-mounted data directories. Optional, but run it
# (as root) before the first `docker compose up`, after migrating data, and whenever
# a mount directory was created by Docker as root.
#
#   sudo sh docker/preflight.sh [NEKO_HOME_DIR [LOGS_DIR]]
#
# Defaults: neko-home/ and logs/ next to this script, i.e. the sources used by
# docker-compose.yml. When an override file mounts other directories, pass the
# paths exactly as written there (not what `docker inspect` reports, which has
# already followed any symlink). Relative paths resolve against docker/, as in
# Compose, wherever this script is run from.
#
# Docker resolves a host symlink before mounting it, so the container cannot tell
# that /home/neko or /app/logs is really a shared host directory. The entrypoint
# therefore only chowns /app/logs while it is empty. This script runs where the
# symlink is still visible: it rejects mount sources that are, or go through, a
# symlink at any path component, creates missing directories, and sets the owner
# of each mount root (never recursively) to the container user. Data inside neko-home is aligned by the entrypoint on every start.
set -eu

NEKO_UID=1000
NEKO_GID=1000

fail() { echo "preflight: $*" >&2; exit 1; }

case "${1:-}" in
    -h|--help) sed -n '2,12s/^# \{0,1\}//p' "$0"; exit 0 ;;
esac
[ "$#" -le 2 ] || fail "too many arguments (see --help)"

script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)
home_dir=${1:-$script_dir/neko-home}
logs_dir=${2:-$script_dir/logs}
# Compose resolves relative bind sources against the project directory, which is
# docker/ (where docker-compose.yml lives), not against the caller's cwd.
case "$home_dir" in /*) ;; *) home_dir=$script_dir/$home_dir ;; esac
case "$logs_dir" in /*) ;; *) logs_dir=$script_dir/$logs_dir ;; esac

check_dir() {
    # $1: label, $2: path
    [ -n "$2" ] || fail "$1: empty path"
    if [ -L "$2" ]; then
        fail "$1: $2 is a symlink (-> $(readlink -- "$2")).
  Docker would mount its target and the container would take ownership of it.
  Write the real directory into an override file (e.g. compose.local.yaml)
  instead of linking, after confirming it is dedicated to N.E.K.O."
    fi
    # A link in any parent component is followed just the same, e.g. /srv/link/logs
    # with link -> /var lands on /var/logs. Compare the path with links resolved
    # against the same path with them kept; any difference means a link.
    resolved=$(realpath -m -- "$2") && literal=$(realpath -m -s -- "$2") \
        || fail "$1: cannot resolve $2 (GNU realpath is required)"
    if [ "$resolved" != "$literal" ]; then
        fail "$1: $2 goes through a symlink (resolves to $resolved).
  Confirm that directory is dedicated to N.E.K.O, then mount and pass that
  real path instead."
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
# Act only on the normalized form: no trailing "/" or "/." that would make
# chown -h dereference the last component.
home_dir=$(realpath -m -s -- "$home_dir")
logs_dir=$(realpath -m -s -- "$logs_dir")
fix_dir neko-home "$home_dir"
fix_dir logs "$logs_dir"
echo "preflight: done"
