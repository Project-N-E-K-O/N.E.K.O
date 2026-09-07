from __future__ import annotations

import json
import threading
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.config.schema import PluginModelRequirementSchema
from plugin.server.application.model_config_service import ModelConfigService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.model_config import SECRET_MASK
from plugin.server.infrastructure.model_config_store import CONFIG_FILENAME, ModelConfigStore
from plugin.server.routes import model_config
from utils.file_utils import atomic_write_json


pytestmark = pytest.mark.plugin_unit
PREFIX = "/api/model-config"
SECRET = "sk-model-route-private-credential"
SLOT = {
    "name": "Plugin analysis",
    "protocol": "openai_chat",
    "base_url": "https://example.test/v1",
    "model": "vision-model",
    "api_key": SECRET,
    "capabilities": ["text", "image_input"],
}


class TemporaryConfigManager:
    """Only the plugin model file is accessible; never consult live config."""

    def __init__(self, root: Path):
        self.root = root
        self.io_threads: list[int] = []
        self.saved_files: list[str] = []

    def get_runtime_config_path(self, filename: str) -> Path:
        assert filename == CONFIG_FILENAME
        self.io_threads.append(threading.get_ident())
        return self.root / filename

    def save_json_config(self, filename: str, data: dict) -> None:
        self.saved_files.append(filename)
        atomic_write_json(self.get_runtime_config_path(filename), data)


@pytest.fixture
def model_setup(tmp_path, monkeypatch):
    from utils import cloudsave_runtime

    monkeypatch.setattr(
        cloudsave_runtime, "cloudsave_writable_transaction", lambda *_args, **_kwargs: nullcontext()
    )
    cm = TemporaryConfigManager(tmp_path)
    requirements_threads = []

    def requirements(plugin_id):
        requirements_threads.append(threading.get_ident())
        if plugin_id == "legacy":
            return {}
        if plugin_id not in {"first", "second"}:
            raise ServerDomainError("PLUGIN_NOT_FOUND", "Plugin manifest not found", 404)
        return {"analysis": PluginModelRequirementSchema(label="Analysis", capabilities=["image_input"])}

    service = ModelConfigService(ModelConfigStore(cm), requirements_loader=requirements)
    monkeypatch.setattr(model_config, "service", service)
    app = FastAPI()
    app.include_router(model_config.router)
    return app, cm, requirements_threads


@pytest.fixture
async def model_client(model_setup):
    app, _, _ = model_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


async def create_slot(client, **overrides):
    response = await client.post(f"{PREFIX}/slots", json={**SLOT, **overrides})
    assert response.status_code == 201, response.text
    assert SECRET not in response.text
    return response.json()


