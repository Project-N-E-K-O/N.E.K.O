"""Component permission narrowing at registration.

A component (an entry, an LLM tool, an entry hook, an event handler) may list
``permissions=[...]`` on its decorator to say it only needs part of what
plugin.toml declares. Leaving it out means the component inherits every
declared permission. Narrowing only feeds the permission panel ("turning this
off stops these features"); nothing is enforced per component here.

At registration each narrowing must be a subset of the declarations. A
component that lists more is not registered and the reason is logged. The
plugin process and the host's static scan both decide through
``resolve_component_permissions``, so their entry tables agree, and the check
itself is ``narrowing_issues``, the one ``neko-plugin check`` uses.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Hashable, TypeVar

from plugin.sdk.shared.constants import HOOK_META_ATTR

from .declarations import PermissionIssue, declared_permission_ids, narrowing_issues

# ``target`` of an entry hook that applies to every entry.
ALL_ENTRIES = "*"
# Event type of entries; entry hooks only attach to these.
PLUGIN_ENTRY = "plugin_entry"

K = TypeVar("K", bound=Hashable)

Narrowing = tuple[str, ...]


def normalize_narrowing(value: object, *, where: str) -> Narrowing | None:
    """The ``permissions=`` value as stored in metadata: ``None`` or a tuple of ids.

    Only the shape is checked here (a list or tuple of strings), when the
    decorator runs; whether the ids are known and declared is decided at
    registration, against plugin.toml.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        # Keep the author's order; repeated ids add nothing.
        return tuple(dict.fromkeys(value))
    raise TypeError(f"{where}: permissions= must be a list of permission id strings")


def read_declared_permission_ids(config_path: object) -> frozenset[str] | None:
    """Declared ids from the plugin.toml at ``config_path``; ``None`` if unknown.

    Unknown (no path, unreadable file, ``[permissions]`` not a table) means
    narrowing is not checked, as ``neko-plugin check`` does for an invalid table.
    """
    if not isinstance(config_path, (str, Path)) or not str(config_path):
        return None
    import tomllib

    try:
        with Path(config_path).open("rb") as stream:
            config = tomllib.load(stream)
    except (OSError, ValueError):
        return None
    return declared_permission_ids(config)


def narrowing_errors(
    permissions: Narrowing | None, declared: frozenset[str] | None, label: str
) -> list[PermissionIssue]:
    """Errors that keep a component with this narrowing from registering."""
    if permissions is None or declared is None:
        return []
    return [issue for issue in narrowing_issues(list(permissions), declared, label) if issue.severity == "error"]


def describe_issue(issue: PermissionIssue) -> str:
    """One log line for a narrowing error (logs are English, like the rest)."""
    params = dict(issue.params)
    label = params.get("label", "component")
    if issue.code == "narrowing_not_declared":
        return (
            f'{label}: permissions= lists "{params.get("permission")}", which plugin.toml does not '
            "declare in [permissions.require] or [permissions.ask]"
        )
    if issue.code == "unknown_permission":
        return f"{label}: unknown permission id (known: {params.get('known', '')})"
    if issue.code == "narrowing_type":
        return f"{label}: permissions= must be a list of permission id strings"
    return f"{label}: {issue.code}"


def merge_narrowing(own: Narrowing | None, extra: Iterable[Narrowing | None]) -> Narrowing | None:
    """``own`` plus every narrowing in ``extra``; ``None`` (inherit all) absorbs the rest."""
    if own is None:
        return None
    merged = list(own)
    for item in extra:
        if item is None:
            return None
        merged.extend(permission for permission in item if permission not in merged)
    return tuple(merged)


@dataclass(frozen=True, slots=True)
class Component:
    """One registrable component, as the resolver sees it."""

    label: str
    event_type: str
    entry_id: str
    permissions: Narrowing | None


