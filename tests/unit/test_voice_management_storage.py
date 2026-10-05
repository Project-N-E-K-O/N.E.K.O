"""Imported voices retain their local identity across account/config changes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest

from utils.config_manager.voice_storage import VoiceStorageMixin
from utils.voice_config import read_legacy_voice_id


class MemoryVoiceManager(VoiceStorageMixin):
    def __init__(self):
        self.storage = {}
        self.scope = "scope-a"
        self.characters = {"猫娘": {}}

    def load_voice_storage(self):
        return deepcopy(self.storage)

    def save_voice_storage(self, value):
        self.storage = deepcopy(value)

    def get_core_config(self):
        return {"AUDIO_API_KEY": "legacy-key"}

    def get_model_api_config(self, tier):
        return {"api_key": "legacy-key"}

    def is_free_voice(self):
        return False

    def _get_cosyvoice_storage_keys(self, storage=None):
        return []

    def _get_minimax_storage_keys(self):
        return []

    def _get_elevenlabs_storage_keys(self):
        return []

    def _get_mimo_storage_keys(self):
        return []

    def _get_doubao_tts_storage_keys(self):
        return []

    def _get_glm_tts_storage_keys(self):
        return []

    def _get_vllm_omni_storage_keys(self):
        return []

    def _region_verdict_is_provisional(self):
        return False

    def load_characters(self):
        return deepcopy(self.characters)

    def save_characters(self, value):
        self.characters = deepcopy(value)


class DoubaoVoiceManager(MemoryVoiceManager):
    """Real keybook resolution and imported storage with isolated disk-free config."""
    def __init__(self):
        super().__init__()
        self.raw = {
            "ttsModelProvider": "doubao_tts", "ttsModelApiKey": "synthesis-key",
            "assistApiKeyDoubaoTts": "synthesis-key", "ttsModelUrl": "https://doubao-proxy.example",
            "ttsModelId": "custom-resource", "doubaoVoiceManagementAppId": "workspace-a",
            "doubaoVoiceManagementAccessKey": "management-ak", "doubaoVoiceManagementSecretKey": "management-sk",
        }

    def load_json_config(self, name, default=None):
        assert name == "core_config.json"
        return deepcopy(self.raw)

    async def aget_core_config(self):
        return self.get_core_config()

    async def aget_model_api_config(self, tier):
        return self.get_model_api_config(tier)

    async def aensure_region_resolved(self):
        return True


@pytest.fixture
def doubao_import(monkeypatch):
    from utils.voice_management import providers
    from utils.voice_management.providers.doubao import DoubaoVoiceAdapter
    cm, adapter = DoubaoVoiceManager(), DoubaoVoiceAdapter()
    original_adapter = providers.get_adapter
    monkeypatch.setattr(providers, "get_adapter", lambda provider: adapter if provider == "doubao_tts" else original_adapter(provider))
    runtime = adapter.resolve_runtime(cm)
    ref, data, _ = cm.import_remote_voice(runtime.scope_id, runtime.provider, "S_remote123", {
        **adapter.import_metadata(runtime), "prefix": "Voice", "can_overwrite": True, "remote_revision": "1",
    })
    return cm, adapter, ref, data


def test_doubao_scopes_follow_record_endpoints_without_merging_accounts(doubao_import):
    cm, adapter, ref, data = doubao_import
    cm.raw.update(ttsModelUrl="https://second-proxy.example", ttsModelId="second-resource")
    second_rt = adapter.resolve_runtime(cm)
    second_ref, _, _ = cm.import_remote_voice(second_rt.scope_id, second_rt.provider, "S_remote123", adapter.import_metadata(second_rt))
    cm.raw.update(ttsModelProvider="minimax", ttsModelUrl="https://minimax.example", ttsModelId="speech-02")
    assert cm.get_imported_voice(ref)["scope_id"] == data["scope_id"]
    active = cm.get_voices_for_current_api()
    assert ref in active and second_ref in active
    assert active[ref]["doubao_base_url"] != active[second_ref]["doubao_base_url"]
    cm.characters = {"猫娘": {"Test": {"voice_id": ref}}}
    cm.raw["assistApiKeyDoubaoTts"] = "new-account-key"
    assert cm.get_imported_voice(ref) is None
    assert cm.get_imported_voice(ref, include_inactive=True)["availability"] == "unavailable"
    cm.get_voices_for_current_api(for_listing=True)
    assert read_legacy_voice_id(cm.characters["猫娘"]["Test"]["voice_id"]) == ref
    cm.raw["assistApiKeyDoubaoTts"] = "synthesis-key"
    assert cm.get_imported_voice(ref) is not None
    cm.raw["doubaoVoiceManagementAppId"] = "workspace-b"
    assert cm.get_imported_voice(ref) is None
    assert cm.get_imported_voice(second_ref) is None


@pytest.fixture
def manager(monkeypatch):
    from utils.voice_management import providers

    cm = MemoryVoiceManager()
    monkeypatch.setattr(providers, "get_adapter", lambda provider: SimpleNamespace(
        resolve_runtime=lambda manager, voice_data=None: SimpleNamespace(scope_id=manager.scope, api_key="key"),
    ))
    return cm


def import_voice(cm, remote_id="remote-id", provider="minimax", **metadata):
    return cm.import_remote_voice(cm.scope, provider, remote_id, {"prefix": "Voice", **metadata})


def test_import_is_local_and_idempotent(manager):
    ref, metadata, created = import_voice(manager)
    assert ref.startswith("voice_") and created
    assert metadata["remote_voice_id"] == "remote-id"
    again, _, created = import_voice(manager)
    assert again == ref and not created
    assert "legacy-key" not in manager.storage
    assert manager.get_voices_for_current_api()[ref]["source"] == "clone"


def test_same_remote_id_different_scope_and_provider(manager):
    first, _, _ = import_voice(manager)
    second, _, _ = import_voice(manager, provider="elevenlabs")
    manager.scope = "scope-b"
    third, _, _ = import_voice(manager)
    assert len({first, second, third}) == 3
    assert manager.get_imported_voice(first) is None
    assert manager.get_imported_voice(first, include_inactive=True)
    assert third in manager.get_voices_for_current_api()
    assert manager.get_voices_for_current_api(for_listing=True)[first]["availability"] == "unavailable"


def test_inactive_voice_cannot_bind_but_cleanup_preserves_existing_binding(manager):
    ref, _, _ = import_voice(manager)
    manager.characters = {"猫娘": {"cat": {"_reserved": {
        "voice_id": {"source": "clone", "provider": "minimax", "ref": ref},
    }}}}
    manager.scope = "scope-b"
    assert not manager.validate_voice_id(ref)
    assert manager.cleanup_invalid_voice_ids() == (0, [])
    assert read_legacy_voice_id(manager.characters["猫娘"]["cat"]["_reserved"]["voice_id"]) == ref


def test_cleanup_does_not_erase_import_binding_when_storage_cannot_be_read(manager, monkeypatch):
    ref, _, _ = import_voice(manager)
    manager.characters = {"猫娘": {"cat": {"_reserved": {"voice_id": ref}}}}
    monkeypatch.setattr(manager, "load_voice_storage", lambda: {})
    assert manager.cleanup_invalid_voice_ids() == (0, [])
    assert manager.characters["猫娘"]["cat"]["_reserved"]["voice_id"] == ref


@pytest.mark.parametrize("provider", ["minimax", "elevenlabs", "cosyvoice", "doubao_tts", "glm_tts"])
def test_structured_binding_roundtrips_library_identity(manager, provider):
    ref, _, _ = import_voice(manager, provider=provider)
    stored = manager.voice_id_to_storage_value(ref)
    assert stored == {"source": "clone", "provider": provider, "ref": ref}
    assert read_legacy_voice_id(stored) == ref


def test_delete_targets_exact_library_record_even_if_scope_inactive(manager):
    first, _, _ = import_voice(manager)
    manager.scope = "scope-b"
    second, _, _ = import_voice(manager)
    assert manager.delete_voice_for_current_api(first)
    assert manager.get_imported_voice(first, include_inactive=True) is None
    assert manager.get_imported_voice(second)
    assert not manager.delete_voice_for_current_api(first)


def test_concurrent_import_and_legacy_create_preserve_every_record(manager):
    def save(index):
        if index % 2:
            return manager.save_voice_for_api_key("legacy-key", f"legacy-{index}", {})
        return import_voice(manager, remote_id=f"remote-{index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(save, range(32)))
    assert sum(len(bucket) for bucket in manager.storage.values()) == 32


def test_concurrent_duplicate_import_has_one_identity(manager):
    with ThreadPoolExecutor(max_workers=8) as executor:
        result = list(executor.map(lambda index: import_voice(manager), range(24)))
    assert len({ref for ref, _, _ in result}) == 1
    assert sum(created for _, _, created in result) == 1


def test_update_racing_delete_never_resurrects_record(manager):
    ref, _, _ = import_voice(manager)
    other, _, _ = import_voice(manager, remote_id="other")

    def update():
        try:
            manager.update_imported_voice(ref, "scope-a", {"overwrite_status": "ready"})
        except ValueError:
            pass

    with ThreadPoolExecutor(max_workers=2) as executor:
        update_future = executor.submit(update)
        delete_future = executor.submit(manager.delete_imported_voice, ref)
        update_future.result()
        assert delete_future.result()
    assert manager.get_imported_voice(ref, include_inactive=True) is None
    assert manager.get_imported_voice(other)


def test_late_status_refresh_cannot_overwrite_new_operation(manager):
    import threading

    ref, _, _ = import_voice(manager)
    manager.update_imported_voice(ref, "scope-a", {"overwrite_operation_id": "first", "overwrite_status": "processing"})
    query_finished = threading.Event()
    new_operation_started = threading.Event()

    def late_refresh():
        query_finished.set()
        assert new_operation_started.wait(2)
        with pytest.raises(ValueError):
            manager.update_imported_voice(ref, "scope-a", {"overwrite_status": "ready"}, expected_operation_id="first")

    with ThreadPoolExecutor(max_workers=2) as executor:
        refresh = executor.submit(late_refresh)
        assert query_finished.wait(2)
        manager.update_imported_voice(ref, "scope-a", {"overwrite_operation_id": "second", "overwrite_status": "processing"})
        new_operation_started.set()
        refresh.result()
    record = manager.get_imported_voice(ref)
    assert record["overwrite_operation_id"] == "second"
    assert record["overwrite_status"] == "processing"


def test_status_refresh_without_operation_requires_still_no_operation(manager):
    ref, _, _ = import_voice(manager)
    manager.update_imported_voice(ref, "scope-a", {"remote_status": "ready"}, expected_operation_id="")
    manager.update_imported_voice(ref, "scope-a", {"overwrite_operation_id": "new"})
    with pytest.raises(ValueError):
        manager.update_imported_voice(ref, "scope-a", {"remote_status": "old"}, expected_operation_id="")


def test_update_preserves_identity_and_records_result_after_key_switch(manager):
    ref, before, _ = import_voice(manager)
    manager.scope = "scope-b"
    after = manager.update_imported_voice(ref, before["scope_id"], {
        "scope_id": "scope-b", "provider": "elevenlabs", "remote_voice_id": "other",
        "source": "design", "overwrite_status": "processing",
    })
    for key in ("scope_id", "provider", "remote_voice_id", "source", "origin", "imported_at"):
        assert after[key] == before[key]
    assert after["overwrite_status"] == "processing"
    with pytest.raises(ValueError):
        manager.update_imported_voice(ref, "wrong-scope", {})


def test_legacy_storage_still_lists_and_saves(manager):
    manager.save_voice_for_current_api("legacy-id", {"provider": "minimax"})
    assert "legacy-id" in manager.get_voices_for_current_api()
    assert manager.validate_voice_id("legacy-id")


def test_existing_legacy_id_shaped_like_import_namespace_still_valid(manager):
    ref = "voice_" + "a" * 32
    manager.save_voice_for_current_api(ref, {"provider": "minimax"})
    assert manager.validate_voice_id(ref)
    assert manager.voice_id_to_storage_value(ref) == {"source": "clone", "provider": "minimax", "ref": ref}
    assert manager.delete_voice_for_current_api(ref)


def test_corrupt_real_storage_is_never_replaced(tmp_path):
    class FileManager(VoiceStorageMixin):
        def load_json_config(self, name, default_value=None):
            import json
            return json.loads((tmp_path / name).read_text(encoding="utf-8"))

        def save_json_config(self, name, data):
            pytest.fail("Corrupt storage must not be overwritten")

    (tmp_path / "voice_storage.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        FileManager().import_remote_voice("scope", "minimax", "remote", {})


@pytest.mark.asyncio
async def test_async_storage_mutations_are_dual(manager):
    ref, _, created = await manager.aimport_remote_voice("scope-a", "minimax", "remote", {})
    assert created
    updated = await manager.aupdate_imported_voice(ref, "scope-a", {"overwrite_status": "ready"})
    assert updated["overwrite_status"] == "ready"
    assert await manager.adelete_imported_voice(ref)
