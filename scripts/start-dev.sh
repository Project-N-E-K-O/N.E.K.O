#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}/.."

export NEKO_SERVERS_DESKTOP_CLIENT_ID="neko-servers-desktop-dev"
exec python3 launcher.py "$@"
