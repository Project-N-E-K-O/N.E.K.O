#!/bin/sh
# Executed by the one-shot installer, never by the application container.
set -eu
umask 077
fail() { echo "watchdog install: $*" >&2; exit 1; }

for directory in /host-opt /host-cron.d; do
    [ ! -L "$directory" ] && [ -d "$directory" ] || fail "unsafe $directory"
    case "$(stat -c '%u:%g:%a' "$directory")" in
        0:0:755|0:0:750|0:0:700) ;;
        *) fail "$directory must be root-owned and not writable by others" ;;
    esac
done
[ ! -L /host-opt/neko ] || fail "symlink at /opt/neko"
if [ -e /host-opt/neko ]; then
    [ -d /host-opt/neko ] || fail "/opt/neko is not a directory"
    case "$(stat -c '%u:%g:%a' /host-opt/neko)" in
        0:0:755|0:0:750|0:0:700) ;;
        *) fail "/opt/neko must be root-owned and not writable by others" ;;
    esac
fi
mkdir -p /host-opt/neko
chmod 700 /host-opt/neko
[ ! -L /host-cron.d/neko-watchdog ] || fail "symlink at cron destination"
[ ! -L /host-opt/neko/watchdog.sh ] || fail "symlink at watchdog destination"
if [ -e /host-opt/neko/watchdog.sh ]; then
    [ -f /host-opt/neko/watchdog.sh ] || fail "watchdog destination is not a regular file"
fi
# Preserve only the documented setting, never arbitrary cron commands.
grace=
if [ -e /host-cron.d/neko-watchdog ]; then
    [ -f /host-cron.d/neko-watchdog ] || fail "cron destination is not a regular file"
    [ "$(stat -c '%u:%g:%a' /host-cron.d/neko-watchdog)" = 0:0:644 ] || fail "unsafe existing cron permissions"
    assignment_pattern='^[[:blank:]]*NEKO_WATCHDOG_STARTUP_GRACE_SECONDS[[:blank:]]*=[[:blank:]]*'
    assignment_count=$(grep -c "$assignment_pattern" /host-cron.d/neko-watchdog) || {
        status=$?
        [ "$status" -eq 1 ] || fail "cannot read existing cron settings"
    }
    [ "$assignment_count" -le 1 ] || fail "duplicate startup grace settings"
    grace=$(sed -n "/$assignment_pattern/{s/$assignment_pattern//;s/[[:blank:]]*$//;p;}" /host-cron.d/neko-watchdog) || fail "cannot read existing startup grace"
    # Strip one matching cron quote pair; never evaluate shell expressions.
    grace=$(printf '%s\n' "$grace" | sed -e 's/^"\(.*\)"$/\1/' -e 't' -e "s/^'\(.*\)'$/\1/")
    case "$grace" in
        '') ;;
        *[!0-9]*) fail "invalid existing startup grace" ;;
        *) [ "${#grace}" -le 6 ] || fail "invalid existing startup grace" ;;
    esac
fi
script=
cron=
trap 'rm -f "$script" "$cron"' EXIT
trap 'exit 1' HUP INT TERM
script=$(mktemp /host-opt/neko/.watchdog.XXXXXX)
cron=$(mktemp /host-cron.d/.neko-watchdog.XXXXXX)
cp /source/watchdog.sh "$script"
chown 0:0 "$script"
chmod 700 "$script"
printf '%s\n' 'SHELL=/bin/bash' 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
    '*/5 * * * * root /opt/neko/watchdog.sh' > "$cron"
[ -z "$grace" ] || sed -i "3iNEKO_WATCHDOG_STARTUP_GRACE_SECONDS=$grace" "$cron"
chown 0:0 "$cron"
chmod 644 "$cron"
mv -f "$script" /host-opt/neko/watchdog.sh
mv -f "$cron" /host-cron.d/neko-watchdog
if [ -e /host-opt/neko/disabled ]; then
    echo "Watchdog installed, but recovery remains PAUSED. After maintenance, manually remove /opt/neko/disabled to resume."
else
    echo "Watchdog installed. /opt/neko/disabled pauses recovery; installation does not remove it."
fi
