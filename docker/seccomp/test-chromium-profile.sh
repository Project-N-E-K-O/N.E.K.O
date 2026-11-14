#!/usr/bin/env bash
# Tries docker/seccomp/chromium.json on a real Docker daemon: what it is there
# to allow is allowed, what it leaves refused is refused, Compose picks it up
# the way docker/README_Docker.md says, and Debian's Chromium starts under it
# as the `neko` user with its sandbox on.
#
#   bash docker/seccomp/test-chromium-profile.sh            # everything
#   bash docker/seccomp/test-chromium-profile.sh --quick    # without Chromium (no image is built)
#
# Needs Docker with Compose v2 on a Linux host, and network access to pull
# debian:bookworm-slim (and, without --quick, to install Chromium into it).
# Publishes no port, touches no container or directory of a deployment, and
# removes what it made.
#
# NEKO_SECCOMP_REQUIRE_CONTROL=1 (set in CI) makes it an error when Docker
# without the profile already allows what the profile is there to allow: the
# checks below could then not tell whether the profile does anything.
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
docker_dir=$(dirname -- "$here")
profile="$here/chromium.json"
base_image='debian:bookworm-slim'
chromium_image="neko-seccomp-test:chromium-$$"
project="neko-seccomp-test-$$"
marker='neko-seccomp-ok'

quick=0
case "${1:-}" in
    '') ;;
    --quick) quick=1 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
esac

work=$(mktemp -d)
cleanup() {
    if [[ -f $work/compose/docker-compose.yml ]]; then
        (cd "$work/compose" && docker compose -p "$project" down --remove-orphans > /dev/null 2>&1) || true
    fi
    docker image rm -f "$chromium_image" > /dev/null 2>&1 || true
    rm -rf -- "$work" 2> /dev/null || true
}
trap cleanup EXIT

pass=0
ok() { pass=$((pass + 1)); echo "ok - $1"; }
die() {
    echo "FAIL - $1" >&2
    if [[ -s $work/out ]]; then
        echo '--- output of the last command ---' >&2
        tail -n 40 "$work/out" >&2
    fi
    exit 1
}

# A container of the base image as an unprivileged user; "$1" is "default" or
# "profile", the rest is the command.
in_container() {
    local with=$1
    shift
    local options=(--rm --user 1000:1000)
    [[ $with == profile ]] && options+=(--security-opt "seccomp=$profile")
    docker run "${options[@]}" "$base_image" "$@" > "$work/out" 2>&1
}

docker pull -q "$base_image" > /dev/null || die "$base_image could not be pulled"

# --- What the profile is there for --------------------------------------------

control=1
if in_container default unshare --user true; then
    control=0
    if [[ ${NEKO_SECCOMP_REQUIRE_CONTROL:-} == 1 ]]; then
        die 'Docker allows a user namespace without the profile; these checks cannot tell what the profile does'
    fi
    echo 'note - Docker allows a user namespace here without the profile; the profile is not needed on this host'
else
    ok "without the profile, a user namespace is refused (Docker's default)"
fi

in_container profile unshare --user true \
    || die 'with the profile, an unprivileged user cannot create a user namespace'
ok 'with the profile, an unprivileged user creates a user namespace'

# The process is root inside the namespaces it made, and the kernel lets such a
# process name its own UTS namespace. The profile does not: everything other
# than making namespaces is left as Docker's default has it.
in_container profile unshare --user --map-root-user --uts true \
    || die 'with the profile, user and UTS namespaces cannot be created together'
if in_container profile unshare --user --map-root-user --uts hostname neko-seccomp-test; then
    die 'with the profile, sethostname is allowed; it must stay refused as in the default'
fi
ok 'with the profile, what the default refuses besides namespaces stays refused (sethostname)'

# --- Through Compose, as the README says --------------------------------------

mkdir -p "$work/compose/seccomp"
cp "$docker_dir/docker-compose.yml" "$docker_dir/compose.browser.yaml" "$work/compose/"
cp "$profile" "$work/compose/seccomp/"
compose_env() {
    {
        echo "NEKO_IMAGE=$base_image"
        echo "NEKO_CONTAINER_NAME=$project"
        [[ $1 == browser ]] && echo 'COMPOSE_FILE=docker-compose.yml:compose.browser.yaml'
    } > "$work/compose/.env"
    return 0
}
through_compose() {
    (cd "$work/compose" && docker compose -p "$project" run --rm --no-deps -T --user 1000:1000 neko-main "$@") \
        > "$work/out" 2>&1
}

compose_env plain
if through_compose unshare --user true; then
    (( control == 0 )) || die 'the Compose file alone already allows a user namespace'
else
    ok 'Compose without the browser file: a user namespace is refused'
fi

compose_env browser
through_compose unshare --user true \
    || die 'COMPOSE_FILE in .env naming compose.browser.yaml does not put the profile on the container'
ok 'Compose with the browser file named in .env: the profile is on the container'

# --- Chromium itself ----------------------------------------------------------

if (( quick )); then
    echo "skipped - Chromium (--quick)"
    echo "all $pass checks passed"
    exit 0
fi

# The packages docker/Dockerfile.full installs for the browser tools, and the
# user the entrypoint runs the services as.
docker build -q -t "$chromium_image" - > "$work/out" 2>&1 << 'EOF' || die 'the image with Chromium could not be built'
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium chromium-sandbox \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home neko
EOF

# Started the way the entrypoint starts the services (root, then `runuser -u
# neko`), with no --no-sandbox: Chromium refuses to start when it cannot set
# its sandbox up, so a page that comes back was rendered inside it. The page
# sets its title from a script, so the title that is looked for is in the
# output only when a renderer ran it (it is not in the command line).
rendered="<title>$marker-42</title>"
chromium() {
    local with=$1
    local options=(--rm)
    [[ $with == profile ]] && options+=(--security-opt "seccomp=$profile")
    docker run "${options[@]}" "$chromium_image" \
        runuser -u neko -- timeout 120 chromium --headless=new --no-first-run --user-data-dir=/tmp/profile \
        --dump-dom "data:text/html,<script>document.title='$marker-'+(6*7)</script>" > "$work/out" 2>&1
}

if chromium default && grep -qF "$rendered" "$work/out"; then
    (( control == 0 )) || die 'Chromium starts without the profile; this check cannot tell what the profile does'
else
    ok "without the profile, Chromium does not start (Docker's default)"
fi

chromium profile || die 'with the profile, Chromium does not start'
grep -qF "$rendered" "$work/out" || die 'with the profile, Chromium started but did not render the page'
ok 'with the profile, Chromium starts as the neko user with its sandbox and renders a page'

echo "all $pass checks passed"
