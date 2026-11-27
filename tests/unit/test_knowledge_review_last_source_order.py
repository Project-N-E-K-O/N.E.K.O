"""Keep last-source removal ordered against imports still being parsed."""

import asyncio
import json
import threading

import pytest

from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_review_round_twenty_ordering import ObservedLock
from tests.unit.test_knowledge_subsystem import _pack
from tests.unit.test_knowledge_unified_core import _finished


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("remove_last_source", [True, False])
@pytest.mark.parametrize("removal_arrives_first", [True, False])
async def test_removal_keeps_its_order_against_an_import_in_parsing(
    tmp_path, monkeypatch, remove_last_source, removal_arrives_first,
):
    service = KnowledgeService(tmp_path)
    await service.start()
    entered, release = threading.Event(), threading.Event()
    incoming = removal = None
    lock = None
    holding_lock = False
    try:
        original = b"Original lighthouse reference."
        preview = (await service.preview_source("notes.txt", original))["preview"]
        installed = await _finished(service, await service.commit_source(preview["preview_id"], preview["sha256"]))
        pack_id = installed["pack_id"]
        source = next((tmp_path / "sources" / pack_id / "originals").rglob("original"))
        frozen = tmp_path / "jobs" / (installed["job_id"] + ".pack.json")
        frozen_bytes = frozen.read_bytes()
        prepare = service._prepare_import

        def hold_parse(*args, **kwargs):
            # Pause the real parser worker before it reserves pack admission;
            # removal's locked recheck cannot yet see an incoming job.
            entered.set()
            assert release.wait(5), "parser was not released"
            return prepare(*args, **kwargs)

        monkeypatch.setattr(service, "_prepare_import", hold_parse)
        lock = ObservedLock()
        service._write_lock = lock
        await lock.acquire()
        holding_lock = True
        raw = json.dumps(_pack(pack_id)).encode()

        def begin_removal():
            request = (service.remove_source(pack_id, preview["doc_id"])
                       if remove_last_source else service.remove_pack(pack_id))
            return asyncio.create_task(request)

        if removal_arrives_first:
            removal = begin_removal()
            await asyncio.wait_for(lock.waiting.get(), 2)
        incoming = asyncio.create_task(service.import_pack(raw))
        assert await asyncio.to_thread(entered.wait, 2)
        if not removal_arrives_first:
            removal = begin_removal()
            await asyncio.wait_for(lock.waiting.get(), 2)
        assert pack_id not in service._admitting
        lock.release()
        holding_lock = False
        await removal
        assert pack_id not in service._registry.packs
        release.set()
        result = await _finished(service, await incoming)

        # A later import survives either removal entry point; an earlier one
        # still yields to the later deletion after its parser resumes.
        assert result["state"] == ("active" if removal_arrives_first else "cancelled")
        assert (pack_id in service._registry.packs) is removal_arrives_first
        assert source.read_bytes() == original
        assert frozen.read_bytes() == frozen_bytes
    finally:
        release.set()
        if holding_lock:
            lock.release()
        pending = [task for task in (incoming, removal) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await service.stop()
