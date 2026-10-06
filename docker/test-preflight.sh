#!/bin/bash
# Isolated regression test for preflight.sh. Run as root for real ownership checks;
# everything happens under a temporary directory.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run this isolated test as root' >&2; exit 1; }
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(mktemp -d)
trap 'rm -rf -- "$ROOT"' EXIT
sh -n "$SOURCE/preflight.sh"

pass=0
ok() { pass=$((pass + 1)); echo "ok - $1"; }
die() { echo "FAIL - $1" >&2; exit 1; }
owner() { stat -c '%u:%g' -- "$1"; }

# Each case gets a fresh copy so the script's default paths land in the sandbox.
new_case() {
    CASE=$(mktemp -d "$ROOT/case.XXXXXX")
    cp "$SOURCE/preflight.sh" "$CASE/preflight.sh"
}
run() { sh "$CASE/preflight.sh" "$@" > "$CASE/out" 2>&1; }

new_case
run || die 'fresh deployment'
[[ -d $CASE/neko-home && -d $CASE/logs ]] || die 'directories not created'
[[ $(owner "$CASE/neko-home") == 1000:1000 && $(owner "$CASE/logs") == 1000:1000 ]] \
    || die 'fresh directories not owned by 1000'
ok 'creates missing mount sources owned by 1000'

new_case
mkdir "$CASE/neko-home" "$CASE/logs"
echo old > "$CASE/logs/root.log"
chown 0:0 "$CASE/neko-home" "$CASE/logs" "$CASE/logs/root.log"
run || die 'root-owned non-empty logs'
[[ $(owner "$CASE/logs") == 1000:1000 ]] || die 'non-empty logs root not fixed'
[[ $(owner "$CASE/neko-home") == 1000:1000 ]] || die 'neko-home root not fixed'
[[ $(owner "$CASE/logs/root.log") == 0:0 ]] || die 'contents were changed'
ok 'fixes non-empty mount roots without recursing'

new_case
mkdir "$CASE/shared" "$CASE/logs"
chown 0:0 "$CASE/shared" "$CASE/logs"
ln -s shared "$CASE/neko-home"
if run; then die 'symlinked neko-home accepted'; fi
grep -q 'is a symlink' "$CASE/out" || die 'symlink error not reported'
[[ $(owner "$CASE/shared") == 0:0 ]] || die 'symlink target was changed'
[[ $(owner "$CASE/logs") == 0:0 ]] || die 'logs changed although validation failed'
ok 'rejects a symlinked neko-home and changes nothing'

new_case
mkdir "$CASE/shared"
chown 0:0 "$CASE/shared"
ln -s shared "$CASE/logs"
if run; then die 'symlinked logs accepted'; fi
[[ $(owner "$CASE/shared") == 0:0 ]] || die 'logs symlink target was changed'
[[ ! -e $CASE/neko-home ]] || die 'neko-home created although validation failed'
ok 'rejects a symlinked logs directory before creating anything'

new_case
mkdir -p "$CASE/shared/home" "$CASE/shared/logs"
chown 0:0 "$CASE/shared/home" "$CASE/shared/logs"
ln -s shared "$CASE/link"
if run "$CASE/link/home" "$CASE/link/logs"; then die 'link in a parent component accepted'; fi
grep -q 'goes through a symlink' "$CASE/out" || die 'parent link error not reported'
[[ $(owner "$CASE/shared/home") == 0:0 && $(owner "$CASE/shared/logs") == 0:0 ]] \
    || die 'parent link target was changed'
ok 'rejects a symlink in a parent path component'

for suffix in / /. //; do
    new_case
    mkdir "$CASE/shared" "$CASE/logs"
    chown 0:0 "$CASE/shared"
    ln -s shared "$CASE/neko-home"
    if run "$CASE/neko-home$suffix" "$CASE/logs"; then die "symlink with '$suffix' accepted"; fi
    [[ $(owner "$CASE/shared") == 0:0 ]] || die "symlink target with '$suffix' was changed"
done
ok 'rejects a symlink named with a trailing / or /.'

new_case
mkdir -p "$CASE/home" "$CASE/logs"
run "$CASE/home/" "$CASE/logs/." || die 'trailing separators on real directories'
[[ $(owner "$CASE/home") == 1000:1000 && $(owner "$CASE/logs") == 1000:1000 ]] \
    || die 'trailing separators not normalized'
ok 'normalizes trailing separators on real directories'

new_case
mkdir "$CASE/real"
mv "$CASE/preflight.sh" "$CASE/real/preflight.sh"
ln -s real "$CASE/via"
sh "$CASE/via/preflight.sh" > "$CASE/out" 2>&1 || die 'script reached through a linked directory'
[[ -d $CASE/real/neko-home && -d $CASE/real/logs ]] || die 'defaults not resolved physically'
ok 'resolves default paths physically when invoked through a link'

new_case
ln -s missing "$CASE/logs"
if run; then die 'dangling symlink accepted'; fi
[[ ! -e $CASE/missing ]] || die 'dangling symlink target created'
ok 'rejects a dangling symlink'

new_case
touch "$CASE/neko-home"
if run; then die 'regular file accepted'; fi
grep -q 'not a directory' "$CASE/out" || die 'file error not reported'
ok 'rejects a non-directory mount source'

new_case
mkdir -p "$CASE/elsewhere/home" "$CASE/elsewhere/logs"
run "$CASE/elsewhere/home" "$CASE/elsewhere/logs" || die 'explicit paths'
[[ $(owner "$CASE/elsewhere/home") == 1000:1000 && $(owner "$CASE/elsewhere/logs") == 1000:1000 ]] \
    || die 'explicit paths not fixed'
