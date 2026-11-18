"""Steam's existing preparation must obey managed publication and identity fences."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_routers.workshop_router import publish
from services.model_workshop import runtime
from services.model_workshop.paths import WorkshopError
from tests.unit.test_model_workshop_install import bundle_path, install, fail_registration
from tests.unit.test_model_workshop_legacy import config as config, service as service, legacy as legacy


@pytest.mark.parametrize('kind', ['live2d', 'vrm', 'pmx'])
@pytest.mark.parametrize('pending', [False, True])
def test_steam_preparation_copies_only_published_managed_models(legacy, tmp_path, monkeypatch, kind, pending):
    _, cfg, service = legacy
    package, _ = bundle_path(tmp_path, kind)
    if pending:
        fail_registration(service, monkeypatch)
    attempt = install(service, package)
    row = (json.loads(service.installer.journal_path(attempt['task_id']).read_text(encoding='utf-8'))['record']
           if pending else attempt['result'])
    model_type = row['model_format']
    ref = (Path(row['target']).name if model_type == 'live2d'
           else '/user_' + model_type + '/' + Path(row['local_entrypoint']).relative_to(model_type).as_posix())
    monkeypatch.setattr(publish, 'get_config_manager', lambda: cfg)
    monkeypatch.setattr(publish, 'get_workshop_path_async', AsyncMock(return_value=str(tmp_path / 'steam')))
    monkeypatch.setattr(publish, 'read_workshop_meta', lambda _: None)
    request = SimpleNamespace(json=AsyncMock(return_value={
        'charaData': {'档案名': 'Exported', model_type: ref},
        'modelType': model_type, 'modelName': ref, 'fileName': 'Exported.chara.json',
    }))
    response = asyncio.run(publish.prepare_workshop_upload(request))
    body = json.loads(response.body)
    if pending:
        assert response.status_code == 409, body
        assert body['error_code'].startswith('model_workshop_')
        assert not list((tmp_path / 'steam/WorkshopExport').glob('item_*'))
    else:
        assert response.status_code == 200, body
        destination = Path(body['temp_folder'])
        card = json.loads((destination / 'Exported.chara.json').read_text(encoding='utf-8'))
        copied = destination / card[model_type]
        if kind == 'live2d':
            copied = copied / Path(row['local_entrypoint']).name
        assert copied.read_bytes() == (cfg.app_docs_dir / row['local_entrypoint']).read_bytes()
        assert not card.get('item_id')


def test_managed_export_read_blocks_migration_and_releases_after_error(legacy, tmp_path):
    _, cfg, service = legacy
    package, _ = bundle_path(tmp_path)
    row = install(service, package)['result']
    source = cfg.app_docs_dir / row['target']
    with pytest.raises(OSError):
        with runtime.model_read_guard(cfg, source):
            with pytest.raises(WorkshopError, match='model_workshop_busy'):
                runtime.check_migration_ready(cfg.app_docs_dir, tmp_path / 'destination')
            raise OSError('injected export read error')
    runtime.check_migration_ready(cfg.app_docs_dir, tmp_path / 'destination')


def test_bulk_read_rechecks_install_identity_after_service_lookup(legacy, tmp_path, monkeypatch):
    _, cfg, service = legacy
    package, _ = bundle_path(tmp_path, 'vrm')
    old = install(service, package)['result']
    lookup = runtime.get_service
    replaced = False
    def replace_before_lock(config):
        nonlocal replaced
        if not replaced:
            replaced = True
            runtime.remove_managed_model(config, old)
            assert install(service, package)['phase'] == 'completed'
        return lookup(config)
    monkeypatch.setattr(runtime, 'get_service', replace_before_lock)
    with pytest.raises(WorkshopError, match='model_workshop_install_conflict'):
        with runtime.model_read_guard(cfg, cfg.app_docs_dir / old['local_entrypoint']):
            pytest.fail('old read must not consume a new installation')


@pytest.mark.parametrize('external', [False, True])
def test_unknown_registry_blocks_only_sources_with_possible_local_ownership(legacy, tmp_path, external):
    _, cfg, service = legacy
    source = (tmp_path / 'steam' if external else cfg.vrm_dir) / 'Ordinary.vrm'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'ordinary resource')
    (service.paths.private / 'installed.json').write_text('{', encoding='utf-8')
    if external:
        # A damaged local registry cannot own an unrelated Steam/builtin path.
        with runtime.model_read_guard(cfg, source):
            assert source.read_bytes() == b'ordinary resource'
    else:
        with pytest.raises(WorkshopError, match='model_workshop_unavailable'):
            with runtime.model_read_guard(cfg, source):
                pytest.fail('unknown local ownership must fail closed')


def test_rejected_steam_export_preserves_a_replacement_directory(legacy, tmp_path, monkeypatch):
    from contextlib import contextmanager

    _, cfg, service = legacy
    package, _ = bundle_path(tmp_path, 'vrm')
    row = install(service, package)['result']
    export_root = tmp_path / 'steam/WorkshopExport'
    @contextmanager
    def replaced_export(config, source):
        destination = next(export_root.glob('item_*'))
        destination.rename(tmp_path / 'displaced-export')
        destination.mkdir()
        (destination / 'foreign.txt').write_text('preserve', encoding='utf-8')
        raise WorkshopError('model_workshop_unavailable')
        yield  # pragma: no cover
    monkeypatch.setattr(runtime, 'model_read_guard', replaced_export)
    monkeypatch.setattr(publish, 'get_config_manager', lambda: cfg)
    monkeypatch.setattr(publish, 'get_workshop_path_async', AsyncMock(return_value=str(tmp_path / 'steam')))
    request = SimpleNamespace(json=AsyncMock(return_value={
        'charaData': {'档案名': 'Exported', 'vrm': 'Avatar.vrm'}, 'modelType': 'vrm',
        'modelName': '/user_vrm/' + Path(row['local_entrypoint']).name, 'fileName': 'Exported.chara.json',
    }))
    response = asyncio.run(publish.prepare_workshop_upload(request))
    assert response.status_code == 409
    assert next(export_root.glob('item_*/foreign.txt')).read_text(encoding='utf-8') == 'preserve'


@pytest.mark.asyncio
async def test_cancelled_steam_copy_keeps_read_fence_until_worker_finishes(legacy, tmp_path, monkeypatch):
    import shutil
    import threading

    _, cfg, service = legacy
    package, _ = bundle_path(tmp_path, 'vrm')
    row = install(service, package)['result']
    source = cfg.app_docs_dir / row['local_entrypoint']
    entered, release = threading.Event(), threading.Event()
    original = shutil.copy2
    def slow_copy(actual, destination, *args, **kwargs):
        if Path(actual) == source:
            entered.set()
            assert release.wait(10)
        return original(actual, destination, *args, **kwargs)
    monkeypatch.setattr(shutil, 'copy2', slow_copy)
    monkeypatch.setattr(publish, 'get_config_manager', lambda: cfg)
    monkeypatch.setattr(publish, 'get_workshop_path_async', AsyncMock(return_value=str(tmp_path / 'steam')))
    request = SimpleNamespace(json=AsyncMock(return_value={
        'charaData': {'档案名': 'Exported', 'vrm': 'Avatar.vrm'}, 'modelType': 'vrm',
        'modelName': '/user_vrm/' + source.name, 'fileName': 'Exported.chara.json',
    }))
    task = asyncio.create_task(publish.prepare_workshop_upload(request))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        # Cancellation cannot release Steam's destination claim before copy2 ends.
        destination = next((tmp_path / 'steam/WorkshopExport').glob('item_*'))
        with pytest.raises(publish.ContentFolderBusy):
            with publish.claim_content_folder(str(destination), purpose=publish.CLEANUP_PURPOSE):
                pytest.fail('unfinished preparation still owns the export directory')
        with pytest.raises(WorkshopError, match='model_workshop_busy'):
            runtime.check_migration_ready(cfg.app_docs_dir, tmp_path / 'destination')
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    runtime.check_migration_ready(cfg.app_docs_dir, tmp_path / 'destination')
    assert source.is_file()
