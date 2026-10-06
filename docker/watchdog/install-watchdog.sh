#!/bin/sh
# Installs the host watchdog. Never run by the application container.
#   On the host (no helper image):  sudo sh docker/watchdog/install-watchdog.sh --host
#   In a one-off container: mount /etc/cron.d at /host-cron.d, /opt at /host-opt and
#   this directory at /source, then run it without arguments.
set -eu
umask 077
fail() { echo "watchdog install: $*" >&2; exit 1; }

opt_dir=/host-opt
cron_dir=/host-cron.d
source_script=/source/watchdog.sh
case "${1:-}" in
    '') ;;
    --host)
        opt_dir=/opt
        cron_dir=/etc/cron.d
        source_script=$(cd -- "$(dirname -- "$0")" && pwd)/watchdog.sh ;;
    *) fail "unknown argument: $1" ;;
esac
[ "$(id -u)" = 0 ] || fail "must run as root"
[ ! -L "$source_script" ] && [ -f "$source_script" ] || fail "missing $source_script"

for directory in "$opt_dir" "$cron_dir"; do
    [ ! -L "$directory" ] && [ -d "$directory" ] || fail "unsafe $directory"
    case "$(stat -c '%u:%g:%a' "$directory")" in
        0:0:755|0:0:750|0:0:700) ;;
        *) fail "$directory must be root-owned and not writable by others" ;;
    esac
done
[ ! -L "$opt_dir/neko" ] || fail "symlink at /opt/neko"
if [ -e "$opt_dir/neko" ]; then
    [ -d "$opt_dir/neko" ] || fail "/opt/neko is not a directory"
    case "$(stat -c '%u:%g:%a' "$opt_dir/neko")" in
        0:0:755|0:0:750|0:0:700) ;;
        *) fail "/opt/neko must be root-owned and not writable by others" ;;
    esac
fi
mkdir -p "$opt_dir/neko"
[ ! -L "$cron_dir/neko-watchdog" ] || fail "symlink at cron destination"
[ ! -L "$opt_dir/neko/watchdog.sh" ] || fail "symlink at watchdog destination"
if [ -e "$opt_dir/neko/watchdog.sh" ]; then
    [ -f "$opt_dir/neko/watchdog.sh" ] || fail "watchdog destination is not a regular file"
fi
# Preserve only the documented setting, never arbitrary cron commands.
grace=
if [ -e "$cron_dir/neko-watchdog" ]; then
    [ -f "$cron_dir/neko-watchdog" ] || fail "cron destination is not a regular file"
    [ "$(stat -c '%u:%g:%a' "$cron_dir/neko-watchdog")" = 0:0:644 ] || fail "unsafe existing cron permissions"
    assignment_pattern='^[[:blank:]]*NEKO_WATCHDOG_STARTUP_GRACE_SECONDS[[:blank:]]*=[[:blank:]]*'
    assignment_count=$(grep -c "$assignment_pattern" "$cron_dir/neko-watchdog") || {
        status=$?
        [ "$status" -eq 1 ] || fail "cannot read existing cron settings"
    }
    [ "$assignment_count" -le 1 ] || fail "duplicate startup grace settings"
    grace=$(sed -n "/$assignment_pattern/{s/$assignment_pattern//;s/[[:blank:]]*$//;p;}" "$cron_dir/neko-watchdog") || fail "cannot read existing startup grace"
    # Strip one matching cron quote pair; never evaluate shell expressions.
    grace=$(printf '%s\n' "$grace" | sed -e 's/^"\(.*\)"$/\1/' -e 't' -e "s/^'\(.*\)'$/\1/")
    case "$grace" in
        '') ;;
        *[!0-9]*) fail "invalid existing startup grace" ;;
        *) [ "${#grace}" -le 6 ] || fail "invalid existing startup grace" ;;
    esac
fi
chmod 700 "$opt_dir/neko"
script=
cron=
trap 'rm -f "$script" "$cron"' EXIT
trap 'exit 1' HUP INT TERM
script=$(mktemp "$opt_dir/neko/.watchdog.XXXXXX")
cron=$(mktemp "$cron_dir/.neko-watchdog.XXXXXX")
cp "$source_script" "$script"
chown 0:0 "$script"
chmod 700 "$script"
printf '%s\n' 'SHELL=/bin/bash' 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
    '*/5 * * * * root /opt/neko/watchdog.sh' > "$cron"
[ -z "$grace" ] || sed -i "3iNEKO_WATCHDOG_STARTUP_GRACE_SECONDS=$grace" "$cron"
chown 0:0 "$cron"
chmod 644 "$cron"
mv -f "$script" "$opt_dir/neko/watchdog.sh"
mv -f "$cron" "$cron_dir/neko-watchdog"
if [ -e "$opt_dir/neko/disabled" ]; then
    echo "Watchdog installed, but recovery remains PAUSED. After maintenance, manually remove /opt/neko/disabled to resume."
else
    echo "Watchdog installed. /opt/neko/disabled pauses recovery; installation does not remove it."
fi
