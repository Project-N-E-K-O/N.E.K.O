"""GLM private voice listing, according to the official VoiceListResponse."""

from ..types import ManagementCapabilities, RemoteVoice, VoiceManagementError, VoicePage
from ._shared import ImportOnlyAdapter, filter_voices, remote_date, request_json, runtime_for, voice_rows


class GlmVoiceAdapter(ImportOnlyAdapter):
    provider = "glm_tts"
    capabilities = ManagementCapabilities(list_voices=True, details=True, overwrite=False)

    def resolve_runtime(self, config_manager, *, voice_data=None):
        from utils.glm_tts import GLM_TTS_DEFAULT_BASE_URL

        return runtime_for(self.provider, config_manager.get_tts_api_key(self.provider), GLM_TTS_DEFAULT_BASE_URL, model="glm-tts")

    def import_metadata(self, runtime):
        return {"glm_base_url": runtime.base_url, "clone_model": "glm-tts-clone"}

    async def get_voice(self, runtime, voice_id):
        voice_id = self.validate_voice_id(voice_id)
        page = await self.list_voices(runtime)
        voice = next((voice for voice in page.voices if voice.voice_id == voice_id), None)
        if voice is None:
            raise VoiceManagementError("VOICE_NOT_FOUND", 404)
        return voice

    async def list_voices(self, runtime, *, cursor=None, query=""):
        if cursor:
            raise VoiceManagementError("INVALID_CURSOR")
        # Filter locally as well so the search field can match IDs, not only names.
        data = await request_json("GET", f"{runtime.base_url}/voice/list", headers={"Authorization": f"Bearer {runtime.api_key}"}, params={"voiceType": "PRIVATE"})
        if data.get("error"):
            raise VoiceManagementError("UPSTREAM_REJECTED")
        voices = [RemoteVoice(str(row["voice"]), str(row.get("voice_name") or row["voice"]), remote_date(row.get("create_time")), "ready", {"glm_base_url": runtime.base_url, "clone_model": "glm-tts-clone"}, False)
                  for row in voice_rows(data.get("voice_list")) if row.get("voice") and row.get("voice_type") == "PRIVATE"]
        return VoicePage(filter_voices(voices, query))
