"""Platform-neutral live provider router.

This keeps runtime code from reaching into a concrete live ingest module.
Bilibili owns the production transport today; Douyin fills the same provider
shape up to the documented read-only degraded states.
"""

from __future__ import annotations

from typing import Any

from .contracts import LiveRoomStatus, ViewerIdentity, normalize_live_platform, parse_room_id

try:
    from ..modules.douyin_live_ingest.room_ref import parse_douyin_room_ref
except ImportError:
    def parse_douyin_room_ref(value: Any) -> Any:
        room_ref = str(value or "").strip()
        return type(
            "ParsedDouyinRoomRef",
            (),
            {
                "ok": bool(room_ref),
                "room_ref": room_ref,
                "message": "" if room_ref else "room_ref must not be empty",
            },
        )()


class LiveProviderRouter:
    """Route live input calls to the selected platform provider."""

    id = "live_provider"

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    @property
    def platform(self) -> str:
        return normalize_live_platform(getattr(self.runtime.config, "live_platform", "bilibili"))

    def configured_room_ref(self) -> str:
        raw_room_ref = getattr(self.runtime.config, "live_room_ref", "")
        if self.platform == "douyin":
            parsed = parse_douyin_room_ref(raw_room_ref)
            return parsed.room_ref if parsed.ok else ""
        room_ref = _safe_public_room_ref(raw_room_ref)
        if self.platform != "bilibili":
            return room_ref
        if room_ref:
            return room_ref
        room_id = _safe_public_room_id(getattr(self.runtime.config, "live_room_id", 0))
        return str(room_id) if room_id > 0 else ""

    def configured_room_id(self) -> int:
        if self.platform == "bilibili":
            room_id = _safe_public_room_id(getattr(self.runtime.config, "live_room_id", 0))
            if room_id > 0:
                return room_id
            return parse_room_id(self.configured_room_ref())
        return 0

    def normalize_room_ref(self, value: Any) -> dict[str, Any]:
        return normalize_room_ref_for_platform(self.platform, value)

    def _ingest(self) -> Any | None:
        return self.provider_for(self.platform)

    def provider_for(self, platform: Any) -> Any | None:
        """Return the concrete provider for a captured platform.

        Callers that are changing config must capture ownership before activating
        the new config; resolving through ``self.platform`` afterwards can stop
        the new provider while leaving the old one alive.
        """

        normalized = normalize_live_platform(platform)
        if normalized == "bilibili":
            return getattr(self.runtime, "bili_live_ingest", None)
        if normalized == "douyin":
            return getattr(self.runtime, "douyin_live_ingest", None)
        return None

    def _identity(self) -> Any | None:
        if self.platform == "bilibili":
            return getattr(self.runtime, "bili_identity", None)
        if self.platform == "douyin":
            return getattr(self.runtime, "douyin_identity", None)
        return None

    def is_listening(self) -> bool:
        ingest = self._ingest()
        checker = getattr(ingest, "is_listening", None)
        if not callable(checker):
            return False
        try:
            return checker() is True
        except Exception:
            return False

    def listener_state(self) -> dict[str, Any]:
        ingest = self._ingest()
        state_fn = getattr(ingest, "listener_state", None)
        if not callable(state_fn):
            return {}
        try:
            data = state_fn()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    async def start_listening(self, room_ref: Any) -> bool:
        normalized = self.normalize_room_ref(room_ref)
        if not normalized.get("ok"):
            return False
        ingest = self._ingest()
        starter = getattr(ingest, "start_listening", None)
        if not callable(starter):
            return False
        if self.platform == "bilibili":
            return await starter(int(normalized.get("room_id") or 0)) is True
        return await starter(str(normalized.get("room_ref") or "")) is True

    async def stop_listening(self) -> None:
        ingest = self._ingest()
        stopper = getattr(ingest, "stop_listening", None)
        if callable(stopper):
            await stopper()

    def normalize(self, payload: dict[str, Any]) -> Any:
        ingest = self._ingest()
        normalizer = getattr(ingest, "normalize", None)
        if not callable(normalizer):
            raise ValueError(f"unsupported live platform: {self.platform}")
        return normalizer(payload)

    async def lookup_room_status(self, room_ref: Any) -> LiveRoomStatus:
        normalized = self.normalize_room_ref(room_ref)
        if not normalized.get("ok"):
            return LiveRoomStatus(room_id=0, ok=False, message=str(normalized.get("message") or "room not configured"))
        ingest = self._ingest()
        lookup = getattr(ingest, "lookup_room_status", None)
        if not callable(lookup):
            return LiveRoomStatus(room_id=0, ok=False, message=f"unsupported live platform: {self.platform}")
        if self.platform == "bilibili":
            return await lookup(int(normalized.get("room_id") or 0))
        return await lookup(str(normalized.get("room_ref") or ""))

    async def resolve_identity(self, event: Any) -> ViewerIdentity:
        identity = self._identity()
        resolver = getattr(identity, "resolve", None)
        if callable(resolver):
            return await resolver(event)
        return ViewerIdentity(uid=str(getattr(event, "uid", "") or ""), nickname=str(getattr(event, "nickname", "") or ""))

    def identity_step_id(self) -> str:
        return "bili_identity" if self.platform == "bilibili" else f"{self.platform}_identity"

    def status(self) -> dict[str, Any]:
        ingest = self._ingest()
        status_fn = getattr(ingest, "status", None)
        try:
            data = status_fn() if callable(status_fn) else {}
        except Exception:
            data = {}
        status = data if isinstance(data, dict) else {}
        status["platform"] = self.platform
        status["room_ref"] = _safe_status_room_ref(
            self.platform,
            status.get("room_ref"),
            fallback=self.configured_room_ref(),
        )
        status["room_id"] = _safe_status_room_id(
            self.platform,
            status.get("room_id"),
            fallback=self.configured_room_id(),
        )
        if status.get("listening") is not True and status.get("listening") is not False:
            status["listening"] = self.is_listening()
        return status


class _LegacyBilibiliIdentityProvider:
    """Compatibility adapter for older tests and sandboxes without live_provider."""

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
    if normalized_platform == "douyin":
        parsed = parse_douyin_room_ref(value)
        return {
            "ok": parsed.ok,
            "platform": "douyin",
            "room_ref": parsed.room_ref,
            "room_id": 0,
            "message": parsed.message,
        }
    room_ref = _safe_public_room_ref(value)
    return {
        "ok": bool(room_ref),
        "platform": normalized_platform,
        "room_ref": room_ref,
        "room_id": 0,
        "message": "" if room_ref else "room_ref must be configured",
    }


def _safe_public_room_ref(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value) if value > 0 else ""
    if isinstance(value, str):
        return value.strip()
    return ""


def _safe_public_room_id(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, str):
        return parse_room_id(value)
    return 0


def _safe_status_room_ref(platform: str, value: Any, *, fallback: str = "") -> str:
    normalized = normalize_room_ref_for_platform(platform, value)
    if normalized.get("ok"):
        return _safe_public_room_ref(normalized.get("room_ref"))
    fallback_normalized = normalize_room_ref_for_platform(platform, fallback)
    return _safe_public_room_ref(fallback_normalized.get("room_ref")) if fallback_normalized.get("ok") else ""


def _safe_status_room_id(platform: str, value: Any, *, fallback: int = 0) -> int:
    if platform != "bilibili":
        return 0
    room_id = _safe_public_room_id(value)
    return room_id if room_id > 0 else _safe_public_room_id(fallback)
