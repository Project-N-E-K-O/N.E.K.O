"""Data for the plugin permission panel, the privacy page and the pending list.

Read-only views assembled from three sources, each the authority for its part:

* the permission store: what the user decided (``permission_service``);
* the plugin's ``[permissions]`` declaration in its plugin.toml;
* the static analysis report: what the scan found, and where it could not see.

Last use comes from the call audit (``permission_usage``). No user-facing text
is produced here: the plugin manager names permissions by id in its own
translations. Declared reasons are passed on as written by the plugin.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plugin.core.capability_analysis import (
    COUNTED_BLIND_SPOT_TYPES,
    DIRECT_BASES,
    POSSIBLE_ONLY_PERMISSIONS,
)
from plugin.core.state import state
from plugin.sdk.shared.permissions.catalog import (
    ALWAYS_EXPANDED_TIERS,
    BLIND_SPOT_TIERS,
    PERMISSION_IDS,
    PERMISSION_TIERS,
    PERMISSIONS,
    PRIVACY_GROUPS,
    RISK_TIERS,
    TIER_HIGH,
    TIER_COLLAPSE_OVER,
    UNLISTED_TIERS,
    group_tier,
    tier_collapsed,
    tier_rank,
)
from plugin.sdk.shared.permissions.declarations import (
    PermissionDeclarations,
    parse_permission_declarations,
)
from plugin.server.application.plugins.capability_analysis_service import (
    read_report,
    schedule_analysis,
)
from plugin.server.application.plugins.permission_service import (
    MESSAGES_PUSH,
    permission_record,
    record_states,
)
from plugin.server.application.plugins.permission_usage import UsageIndex, usage_index
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.plugin_permissions import (
    CONDITION_ANYTIME,
    GRANTS_RESET,
    LEGACY_APPROVED,
    LEGACY_DENIED,
)

# Reconciliation of the declaration with the scan, one per permission row.
RECONCILE_MATCHED = "matched"  # declared and found
RECONCILE_UNDECLARED = "undeclared"  # found, not declared
RECONCILE_UNSCANNED = "unscanned"  # declared, not found
RECONCILE_SCAN_PENDING = "scan_pending"  # declared; the scan is queued or running
# declared; the scan failed or is unavailable (``analysis.status`` says which)
RECONCILE_SCAN_UNAVAILABLE = "scan_unavailable"
# The foreground / background condition is the plugin's own claim: the host
# cannot verify whether the user is using the plugin yet.
CONDITION_BASIS = "plugin_reported"

UNUSED_DAYS = 30
WEEK_DAYS = 7
_SCANNED_STATUSES = frozenset({"completed", "partial"})
_SCANNING_STATUSES = frozenset({"queued", "running"})
_PERSONAL_DATA = ("plugin_sdk.main_agent.memory:read", "plugin_sdk.main_agent.conversations:read")
_SENDS_OUT = ("system.network:connect", "plugin_sdk.models:invoke")
_MAX_MANIFEST_BYTES = 1024 * 1024


def _not_found(plugin_id: str) -> ServerDomainError:
    return ServerDomainError("PLUGIN_NOT_FOUND", "Plugin not found", 404, details={"plugin_id": plugin_id})


def catalog() -> dict:
    """The shared risk-tier table and privacy groups."""
    return {
        "tiers": [
            {
                "tier": tier,
                "order": index,
                "always_expanded": tier in ALWAYS_EXPANDED_TIERS,
                "collapse_over": None if tier in ALWAYS_EXPANDED_TIERS else TIER_COLLAPSE_OVER,
                "listed": tier not in UNLISTED_TIERS,
            }
            for index, tier in enumerate(RISK_TIERS)
        ],
        "permissions": {
            permission: {
                "tier": PERMISSION_TIERS[permission],
                "enforced": PERMISSIONS[permission].enforced,
                "lock": PERMISSIONS[permission].lock,
                "privacy_group": next(g.id for g in PRIVACY_GROUPS if permission in g.permissions),
            }
            for permission in PERMISSION_IDS
        },
        "blind_spot_tiers": dict(BLIND_SPOT_TIERS),
        "privacy_groups": [
            {
                "group": group.id,
                "permissions": list(group.permissions),
                "tier": group_tier(group),
                "partial_block": group.partial_block,
            }
            for group in PRIVACY_GROUPS
        ],
    }


# ----------------------------------------------------------------------
# Per-plugin inputs
# ----------------------------------------------------------------------


@dataclass
class _PluginInputs:
    plugin_id: str
    name: str
    version: str
    root: Path | None
    record: dict
    report: dict
    manifest: dict
    declarations: PermissionDeclarations


def _plugin_metas() -> dict[str, dict]:
    with state.acquire_plugins_read_lock():
        return {key: dict(value) for key, value in state.plugins.items() if isinstance(value, dict)}


def _read_manifest(root: Path | None) -> dict:
    if root is None:
        return {}
    path = root / "plugin.toml"
    try:
        if not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
            return {}
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError, UnicodeError):
        return {}


def _inputs(plugin_id: str, meta: dict, *, verify: bool = False) -> _PluginInputs:
    """``verify``: check the cached scan against the plugin's files (a tree
    walk) and queue a new scan when it is stale or missing. The single-plugin
    panel does; the all-plugin views use the cached reports as they are."""
    config_path = meta.get("config_path")
    root = Path(config_path).parent if config_path else None
    version = str(meta.get("version") or "")
    if root is None:
        report = {"status": "unavailable"}
    else:
        report = read_report(plugin_id, root, version, verify_revision=verify)
        if verify and report.get("status") == "unavailable" and not report.get("analysis_id"):
            schedule_analysis(plugin_id)
            report = read_report(plugin_id, root, version, verify_revision=False)
    manifest = _read_manifest(root)
    declarations, _issues = parse_permission_declarations(manifest)
    return _PluginInputs(
        plugin_id=plugin_id,
        name=str(meta.get("name") or plugin_id),
        version=version,
        root=root,
        record=permission_record(plugin_id),
        report=report,
        manifest=manifest,
        declarations=declarations,
    )


def _checks_off() -> bool:
    from plugin.core.capability_policy import checks_disabled

    try:
        return checks_disabled()
    except Exception:
        return False


# ----------------------------------------------------------------------
# Rows
# ----------------------------------------------------------------------


def _scan_summary(permission: str, findings: list[dict], basis: object) -> dict | None:
    if not findings:
        return None
    direct = any(row.get("basis") in DIRECT_BASES for row in findings)
    return {
        "files": len({row.get("file") for row in findings}),
        "sites": len(findings),
        # The strongest basis among the findings, as the report states it.
        "basis": basis if isinstance(basis, str) else None,
        # fs read/write findings can only be "possible": the path may lie in
        # the plugin's own data directory, which needs no permission.
        "possible_only": not direct or permission in POSSIBLE_ONLY_PERMISSIONS,
    }


def _reconcile(declared: bool, found: bool, status: object) -> str | None:
    scanned = status in _SCANNED_STATUSES
    if declared and found:
        return RECONCILE_MATCHED
    if found:
        return RECONCILE_UNDECLARED
    if declared:
        if scanned:
            return RECONCILE_UNSCANNED
        return RECONCILE_SCAN_PENDING if status in _SCANNING_STATUSES else RECONCILE_SCAN_UNAVAILABLE
    return None


def _declaration(inputs: _PluginInputs, permission: str) -> dict | None:
    item = inputs.declarations.items.get(permission)
    if item is None:
        return None
    return {
        "level": item.level,
        "reason": item.reason,
        "background": item.background,
        "background_reason": item.background_reason,
        "hosts": list(item.hosts),
    }


def _unused(index: UsageIndex, inputs: _PluginInputs, permission: str, status: str, entry: dict, now: datetime):
    """``True`` / ``False`` for a granted permission when the audit history
    covers the whole period; ``None`` otherwise."""
    if status != "granted" or not index.covers_days(UNUSED_DAYS):
        return None
    cutoff = now - timedelta(days=UNUSED_DAYS)
    decided = _parse_time(entry.get("decided_at"))
    if decided is not None and decided > cutoff:
        return False
    last = index.last_effective_use(inputs.plugin_id, permission)
    return last is None or last.at < cutoff


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _rows(inputs: _PluginInputs, index: UsageIndex, checks_off: bool, now: datetime) -> list[dict]:
    report = inputs.report
    scanned = report.get("status") in _SCANNED_STATUSES
    analysed = report.get("permissions") if scanned and isinstance(report.get("permissions"), dict) else {}
    stored = inputs.record.get("permissions") or {}
    states = record_states(inputs.record, checks_off=checks_off)
    rows = []
    for permission in PERMISSION_IDS:
        declared = permission in inputs.declarations.items
        item = analysed.get(permission) if isinstance(analysed.get(permission), dict) else {}
        findings = [row for row in item.get("findings") or [] if isinstance(row, dict)]
        resolved = states[permission]
        entry = stored.get(permission) if isinstance(stored.get(permission), dict) else {}
        last = index.last_use(inputs.plugin_id, permission)
        # A row for what is declared, found by the scan, decided, or seen in use.
        if not (declared or findings or resolved.status != "undecided" or last is not None):
            continue
        granted = resolved.status == "granted"
        rows.append(
            {
                "permission": permission,
                "tier": PERMISSION_TIERS[permission],
                "reconcile": _reconcile(declared, bool(findings), report.get("status")),
                "declared": _declaration(inputs, permission),
                "scan": _scan_summary(permission, findings, item.get("basis")),
                "state": resolved.status,
                "condition": entry.get("condition") if granted else None,
                "condition_basis": CONDITION_BASIS,
                "legacy_any": granted and entry.get("legacy_any") is True,
                "enforced": resolved.enforced,
                "last_used": last.last_used() if last is not None else None,
                "unused_30d": _unused(index, inputs, permission, resolved.status, entry, now),
            }
        )
    rows.sort(key=lambda row: (tier_rank(row["tier"]), PERMISSION_IDS.index(row["permission"])))
    return rows


def _tiers(rows: list[dict]) -> list[dict]:
    out = []
    for tier in RISK_TIERS:
        if tier in UNLISTED_TIERS:
            continue
        count = sum(1 for row in rows if row["tier"] == tier)
        out.append({"tier": tier, "count": count, "collapsed": tier_collapsed(tier, count)})
    return out


def _counts(rows: list[dict]) -> dict:
    return {
        "total": len(rows),
        "granted": sum(1 for row in rows if row["state"] == "granted"),
        "denied": sum(1 for row in rows if row["state"] == "denied"),
        "undecided": sum(1 for row in rows if row["state"] == "undecided"),
        "undeclared_found": sum(1 for row in rows if row["reconcile"] == RECONCILE_UNDECLARED),
    }


def _blind_spots(report: dict) -> dict:
    files = []
    for row in report.get("blind_spot_files") or []:
        if not isinstance(row, dict):
            continue
        types = {
            kind: count
            for kind, count in (row.get("types") or {}).items()
            if kind in COUNTED_BLIND_SPOT_TYPES
        }
        files.append(
            {
                "file": row.get("file"),
                "count": sum(types.values()),
                "types": types,
                "high_risk": any(BLIND_SPOT_TIERS.get(kind) == TIER_HIGH for kind in types),
            }
        )
    not_analyzed = [row for row in report.get("not_analyzed") or [] if isinstance(row, dict)]
    return {
        "files": files,
        "counted": sum(row["count"] for row in files),
        # A row with a file is one skipped file or directory; a row without
        # one either sums up what a cut-short list left out (``omitted``) or
        # notes a scan-wide condition (over budget, unreadable), not a file.
        "not_analyzed_files": sum(
            1 if row.get("file") is not None else int(row.get("omitted") or 0)
            for row in not_analyzed
        ),
    }


def _developer(inputs: _PluginInputs) -> dict:
    report = inputs.report
    analysed = report.get("permissions") if isinstance(report.get("permissions"), dict) else {}
    table = inputs.manifest.get("permissions")
    return {
        "findings": {
            permission: [
                {key: row.get(key) for key in ("file", "line", "method", "basis")}
                for row in (item.get("findings") or [])
                if isinstance(row, dict)
            ]
            for permission, item in analysed.items()
            if isinstance(item, dict) and item.get("findings")
        },
        "blind_spots": [
            {key: row.get(key) for key in ("type", "file", "line")}
            for row in report.get("blind_spots") or []
            if isinstance(row, dict)
        ],
        "not_analyzed": [row for row in report.get("not_analyzed") or [] if isinstance(row, dict)],
        "manifest_permissions": table if isinstance(table, dict) else None,
    }


def _source(record: dict) -> dict:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    fingerprint = source.get("fingerprint")
    return {
        "channel": source.get("channel"),
        "fingerprint8": fingerprint[:8] if isinstance(fingerprint, str) and fingerprint else None,
        "confirmed": record.get("source_confirmed") is not False,
    }


def _grants_reset(record: dict) -> dict | None:
    value = record.get(GRANTS_RESET)
    if not isinstance(value, dict) or not isinstance(value.get("at"), str):
        return None
    return {"at": value["at"], "reason": value.get("reason")}


def plugin_panel(plugin_id: str, *, index: UsageIndex = usage_index) -> dict:
    """Everything the permission tab and the three dialogs show for one plugin."""
    meta = _plugin_metas().get(plugin_id)
    if meta is None:
        raise _not_found(plugin_id)
    inputs = _inputs(plugin_id, meta, verify=True)
    checks_off = _checks_off()
    rows = _rows(inputs, index, checks_off, datetime.now(timezone.utc))
    report = inputs.report
    return {
        "plugin_id": plugin_id,
        "version": inputs.version,
        "source": _source(inputs.record),
        "grants_reset": _grants_reset(inputs.record),
        "checks_disabled": checks_off,
        "analysis": {
            "status": report.get("status"),
            "analyzer_version": report.get("_analyzer_version"),
            "analyzed_at": report.get("analyzed_at"),
            "declarations": inputs.declarations.status,
        },
        "counts": _counts(rows),
        "rows": rows,
        "tiers": _tiers(rows),
        "blind_spots": _blind_spots(report),
        "developer": _developer(inputs),
    }


# ----------------------------------------------------------------------
# All plugins: pending list and privacy page
# ----------------------------------------------------------------------


def _all_rows(index: UsageIndex) -> list[tuple[_PluginInputs, list[dict]]]:
    checks_off = _checks_off()
    now = datetime.now(timezone.utc)
    out = []
    for plugin_id, meta in sorted(_plugin_metas().items()):
        inputs = _inputs(plugin_id, meta)
        out.append((inputs, _rows(inputs, index, checks_off, now)))
    return out


def _has_decision(record: dict) -> bool:
    if record.get(LEGACY_DENIED) or record.get(LEGACY_APPROVED):
        return True
    return any(
        isinstance(entry, dict) and entry.get("state") in ("granted", "denied")
        for entry in (record.get("permissions") or {}).values()
    )


def _needs_decision(row: dict) -> bool:
    """An undecided item the first-start dialog lists: a required one, a
    system one, or one the scan found but the plugin did not declare."""
    if row["state"] != "undecided":
        return False
    declared = row["declared"] or {}
    return (
        declared.get("level") == "require"
        or row["permission"].startswith("system.")
        or row["reconcile"] == RECONCILE_UNDECLARED
    )


def _pending(plugins: list[tuple[_PluginInputs, list[dict]]], index: UsageIndex) -> dict:
    to_confirm, upgraded, inferred, first_use = [], [], [], []
    refused = index.refused_uses()
    checks_off = _checks_off()
    for inputs, rows in plugins:
        if inputs.declarations.status != "declared":
            # No declaration of its own: what the scan found are inferred
            # items, collected for the user rather than asked one by one.
            inferred.extend(
                {"plugin_id": inputs.plugin_id, "permission": row["permission"]}
                for row in rows
                if row["state"] == "undecided" and row["reconcile"] == RECONCILE_UNDECLARED
            )
        else:
            items = [row["permission"] for row in rows if _needs_decision(row)]
            if items:
                # Approximate: the store keeps no earlier permission set, so
                # "asks for more after an update" is any plugin with an
                # earlier decision that still has items to decide. Items a
                # newer analyzer found on an unchanged plugin count too. An
                # update that changed the plugin's source dropped its
                # decisions (``grants_reset``); it is still listed as
                # upgraded, which is what the user did (e.g. a market update).
                earlier = _has_decision(inputs.record) or _grants_reset(inputs.record) is not None
                target = upgraded if earlier else to_confirm
                target.append({"plugin_id": inputs.plugin_id, "items": items})
        # Used while the user has not decided: not asked in a dialog, listed
        # here instead. Whatever the declaration and the scan say.
        states = record_states(inputs.record, checks_off=checks_off)
        stored = inputs.record.get("permissions") or {}
        for permission in PERMISSION_IDS:
            seen = refused.get((inputs.plugin_id, permission))
            if seen is None or states[permission].status != "undecided":
                continue
            entry = stored.get(permission) if isinstance(stored.get(permission), dict) else {}
            decided = _parse_time(entry.get("decided_at"))
            if decided is not None and seen[0] <= decided:
                continue  # refused before the user last set it
            first_use.append(
                {
                    "plugin_id": inputs.plugin_id,
                    "permission": permission,
                    "at": seen[0].isoformat(),
                    "count": seen[1],
                }
            )
    return {"to_confirm": to_confirm, "upgraded": upgraded, "inferred": inferred, "first_use": first_use}


def pending(*, index: UsageIndex = usage_index) -> dict:
    """The "needs you" list of the privacy page."""
    return _pending(_all_rows(index), index)


def _can_use(row: dict) -> bool:
    """Whether the plugin can use the permission now: granted, or a
    disclosure-only permission it declares or was found using."""
    if row["state"] == "granted":
        return True
    # ``enforced`` is the effective flag: also false while the master switch
    # has the checks off, when an undecided permission is not refused either.
    return not row["enforced"] and (
        row["declared"] is not None or row["scan"] is not None
    )


def _history(index: UsageIndex) -> dict:
    since = index.history_since()
    return {"since": since.isoformat() if since else None, "covers_30d": index.covers_days(UNUSED_DAYS)}


def privacy_overview(*, index: UsageIndex = usage_index) -> dict:
    plugins = _all_rows(index)
    pending_items = _pending(plugins, index)
    groups = []
    for group in PRIVACY_GROUPS:
        able = {
            inputs.plugin_id
            for inputs, rows in plugins
            for row in rows
            if row["permission"] in group.permissions and _can_use(row)
        }
        latest = index.latest(group.permissions)
        groups.append(
            {
                "group": group.id,
                "tier": group_tier(group),
                "plugins": len(able),
                "latest": {"plugin_id": latest.plugin_id, "at": latest.at.isoformat()} if latest else None,
                "partial_block": group.partial_block,
            }
        )
    combos = []
    for inputs, rows in plugins:
        can = {row["permission"] for row in rows if _can_use(row)}
        reads = [permission for permission in _PERSONAL_DATA if permission in can]
        sends = [permission for permission in _SENDS_OUT if permission in can]
        if reads and sends:
            combos.append({"plugin_id": inputs.plugin_id, "reads": reads, "sends": sends})
    return {
        "checks_disabled": _checks_off(),
        "history": _history(index),
        "pending": {key: len(value) for key, value in pending_items.items()},
        "groups": groups,
        "combos": combos,
    }


def privacy_group(group_id: str, *, index: UsageIndex = usage_index) -> dict:
    group = next((group for group in PRIVACY_GROUPS if group.id == group_id), None)
    if group is None:
        raise ServerDomainError(
            "PRIVACY_GROUP_NOT_FOUND", "Privacy group not found", 404, details={"group": group_id}
        )
    plugins = _all_rows(index)
    names = {inputs.plugin_id: inputs.name for inputs, _ in plugins}
    out = []
    listed = set()
    for inputs, rows in plugins:
        items = [row for row in rows if row["permission"] in group.permissions]
        if not items:
            continue
        listed.add(inputs.plugin_id)
        out.append(
            {
                "plugin_id": inputs.plugin_id,
                "name": inputs.name,
                "installed": True,
                "has_permission": any(_can_use(row) for row in items),
                "items": [
                    {
                        **{
                            key: row[key]
                            for key in (
                                "permission", "state", "condition", "condition_basis",
                                "legacy_any", "enforced", "last_used", "unused_30d",
                            )
                        },
                        "uses_7d": index.count(inputs.plugin_id, row["permission"], WEEK_DAYS),
                    }
                    for row in items
                ],
            }
        )
    # Plugins the audit saw using the data without a row here (e.g. uninstalled).
    for plugin_id in sorted(index.plugins_using(group.permissions) - listed):
        items = []
        for permission in group.permissions:
            last = index.last_use(plugin_id, permission)
            if last is None:
                continue
            items.append(
                {
                    "permission": permission,
                    "state": None,
                    "condition": None,
                    "condition_basis": CONDITION_BASIS,
                    "legacy_any": False,
                    "enforced": PERMISSIONS[permission].enforced,
                    "last_used": last.last_used(),
                    "unused_30d": None,
                    "uses_7d": index.count(plugin_id, permission, WEEK_DAYS),
                }
            )
        out.append(
            {
                "plugin_id": plugin_id,
                "name": names.get(plugin_id, plugin_id),
                "installed": plugin_id in names,
                "has_permission": False,
                "items": items,
            }
        )
    return {
        "group": group.id,
        "tier": group_tier(group),
        "partial_block": group.partial_block,
        "checks_disabled": _checks_off(),
        "history": _history(index),
        "plugins": out,
        "recent": [use.to_dict() for use in index.recent(group.permissions)],
    }


def check_refusable(permission: str, state_value: str) -> None:
    """Sending content to N.E.K.O is disclosure only: it is approved or left
    undecided, never refused (a stored refusal would still read undecided)."""
    if state_value == "denied" and permission == MESSAGES_PUSH:
        raise ServerDomainError(
            "PERMISSION_NOT_REFUSABLE",
            "This permission is disclosure only and cannot be refused",
            422,
            details={"permission": permission},
        )


def check_condition_allowed(plugin_id: str, permission: str, state_value: str, condition: str) -> None:
    """Raise unless ``condition`` may be chosen when granting ``permission``.

    "Any time" needs the plugin to declare background use of it, or to have
    no declaration at all (the item is then inferred from the scan).
    """
    if state_value != "granted" or condition != CONDITION_ANYTIME:
        return
    meta = _plugin_metas().get(plugin_id)
    if meta is None:
        raise _not_found(plugin_id)
    config_path = meta.get("config_path")
    declarations, _issues = parse_permission_declarations(
        _read_manifest(Path(config_path).parent if config_path else None)
    )
    if declarations.status != "declared":
        return
    item = declarations.items.get(permission)
    if item is None or not item.background:
        raise ServerDomainError(
            "PERMISSION_BACKGROUND_NOT_DECLARED",
            "The plugin does not declare background use of this permission",
            422,
            details={"plugin_id": plugin_id, "permission": permission},
        )


def forget_refused(plugin_id: str, permission: str) -> None:
    """After the user sets ``permission``: earlier refusals leave the pending list."""
    usage_index.forget_refused(plugin_id, permission)


def ensure_plugin_known(plugin_id: str) -> None:
    if plugin_id not in _plugin_metas():
        raise _not_found(plugin_id)