[[ ! -e $CASE/neko-home && ! -e $CASE/logs ]] || die 'default paths touched with explicit arguments'
ok 'uses explicit paths for overridden mounts'

new_case
mkdir -p "$CASE/elsewhere" "$CASE/data"
(cd "$CASE/elsewhere" && sh "$CASE/preflight.sh" ./data/home data/logs > "$CASE/out" 2>&1) \
    || die 'relative paths'
[[ $(owner "$CASE/data/home") == 1000:1000 && $(owner "$CASE/data/logs") == 1000:1000 ]] \
    || die 'relative paths not resolved against the script directory'
[[ ! -e $CASE/elsewhere/data ]] || die 'relative paths resolved against the caller cwd'
ok 'resolves relative paths against docker/ like Compose'

# Outside the defaults, a non-empty directory owned by someone else is never taken
# over (think /var/lib/docker or ~/.ssh); an empty or already-aligned one is.
new_case
mkdir -p "$CASE/srv/state" "$CASE/srv/logs" "$CASE/srv/mine"
echo secret > "$CASE/srv/state/file"
chown 0:0 "$CASE/srv/state" "$CASE/srv/state/file"
if run "$CASE/srv/state" "$CASE/srv/logs"; then die 'foreign non-empty custom directory accepted'; fi
grep -q 'already holds data' "$CASE/out" || die 'foreign data error not reported'
[[ $(owner "$CASE/srv/state") == 0:0 && $(owner "$CASE/srv/logs") == 0:0 ]] \
    || die 'changed something although validation failed'
touch "$CASE/srv/mine/x"
chown 1000:1000 "$CASE/srv/mine"
run "$CASE/srv/mine" "$CASE/srv/logs" || die 'aligned non-empty or empty custom directories'
[[ $(owner "$CASE/srv/logs") == 1000:1000 ]] || die 'empty custom directory not fixed'
ok 'takes over custom directories only when empty or already aligned'

# Race: swap the validated parent for a symlink right before the privileged step.
# The stubbed basename runs inside fix_dir, after check_dir has passed.
new_case
mkdir -p "$CASE/p" "$CASE/victim/home" "$CASE/stub" "$CASE/logs"
chown 0:0 "$CASE/victim" "$CASE/victim/home"
cat > "$CASE/stub/basename" <<EOF
#!/bin/sh
if [ -d "$CASE/p" ] && [ ! -L "$CASE/p" ]; then
    mv "$CASE/p" "$CASE/p.orig" && ln -s victim "$CASE/p"
fi
exec /usr/bin/basename "\$@"
EOF
chmod 700 "$CASE/stub/basename"
if PATH="$CASE/stub:$PATH" run "$CASE/p/home" "$CASE/logs"; then die 'parent swap not detected'; fi
grep -q 'changed during the check' "$CASE/out" || die 'parent swap error not reported'
[[ $(owner "$CASE/victim/home") == 0:0 ]] || die 'swapped-in target was changed'
ok 'detects a parent swapped for a symlink after validation'

new_case
mkdir -p "$CASE/home/.ssh" "$CASE/logs"
if run "$CASE/home/.ssh" "$CASE/logs"; then die 'hidden directory accepted'; fi
grep -q 'hidden path' "$CASE/out" || die 'hidden path error not reported'
if run "$CASE/missing/home" "$CASE/logs"; then die 'missing parent accepted'; fi
grep -q 'does not exist' "$CASE/out" || die 'missing parent error not reported'
[[ ! -e $CASE/missing ]] || die 'missing parent was created'
ok 'refuses hidden paths and missing parents'

new_case
mkdir "$CASE/neko-home" "$CASE/logs"
chown 1000:1000 "$CASE/neko-home" "$CASE/logs"
run || die 'already aligned'
grep -q 'neko-home: .* ok (1000:1000)' "$CASE/out" || die 'aligned directory not reported ok'
ok 'leaves aligned directories alone'

# Paths that must never be handed to uid 1000. chown and mkdir are stubbed so a
# regression records the call instead of touching the real system.
new_case
mkdir "$CASE/stub" "$CASE/logs"
for tool in chown mkdir; do
    printf '#!/bin/sh\necho "%s $*" >> "%s/called"\nexit 1\n' "$tool" "$CASE" > "$CASE/stub/$tool"
    chmod 700 "$CASE/stub/$tool"
done
for bad in / /. // /data /root /home /home/someone /var /var/log /mnt/disk /etc/neko \
           /usr/local/neko /proc/neko "$CASE" "$CASE/.." "$(dirname -- "$CASE")"; do
    if PATH="$CASE/stub:$PATH" run "$bad" "$CASE/logs"; then die "accepted $bad"; fi
    grep -q 'refusing to take ownership' "$CASE/out" || die "no refusal for $bad"
    if PATH="$CASE/stub:$PATH" run "$CASE/logs" "$bad"; then die "accepted logs $bad"; fi
done
[[ ! -e $CASE/called ]] || die "chown/mkdir reached: $(cat "$CASE/called")"
ok 'refuses the filesystem root, system and top-level directories, and the checkout'

new_case
if run a b c; then die 'extra arguments accepted'; fi
run --help || die '--help failed'
grep -q 'sudo sh docker/preflight.sh' "$CASE/out" || die '--help shows no usage'
ok 'argument handling'

echo "all $pass preflight checks passed"
