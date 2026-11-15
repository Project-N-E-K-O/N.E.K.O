from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from plugin.config.schema import PluginModelRequirementSchema
from plugin.server.application.model_config_service import (
    ModelConfigService,
    host_slot_seed_id,
    load_model_requirements,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.model_config import SECRET_MASK, ModelSlot
from plugin.server.infrastructure.model_config_store import (
    CONFIG_FILENAME,
    ModelConfigStore,
)
from utils.file_utils import atomic_write_json


pytestmark = pytest.mark.plugin_unit


class TempConfigManager:
    def __init__(self, root):
        self.config_dir = root / "config"
        self.transactions = []

    def get_runtime_config_path(self, filename):
        return self.config_dir / filename

    def save_json_config(self, filename, data):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.get_runtime_config_path(filename), data)


@pytest.fixture
def host_api_unconfigured(monkeypatch):
    """Keep host_status resolution deterministic without touching real config."""
    import utils.config_manager as config_manager

    class Manager:
        def get_model_api_config(self, model_type):
            return {}

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: Manager())


@pytest.fixture
def config_env(tmp_path, monkeypatch, host_api_unconfigured):
    import utils.cloudsave_runtime as cloudsave

    cm = TempConfigManager(tmp_path)

    @contextmanager
    def writable_transaction(manager, **kwargs):
        assert manager is cm
        cm.transactions.append(kwargs)
        yield

    monkeypatch.setattr(cloudsave, "cloudsave_writable_transaction", writable_transaction)
    declarations = {
        "alpha": {"analysis": PluginModelRequirementSchema(label="Analysis", capabilities=["image_input"])},
        "beta": {"analysis": PluginModelRequirementSchema(label="Analysis")},
        "legacy": {},
    }

    def requirements(plugin_id):
        if plugin_id not in declarations:
            raise ServerDomainError("PLUGIN_NOT_FOUND", "Plugin not found", 404)
        return declarations[plugin_id]

    service = ModelConfigService(ModelConfigStore(cm), requirements)
    return cm, service, declarations


def slot_payload(**updates):
    return {
        "name": "Shared model",
        "protocol": "openai_chat",
        "base_url": "https://models.example/v1/",
        "model": "example-model",
        "api_key": "test-secret-key",
        "capabilities": ["text", "image_input", "streaming"],
        **updates,
    }


def test_first_list_seeds_default_host_slots_and_read_stays_read(config_env):
    cm, service, _ = config_env
    listed = service.list_slots()
    assert listed["schema_version"] == 1
    seeded = {slot["id"]: slot for slot in listed["slots"]}
    assert set(seeded) == {host_slot_seed_id("assist"), host_slot_seed_id("core")}
    for slot in seeded.values():
        assert slot["source"] == "host_follow"
        assert slot["base_url"] == slot["model"] == slot["api_key"] == ""
        assert slot["capabilities"] == ["text", "streaming"]
        assert slot["host_status"] == {"configured": False, "error_code": "host_api_not_configured"}
    assert seeded[host_slot_seed_id("assist")]["host_api"] == "assist"
    assert seeded[host_slot_seed_id("core")]["host_api"] == "core"
    assert cm.get_runtime_config_path(CONFIG_FILENAME).exists()
    # Seeding is a one-time write; later lists never touch the store again.
    writes = len(cm.transactions)
    assert service.list_slots() == listed
    assert len(cm.transactions) == writes
    assert service.get_bindings("legacy")["ready"] is True


def test_shared_slot_rename_and_secret_roundtrip_leave_core_config_untouched(config_env):
    cm, service, _ = config_env
    cm.config_dir.mkdir(parents=True)
    core_path = cm.config_dir / "core_config.json"
    original_core = b'{"coreApi":"free","agentModelId":"existing"}'
    core_path.write_bytes(original_core)

    created = service.create_slot(slot_payload())
    slot_id = created["id"]
    assert created["base_url"] == "https://models.example/v1"
    assert created["api_key"] == SECRET_MASK
    for plugin_id in ("alpha", "beta"):
        service.set_binding(plugin_id, "analysis", slot_id, 0)
    renamed = service.update_slot(slot_id, {"name": "Renamed"})
    assert renamed["id"] == slot_id
    assert len(renamed["bound_by"]) == 2
    assert service.get_bindings("alpha")["requirements"]["analysis"]["status"] == "bound"
    assert service.get_bindings("beta")["bindings"] == {"analysis": slot_id}
    assert "test-secret-key" not in json.dumps(service.list_slots())
    persisted = json.loads(cm.get_runtime_config_path(CONFIG_FILENAME).read_text())
    assert persisted["slots"][slot_id]["api_key"] == "test-secret-key"
    assert core_path.read_bytes() == original_core
    assert all(tx == {"operation": "save", "target": CONFIG_FILENAME} for tx in cm.transactions)


