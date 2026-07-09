"""Minimal live provider adapter used by the pipeline split."""

from __future__ import annotations

from typing import Any

from .contracts import ViewerIdentity, normalize_live_platform, parse_room_id


class LiveProviderRouter:
    """Runtime-facing placeholder until provider-events adds full routing."""

    id = "live_provider"

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    @property
    def platform(self) -> str:
        return normalize_live_platform(getattr(self.runtime.config, "live_platform", "bilibili"))

    def configured_room_ref(self) -> str:
        raw_room_ref = str(getattr(self.runtime.config, "live_room_ref", "") or "").strip()
        if self.platform != "bilibili":
            return raw_room_ref
        if raw_room_ref:
            return raw_room_ref
        room_id = self.configured_room_id()
        return str(room_id) if room_id > 0 else ""

    def configured_room_id(self) -> int:
        if self.platform != "bilibili":
            return 0
        try:
            return max(0, int(getattr(self.runtime.config, "live_room_id", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def normalize_room_ref(self, value: Any) -> dict[str, Any]:
        return normalize_room_ref_for_platform(self.platform, value)

    def is_listening(self) -> bool:
        ingest = getattr(self.runtime, "bili_live_ingest", None)
        checker = getattr(ingest, "is_listening", None)
        try:
            return bool(checker()) if callable(checker) else False
        except Exception:
            return False

    def listener_state(self) -> dict[str, Any]:
        ingest = getattr(self.runtime, "bili_live_ingest", None)
        state_fn = getattr(ingest, "listener_state", None)
        try:
            data = state_fn() if callable(state_fn) else {}
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    async def start_listening(self, room_ref: Any) -> bool:
        normalized = self.normalize_room_ref(room_ref)
        if not normalized.get("ok"):
            return False
        if self.platform != "bilibili":
            return False
        ingest = getattr(self.runtime, "bili_live_ingest", None)
        starter = getattr(ingest, "start_listening", None)
        if not callable(starter):
            return False
        return await starter(int(normalized.get("room_id") or 0)) is True

    async def stop_listening(self) -> None:
        ingest = getattr(self.runtime, "bili_live_ingest", None)
        stopper = getattr(ingest, "stop_listening", None)
        if callable(stopper):
            await stopper()

    async def lookup_room_status(self, room_ref: Any) -> Any:
        normalized = self.normalize_room_ref(room_ref)
        if not normalized.get("ok"):
            return None
        if self.platform != "bilibili":
            return None
        ingest = getattr(self.runtime, "bili_live_ingest", None)
        lookup = getattr(ingest, "lookup_room_status", None)
        if not callable(lookup):
            return None
        return await lookup(int(normalized.get("room_id") or 0))

    async def resolve_identity(self, event: Any) -> ViewerIdentity:
        identity = getattr(self.runtime, "bili_identity", None)
        resolver = getattr(identity, "resolve", None)
        if callable(resolver):
            return await resolver(event)
        return ViewerIdentity(uid=str(getattr(event, "uid", "") or ""), nickname=str(getattr(event, "nickname", "") or ""))

    def identity_step_id(self) -> str:
        return "bili_identity" if self.platform == "bilibili" else f"{self.platform}_identity"

    def status(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "room_id": self.configured_room_id(),
            "room_ref": self.configured_room_ref(),
            "listening": self.is_listening(),
        }


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


def normalize_room_ref_for_platform(platform: Any, value: Any) -> dict[str, Any]:
    normalized_platform = normalize_live_platform(platform)
    if normalized_platform == "bilibili":
        room_id = parse_room_id(value)
        return {
            "ok": room_id > 0,
            "platform": "bilibili",
            "room_ref": str(room_id) if room_id > 0 else "",
            "room_id": room_id,
            "message": "" if room_id > 0 else "room_id must be positive",
        }
    room_ref = str(value or "").strip()
    return {
        "ok": bool(room_ref),
        "platform": normalized_platform,
        "room_ref": room_ref,
        "room_id": 0,
        "message": "" if room_ref else "room_ref must not be empty",
    }
