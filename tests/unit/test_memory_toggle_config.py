"""Memory settings preserve configuration data and distinguish failures from defaults."""

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import memory_router
from utils import config_manager
from utils.config_manager.storage_roots import StorageRootsMixin


pytestmark = pytest.mark.unit

SETTINGS = [
    ("review_config", "recent_memory_auto_review"),
    ("powerful_memory_config", "powerful_memory_enabled"),
]


@pytest.fixture
def setting_client(tmp_path, monkeypatch):
    # Use the real file loader without initializing or migrating a user root.
    manager = StorageRootsMixin.__new__(StorageRootsMixin)
    manager.docs_dir = tmp_path
    manager.app_docs_dir = tmp_path
    manager.load_root_state = lambda: {"mode": "normal"}
    manager.config_dir = tmp_path / "config"
    manager.project_config_dir = tmp_path / "project-config"
    # cloudsave_writable_transaction needs a local state root next to the docs root.
    manager.anchor_root = tmp_path / "anchor"
    manager.config_dir.mkdir()
    manager.project_config_dir.mkdir()
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: manager)
    monkeypatch.setattr(memory_router, "_memory_toggle_write_lock", asyncio.Lock())
    app = FastAPI()
    app.include_router(memory_router.router)
    with TestClient(app) as client:
        yield client, manager


@pytest.mark.parametrize("endpoint,key", SETTINGS)
@pytest.mark.parametrize("value", ["missing_file", "missing_key", False, True])
def test_memory_setting_reads_preserve_defaults_and_saved_values(setting_client, endpoint, key, value):
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    if value != "missing_file":
        data = {"unrelated_setting": "keep"}
        if value != "missing_key":
            data[key] = value
        path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes() if path.exists() else None

    response = client.get(f"/api/memory/{endpoint}")

    assert response.status_code == 200
    assert response.json() == {"enabled": value if isinstance(value, bool) else True}
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("endpoint,key", SETTINGS)
def test_memory_setting_reads_preserve_project_config_fallback(setting_client, endpoint, key):
    client, manager = setting_client
    path = manager.project_config_dir / "core_config.json"
    path.write_text(json.dumps({key: False}), encoding="utf-8")

    response = client.get(f"/api/memory/{endpoint}")

    assert response.status_code == 200
    assert response.json() == {"enabled": False}
    assert not (manager.config_dir / "core_config.json").exists()


@pytest.mark.parametrize("endpoint,key", SETTINGS)
@pytest.mark.parametrize("payload", [b"{broken", b"[]", b"null", b'"text"', b"\xff"],
                         ids=["broken-json", "array", "null", "string", "bad-encoding"])
def test_memory_setting_reads_reject_invalid_files(setting_client, endpoint, key, payload):
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    path.write_bytes(payload)

    response = client.get(f"/api/memory/{endpoint}")

    assert response.status_code == 503
    assert "enabled" not in response.json()
    assert "code" not in response.json()
    assert path.read_bytes() == payload


@pytest.mark.parametrize("endpoint,key", SETTINGS)
@pytest.mark.parametrize("value", [None, "false", 0, 1, [], {}])
def test_memory_setting_reads_reject_non_boolean_values(setting_client, endpoint, key, value):
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    payload = json.dumps({key: value}).encode("utf-8")
    path.write_bytes(payload)

    response = client.get(f"/api/memory/{endpoint}")

    assert response.status_code == 503
    assert "enabled" not in response.json()
    assert response.json()["code"] == "invalid_memory_setting"
    assert path.read_bytes() == payload


@pytest.mark.parametrize("endpoint,key", SETTINGS)
def test_memory_setting_reads_reject_permission_errors(setting_client, endpoint, key):
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    payload = json.dumps({key: False}).encode("utf-8")
    path.write_bytes(payload)
    with patch("builtins.open", side_effect=PermissionError("config read denied")):
        response = client.get(f"/api/memory/{endpoint}")

    assert response.status_code == 503
    assert "enabled" not in response.json()
    assert "code" not in response.json()
    assert path.read_bytes() == payload


@pytest.mark.parametrize("endpoint,key", SETTINGS)
@pytest.mark.parametrize("payload", [None, [], True, 0, "false", {}, {"enabled": None},
                                      {"enabled": "false"}, {"enabled": 0}, {"enabled": 1},
                                      {"enabled": []}, {"enabled": {}}])
def test_memory_setting_writes_reject_invalid_requests(setting_client, endpoint, key, payload):
    """Invalid requests cannot change any configuration bytes."""
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    before = json.dumps({key: True, "unrelated_setting": "keep"}).encode()
    path.write_bytes(before)

    response = client.post(f"/api/memory/{endpoint}", content=json.dumps(payload))

    assert response.status_code == 400
    assert response.json()["success"] is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("endpoint,key", SETTINGS)
@pytest.mark.parametrize("payload", [b"{broken", b"", b"\xff"])
def test_memory_setting_writes_reject_malformed_json(setting_client, endpoint, key, payload):
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    response = client.post(f"/api/memory/{endpoint}", content=payload)
    assert response.status_code == 400
    assert response.json()["success"] is False
    assert not path.exists()


