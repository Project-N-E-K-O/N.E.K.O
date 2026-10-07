"""CosyVoice enrollment REST API with per-request credentials and region."""

import io
import re
from datetime import datetime
from urllib.parse import urlsplit

from utils.dashscope_region import DASHSCOPE_DEFAULT_HTTP_API_URL, dashscope_http_url_from_base

from ..types import ManagementCapabilities, RemoteVoice, VoiceManagementError, VoicePage
from ._shared import ImportOnlyAdapter, filter_voices, numeric_cursor, remote_date, request_json, runtime_for, voice_rows


class CosyVoiceAdapter(ImportOnlyAdapter):
    capabilities = ManagementCapabilities(list_voices=True, details=True, overwrite=True)

    def __init__(self, provider="cosyvoice"):
        self.provider = provider

    def resolve_runtime(self, config_manager, *, voice_data=None):
        from utils.api_config_loader import get_cosyvoice_clone_model
        from config import TFLINK_UPLOAD_URL

        config = config_manager.get_cosyvoice_clone_runtime(self.provider)
        core = config_manager.get_core_config()
        configured_model = str(core.get("TTS_MODEL") or "")
        model = configured_model if self.provider == "cosyvoice" and configured_model.startswith("cosyvoice-v") else get_cosyvoice_clone_model(self.provider)
        default = "https://dashscope-intl.aliyuncs.com/api/v1" if self.provider == "cosyvoice_intl" else DASHSCOPE_DEFAULT_HTTP_API_URL
        # Persist the effective SDK endpoint so management, synthesis and scope
        # agree even when the Qwen profile contains a custom proxy URL.
        base_url = dashscope_http_url_from_base(config.get("base_url") or "", default)
        # The allowlisted endpoint must encrypt the initial Bearer request too.
        base_url = urlsplit(base_url)._replace(scheme="https").geturl()
        return runtime_for(self.provider, config.get("api_key"), base_url, model=model, settings={"upload_url": TFLINK_UPLOAD_URL})

    def import_metadata(self, runtime):
        return {"dashscope_base_url": runtime.base_url, "clone_model": runtime.model}

    def manual_fields(self, runtime):
        return [{"key": "clone_model", "label_key": "voice.remote.model", "required": True, "default_value": runtime.model}]

    def compare_revisions(self, current, previous):
        # gmt_modified is a timestamp. Never sort arbitrary strings or mix
        # offset-bearing times with timestamps whose timezone is unspecified.
        timestamps = []
        for value in (current, previous):
            if not isinstance(value, str) or re.fullmatch(
                r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?", value,
            ) is None:
                return None
            try:
                timestamps.append(datetime.fromisoformat(value))
            except ValueError:
                return None
        if (timestamps[0].tzinfo is None) != (timestamps[1].tzinfo is None):
            return None
        return (timestamps[0] > timestamps[1]) - (timestamps[0] < timestamps[1])

    async def _call(self, runtime, action, *, mutation=False, **fields):
        data = await request_json("POST", f"{runtime.base_url}/services/audio/tts/customization", headers={"Authorization": f"Bearer {runtime.api_key}"}, json={"model": "voice-enrollment", "input": {"action": action, **fields}}, mutation=mutation)
        if data.get("code"):
            code = str(data["code"])
            details = {"attempt_outcome": "unknown"} if mutation else None
            if code in ("InvalidApiKey", "InvalidAuthentication"):
                raise VoiceManagementError("AUTH_FAILED", 401, details)
            if code in ("AccessDenied", "Forbidden"):
                raise VoiceManagementError("PERMISSION_DENIED", 403, details)
            if code in ("VoiceNotFound", "InvalidVoiceId", "InvalidVoiceID"):
                raise VoiceManagementError("VOICE_NOT_FOUND", 404, details)
            raise VoiceManagementError("UPSTREAM_REJECTED", details=details)
        output = data.get("output")
        if not isinstance(output, dict):
            raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN" if mutation else "UPSTREAM_INVALID_RESPONSE", 502)
        return output

    def _voice(self, runtime, row, voice_id=""):
        raw_id = str(row.get("voice_id") or voice_id)
        status = {"OK": "ready", "UNDEPLOYED": "unavailable"}.get(str(row.get("status", "")), "unknown")
        metadata = self.import_metadata(runtime)
        if row.get("gmt_modified"):
            metadata["remote_revision"] = str(row["gmt_modified"])
        if row.get("target_model"):
            metadata["clone_model"] = str(row["target_model"])
        return RemoteVoice(raw_id, str(row.get("name") or raw_id), remote_date(row.get("gmt_create")), status, metadata, status == "ready")

    async def list_voices(self, runtime, *, cursor=None, query=""):
        page = numeric_cursor(cursor)
        output = await self._call(runtime, "list_voice", page_index=page, page_size=100)
        rows = voice_rows(output.get("voice_list"))
        voices = [self._voice(runtime, row) for row in rows if row.get("voice_id")]
        return VoicePage(filter_voices(voices, query), str(page + 1) if len(rows) == 100 else None)

    async def get_voice(self, runtime, voice_id):
        voice_id = self.validate_voice_id(voice_id)
        output = await self._call(runtime, "query_voice", voice_id=voice_id)
        if not output or not output.get("target_model"):
            raise VoiceManagementError("VOICE_NOT_FOUND", 404)
        return self._voice(runtime, output, voice_id)

    async def overwrite(self, runtime, voice_id, *, audio, filename, before_mutation=None):
        from utils.voice_clone import QwenVoiceCloneClient, QwenVoiceCloneError

        current = await self.get_voice(runtime, voice_id)
        if not current.can_overwrite:
            raise VoiceManagementError("VOICE_NOT_READY")
        uploader = QwenVoiceCloneClient(runtime.api_key, runtime.settings["upload_url"], runtime.base_url)
        try:
            url = await uploader.upload_file(io.BytesIO(audio), filename)
        except QwenVoiceCloneError:
            raise VoiceManagementError("UPLOAD_FAILED", 502) from None
        # Upload does not mutate the voice. Recheck ownership immediately before update.
        if before_mutation:
            await before_mutation(current)
        await self._call(runtime, "update_voice", mutation=True, voice_id=voice_id, url=url)
        try:
            updated = await self.get_voice(runtime, voice_id)
        except VoiceManagementError:
            # The update was acknowledged; a failed follow-up query is not a rollback.
            return RemoteVoice(voice_id, current.name, current.created_at, "processing", current.metadata, False)
        previous_revision = current.metadata.get("remote_revision")
        updated_revision = updated.metadata.get("remote_revision")
        if updated.status == "ready" and self.compare_revisions(updated_revision, previous_revision) != 1:
            return RemoteVoice(voice_id, updated.name, updated.created_at, "processing", updated.metadata, False)
        return updated
