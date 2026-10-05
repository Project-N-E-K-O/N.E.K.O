"""Keep a preview's credentials attached to its imported voice context."""

import asyncio

from utils.voice_management import providers
from utils.voice_management.runtime_snapshot import VoiceRuntimeSnapshot


class ImportedPreviewConfig(VoiceRuntimeSnapshot):
    async def is_current(self):
        try:
            runtime = await asyncio.to_thread(
                providers.get_adapter(self.runtime.provider).resolve_runtime, self.manager,
            )
            return runtime.scope_id == self.runtime.scope_id and bool(runtime.api_key)
        except Exception:
            return False
