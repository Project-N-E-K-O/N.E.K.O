"""Pure capability decision along a plugin call chain (docs/design/plugin-call-chain.md)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from plugin.core.capability_analysis import CAPABILITIES

# Defined now so the wire contract is fixed; observe mode never returns it.
CAPABILITY_NOT_GRANTED = "CAPABILITY_NOT_GRANTED"


def current_mode() -> str:
    """``off`` / ``observe`` / ``enforce``; read at call time."""
    from plugin import settings

    return getattr(settings, "PLUGIN_CAPABILITY_POLICY", "observe")


def policy_enabled() -> bool:
    return current_mode() != "off"


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
    Capabilities outside the analyzer vocabulary are not judged. A caller
    missing it (``caller_chain``) is reported before the plugin itself
    (``self_not_approved``), the order the later stages enforce them in.
    """
    chain = tuple(chain)
    if capability not in CAPABILITIES or not chain:
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
