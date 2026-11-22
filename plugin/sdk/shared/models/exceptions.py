"""Unified exception hierarchy for SDK v2."""

from __future__ import annotations

import errno as _errno
from collections.abc import Mapping

from ..permissions.catalog import PLUGIN_PERMISSION_DENIED


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


class PermissionDeniedError(SdkError):
    """The host refused a use of a permission.

    ``permission`` is the refused permission id and ``reason`` one of
    ``PERMISSION_DENIED_REASONS``. Each interface delivers it in its own
    existing style (raised, or returned as ``Err``).
    """

    # Class-level defaults: SdkError drops None-valued context, and unpickling
    # rebuilds from the message alone.
    permission: str | None = None
    reason: str | None = None

    def __init__(
        self,
        message: str = "",
        *,
        permission: str | None = None,
        reason: str | None = None,
        **context: object,
    ) -> None:
        context.pop("code", None)
        super().__init__(
            message or _denied_message(permission, reason),
            code=PLUGIN_PERMISSION_DENIED,
            permission=permission,
            reason=reason,
            **context,
        )

    @classmethod
    def from_wire(cls, code: object, message: object, details: object) -> "PermissionDeniedError | None":
        """Rebuild the error from a host ``{code, message, details}``; None if it is not one.

        Only ``PLUGIN_PERMISSION_DENIED`` with a ``details.permission`` string
        qualifies; any other code (including plugin config's own
        ``PERMISSION_DENIED``) is left as it is.
        """
        if code != PLUGIN_PERMISSION_DENIED or not isinstance(details, Mapping):
            return None
        permission = details.get("permission")
        if not isinstance(permission, str) or not permission:
            return None
        reason = details.get("reason")
        return cls(
            message if isinstance(message, str) else "",
            permission=permission,
            reason=reason if isinstance(reason, str) else None,
            details=dict(details),
        )

    @classmethod
    def from_exception(cls, error: BaseException) -> "PermissionDeniedError | None":
        """``from_wire`` for an exception carrying ``code`` / ``details`` (e.g. RpcRemoteError)."""
        if isinstance(error, PermissionDeniedError):
            return error
        return cls.from_wire(
            getattr(error, "code", None),
            getattr(error, "message", None) or str(error),
            getattr(error, "details", None),
        )


class SystemPermissionDeniedError(PermissionDeniedError, PermissionError):
    """A ``system.*`` permission was refused inside the plugin process.

    Also a ``PermissionError`` so standard-library callers that already catch
    it keep working. Only ``errno`` is set (never ``strerror``), so ``str()``
    stays the plain message. Do not pass OSError member names (``filename``,
    ``errno``, ``strerror``, ``filename2``, ``winerror``) as context.
    """

    def __init__(self, message: str = "", **kwargs: object) -> None:
        super().__init__(message, **kwargs)
        self.errno = _errno.EACCES


def _denied_message(permission: str | None, reason: str | None) -> str:
    text = f"permission denied: {permission}" if permission else "permission denied"
    return f"{text} ({reason})" if reason else text


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
    "PermissionDeniedError",
    "SystemPermissionDeniedError",
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
