"""Read-only publication status of an authoring workbench project.

Local git facts come from ``neko_plugin_cli.repo_state``; the GitHub side only
uses public REST reads of the repository that ``origin`` points at. Nothing
here pushes, tags or notifies the Market.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict
from urllib.parse import quote

from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source
from plugin.neko_plugin_cli.repo_action_migration import migrate_github_actions
from plugin.neko_plugin_cli.repo_state import RepoState, read_repo_state
from plugin.neko_plugin_cli.templates.generator import PluginSpec
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from plugin.server.infrastructure import authoring_project_store as store
from plugin.utils.http_imports import ensure_httpx

_GITHUB_API = "https://api.github.com"
_CACHE_SECONDS = 60.0
# Unauthenticated GitHub reads are limited to 60 per hour, so answers are kept.
_cache: dict[tuple[str, str, str], tuple[float, dict[str, object]]] = {}


def _read_local(project_id: str) -> tuple[str, str, RepoState, dict[str, str]]:
    record = next((item for item in store.list_records() if item.project_id == project_id), None)
    if record is None:
        raise project_error(Code.PROJECT_NOT_FOUND, f"unknown project: {project_id}", 404)
    try:
        source = load_plugin_source(record.source_dir)
        changes = migrate_github_actions(
            PluginSpec(plugin_id=source.plugin_id), record.source_dir, dry_run=True
        )
    except (OSError, TypeError, KeyError, ValueError) as exc:
        raise project_error(Code.INVALID_PROJECT_DIRECTORY, str(exc), 400) from exc
    state = read_repo_state(record.source_dir, version=source.version)
    actions = {change.relative_path.as_posix(): change.status.value for change in changes}
    return source.plugin_id, source.version, state, actions


def _json_object(response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_state(run: object) -> str:
    if not isinstance(run, dict):
        return "not_run"
    if run.get("status") != "completed":
        return "running"
    return "success" if run.get("conclusion") == "success" else "failure"


async def _read_remote(
    repository: str, head: str | None, tag: str, plugin_id: str
) -> dict[str, object]:
    key = (repository.casefold(), head or "", tag)
    cached = _cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    httpx = await ensure_httpx()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "neko-plugin",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    result: dict[str, object] = {
        "rate_limited": False,
        "visibility": "unknown",
        "verify": "unknown",
        "release": {"state": "unknown", "missing_assets": []},
    }
    timeout = httpx.Timeout(env_seconds("NEKO_PLUGIN_PUBLISH_STATUS_TIMEOUT", 10.0))
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:

        async def get(path: str):
            try:
                response = await client.get(f"{_GITHUB_API}{path}")
            except httpx.HTTPError:
                return None
            limited = response.headers.get("x-ratelimit-remaining") == "0"
            if response.status_code == 429 or (response.status_code == 403 and limited):
                result["rate_limited"] = True
                return None
            return response

        response = await get(f"/repos/{repository}")
        if response is not None:
            if response.status_code == 404:
                result["visibility"] = "not_found"
            elif response.status_code == 200:
                private = _json_object(response).get("private")
                result["visibility"] = "private" if private else "public"

        if head:
            response = await get(
                f"/repos/{repository}/actions/workflows/verify.yml/runs?head_sha={head}&per_page=1"
            )
            if response is not None:
                if response.status_code == 404:
                    result["verify"] = "missing"
                elif response.status_code == 200:
                    runs = _json_object(response).get("workflow_runs")
                    result["verify"] = _run_state(runs[0] if isinstance(runs, list) and runs else None)

        response = await get(f"/repos/{repository}/releases/tags/{quote(tag, safe='')}")
        if response is not None:
            if response.status_code == 404:
                result["release"] = {"state": "missing", "missing_assets": []}
            elif response.status_code == 200:
                assets = _json_object(response).get("assets")
                ready = {
                    asset.get("name")
                    for asset in (assets if isinstance(assets, list) else [])
                    if isinstance(asset, dict)
                    and asset.get("state") == "uploaded"
                    and isinstance(asset.get("size"), int)
                    and asset["size"] > 0
                }
                # Same asset names ``neko-plugin publish`` waits for.
                expected = (
                    f"{plugin_id}.neko-plugin",
                    f"{plugin_id}.market-release-check.txt",
                    "market-evidence.json",
                )
                result["release"] = {
                    "state": "published",
                    "missing_assets": [name for name in expected if name not in ready],
                }

    if not result["rate_limited"]:
        _cache[key] = (time.monotonic(), result)
    return result


async def get_publish_status(project_id: str) -> dict[str, object]:
    """Describe where a project stands on the way from local source to the Market."""
    plugin_id, version, state, actions = await asyncio.to_thread(_read_local, project_id)
    remote = None
    if state.github_repository:
        remote = await _read_remote(state.github_repository, state.head, state.tag, plugin_id)
    return {
        "plugin_id": plugin_id,
        "version": version,
        "repository": asdict(state),
        "actions": actions,
        "remote": remote,
    }
