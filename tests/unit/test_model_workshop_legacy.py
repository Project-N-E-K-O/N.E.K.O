"""Managed packages use existing model routes without changing ordinary model semantics."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import monitor, monitor_auth
from app.model_workshop_static import ModelWorkshopRequestGate
from main_routers import live2d_router, mmd_router, vrm_router
from main_routers.characters_router import live2d_models
from services.model_workshop import catalog, runtime
from services.model_workshop.paths import WorkshopError
from tests.unit.test_model_workshop_install import (
    bundle_path,
    config as config,
    service as service,
    install,
    fail_registration,
    journal,
)


@pytest.fixture
def legacy(service, monkeypatch, tmp_path):
    config = service.config
    config.project_root = tmp_path / "project"
    config.readable_live2d_dir = config.live2d_dir
    config.get_live2d_lookup_roots = lambda **_: [config.live2d_dir]

    def ensure(path):
        path.mkdir(parents=True, exist_ok=True)
        return True

    config.ensure_live2d_directory = lambda: ensure(config.live2d_dir)
    config.ensure_vrm_directory = lambda: ensure(config.vrm_dir)
    config.ensure_mmd_directory = lambda: ensure(config.mmd_dir)
    config.mmd_animation_dir = config.mmd_dir / "animation"
    for kind in ("live2d", "vrm", "mmd"):
        ensure(getattr(config, kind + "_dir"))
        ensure(config.project_root / "static" / kind)
    monkeypatch.setattr(runtime, "_services", {str(service.paths.root): service})
    monkeypatch.setattr(runtime, "_stopped", False)
    from utils import config_manager

    monkeypatch.setattr(config_manager, "get_config_manager", lambda *_, **__: config)
    monkeypatch.setattr(catalog, "get_config_manager", lambda: config)
    for module in (live2d_router, mmd_router, vrm_router, live2d_models):
        monkeypatch.setattr(module, "get_config_manager", lambda: config)
        if hasattr(module, "get_subscribed_workshop_items"):
            monkeypatch.setattr(
                module,
                "get_subscribed_workshop_items",
                AsyncMock(return_value={"success": True, "items": []}),
            )
    # No external Steam path or unrelated resource tree participates in these tests.
    from utils import frontend_utils

    monkeypatch.setattr(frontend_utils, "_resolve_workshop_search_dir", lambda: None)
    monkeypatch.chdir(config.project_root)
    app = FastAPI()
    for module in (live2d_router, mmd_router, vrm_router):
        app.include_router(module.router)
    app.add_middleware(ModelWorkshopRequestGate, config_getter=lambda: config)
    with TestClient(app) as client:
        yield client, config, service
    live2d_router._MODEL_CONFIG_WRITE_LOCKS.clear()


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx"])
def test_existing_catalog_only_adds_trusted_display_metadata(legacy, tmp_path, kind):
    client, _, service = legacy
    package, _ = bundle_path(tmp_path, kind)
    row = install(service, package)["result"]
    actual_kind = row["model_format"]
    prefix = "/api/live2d" if actual_kind == "live2d" else "/api/model/" + actual_kind
    response = client.get(prefix + "/models")
    assert response.status_code == 200, response.text
    model = next(
        value
        for value in (
            response.json() if kind == "live2d" else response.json()["models"]
        )
        if row["package_sha256"] in value.get("name", "")
    )
    assert model["display_name"] == "Native fixture"
    assert model["name"] != model["display_name"]
    assert model["model_workshop"]["install_id"] == row["install_id"]
    assert not model.get("item_id") and model.get("source") != "steam_workshop"


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx"])
def test_unfinished_model_cannot_escape_catalog_or_named_config_fallbacks(
    legacy, tmp_path, monkeypatch, kind
):
    client, _, service = legacy
    package, _ = bundle_path(tmp_path, kind)
    fail_registration(service, monkeypatch)
    result = install(service, package)
    row = journal(service, result["task_id"])["record"]
    actual_kind = row["model_format"]
    name = (
        row["target"].split("/")[-1]
        if kind == "live2d"
        else row["local_entrypoint"].split("/")[-1].rsplit(".", 1)[0]
    )
    prefix = "/api/live2d" if kind == "live2d" else "/api/model/" + actual_kind
    data = client.get(prefix + "/models").json()
    models = data if kind == "live2d" else data["models"]
    assert not any(row["package_sha256"] in value.get("name", "") for value in models)
    path = (
        prefix
        + "/emotion_mapping"
        + ("?model=" + name if kind == "pmx" else "/" + name)
    )
    response = client.get(path)
    assert (
        response.status_code == 503 and response.headers["cache-control"] == "no-store"
    )
    if kind == "live2d":
        with pytest.raises(WorkshopError, match="unavailable"):
            live2d_models._resolve_live2d_model_binding(
                "/user_live2d/" + row["local_entrypoint"][len("live2d/") :]
            )
        with pytest.raises(WorkshopError, match="unavailable"):
            live2d_models._resolve_live2d_model_binding(
                "http://127.0.0.1:48911/user_live2d/"
                + row["local_entrypoint"][len("live2d/") :]
            )
    if kind == "pmx":
        response = client.post(
            prefix + "/emotion_mapping",
            json={"model": name, "mapping": {"happy": ["smile"]}},
        )
        assert response.status_code == 409
        assert not (service.paths.mmd / "emotion_config").exists()


@pytest.mark.parametrize(
    "kind,route", [("live2d", "name"), ("vrm", "name"), ("vrm", "url"), ("pmx", "url")]
)
def test_original_delete_endpoints_coordinate_model_and_auxiliary_config(
    legacy, tmp_path, kind, route
):
    client, config, service = legacy
    package, _ = bundle_path(tmp_path, kind)
    row = install(service, package)["result"]
    actual_kind = row["model_format"]
    from pathlib import Path

    name = (
        Path(row["target"]).name
        if kind == "live2d"
        else Path(row["local_entrypoint"]).stem
    )
    mapping = None
    if kind == "vrm":
        mapping = vrm_router._get_emotion_config_path(name)
    elif kind == "pmx":
        mapping = config.mmd_dir / "emotion_config" / (name + ".json")
        mapping.parent.mkdir()
    if mapping:
        mapping.write_text('{"happy":["smile"]}')
    prefix = "/api/live2d" if kind == "live2d" else "/api/model/" + actual_kind
    if route == "name":
        response = client.delete(prefix + "/model/" + name)
    else:
        response = client.request(
            "DELETE",
            prefix + "/model",
            json={
                "url": "/user_"
                + actual_kind
                + "/"
                + Path(row["local_entrypoint"]).relative_to(actual_kind).as_posix()
            },
        )
    assert response.status_code == 200, response.text
    assert not (service.paths.root / row["target"]).exists()
    assert service.installer.registry.installed()[0]["status"] == "missing"
    assert mapping is None or not mapping.exists()


def test_vrm_old_delete_after_steam_wait_cannot_delete_new_attempt_or_mapping(
    legacy, tmp_path, monkeypatch
):
    _, config, service = legacy
    package, _ = bundle_path(tmp_path, "vrm")
    old = install(service, package)["result"]
    target = service.paths.root / old["target"]
    mapping = vrm_router._get_emotion_config_path(target.stem)
    mapping.write_text("{}")
    entered, release = threading.Event(), threading.Event()

    async def discovery():
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        return {"success": True, "items": []}

    monkeypatch.setattr(vrm_router, "get_subscribed_workshop_items", discovery)
    with ThreadPoolExecutor(max_workers=1) as pool:
        deletion = pool.submit(
            lambda: asyncio.run(vrm_router._delete_managed_vrm(config, target))
        )
        assert entered.wait(5)
        try:
            runtime.remove_managed_model(config, old)
            new = install(service, package)["result"]
            mapping.write_text('{"new":true}')
        finally:
            release.set()
        with pytest.raises(WorkshopError, match="conflict"):
            deletion.result(5)
    assert target.is_file() and new["install_id"] != old["install_id"]
    assert json.loads(mapping.read_text()) == {"new": True}


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx"])
def test_unreadable_registry_blocks_legacy_deletion_before_any_file_is_removed(legacy, tmp_path, kind):
    client, _, service = legacy
    path, _ = bundle_path(tmp_path, kind)
    row = install(service, path)["result"]
    from pathlib import Path

    name = Path(row["target"]).name if kind == "live2d" else Path(row["local_entrypoint"]).stem
    prefix = "/api/live2d" if kind == "live2d" else "/api/model/" + row["model_format"]
    (service.paths.private / "installed.json").write_bytes(b"{")
    if kind == "pmx":
        response = client.request("DELETE", prefix + "/model", json={
            "url": "/user_mmd/" + Path(row["local_entrypoint"]).relative_to("mmd").as_posix()
        })
    else:
        response = client.delete(prefix + "/model/" + name)
    assert response.status_code == 409, response.text
    assert (service.paths.root / row["local_entrypoint"]).is_file()


@pytest.mark.parametrize("raises", [False, True])
def test_vrm_delete_preserves_uncertain_shared_mapping_when_steam_is_unavailable(legacy, tmp_path, monkeypatch, raises):
    client, _, service = legacy
    path, _ = bundle_path(tmp_path, "vrm")
    row = install(service, path)["result"]
    target = service.paths.root / row["target"]
    mapping = vrm_router._get_emotion_config_path(target.stem)
    mapping.write_text('{"keep":true}')

    async def offline():
        if raises:
            raise OSError("Steam unavailable")
        return {"success": False}

    monkeypatch.setattr(vrm_router, "get_subscribed_workshop_items", offline)
    response = client.delete("/api/model/vrm/model/" + target.stem)
    assert response.status_code == 200, response.text
    assert not target.exists()
    assert json.loads(mapping.read_text()) == {"keep": True}


def test_managed_motion_addition_does_not_recreate_deleted_model(legacy, tmp_path):
    _, config, service = legacy
    package, _ = bundle_path(tmp_path)
    row = install(service, package)["result"]
    target = service.paths.root / row["target"]
    runtime.remove_managed_model(config, row)
    with pytest.raises(WorkshopError, match="unavailable"):
        runtime.write_managed_blob(config, target / "motions/new.motion3.json", b"{}")
    assert not target.exists()


def test_managed_vrm_emotion_mapping_uses_the_existing_project_config_location(legacy, tmp_path):
    client, _, service = legacy
    package, _ = bundle_path(tmp_path, "vrm")
    row = install(service, package)["result"]
    name = (service.paths.root / row["target"]).stem
    response = client.post("/api/model/vrm/emotion_mapping/" + name, json={"happy": ["smile"]})
    assert response.status_code == 200, response.text
    config_path = vrm_router._get_emotion_config_path(name)
    assert not config_path.is_relative_to(service.paths.root)
    assert json.loads(config_path.read_text()) == {"happy": ["smile"]}


def test_managed_vrm_config_exception_does_not_authorize_an_arbitrary_external_file(legacy, tmp_path):
    _, config, service = legacy
    package, _ = bundle_path(tmp_path, "vrm")
    row = install(service, package)["result"]
    name = (service.paths.root / row["target"]).stem
    unrelated = tmp_path / (name + "_emotion.json")
    with pytest.raises(WorkshopError, match="unsafe_path"):
        runtime.write_managed_json(config, unrelated, {}, kind="vrm", name=name)
    assert not unrelated.exists()


def test_config_edit_started_before_reinstall_cannot_overwrite_new_model(
    legacy, tmp_path, monkeypatch
):
    _, config, service = legacy
    package, _ = bundle_path(tmp_path)
    old = install(service, package)["result"]
    entry = service.paths.root / old["local_entrypoint"]
    original_read = live2d_router.read_json_async

    class Request:
        async def json(self):
            return {"FileReferences": {"Motions": {"stale-edit": []}}}

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_read(path):
            data = await original_read(path)
            entered.set()
            await release.wait()
            return data

        monkeypatch.setattr(live2d_router, "read_json_async", held_read)
        edit = asyncio.create_task(
            live2d_router.update_model_config(entry.parent.name, Request())
        )
        assert await asyncio.wait_for(entered.wait(), 5)
        try:
            await asyncio.to_thread(runtime.remove_managed_model, config, old)
            new = await asyncio.to_thread(install, service, package)
            assert new["result"]["install_id"] != old["install_id"]
            expected = entry.read_bytes()
        finally:
            release.set()
        response = await edit
        assert getattr(response, "status_code", 200) == 409
        assert entry.read_bytes() == expected

    asyncio.run(run())


def test_named_response_rechecks_a_publication_started_after_request_gate(
    legacy, tmp_path, monkeypatch
):
    _, config, service = legacy
    package, _ = bundle_path(tmp_path, "vrm")
    import hashlib

    name = "neko_model_" + hashlib.sha256(package.read_bytes()).hexdigest()
    fail_registration(service, monkeypatch)

    async def endpoint(scope, receive, send):
        result = await asyncio.to_thread(install, service, package)
        assert result["phase"] == "failed"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {
                "type": "http.response.body",
                "body": b"not registered model configuration",
            }
        )

    async def run():
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.disconnect"}

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/model/vrm/emotion_mapping/" + name,
            "headers": [],
            "query_string": b"",
        }
        await ModelWorkshopRequestGate(endpoint, lambda: config)(scope, receive, send)
        assert sent[0]["status"] == 503
        assert b"not registered" not in b"".join(item.get("body", b"") for item in sent)

    asyncio.run(run())


def test_actual_monitor_auth_wraps_read_only_pending_checks(
    legacy, tmp_path, monkeypatch
):
    _, config, service = legacy
    package, _ = bundle_path(tmp_path)
    fail_registration(service, monkeypatch)
    result = install(service, package)
    row = journal(service, result["task_id"])["record"]

    monkeypatch.setattr(monitor, "_config_manager", config)
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "monitor-test-secret")
    reads = []
    original = runtime.snapshot

    def observed(*args, **kwargs):
        reads.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "snapshot", observed)
    path = "/api/live2d/emotion_mapping/" + row["target"].split("/")[-1]
    with TestClient(monitor.app) as client:
        assert client.get(path).status_code == 401
        assert reads == []  # No state disclosure before Monitor's own auth.
        response = client.get(
            path, headers={"Authorization": "Bearer monitor-test-secret"}
        )
        assert response.status_code == 503 and reads
    assert service.installer.journal_path(result["task_id"]).exists()
    assert (
        service._writer is not None
    )  # Monitor never recovers or replaces Main's owner.


def _legacy_upload(client, kind, directory):
    """Exercise the actual old upload routes with a chosen model-root name."""
    import io
    import zipfile

    if kind == 'live2d':
        return client.post('/api/live2d/upload_model', files=[
            ('files', (directory + '/Avatar.model3.json', b'{}', 'application/json')),
        ])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr(directory + '/Avatar.pmx', b'PMX test upload')
    return client.post('/api/model/mmd/upload_zip', files={
        'file': ('Avatar.zip', stream.getvalue(), 'application/zip'),
    })


@pytest.mark.parametrize('kind', ['live2d', 'pmx'])
@pytest.mark.parametrize('missing_entrypoint', [False, True])
def test_rejected_old_upload_preserves_managed_directory_and_local_edits(
    legacy, tmp_path, kind, missing_entrypoint
):
    client, _, service = legacy
    package, _ = bundle_path(tmp_path, kind)
    row = install(service, package)['result']
    target = service.paths.root / row['target']
    if missing_entrypoint:
        (service.paths.root / row['local_entrypoint']).unlink()
    (target / 'local-edit.txt').write_bytes(b'keep local edits')
    before = {str(path.relative_to(target)): path.read_bytes()
              for path in target.rglob('*') if path.is_file()}
    registry = (service.paths.private / "installed.json").read_bytes()
    response = _legacy_upload(client, kind, target.name)
    assert response.status_code == 409, response.text
    assert response.json()['error_code'] == 'model_workshop_install_conflict'
    assert target.is_dir()
    assert {str(path.relative_to(target)): path.read_bytes()
            for path in target.rglob('*') if path.is_file()} == before
    assert (service.paths.private / "installed.json").read_bytes() == registry


@pytest.mark.parametrize('kind', ['live2d', 'pmx'])
def test_old_upload_uncertain_registry_preserves_existing_directory(legacy, monkeypatch, kind):
    client, cfg, _ = legacy
    root = cfg.live2d_dir if kind == 'live2d' else cfg.mmd_dir
    target = root / 'existing'
    target.mkdir()
    (target / 'local-edit.txt').write_bytes(b'keep')
    def unreadable(_):
        raise WorkshopError('model_workshop_state_unreadable')
    monkeypatch.setattr(runtime, 'snapshot_for_mutation', unreadable)
    response = _legacy_upload(client, kind, target.name)
    assert response.status_code == 409, response.text
    assert (target / 'local-edit.txt').read_bytes() == b'keep'


@pytest.mark.parametrize('kind', ['live2d', 'pmx'])
def test_ordinary_old_upload_still_uses_its_existing_import_path(legacy, kind):
    client, cfg, _ = legacy
    response = _legacy_upload(client, kind, 'ordinary-avatar')
    assert response.status_code == 200 and response.json()['success'], response.text
    root = cfg.live2d_dir if kind == 'live2d' else cfg.mmd_dir
    assert (root / 'ordinary-avatar').is_dir()


@pytest.mark.parametrize('kind', ['live2d', 'pmx'])
@pytest.mark.parametrize('replacement', [False, True])
def test_failed_old_upload_cleans_only_its_own_reserved_directory(legacy, monkeypatch, kind, replacement):
    import shutil

    client, cfg, _ = legacy
    root = cfg.live2d_dir if kind == 'live2d' else cfg.mmd_dir
    target = root / 'incoming'
    def fail(*args, **kwargs):
        assert target.is_dir()
        if replacement:
            target.rename(root / 'original-reservation')
            target.mkdir()
            (target / 'other-owner.txt').write_bytes(b'keep')
        raise OSError('injected copy/extract failure')
    if kind == 'live2d':
        monkeypatch.setattr(shutil, 'copytree', fail)
    else:
        monkeypatch.setattr(mmd_router, '_extract_zip_with_encoding', fail)
    response = _legacy_upload(client, kind, target.name)
    assert response.status_code == 500, response.text
    if replacement:
        assert (target / 'other-owner.txt').read_bytes() == b'keep'
    else:
        assert not target.exists()


@pytest.mark.parametrize('kind', ['live2d', 'pmx'])
def test_old_upload_cannot_clean_a_directory_published_before_reservation(legacy, monkeypatch, kind):
    from pathlib import Path

    client, cfg, _ = legacy
    root = cfg.live2d_dir if kind == 'live2d' else cfg.mmd_dir
    target = root / 'incoming'
    original = Path.mkdir
    def racing_mkdir(path, *args, **kwargs):
        if path == target:
            original(path, *args, **kwargs)
            (path / 'other-owner.txt').write_bytes(b'keep')
            raise FileExistsError('concurrent publication won')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', racing_mkdir)
    response = _legacy_upload(client, kind, target.name)
    assert response.status_code == 500, response.text
    assert (target / 'other-owner.txt').read_bytes() == b'keep'
