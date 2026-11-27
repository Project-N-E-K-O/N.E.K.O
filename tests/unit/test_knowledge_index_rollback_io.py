"""Keep registry-authoritative packs repairable after rollback I/O failures."""

import sqlite3

import pytest

from services.knowledge import service as module
from services.knowledge.models import canonical_pack_bytes, decode_pack_bytes, pack_sha256
from services.knowledge.service import KnowledgeService
from tests.knowledge_helpers import _stop_automatic_indexer
from tests.knowledge_fixtures import _finished, _import, _pack, _raw


pytestmark = pytest.mark.unit


@pytest.fixture
async def registered_pack(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        pack_id = "imp.rollback"
        assert (await _import(service, _pack(pack_id)))["state"] == "active"
        previous = service._registry.packs[pack_id]
        original = (tmp_path / "packs" / previous.file_name).read_bytes()
        replacement = canonical_pack_bytes(decode_pack_bytes(_raw(_pack(pack_id, entries=[
            {"title": f"New {index}", "content": f"Replacement reference {index}."} for index in range(4)
        ]))))
        yield service, previous, original, replacement
    finally:
        await service.stop()


@pytest.mark.parametrize("rollback_fails", [False, True])
async def test_reimport_repairs_the_old_index_after_registry_and_rollback_writes_fail(registered_pack, monkeypatch, rollback_fails):
    service, previous, original, replacement = registered_pack
    replacement_sha = pack_sha256(replacement)
    replace_pack, save_registry = service._store.replace_pack, module.save_registry
    rollbacks = []

    def fail_registry(root, registry):
        if registry.packs[previous.pack_id].pack_sha256 == replacement_sha:
            raise OSError("private registry failure")
        return save_registry(root, registry)

    def maybe_fail_rollback(pack, **kwargs):
        if kwargs["pack_sha256"] == previous.pack_sha256:
            rollbacks.append(True)
            if rollback_fails:
                raise OSError("private rollback failure")
        return replace_pack(pack, **kwargs)

    with monkeypatch.context() as faults:
        faults.setattr(module, "save_registry", fail_registry)
        faults.setattr(service._store, "replace_pack", maybe_fail_rollback)
        failed = await _finished(service, await service.import_pack(replacement))
        assert failed["state"] == "failed"
        assert rollbacks == [True]

    assert service._registry.packs[previous.pack_id] == previous
    assert (service.root / "packs" / previous.file_name).read_bytes() == original
    pending = service._pending_chunk_reconcile
    broken = previous.pack_id in service._broken_packs
    if rollback_fails:
        assert service._store.pack_versions()[previous.pack_id] == replacement_sha
    else:
        assert service._store.pack_versions()[previous.pack_id] == previous.pack_sha256

    # Schedule the normal counter coordinator before the repair request;
    # this does not rebuild the wrong index or clear the pack's repair flag.
    await service._indexer._index_round()
    result = await service.import_pack(original)
    if rollback_fails:
        assert not result.get("unchanged"), result
        assert pending and broken
        assert (await _finished(service, result))["state"] == "active"
    else:
        assert result.get("unchanged") is True
        assert not pending and not broken
    assert service._store.pack_versions()[previous.pack_id] == previous.pack_sha256
    assert previous.pack_id not in service._broken_packs


@pytest.mark.parametrize("version_probe_fails", [False, True])
async def test_failed_rollback_only_marks_an_unproved_index_broken(registered_pack, monkeypatch, version_probe_fails):
    service, previous, original, replacement = registered_pack
    if version_probe_fails:
        service._store.replace_pack(decode_pack_bytes(replacement), pack_sha256=pack_sha256(replacement))
    warnings = []

    def fail_replace(*_args, **_kwargs):
        raise OSError("private rollback failure")

    def fail_probe():
        raise sqlite3.OperationalError("private version probe failure")

    with monkeypatch.context() as faults:
        faults.setattr(service._store, "replace_pack", fail_replace)
        faults.setattr(module.logger, "warning", lambda message, *args: warnings.append(message % args))
        if version_probe_fails:
            faults.setattr(service._store, "pack_versions", fail_probe)
        # The failed probe must not escape and replace the original rollback
        # diagnostic, while an intact registered index remains usable.
        await service._thread(service._restore_index_blocking, previous.pack_id, previous)

    assert (previous.pack_id in service._broken_packs) is version_probe_fails
    assert service._pending_chunk_reconcile
    assert len(warnings) == 1 and "OSError" in warnings[0]
    assert "private" not in warnings[0]
    await service._indexer._index_round()
    result = await service.import_pack(original)
    if version_probe_fails:
        assert not result.get("unchanged"), result
        assert (await _finished(service, result))["state"] == "active"
    else:
        assert result.get("unchanged") is True
