"""Minimal live provider adapter used by the pipeline split."""

from __future__ import annotations

from typing import Any

from .contracts import ViewerIdentity


class _LegacyBilibiliIdentityProvider:
    """Compatibility adapter for tests and runtimes without live_provider."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def resolve_identity(self, event: Any) -> ViewerIdentity:
        identity = getattr(self.runtime, "bili_identity", None)
        resolver = getattr(identity, "resolve", None)
        if callable(resolver):
            return await resolver(event)
        return ViewerIdentity(uid=str(getattr(event, "uid", "") or ""), nickname=str(getattr(event, "nickname", "") or ""))

    def identity_step_id(self) -> str:
        return "bili_identity"


def identity_provider_for(runtime: Any) -> Any:
    """Return the selected live identity provider, preserving legacy contexts."""

    provider = getattr(runtime, "live_provider", None)
    return provider if provider is not None else _LegacyBilibiliIdentityProvider(runtime)
