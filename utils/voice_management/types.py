"""Shared contracts; importing these does not load providers or TTS workers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


def build_voice_scope(
    provider: str,
    api_key: str,
    base_url: str,
    model: str = "",
    resource_id: str = "",
    workspace: str = "",
) -> tuple[str, str]:
    """Separate synthesis ownership without exposing a credential fragment.

    The synthesis model belongs to voice metadata, not account identity. A
    changed management credential similarly must not move an imported voice.
    """
    identity = [provider, api_key, base_url.rstrip("/"), resource_id, workspace]
    scope_id = hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    return scope_id, "__REMOTE_VOICES__" + scope_id


@dataclass(frozen=True)
class VoiceRuntime:
    provider: str
    api_key: str = field(repr=False)
    base_url: str
    scope_id: str
    storage_key: str
    model: str = ""
    resource_id: str = ""
    settings: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ManagementCapabilities:
    list_voices: bool = False
    details: bool = False
    overwrite: bool = False
    manual_import: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {
            "list": self.list_voices,
            "details": self.details,
            "overwrite": self.overwrite,
            "manual_import": self.manual_import,
        }


@dataclass(frozen=True)
class RemoteVoice:
    voice_id: str
    name: str = ""
    created_at: str | None = None
    status: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)
    can_overwrite: bool = False


@dataclass(frozen=True)
class VoicePage:
    voices: list[RemoteVoice]
    next_cursor: str | None = None


class VoiceManagementError(Exception):
    """Only stable public codes and explicitly safe details leave the adapter."""

    def __init__(
        self, code: str, status_code: int = 400, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


class VoiceManagementAdapter(Protocol):
    capabilities: ManagementCapabilities

    def resolve_runtime(self, config_manager: Any, *, voice_data: dict | None = None) -> VoiceRuntime: ...

    def capabilities_for(self, runtime: VoiceRuntime) -> ManagementCapabilities: ...

    def import_metadata(self, runtime: VoiceRuntime) -> dict[str, Any]: ...

    def manual_fields(self, runtime: VoiceRuntime) -> list[dict[str, Any]]: ...

    def validate_voice_id(self, value: str) -> str: ...

    async def list_voices(
        self, runtime: VoiceRuntime, *, cursor: str | None = None, query: str = ""
    ) -> VoicePage: ...

    async def get_voice(
        self, runtime: VoiceRuntime, voice_id: str
    ) -> RemoteVoice | None: ...

    async def overwrite(
        self,
        runtime: VoiceRuntime,
        voice_id: str,
        *,
        audio: bytes,
        filename: str,
        before_mutation: Callable[[RemoteVoice], Awaitable[None]] | None = None,
    ) -> RemoteVoice: ...
