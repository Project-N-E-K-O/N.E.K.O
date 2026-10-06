"""Keep a preview's credentials attached to its imported voice context."""

import asyncio
from fastapi.responses import JSONResponse

from utils.config_manager.imported_voices import VOICE_STORAGE_LOCK
from utils.voice_management import providers
from utils.voice_management.runtime_snapshot import VoiceRuntimeSnapshot


async def preview_response(preview, payload):
    """Do not deliver imported audio after its captured context was retired."""
    if preview is not None and not await preview.is_current():
        return JSONResponse({
            "success": False, "error": "IMPORTED_VOICE_UNAVAILABLE", "code": "IMPORTED_VOICE_UNAVAILABLE",
        }, status_code=409)
    return payload


class ImportedPreviewConfig(VoiceRuntimeSnapshot):
    def __init__(self, manager, runtime, *, voice_data=None):
        super().__init__(manager, runtime)
        self.voice_data = dict(voice_data or {})

    async def is_current(self):
        def check():
            # Serialize the delivery checkpoint with local delete/update commits.
            # Provider I/O has already finished and never runs under this lock.
            with VOICE_STORAGE_LOCK:
                current = self.manager.get_imported_voice(self.voice_data["local_ref"])
                if current is None or any(
                    current.get(field) != self.voice_data.get(field)
                    for field in (
                        "scope_id", "provider", "remote_voice_id", "overwrite_operation_id",
                        "overwrite_status", "remote_revision",
                    )
                ):
                    return False
                runtime = providers.get_adapter(self.runtime.provider).resolve_runtime(
                    self.manager, voice_data=current,
                )
                return runtime.scope_id == self.runtime.scope_id and bool(runtime.api_key)

        try:
            return await asyncio.to_thread(check)
        except Exception:
            return False
