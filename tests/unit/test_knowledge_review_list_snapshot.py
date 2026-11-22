"""Keep one pack row when source removal races the retained-history scan."""

import asyncio
import threading

import pytest

from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_unified_core import _finished


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("remove_last_source", [True, False])
async def test_pack_listing_does_not_repeat_an_active_row_after_concurrent_removal(
    tmp_path, monkeypatch, remove_last_source
):
    service = KnowledgeService(tmp_path)
    await service.start()
    entered, release = asyncio.Event(), threading.Event()
    listing = None
    try:
        preview = (await service.preview_source(
            "notes.txt", b"Lighthouses guide ships.", name="Snapshot references"
        ))["preview"]
        installed = await _finished(service, await service.commit_source(preview["preview_id"], preview["sha256"]))
        assert installed["state"] == "active"
        pack_id = installed["pack_id"]
        original = next((service.root / "sources" / pack_id / "originals").rglob("original"))
        original_bytes = original.read_bytes()
        retained = service._sources.retained_packs
        loop = asyncio.get_running_loop()

        def hold_history_scan():
            # list_packs has already emitted the active row before this real
            # sidecar scan starts. Another window can now commit its removal.
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "retained scan was not released"
            return retained()

        monkeypatch.setattr(service._sources, "retained_packs", hold_history_scan)
        listing = asyncio.create_task(service.list_packs())
        await asyncio.wait_for(entered.wait(), 3)
        if remove_last_source:
            await service.remove_source(pack_id, preview["doc_id"])
        else:
            await service.remove_pack(pack_id)
        assert pack_id not in service._registry.packs
        release.set()
        rows = await asyncio.wait_for(listing, 3)
        assert [row["pack_id"] for row in rows] == [pack_id]
        assert rows[0]["retained"] is False
        # A later refresh observes the new state: recoverable document history
        # stays visible, while explicit pack removal leaves no placeholder.
        refreshed = await service.list_packs()
        if remove_last_source:
            assert len(refreshed) == 1 and refreshed[0]["pack_id"] == pack_id
            assert refreshed[0]["retained"] is True and refreshed[0]["sources_managed"] is True
            restored = await _finished(service, await service.restore_source(pack_id, preview["doc_id"], 1))
            assert restored["state"] == "active"
            active = await service.list_packs()
            assert len(active) == 1 and active[0]["pack_id"] == pack_id and active[0]["retained"] is False
        else:
            assert refreshed == []
        assert original.read_bytes() == original_bytes
    finally:
        release.set()
        if listing is not None:
            await asyncio.gather(listing, return_exceptions=True)
        await service.stop()
