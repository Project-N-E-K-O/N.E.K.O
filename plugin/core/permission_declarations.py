"""Plugin permission declarations in plugin.toml.

``[permissions.require]`` and ``[permissions.ask]`` map a permission id to a
table with at least a ``reason``. A reason is either one string or a table of
language code -> string; readers fall back to any present language. ``ask``
entries may opt into background use with ``background = true`` plus a
``background_reason``. Components narrow with ``permissions=[...]`` on their
decorator, which must be a subset of the declared ids.

This module is pure: it parses an already-loaded plugin.toml mapping and
returns structured declarations plus issue codes. Callers render the codes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from plugin.sdk.shared.permissions import PERMISSION_IDS

SECTIONS: tuple[str, ...] = ("require", "ask")
NETWORK_PERMISSION = "system.network:connect"
_ENTRY_KEYS = frozenset({"reason", "background", "background_reason", "hosts"})
# Locale tried when the requested one is absent, before any other present one.
_FALLBACK_LOCALE = "en"


@dataclass(frozen=True)
class PermissionIssue:
    """One problem found in a declaration; ``code`` selects the message."""

    severity: str  # "error" | "warning"
    code: str
    params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionDeclaration:
    permission: str
    level: str  # "require" | "ask"
    reason: str | dict[str, str]
    background: bool = False
    background_reason: str | dict[str, str] | None = None
    hosts: tuple[str, ...] = ()


@dataclass
class PermissionDeclarations:
    # "none": no [permissions] table; "declared": parsed; "invalid": the
    # table exists but is not a table.
    status: str = "none"
    items: dict[str, PermissionDeclaration] = field(default_factory=dict)
    # Known ids whose entry is invalid (reported on its own).
    invalid: set[str] = field(default_factory=set)

    def level(self, permission: str) -> str | None:
        item = self.items.get(permission)
        return item.level if item is not None else None

    def ids(self) -> frozenset[str]:
        return frozenset(self.items)


def _text_issue(value: object, label: str) -> tuple[str | dict[str, str] | None, list[PermissionIssue]]:
    """Validate one user-facing text (reason / background_reason)."""
    if isinstance(value, str):
        if value.strip():
            return value.strip(), []
        return None, [PermissionIssue("error", "text_empty", {"label": label})]
    if isinstance(value, Mapping):
        issues: list[PermissionIssue] = []
        texts: dict[str, str] = {}
        for language, text in value.items():
            item_label = f"{label}.{language}"
            if not isinstance(text, str):
                issues.append(PermissionIssue("error", "text_translation_type", {"label": item_label}))
            elif not text.strip():
                issues.append(PermissionIssue("warning", "text_translation_empty", {"label": item_label}))
            else:
                texts[str(language)] = text.strip()
        if not texts:
            issues.append(PermissionIssue("error", "text_empty", {"label": label}))
            return None, issues
        return texts, issues
    return None, [PermissionIssue("error", "text_type", {"label": label})]


def parse_permission_declarations(
    config: Mapping[str, object],
) -> tuple[PermissionDeclarations, list[PermissionIssue]]:
    """Parse the top-level ``[permissions]`` table of a loaded plugin.toml."""
    declarations = PermissionDeclarations()
    issues: list[PermissionIssue] = []
    table = config.get("permissions")
    if table is None:
        return declarations, issues
    if not isinstance(table, Mapping):
        declarations.status = "invalid"
        issues.append(PermissionIssue("error", "table_type", {"label": "[permissions]"}))
        return declarations, issues
    declarations.status = "declared"
    for key in sorted(set(table) - set(SECTIONS)):
        issues.append(PermissionIssue("error", "unknown_section", {"label": f"[permissions].{key}"}))
    # Every id seen, including entries dropped as invalid.
    seen: set[str] = set()
    for section in SECTIONS:
        entries = table.get(section)
        if entries is None:
            continue
        section_label = f"[permissions.{section}]"
        if not isinstance(entries, Mapping):
            issues.append(PermissionIssue("error", "table_type", {"label": section_label}))
            continue
        for permission, entry in entries.items():
            label = f'{section_label}."{permission}"'
            if permission not in PERMISSION_IDS:
                issues.append(
                    PermissionIssue(
                        "error",
                        "unknown_permission",
                        {"label": label, "known": ", ".join(PERMISSION_IDS)},
                    )
                )
                continue
            if permission in seen:
                issues.append(PermissionIssue("error", "duplicate_permission", {"permission": permission}))
                continue
            seen.add(permission)
            declaration, entry_issues = _parse_entry(permission, section, entry, label)
            issues.extend(entry_issues)
            if declaration is not None:
                declarations.items[permission] = declaration
            else:
                declarations.invalid.add(permission)
    return declarations, issues


def _parse_entry(
    permission: str, section: str, entry: object, label: str
) -> tuple[PermissionDeclaration | None, list[PermissionIssue]]:
    issues: list[PermissionIssue] = []
    if not isinstance(entry, Mapping):
        return None, [PermissionIssue("error", "entry_type", {"label": label})]
    for key in sorted(set(entry) - _ENTRY_KEYS):
        issues.append(PermissionIssue("warning", "unknown_field", {"label": f"{label}.{key}"}))

    if "reason" not in entry:
        issues.append(PermissionIssue("error", "reason_missing", {"label": label}))
        reason = None
    else:
        reason, reason_issues = _text_issue(entry["reason"], f"{label}.reason")
        issues.extend(reason_issues)

    background = entry.get("background", False)
    background_reason = None
    if not isinstance(background, bool):
        issues.append(PermissionIssue("error", "background_type", {"label": f"{label}.background"}))
        background = False
    if section == "require":
        for key in ("background", "background_reason"):
            if key in entry:
                issues.append(PermissionIssue("warning", "background_on_require", {"label": f"{label}.{key}"}))
        background = False
    elif background:
        if "background_reason" not in entry:
            issues.append(PermissionIssue("error", "background_reason_missing", {"label": label}))
        else:
            background_reason, text_issues = _text_issue(
                entry["background_reason"], f"{label}.background_reason"
            )
            issues.extend(text_issues)
    elif "background_reason" in entry:
        issues.append(
            PermissionIssue("warning", "background_reason_unused", {"label": f"{label}.background_reason"})
        )

    hosts: tuple[str, ...] = ()
    if "hosts" in entry:
        value = entry["hosts"]
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            issues.append(PermissionIssue("error", "hosts_type", {"label": f"{label}.hosts"}))
        elif permission != NETWORK_PERMISSION:
            issues.append(PermissionIssue("warning", "hosts_unused", {"label": f"{label}.hosts"}))
        else:
            hosts = tuple(item.strip() for item in value)

    # Any error makes the entry invalid; it is not a usable declaration.
    if reason is None or (background and background_reason is None) or any(
        issue.severity == "error" for issue in issues
    ):
        return None, issues
    return (
        PermissionDeclaration(
            permission=permission,
            level=section,
            reason=reason,
            background=background,
            background_reason=background_reason,
            hosts=hosts,
        ),
        issues,
    )


def resolve_text(text: str | Mapping[str, str] | None, locale: str | None) -> str | None:
    """Pick the text for ``locale``; fall back to a stable present language.

    Order: exact locale, then the same base language (``zh-TW`` -> ``zh`` or
    ``zh-CN``), then English, then the alphabetically first language. The
    same declaration therefore always shows the same fallback.
    """
    if text is None or isinstance(text, str):
        return text
    present = {key: value for key, value in text.items() if isinstance(value, str) and value.strip()}
    if not present:
        return None
    by_lower = {key.lower().replace("_", "-"): key for key in present}
    wanted = (locale or "").lower().replace("_", "-")
    if wanted in by_lower:
        return present[by_lower[wanted]]
    base = wanted.split("-", 1)[0]
    if base:
        candidates = sorted(key for key in by_lower if key == base or key.startswith(base + "-"))
        if candidates:
            return present[by_lower[candidates[0]]]
    if _FALLBACK_LOCALE in by_lower:
        return present[by_lower[_FALLBACK_LOCALE]]
    return present[by_lower[sorted(by_lower)[0]]]


def narrowing_issues(
    narrowed: object, declared: frozenset[str], label: str
) -> list[PermissionIssue]:
    """Check one component's ``permissions=`` value against the declarations.

    ``narrowed`` is the literal value, or ``None`` when it is not a literal
    (caller decides; a non-literal cannot be confirmed statically).
    """
    if not isinstance(narrowed, (list, tuple)) or not all(isinstance(item, str) for item in narrowed):
        return [PermissionIssue("error", "narrowing_type", {"label": label})]
    issues: list[PermissionIssue] = []
    for permission in narrowed:
        if permission not in PERMISSION_IDS:
            issues.append(
                PermissionIssue(
                    "error",
                    "unknown_permission",
                    {"label": f'{label} "{permission}"', "known": ", ".join(PERMISSION_IDS)},
                )
            )
        elif permission not in declared:
            issues.append(
                PermissionIssue("error", "narrowing_not_declared", {"label": label, "permission": permission})
            )
    return issues