async def test_crud_preserves_credentials_and_main_configuration(model_client, model_setup):
    _, cm, _ = model_setup
    core_path = cm.root / "core_config.json"
    core_data = b'{"CORE_API_TYPE":"existing-main","AGENT_MODEL":"existing-agent"}\n'
    core_path.write_bytes(core_data)

    response = await model_client.get(f"{PREFIX}/slots")
    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "slots": []}

    created = await create_slot(model_client)
    assert created["api_key"] == SECRET_MASK
    assert created["bound_by"] == []
    slot_id = created["id"]
    updated = await model_client.patch(
        f"{PREFIX}/slots/{slot_id}", json={"name": "Renamed"}
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == slot_id
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["api_key"] == SECRET_MASK
    assert SECRET not in updated.text

    fetched = await model_client.get(f"{PREFIX}/slots/{slot_id}")
    listed = await model_client.get(f"{PREFIX}/slots")
    assert fetched.json() == updated.json()
    assert listed.json()["slots"] == [updated.json()]
    stored = json.loads((cm.root / CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert stored["slots"][slot_id]["api_key"] == SECRET
    assert cm.saved_files == [CONFIG_FILENAME, CONFIG_FILENAME]
    assert core_path.read_bytes() == core_data


async def test_shared_slot_binding_blocks_delete_until_every_plugin_unbinds(model_client):
    slot_id = (await create_slot(model_client))["id"]
    for plugin_id in ("first", "second"):
        initial = await model_client.get(f"{PREFIX}/plugins/{plugin_id}/bindings")
        assert initial.json()["ready"] is False
        assert initial.json()["requirements"]["analysis"]["status"] == "unbound"
        response = await model_client.put(
            f"{PREFIX}/plugins/{plugin_id}/bindings/analysis", json={"slot_id": slot_id, "expected_version": 0}
        )
        assert response.status_code == 200
        bound = await model_client.get(f"{PREFIX}/plugins/{plugin_id}/bindings")
        assert bound.json()["ready"] is True
        assert bound.json()["bindings"] == {"analysis": slot_id}

    slot = (await model_client.get(f"{PREFIX}/slots/{slot_id}")).json()
    assert slot["bound_by"] == [
        {"plugin_id": "first", "usage_id": "analysis", "version": 1},
        {"plugin_id": "second", "usage_id": "analysis", "version": 1},
    ]
    for plugin_id in ("first", "second"):
        blocked = await model_client.delete(f"{PREFIX}/slots/{slot_id}")
        assert blocked.status_code == 409
        assert blocked.headers["x-error-code"] == "MODEL_SLOT_IN_USE"
        unbound = await model_client.delete(f"{PREFIX}/plugins/{plugin_id}/bindings/analysis", params={"expected_version": 1})
        assert unbound.status_code == 200

    deleted = await model_client.delete(f"{PREFIX}/slots/{slot_id}")
    assert deleted.json() == {"success": True}
    missing = await model_client.get(f"{PREFIX}/slots/{slot_id}")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "MODEL_SLOT_NOT_FOUND"


async def test_legacy_plugin_and_undeclared_usage(model_client):
    slot_id = (await create_slot(model_client))["id"]
    legacy = await model_client.get(f"{PREFIX}/plugins/legacy/bindings")
    assert legacy.json() == {"plugin_id": "legacy", "requirements": {}, "bindings": {}, "ready": True}
    response = await model_client.put(
        f"{PREFIX}/plugins/first/bindings/undeclared", json={"slot_id": slot_id, "expected_version": 0}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "MODEL_USAGE_NOT_DECLARED"
    missing = await model_client.get(f"{PREFIX}/plugins/missing/bindings")
    assert missing.status_code == 404


@pytest.mark.parametrize(
    "invalid",
    [
        {"protocol": "unrecognized"},
        {"base_url": "https://example.test/v1?key=" + SECRET},
        {"api_key": [SECRET]},
        {"capabilities": ["native_video"]},
        {"defaults": {"max_output_tokens": 0}},
        {"name": "  "},
        {"timeout_seconds": 0},
        {"unknown": SECRET},
    ],
)
async def test_invalid_slot_fields_are_rejected_without_echoing_secrets(model_client, invalid):
    response = await model_client.post(f"{PREFIX}/slots", json={**SLOT, **invalid})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MODEL_SLOT_INVALID"
    assert SECRET not in response.text
    listed = await model_client.get(f"{PREFIX}/slots")
    assert listed.json()["slots"] == []


@pytest.mark.parametrize("payload", [{}, {"slot_id": 12}, {"slot_id": "slot_x", "api_key": SECRET}])
async def test_binding_payload_is_strict_and_does_not_echo_secrets(model_client, payload):
    response = await model_client.put(f"{PREFIX}/plugins/first/bindings/analysis", json=payload)
    assert response.status_code == 422
    assert SECRET not in response.text


@pytest.mark.parametrize("payload", [[{"api_key": SECRET}], SECRET])
async def test_non_object_bodies_do_not_echo_credentials(model_client, payload):
    slot_id = (await create_slot(model_client))["id"]
    for method, path in [
        ("POST", f"{PREFIX}/slots"),
        ("PATCH", f"{PREFIX}/slots/{slot_id}"),
        ("PUT", f"{PREFIX}/plugins/first/bindings/analysis"),
    ]:
        response = await model_client.request(method, path, json=payload)
        assert response.status_code == 422
        assert SECRET not in response.text


async def test_invalid_json_does_not_echo_credentials(model_client):
    response = await model_client.post(
        f"{PREFIX}/slots",
        content='{"api_key":"' + SECRET + '",',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert SECRET not in response.text


async def test_model_config_routes_are_included_in_plugin_app():
    from plugin.server.http_app import build_plugin_server_app

    app = build_plugin_server_app()
    paths = {route.path for route in app.routes}
    assert f"{PREFIX}/slots" in paths
    assert f"{PREFIX}/plugins/{{plugin_id}}/bindings/{{usage_id}}" in paths


async def test_incompatible_binding_is_rejected(model_client):
    slot_id = (await create_slot(model_client, capabilities=["text"]))["id"]
    response = await model_client.put(
        f"{PREFIX}/plugins/first/bindings/analysis", json={"slot_id": slot_id, "expected_version": 0}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MODEL_CAPABILITY_MISMATCH"


async def test_storage_error_does_not_leak_credentials(model_client, model_setup, monkeypatch):
    _, cm, _ = model_setup

    def fail_save(*_args):
        raise OSError("sensitive storage error " + SECRET)

    monkeypatch.setattr(cm, "save_json_config", fail_save)
    response = await model_client.post(f"{PREFIX}/slots", json=SLOT)
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "MODEL_CONFIG_WRITE_FAILED"
    assert SECRET not in response.text


async def test_corrupt_stored_configuration_is_not_exposed(model_client, model_setup):
    _, cm, _ = model_setup
    path = cm.root / CONFIG_FILENAME
    path.write_text(json.dumps({"api_key": SECRET}), encoding="utf-8")
    before = path.read_bytes()
    response = await model_client.get(f"{PREFIX}/slots")
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "MODEL_CONFIG_INVALID"
    assert SECRET not in response.text
    assert path.read_bytes() == before


async def test_config_and_manifest_io_run_outside_http_event_loop(model_client, model_setup):
    _, cm, requirements_threads = model_setup
    event_loop_thread = threading.get_ident()
    slot_id = (await create_slot(model_client))["id"]
    for path in ("slots", f"slots/{slot_id}", "plugins/first/bindings"):
        response = await model_client.get(f"{PREFIX}/{path}")
        assert response.status_code == 200
    updated = await model_client.patch(f"{PREFIX}/slots/{slot_id}", json={"name": "Updated"})
    assert updated.status_code == 200
    bound = await model_client.put(
        f"{PREFIX}/plugins/first/bindings/analysis", json={"slot_id": slot_id, "expected_version": 0}
    )
    assert bound.status_code == 200
    unbound = await model_client.delete(f"{PREFIX}/plugins/first/bindings/analysis", params={"expected_version": 1})
    assert unbound.status_code == 200
    deleted = await model_client.delete(f"{PREFIX}/slots/{slot_id}")
    assert deleted.status_code == 200
    assert cm.io_threads and requirements_threads
    assert event_loop_thread not in cm.io_threads
    assert event_loop_thread not in requirements_threads


async def test_binding_mutations_require_versions_and_preserve_delete_tombstones(model_client, model_setup):
    slot_id = (await create_slot(model_client))["id"]
    url = f"{PREFIX}/plugins/first/bindings/analysis"
    missing = await model_client.put(url, json={"slot_id": slot_id})
    assert missing.status_code == 422
    missing = await model_client.delete(url)
    assert missing.status_code == 422
    for invalid in (True, -1, "0", None):
        rejected = await model_client.put(url, json={"slot_id": slot_id, "expected_version": invalid})
        assert rejected.status_code == 422
    # Even deleting an empty binding invalidates requests based on that snapshot.
    deleted = await model_client.delete(url, params={"expected_version": 0})
    assert deleted.json()["version"] == 1
    stale = await model_client.put(url, json={"slot_id": slot_id, "expected_version": 0})
    assert stale.status_code == 409
    assert stale.headers["x-error-code"] == "MODEL_BINDING_CONFLICT"
    accepted = await model_client.put(url, json={"slot_id": slot_id, "expected_version": 1})
    assert accepted.json()["version"] == 2
    stale_delete = await model_client.delete(url, params={"expected_version": 1})
    assert stale_delete.status_code == 409
    current = await model_client.get(f"{PREFIX}/plugins/first/bindings")
    assert current.json()["bindings"] == {"analysis": slot_id}
    assert current.json()["requirements"]["analysis"]["version"] == 2
    # A new store instance sees the durable version, including after unbinding.
    _, cm, _ = model_setup
    deleted = await model_client.delete(url, params={"expected_version": 2})
    assert deleted.status_code == 200
    stored = ModelConfigStore(cm).read()
    assert stored.bindings == {}
    assert stored.binding_versions["first"]["analysis"] == 3


async def test_delayed_old_binding_cannot_overwrite_newer_write(model_client, monkeypatch):
    import asyncio

    old_slot = (await create_slot(model_client, name="Old"))["id"]
    new_slot = (await create_slot(model_client, name="New"))["id"]
    url = f"{PREFIX}/plugins/first/bindings/analysis"
    entered, release = threading.Event(), threading.Event()
    original = model_config.service.requirements_loader

    def delayed_requirements(plugin_id):
        if not entered.is_set():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release the old request")
        return original(plugin_id)

    monkeypatch.setattr(model_config.service, "requirements_loader", delayed_requirements)
    old = asyncio.create_task(model_client.put(url, json={"slot_id": old_slot, "expected_version": 0}))
    try:
        reached = await asyncio.to_thread(entered.wait, 5)
        assert reached
        new = await model_client.put(url, json={"slot_id": new_slot, "expected_version": 0})
        assert new.status_code == 200
    finally:
        release.set()
        old_result = await old
    assert old_result.status_code == 409
    current = await model_client.get(f"{PREFIX}/plugins/first/bindings")
    assert current.json()["bindings"] == {"analysis": new_slot}


@pytest.mark.parametrize("key", ["prefix......suffix-extra", "abcdef......wxyz", "******", SECRET_MASK])
async def test_literal_key_round_trip_and_omitted_key_preservation(model_client, model_setup, key):
    created = await create_slot(model_client, api_key=key)
    slot_id = created["id"]
    assert created["api_key_preview"] != key
    response = await model_client.patch(f"{PREFIX}/slots/{slot_id}", json={"api_key": "temporary"})
    assert response.status_code == 200
    response = await model_client.patch(f"{PREFIX}/slots/{slot_id}", json={"api_key": key})
    assert response.status_code == 200
    response = await model_client.patch(f"{PREFIX}/slots/{slot_id}", json={"name": "Rename only"})
    assert response.status_code == 200
    _, cm, _ = model_setup
    assert ModelConfigStore(cm).read().slots[slot_id].api_key == key


async def test_confirmation_fences_a_write_that_has_not_committed(model_client):
    slot_id = (await create_slot(model_client))["id"]
    url = f"{PREFIX}/plugins/first/bindings/analysis"
    confirmed = await model_client.post(url + "/confirm", json={"expected_version": 0})
    assert confirmed.json() == {"slot_id": None, "version": 1}
    late = await model_client.put(url, json={"slot_id": slot_id, "expected_version": 0})
    assert late.status_code == 409
    repeated = await model_client.post(url + "/confirm", json={"expected_version": 0})
    assert repeated.json() == confirmed.json()
    saved = await model_client.put(url, json={"slot_id": slot_id, "expected_version": 1})
    assert saved.status_code == 200
    confirmed = await model_client.post(url + "/confirm", json={"expected_version": 1})
    assert confirmed.json() == {"slot_id": slot_id, "version": 2}
    # An uncertain DELETE is fenced too, preserving the currently bound slot.
    confirmed = await model_client.post(url + "/confirm", json={"expected_version": 2})
    assert confirmed.json() == {"slot_id": slot_id, "version": 3}
    late = await model_client.delete(url, params={"expected_version": 2})
    assert late.status_code == 409
