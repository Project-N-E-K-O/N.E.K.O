"""GitHub Release facts shared by the CLI publisher and workbench status."""

from __future__ import annotations

from typing import Literal

WorkflowState = Literal[
    "success", "failure", "running", "not_run", "missing", "unknown"
]
RELEASE_CHECK_SUFFIX = ".market-release-check.txt"
MARKET_EVIDENCE_NAME = "market-evidence.json"


def workflow_run_state(run: object) -> WorkflowState:
    if not isinstance(run, dict):
        return "not_run"
    if run.get("status") != "completed":
        return "running"
    return "success" if run.get("conclusion") == "success" else "failure"


def select_release_run(payload: object, tag: str) -> dict | None:
    """Select the latest tag push or manual tag run from GitHub's newest-first list."""
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    return next(
        (
            run
            for run in (runs if isinstance(runs, list) else [])
            if isinstance(run, dict)
            and run.get("head_branch") == tag
            and run.get("event") in {"push", "workflow_dispatch"}
        ),
        None,
    )


def release_asset_is_downloadable(asset: object) -> bool:
    if not isinstance(asset, dict):
        return False
    size = asset.get("size")
    download_url = asset.get("browser_download_url")
    return (
        asset.get("state") == "uploaded"
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size > 0
        and isinstance(download_url, str)
        and bool(download_url.strip())
    )


def missing_release_assets(payload: object, plugin_id: str) -> list[str]:
    assets = payload.get("assets") if isinstance(payload, dict) else None
    ready = {
        asset.get("name")
        for asset in (assets if isinstance(assets, list) else [])
        if release_asset_is_downloadable(asset)
    }
    expected = (
        f"{plugin_id}.neko-plugin",
        f"{plugin_id}{RELEASE_CHECK_SUFFIX}",
        MARKET_EVIDENCE_NAME,
    )
    return [name for name in expected if name not in ready]


def release_ready(missing_assets: list[str], workflow: WorkflowState) -> bool:
    return not missing_assets and workflow == "success"
