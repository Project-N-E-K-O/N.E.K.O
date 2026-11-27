"""Keep local replacement provenance distinct from same-content repairs."""

import json
import zipfile

import pytest

from knowledge.registry import load_registry
from knowledge.service import KnowledgeService
from tests.knowledge_archive_fixtures import make_archive
from tests.knowledge_fixtures import _finished, _import, _pack, _raw


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("input_format", ["json", "zip"])
async def test_local_new_body_replaces_builtin_origin_and_persists(tmp_path, input_format):
    archive = make_archive(tmp_path / "incoming.zip")
    with zipfile.ZipFile(archive) as source:
        raw = source.read("pack.json")
    builtin = json.loads(raw)
    builtin["entries"][0]["content"] = "The original built-in reference."
    root = tmp_path / "library"
    service = KnowledgeService(root)
    await service.start()
    try:
        assert (await _import(service, builtin))["state"] == "active"
        original = load_registry(root).packs[builtin["pack_id"]]
        assert original.origin == "builtin"
        result = await service.import_pack(raw if input_format == "json" else archive,
                                           _options={"origin": "imported", "enabled": False})
        assert (await _finished(service, result))["state"] == "active"
        record = load_registry(root).packs[builtin["pack_id"]]
        assert record.origin == "imported"
        assert record.pack_sha256 != original.pack_sha256
        assert record.installed_at == original.installed_at
        assert record.enabled is False and record.auto_context is False
    finally:
        await service.stop()

    restarted = KnowledgeService(root)
    await restarted.start()
    try:
        row = (await restarted.list_packs())[0]
        assert row["origin"] == "imported" and row["enabled"] is False
    finally:
        await restarted.stop()


@pytest.mark.parametrize("repair", [False, True])
async def test_same_builtin_body_keeps_its_origin_on_repeat_or_repair(tmp_path, repair):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        payload = _pack()
        assert (await _import(service, payload))["state"] == "active"
        original = load_registry(tmp_path).packs[payload["pack_id"]]
        if repair:
            # Missing canonical bytes force a real install instead of unchanged.
            (tmp_path / "packs" / original.file_name).unlink()
        result = await service.import_pack(_raw(payload), _options={"origin": "imported", "enabled": False})
        if repair:
            assert (await _finished(service, result))["state"] == "active"
        else:
            assert result["unchanged"] is True
        assert load_registry(tmp_path).packs[payload["pack_id"]].origin == "builtin"
        assert (await service.list_packs())[0]["origin"] == "builtin"
    finally:
        await service.stop()


async def test_trusted_import_without_origin_keeps_existing_builtin_label(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        payload = _pack()
        assert (await _import(service, payload))["state"] == "active"
        payload["entries"][0]["content"] = "Updated through the existing internal import contract."
        assert (await _import(service, payload))["state"] == "active"
        assert load_registry(tmp_path).packs[payload["pack_id"]].origin == "builtin"
    finally:
        await service.stop()
