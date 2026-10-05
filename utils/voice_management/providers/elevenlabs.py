"""ElevenLabs v2 pagination; include user/workspace clones and exclude defaults."""

from urllib.parse import quote

from ..types import ManagementCapabilities, RemoteVoice, VoiceManagementError, VoicePage
from ._shared import ImportOnlyAdapter, filter_voices, remote_date, request_json, runtime_for, voice_rows


class ElevenLabsVoiceAdapter(ImportOnlyAdapter):
    provider = "elevenlabs"
    capabilities = ManagementCapabilities(list_voices=True, details=True, overwrite=False)

    def resolve_runtime(self, config_manager):
        return runtime_for(self.provider, config_manager.get_tts_api_key(self.provider), "https://api.elevenlabs.io", model="eleven_v3")

    def import_metadata(self, runtime):
        return {"elevenlabs_base_url": runtime.base_url}

    def validate_voice_id(self, value):
        voice_id = super().validate_voice_id(value)
        if any(char in ":%" for char in voice_id):
            raise VoiceManagementError("INVALID_VOICE_ID")
        return voice_id

    @staticmethod
    def _voice(runtime, row):
        if row.get("category") not in ("cloned", "professional") or not row.get("voice_id"):
            return None
        tuning = row.get("fine_tuning") or {}
        if not isinstance(tuning, dict):
            raise VoiceManagementError("UPSTREAM_INVALID_RESPONSE", 502)
        states = tuning.get("state") or {}
        if not isinstance(states, dict):
            raise VoiceManagementError("UPSTREAM_INVALID_RESPONSE", 502)
        status = "ready"
        if row.get("category") == "professional":
            status = "ready" if "fine_tuned" in states.values() else "processing"
        return RemoteVoice(str(row["voice_id"]), str(row.get("name") or row["voice_id"]), remote_date(row.get("created_at_unix")), status, {"elevenlabs_base_url": runtime.base_url, "raw_voice_id": str(row["voice_id"])}, False)

    async def list_voices(self, runtime, *, cursor=None, query=""):
        params = {"page_size": 100, "voice_type": "non-community", "include_total_count": "false"}
        if cursor:
            if len(str(cursor)) > 2048:
                raise VoiceManagementError("INVALID_CURSOR")
            params["next_page_token"] = cursor
        data = await request_json("GET", f"{runtime.base_url}/v2/voices", headers={"xi-api-key": runtime.api_key}, params=params)
        voices = [voice for row in voice_rows(data.get("voices")) if (voice := self._voice(runtime, row)) is not None]
        token = data.get("next_page_token") if data.get("has_more") else None
        if data.get("has_more") and (not isinstance(token, str) or not token or token == cursor):
            raise VoiceManagementError("UPSTREAM_INVALID_RESPONSE", 502)
        return VoicePage(filter_voices(voices, query), token)

    async def get_voice(self, runtime, voice_id):
        voice_id = self.validate_voice_id(voice_id)
        data = await request_json("GET", f"{runtime.base_url}/v1/voices/{quote(voice_id, safe='')}", headers={"xi-api-key": runtime.api_key}, allow_not_found=True)
        if data is None:
            raise VoiceManagementError("VOICE_NOT_FOUND", 404)
        voice = self._voice(runtime, data)
        if voice is None:
            raise VoiceManagementError("VOICE_NOT_FOUND", 404)
        return voice
