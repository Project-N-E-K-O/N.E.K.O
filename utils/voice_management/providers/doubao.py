"""Signed Doubao management listing and same-speaker clone updates."""

import base64
import hashlib
import hmac
import io
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from ..types import ManagementCapabilities, RemoteVoice, VoiceManagementError, VoicePage
from ._shared import ImportOnlyAdapter, filter_voices, remote_date, request_json, runtime_for, voice_rows

_STATES = ("Success", "Active", "Training", "Unknown", "Expired", "Reclaimed")


def _cursor(state_index, token):
    return "db:" + base64.urlsafe_b64encode(json.dumps([state_index, token], separators=(",", ":")).encode()).decode().rstrip("=")


def _parse_cursor(value):
    if not value:
        return 0, None
    try:
        if not isinstance(value, str) or not value.startswith("db:") or len(value) > 2048:
            raise ValueError
        encoded = value[3:]
        decoded = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        index, token = decoded
        if type(index) is not int or not 0 <= index < len(_STATES) or (token is not None and not isinstance(token, str)):
            raise ValueError
        return index, token
    except (ValueError, TypeError):
        raise VoiceManagementError("INVALID_CURSOR") from None


def _signed_headers(settings, body, query, *, timestamp=None):
    date = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    day = date[:8]
    region = "cn-beijing" if settings.get("project_name") else "cn-north-1"
    scope = f"{day}/{region}/speech_saas_prod/request"
    body_hash = hashlib.sha256(body).hexdigest()
    signed_headers = "host;x-content-sha256;x-date"
    canonical = "\n".join(("POST", "/", urlencode(sorted(query.items())), f"host:open.volcengineapi.com\nx-content-sha256:{body_hash}\nx-date:{date}\n", signed_headers, body_hash))
    to_sign = "\n".join(("HMAC-SHA256", date, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    key = settings["secret_key"].encode()
    for part in (day, region, "speech_saas_prod", "request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json; charset=utf-8", "X-Date": date, "X-Content-Sha256": body_hash, "Authorization": f"HMAC-SHA256 Credential={settings['access_key']}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"}


class DoubaoVoiceAdapter(ImportOnlyAdapter):
    provider = "doubao_tts"
    capabilities = ManagementCapabilities(list_voices=True, details=True, overwrite=True)

    def resolve_runtime(self, config_manager, *, voice_data=None):
        from utils.doubao_tts import DOUBAO_TTS_DEFAULT_BASE_URL, DOUBAO_TTS_DEFAULT_RESOURCE_ID

        core = config_manager.load_json_config("core_config.json", {})
        active = str(core.get("ttsModelProvider") or "") == self.provider
        # Only server-loaded records may supply their previously captured endpoint.
        # Selecting another provider must not discard it; explicitly changing the
        # active Doubao settings, effective synthesis key or project still isolates it.
        saved = voice_data or {}
        base_url = (core.get("ttsModelUrl") if active else saved.get("doubao_base_url")) or DOUBAO_TTS_DEFAULT_BASE_URL
        resource = (core.get("ttsModelId") if active else saved.get("doubao_resource_id")) or DOUBAO_TTS_DEFAULT_RESOURCE_ID
        settings = {"access_key": str(core.get("doubaoVoiceManagementAccessKey") or "").strip(), "secret_key": str(core.get("doubaoVoiceManagementSecretKey") or "").strip(), "app_id": str(core.get("doubaoVoiceManagementAppId") or "").strip(), "project_name": str(core.get("doubaoVoiceManagementProjectName") or "").strip()}
        # Management queries explicitly select one ownership namespace. A key
        # rotation within it must not move voices, while selecting another
        # app/project must not reuse the old project's local ID registrations.
        workspace = "project:" + settings["project_name"] if settings["project_name"] else "app:" + settings["app_id"] if settings["app_id"] else ""
        return runtime_for(self.provider, config_manager.get_tts_api_key(self.provider), base_url, resource_id=resource, workspace=workspace, settings=settings)

    def capabilities_for(self, runtime):
        settings = runtime.settings
        listing = all(settings.get(field) and "***" not in settings[field] for field in ("access_key", "secret_key")) and bool(settings.get("app_id") or settings.get("project_name"))
        return ManagementCapabilities(list_voices=listing, details=listing, overwrite=True)

    def import_metadata(self, runtime):
        return {"doubao_base_url": runtime.base_url, "doubao_resource_id": runtime.resource_id, "clone_model": runtime.resource_id}

    def manual_fields(self, runtime):
        return [{"key": "doubao_resource_id", "label_key": "voice.remote.resource", "required": True, "default_value": runtime.resource_id}]

    def compare_revisions(self, current, previous):
        # AppID and ProjectName APIs document V1 and v1 respectively; both
        # represent the speaker's training count rather than an opaque tag.
        counts = []
        for value in (current, previous):
            match = re.fullmatch(r"[vV]?([0-9]{1,18})", value) if isinstance(value, str) else None
            if match is None:
                return None
            counts.append(int(match[1]))
        return (counts[0] > counts[1]) - (counts[0] < counts[1])

    def validate_voice_id(self, value):
        voice_id = super().validate_voice_id(value)
        if not voice_id.startswith("S_") or len(voice_id) <= 2:
            raise VoiceManagementError("INVALID_VOICE_ID")
        return voice_id

    def _voice(self, runtime, row):
        voice_id = str(row.get("SpeakerID") or "")
        state = str(row.get("State") or "")
        status = {"Success": "ready", "Active": "ready", "Training": "processing", "Unknown": "unavailable", "Expired": "unavailable", "Reclaimed": "unavailable"}.get(state, "unknown")
        remaining = row.get("AvailableTrainingTimes")
        can_update = state == "Success" and isinstance(remaining, (int, float)) and remaining > 0
        metadata = self.import_metadata(runtime)
        if row.get("Version") is not None:
            metadata["remote_revision"] = str(row["Version"])
        return RemoteVoice(voice_id, str(row.get("Alias") or voice_id), remote_date(row.get("CreateTime"), milliseconds=True), status, metadata, can_update)

    async def _list(self, runtime, *, cursor=None, voice_id=None, state="Success"):
        if not self.capabilities_for(runtime).list_voices:
            raise VoiceManagementError("MANAGEMENT_CONFIG_MISSING")
        settings = runtime.settings
        project = settings.get("project_name")
        query = {"Action": "BatchListMegaTTSTrainStatus", "Version": "2025-05-21" if project else "2023-11-07"}
        payload = {"ProjectName": project, "State": state} if project else {"AppID": settings["app_id"]}
        if cursor:
            if len(str(cursor)) > 2048:
                raise VoiceManagementError("INVALID_CURSOR")
            payload["NextToken"] = cursor
            payload["MaxResults"] = 100
        else:
            payload.update({"PageNumber": 1, "PageSize": 100})
        if voice_id:
            payload["SpeakerIDs"] = [voice_id]
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        data = await request_json("POST", "https://open.volcengineapi.com", params=query, content=body, headers=_signed_headers(settings, body, query))
        response_metadata = data.get("ResponseMetadata") or {}
        if not isinstance(response_metadata, dict):
            raise VoiceManagementError("UPSTREAM_INVALID_RESPONSE", 502)
        error = response_metadata.get("Error")
        if error:
            if not isinstance(error, dict):
                raise VoiceManagementError("UPSTREAM_INVALID_RESPONSE", 502)
            code = str(error.get("Code") or "")
            name = "PERMISSION_DENIED" if code.startswith("OperationDenied") else "AUTH_FAILED" if "Signature" in code or "AccessKey" in code else "UPSTREAM_REJECTED"
            raise VoiceManagementError(name, 403 if name == "PERMISSION_DENIED" else 401 if name == "AUTH_FAILED" else 400)
        result = data.get("Result")
        if not isinstance(result, dict):
            raise VoiceManagementError("UPSTREAM_INVALID_RESPONSE", 502)
        rows = voice_rows(result.get("Statuses"))
        token = result.get("NextToken") or None
        if token and (not isinstance(token, str) or token == cursor):
            raise VoiceManagementError("UPSTREAM_INVALID_RESPONSE", 502)
        return VoicePage([self._voice(runtime, row) for row in rows if row.get("SpeakerID")], token)

    async def list_voices(self, runtime, *, cursor=None, query=""):
        if not runtime.settings.get("project_name"):
            page = await self._list(runtime, cursor=cursor)
            return VoicePage(filter_voices(page.voices, query), page.next_cursor)
        # The new API requires an exact State. Carry that state in our cursor,
        # and traverse the finite state set without losing Active voices.
        index, token = _parse_cursor(cursor)
        while index < len(_STATES):
            page = await self._list(runtime, cursor=token, state=_STATES[index])
            following = _cursor(index, page.next_cursor) if page.next_cursor else _cursor(index + 1, None) if index + 1 < len(_STATES) else None
            if page.voices or page.next_cursor or following is None:
                return VoicePage(filter_voices(page.voices, query), following)
            index += 1
            token = None
        return VoicePage([])

    async def get_voice(self, runtime, voice_id):
        voice_id = self.validate_voice_id(voice_id)
        if not self.capabilities_for(runtime).details:
            return None
        states = _STATES if runtime.settings.get("project_name") else ("Success",)
        for state in states:
            page = await self._list(runtime, voice_id=voice_id, state=state)
            voice = next((voice for voice in page.voices if voice.voice_id == voice_id), None)
            if voice:
                return voice
        return None

    async def overwrite(self, runtime, voice_id, *, audio, filename, before_mutation=None):
        from utils.doubao_tts import DoubaoVoiceCloneClient, DoubaoTtsError

        voice_id = self.validate_voice_id(voice_id)
        current = await self.get_voice(runtime, voice_id)
        if current is None or not current.can_overwrite:
            raise VoiceManagementError("OVERWRITE_UNSUPPORTED")
        if before_mutation:
            await before_mutation(current)
        client = DoubaoVoiceCloneClient(runtime.api_key, base_url=runtime.base_url, resource_id=runtime.resource_id)
        try:
            returned = await client.clone_voice(io.BytesIO(audio), speaker_id=voice_id, display_name=current.name, audio_format="wav")
        except DoubaoTtsError:
            # Existing client errors contain response bodies; never forward their text.
            # This legacy client does not expose the response status/code as
            # structured fields; fail closed rather than infer non-commit.
            raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 502) from None
        if returned != voice_id:
            raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 502)
        try:
            updated = await self.get_voice(runtime, voice_id)
        except VoiceManagementError:
            updated = None
        previous_revision = current.metadata.get("remote_revision")
        updated_revision = updated.metadata.get("remote_revision") if updated else None
        if updated and updated.status == "ready" and self.compare_revisions(updated_revision, previous_revision) == 1:
            return updated
        return RemoteVoice(voice_id, current.name, current.created_at, "processing", updated.metadata if updated else current.metadata, False)
