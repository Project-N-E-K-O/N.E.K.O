"""Bounded HTTP and parsing helpers for remote voice management adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..types import ManagementCapabilities, VoiceManagementError, VoiceRuntime, build_voice_scope


def runtime_for(provider, api_key, base_url, *, model="", resource_id="", workspace="", settings=None):
    key = str(api_key or "").strip()
    if not key or "***" in key:
        raise VoiceManagementError("CONFIG_MISSING")
    url = str(base_url or "").strip().rstrip("/")
    scope_id, storage_key = build_voice_scope(provider, key, url, resource_id=resource_id, workspace=workspace)
    return VoiceRuntime(provider, key, url, scope_id, storage_key, model, resource_id, settings or {})


def numeric_cursor(cursor, *, initial=0):
    if cursor is None or cursor == "":
        return initial
    try:
        value = int(cursor)
    except (TypeError, ValueError):
        raise VoiceManagementError("INVALID_CURSOR") from None
    if str(value) != str(cursor) or not initial <= value <= 10000:
        raise VoiceManagementError("INVALID_CURSOR")
    return value


def remote_date(value, *, milliseconds=False):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / (1000 if milliseconds else 1), timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    return str(value) if isinstance(value, str) else None


def voice_rows(value):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise VoiceManagementError("UPSTREAM_INVALID_RESPONSE", 502)
    return value


def filter_voices(voices, query):
    search = str(query or "").strip().casefold()
    return [voice for voice in voices if not search or search in voice.voice_id.casefold() or search in voice.name.casefold()]


async def request_json(method, url, *, mutation=False, allow_not_found=False, **kwargs) -> dict[str, Any] | None:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=60 if mutation else 20, follow_redirects=False) as client:
            response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN" if mutation else "UPSTREAM_TIMEOUT", 504) from None
    except httpx.RequestError:
        raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN" if mutation else "UPSTREAM_UNAVAILABLE", 502) from None
    status = response.status_code
    if status == 404 and allow_not_found:
        return None
    if status == 401:
        raise VoiceManagementError("AUTH_FAILED", 401)
    if status == 403:
        raise VoiceManagementError("PERMISSION_DENIED", 403)
    if status == 429:
        raise VoiceManagementError("RATE_LIMITED", 429)
    if not 200 <= status < 300:
        if mutation and status >= 500:
            raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 502)
        raise VoiceManagementError("UPSTREAM_UNAVAILABLE" if status >= 500 else "UPSTREAM_REJECTED", 502 if status >= 500 else 400)
    try:
        result = response.json()
    except ValueError:
        raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN" if mutation else "UPSTREAM_INVALID_RESPONSE", 502) from None
    if not isinstance(result, dict):
        raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN" if mutation else "UPSTREAM_INVALID_RESPONSE", 502)
    return result


class ImportOnlyAdapter:
    capabilities = ManagementCapabilities(list_voices=True, details=False, overwrite=False)

    def capabilities_for(self, runtime):
        return self.capabilities

    def manual_fields(self, runtime):
        return []

    def validate_voice_id(self, value):
        if not isinstance(value, str):
            raise VoiceManagementError("INVALID_VOICE_ID")
        voice_id = value.strip()
        if not voice_id or len(voice_id) > 512 or any(char.isspace() or ord(char) < 32 or char in "/\\?#" for char in voice_id):
            raise VoiceManagementError("INVALID_VOICE_ID")
        return voice_id

    async def get_voice(self, runtime, voice_id):
        self.validate_voice_id(voice_id)
        return None

    async def overwrite(self, runtime, voice_id, *, audio, filename, before_mutation=None):
        raise VoiceManagementError("OVERWRITE_UNSUPPORTED")
