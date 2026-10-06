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
refuse() {
    fail "$1: refusing to take ownership of $2; it is a system, shared or top-level
  directory. Mount a dedicated directory such as $script_dir/$1 instead."
}

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
    # The mount root is handed to uid 1000 and mounted read-write, so it must be a
    # dedicated directory: never the filesystem root, a top-level directory, a
    # system tree, a user's home itself, or this checkout (or one of its parents).
    case "$resolved" in
        /etc|/etc/*|/proc|/proc/*|/sys|/sys/*|/dev|/dev/*|/boot|/boot/*|/run|/run/*|\
        /bin|/bin/*|/sbin|/sbin/*|/lib|/lib/*|/lib32|/lib32/*|/lib64|/lib64/*|/usr|/usr/*)
            refuse "$1" "$resolved" ;;
    esac
    case "$resolved" in
        /*/*/*) ;;
        /home/*|/var/*|/mnt/*|/media/*) refuse "$1" "$resolved" ;;
        /*/*) ;;
        *) refuse "$1" "$resolved" ;;
    esac
    case "$script_dir/" in
        "$resolved"/*) refuse "$1" "$resolved" ;;
    esac
    if [ -e "$2" ] && [ ! -d "$2" ]; then
        fail "$1: $2 exists but is not a directory"
    fi
    parent=$(dirname -- "$resolved")
    [ -d "$parent" ] || fail "$1: parent directory $parent does not exist; create it first"
    # Deny-lists cannot enumerate every sensitive tree (/var/lib/docker, ~/.ssh, ...),
    # so outside the two default paths only take over a directory that is plainly
    # ours: missing, empty, or already owned by the container user.
    if ! is_default "$1" "$resolved"; then
        case "$resolved" in
            */.*) fail "$1: refusing hidden path $resolved; use a dedicated, visible directory" ;;
        esac
        foreign_nonempty "$resolved" && refuse_foreign "$1" "$resolved"
    fi
    return 0
}

# The default paths may be taken over even when non-empty, unless they are a mount
# point: a bind-mounted docker/logs could be any shared host directory.
is_default() { [ "$2" = "$script_dir/$1" ] && ! is_mountpoint "$2"; }

is_mountpoint() {
    # Field 5 of mountinfo is the mount point, with space, tab, newline and
    # backslash written as octal escapes. Bind mounts on the same filesystem share
    # a device number, so stat cannot tell them apart. The path goes through the
    # environment because awk -v would interpret its backslashes.
    [ -d "$1" ] || return 1
    MOUNT_PATH=$1 awk '
        # Decode every \ooo escape; portable across awks, unlike gsub with "\\".
        function unescape(s,    out, i, d) {
            out = ""
            while ((i = index(s, "\\")) > 0) {
                d = substr(s, i + 1, 3)
                out = out substr(s, 1, i - 1) \
                    sprintf("%c", substr(d, 1, 1) * 64 + substr(d, 2, 1) * 8 + substr(d, 3, 1))
                s = substr(s, i + 4)
            }
            return out s
        }
        unescape($5) == ENVIRON["MOUNT_PATH"] { found = 1 }
        END { exit !found }' "${NEKO_PREFLIGHT_MOUNTINFO:-/proc/self/mountinfo}"
}

# True when $1 is an existing, non-empty directory not owned by the container user.
foreign_nonempty() {
    [ -d "$1" ] && [ "$(stat -c '%u:%g' -- "$1")" != "$NEKO_UID:$NEKO_GID" ] \
        && [ -n "$(ls -A -- "$1")" ]
}

refuse_foreign() {
    fail "$1: $2 already holds data owned by $(stat -c '%u:%g' -- "$2").
  Only the default docker/neko-home and docker/logs are taken over when non-empty,
  and only when they are not a mount point.
  If this directory really is dedicated to N.E.K.O, change its owner yourself:
    sudo chown -h $NEKO_UID:$NEKO_GID -- '$2'"
}

fix_dir() {
    # $1: label, $2: normalized absolute path. Runs in a subshell (it changes cwd).
    parent=$(dirname -- "$2")
    name=$(basename -- "$2")
    # Pin the parent: once inside it, a parent swapped for a symlink cannot redirect
    # the privileged calls below, which only use ./name and never follow it.
    cd -P -- "$parent" || fail "$1: cannot enter $parent"
    [ "$(pwd -P)" = "$parent" ] || fail "$1: $parent changed during the check"
    if [ ! -e "./$name" ] && [ ! -L "./$name" ]; then
        mkdir -- "./$name" || fail "$1: cannot create $2"
        echo "preflight: $1: created $2"
    fi
    [ ! -L "./$name" ] && [ -d "./$name" ] || fail "$1: $2 changed during the check"
    owner=$(stat -c '%u:%g' -- "./$name") || fail "$1: cannot stat $2"
    if [ "$owner" = "$NEKO_UID:$NEKO_GID" ]; then
        echo "preflight: $1: $2 ok ($owner)"
        return 0
    fi
    if ! is_default "$1" "$2" && foreign_nonempty "./$name"; then
        refuse_foreign "$1" "$2"
    fi
    # -h: never follow a link. Only the directory itself, never its contents.
    chown -h "$NEKO_UID:$NEKO_GID" -- "./$name" \
        || fail "$1: cannot chown $2 to $NEKO_UID:$NEKO_GID (run with sudo)"
    echo "preflight: $1: $2 owner $owner -> $NEKO_UID:$NEKO_GID"
}

# Normalize first (absolute, no trailing "/" or "/." that would make chown -h
# dereference the last component), then validate both before touching either.
home_dir=$(realpath -m -s -- "$home_dir") && logs_dir=$(realpath -m -s -- "$logs_dir") \
    || fail "cannot resolve the paths (GNU realpath is required)"
check_dir neko-home "$home_dir"
check_dir logs "$logs_dir"
(fix_dir neko-home "$home_dir")
(fix_dir logs "$logs_dir")
echo "preflight: done"
