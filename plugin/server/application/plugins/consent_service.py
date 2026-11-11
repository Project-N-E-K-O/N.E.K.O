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
from plugin.server.application.plugins.operation_lock import (
    serialized_plugin_operation,
)
from plugin.server.domain.consent_policy import ANALYZING_STATUSES, derive_consent
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.plugin_consent import consent_store

logger = get_logger("server.plugins.consent")


def _consent_view(plugin_id: str, meta: dict | None = None):
    """record + report + derived view; raises ServerDomainError on failure."""
    if meta is not None:
        if not isinstance(meta, dict) or not meta.get("config_path"):
            raise ServerDomainError("PLUGIN_NOT_FOUND", "Plugin not found", 404)
        root = Path(meta["config_path"]).parent
        version = str(meta.get("version") or "")
    else:
        root, version = plugin_source(plugin_id)
    record = consent_store.read(plugin_id)
    report = read_report(plugin_id, root, version)
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
        record, _, view, _ = _consent_view(plugin_id, meta)
    except ServerDomainError:
        # A store or registry read failure must never force a confirmation.
        return {"state": "analyzing", "decision": None, "upgraded": False}
    return {
        "state": view.state,
        "decision": record.get("decision"),
        "upgraded": view.upgraded,
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
            record, report, view, version = _consent_view(plugin_id, meta)
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


@serialized_plugin_operation
async def approve_plugin(
    plugin_id: str, *, version: str, unconfirmed: list[str], analysis_status: str
) -> dict:
    # Revalidate and persist under the same operation boundary as uninstall.
    details = await asyncio.to_thread(consent_details, plugin_id)
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
    await asyncio.to_thread(
        consent_store.decide,
        plugin_id,
        "approved",
        sorted(capabilities),
        details["version"],
    )
    return await asyncio.to_thread(consent_details, plugin_id)


@serialized_plugin_operation
async def deny_plugin(plugin_id: str) -> dict:
    from plugin.server.application.plugins.lifecycle_service import (
        PluginLifecycleService,
    )

    details = await asyncio.to_thread(consent_details, plugin_id)
    await asyncio.to_thread(
        consent_store.decide, plugin_id, "denied", [], details["version"]
    )

    try:
        await PluginLifecycleService().stop_plugin(plugin_id)
    except ServerDomainError as exc:
        if exc.code != "PLUGIN_NOT_RUNNING":
            raise
    return await asyncio.to_thread(consent_details, plugin_id)


@serialized_plugin_operation
async def revoke_plugin(plugin_id: str) -> dict:
    await asyncio.to_thread(consent_store.delete, plugin_id)
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