@pytest.mark.parametrize("key", [SECRET_MASK, "********", "******", "abcdef......wxyz", "prefix......suffix-extra"])
def test_opaque_keys_can_be_created_replaced_and_cleared(config_env, key):
    _, service, _ = config_env
    slot_id = service.create_slot(slot_payload(api_key=key))["id"]
    assert service.store.read().slots[slot_id].api_key == key
    assert service.get_slot(slot_id)["api_key_preview"] != key
    service.update_slot(slot_id, {"api_key": "old-key"})
    service.update_slot(slot_id, {"api_key": key})
    assert service.store.read().slots[slot_id].api_key == key
    service.update_slot(slot_id, {"name": "Renamed"})
    assert service.store.read().slots[slot_id].api_key == key
    service.update_slot(slot_id, {"api_key": ""})
    assert service.store.read().slots[slot_id].api_key == ""


@pytest.mark.parametrize("change", [
    {"base_url": "https://another.example/v1"},
    {"protocol": "anthropic_messages"},
])
def test_endpoint_change_does_not_reuse_previous_credential(config_env, change):
    _, service, _ = config_env
    slot_id = service.create_slot(slot_payload())["id"]
    with pytest.raises(ServerDomainError) as error:
        service.update_slot(slot_id, change)
    assert error.value.code == "MODEL_CREDENTIAL_UPDATE_REQUIRED"
    assert service.store.read().slots[slot_id].api_key == "test-secret-key"
    service.update_slot(slot_id, {**change, "api_key": "new-test-key"})
    assert service.store.read().slots[slot_id].api_key == "new-test-key"


def test_partial_defaults_update_preserves_omitted_fields(config_env):
    _, service, _ = config_env
    slot_id = service.create_slot(slot_payload(defaults={"temperature": 0.7, "max_output_tokens": 8192}))["id"]
    service.update_slot(slot_id, {"defaults": {"temperature": 0.2}})
    defaults = service.store.read().slots[slot_id].defaults
    assert (defaults.temperature, defaults.max_output_tokens) == (0.2, 8192)
    service.update_slot(slot_id, {"defaults": {"max_output_tokens": None}})
    defaults = service.store.read().slots[slot_id].defaults
    assert (defaults.temperature, defaults.max_output_tokens) == (0.2, None)


def test_binding_must_be_declared_and_meet_capabilities(config_env):
    cm, service, _ = config_env
    slot_id = service.create_slot(slot_payload(capabilities=["text"]))["id"]
    for plugin_id, usage_id, code in [
        ("alpha", "analysis", "MODEL_CAPABILITY_MISMATCH"),
        ("beta", "arbitrary", "MODEL_USAGE_NOT_DECLARED"),
        ("legacy", "analysis", "MODEL_USAGE_NOT_DECLARED"),
        ("missing", "analysis", "PLUGIN_NOT_FOUND"),
    ]:
        with pytest.raises(ServerDomainError) as error:
            service.set_binding(plugin_id, usage_id, slot_id, 0)
        assert error.value.code == code
    assert service.store.read().bindings == {}
    assert service.get_bindings("alpha")["ready"] is False


def test_capability_edit_cannot_break_existing_binding(config_env):
    _, service, declarations = config_env
    slot_id = service.create_slot(slot_payload())["id"]
    service.set_binding("alpha", "analysis", slot_id, 0)
    with pytest.raises(ServerDomainError) as error:
        service.update_slot(slot_id, {"capabilities": ["text"]})
    assert error.value.code == "MODEL_CAPABILITY_MISMATCH"
    assert "image_input" in service.store.read().slots[slot_id].capabilities
    declarations["alpha"]["analysis"] = PluginModelRequirementSchema(label="New requirement", capabilities=["tool_calling"])
    assert service.get_bindings("alpha")["requirements"]["analysis"]["status"] == "incompatible"


