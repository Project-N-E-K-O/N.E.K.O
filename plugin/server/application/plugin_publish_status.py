"""Read-only publication status of an authoring workbench project.

Local git facts come from ``neko_plugin_cli.repo_state``; the GitHub side only
uses public REST reads of the repository that ``origin`` points at. Nothing
here pushes, tags or notifies the Market.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple, TypedDict
from urllib.parse import quote

from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source
from plugin.neko_plugin_cli.repo_state import RepoState, read_repo_state, read_workspace_log
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from plugin.server.infrastructure import authoring_project_store as store
from plugin.utils.github_auth import github_headers
from plugin.utils.github_release import (
    WorkflowState,
    missing_release_assets,
    release_ready,
    select_release_run,
    workflow_run_state as _run_state,
)
from plugin.utils.http_imports import ensure_httpx

if TYPE_CHECKING:
    from plugin.neko_plugin_cli.commands.release_cmd import PushHint

_GITHUB_API = "https://api.github.com"
_CACHE_SECONDS = 60.0
# A verify still running or a release still uploading can change any moment;
# keep it only long enough that opening the tab twice does not ask GitHub twice.
_CHANGING_CACHE_SECONDS = 15.0
# A blocked api.github.com should fail fast instead of holding every refresh.
_CONNECT_TIMEOUT_SECONDS = 4.0
# Unauthenticated GitHub reads are limited to 60 per hour. Final answers are
# kept for a minute, answers that can still change for a few seconds, and an
# answer GitHub did not give definitively (error, timeout) is never kept.
# The value is (monotonic expiry, result).
_cache: dict[tuple[str, str, str, str], tuple[float, RemoteGithubState]] = {}
_inflight: dict[tuple[str, str, str, str], asyncio.Future[RemoteGithubState]] = {}
# Monotonic time until which GitHub said the quota is spent.
_rate_limited_until = 0.0
_rate_limit_credential: int | None = None
_client: tuple[asyncio.AbstractEventLoop, object] | None = None


class RemoteReleaseState(TypedDict):
    state: Literal["published", "missing", "unknown"]
    missing_assets: list[str]
    workflow: WorkflowState
    ready: bool


VerifyState = WorkflowState
RemoteTagState = Literal["missing", "at_head", "elsewhere", "unknown"]


class RemoteGithubState(TypedDict):
    """Public GitHub reads for one release; mirrors the frontend contract."""

    rate_limited: bool
    # False when a read failed by network error, timeout or a 5xx answer.
    reachable: bool
    visibility: Literal["public", "private", "not_found", "unknown"]
    verify: VerifyState
    release: RemoteReleaseState
    # Remote tag ``v<version>`` against the local HEAD.
    tag: RemoteTagState


class LocalStatus(NamedTuple):
    source_dir: Path
    plugin_id: str
    version: str
    repository: RepoState
    # Relative workflow path -> ``ActionFileStatus`` value.
    actions: dict[str, str]


def push_hint(state: RepoState) -> PushHint | None:
    """The git push that gets HEAD to its origin branch, None when none is due.

    Quoted as the CLI prints it; ``fill_in`` names the value of a placeholder.
    """
    from plugin.neko_plugin_cli.commands.release_cmd import _push_hint

    if not state.branch or state.head is None or state.head_pushed is True:
        return None
    remote, _, remote_branch = (state.upstream or "").partition("/")
    if remote == "origin" and remote_branch:
        # Named in full: a bare git push is refused for a differently named
        # upstream under push.default=simple.
        return _push_hint(state.branch, remote_branch)
    return _push_hint(state.branch)


def _fill_in(placeholder: str, value: str | None) -> dict[str, str] | None:
    return {"placeholder": placeholder.strip('"'), "value": value} if value is not None else None


def repository_status(state: RepoState) -> dict[str, object]:
    """``state`` plus the quoted commands the publish page shows for it.

    A placeholder's value travels in its own ``*_fill_in`` field, never inside
    a command: a value that cannot be quoted for the user's shell would run
    when pasted.
    """
    from plugin.neko_plugin_cli.commands.release_cmd import (
        _BRANCH_PLACEHOLDER,
        _push_hint,
        _shell_argument,
    )

    due = push_hint(state)
    first_push = _push_hint(state.branch) if state.branch else None
    branch_argument = _shell_argument(state.branch, _BRANCH_PLACEHOLDER) if state.branch else None
    return {
        **asdict(state),
        "push_command": due.command if due else None,
        "push_fill_in": _fill_in(due.placeholder, due.fill_in) if due else None,
        "branch_argument": branch_argument,
        "branch_fill_in": (
            _fill_in(_BRANCH_PLACEHOLDER, state.branch) if branch_argument == _BRANCH_PLACEHOLDER else None
        ),
        "set_upstream_command": first_push.command if first_push else None,
        "set_upstream_fill_in": _fill_in(first_push.placeholder, first_push.fill_in) if first_push else None,
    }


def read_local_status(project_id: str) -> LocalStatus:
    """Local facts of a registered project; shared with the publish gate."""
    # The template generator pulls in the build rules (pydantic); keep it out
    # of server startup, like the other authoring entry points do.
    from plugin.neko_plugin_cli.repo_action_migration import migrate_github_actions
    from plugin.neko_plugin_cli.templates.generator import PluginSpec

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
    return LocalStatus(record.source_dir, source.plugin_id, source.version, state, actions)


def _json_object(response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _unknown(*, rate_limited: bool = False) -> RemoteGithubState:
    return {
        "rate_limited": rate_limited,
        "reachable": True,
        "visibility": "unknown",
        "verify": "unknown",
        "release": {
            "state": "unknown", "missing_assets": [], "workflow": "unknown", "ready": False,
        },
        "tag": "unknown",
    }


def _is_final(result: RemoteGithubState) -> bool:
    release = result["release"]
    return (
        result["verify"] in {"success", "failure"}
        and release["state"] == "published"
        and release["ready"]
    )


def _shared_client(httpx):
    """One keep-alive client per event loop, so refreshes reuse the TLS connection."""
    global _client
    loop = asyncio.get_running_loop()
    if _client is None or _client[0] is not loop or _client[1].is_closed:
        timeout = httpx.Timeout(
            env_seconds("NEKO_PLUGIN_PUBLISH_STATUS_TIMEOUT", 10.0),
            connect=_CONNECT_TIMEOUT_SECONDS,
        )
        _client = (loop, httpx.AsyncClient(timeout=timeout))
    return _client[1]


async def _read(
    repository: str, head: str | None, tag: str, plugin_id: str,
    *, headers: dict[str, str] | None = None,
) -> RemoteGithubState:
    httpx = await ensure_httpx()
    client = _shared_client(httpx)
    if headers is None:
        headers = await asyncio.to_thread(github_headers)
    result = _unknown()
    definitive = True

    async def get(path: str):
        nonlocal definitive
        global _rate_limited_until
        global _rate_limit_credential
        try:
            response = await client.get(f"{_GITHUB_API}{path}", headers=headers)
        except httpx.HTTPError:
            result["reachable"] = False
            definitive = False
            return None
        limited = response.headers.get("x-ratelimit-remaining") == "0"
        if response.status_code == 429 or (response.status_code == 403 and limited):
            _rate_limit_credential = hash(headers.get("Authorization", ""))
            result["rate_limited"] = True
            definitive = False
            reset = response.headers.get("x-ratelimit-reset", "")
            if reset.isdigit():
                _rate_limited_until = time.monotonic() + max(int(reset) - time.time(), 0)
            return None
        if response.status_code >= 500:
            result["reachable"] = False
        if response.status_code not in {200, 404}:
            definitive = False
            return None
        return response

    async def read_visibility() -> None:
        response = await get(f"/repos/{repository}")
        if response is None:
            return
        if response.status_code == 404:
            result["visibility"] = "not_found"
        else:
            private = _json_object(response).get("private")
            result["visibility"] = "private" if private else "public"

    async def read_verify() -> None:
        response = await get(
            f"/repos/{repository}/actions/workflows/verify.yml/runs?head_sha={head}&per_page=1"
        )
        if response is None:
            return
        if response.status_code == 404:
            result["verify"] = "missing"
        else:
            runs = _json_object(response).get("workflow_runs")
            result["verify"] = _run_state(runs[0] if isinstance(runs, list) and runs else None)

    async def read_release() -> None:
        response = await get(f"/repos/{repository}/releases/tags/{quote(tag, safe='')}")
        if response is None:
            return
        if response.status_code == 404:
            result["release"] = {
                "state": "missing", "missing_assets": [], "workflow": "unknown", "ready": False,
            }
            return
        missing = missing_release_assets(_json_object(response), plugin_id)
        workflow: WorkflowState = "unknown"
        # The existing Release belongs to its remote tag, even if local HEAD moved on.
        tag_commit = await tag_read
        if tag_commit:
            response = await get(
                f"/repos/{repository}/actions/workflows/release.yml/runs?head_sha={tag_commit}"
            )
            if response is not None:
                workflow = (
                    "missing" if response.status_code == 404
                    else _run_state(select_release_run(_json_object(response), tag))
                )
        result["release"] = {
            "state": "published",
            "missing_assets": missing,
            "workflow": workflow,
            "ready": release_ready(missing, workflow),
        }

    async def read_tag() -> str | None:
        response = await get(f"/repos/{repository}/git/ref/tags/{quote(tag, safe='')}")
        if response is None:
            return
        if response.status_code == 404:
            result["tag"] = "missing"
            return
        target = _json_object(response).get("object")
        target = target if isinstance(target, dict) else {}
        if target.get("type") == "tag":
            # Annotated tag: the ref names the tag object, which names the commit.
            response = await get(f"/repos/{repository}/git/tags/{target.get('sha')}")
            if response is None or response.status_code != 200:
                return
            target = _json_object(response).get("object")
            target = target if isinstance(target, dict) else {}
        commit = target.get("sha")
        if isinstance(commit, str) and head:
            result["tag"] = "at_head" if commit == head else "elsewhere"
        return commit if isinstance(commit, str) else None

    tag_read = asyncio.create_task(read_tag())
    reads = [read_visibility(), read_release(), tag_read]
    if head:
        reads.append(read_verify())
    await asyncio.gather(*reads)
    if definitive:
        ttl = _CACHE_SECONDS if _is_final(result) else _CHANGING_CACHE_SECONDS
        _cache[(repository.casefold(), head or "", tag, plugin_id)] = (time.monotonic() + ttl, result)
    return result


async def read_remote_github(
    repository: str, head: str | None, tag: str, plugin_id: str, *, fresh: bool = False
) -> RemoteGithubState:
    """Public GitHub reads for one release; shared with the publish tasks.

    ``fresh`` skips the cache but still joins a read already in flight and
    still waits out a known rate limit.
    """
    key = (repository.casefold(), head or "", tag, plugin_id)
    if not fresh:
        cached = _cache.get(key)
        if cached is not None and time.monotonic() < cached[0]:
            return cached[1]
    headers = None
    if time.monotonic() < _rate_limited_until:
        headers = await asyncio.to_thread(github_headers)
        # Signing in must not keep waiting out the anonymous quota.
        if hash(headers.get("Authorization", "")) == _rate_limit_credential:
            return _unknown(rate_limited=True)
    pending = _inflight.get(key)
    if pending is None or pending.get_loop() is not asyncio.get_running_loop():
        pending = asyncio.ensure_future(_read(repository, head, tag, plugin_id, headers=headers))
        _inflight[key] = pending

        def forget(done: asyncio.Future[RemoteGithubState]) -> None:
            if _inflight.get(key) is done:
                del _inflight[key]

        pending.add_done_callback(forget)
    # A caller that gives up must not cancel the read other callers share.
    return await asyncio.shield(pending)


async def get_publish_status(project_id: str, *, fresh: bool = False) -> dict[str, object]:
    """Describe where a project stands on the way from local source to the Market."""
    local = await asyncio.to_thread(read_local_status, project_id)
    state = local.repository
    remote = None
    workspace_read = asyncio.to_thread(read_workspace_log, local.source_dir, state)
    if state.github_repository:
        remote, workspace = await asyncio.gather(
            read_remote_github(
                state.github_repository, state.head, state.tag, local.plugin_id, fresh=fresh
            ),
            workspace_read,
        )
    else:
        workspace = await workspace_read
    return {
        "plugin_id": local.plugin_id,
        "version": local.version,
        "repository": repository_status(state),
        "actions": local.actions,
        "remote": remote,
        "workspace": asdict(workspace) if workspace else None,
    }