@pytest.mark.parametrize("endpoint,key", SETTINGS)
@pytest.mark.parametrize("payload", [b"{broken", b"[]", b"null", b'"text"', b"\xff"])
def test_memory_setting_writes_preserve_invalid_files(setting_client, endpoint, key, payload):
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    path.write_bytes(payload)

    response = client.post(f"/api/memory/{endpoint}", json={"enabled": True})

    assert response.json()["success"] is False
    assert path.read_bytes() == payload


@pytest.mark.parametrize("endpoint,key", SETTINGS)
def test_memory_setting_writes_preserve_unreadable_files(setting_client, endpoint, key):
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    before = json.dumps({key: False, "unrelated_setting": "keep"}).encode()
    path.write_bytes(before)
    with patch.object(manager, "load_json_config", side_effect=PermissionError("config read denied")):
        response = client.post(f"/api/memory/{endpoint}", json={"enabled": True})
    assert response.json()["success"] is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("endpoint,key", SETTINGS)
@pytest.mark.parametrize("location", ["missing", "runtime", "project"])
def test_memory_setting_writes_preserve_other_settings(setting_client, endpoint, key, location):
    client, manager = setting_client
    before = {"unrelated_setting": {"nested": "keep"}, key: False}
    if location != "missing":
        directory = manager.config_dir if location == "runtime" else manager.project_config_dir
        (directory / "core_config.json").write_text(json.dumps(before), encoding="utf-8")

    response = client.post(f"/api/memory/{endpoint}", json={"enabled": True})

    assert response.status_code == 200
    assert response.json() == {"success": True, "enabled": True}
    expected = {key: True} if location == "missing" else {**before, key: True}
    assert json.loads((manager.config_dir / "core_config.json").read_text(encoding="utf-8")) == expected
    if location == "project":
        assert json.loads((manager.project_config_dir / "core_config.json").read_text(encoding="utf-8")) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("migration_result", ["success", "http_error", "rejected", "exception"])
async def test_memory_setting_concurrent_writes_preserve_both_toggles(
    setting_client, monkeypatch, migration_result
):
    """Hold the migration while a second writer arrives, then check both committed values."""
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    initial = {"recent_memory_auto_review": True, "powerful_memory_enabled": True, "unrelated": "keep"}
    path.write_text(json.dumps(initial), encoding="utf-8")
    started = asyncio.Event()
    release = asyncio.Event()
    second_arrived = asyncio.Event()

    async def migrate(*args, **kwargs):
        started.set()
        await release.wait()
        if migration_result == "exception":
            raise httpx.ReadTimeout("migration timed out")
        return SimpleNamespace(
            status_code=500 if migration_result == "http_error" else 200,
            json=lambda: {"ok": migration_result != "rejected", "count": 1},
        )

    from utils import internal_http_client
    monkeypatch.setattr(internal_http_client, "get_internal_http_client", lambda: SimpleNamespace(post=migrate))

    async def request_started(request):
        if request.url.path.endswith("/review_config"):
            second_arrived.set()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app), base_url="http://test", event_hooks={"request": [request_started]}
    ) as api:
        strong = asyncio.create_task(api.post("/api/memory/powerful_memory_config", json={"enabled": False}))
        review = None
        reads = []
        try:
            await asyncio.wait_for(started.wait(), 2)
            review = asyncio.create_task(api.post("/api/memory/review_config", json={"enabled": False}))
            await asyncio.wait_for(second_arrived.wait(), 2)
            reads = [asyncio.create_task(api.get(f"/api/memory/{name}")) for name, _ in SETTINGS]
            completed, _ = await asyncio.wait({review, *reads}, timeout=0.1)
            assert not completed, "The second save must wait until the migration/write finishes"
            assert json.loads(path.read_text(encoding="utf-8")) == initial
        finally:
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*([strong, review, *reads] if review else [strong])), 3)

    assert results[0].json()["success"] is (migration_result == "success")
    assert results[1].json() == {"success": True, "enabled": False}
    expected = {**initial, "recent_memory_auto_review": False, "powerful_memory_enabled": migration_result != "success"}
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert [response.json() for response in results[2:]] == [
        {"enabled": expected[setting]} for _, setting in SETTINGS
    ]


@pytest.mark.parametrize("endpoint,key", SETTINGS)
def test_memory_setting_write_failure_preserves_config_and_releases_lock(setting_client, endpoint, key):
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    before = json.dumps({key: False, "unrelated": "keep"}).encode()
    path.write_bytes(before)
    with patch.object(manager, "save_json_config", side_effect=PermissionError("config write denied")):
        response = client.post(f"/api/memory/{endpoint}", json={"enabled": True})
    assert response.json()["success"] is False
    assert path.read_bytes() == before
    assert client.post(f"/api/memory/{endpoint}", json={"enabled": True}).json()["success"] is True