def test_delete_requires_explicit_unbind_and_stale_binding_can_be_cleaned(config_env):
    _, service, declarations = config_env
    slot_id = service.create_slot(slot_payload())["id"]
    service.set_binding("alpha", "analysis", slot_id, 0)
    del declarations["alpha"]
    assert service.get_slot(slot_id)["bound_by"] == [{"plugin_id": "alpha", "usage_id": "analysis", "version": 1}]
    with pytest.raises(ServerDomainError) as error:
        service.delete_slot(slot_id)
    assert error.value.code == "MODEL_SLOT_IN_USE"
    service.delete_binding("alpha", "analysis", 1)
    service.delete_slot(slot_id)
    assert service.store.read().bindings == {}
    assert {slot["id"] for slot in service.list_slots()["slots"]} == {
        host_slot_seed_id("assist"), host_slot_seed_id("core"),
    }


def test_fallback_references_cannot_dangle_cycle_or_lose_capabilities(config_env):
    _, service, _ = config_env
    first = service.create_slot(slot_payload())["id"]
    second = service.create_slot(slot_payload(fallback_slot_id=first))["id"]
    with pytest.raises(ServerDomainError) as error:
        service.update_slot(first, {"fallback_slot_id": second})
    assert error.value.code == "MODEL_FALLBACK_CYCLE"
    with pytest.raises(ServerDomainError, match="fallback"):
        service.delete_slot(first)
    with pytest.raises(ServerDomainError) as error:
        service.update_slot(first, {"capabilities": ["text"]})
    assert error.value.code == "MODEL_CAPABILITY_MISMATCH"
    with pytest.raises(ServerDomainError) as error:
        service.create_slot(slot_payload(fallback_slot_id="slot_" + "0" * 32))
    assert error.value.code == "MODEL_SLOT_NOT_FOUND"


@pytest.mark.parametrize("raw", [
    "{invalid json with secret-key",
    '{"schema_version":2,"slots":{},"bindings":{}}',
    '{"schema_version":true,"slots":{},"bindings":{}}',
    '{"schema_version":1,"slots":[],"bindings":{}}',
    '{"schema_version":1,"slots":{},"bindings":{"alpha":{"analysis":"slot_00000000000000000000000000000000"}}}',
])
def test_invalid_storage_is_reported_without_overwrite_or_secret_leak(config_env, raw):
    cm, service, _ = config_env
    path = cm.get_runtime_config_path(CONFIG_FILENAME)
    path.parent.mkdir(parents=True)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ServerDomainError) as error:
        service.create_slot(slot_payload())
    assert "secret-key" not in str(error.value)
    assert path.read_text(encoding="utf-8") == raw


def test_concurrent_services_do_not_lose_updates(config_env):
    cm, _, _ = config_env

    def create(index):
        service = ModelConfigService(ModelConfigStore(cm))
        return service.create_slot(slot_payload(name=f"model-{index}"))["id"]

    with ThreadPoolExecutor(max_workers=6) as executor:
        ids = list(executor.map(create, range(18)))
    assert set(ModelConfigStore(cm).read().slots) == set(ids)


def test_write_fence_rejection_does_not_create_config(config_env, monkeypatch):
    import utils.cloudsave_runtime as cloudsave

    cm, service, _ = config_env

    @contextmanager
    def blocked(*args, **kwargs):
        raise cloudsave.MaintenanceModeError("maintenance_readonly")
        yield  # pragma: no cover

    monkeypatch.setattr(cloudsave, "cloudsave_writable_transaction", blocked)
    with pytest.raises(cloudsave.MaintenanceModeError):
        service.create_slot(slot_payload())
    assert not cm.get_runtime_config_path(CONFIG_FILENAME).exists()


