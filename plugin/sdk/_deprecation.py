from __future__ import annotations

import contextlib
import contextvars
import warnings

_SUPPRESS_SYNC_DEPRECATION: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "plugin_sdk_suppress_sync_deprecation",
    default=False,
)


def warn_sync_deprecated(owner: str, method: str, async_method: str | None = None) -> None:
    if _SUPPRESS_SYNC_DEPRECATION.get():
        return
    replacement = async_method or f"{method}_async"
    warnings.warn(
        f"{owner}.{method}() is deprecated and will be removed in a future version; "
        f"use {owner}.{replacement}() instead.",
        DeprecationWarning,
        stacklevel=3,
    )


@contextlib.contextmanager
def suppress_sync_deprecation():
    token = _SUPPRESS_SYNC_DEPRECATION.set(True)
    try:
        yield
    finally:
        _SUPPRESS_SYNC_DEPRECATION.reset(token)
