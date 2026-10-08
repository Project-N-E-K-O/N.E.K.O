"""Isolated process exercising the real overwrite service and JSON persistence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from tests.unit.test_voice_management_service import Adapter
from tests.unit.test_voice_management_storage import MemoryVoiceManager
from utils.voice_management import providers, service
from utils.voice_management.types import RemoteVoice, VoiceManagementError
from utils.file_utils import atomic_write_json


def barrier(stage):
    print("BARRIER:" + stage, flush=True)
    if not sys.stdin.readline():
        raise RuntimeError("Parent closed control pipe")


class FileManager(MemoryVoiceManager):
    def __init__(self, directory, stage):
        super().__init__()
        self.path = Path(directory) / "voices.json"
        self.key = "isolated-fake-key"
        self.management_secret = "isolated-fake-management-key"
        self.stage = stage

    def load_voice_storage(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save_voice_storage(self, storage):
        records = [record for bucket in storage.values() for record in bucket.values()]
        final = any(record.get("overwrite_status") == "completed" for record in records)
        pending = any(record.get("overwrite_status") == "processing"
                      and record.get("overwrite_submission_phase") == "prepared" for record in records)
        if final and self.stage == "before_save":
            barrier(self.stage)
        atomic_write_json(self.path, storage)
        if pending and self.stage == "prepared":
            barrier(self.stage)

    def transition_imported_voice_overwrite(self, *args, **kwargs):
        result = super().transition_imported_voice_overwrite(*args, **kwargs)
        if result.applied and kwargs["action"] == "submit" and self.stage == "converted":
            barrier(self.stage)
        return result


class HttpAdapter(Adapter):
    def __init__(self, url, stage):
        super().__init__()
        self.url = url
        self.stage = stage

    async def overwrite(self, runtime, voice_id, *, audio, filename, before_mutation=None):
        await before_mutation(self.remote)

        async def chunks():
            yield audio[:8192]
            if self.stage == "sending":
                await asyncio.to_thread(barrier, self.stage)
            yield audio[8192:]

        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.post(self.url, content=chunks(), headers={"Content-Length": str(len(audio))})
            response.raise_for_status()
        if self.stage == "accepted":
            await asyncio.to_thread(barrier, self.stage)
        return RemoteVoice(voice_id, status="ready", metadata={"remote_revision": "2"}, can_overwrite=True)


async def run(directory, url, stage, action):
    cm = FileManager(directory, stage)
    adapter = HttpAdapter(url, stage)
    providers.get_adapter = lambda provider: adapter
    runtime = adapter.resolve_runtime(cm)
    if not cm.load_voice_storage():
        cm.import_remote_voice(runtime.scope_id, runtime.provider, "remote-original", {
            "can_overwrite": True, "remote_revision": "1",
        })
    record = next(record for bucket in cm.load_voice_storage().values() for record in bucket.values())
    ref = record["local_ref"]
    token = service.context_token(runtime)
    try:
        if action == "refresh":
            result = await service.refresh_overwrite_status(adapter, cm, ref, token=token)
        elif action == "abandon":
            from utils.voice_management.overwrite_recovery import abandon_unknown_overwrite
            result = await abandon_unknown_overwrite(
                adapter, cm, ref, token=token, operation_id=record["overwrite_operation_id"],
                record_revision=record["_record_revision"],
            )
        elif action == "recover":
            from utils.voice_management.overwrite_recovery import recover_prepared_overwrite
            result = await recover_prepared_overwrite(
                adapter, cm, ref, token=token, operation_id=record["overwrite_operation_id"],
                record_revision=record["_record_revision"],
            )
        else:
            result = await service.overwrite_remote_voice(
                adapter, cm, ref, token=token, audio=b"a" * 16384, filename="isolated.wav",
            )
        print(json.dumps({"result": result}), flush=True)
    except VoiceManagementError as exc:
        print(json.dumps({"code": exc.code, "details": exc.details}), flush=True)


if __name__ == "__main__":
    asyncio.run(run(*sys.argv[1:]))