@dataclass(frozen=True, slots=True)
class EntryHook:
    """An entry hook: its permissions join every entry its ``target`` matches."""

    label: str
    target: str
    permissions: Narrowing | None

    def applies_to(self, component: Component) -> bool:
        return component.event_type == PLUGIN_ENTRY and self.target in (ALL_ENTRIES, component.entry_id)


@dataclass(frozen=True, slots=True)
class Resolution:
    """What registration does with one component.

    ``errors`` non-empty: the component is not registered. Otherwise
    ``permissions`` is what its metadata records (``None`` = inherits all).
    """

    permissions: Narrowing | None
    errors: tuple[str, ...] = ()

    @property
    def registered(self) -> bool:
        return not self.errors


def resolve_component_permissions(
    components: Mapping[K, Component],
    hooks: Sequence[EntryHook],
    declared: frozenset[str] | None,
) -> dict[K, Resolution]:
    """Decide every component of one plugin.

    - A component's own narrowing must be a subset of ``declared``.
    - An entry hook's permissions join each entry it targets; a hook that
      lists an undeclared permission closes those entries.
    - Event handlers (any non-entry event type) are components on their own:
      one that fails is not subscribed.
    ``declared=None`` (unknown) checks nothing but still merges hooks.
    """
    hook_errors = {
        index: tuple(describe_issue(issue) for issue in narrowing_errors(hook.permissions, declared, hook.label))
        for index, hook in enumerate(hooks)
    }
    resolved: dict[K, Resolution] = {}
    for key, component in components.items():
        errors = [describe_issue(issue) for issue in narrowing_errors(component.permissions, declared, component.label)]
        matching = [index for index, hook in enumerate(hooks) if hook.applies_to(component)]
        for index in matching:
            errors.extend(f"{component.label} is closed by {message}" for message in hook_errors[index])
        permissions = merge_narrowing(component.permissions, (hooks[index].permissions for index in matching))
        resolved[key] = Resolution(permissions=permissions, errors=tuple(errors))
    return resolved


def with_permissions(meta: object, permissions: Narrowing | None) -> object:
    """``meta`` recording ``permissions``, as a copy: decorator metadata is shared."""
    if getattr(meta, "permissions", None) == permissions:
        return meta
    if dataclasses.is_dataclass(meta) and not isinstance(meta, type) and any(
        item.name == "permissions" for item in dataclasses.fields(meta)
    ):
        return dataclasses.replace(meta, permissions=permissions)
    return meta


def static_entry_hooks(cls: type) -> list[EntryHook]:
    """Entry hooks (``@hook`` / ``@before_entry`` ...) a plugin class declares.

    Shared by the plugin process and the host's static scan so both merge the
    same hooks into the same entries.
    """
    getmembers_static = getattr(inspect, "getmembers_static", inspect.getmembers)
    hooks: list[EntryHook] = []
    for attr_name, class_value in getmembers_static(cls):
        target = class_value.__func__ if isinstance(class_value, (staticmethod, classmethod)) else class_value
        meta = getattr(target, HOOK_META_ATTR, None)
        if meta is None:
            continue
        hooks.append(
            EntryHook(
                label=f"{getattr(meta, 'timing', 'before')} hook {attr_name!r}",
                target=str(getattr(meta, "target", ALL_ENTRIES)),
                permissions=getattr(meta, "permissions", None),
            )
        )
    return hooks


def component_label(event_type: str, entry_id: str) -> str:
    if event_type == PLUGIN_ENTRY:
        return f"entry {entry_id!r}"
    return f"{event_type} handler {entry_id!r}"


__all__ = [
    "ALL_ENTRIES",
    "Component",
    "EntryHook",
    "Narrowing",
    "PLUGIN_ENTRY",
    "Resolution",
    "component_label",
    "describe_issue",
    "merge_narrowing",
    "narrowing_errors",
    "normalize_narrowing",
    "read_declared_permission_ids",
    "resolve_component_permissions",
    "static_entry_hooks",
    "with_permissions",
]
