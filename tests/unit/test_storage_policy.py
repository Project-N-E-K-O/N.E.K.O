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
@pytest.mark.skipif(os.name == "nt", reason="POSIX required directory barriers")
def test_save_storage_policy_flushes_every_new_first_run_ancestor(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    flushed_directories = []
    real_fsync = storage_policy_module._fsync_policy_directory_required

    def record_directory_fsync(path):
        flushed_directories.append(path)
        real_fsync(path)

    monkeypatch.setattr(
        storage_policy_module,
        "_fsync_policy_directory_required",
        record_directory_fsync,
    )

    save_storage_policy(
        config_manager,
        selected_root=selected_root,
        selection_source="current",
    )

    policy_path = get_storage_policy_path(config_manager)
    assert flushed_directories == [
        policy_path.parent,
        policy_path.parent.parent,
        policy_path.parent.parent.parent,
        policy_path.parent.parent.parent.parent,
    ]


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX required directory barrier")
def test_save_storage_policy_fails_closed_when_parent_flush_fails(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    real_fsync = storage_policy_module.os.fsync

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("policy directory flush failed")
        return real_fsync(fd)

    monkeypatch.setattr(storage_policy_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(StoragePolicyError) as caught:
        save_storage_policy(
            config_manager,
            selected_root=selected_root,
            selection_source="current",
        )

    assert caught.value.reason == "policy_flush_failed"


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
@pytest.mark.parametrize("existing_layer", ["anchor", "state"])
def test_load_storage_policy_accepts_handle_bound_first_run_absence(
    tmp_path,
    existing_layer,
):
    config_manager = _DummyConfigManager(tmp_path)
    anchor_root = config_manager._standard_root / config_manager.app_name
    anchor_root.mkdir(parents=True)
    if existing_layer == "state":
        (anchor_root / "state").mkdir()
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
@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO race injection")
def test_load_storage_policy_does_not_block_when_policy_becomes_fifo_before_open(
    tmp_path,
    monkeypatch,
):
    if not hasattr(os, "mkfifo"):
        pytest.skip("POSIX FIFO creation is unavailable")

    config_manager = _DummyConfigManager(tmp_path)
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    policy_path = get_storage_policy_path(config_manager)
    save_storage_policy(
        config_manager,
        selected_root=selected_root,
        selection_source="current",
    )
    real_open = storage_policy_module.os.open
    opened_flags = []
    replaced = False

    def replace_with_fifo_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if path == policy_path.name and dir_fd is not None and not replaced:
            replaced = True
            policy_path.unlink()
            os.mkfifo(policy_path)
            opened_flags.append(flags)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage_policy_module.os, "open", replace_with_fifo_before_open)
    outcome = []

    def load_raced_policy():
        try:
            load_storage_policy(config_manager)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=load_raced_policy, daemon=True)
    worker.start()
    worker.join(timeout=1)
    if worker.is_alive():
        # Release a regressed blocking reader so it cannot leak into later tests.
        writer_fd = real_open(policy_path, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer_fd)
        worker.join(timeout=1)

    assert not worker.is_alive(), "opening a raced policy FIFO must be non-blocking"
    assert len(outcome) == 1
    assert isinstance(outcome[0], StoragePolicyError)
    assert outcome[0].reason == "policy_changed_during_read"
    assert opened_flags and opened_flags[0] & os.O_NONBLOCK
    assert opened_flags[0] & os.O_NOFOLLOW


@pytest.mark.unit
def test_load_storage_policy_rejects_a_regular_file_in_the_anchor_chain(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    config_manager._standard_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(StoragePolicyError) as caught:
        load_storage_policy(config_manager, default={})

    assert caught.value.reason == "anchor_root_not_directory"


@pytest.mark.unit
def test_load_storage_policy_finds_an_ancestor_file_after_windows_style_missing_child(
    tmp_path,
    monkeypatch,
):
    config_manager = _DummyConfigManager(tmp_path)
    blocked_parent = config_manager._standard_root
    blocked_parent.write_text("not a directory", encoding="utf-8")
    anchor_root = blocked_parent / config_manager.app_name
    real_lstat = Path.lstat

    def windows_style_lstat(path):
        if path == anchor_root:
            raise FileNotFoundError("simulated Windows child lookup")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", windows_style_lstat)

    with pytest.raises(StoragePolicyError) as caught:
        load_storage_policy(config_manager, anchor_root=anchor_root, default={})

    assert caught.value.reason == "anchor_root_not_directory"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX dir-fd race injection")
def test_load_storage_policy_rejects_anchor_replacement_before_handle_open(
    tmp_path,
    monkeypatch,
):
    config_manager = _DummyConfigManager(tmp_path)
    anchor_root = config_manager._standard_root / config_manager.app_name
    anchor_root.mkdir(parents=True)
    replacement_anchor = tmp_path / "replacement-anchor"
    replacement_anchor.mkdir()
    detached_anchor = tmp_path / "detached-anchor"
    real_open = os.open
    replaced = False

    def replace_anchor_before_handle_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if not replaced and path == anchor_root.name and dir_fd is not None:
            replaced = True
            anchor_root.rename(detached_anchor)
            replacement_anchor.rename(anchor_root)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_anchor_before_handle_open)

    with pytest.raises(StoragePolicyError) as caught:
        load_storage_policy(config_manager, default={})

    assert caught.value.reason == "anchor_root_changed"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX dir-fd race injection")
@pytest.mark.parametrize("replacement_kind", ["directory", "symlink"])
def test_load_storage_policy_rejects_anchor_replacement_after_handle_binding(
    tmp_path,
    monkeypatch,
    replacement_kind,
):
    config_manager = _DummyConfigManager(tmp_path)
    anchor_root = config_manager._standard_root / config_manager.app_name
    selected_root = tmp_path / "selected-original"
    selected_root.mkdir()
    save_storage_policy(
        config_manager,
        selected_root=selected_root,
        selection_source="current",
    )

    replacement_anchor = tmp_path / "replacement-anchor"
    replacement_selected_root = tmp_path / "selected-replacement"
    replacement_selected_root.mkdir()
    save_storage_policy(
        config_manager,
        selected_root=replacement_selected_root,
        selection_source="current",
        anchor_root=replacement_anchor,
    )

    detached_anchor = tmp_path / "detached-anchor"
    real_open = os.open
    replaced = False

    def replace_anchor_before_state_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if not replaced and path == "state" and dir_fd is not None:
            replaced = True
            anchor_root.rename(detached_anchor)
            if replacement_kind == "directory":
                replacement_anchor.rename(anchor_root)
            else:
                try:
                    anchor_root.symlink_to(replacement_anchor, target_is_directory=True)
                except (OSError, NotImplementedError):
                    pytest.skip("symbolic links are unavailable on this platform")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_anchor_before_state_open)

    with pytest.raises(StoragePolicyError) as caught:
        load_storage_policy(config_manager)

    assert caught.value.reason in {"anchor_root_changed", "anchor_root_redirect"}


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX dir-fd race injection")
@pytest.mark.parametrize("missing_entry", ["state", "storage_policy.json"])
@pytest.mark.parametrize("replacement_timing", ["before", "after"])
def test_load_storage_policy_does_not_accept_absence_from_a_detached_anchor(
    tmp_path,
    monkeypatch,
    missing_entry,
    replacement_timing,
):
    config_manager = _DummyConfigManager(tmp_path)
    anchor_root = config_manager._standard_root / config_manager.app_name
    anchor_root.mkdir(parents=True)
    if missing_entry == "storage_policy.json":
        (anchor_root / "state").mkdir()

    replacement_anchor = tmp_path / "replacement-anchor"
    replacement_anchor.mkdir()
    if missing_entry == "storage_policy.json":
        (replacement_anchor / "state").mkdir()
    detached_anchor = tmp_path / "detached-anchor"
    real_stat = os.stat
    replaced = False

    def replace_anchor():
        nonlocal replaced
        replaced = True
        anchor_root.rename(detached_anchor)
        replacement_anchor.rename(anchor_root)

    def replace_anchor_around_absence_check(path, *args, **kwargs):
        should_replace = (
            not replaced
            and path == missing_entry
            and kwargs.get("dir_fd") is not None
        )
        if should_replace and replacement_timing == "before":
            replace_anchor()
        try:
            return real_stat(path, *args, **kwargs)
        except FileNotFoundError:
            if should_replace and replacement_timing == "after":
                replace_anchor()
            raise

    monkeypatch.setattr(os, "stat", replace_anchor_around_absence_check)

    with pytest.raises(StoragePolicyError) as caught:
        load_storage_policy(config_manager, default={})

    assert caught.value.reason == "anchor_root_changed"