@pytest.mark.parametrize("endpoint,key", SETTINGS)
def test_memory_setting_writes_keep_maintenance_fence(setting_client, endpoint, key):
    from utils.cloudsave_runtime import MaintenanceModeError

    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    before = json.dumps({key: False, "unrelated": "keep"}).encode()
    path.write_bytes(before)
    with patch.object(manager, "load_root_state", return_value={"mode": "maintenance_readonly"}):
        with pytest.raises(MaintenanceModeError):
            client.post(f"/api/memory/{endpoint}", json={"enabled": True})
    assert path.read_bytes() == before
    assert client.post(f"/api/memory/{endpoint}", json={"enabled": True}).json()["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,key", SETTINGS)
@pytest.mark.parametrize("save_fails", [False, True])
async def test_memory_setting_readback_waits_for_pending_write(setting_client, monkeypatch, endpoint, key, save_fails):
    """A recovery read must wait for a still-running file write, even if it fails."""
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    initial = {"recent_memory_auto_review": False, "powerful_memory_enabled": False, "unrelated": "keep"}
    path.write_text(json.dumps(initial), encoding="utf-8")
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original_save = manager.save_json_config

    def delayed_save(*args, **kwargs):
        loop.call_soon_threadsafe(started.set)
        if not release.wait(5):
            raise TimeoutError("test did not release the file write")
        if save_fails:
            raise PermissionError("config write denied")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(manager, "save_json_config", delayed_save)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://test") as api:
        save = asyncio.create_task(api.post(f"/api/memory/{endpoint}", json={"enabled": True}))
        reads = []
        try:
            await asyncio.wait_for(started.wait(), 2)
            reads = [asyncio.create_task(api.get(f"/api/memory/{name}")) for name, _ in SETTINGS]
            completed, _ = await asyncio.wait(reads, timeout=0.1)
            assert not completed, "Recovery reads must wait for the pending file write"
            assert json.loads(path.read_text(encoding="utf-8")) == initial
        finally:
            release.set()
            results = await asyncio.wait_for(asyncio.gather(save, *reads), 3)

    assert results[0].json()["success"] is not save_fails
    expected = {**initial, key: not save_fails}
    assert [response.json() for response in results[1:]] == [
        {"enabled": expected[setting]} for _, setting in SETTINGS
    ]
    assert json.loads(path.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("endpoint,key", SETTINGS)
@pytest.mark.parametrize("old_value", [False, True, None, 0, 1, "false", [], {}])
@pytest.mark.parametrize("enabled", [False, True])
def test_memory_setting_post_can_repair_legacy_values(setting_client, monkeypatch, endpoint, key, old_value, enabled):
    """Only confirmed false may skip migration when explicitly saving off."""
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    initial = {key: old_value, "unrelated": {"keep": True}}
    path.write_text(json.dumps(initial), encoding="utf-8")
    migrations = []

    async def migrate(*args, **kwargs):
        migrations.append(True)
        assert json.loads(path.read_text(encoding="utf-8")) == initial
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "count": 1})

    from utils import internal_http_client
    monkeypatch.setattr(internal_http_client, "get_internal_http_client", lambda: SimpleNamespace(post=migrate))
    response = client.post(f"/api/memory/{endpoint}", json={"enabled": enabled})

    assert response.json() == {"success": True, "enabled": enabled}
    assert len(migrations) == int(endpoint == "powerful_memory_config" and enabled is False and old_value is not False)
    assert json.loads(path.read_text(encoding="utf-8")) == {**initial, key: enabled}
    assert client.get(f"/api/memory/{endpoint}").json() == {"enabled": enabled}


@pytest.mark.parametrize("old_value", [None, 0, 1, "false", [], {}])
def test_memory_setting_legacy_repair_preserves_file_when_migration_fails(setting_client, monkeypatch, old_value):
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    before = json.dumps({"powerful_memory_enabled": old_value, "unrelated": "keep"}).encode()
    path.write_bytes(before)

    async def migrate(*args, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: {"ok": False, "error": "migration failed"})

    from utils import internal_http_client
    monkeypatch.setattr(internal_http_client, "get_internal_http_client", lambda: SimpleNamespace(post=migrate))
    response = client.post("/api/memory/powerful_memory_config", json={"enabled": False})
    assert response.json()["success"] is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("endpoint,key", SETTINGS)
def test_memory_setting_reads_identify_repairable_values_independently(setting_client, endpoint, key):
    client, manager = setting_client
    path = manager.config_dir / "core_config.json"
    other_endpoint, other_key = next(item for item in SETTINGS if item[0] != endpoint)
    before = json.dumps({key: None, other_key: False, "unrelated": "keep"}).encode()
    path.write_bytes(before)
    response = client.get(f"/api/memory/{endpoint}")
    assert response.status_code == 503
    assert response.json()["code"] == "invalid_memory_setting"
    assert "enabled" not in response.json()
    assert client.get(f"/api/memory/{other_endpoint}").json() == {"enabled": False}
    assert path.read_bytes() == before
