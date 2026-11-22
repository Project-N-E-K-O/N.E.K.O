"""The plugin permission vocabulary: the one place permission ids are defined.

Every host registration point (call-chain decisions, the RPC operation table,
the message route, the plugin manager labels) reads from here, so an id that is
missing here is not judged anywhere.

Ids are tree-shaped strings, ``<resource path>:<verb>``. A permission whose
data carries a lock is ``lock=True``; one the host only discloses and never
refuses is ``enforced=False``. ``legacy_name`` is the name the earlier
capability vocabulary used for the same thing, kept so stored approvals and
the public ``call_chain`` view keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    id: str
    lock: bool = False
    enforced: bool = True
    legacy_name: str | None = None


_SPECS = (
    PermissionSpec("plugin_sdk.main_agent.memory:read", lock=True, legacy_name="memory.read"),
    PermissionSpec("plugin_sdk.main_agent.conversations:read", lock=True, legacy_name="conversations.read"),
    PermissionSpec("plugin_sdk.models:invoke"),
    PermissionSpec("system.network:connect"),
    PermissionSpec("system.fs:read"),
    PermissionSpec("system.fs:write"),
    PermissionSpec("system.process:spawn"),
    PermissionSpec("system.env:read", enforced=False),
    PermissionSpec("plugin_sdk.main_agent.messages:push", enforced=False, legacy_name="messages.push"),
)

PERMISSIONS: MappingProxyType[str, PermissionSpec] = MappingProxyType({spec.id: spec for spec in _SPECS})
PERMISSION_IDS: tuple[str, ...] = tuple(spec.id for spec in _SPECS)
LOCK_PERMISSIONS: frozenset[str] = frozenset(spec.id for spec in _SPECS if spec.lock)
UNENFORCED_PERMISSIONS: frozenset[str] = frozenset(spec.id for spec in _SPECS if not spec.enforced)
# Earlier capability name -> permission id.
LEGACY_PERMISSION_NAMES: MappingProxyType[str, str] = MappingProxyType(
    {spec.legacy_name: spec.id for spec in _SPECS if spec.legacy_name is not None}
)

# Error code for every permission refusal. Deliberately not ``PERMISSION_DENIED``,
# which plugin config access already uses for a different failure.
PLUGIN_PERMISSION_DENIED = "PLUGIN_PERMISSION_DENIED"
PERMISSION_DENIED_REASONS: tuple[str, ...] = (
    "not_declared",
    "denied",
    "undecided",
    "background",
    "receiver_lacks",
)


def normalize_permission(name: object) -> str | None:
    """Return the permission id for ``name`` (an id or a legacy name), else None."""
    if not isinstance(name, str):
        return None
    if name in PERMISSIONS:
        return name
    return LEGACY_PERMISSION_NAMES.get(name)


def legacy_name(permission: str) -> str | None:
    spec = PERMISSIONS.get(permission)
    return spec.legacy_name if spec is not None else None


__all__ = [
    "LEGACY_PERMISSION_NAMES",
    "LOCK_PERMISSIONS",
    "PERMISSIONS",
    "PERMISSION_DENIED_REASONS",
    "PERMISSION_IDS",
    "PLUGIN_PERMISSION_DENIED",
    "PermissionSpec",
    "UNENFORCED_PERMISSIONS",
    "legacy_name",
    "normalize_permission",
]
