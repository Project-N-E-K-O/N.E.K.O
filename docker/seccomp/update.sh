#!/usr/bin/env bash
# chromium.json is Docker's default seccomp profile with one rule added at the
# end. This script writes it from that default, or checks that the copy in the
# repository is what it would write.
#
#   bash docker/seccomp/update.sh            # rewrite chromium.json
#   bash docker/seccomp/update.sh --check    # exit 1 when chromium.json is not what would be written
#
# To move to a newer default: change UPSTREAM_COMMIT and UPSTREAM_TAG below,
# run this without arguments, then run test-chromium-profile.sh.
set -euo pipefail

# github.com/moby/profiles (Apache-2.0), seccomp/default.json. The tag is what
# Docker 29.9 ships as its default; the commit is that tag, written out because
# a tag can be moved.
UPSTREAM_TAG='seccomp/v0.2.4'
UPSTREAM_COMMIT='245180c51918481c0525424b3ee025d2b435d46c'
UPSTREAM_URL="https://raw.githubusercontent.com/moby/profiles/${UPSTREAM_COMMIT}/seccomp/default.json"

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
target="$here/chromium.json"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

die() { echo "update.sh: $1" >&2; exit "${2:-1}"; }

check=0
case "${1:-}" in
    '') ;;
    --check) check=1 ;;
    *) die "unknown argument: $1" 2 ;;
esac

curl -fsSL --retry 3 --retry-delay 2 "$UPSTREAM_URL" -o "$work/default.json" \
    || die "the default profile could not be fetched from $UPSTREAM_URL"

# The rule goes in as the last element of the "syscalls" array, so the file
# has to end the way it is expected to: the array closing, then the object.
tab=$(printf '\t')
[[ "$(tail -n 3 "$work/default.json")" == "${tab}${tab}}"$'\n'"${tab}]"$'\n'"}" ]] \
    || die 'the default profile does not end with the "syscalls" array; this script has to be adapted'

{
    # Everything but the last three lines ("}" of the last rule, "]", "}").
    sed '$d' "$work/default.json" | sed '$d' | sed '$d'
    printf '\t\t},\n'
    printf '\t\t{\n'
    printf '\t\t\t"names": [\n'
    printf '\t\t\t\t"clone",\n'
    printf '\t\t\t\t"unshare"\n'
    printf '\t\t\t],\n'
    printf '\t\t\t"action": "SCMP_ACT_ALLOW",\n'
    printf '\t\t\t"comment": "N.E.K.O: a process without CAP_SYS_ADMIN may create namespaces, which the sandbox of Chromium does. The only difference from seccomp/default.json of github.com/moby/profiles at %s."\n' "$UPSTREAM_TAG"
    printf '\t\t}\n'
    printf '\t]\n'
    printf '}\n'
} > "$work/chromium.json"

if (( check )); then
    cmp -s "$work/chromium.json" "$target" \
        || die "chromium.json is not the default profile of $UPSTREAM_TAG plus the one rule; run this script without --check and look at the diff"
    echo "ok - chromium.json is the default profile of $UPSTREAM_TAG plus one rule"
else
    cp "$work/chromium.json" "$target"
    echo "wrote $target from $UPSTREAM_TAG"
fi
