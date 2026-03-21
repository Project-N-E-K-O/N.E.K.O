"""Unified exception hierarchy for SDK v2."""

from __future__ import annotations


class SdkError(RuntimeError):
    """Base error for all SDK v2 failures."""

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        details: object | None = None,
        **context: object,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details
        self.context = {k: v for k, v in context.items() if v is not None}
        for k, v in self.context.items():
            setattr(self, k, v)


class ValidationError(SdkError):
    """Input validation failure (bad arguments, config schema, filter syntax, etc.)."""


class NotFoundError(SdkError):
    """A requested resource does not exist."""


class TransportError(SdkError):
    """Transport/runtime boundary failure (RPC, bus, capability unavailable)."""


class ConflictError(SdkError):
    """Operation conflicts with current state (duplicate entry, revision mismatch)."""


class PluginCallError(SdkError):
    """Cross-plugin call failure (circular call, depth exceeded, bad entry/event ref, router error)."""


# ---------------------------------------------------------------------------
# Backwards-compat aliases (to be removed once all call-sites are updated)
# ---------------------------------------------------------------------------
InvalidArgumentError = ValidationError
ConfigPathError = ValidationError
ConfigValidationError = ValidationError
ConfigProfileError = ValidationError
PluginConfigError = ValidationError
BusFilterError = ValidationError
MessageValidationError = ValidationError
NonReplayableTraceError = SdkError
CapabilityUnavailableError = TransportError
BusTransportError = TransportError
BusError = TransportError
EventPublishError = TransportError
ConversationNotFoundError = NotFoundError
RecordConflictError = ConflictError
EntryConflictError = ConflictError
CircularCallError = PluginCallError
CallChainTooDeepError = PluginCallError
InvalidEntryRefError = PluginCallError
InvalidEventRefError = PluginCallError
PluginRouterError = PluginCallError
AuthorizationError = SdkError

# Alias type unions (kept as aliases for transition, can be removed later)
AdapterErrorLike = SdkError
GatewayErrorLike = SdkError
BusErrorLike = SdkError
ConfigErrorLike = SdkError
RouterErrorLike = SdkError
HookErrorLike = SdkError
CallChainErrorLike = SdkError


__all__ = [
    "SdkError",
    "ValidationError",
    "NotFoundError",
    "TransportError",
    "ConflictError",
    "PluginCallError",
    # Backwards-compat aliases
    "InvalidArgumentError",
    "ConfigPathError",
    "ConfigValidationError",
    "ConfigProfileError",
    "PluginConfigError",
    "BusFilterError",
    "MessageValidationError",
    "NonReplayableTraceError",
    "CapabilityUnavailableError",
    "BusTransportError",
    "BusError",
    "EventPublishError",
    "ConversationNotFoundError",
    "RecordConflictError",
    "EntryConflictError",
    "CircularCallError",
    "CallChainTooDeepError",
    "InvalidEntryRefError",
    "InvalidEventRefError",
    "PluginRouterError",
    "AuthorizationError",
    "AdapterErrorLike",
    "GatewayErrorLike",
    "BusErrorLike",
    "ConfigErrorLike",
    "RouterErrorLike",
    "HookErrorLike",
    "CallChainErrorLike",
]
