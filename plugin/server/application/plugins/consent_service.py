"""First-run decisions: consent queries and serialized decision commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.application.plugins import capability_analysis_service
from plugin.server.application.plugins.capability_analysis_service import (
    plugin_source,
    read_report,
)
from plugin.server.application.plugins.permission_service import (
    mirror_consent_decision,
)
from plugin.server.application.plugins.operation_lock import (
    serialized_plugin_operation,
)
from plugin.server.domain.consent_policy import ANALYZING_STATUSES, derive_consent
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.plugin_consent import consent_store

logger = get_logger("server.plugins.consent")


def _consent_view(
    plugin_id: str, meta: dict | None = None, *, verify_revision: bool = True
):
    """record + report + derived view; raises ServerDomainError on failure."""
    if meta is not None:
        if not isinstance(meta, dict) or not meta.get("config_path"):
            raise ServerDomainError("PLUGIN_NOT_FOUND", "Plugin not found", 404)
        root = Path(meta["config_path"]).parent
        version = str(meta.get("version") or "")
    else:
        root, version = plugin_source(plugin_id)
    record = consent_store.read(plugin_id)
    report = read_report(plugin_id, root, version, verify_revision=verify_revision)
    return record, report, derive_consent(record, report), version


def consent_details(plugin_id: str, *, schedule_missing: bool = False) -> dict:
    record, report, view, version = _consent_view(plugin_id)
    if schedule_missing and report["status"] == "unavailable" and not report.get("_source"):
        capability_analysis_service.schedule_analysis(plugin_id)
        record, report, view, version = _consent_view(plugin_id)
    return {
        "plugin_id": plugin_id,
        "version": version,
        "decision": record.get("decision"),
        "decided_at": record.get("decided_at"),
        "approved_capabilities": sorted(
            set(record.get("approved_capabilities") or [])
        ),
        "unconfirmed": view.unconfirmed,
        "state": view.state,
        "upgraded": view.upgraded,
        "analysis": report,
    }


def consent_summary(plugin_id: str, meta: dict | None = None) -> dict:
    try:
        record, report, view, _ = _consent_view(plugin_id, meta, verify_revision=False)
    except ServerDomainError:
        # A store or registry read failure must never force a confirmation.
        return {"state": "analyzing", "decision": None, "upgraded": False, "blind_spots": 0}
    blind_spots = report.get("blind_spots")
    return {
        "state": view.state,
        "decision": record.get("decision"),
        "upgraded": view.upgraded,
        # Places the static analysis could not see through; the list marks
        # such plugins for review.
        "blind_spots": len(blind_spots) if isinstance(blind_spots, list) else 0,
    }


def consent_notices() -> list[dict]:
    """Running plugins that still have unconfirmed capabilities."""
    with state.acquire_plugin_hosts_read_lock():
        running = set(state.plugin_hosts)
    with state.acquire_plugins_read_lock():
        metas = {key: dict(value) for key, value in state.plugins.items()}
    notices = []
    for plugin_id in sorted(running):
        meta = metas.get(plugin_id)
        try:
            _, report, view, version = _consent_view(plugin_id, meta, verify_revision=False)
        except ServerDomainError:
            continue
        if view.decision == "denied" or view.state != "confirm":
            continue
        notices.append(
            {
                "plugin_id": plugin_id,
                "name": meta.get("name") or plugin_id,
                "version": version,
                "unconfirmed": view.unconfirmed,
                "upgraded": view.upgraded,
                "analysis_status": report["status"],
            }
        )
    return notices


async def _decide_with_mirror(
    plugin_id: str,
    decision: str | None,
    capabilities: list[str] | None = None,
    version: str = "",
    *,
    newly_approved: list[str] | None = None,
) -> None:
    """Write a consent decision, keeping the permission store in step.

    Transitional: the permission-store mirror goes with the consent dialog.
    The mirror runs first; if the consent write then fails, the mirror is
    undone, so a failed request leaves this plugin's records in both files
    unchanged (a first-use import of the legacy file may still be saved).
    """
    undo = await asyncio.to_thread(
        mirror_consent_decision,
        plugin_id,
        decision,
        capabilities or [],
        newly_approved=newly_approved,
    )
    try:
        if decision is None:
            await asyncio.to_thread(consent_store.delete, plugin_id)
        else:
            await asyncio.to_thread(
                consent_store.decide, plugin_id, decision, capabilities or [], version
            )
    except BaseException as exc:
        # serialized_plugin_operation already shields cancellation once the
        # lock is held; BaseException keeps the undo even if that changes.
        try:
            await asyncio.to_thread(undo)
        except Exception as undo_exc:
            if isinstance(exc, ServerDomainError):
                exc.details["permission_rollback"] = "incomplete"
            logger.error(
                "consent write failed and the permission store could not be "
                "rolled back: plugin_id={}, error={}",
                plugin_id,
                type(undo_exc).__name__,
            )
        raise


@serialized_plugin_operation
async def approve_plugin(
    plugin_id: str, *, version: str, unconfirmed: list[str], analysis_status: str
) -> dict:
    # Revalidate under the operation lock and queue analysis if the report is stale.
    details = await asyncio.to_thread(consent_details, plugin_id, schedule_missing=True)
    if (
        analysis_status in ANALYZING_STATUSES
        or details["version"] != version
        or details["analysis"]["status"] != analysis_status
        or set(details["unconfirmed"]) != set(unconfirmed)
    ):
        raise ServerDomainError(
            "PLUGIN_CONSENT_CHANGED", "Plugin capabilities changed. Review them again.", 409
        )
    capabilities = set(details["approved_capabilities"])
    capabilities.update(unconfirmed)
    await _decide_with_mirror(
        plugin_id,
        "approved",
        sorted(capabilities),
        details["version"],
        newly_approved=sorted(unconfirmed),
    )
    return await asyncio.to_thread(consent_details, plugin_id)


@serialized_plugin_operation
async def deny_plugin(plugin_id: str) -> dict:
    from plugin.server.application.plugins.lifecycle_service import (
        PluginLifecycleService,
    )

    details = await asyncio.to_thread(consent_details, plugin_id)
    await _decide_with_mirror(plugin_id, "denied", [], details["version"])

    try:
        await PluginLifecycleService().stop_plugin(plugin_id)
    except ServerDomainError as exc:
        if exc.code != "PLUGIN_NOT_RUNNING":
            raise
    return await asyncio.to_thread(consent_details, plugin_id)


@serialized_plugin_operation
async def revoke_plugin(plugin_id: str) -> dict:
    await _decide_with_mirror(plugin_id, None)
    return await asyncio.to_thread(consent_details, plugin_id)


def denied_plugin_ids(plugin_ids) -> set[str]:
    try:
        return {
            plugin_id
            for plugin_id in plugin_ids
            if consent_store.read(plugin_id).get("decision") == "denied"
        }
    except ServerDomainError as exc:
        logger.warning(
            "could not read plugin consent decisions: error={}",
            type(exc).__name__,
        )
        return set()


def forget_plugin(plugin_id: str) -> None:
    try:
        capability_analysis_service.analysis_service.forget(plugin_id)
    finally:
        consent_store.delete(plugin_id)