@pytest.mark.parametrize("updates", [
    {"base_url": "ftp://models.example/v1"},
    {"base_url": "https://user:secret@models.example/v1"},
    {"base_url": "https://models.example/v1?api_key=secret"},
    {"base_url": "https://models.example:bad/v1"},
    {"model": "  "},
    {"protocol": "realtime"},
    {"api_key": "abc\r\ndef"},
    {"api_key": "••••••"},
    {"api_key": "sk-密钥"},
    {"timeout_seconds": float("inf")},
    {"timeout_seconds": True},
    {"defaults": {"max_output_tokens": True}},
    {"capabilities": ["audio_input"]},
])
def test_invalid_slot_configuration_is_rejected(config_env, updates):
    _, service, _ = config_env
    with pytest.raises(ServerDomainError) as error:
        service.create_slot(slot_payload(**updates))
    assert error.value.code == "MODEL_SLOT_INVALID"


def test_manifest_is_read_directly_without_runtime_overrides(tmp_path, monkeypatch):
    from plugin.server.application import model_config_service as module

    manifest = tmp_path / "plugin.toml"
    manifest.write_text('[plugin]\nid="alpha"\n[plugin.models.analysis]\nlabel="Analysis"\ncapabilities=["image_input"]\n')
    monkeypatch.setattr(module, "get_plugin_manifest_path", lambda plugin_id: manifest)
    assert load_model_requirements("alpha")["analysis"].capabilities == ["image_input"]
    manifest.write_text('[plugin]\nid="alpha"\n')
    assert load_model_requirements("alpha") == {}
    with pytest.raises(ServerDomainError):
        load_model_requirements("other")


def test_key_not_in_model_repr():
    slot = ModelSlot.model_validate(slot_payload())
    assert "test-secret-key" not in repr(slot)


def test_store_with_real_config_manager_and_write_transaction(tmp_path, monkeypatch):
    from utils.config_manager import ConfigManager

    # Keep both migration candidates and the selected root inside the fixture.
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(ConfigManager, "_get_documents_directory", lambda self: tmp_path)
    monkeypatch.setattr(ConfigManager, "_get_standard_data_directory_candidates", lambda self: [tmp_path])
    monkeypatch.setattr(ConfigManager, "get_legacy_app_root_candidates", lambda self: [])
    monkeypatch.setattr(ConfigManager, "_get_project_config_directory", lambda self: tmp_path / "project_config")
    cm = ConfigManager("N.E.K.O")
    service = ModelConfigService(ModelConfigStore(cm))
    created = service.create_slot(slot_payload())
    slot_id = created["id"]
    service.update_slot(slot_id, {"name": "Saved through real transaction"})
    fresh = ModelConfigService(ModelConfigStore(cm))
    assert fresh.get_slot(slot_id)["name"] == "Saved through real transaction"
    with pytest.raises(ServerDomainError) as error:
        fresh.delete_slot("not-a-slot")
    assert error.value.code == "MODEL_SLOT_NOT_FOUND"
    fresh.delete_slot(slot_id)
    assert {slot["id"] for slot in fresh.list_slots()["slots"]} == {
        host_slot_seed_id("assist"), host_slot_seed_id("core"),
    }


@pytest.mark.parametrize("key,preview", [
    ("sk-abcdef-secret-middle-1234", "sk-abc......1234"),
    ("1234567890", "******"),
    ("short", "******"),
    ("", ""),
])
def test_slot_returns_only_masked_preview(config_env, key, preview, host_api_unconfigured):
    _, service, _ = config_env
    created = service.create_slot(slot_payload(api_key=key))
    assert created["api_key_preview"] == preview
    assert created["api_key"] == (SECRET_MASK if key else "")
    assert service.get_slot(created["id"])["api_key_preview"] == preview
    listed = next(slot for slot in service.list_slots()["slots"] if slot["id"] == created["id"])
    assert listed["api_key_preview"] == preview
    if key:
        service.update_slot(created["id"], {"name": "Renamed"})
        assert service.store.read().slots[created["id"]].api_key == key
    assert "api_key_preview" not in service.store.read().slots[created["id"]].model_dump()


