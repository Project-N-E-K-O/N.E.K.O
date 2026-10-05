"""MiniMax's category-filtered get_voice endpoint (CN and international)."""

import re

from ..types import ManagementCapabilities, RemoteVoice, VoiceManagementError, VoicePage
from ._shared import ImportOnlyAdapter, filter_voices, remote_date, request_json, runtime_for, voice_rows


class MiniMaxVoiceAdapter(ImportOnlyAdapter):
    capabilities = ManagementCapabilities(list_voices=True, details=True, overwrite=False)

    def __init__(self, provider="minimax"):
        self.provider = provider

    def resolve_runtime(self, config_manager, *, voice_data=None):
        from utils.tts.providers.minimax import get_minimax_base_url

        return runtime_for(self.provider, config_manager.get_tts_api_key(self.provider), get_minimax_base_url(self.provider))

    def import_metadata(self, runtime):
        return {"minimax_base_url": runtime.base_url}

    def validate_voice_id(self, value):
        voice_id = super().validate_voice_id(value)
        if not 8 <= len(voice_id) <= 256 or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*[A-Za-z0-9]", voice_id) is None:
            raise VoiceManagementError("INVALID_VOICE_ID")
        return voice_id

    async def get_voice(self, runtime, voice_id):
        voice_id = self.validate_voice_id(voice_id)
        page = await self.list_voices(runtime)
        # Unused clones are omitted by the vendor; absence is not proof of invalid ID.
        return next((voice for voice in page.voices if voice.voice_id == voice_id), None)

    async def list_voices(self, runtime, *, cursor=None, query=""):
        if cursor:
            raise VoiceManagementError("INVALID_CURSOR")
        data = await request_json("POST", f"{runtime.base_url}/v1/get_voice", headers={"Authorization": f"Bearer {runtime.api_key}"}, json={"voice_type": "voice_cloning"})
        base_resp = data.get("base_resp")
        if not isinstance(base_resp, dict) or base_resp.get("status_code") not in (0, "0"):
            code = str(base_resp.get("status_code", "")) if isinstance(base_resp, dict) else ""
            error = "AUTH_FAILED" if code in ("1004", "2049") else "RATE_LIMITED" if code == "1002" else "UPSTREAM_REJECTED"
            raise VoiceManagementError(error, 401 if error == "AUTH_FAILED" else 429 if error == "RATE_LIMITED" else 400)
        voices = [RemoteVoice(str(row["voice_id"]), str(row.get("voice_name") or row["voice_id"]), remote_date(row.get("created_time")), "ready", {"minimax_base_url": runtime.base_url}, False)
                  for row in voice_rows(data.get("voice_cloning")) if row.get("voice_id")]
        return VoicePage(filter_voices(voices, query))
