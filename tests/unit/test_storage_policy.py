import json


import os


import stat


import threading


from pathlib import Path


from types import SimpleNamespace


import pytest


from utils.storage import policy as storage_policy_module


from utils.storage.entries import RuntimeStorageEntryBoundaryError, checked_runtime_entry_path


from utils.storage_policy import (
    CLOUDSAVE_STRATEGY_FIXED_ANCHOR,
    StoragePolicyError,
    StorageSelectionValidationError,
    get_storage_policy_path,
    is_runtime_root_available,
    load_storage_policy,
    save_storage_policy,
    validate_selected_root,
)


class _DummyConfigManager:
    def __init__(self, tmp_path: Path):
        self.app_name = "N.E.K.O"
        self.app_docs_dir = tmp_path / "runtime" / self.app_name
        self.app_docs_dir.mkdir(parents=True, exist_ok=True)
        self._standard_root = tmp_path / "anchor-base"

    def _get_standard_data_directory_candidates(self):
        return [self._standard_root]


@pytest.mark.unit
def test_save_storage_policy_writes_stable_layout_under_anchor_state(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    payload = save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        selection_source="current",
    )

    policy_path = get_storage_policy_path(config_manager)
    assert policy_path == tmp_path / "anchor-base" / "N.E.K.O" / "state" / "storage_policy.json"
    assert policy_path.is_file()

    reloaded_payload = load_storage_policy(config_manager)
    assert reloaded_payload == payload
    assert payload["anchor_root"] == str(tmp_path / "anchor-base" / "N.E.K.O")
    assert payload["selected_root"] == str(config_manager.app_docs_dir)
    assert payload["cloudsave_strategy"] == CLOUDSAVE_STRATEGY_FIXED_ANCHOR
    assert payload["selection_source"] == "user_selected"
    assert payload["first_run_completed"] is True


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinked state boundary")
def test_write_fixed_anchor_state_json_rejects_symlinked_state_without_external_write(
    tmp_path,
):
    anchor_root = tmp_path / "anchor" / "N.E.K.O"
    anchor_root.mkdir(parents=True)
    external_state = tmp_path / "external-state"
    external_state.mkdir()
    sentinel = external_state / "sentinel.json"
    sentinel.write_text('{"owner":"foreign"}', encoding="utf-8")
    (anchor_root / "state").symlink_to(external_state, target_is_directory=True)

    with pytest.raises(StoragePolicyError):
        storage_policy_module.write_fixed_anchor_state_json(
            anchor_root,
            "storage_migration.json",
            {"status": "pending"},
        )

    assert sentinel.read_text(encoding="utf-8") == '{"owner":"foreign"}'
    assert not (external_state / "storage_migration.json").exists()


@pytest.mark.unit
def test_load_storage_policy_returns_default_when_payload_is_unreadable(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    policy_path = get_storage_policy_path(config_manager)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text("{not-json", encoding="utf-8")

    default_payload = {"selected_root": str(config_manager.app_docs_dir)}

    with pytest.raises(StoragePolicyError) as caught:
        load_storage_policy(config_manager, default=default_payload)

    assert caught.value.error_code == "storage_policy_unavailable"
    assert caught.value.reason == "malformed"
    assert policy_path.read_text(encoding="utf-8") == "{not-json"


@pytest.mark.unit
def test_load_storage_policy_uses_default_only_when_file_is_absent(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    default_payload = {"selected_root": str(config_manager.app_docs_dir)}

    assert load_storage_policy(config_manager, default=default_payload) == default_payload


@pytest.mark.unit
def test_load_storage_policy_never_follows_the_policy_file(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    policy_path = get_storage_policy_path(config_manager)
    policy_path.parent.mkdir(parents=True)
    redirected_payload = tmp_path / "redirected-policy.json"
    redirected_payload.write_text("{}", encoding="utf-8")
    try:
        policy_path.symlink_to(redirected_payload)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    with pytest.raises(StoragePolicyError) as caught:
        load_storage_policy(config_manager)

    assert caught.value.reason == "policy_path_redirect"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("version", 2, "version_invalid"),
        ("cloudsave_strategy", "movable", "cloudsave_strategy_invalid"),
        ("selection_source", "custom", "selection_source_invalid"),
        ("first_run_completed", False, "first_run_completed_invalid"),
        ("updated_at", "", "updated_at_invalid"),
    ],
)
def test_load_storage_policy_rejects_invalid_required_schema(
    tmp_path,
    field,
    value,
    reason,
):
    config_manager = _DummyConfigManager(tmp_path)
    policy_path = get_storage_policy_path(config_manager)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "anchor_root": str(policy_path.parents[1]),
        "selected_root": str(tmp_path / "selected" / "N.E.K.O"),
        "selection_source": "user_selected",
        "cloudsave_strategy": "fixed_anchor",
        "first_run_completed": True,
        "updated_at": "2026-09-11T00:00:00Z",
    }
    payload[field] = value
    storage_policy_module.atomic_write_json(policy_path, payload)

    with pytest.raises(StoragePolicyError) as caught:
        load_storage_policy(config_manager)

    assert caught.value.reason == reason


@pytest.mark.unit
@pytest.mark.parametrize(
    "selected_root_factory",
    [
        lambda config_manager, _tmp_path: Path(storage_policy_module.__file__).resolve().parents[2],
        lambda config_manager, _tmp_path: get_storage_policy_path(config_manager).parent / "nested",
    ],
)
def test_load_storage_policy_rejects_dangerous_selected_root(
    tmp_path,
    selected_root_factory,
):
    config_manager = _DummyConfigManager(tmp_path)
    policy_path = get_storage_policy_path(config_manager)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    selected_root = selected_root_factory(config_manager, tmp_path)
    payload = {
        "version": 1,
        "anchor_root": str(policy_path.parents[1]),
        "selected_root": str(selected_root),
        "selection_source": "user_selected",
        "cloudsave_strategy": "fixed_anchor",
        "first_run_completed": True,
        "updated_at": "2026-09-11T00:00:00Z",
    }
    storage_policy_module.atomic_write_json(policy_path, payload)

    with pytest.raises(StoragePolicyError) as caught:
        load_storage_policy(config_manager)

    assert caught.value.reason in {
        "selected_root_inside_project",
        "selected_root_inside_reserved_root",
    }
    assert json.loads(policy_path.read_text(encoding="utf-8")) == payload


@pytest.mark.unit
def test_runtime_root_availability_requires_real_write_probe(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setattr(storage_policy_module, "_can_write_existing_directory", lambda _path: False)

    assert is_runtime_root_available(runtime_root) is False


@pytest.mark.unit
def test_validate_selected_root_rejects_symlink_in_path_chain(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    with pytest.raises(StorageSelectionValidationError) as exc_info:
        validate_selected_root(
            config_manager,
            linked_parent,
            selection_source="custom",
        )

    assert exc_info.value.error_code == "selected_root_symlink_unsupported"
