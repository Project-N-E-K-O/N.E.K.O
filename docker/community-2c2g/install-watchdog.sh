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
script=
cron=
trap 'rm -f "$script" "$cron"' EXIT
trap 'exit 1' HUP INT TERM
script=$(mktemp /host-opt/neko/.watchdog.XXXXXX)
cron=$(mktemp /host-cron.d/.neko-watchdog.XXXXXX)
cp /source/watchdog.sh "$script"
chown 0:0 "$script"
chmod 700 "$script"
mv -f "$script" /host-opt/neko/watchdog.sh
printf '%s\n' 'SHELL=/bin/bash' 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
    '*/5 * * * * root /opt/neko/watchdog.sh' > "$cron"
chown 0:0 "$cron"
chmod 644 "$cron"
mv -f "$cron" /host-cron.d/neko-watchdog
echo "Watchdog installed. /opt/neko/disabled pauses recovery; installation does not remove it."