@pytest.mark.unit
def test_load_storage_policy_fails_closed_for_read_error(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            storage_policy_module,
            "_read_storage_policy_json",
            lambda *_args: (_ for _ in ()).throw(PermissionError("private path")),
        )
        with pytest.raises(StoragePolicyError) as caught:
            load_storage_policy(config_manager, default={})

    assert caught.value.reason == "unreadable"


@pytest.mark.unit
def test_load_storage_policy_fails_closed_for_non_object_payload(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    policy_path = get_storage_policy_path(config_manager)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text("[]", encoding="utf-8")

    with pytest.raises(StoragePolicyError) as caught:
        load_storage_policy(config_manager, default={})

    assert caught.value.reason == "not_object"


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
def test_validate_selected_root_rejects_anchor_reserved_state_directory(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    invalid_target = tmp_path / "anchor-base" / "N.E.K.O" / "state" / "nested"

    with pytest.raises(StorageSelectionValidationError) as exc_info:
        validate_selected_root(config_manager, invalid_target)

    assert "锚点目录保留区域" in str(exc_info.value)


@pytest.mark.unit
def test_validate_selected_root_still_rejects_repository_paths_after_packaging(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    repository_root = Path(storage_policy_module.__file__).resolve().parents[2]

    with pytest.raises(StorageSelectionValidationError) as exc_info:
        validate_selected_root(config_manager, repository_root / "frontend")

    assert exc_info.value.error_code == "selected_root_inside_project"


@pytest.mark.unit
def test_validate_selected_root_appends_app_folder_for_custom_parent_directory(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    selected_parent = tmp_path / "custom-parent"
    selected_parent.mkdir()

    normalized = validate_selected_root(
        config_manager,
        selected_parent,
        selection_source="custom",
    )

    assert normalized == selected_parent / "N.E.K.O"


@pytest.mark.unit
def test_validate_selected_root_keeps_custom_app_folder_when_already_selected(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    selected_root = tmp_path / "custom-parent" / "N.E.K.O"

    normalized = validate_selected_root(
        config_manager,
        selected_root,
        selection_source="custom",
    )

    assert normalized == selected_root


@pytest.mark.unit
def test_apfs_case_alias_is_the_current_physical_root_not_a_nested_custom_root(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    current_root = config_manager.app_docs_dir
    case_alias = current_root.with_name(current_root.name.swapcase())
    if not case_alias.exists() or not case_alias.samefile(current_root):
        pytest.skip("test volume is case-sensitive")

    assert storage_policy_module.paths_equal(case_alias, current_root) is True
    assert storage_policy_module.path_is_within(case_alias / "pending", current_root) is True
    assert validate_selected_root(
        config_manager,
        case_alias,
        selection_source="custom",
    ) == current_root.resolve()


@pytest.mark.unit
def test_missing_case_variant_remains_distinct_on_case_sensitive_filesystem(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    current_root = config_manager.app_docs_dir
    case_variant = current_root.with_name(current_root.name.swapcase())
    if case_variant.exists():
        pytest.skip("test volume is case-insensitive")

    assert storage_policy_module.paths_equal(case_variant, current_root) is False
    assert storage_policy_module.path_is_within(case_variant / "pending", current_root) is False


@pytest.mark.unit
def test_selected_root_rejects_uninspectable_physical_identity(tmp_path, monkeypatch):
    config_manager = _DummyConfigManager(tmp_path)
    selected_root = tmp_path / "selected" / "N.E.K.O"
    selected_root.parent.mkdir()

    def deny_identity(_path):
        raise storage_policy_module.PathIdentityUnavailable("permission denied")

    monkeypatch.setattr(storage_policy_module, "_existing_path_identity", deny_identity)

    with pytest.raises(StorageSelectionValidationError) as exc_info:
        validate_selected_root(
            config_manager,
            selected_root,
            selection_source="custom",
        )

    assert exc_info.value.error_code == "selected_root_identity_uninspectable"


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


@pytest.mark.unit
def test_runtime_entry_boundary_treats_windows_reparse_parent_as_redirect(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "runtime"
    state_root = root / "state"
    state_root.mkdir(parents=True)
    real_lstat = Path.lstat
    reparse_flag = 0x400
    monkeypatch.setattr(
        storage_policy_module.stat,
        "FILE_ATTRIBUTE_REPARSE_POINT",
        reparse_flag,
        raising=False,
    )

    def fake_lstat(path):
        result = real_lstat(path)
        if path == state_root:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=reparse_flag,
            )
        return result

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(RuntimeStorageEntryBoundaryError, match="重解析点"):
        checked_runtime_entry_path(root, "state/game_scores")
