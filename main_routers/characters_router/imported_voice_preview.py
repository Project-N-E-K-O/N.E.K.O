"""Keep a preview's credentials attached to its imported voice context."""

import asyncio
from fastapi.responses import JSONResponse

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
        try:
            runtime = await asyncio.to_thread(
                providers.get_adapter(self.runtime.provider).resolve_runtime, self.manager,
                voice_data=self.voice_data,
            )
            return runtime.scope_id == self.runtime.scope_id and bool(runtime.api_key)
        except Exception:
            return False