def test_output_default_limit_is_validated_before_saving(config_env):
    from plugin.server.domain.model_config import MAX_OUTPUT_TOKENS
    from plugin.server.model_gateway.request import prepare_chat_request

    _, service, _ = config_env
    with pytest.raises(ServerDomainError) as error:
        service.create_slot(slot_payload(defaults={"max_output_tokens": MAX_OUTPUT_TOKENS + 1}))
    assert error.value.code == "MODEL_SLOT_INVALID"
    assert all(slot["source"] == "host_follow" for slot in service.list_slots()["slots"])
    created = service.create_slot(slot_payload(defaults={"max_output_tokens": MAX_OUTPUT_TOKENS}))
    with pytest.raises(ServerDomainError):
        service.update_slot(created["id"], {"defaults": {"max_output_tokens": MAX_OUTPUT_TOKENS + 1}})
    slot = service.store.read().slots[created["id"]]
    prepared = prepare_chat_request(slot, {"model": "analysis", "messages": [{"role": "user", "content": "Hi"}]})
    assert prepared["max_completion_tokens"] == MAX_OUTPUT_TOKENS


def host_payload(**updates):
    return {"name": "Host assist", "source": "host_follow", "host_api": "assist", **updates}


def test_host_follow_slot_stores_no_credentials(config_env, monkeypatch):
    import utils.config_manager as config_manager

    class Manager:
        def get_model_api_config(self, model_type):
            return {"model": "host-model", "base_url": "https://host.test/v1", "api_key": "sk-host"}

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: Manager())
    _, service, _ = config_env
    created = service.create_slot(host_payload())
    assert created["source"] == "host_follow"
    assert created["api_key"] == ""
    assert created["host_status"] == {"configured": True, "model": "host-model", "protocol": "openai_chat", "free": False}
    stored = service.store.read().slots[created["id"]]
    assert stored.base_url == stored.model == stored.api_key == ""
    assert "sk-host" not in json.dumps(service.list_slots())


def test_host_follow_slot_rejects_stored_endpoint_or_key(config_env):
    _, service, _ = config_env
    for updates in ({"base_url": "https://x.test/v1"}, {"model": "m"}, {"api_key": "k"}, {"host_api": None}):
        with pytest.raises(ServerDomainError) as error:
            service.create_slot(host_payload(**updates))
        assert error.value.code == "MODEL_SLOT_INVALID"
    with pytest.raises(ServerDomainError):
        service.create_slot(slot_payload(host_api="assist"))


def test_switching_sources_clears_inapplicable_fields(config_env):
    _, service, _ = config_env
    manual_id = service.create_slot(slot_payload())["id"]
    switched = service.update_slot(manual_id, {"source": "host_follow", "host_api": "assist"})
    assert switched["base_url"] == switched["model"] == ""
    assert switched["api_key"] == ""
    back = service.update_slot(manual_id, {
        "source": "manual", "protocol": "openai_chat",
        "base_url": "https://new.test/v1", "model": "new-model", "api_key": "new-key",
    })
    assert back["host_api"] is None
    assert back["model"] == "new-model"
    with pytest.raises(ServerDomainError):
        service.update_slot(manual_id, {"source": "manual", "base_url": "", "model": ""})


def test_manual_endpoint_change_still_requires_key_update(config_env):
    _, service, _ = config_env
    slot_id = service.create_slot(slot_payload())["id"]
    with pytest.raises(ServerDomainError) as error:
        service.update_slot(slot_id, {"base_url": "https://other.test/v1"})
    assert error.value.code == "MODEL_CREDENTIAL_UPDATE_REQUIRED"


def test_ensure_host_follow_binding_creates_shared_slot_and_binds_compatible_usages(config_env):
    cm, service, declarations = config_env
    declarations["beta"]["vision"] = PluginModelRequirementSchema(label="Vision", capabilities=["image_input"])
    first = service.ensure_host_follow_binding("beta")
    assert first["bound"] == ["analysis"]
    slot_id = first["slot_id"]
    assert service.get_bindings("beta")["bindings"] == {"analysis": slot_id}
    assert service.get_bindings("beta")["requirements"]["vision"]["status"] == "unbound"
    # A second plugin reuses the same shared slot; existing bindings are untouched.
    service.set_binding("beta", "analysis", service.create_slot(slot_payload())["id"], 1)
    second = service.ensure_host_follow_binding("alpha")
    assert second["slot_id"] == slot_id
    assert second["bound"] == []  # alpha requires image_input, the shared slot is text+streaming
    assert service.get_bindings("beta")["bindings"]["analysis"] != slot_id
    # The two seeded defaults plus the manual slot beta was rebound to.
    assert len(service.list_slots()["slots"]) == 3


