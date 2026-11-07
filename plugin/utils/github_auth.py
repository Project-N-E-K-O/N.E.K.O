"""Local GitHub credential selection shared by publishing and status reads."""

from __future__ import annotations

import os
import subprocess


def gh_auth_token() -> str | None:
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "neko-plugin",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (
        os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or gh_auth_token()
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
