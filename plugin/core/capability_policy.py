"""Pure permission decisions along a plugin call chain, and the master switch.

The vocabulary is ``plugin.sdk.shared.permissions``: a permission outside it is
not judged. The master switch only decides whether a judged refusal is carried
out; while it is on, every use is still judged and recorded.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from plugin.sdk.shared.permissions import PERMISSIONS

# Defined now so the wire contract is fixed; nothing returns it yet.
CAPABILITY_NOT_GRANTED = "CAPABILITY_NOT_GRANTED"

# Where the master switch can be turned on from.
SOURCE_HOST_CONFIG = "host_config"
SOURCE_LEGACY_ENV = "legacy_env"
LEGACY_ENV_NAME = "NEKO_PLUGIN_CAPABILITY_POLICY"
_LEGACY_SWITCHED_OFF = frozenset({"off", "observe"})


def checks_disabled_sources() -> tuple[str, ...]:
    """Which configuration turned permission checks off; empty while they are on.

    Either source is enough: when the host setting and the legacy variable
    disagree, the switch is on. Read at call time.
    """
    from plugin import settings

    sources = []
    if getattr(settings, "PLUGIN_PERMISSION_CHECKS_DISABLED", False) is True:
        sources.append(SOURCE_HOST_CONFIG)
    if getattr(settings, "PLUGIN_CAPABILITY_POLICY", None) in _LEGACY_SWITCHED_OFF:
        sources.append(SOURCE_LEGACY_ENV)
    return tuple(sources)


def checks_disabled() -> bool:
    return bool(checks_disabled_sources())


def current_mode() -> str:
    """``enforce`` while checks are in force, ``observe`` while the switch is on."""
    return "observe" if checks_disabled() else "enforce"


def is_enforced(permission: str) -> bool:
    """Whether the host may refuse a use of ``permission`` right now.

    False for permissions outside the vocabulary, for disclosure-only ones and
    while the master switch is on.
    """
    spec = PERMISSIONS.get(permission)
    return spec is not None and spec.enforced and not checks_disabled()


def startup_notices() -> list[str]:
    """Log lines describing the switch, for the host to print once at startup."""
    from plugin import settings

    notices = []
    legacy = getattr(settings, "PLUGIN_CAPABILITY_POLICY", None)
    if legacy is not None:
        if legacy in _LEGACY_SWITCHED_OFF:
            meaning = "it now turns the master switch on: uses are still judged and recorded, none are refused"
        elif legacy == "enforce":
            meaning = "it now leaves permission checks in force"
            if SOURCE_HOST_CONFIG in checks_disabled_sources():
                meaning += ", but NEKO_PLUGIN_PERMISSION_CHECKS_DISABLED switches them off anyway"
        else:
            meaning = "the value is not one of off / observe / enforce and is ignored"
        notices.append(
            f"{LEGACY_ENV_NAME}={legacy!r} is merged into the plugin permission master switch; {meaning}. "
            "The variable is deprecated and will be removed: use NEKO_PLUGIN_PERMISSION_CHECKS_DISABLED instead."
        )
    sources = checks_disabled_sources()
    if sources:
        notices.append(
            "Plugin permission checks are switched off (source: "
            + ", ".join(sources)
            + "): every use is still judged and recorded, none is refused."
        )
    return notices


@dataclass(frozen=True, slots=True)
class Decision:
    capability: str
    chain: tuple[str, ...]
    judged: bool
    blocked_by: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def allowed(self) -> bool:
        return not self.blocked_by


def decide(capability: str, chain: Iterable[str], granted: Callable[[str], frozenset[str]]) -> Decision:
    """Allow ``capability`` only if every plugin on ``chain`` was granted it.

    ``chain`` runs from the root caller to the plugin using the capability.
    Permissions outside the vocabulary are not judged. A caller missing it
    (``caller_chain``) is reported before the plugin itself
    (``self_not_approved``), the order the later stages enforce them in.
    The master switch does not change the decision, only whether it is carried out.
    """
    chain = tuple(chain)
    if capability not in PERMISSIONS or not chain:
        return Decision(capability, chain, judged=False)
    grants = {plugin: granted(plugin) for plugin in dict.fromkeys(chain)}  # one read per plugin
    missing = [capability not in grants[plugin] for plugin in chain]
    blocked = tuple(plugin for plugin, lacks in zip(chain, missing) if lacks)
    # By position, so in A -> B -> A the first A still counts as a caller.
    if any(missing[:-1]):
        reason = "caller_chain"
    elif missing[-1]:
        reason = "self_not_approved"
    else:
        reason = None
    return Decision(capability, chain, judged=True, blocked_by=blocked, reason=reason)