def test_ensure_host_follow_binding_is_idempotent_and_skips_undeclared_plugins(config_env):
    _, service, _ = config_env
    assert service.ensure_host_follow_binding("legacy") == {"bound": [], "slot_id": None}
    assert {slot["id"] for slot in service.list_slots()["slots"]} == {
        host_slot_seed_id("assist"), host_slot_seed_id("core"),
    }
    first = service.ensure_host_follow_binding("beta")
    again = service.ensure_host_follow_binding("beta")
    assert again == {"bound": [], "slot_id": first["slot_id"]}
    assert len(service.list_slots()["slots"]) == 2
    version = service.get_bindings("beta")["requirements"]["analysis"]["version"]
    assert version == 1


def test_deleted_default_slots_are_not_resurrected(config_env):
    _, service, _ = config_env
    service.list_slots()
    service.delete_slot(host_slot_seed_id("core"))
    remaining = {slot["id"] for slot in service.list_slots()["slots"]}
    assert remaining == {host_slot_seed_id("assist")}
    # A later install must not resurrect the deleted default either.
    service.ensure_host_follow_binding("beta")
    assert {slot["id"] for slot in service.list_slots()["slots"]} == remaining


def test_install_before_first_list_seeds_defaults_and_binds_shared_assist(config_env):
    _, service, _ = config_env
    result = service.ensure_host_follow_binding("beta")
    assert result["slot_id"] == host_slot_seed_id("assist")
    assert result["bound"] == ["analysis"]
    assert {slot["id"] for slot in service.list_slots()["slots"]} == {
        host_slot_seed_id("assist"), host_slot_seed_id("core"),
    }
    assert service.get_bindings("beta")["bindings"] == {"analysis": host_slot_seed_id("assist")}


def test_existing_assist_slot_is_reused_without_duplicate_seed(config_env):
    _, service, _ = config_env
    manual_assist = service.create_slot(host_payload(name="My own assist"))["id"]
    result = service.ensure_host_follow_binding("beta")
    assert result["slot_id"] == manual_assist
    slots = {slot["id"]: slot for slot in service.list_slots()["slots"]}
    assert host_slot_seed_id("assist") not in slots  # no duplicate assist slot
    assert host_slot_seed_id("core") in slots
    assert service.get_bindings("beta")["bindings"] == {"analysis": manual_assist}


def test_ensure_default_slots_is_idempotent_and_never_raises(config_env, monkeypatch):
    cm, service, _ = config_env
    assert service.ensure_default_slots() == {"seeded": True}
    assert service.ensure_default_slots() == {"seeded": False}
    assert {slot["id"] for slot in service.list_slots()["slots"]} == {
        host_slot_seed_id("assist"), host_slot_seed_id("core"),
    }
    # A read-only storage window must degrade to lazy seeding, not raise.
    import utils.cloudsave_runtime as cloudsave

    fresh = ModelConfigService(ModelConfigStore(TempConfigManager(cm.config_dir.parent / "other")))

    @contextmanager
    def blocked(*args, **kwargs):
        raise cloudsave.MaintenanceModeError("maintenance_readonly")
        yield  # pragma: no cover

    monkeypatch.setattr(cloudsave, "cloudsave_writable_transaction", blocked)
    assert fresh.ensure_default_slots() == {"seeded": False}


def test_concurrent_first_lists_seed_exactly_once(config_env):
    cm, _, _ = config_env

    def list_slots(_index):
        return ModelConfigService(ModelConfigStore(cm)).list_slots()

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(list_slots, range(12)))
    expected = {host_slot_seed_id("assist"), host_slot_seed_id("core")}
    for result in results:
        assert {slot["id"] for slot in result["slots"]} == expected
    assert set(ModelConfigStore(cm).read().slots) == expected
