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
    PermissionSpec("system.network:listen", enforced=False),
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


# Risk tiers, most dangerous first. The single table both the host and the
# plugin manager read (the UI fetches it from the host); a tier only decides
# order, colour and folding, never what a permission allows.
TIER_HIGH = "high"
TIER_MEDIUM = "medium"
TIER_LOW = "low"
TIER_SAFE = "safe"
RISK_TIERS: tuple[str, ...] = (TIER_HIGH, TIER_MEDIUM, TIER_LOW, TIER_SAFE)
# A tier other than the always-expanded ones folds when it has more items.
TIER_COLLAPSE_OVER = 3
ALWAYS_EXPANDED_TIERS: frozenset[str] = frozenset({TIER_HIGH})
# "safe" covers what needs no permission (own data directory, config,
# storage); it is described in one sentence, never listed item by item.
UNLISTED_TIERS: frozenset[str] = frozenset({TIER_SAFE})

PERMISSION_TIERS: MappingProxyType[str, str] = MappingProxyType(
    {
        "system.network:connect": TIER_HIGH,
        "system.process:spawn": TIER_HIGH,
        "system.fs:write": TIER_HIGH,
        "system.env:read": TIER_HIGH,
        "plugin_sdk.main_agent.memory:read": TIER_HIGH,
        "plugin_sdk.main_agent.conversations:read": TIER_HIGH,
        "system.fs:read": TIER_MEDIUM,
        "plugin_sdk.main_agent.messages:push": TIER_MEDIUM,
        "system.network:listen": TIER_LOW,
        "plugin_sdk.models:invoke": TIER_LOW,
    }
)
# Static-analysis blind spot types shown with a tier; the others have none.
BLIND_SPOT_TIERS: MappingProxyType[str, str] = MappingProxyType({"native_loader": TIER_HIGH})


@dataclass(frozen=True, slots=True)
class PrivacyGroup:
    """One row of the privacy overview: a kind of data or resource.

    ``partial_block``: some ways of reading this data are not covered by the
    permission checks yet, so the page says so next to the row.
    """

    id: str
    permissions: tuple[str, ...]
    partial_block: bool = False


PRIVACY_GROUPS: tuple[PrivacyGroup, ...] = (
    PrivacyGroup("memory", ("plugin_sdk.main_agent.memory:read",), partial_block=True),
    PrivacyGroup("conversations", ("plugin_sdk.main_agent.conversations:read",), partial_block=True),
    PrivacyGroup("network", ("system.network:connect",)),
    PrivacyGroup("fs_write", ("system.fs:write",)),
    PrivacyGroup("process_env", ("system.process:spawn", "system.env:read")),
    PrivacyGroup("fs_read", ("system.fs:read",)),
    PrivacyGroup("messages", ("plugin_sdk.main_agent.messages:push",)),
    PrivacyGroup("listen", ("system.network:listen",)),
    PrivacyGroup("models", ("plugin_sdk.models:invoke",)),
)


def tier_rank(tier: str | None) -> int:
    """Sort key: tiers in ``RISK_TIERS`` order, no tier last."""
    return RISK_TIERS.index(tier) if tier in RISK_TIERS else len(RISK_TIERS)


def group_tier(group: PrivacyGroup) -> str:
    """The most dangerous tier among the group's permissions."""
    return min((PERMISSION_TIERS[permission] for permission in group.permissions), key=tier_rank)


def tier_collapsed(tier: str, count: int) -> bool:
    """Whether a tier section with ``count`` items starts folded."""
    return tier not in ALWAYS_EXPANDED_TIERS and count > TIER_COLLAPSE_OVER


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


# Every permission has exactly one tier and every privacy group refers to
# known permissions, each permission in exactly one group.
assert set(PERMISSION_TIERS) == set(PERMISSIONS)
assert set(PERMISSION_TIERS.values()) <= set(RISK_TIERS)
assert sorted(p for group in PRIVACY_GROUPS for p in group.permissions) == sorted(PERMISSIONS)

__all__ = [
    "ALWAYS_EXPANDED_TIERS",
    "BLIND_SPOT_TIERS",
    "LEGACY_PERMISSION_NAMES",
    "LOCK_PERMISSIONS",
    "PERMISSIONS",
    "PERMISSION_DENIED_REASONS",
    "PERMISSION_IDS",
    "PERMISSION_TIERS",
    "PRIVACY_GROUPS",
    "PLUGIN_PERMISSION_DENIED",
    "PermissionSpec",
    "PrivacyGroup",
    "RISK_TIERS",
    "TIER_COLLAPSE_OVER",
    "TIER_HIGH",
    "TIER_LOW",
    "TIER_MEDIUM",
    "TIER_SAFE",
    "UNLISTED_TIERS",
    "UNENFORCED_PERMISSIONS",
    "group_tier",
    "legacy_name",
    "normalize_permission",
    "tier_collapsed",
    "tier_rank",
]
