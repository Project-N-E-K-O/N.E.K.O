"""Pure capability decisions along a plugin call chain.

Shared by the host (which decides) and the SDK (which only reads the rules).
Nothing here reads plugin-supplied data: callers pass the chain the host built
from its own registry and a resolver for each plugin's approved capabilities.
See docs/design/plugin-call-chain.md.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from plugin.core.capability_analysis import CAPABILITIES

POLICY_OFF = "off"
POLICY_OBSERVE = "observe"
POLICY_ENFORCE = "enforce"
POLICY_MODES = (POLICY_OFF, POLICY_OBSERVE, POLICY_ENFORCE)

# Defined now so the wire contract is fixed; no stage returns it yet.
CAPABILITY_NOT_GRANTED = "CAPABILITY_NOT_GRANTED"
REASON_CALLER_CHAIN = "caller_chain"
REASON_SELF_NOT_APPROVED = "self_not_approved"

JUDGED_CAPABILITIES = frozenset(CAPABILITIES)


def current_mode() -> str:
    """Read the switch at call time so tests and restarts see one source."""
    from plugin import settings

    mode = getattr(settings, "PLUGIN_CAPABILITY_POLICY", POLICY_OBSERVE)
    return mode if mode in POLICY_MODES else POLICY_OBSERVE


def policy_enabled() -> bool:
    return current_mode() != POLICY_OFF


@dataclass(frozen=True, slots=True)
class Decision:
    capability: str
    chain: tuple[str, ...]
    judged: bool
    blocked_by: tuple[str, ...] = ()
    caller_blocked: bool = False
    self_blocked: bool = False

    @property
    def allowed(self) -> bool:
        return not self.blocked_by

    @property
    def reason(self) -> str | None:
        # The anti-laundering check is enforced first, so it names the cause
        # even when the plugin itself is not approved either.
        if self.caller_blocked:
            return REASON_CALLER_CHAIN
        if self.self_blocked:
            return REASON_SELF_NOT_APPROVED
        return None


def decide(
    capability: str,
    chain: Iterable[str],
    granted: Callable[[str], frozenset[str]],
) -> Decision:
    """Allow ``capability`` only if every plugin on ``chain`` was granted it.

    ``chain`` runs from the root caller to the plugin using the capability
    (last element). Capabilities outside the analyzer vocabulary are not
    judged. The result never widens what any single plugin was granted.
    """
    chain = tuple(chain)
    if capability not in JUDGED_CAPABILITIES or not chain:
        return Decision(capability=capability, chain=chain, judged=False)
    blocked = tuple(plugin for plugin in chain if capability not in granted(plugin))
    *callers, user = chain
    return Decision(
        capability=capability,
        chain=chain,
        judged=True,
        blocked_by=blocked,
        caller_blocked=any(plugin in blocked for plugin in callers if plugin != user),
        self_blocked=user in blocked,
    )


def should_reject(decision: Decision, mode: str) -> bool:
    """Whether the host refuses the use. Observe-only stage: never."""
    del decision, mode
    return False


def not_granted_error(decision: Decision) -> dict[str, object]:
    return {
        "code": CAPABILITY_NOT_GRANTED,
        "message": f"Capability '{decision.capability}' is not granted along the call chain",
        "details": {
            "capability": decision.capability,
            "blocked_by": list(decision.blocked_by),
            "reason": decision.reason,
        },
    }


__all__ = [
    "CAPABILITY_NOT_GRANTED",
    "Decision",
    "JUDGED_CAPABILITIES",
    "POLICY_ENFORCE",
    "POLICY_MODES",
    "POLICY_OBSERVE",
    "POLICY_OFF",
    "REASON_CALLER_CHAIN",
    "REASON_SELF_NOT_APPROVED",
    "current_mode",
    "decide",
    "not_granted_error",
    "policy_enabled",
]
