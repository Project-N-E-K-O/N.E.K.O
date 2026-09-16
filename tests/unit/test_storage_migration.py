import errno
import json
import os
import shutil
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils.storage_migration import (
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_FAILED,
    STORAGE_MIGRATION_STATUS_PREFLIGHT,
    STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED,
    STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
    create_pending_storage_migration,
    get_storage_migration_path,
    is_retained_root_cleanup_available,
    is_storage_migration_pending,
    is_storage_migration_rollback_required,
    load_storage_migration,
    run_pending_storage_migration,
    StorageMigrationError,
)
from utils.storage_policy import load_storage_policy


class _DummyConfigManager:
    def __init__(self, tmp_path: Path):
        self.app_name = "N.E.K.O"
        self.app_docs_dir = tmp_path / "runtime" / self.app_name
        self.app_docs_dir.mkdir(parents=True, exist_ok=True)
        self._standard_root = tmp_path / "anchor-base"

    def _get_standard_data_directory_candidates(self):
        return [self._standard_root]


def _make_config_manager(tmp_path: Path):
    from utils.config_manager import ConfigManager

    standard_root = tmp_path / "anchor-base"
    patchers = [
        patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path / "runtime-parent"),
        patch.object(ConfigManager, "_get_standard_data_directory_candidates", return_value=[standard_root]),
    ]
    with patchers[0], patchers[1]:
        config_manager = ConfigManager("N.E.K.O")
    config_manager._get_standard_data_directory_candidates = lambda: [standard_root]
    return config_manager


def _make_anchor_root_config_manager(tmp_path: Path):
    from utils.config_manager import ConfigManager

    standard_root = tmp_path / "anchor-base"
    patchers = [
        patch.object(ConfigManager, "_get_documents_directory", return_value=standard_root),
        patch.object(ConfigManager, "_get_standard_data_directory_candidates", return_value=[standard_root]),
    ]
    with patchers[0], patchers[1]:
        config_manager = ConfigManager("N.E.K.O")
    config_manager._get_standard_data_directory_candidates = lambda: [standard_root]
    return config_manager


def _run_fifo_open_replacement(monkeypatch, storage_migration_module, victim, operation):
    real_open = storage_migration_module.os.open
    opened_flags = []
    replaced = False
    results = []
    errors = []

    def replace_with_fifo_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if (
            not replaced
            and (
                Path(path) == victim
                or (dir_fd is not None and path == victim.name)
            )
        ):
            replaced = True
            victim.unlink()
            os.mkfifo(victim)
            opened_flags.append(flags)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(
        storage_migration_module.os,
        "open",
        replace_with_fifo_before_open,
    )

    def run_operation():
        try:
            results.append(operation())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_operation, daemon=True)
    worker.start()
    worker.join(timeout=1)
    if worker.is_alive():
        writer_fd = real_open(victim, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer_fd)
        worker.join(timeout=1)

    return worker, replaced, opened_flags, results, errors


@pytest.mark.unit
def test_create_pending_storage_migration_writes_anchor_checkpoint(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    target_root = tmp_path / "new-storage" / "N.E.K.O"

    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="recommended",
    )

    checkpoint_path = get_storage_migration_path(config_manager)
    assert checkpoint_path == tmp_path / "anchor-base" / "N.E.K.O" / "state" / "storage_migration.json"
    assert checkpoint_path.is_file()

    reloaded_payload = load_storage_migration(config_manager)
    assert reloaded_payload == payload
    assert payload["source_root"] == str(config_manager.app_docs_dir)
    assert payload["target_root"] == str(target_root.resolve())
    assert payload["selection_source"] == "recommended"
    assert payload["version"] == 2
    assert payload["status"] == "pending"
    assert payload["migration_mode"] == "copy"
    assert payload["target_baseline"] == {}
    assert is_storage_migration_pending(payload) is True


@pytest.mark.unit
def test_owned_transaction_cleanup_removes_read_only_tree(tmp_path):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    locked_dir = transaction_root / "backup" / "config"
    locked_dir.mkdir(parents=True)
    locked_file = locked_dir / "characters.json"
    locked_file.write_text('{"preserved": true}', encoding="utf-8")
    os.chmod(locked_file, stat.S_IRUSR)
    # No execute permission means the rmtree callback cannot even lstat a
    # child until it repairs the owned parent directory first.
    os.chmod(locked_dir, stat.S_IRUSR)

    try:
        storage_migration_module._remove_existing_path(transaction_root)
    finally:
        if locked_dir.exists():
            os.chmod(locked_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        if locked_file.exists():
            os.chmod(locked_file, stat.S_IRUSR | stat.S_IWUSR)

    assert not transaction_root.exists()


@pytest.mark.unit
def test_owned_transaction_cleanup_repairs_nested_unsearchable_directories(tmp_path):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    upper = transaction_root / "backup" / "locked-upper"
    lower = upper / "locked-lower"
    lower.mkdir(parents=True)
    locked_file = lower / "characters.json"
    locked_file.write_text('{"preserved": true}', encoding="utf-8")
    os.chmod(locked_file, stat.S_IRUSR)
    os.chmod(lower, 0)
    os.chmod(upper, 0)

    try:
        storage_migration_module._remove_existing_path(transaction_root)
    finally:
        for candidate in (upper, lower):
            if candidate.exists():
                os.chmod(candidate, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        if locked_file.exists():
            os.chmod(locked_file, stat.S_IRUSR | stat.S_IWUSR)

    assert not transaction_root.exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-bound cleanup contract")
def test_owned_transaction_cleanup_does_not_retain_one_fd_per_repaired_directory(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    locked_root = transaction_root / "backup"
    locked_root.mkdir(parents=True)
    for index in range(180):
        locked_dir = locked_root / f"locked-{index:04d}"
        locked_dir.mkdir()
        os.chmod(locked_dir, 0)

    real_open = storage_migration_module.os.open
    real_dup = storage_migration_module.os.dup
    dup_calls = 0

    def enforce_directory_permissions(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and str(path).startswith("locked-"):
            metadata = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
            if not stat.S_IMODE(metadata.st_mode) & stat.S_IXUSR:
                raise PermissionError(errno.EACCES, "simulated unsearchable directory")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def bounded_dup(fd):
        nonlocal dup_calls
        dup_calls += 1
        if dup_calls > 4:
            raise OSError(errno.EMFILE, "simulated descriptor limit")
        return real_dup(fd)

    monkeypatch.setattr(storage_migration_module.os, "open", enforce_directory_permissions)
    monkeypatch.setattr(storage_migration_module.os, "dup", bounded_dup)

    storage_migration_module._remove_existing_path(transaction_root)

    assert not transaction_root.exists()
    assert dup_calls == 1


@pytest.mark.unit
def test_owned_transaction_cleanup_never_chmods_a_link_target(tmp_path):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    transaction_root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    external_file = external / "keep.txt"
    external_file.write_text("keep", encoding="utf-8")
    original_mode = stat.S_IRUSR | stat.S_IXUSR
    os.chmod(external, original_mode)
    observed_mode = stat.S_IMODE(external.stat().st_mode)
    (transaction_root / "external-link").symlink_to(external, target_is_directory=True)

    try:
        storage_migration_module._remove_existing_path(transaction_root)
        assert stat.S_IMODE(external.stat().st_mode) == observed_mode
        assert external_file.read_text(encoding="utf-8") == "keep"
    finally:
        os.chmod(external, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX mount descriptor contract")
def test_owned_transaction_cleanup_rejects_nested_mount_before_any_deletion(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    ordinary_file = transaction_root / "backup" / "a-local.json"
    locked_dir = transaction_root / "backup" / "b-locked"
    mounted_file = transaction_root / "backup" / "z-mounted" / "external.json"
    ordinary_file.parent.mkdir(parents=True)
    ordinary_file.write_text("LOCAL", encoding="utf-8")
    locked_dir.mkdir()
    os.chmod(locked_dir, stat.S_IRUSR)
    locked_mode = stat.S_IMODE(locked_dir.stat().st_mode)
    mounted_file.parent.mkdir()
    mounted_file.write_text("EXTERNAL", encoding="utf-8")
    mounted_inode = mounted_file.parent.stat().st_ino

    def simulated_mount_identity(fd):
        identity = os.fstat(fd)
        return "test-mount", 2 if identity.st_ino == mounted_inode else 1

    monkeypatch.setattr(
        storage_migration_module,
        "_opened_mount_identity",
        simulated_mount_identity,
    )

    try:
        with pytest.raises(StorageMigrationError) as exc_info:
            storage_migration_module._remove_existing_path(transaction_root)

        assert exc_info.value.error_code == "nested_mount_unsupported"
        assert ordinary_file.read_text(encoding="utf-8") == "LOCAL"
        assert mounted_file.read_text(encoding="utf-8") == "EXTERNAL"
        assert stat.S_IMODE(locked_dir.stat().st_mode) == locked_mode
    finally:
        os.chmod(locked_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX mount preflight contract")
def test_preflight_closes_root_fd_when_mount_identity_is_unavailable(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    transaction_root.mkdir()
    opened_fd = -1
    real_open_verified_directory = storage_migration_module._open_verified_directory

    def record_opened_root(path):
        nonlocal opened_fd
        opened_fd = real_open_verified_directory(path)
        return opened_fd

    def reject_mount_identity(_fd):
        raise StorageMigrationError(
            "mount_identity_unavailable",
            "simulated mount identity failure",
        )

    monkeypatch.setattr(
        storage_migration_module,
        "_open_verified_directory",
        record_opened_root,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_opened_mount_identity",
        reject_mount_identity,
    )

    with pytest.raises(StorageMigrationError) as exc_info:
        storage_migration_module._preflight_directory_tree_mounts(transaction_root)

    assert exc_info.value.error_code == "mount_identity_unavailable"
    assert opened_fd >= 0
    with pytest.raises(OSError):
        os.fstat(opened_fd)


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX retained-mode contract")
def test_quarantine_restores_modes_when_name_disappears_after_preflight(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    locked_dir = transaction_root / "locked"
    locked_dir.mkdir(parents=True)
    os.chmod(locked_dir, stat.S_IRUSR)
    original_mode = stat.S_IMODE(locked_dir.stat().st_mode)
    expected_identity = transaction_root.lstat()
    parked = tmp_path / "parked"
    real_preflight = storage_migration_module._preflight_directory_tree_mounts

    def detach_name_after_preflight(*args, **kwargs):
        result = real_preflight(*args, **kwargs)
        transaction_root.rename(parked)
        return result

    monkeypatch.setattr(
        storage_migration_module,
        "_preflight_directory_tree_mounts",
        detach_name_after_preflight,
    )

    try:
        with pytest.raises(FileNotFoundError):
            storage_migration_module._remove_private_directory_via_quarantine(
                transaction_root,
                expected_identity,
            )

        assert parked.is_dir()
        assert stat.S_IMODE((parked / "locked").stat().st_mode) == original_mode
        assert not storage_migration_module._private_directory_quarantine_path(
            transaction_root
        ).exists()
    finally:
        os.chmod(
            parked / "locked",
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,
        )


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX mount preflight contract")
def test_nested_mount_is_rejected_before_quarantine_rename_or_chmod(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    locked_dir = transaction_root / "backup" / "a-locked"
    mount_path = transaction_root / "backup" / "z-mounted"
    locked_dir.mkdir(parents=True)
    mount_path.mkdir()
    os.chmod(locked_dir, stat.S_IRUSR)
    original_mode = stat.S_IMODE(locked_dir.stat().st_mode)
    monkeypatch.setattr(
        storage_migration_module,
        "_mounted_paths",
        lambda: [mount_path],
    )

    try:
        with pytest.raises(StorageMigrationError) as exc_info:
            storage_migration_module._remove_private_directory_via_quarantine(
                transaction_root,
                transaction_root.lstat(),
            )

        assert exc_info.value.error_code == "nested_mount_unsupported"
        assert transaction_root.is_dir()
        assert not storage_migration_module._private_directory_quarantine_path(
            transaction_root
        ).exists()
        assert stat.S_IMODE(locked_dir.stat().st_mode) == original_mode
    finally:
        os.chmod(locked_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX mount-table contract")
@pytest.mark.parametrize(
    ("mounted_side", "relative_mount"),
    (
        ("source", "config/mounted"),
        ("target", "config"),
        # ``state`` is an ancestor inside the runtime root of the declared
        # ``state/game_scores`` entry and must be rejected before inspection.
        ("source", "state"),
    ),
)
def test_live_preflight_rejects_runtime_entry_mount_before_path_inspection(
    tmp_path,
    monkeypatch,
    mounted_side,
    relative_mount,
):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    selected_root = source_root if mounted_side == "source" else target_root
    mount_path = selected_root / relative_mount
    mount_calls = []

    def mounted_paths():
        mount_calls.append("enumerated")
        return [mount_path]

    monkeypatch.setattr(storage_migration_module, "_mounted_paths", mounted_paths)
    monkeypatch.setattr(
        storage_migration_module,
        "_checked_migration_entry_path",
        lambda *_args, **_kwargs: pytest.fail(
            "known runtime-entry mount must be rejected before path inspection"
        ),
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module.validate_storage_migration_preflight_boundaries(
            source_root,
            target_root,
        )

    assert caught.value.error_code == "nested_mount_unsupported"
    assert mount_calls == ["enumerated"]


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX mount-table contract")
def test_live_preflight_allows_root_mount_and_unrelated_nested_mounts(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    mount_calls = []

    def mounted_paths():
        mount_calls.append("enumerated")
        return [
            source_root,
            source_root / "unmanaged" / "mounted",
            target_root / "notes" / "mounted",
        ]

    monkeypatch.setattr(storage_migration_module, "_mounted_paths", mounted_paths)

    storage_migration_module.validate_storage_migration_preflight_boundaries(
        source_root,
        target_root,
    )

    assert mount_calls == ["enumerated"]


@pytest.mark.unit
def test_live_preflight_uses_canonical_runtime_entry_redirect_boundary(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    redirected_entry = source_root / "config"
    external = tmp_path / "external"
    external.mkdir()
    if os.name != "nt":
        monkeypatch.setattr(storage_migration_module, "_mounted_paths", lambda: [])
    try:
        redirected_entry.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory links are unavailable on this platform")

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module.validate_storage_migration_preflight_boundaries(
            source_root,
            target_root,
        )

    assert caught.value.error_code == "runtime_entry_path_unsafe"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity contract")
def test_quarantine_preflight_never_chmods_a_replacement_root(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    transaction_root.mkdir()
    owned_identity = transaction_root.lstat()
    owned_aside = tmp_path / "owned-aside"
    unrelated_locked = transaction_root / "unrelated-locked"

    def replace_before_tree_preflight(_path):
        transaction_root.rename(owned_aside)
        unrelated_locked.mkdir(parents=True)
        os.chmod(unrelated_locked, stat.S_IRUSR)

    monkeypatch.setattr(
        storage_migration_module,
        "_preflight_named_mounts_below",
        replace_before_tree_preflight,
    )

    try:
        with pytest.raises(StorageMigrationError) as exc_info:
            storage_migration_module._remove_private_directory_via_quarantine(
                transaction_root,
                owned_identity,
            )

        assert exc_info.value.error_code == "migration_path_changed"
        assert stat.S_IMODE(unrelated_locked.stat().st_mode) == stat.S_IRUSR
        assert owned_aside.is_dir()
        assert not storage_migration_module._private_directory_quarantine_path(
            transaction_root
        ).exists()
    finally:
        os.chmod(
            unrelated_locked,
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,
        )


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX mount descriptor contract")
def test_owned_transaction_delete_walk_rechecks_mount_boundary(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    mounted_file = transaction_root / "backup" / "mounted" / "external.json"
    locked_dir = transaction_root / "backup" / "z-locked"
    mounted_file.parent.mkdir(parents=True)
    mounted_file.write_text("EXTERNAL", encoding="utf-8")
    locked_dir.mkdir()
    os.chmod(locked_dir, stat.S_IRUSR)
    locked_mode = stat.S_IMODE(locked_dir.stat().st_mode)
    mounted_inode = mounted_file.parent.stat().st_ino
    mounted_checks = 0

    def changing_mount_identity(fd):
        nonlocal mounted_checks
        identity = os.fstat(fd)
        if identity.st_ino == mounted_inode:
            mounted_checks += 1
            return "test-mount", 1 if mounted_checks == 1 else 2
        return "test-mount", 1

    monkeypatch.setattr(
        storage_migration_module,
        "_opened_mount_identity",
        changing_mount_identity,
    )

    try:
        with pytest.raises(StorageMigrationError) as exc_info:
            storage_migration_module._remove_existing_path(transaction_root)

        assert exc_info.value.error_code == "nested_mount_unsupported"
        assert mounted_checks >= 2
        assert mounted_file.read_text(encoding="utf-8") == "EXTERNAL"
        assert stat.S_IMODE(locked_dir.stat().st_mode) == locked_mode
    finally:
        os.chmod(locked_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX pinned quarantine contract")
def test_quarantine_replacement_after_preflight_is_never_deleted(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    target_root.mkdir(parents=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    storage_migration_module._create_owned_transaction_root(
        payload,
        transaction_root,
        payload["txid"],
    )
    quarantine = storage_migration_module._private_directory_quarantine_path(
        transaction_root
    )
    transaction_root.rename(quarantine)
    parked = tmp_path / "owned-parked"
    unrelated = quarantine / "unrelated.txt"
    real_preflight = storage_migration_module._preflight_directory_tree_mounts
    replaced = False

    def replace_after_preflight(path, **kwargs):
        nonlocal replaced
        result = real_preflight(path, **kwargs)
        if Path(path) == quarantine and kwargs.get("make_traversable") and not replaced:
            replaced = True
            quarantine.rename(parked)
            quarantine.mkdir()
            unrelated.write_text("UNRELATED", encoding="utf-8")
        return result

    monkeypatch.setattr(
        storage_migration_module,
        "_preflight_directory_tree_mounts",
        replace_after_preflight,
    )

    removed = storage_migration_module._remove_owned_transaction_quarantine(
        payload,
        quarantine,
        payload["txid"],
    )

    assert replaced is True
    assert removed is False
    assert unrelated.read_text(encoding="utf-8") == "UNRELATED"
    assert (
        parked / storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
    ).is_file()


@pytest.mark.unit
def test_mount_path_parsing_preserves_on_and_decodes_escapes_once(tmp_path):
    from utils import storage_migration as storage_migration_module

    output = (
        r"/dev/disk9s1 on /private/tmp/name on side/space\040dir/literal\134040 "
        "(apfs, local)\n"
    )

    raw_paths = storage_migration_module._parse_macos_mount_paths(output)
    normalized = storage_migration_module._normalize_mount_paths(raw_paths)

    assert raw_paths == [
        r"/private/tmp/name on side/space\040dir/literal\134040"
    ]
    assert normalized == [
        Path(os.path.abspath(r"/private/tmp/name on side/space dir/literal\040"))
    ]


@pytest.mark.unit
def test_directory_publish_never_replaces_a_late_empty_directory(tmp_path):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "staged"
    target = tmp_path / "published"
    source.mkdir()
    (source / "data.json").write_text('{"source": true}', encoding="utf-8")
    target.mkdir()
    target_identity = target.stat()

    with pytest.raises(OSError):
        storage_migration_module._durable_publish_without_replacing(source, target)

    current_identity = target.stat()
    assert (current_identity.st_dev, current_identity.st_ino) == (
        target_identity.st_dev,
        target_identity.st_ino,
    )
    assert list(target.iterdir()) == []
    assert (source / "data.json").is_file()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX exclusive rename contract")
def test_posix_file_publish_is_one_atomic_no_replace_rename(tmp_path):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "staged.json"
    target = tmp_path / "published.json"
    source.write_text("SOURCE", encoding="utf-8")

    storage_migration_module._durable_publish_without_replacing(source, target)

    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "SOURCE"

    contender = tmp_path / "contender.json"
    contender.write_text("CONTENDER", encoding="utf-8")
    with pytest.raises(OSError):
        storage_migration_module._durable_publish_without_replacing(contender, target)
    assert contender.read_text(encoding="utf-8") == "CONTENDER"
    assert target.read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="legacy POSIX hard-link recovery")
@pytest.mark.parametrize("original_target", (False, True))
def test_rollback_recovers_legacy_file_publish_link_unlink_crash(
    tmp_path,
    original_target,
):
    from utils import storage_migration as storage_migration_module

    target_root = tmp_path / "target"
    transaction_root = target_root / ".neko-storage-migration-test"
    staged_path = transaction_root / "staged" / "config"
    backup_path = transaction_root / "backup" / "config"
    staged_path.parent.mkdir(parents=True)
    backup_path.parent.mkdir(parents=True)
    staged_path.write_text("SOURCE", encoding="utf-8")
    publish_snapshot = storage_migration_module._snapshot_path(staged_path)
    target_root.mkdir(exist_ok=True)
    os.link(staged_path, target_root / "config")

    original_entries = []
    target_baseline = {}
    if original_target:
        backup_path.write_text("TARGET", encoding="utf-8")
        original_entries = ["config"]
        target_baseline = {
            "config": storage_migration_module._snapshot_path(backup_path)
        }

    txid = "a" * 32
    payload = {"transaction_owner_token": "b" * 64}
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        txid,
    )
    storage_migration_module._rollback_published_entries(
        target_root,
        transaction_root,
        original_entries,
        ["config"],
        target_baseline,
        {"config": publish_snapshot},
        payload=payload,
        txid=txid,
    )

    assert staged_path.read_text(encoding="utf-8") == "SOURCE"
    if original_target:
        assert (target_root / "config").read_text(encoding="utf-8") == "TARGET"
    else:
        assert not (target_root / "config").exists()


@pytest.mark.unit
def test_rollback_rejects_equal_file_content_at_different_inode(tmp_path):
    from utils import storage_migration as storage_migration_module

    target_root = tmp_path / "target"
    transaction_root = target_root / ".neko-storage-migration-test"
    staged_path = transaction_root / "staged" / "config"
    target_path = target_root / "config"
    staged_path.parent.mkdir(parents=True)
    (transaction_root / "backup").mkdir(parents=True)
    staged_path.write_text("SOURCE", encoding="utf-8")
    target_root.mkdir(exist_ok=True)
    target_path.write_text("SOURCE", encoding="utf-8")
    publish_snapshot = storage_migration_module._snapshot_path(staged_path)

    txid = "a" * 32
    payload = {"transaction_owner_token": "b" * 64}
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        txid,
    )
    with pytest.raises(StorageMigrationError, match="未记录"):
        storage_migration_module._rollback_published_entries(
            target_root,
            transaction_root,
            [],
            ["config"],
            {},
            {"config": publish_snapshot},
            payload=payload,
            txid=txid,
        )

    assert staged_path.read_text(encoding="utf-8") == "SOURCE"
    assert target_path.read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
def test_storage_migration_defaults_to_configured_anchor_root(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)
    configured_anchor_root = tmp_path / "owner-exported-anchor" / "N.E.K.O"
    config_manager.anchor_root = configured_anchor_root
    target_root = tmp_path / "new-storage" / "N.E.K.O"

    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )

    assert get_storage_migration_path(config_manager) == (
        configured_anchor_root / "state" / "storage_migration.json"
    )
    assert load_storage_migration(config_manager) == payload
    assert not (
        tmp_path / "anchor-base" / "N.E.K.O" / "state" / "storage_migration.json"
    ).exists()


@pytest.mark.unit
def test_durable_replace_flushes_both_directory_entries_after_rename(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "staged" / "config"
    target = tmp_path / "target" / "config"
    calls = []
    monkeypatch.setattr(
        storage_migration_module.os,
        "replace",
        lambda left, right: calls.append(("replace", Path(left), Path(right))),
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_fsync_migration_directory",
        lambda path: calls.append(("fsync", Path(path))),
    )

    storage_migration_module._durable_replace(source, target)

    assert calls == [
        ("replace", source, target),
        ("fsync", target.parent),
        ("fsync", source.parent),
    ]


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX required directory barrier")
def test_durable_publish_propagates_a_directory_flush_failure(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "staged" / "config"
    target = tmp_path / "target" / "config"
    source.parent.mkdir()
    target.parent.mkdir()
    source.write_text("SOURCE", encoding="utf-8")
    real_fsync = storage_migration_module._fsync_migration_directory

    def fail_target_directory(path):
        if Path(path) == target.parent:
            raise StorageMigrationError(
                "target_flush_failed",
                "simulated target directory flush failure",
            )
        return real_fsync(Path(path))

    monkeypatch.setattr(
        storage_migration_module,
        "_fsync_migration_directory",
        fail_target_directory,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._durable_publish_without_replacing(source, target)

    assert caught.value.error_code == "target_flush_failed"
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
def test_is_storage_migration_pending_ignores_terminal_status():
    payload = {
        "status": STORAGE_MIGRATION_STATUS_FAILED,
        "source_root": "/tmp/source",
        "target_root": "/tmp/target",
    }

    assert is_storage_migration_pending(payload) is False


@pytest.mark.unit
def test_rollback_required_detection_fails_closed_for_damaged_checkpoint():
    payload = {
        "status": STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED,
    }

    assert is_storage_migration_rollback_required(payload) is True
    assert is_storage_migration_pending(payload) is False


@pytest.mark.unit
def test_load_storage_migration_distinguishes_missing_from_malformed_checkpoint(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    assert load_storage_migration(config_manager) is None

    checkpoint_path = get_storage_migration_path(config_manager)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text('{"status":', encoding="utf-8")

    with pytest.raises(StorageMigrationError) as caught:
        load_storage_migration(config_manager)

    assert caught.value.error_code == "migration_checkpoint_malformed"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX required directory barrier")
def test_delete_storage_migration_propagates_checkpoint_unlink_flush_failure(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _DummyConfigManager(tmp_path)
    create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=tmp_path / "target" / "N.E.K.O",
        selection_source="custom",
    )
    migration_path = get_storage_migration_path(config_manager)
    real_fsync = storage_migration_module.os.fsync

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("checkpoint unlink flush failed")
        return real_fsync(fd)

    monkeypatch.setattr(storage_migration_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module.delete_storage_migration(config_manager)

    assert caught.value.error_code == "target_flush_failed"
    assert not migration_path.exists()


@pytest.mark.unit
def test_load_storage_migration_propagates_read_failure_as_fail_closed_error(tmp_path):
    config_manager = _DummyConfigManager(tmp_path)

    with patch(
        "utils.storage.migration.read_fixed_anchor_state_json",
        side_effect=PermissionError("checkpoint permission denied"),
    ), pytest.raises(StorageMigrationError) as caught:
        load_storage_migration(config_manager)

    assert caught.value.error_code == "migration_checkpoint_unreadable"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX dir-fd race injection")
def test_load_storage_migration_rejects_anchor_replacement_before_handle_open(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _DummyConfigManager(tmp_path)
    anchor_root = config_manager._standard_root / config_manager.app_name
    checkpoint_path = get_storage_migration_path(config_manager)
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text('{"status": "pending"}', encoding="utf-8")
    replacement_anchor = tmp_path / "replacement-anchor"
    replacement_checkpoint = replacement_anchor / "state" / checkpoint_path.name
    replacement_checkpoint.parent.mkdir(parents=True)
    replacement_checkpoint.write_text('{"status": "completed"}', encoding="utf-8")
    detached_anchor = tmp_path / "detached-anchor"
    real_open = storage_migration_module.os.open
    replaced = False

    def replace_anchor_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if not replaced and path == anchor_root.name and dir_fd is not None:
            replaced = True
            anchor_root.rename(detached_anchor)
            replacement_anchor.rename(anchor_root)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage_migration_module.os, "open", replace_anchor_before_open)

    with pytest.raises(StorageMigrationError) as caught:
        load_storage_migration(config_manager)

    assert replaced
    assert caught.value.error_code == "migration_checkpoint_unreadable"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX dir-fd race injection")
def test_load_storage_migration_revalidates_initially_missing_anchor(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _DummyConfigManager(tmp_path)
    anchor_root = config_manager._standard_root / config_manager.app_name
    checkpoint_path = get_storage_migration_path(config_manager)
    real_open = storage_migration_module.os.open
    published = False

    def publish_checkpoint_after_missing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal published
        try:
            return real_open(path, flags, mode, dir_fd=dir_fd)
        except FileNotFoundError:
            if not published:
                published = True
                checkpoint_path.parent.mkdir(parents=True)
                checkpoint_path.write_text('{"status": "pending"}', encoding="utf-8")
            raise

    monkeypatch.setattr(
        storage_migration_module.os,
        "open",
        publish_checkpoint_after_missing_open,
    )

    with pytest.raises(StorageMigrationError) as caught:
        load_storage_migration(config_manager, default={"status": "absent"})

    assert published
    assert anchor_root.exists()
    assert caught.value.error_code == "migration_checkpoint_unreadable"


@pytest.mark.unit
@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "mkfifo"),
    reason="POSIX FIFO replacement is unavailable",
)
@pytest.mark.parametrize(
    ("consumer", "expected_result", "expected_error"),
    (
        ("checkpoint", None, "migration_checkpoint_unreadable"),
        ("owner_marker", False, None),
        ("staged_fsync", None, "target_flush_failed"),
        ("workshop_rewrite", None, None),
    ),
)
def test_verified_read_consumers_reject_fifo_replacement_without_blocking(
    tmp_path,
    monkeypatch,
    consumer,
    expected_result,
    expected_error,
):
    from utils import storage_migration as storage_migration_module

    warnings = []
    if consumer == "checkpoint":
        config_manager = _DummyConfigManager(tmp_path)
        victim = get_storage_migration_path(config_manager)
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim.write_text('{"status": "pending"}', encoding="utf-8")
        operation = lambda: load_storage_migration(config_manager)
    elif consumer == "owner_marker":
        txid = "b" * 32
        payload = {"transaction_owner_token": "a" * 64}
        transaction_root = tmp_path / "transaction"
        transaction_root.mkdir()
        storage_migration_module._write_transaction_owner_marker(
            payload,
            transaction_root,
            txid,
        )
        victim = transaction_root / storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
        operation = lambda: storage_migration_module._transaction_root_is_owned(
            payload,
            transaction_root,
            txid,
        )
    elif consumer == "staged_fsync":
        staged_root = tmp_path / "transaction" / "staged"
        staged_root.mkdir(parents=True)
        victim = staged_root / "state.json"
        victim.write_text("{}", encoding="utf-8")
        operation = lambda: storage_migration_module._fsync_staged_tree(staged_root)
    else:
        source_root = tmp_path / "source"
        content_root = tmp_path / "staged"
        target_root = tmp_path / "target"
        victim = content_root / "config" / "workshop_config.json"
        victim.parent.mkdir(parents=True)
        victim.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(
            storage_migration_module.logger,
            "warning",
            lambda *args, **kwargs: warnings.append((args, kwargs)),
        )
        operation = lambda: storage_migration_module._rewrite_migrated_runtime_config_paths(
            source_root=source_root,
            content_root=content_root,
            target_root=target_root,
        )

    worker, replaced, opened_flags, results, errors = _run_fifo_open_replacement(
        monkeypatch,
        storage_migration_module,
        victim,
        operation,
    )

    assert replaced
    assert not worker.is_alive(), f"{consumer} FIFO replacement must not block"
    assert opened_flags[0] & os.O_NONBLOCK
    assert opened_flags[0] & os.O_NOFOLLOW
    if expected_error:
        assert not results
        assert len(errors) == 1
        assert isinstance(errors[0], StorageMigrationError)
        assert errors[0].error_code == expected_error
    else:
        assert results == [expected_result]
        assert not errors
    if consumer == "workshop_rewrite":
        assert warnings


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative config rewrite")
def test_workshop_rewrite_propagates_staged_file_flush_failure(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    content_root = tmp_path / "transaction" / "staged"
    target_root = tmp_path / "target"
    config_path = content_root / "config" / "workshop_config.json"
    config_path.parent.mkdir(parents=True)
    original_payload = {
        "default_workshop_folder": str(source_root / "workshop"),
    }
    config_path.write_text(json.dumps(original_payload), encoding="utf-8")
    content_root_fd = storage_migration_module._open_verified_directory(content_root)
    target_mount_identity = storage_migration_module._opened_mount_identity(
        content_root_fd
    )
    real_fsync = storage_migration_module.os.fsync

    def fail_regular_file_flush(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "injected staged config flush failure")
        return real_fsync(fd)

    monkeypatch.setattr(storage_migration_module.os, "fsync", fail_regular_file_flush)
    try:
        with pytest.raises(StorageMigrationError) as caught:
            storage_migration_module._rewrite_migrated_runtime_config_paths(
                source_root=source_root,
                content_root=content_root,
                target_root=target_root,
                content_root_fd=content_root_fd,
                expected_target_mount_identity=target_mount_identity,
            )
    finally:
        os.close(content_root_fd)

    assert caught.value.error_code == "target_flush_failed"
    assert json.loads(config_path.read_text(encoding="utf-8")) == original_payload


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative config rewrite")
def test_workshop_rewrite_rejects_oversized_config_before_parsing(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    content_root = tmp_path / "transaction" / "staged"
    target_root = tmp_path / "target"
    config_path = content_root / "config" / "workshop_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"oversized": true}', encoding="utf-8")
    content_root_fd = storage_migration_module._open_verified_directory(content_root)
    target_mount_identity = storage_migration_module._opened_mount_identity(
        content_root_fd
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_WORKSHOP_CONFIG_REWRITE_MAX_BYTES",
        4,
    )
    try:
        with pytest.raises(StorageMigrationError) as caught:
            storage_migration_module._rewrite_migrated_runtime_config_paths(
                source_root=source_root,
                content_root=content_root,
                target_root=target_root,
                content_root_fd=content_root_fd,
                expected_target_mount_identity=target_mount_identity,
            )
    finally:
        os.close(content_root_fd)

    assert caught.value.error_code == "workshop_config_too_large"
    assert config_path.read_text(encoding="utf-8") == '{"oversized": true}'


@pytest.mark.unit
def test_pending_migration_preserves_source_when_workshop_config_is_oversized(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_config = source_root / "config" / "workshop_config.json"
    source_config.parent.mkdir(parents=True, exist_ok=True)
    original_bytes = b'{"default_workshop_folder": "oversized"}'
    source_config.write_bytes(original_bytes)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_WORKSHOP_CONFIG_REWRITE_MAX_BYTES",
        4,
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "workshop_config_too_large"
    assert source_config.read_bytes() == original_bytes
    assert not (target_root / "config").exists()


@pytest.mark.unit
def test_verified_json_reader_enforces_runtime_byte_limit(tmp_path):
    from utils import storage_migration as storage_migration_module

    config_path = tmp_path / "workshop_config.json"
    config_path.write_text('{"oversized": true}', encoding="utf-8")

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._read_json_from_verified_regular_file(
            config_path,
            max_bytes=4,
        )

    assert caught.value.error_code == "workshop_config_too_large"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative config rewrite")
def test_workshop_rewrite_rejects_replaced_temporary_name(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    content_root = tmp_path / "transaction" / "staged"
    target_root = tmp_path / "target"
    config_path = content_root / "config" / "workshop_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"default_workshop_folder": str(source_root / "workshop")}),
        encoding="utf-8",
    )
    content_root_fd = storage_migration_module._open_verified_directory(content_root)
    target_mount_identity = storage_migration_module._opened_mount_identity(
        content_root_fd
    )
    real_replace = storage_migration_module.os.replace

    def replace_swapped_temporary_name(
        source,
        target,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
    ):
        if str(source).startswith(".neko-storage-rewrite-"):
            os.unlink(source, dir_fd=src_dir_fd)
            attacker_fd = os.open(
                source,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=src_dir_fd,
            )
            try:
                os.write(attacker_fd, b'{"attacker": true}')
            finally:
                os.close(attacker_fd)
        return real_replace(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(
        storage_migration_module.os,
        "replace",
        replace_swapped_temporary_name,
    )
    try:
        with pytest.raises(StorageMigrationError) as caught:
            storage_migration_module._rewrite_migrated_runtime_config_paths(
                source_root=source_root,
                content_root=content_root,
                target_root=target_root,
                content_root_fd=content_root_fd,
                expected_target_mount_identity=target_mount_identity,
            )
    finally:
        os.close(content_root_fd)

    assert caught.value.error_code == "staging_entry_changed"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative config rewrite")
def test_workshop_rewrite_rechecks_final_name_after_directory_flush(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    content_root = tmp_path / "transaction" / "staged"
    target_root = tmp_path / "target"
    config_path = content_root / "config" / "workshop_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"default_workshop_folder": str(source_root / "workshop")}),
        encoding="utf-8",
    )
    content_root_fd = storage_migration_module._open_verified_directory(content_root)
    target_mount_identity = storage_migration_module._opened_mount_identity(
        content_root_fd
    )
    real_fsync = storage_migration_module.os.fsync
    replaced = False

    def replace_final_name_during_directory_flush(fd):
        nonlocal replaced
        if not replaced and stat.S_ISDIR(os.fstat(fd).st_mode):
            replaced = True
            os.unlink("workshop_config.json", dir_fd=fd)
            replacement_fd = os.open(
                "workshop_config.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=fd,
            )
            try:
                os.write(replacement_fd, b'{"attacker": true}')
            finally:
                os.close(replacement_fd)
        return real_fsync(fd)

    monkeypatch.setattr(
        storage_migration_module.os,
        "fsync",
        replace_final_name_during_directory_flush,
    )
    try:
        with pytest.raises(StorageMigrationError) as caught:
            storage_migration_module._rewrite_migrated_runtime_config_paths(
                source_root=source_root,
                content_root=content_root,
                target_root=target_root,
                content_root_fd=content_root_fd,
                expected_target_mount_identity=target_mount_identity,
            )
    finally:
        os.close(content_root_fd)

    assert replaced is True
    assert caught.value.error_code == "staging_entry_changed"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative staged flush")
def test_staged_tree_flush_rejects_late_child_mount(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    staged_root = tmp_path / "transaction" / "staged"
    config_dir = staged_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "characters.json").write_text("{}", encoding="utf-8")
    root_fd = storage_migration_module._open_verified_directory(staged_root)
    expected_mount_identity = storage_migration_module._opened_mount_identity(root_fd)
    config_identity = config_dir.lstat()
    real_opened_mount_identity = storage_migration_module._opened_mount_identity

    def report_late_mount(fd):
        if os.path.samestat(os.fstat(fd), config_identity):
            return "test-late-target-mount", 2
        return real_opened_mount_identity(fd)

    monkeypatch.setattr(
        storage_migration_module,
        "_opened_mount_identity",
        report_late_mount,
    )
    try:
        with pytest.raises(StorageMigrationError) as caught:
            storage_migration_module._fsync_staged_tree(
                staged_root,
                root_fd=root_fd,
                expected_mount_identity=expected_mount_identity,
            )
    finally:
        os.close(root_fd)

    assert caught.value.error_code == "nested_mount_unsupported"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX staged-config manifest contract")
def test_launcher_rejects_config_sibling_added_during_workshop_rewrite(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    workshop_config = source_root / "config" / "workshop_config.json"
    workshop_config.parent.mkdir(parents=True, exist_ok=True)
    workshop_config.write_text(
        json.dumps({"default_workshop_folder": str(source_root / "workshop")}),
        encoding="utf-8",
    )
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    real_rebase = storage_migration_module.rebase_runtime_bound_workshop_config_paths
    real_fsync = storage_migration_module.os.fsync
    rewrite_active = False
    injected = False

    def mark_rewrite_active(*args, **kwargs):
        nonlocal rewrite_active
        rewrite_active = True
        return real_rebase(*args, **kwargs)

    def inject_sibling_during_config_directory_flush(fd):
        nonlocal injected
        if (
            rewrite_active
            and not injected
            and stat.S_ISDIR(os.fstat(fd).st_mode)
            and "workshop_config.json" in os.listdir(fd)
        ):
            injected = True
            attacker_fd = os.open(
                "attacker.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=fd,
            )
            try:
                os.write(attacker_fd, b'{"attacker": true}')
            finally:
                os.close(attacker_fd)
        return real_fsync(fd)

    monkeypatch.setattr(
        storage_migration_module,
        "rebase_runtime_bound_workshop_config_paths",
        mark_rewrite_active,
    )
    monkeypatch.setattr(
        storage_migration_module.os,
        "fsync",
        inject_sibling_during_config_directory_flush,
    )

    result = run_pending_storage_migration(config_manager)

    assert injected is True
    assert result["completed"] is False
    assert result["error_code"] == "staging_entry_changed"
    assert not (target_root / "config" / "attacker.json").exists()
    assert workshop_config.is_file()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX pinned rewritten-config snapshot")
def test_launcher_rejects_config_sibling_added_after_workshop_rewrite(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    workshop_config = source_root / "config" / "workshop_config.json"
    workshop_config.parent.mkdir(parents=True, exist_ok=True)
    workshop_config.write_text(
        json.dumps({"default_workshop_folder": str(source_root / "workshop")}),
        encoding="utf-8",
    )
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    real_rewrite = storage_migration_module._rewrite_migrated_runtime_config_paths

    def rewrite_then_add_sibling(**kwargs):
        rewritten_snapshot = real_rewrite(**kwargs)
        sibling = Path(kwargs["content_root"]) / "config" / "attacker.json"
        sibling.write_text('{"attacker": true}', encoding="utf-8")
        return rewritten_snapshot

    monkeypatch.setattr(
        storage_migration_module,
        "_rewrite_migrated_runtime_config_paths",
        rewrite_then_add_sibling,
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "staging_entry_changed"
    assert not (target_root / "config" / "attacker.json").exists()
    assert workshop_config.is_file()


@pytest.mark.unit
def test_retained_root_cleanup_rejects_paths_that_contain_protected_roots(tmp_path):
    retained_root = tmp_path / "retained"
    current_root = retained_root / "current" / "N.E.K.O"
    anchor_root = tmp_path / "anchor" / "N.E.K.O"
    target_root = retained_root / "target" / "N.E.K.O"
    retained_root.mkdir(parents=True)
    current_root.mkdir(parents=True)
    anchor_root.mkdir(parents=True)
    target_root.mkdir(parents=True)

    assert not is_retained_root_cleanup_available(
        retained_root,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
    )
    assert not is_retained_root_cleanup_available(
        tmp_path,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
    )


@pytest.mark.unit
@pytest.mark.parametrize("protected_name", ("current", "anchor", "target"))
def test_retained_root_cleanup_rejects_paths_inside_protected_roots(
    tmp_path,
    protected_name,
):
    current_root = tmp_path / "current" / "N.E.K.O"
    anchor_root = tmp_path / "anchor" / "N.E.K.O"
    target_root = tmp_path / "target" / "N.E.K.O"
    protected_roots = {
        "current": current_root,
        "anchor": anchor_root,
        "target": target_root,
    }
    retained_root = protected_roots[protected_name] / "nested-retained"
    retained_root.mkdir(parents=True)
    for root in protected_roots.values():
        root.mkdir(parents=True, exist_ok=True)

    assert not is_retained_root_cleanup_available(
        retained_root,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
        allow_anchor_root=True,
    )


@pytest.mark.unit
def test_run_pending_storage_migration_commits_policy_and_copies_runtime_entries(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "memory" / "A").mkdir(parents=True, exist_ok=True)
    (source_root / "card_faces").mkdir(parents=True, exist_ok=True)
    (source_root / "avatar_tools" / "local-12345678-1234-4123-8123-123456789abc").mkdir(parents=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")
    (source_root / "memory" / "A" / "recent.json").write_text('[{"role":"user","content":"hi"}]', encoding="utf-8")
    (source_root / "card_faces" / "YUI.png").write_bytes(b"fake-png")
    (source_root / "card_faces" / "YUI.json").write_text('{"origin":"self"}', encoding="utf-8")
    (source_root / "avatar_tools" / "local-12345678-1234-4123-8123-123456789abc" / "record.json").write_text(
        '{"recordVersion":2}',
        encoding="utf-8",
    )

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is True
    assert result["completed"] is True
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_COMPLETED
    assert result["payload"]["retained_source_root"] == str(source_root.resolve())
    assert result["payload"]["retained_source_mode"] == "manual_retention"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == '{"current":"A"}'
    assert (target_root / "memory" / "A" / "recent.json").read_text(encoding="utf-8") == '[{"role":"user","content":"hi"}]'
    assert (target_root / "card_faces" / "YUI.png").read_bytes() == b"fake-png"
    assert (target_root / "card_faces" / "YUI.json").read_text(encoding="utf-8") == '{"origin":"self"}'
    assert (
        target_root
        / "avatar_tools"
        / "local-12345678-1234-4123-8123-123456789abc"
        / "record.json"
    ).read_text(encoding="utf-8") == '{"recordVersion":2}'

    policy_payload = load_storage_policy(config_manager, anchor_root=tmp_path / "anchor-base" / "N.E.K.O")
    assert policy_payload["selected_root"] == str(target_root.resolve())

    root_state = config_manager.load_root_state()
    assert root_state["current_root"] == str(target_root.resolve())
    assert root_state["last_known_good_root"] == str(target_root.resolve())
    assert root_state["last_migration_result"].startswith("completed:")
    assert root_state["legacy_cleanup_pending"] is True


@pytest.mark.unit
def test_run_pending_storage_migration_requires_confirmation_for_existing_target_content(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")
    (target_root / "config").mkdir(parents=True, exist_ok=True)
    (target_root / "config" / "characters.json").write_text('{"existing":"B"}', encoding="utf-8")
    (target_root / "notes.txt").write_text("keep me", encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    missing_confirmation_result = run_pending_storage_migration(config_manager)

    assert missing_confirmation_result["completed"] is False
    assert missing_confirmation_result["error_code"] == "target_confirmation_required"
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == '{"existing":"B"}'

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )

    confirmed_result = run_pending_storage_migration(config_manager)

    assert confirmed_result["completed"] is True
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == '{"current":"A"}'
    assert (target_root / "notes.txt").read_text(encoding="utf-8") == "keep me"


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_nested_source_and_target_paths(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = source_root / "nested-target" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is True
    assert result["completed"] is False
    assert result["error_code"] == "paths_nested"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_apfs_case_alias_of_source(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    source_root.mkdir(parents=True, exist_ok=True)
    target_alias = source_root.with_name(source_root.name.swapcase())
    if not target_alias.exists() or not target_alias.samefile(source_root):
        pytest.skip("test volume is case-sensitive")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_alias,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_matches_source"


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_nested_target_through_apfs_case_alias(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    source_root.mkdir(parents=True, exist_ok=True)
    source_alias = source_root.with_name(source_root.name.swapcase())
    if not source_alias.exists() or not source_alias.samefile(source_root):
        pytest.skip("test volume is case-sensitive")
    target_root = source_alias / "nested-target" / "N.E.K.O"

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "paths_nested"


@pytest.mark.unit
def test_run_pending_storage_migration_fails_closed_when_path_identity_is_uninspectable(
    tmp_path,
    monkeypatch,
):
    from utils.storage import policy as storage_policy_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    def deny_identity(_path):
        raise storage_policy_module.PathIdentityUnavailable("permission denied")

    monkeypatch.setattr(storage_policy_module, "_existing_path_identity", deny_identity)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "path_identity_uninspectable"


@pytest.mark.unit
def test_run_pending_storage_migration_marks_cleanup_pending_only_for_non_anchor_retained_root(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = tmp_path / "legacy-runtime" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    root_state = config_manager.load_root_state()
    assert root_state["legacy_cleanup_pending"] is True


@pytest.mark.unit
def test_run_pending_storage_migration_marks_cleanup_pending_when_anchor_root_retains_runtime_entries(tmp_path):
    config_manager = _make_anchor_root_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    root_state = config_manager.load_root_state()
    assert root_state["legacy_cleanup_pending"] is True


@pytest.mark.unit
def test_run_pending_storage_migration_marks_failure_and_recovers_to_source_root(tmp_path, monkeypatch):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"

    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text('{"current":"A"}', encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )

    def _boom(*args, **kwargs):
        raise StorageMigrationError("copy_failed", "simulated copy failure")

    monkeypatch.setattr("utils.storage_migration._copy_runtime_entry", _boom)

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is True
    assert result["completed"] is False
    assert result["error_code"] == "copy_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED

    policy_payload = load_storage_policy(config_manager, anchor_root=tmp_path / "anchor-base" / "N.E.K.O")
    assert policy_payload["selected_root"] == str(source_root.resolve())
    assert policy_payload["selection_source"] == "recovered"

    root_state = config_manager.load_root_state()
    assert root_state["mode"] == "deferred_init"
    assert root_state["current_root"] == str(source_root.resolve())
    assert root_state["last_migration_result"] == "failed:copy_failed"


@pytest.mark.unit
def test_run_pending_storage_migration_failure_uses_payload_source_before_normalization(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = tmp_path / "external-source" / "N.E.K.O"
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recommended",
    )
    payload = load_storage_migration(config_manager, anchor_root=tmp_path / "anchor-base" / "N.E.K.O")
    persisted_source_root = payload["source_root"]
    original_normalize_runtime_root = storage_migration_module.normalize_runtime_root

    def fail_for_payload_source(value):
        if str(value) == persisted_source_root:
            raise ValueError("simulated source path normalization failure")
        return original_normalize_runtime_root(value)

    monkeypatch.setattr(storage_migration_module, "normalize_runtime_root", fail_for_payload_source)

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is True
    assert result["completed"] is False
    assert result["error_code"] == "storage_migration_unexpected"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED
    assert result["payload"]["backup_root"] == persisted_source_root

    root_state = config_manager.load_root_state()
    assert root_state["mode"] == "deferred_init"
    assert root_state["current_root"] == persisted_source_root
    assert root_state["last_known_good_root"] == persisted_source_root
    assert root_state["last_migration_source"] == persisted_source_root
    assert root_state["last_migration_backup"] == persisted_source_root


@pytest.mark.unit
def test_run_pending_storage_migration_copies_every_selected_root_data_class(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    representative_files = {
        "pngtuber/avatar.png": b"avatar",
        "jukebox/library.json": b"{}",
        "card_faces/YUI.png": b"face",
        "state/game_scores/badminton_scores.db": b"sqlite-score",
        "embedding_models/model.bin": b"embedding",
        "runtimes/rapidocr/runtime.bin": b"runtime",
        "plugin-runtime/plugin-installs/task.json": b"{}",
    }
    for relative_path, content in representative_files.items():
        source_path = source_root / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(content)

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    for relative_path, content in representative_files.items():
        assert (target_root / relative_path).read_bytes() == content


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX file and directory fsync semantics")
def test_fsync_staged_tree_flushes_nested_directories_from_leaf_to_root(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    staged_root = tmp_path / "transaction" / "staged"
    nested_root = staged_root / "state" / "game_scores"
    nested_root.mkdir(parents=True)
    (staged_root / "top-level-state.json").write_text("{}", encoding="utf-8")
    (nested_root / "scores.db").write_bytes(b"score")
    flushed_directories = []

    monkeypatch.setattr(storage_migration_module.sys, "platform", "linux")
    monkeypatch.setattr(
        storage_migration_module,
        "_fsync_migration_directory",
        lambda path: flushed_directories.append(Path(path)),
    )

    storage_migration_module._fsync_staged_tree(staged_root)

    assert flushed_directories == [nested_root, staged_root / "state", staged_root]


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync semantics")
def test_posix_fsync_staged_tree_propagates_directory_failure(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    staged_root = tmp_path / "transaction" / "staged"
    staged_root.mkdir(parents=True)

    def fail_fsync(_fd):
        raise OSError("directory flush failed")

    monkeypatch.setattr(storage_migration_module.sys, "platform", "linux")
    monkeypatch.setattr(storage_migration_module.os, "fsync", fail_fsync)

    with pytest.raises(StorageMigrationError) as exc_info:
        storage_migration_module._fsync_staged_tree(staged_root)

    assert exc_info.value.error_code == "target_flush_failed"


@pytest.mark.unit
def test_new_target_root_flushes_every_created_parent_boundary(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    target_root = tmp_path / "new-volume-folder" / "nested" / "N.E.K.O"
    flushed = []
    monkeypatch.setattr(
        storage_migration_module,
        "_fsync_migration_directory",
        lambda path: flushed.append(Path(path)),
    )

    storage_migration_module._ensure_target_root_writable(target_root)

    assert target_root.is_dir()
    assert flushed == [
        target_root.parent,
        target_root.parent.parent,
        tmp_path,
    ]


@pytest.mark.unit
def test_staged_copy_flushes_before_and_after_restoring_open_file_metadata(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source" / "readonly.json"
    staged_file = tmp_path / "transaction" / "staged" / "config" / "readonly.json"
    source_file.parent.mkdir(parents=True)
    staged_file.parent.mkdir(parents=True)
    source_file.write_text('{"preserved": true}', encoding="utf-8")
    os.chmod(source_file, stat.S_IRUSR)
    source_mode = stat.S_IMODE(source_file.stat().st_mode)
    real_fsync = os.fsync
    real_copy_metadata = storage_migration_module._copy_open_file_metadata
    events = []

    def record_windows_style_fsync(fd):
        os.write(fd, b"")
        events.append("fsync")
        real_fsync(fd)

    def record_copy_metadata(source_fd, target_fd, metadata):
        events.append("metadata")
        return real_copy_metadata(source_fd, target_fd, metadata)

    monkeypatch.setattr(storage_migration_module.os, "fsync", record_windows_style_fsync)
    monkeypatch.setattr(
        storage_migration_module,
        "_copy_open_file_metadata",
        record_copy_metadata,
    )

    storage_migration_module._copy_staged_file_durably(source_file, staged_file)

    assert events == ["fsync", "metadata", "fsync"]
    assert stat.S_IMODE(staged_file.stat().st_mode) == source_mode
    assert staged_file.read_text(encoding="utf-8") == '{"preserved": true}'


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative mount contract")
def test_runtime_directory_copy_rejects_mount_added_after_snapshot(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    source_entry = source_root / "config"
    mounted_directory = source_entry / "a-mounted"
    mounted_file = mounted_directory / "external.bin"
    staged_entry = tmp_path / "transaction" / "staged" / "config"
    mounted_directory.mkdir(parents=True)
    mounted_file.write_bytes(b"external-volume-bytes")
    staged_entry.parent.mkdir(parents=True)
    expected_mount_identity = storage_migration_module._runtime_root_mount_identity(
        source_root
    )
    mounted_identity = mounted_directory.lstat()
    original_snapshot = storage_migration_module._snapshot_path
    mount_added = False

    def snapshot_then_add_mount(path, *args, **kwargs):
        nonlocal mount_added
        snapshot = original_snapshot(path, *args, **kwargs)
        if Path(path) == source_entry:
            mount_added = True
        return snapshot

    def simulated_mount_identity(fd):
        opened = os.fstat(fd)
        if mount_added and os.path.samestat(opened, mounted_identity):
            return "test-mount", 2
        return expected_mount_identity

    monkeypatch.setattr(
        storage_migration_module,
        "_snapshot_path",
        snapshot_then_add_mount,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_opened_mount_identity",
        simulated_mount_identity,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._copy_runtime_entry(
            source_entry,
            staged_entry,
            expected_mount_identity=expected_mount_identity,
        )

    assert caught.value.error_code == "nested_mount_unsupported"
    assert mounted_file.read_bytes() == b"external-volume-bytes"
    assert not (staged_entry / "a-mounted").exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative mount contract")
def test_runtime_file_copy_rejects_mount_added_after_snapshot(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    source_root.mkdir()
    source_entry = source_root / "database.db"
    source_entry.write_bytes(b"external-volume-bytes")
    staged_entry = tmp_path / "transaction" / "staged" / "database.db"
    staged_entry.parent.mkdir(parents=True)
    expected_mount_identity = storage_migration_module._runtime_root_mount_identity(
        source_root
    )
    mounted_identity = source_entry.lstat()
    original_snapshot = storage_migration_module._snapshot_path
    mount_added = False

    def snapshot_then_add_mount(path, *args, **kwargs):
        nonlocal mount_added
        snapshot = original_snapshot(path, *args, **kwargs)
        if Path(path) == source_entry:
            mount_added = True
        return snapshot

    def simulated_mount_identity(fd):
        opened = os.fstat(fd)
        if mount_added and os.path.samestat(opened, mounted_identity):
            return "test-mount", 2
        return expected_mount_identity

    monkeypatch.setattr(
        storage_migration_module,
        "_snapshot_path",
        snapshot_then_add_mount,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_opened_mount_identity",
        simulated_mount_identity,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._copy_runtime_entry(
            source_entry,
            staged_entry,
            expected_mount_identity=expected_mount_identity,
        )

    assert caught.value.error_code == "nested_mount_unsupported"
    assert source_entry.read_bytes() == b"external-volume-bytes"
    assert not staged_entry.exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative target contract")
def test_runtime_directory_copy_never_follows_replaced_staging_directory(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    source_entry = source_root / "config"
    source_file = source_entry / "characters.json"
    staged_entry = tmp_path / "transaction" / "staged" / "config"
    moved_staged_entry = tmp_path / "moved-staged-config"
    external_directory = tmp_path / "external"
    source_entry.mkdir(parents=True)
    staged_entry.parent.mkdir(parents=True)
    external_directory.mkdir()
    source_file.write_text("source-data", encoding="utf-8")
    expected_mount_identity = storage_migration_module._runtime_root_mount_identity(
        source_root
    )
    real_copy_file = storage_migration_module._copy_staged_file_durably
    replaced = False

    def replace_staging_directory_before_file_open(*args, **kwargs):
        nonlocal replaced
        if not replaced:
            replaced = True
            staged_entry.rename(moved_staged_entry)
            staged_entry.symlink_to(external_directory, target_is_directory=True)
        return real_copy_file(*args, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_copy_staged_file_durably",
        replace_staging_directory_before_file_open,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._copy_runtime_entry(
            source_entry,
            staged_entry,
            expected_mount_identity=expected_mount_identity,
        )

    assert caught.value.error_code == "staging_entry_changed"
    assert replaced is True
    assert not (external_directory / "characters.json").exists()
    assert (moved_staged_entry / "characters.json").read_text(encoding="utf-8") == "source-data"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative target mount contract")
def test_runtime_directory_copy_rejects_target_mount_added_before_child_open(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    source_entry = source_root / "config"
    source_file = source_entry / "characters.json"
    staged_entry = tmp_path / "transaction" / "staged" / "config"
    source_entry.mkdir(parents=True)
    staged_entry.parent.mkdir(parents=True)
    source_file.write_text("source-data", encoding="utf-8")
    expected_mount_identity = storage_migration_module._runtime_root_mount_identity(
        source_root
    )
    real_mkdir = storage_migration_module.os.mkdir
    real_opened_mount_identity = storage_migration_module._opened_mount_identity
    mounted_identity = None

    def create_then_overlay_target(path, mode=0o777, *, dir_fd=None):
        nonlocal mounted_identity
        result = real_mkdir(path, mode, dir_fd=dir_fd)
        if dir_fd is not None and path == staged_entry.name:
            mounted_identity = staged_entry.lstat()
        return result

    def observe_late_target_mount(fd):
        opened = os.fstat(fd)
        if mounted_identity is not None and os.path.samestat(opened, mounted_identity):
            return "test-target-mount", 2
        return real_opened_mount_identity(fd)

    monkeypatch.setattr(storage_migration_module.os, "mkdir", create_then_overlay_target)
    monkeypatch.setattr(
        storage_migration_module,
        "_opened_mount_identity",
        observe_late_target_mount,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._copy_runtime_entry(
            source_entry,
            staged_entry,
            expected_mount_identity=expected_mount_identity,
        )

    assert caught.value.error_code == "nested_mount_unsupported"
    assert mounted_identity is not None
    assert not (staged_entry / "characters.json").exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX pinned staging-root contract")
def test_nested_runtime_entry_never_restarts_from_replaced_staging_root(tmp_path):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    source_entry = source_root / "state" / "game_scores"
    source_file = source_entry / "scores.json"
    staged_root = tmp_path / "transaction" / "staged"
    staged_entry = staged_root / "state" / "game_scores"
    moved_staged_root = tmp_path / "moved-staged"
    source_entry.mkdir(parents=True)
    staged_root.mkdir(parents=True)
    source_file.write_text("source-score", encoding="utf-8")
    source_mount_identity = storage_migration_module._runtime_root_mount_identity(
        source_root
    )
    staged_root_fd = storage_migration_module._open_verified_directory(staged_root)
    target_mount_identity = storage_migration_module._opened_mount_identity(
        staged_root_fd
    )
    try:
        staged_root.rename(moved_staged_root)
        staged_root.mkdir()
        staged_parent_fd = storage_migration_module._open_or_create_posix_directory_chain(
            staged_root_fd,
            staged_root,
            ("state",),
            expected_mount_identity=target_mount_identity,
        )
        try:
            with pytest.raises(StorageMigrationError) as caught:
                storage_migration_module._copy_runtime_entry(
                    source_entry,
                    staged_entry,
                    expected_mount_identity=source_mount_identity,
                    target_parent_fd=staged_parent_fd,
                    target_name="game_scores",
                    expected_target_mount_identity=target_mount_identity,
                )
        finally:
            os.close(staged_parent_fd)
    finally:
        os.close(staged_root_fd)

    assert caught.value.error_code == "staging_entry_changed"
    assert not (staged_root / "state").exists()
    assert (
        moved_staged_root / "state" / "game_scores" / "scores.json"
    ).read_text(encoding="utf-8") == "source-score"


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows path-based transaction creation")
def test_launcher_rejects_transaction_root_replaced_after_creation(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("source-data", encoding="utf-8")
    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        pending["txid"],
    )
    moved_transaction_root = target_root / "moved-owned-transaction"
    replacement_sentinel = transaction_root / "third-party.txt"
    real_create = storage_migration_module._create_owned_transaction_root

    def create_then_replace(payload, path, txid):
        created_identity = real_create(payload, path, txid)
        path.rename(moved_transaction_root)
        path.mkdir()
        replacement_sentinel.write_text("keep", encoding="utf-8")
        return created_identity

    monkeypatch.setattr(
        storage_migration_module,
        "_create_owned_transaction_root",
        create_then_replace,
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_ownership_changed"
    assert replacement_sentinel.read_text(encoding="utf-8") == "keep"
    assert not (transaction_root / "staged").exists()
    assert source_file.read_text(encoding="utf-8") == "source-data"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX pinned transaction fsync")
def test_launcher_rejects_transaction_root_replaced_during_pinned_flush(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("source-data", encoding="utf-8")
    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        pending["txid"],
    )
    moved_transaction_root = target_root / "moved-owned-transaction"
    replacement_sentinel = transaction_root / "third-party.txt"
    real_fsync = storage_migration_module.os.fsync
    replaced = False

    def replace_during_transaction_flush(fd):
        nonlocal replaced
        if (
            not replaced
            and transaction_root.is_dir()
            and (transaction_root / "staged").is_dir()
            and (transaction_root / "backup").is_dir()
            and os.path.samestat(os.fstat(fd), transaction_root.lstat())
        ):
            real_fsync(fd)
            transaction_root.rename(moved_transaction_root)
            transaction_root.mkdir()
            replacement_sentinel.write_text("keep", encoding="utf-8")
            replaced = True
            return None
        return real_fsync(fd)

    monkeypatch.setattr(
        storage_migration_module.os,
        "fsync",
        replace_during_transaction_flush,
    )

    result = run_pending_storage_migration(config_manager)

    assert replaced is True
    assert result["completed"] is False
    assert result["error_code"] == "staging_entry_changed"
    assert replacement_sentinel.read_text(encoding="utf-8") == "keep"
    assert moved_transaction_root.is_dir()
    assert source_file.read_text(encoding="utf-8") == "source-data"


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows directory rename guard")
def test_windows_transaction_directory_guard_blocks_rename(tmp_path):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / "transaction"
    moved_root = tmp_path / "moved-transaction"
    transaction_root.mkdir()
    identity = transaction_root.lstat()
    guard = storage_migration_module._open_windows_directory_rename_guard(
        transaction_root,
        identity,
    )
    try:
        with pytest.raises(OSError):
            transaction_root.rename(moved_root)
    finally:
        storage_migration_module._close_windows_directory_rename_guard(guard)

    transaction_root.rename(moved_root)
    assert moved_root.is_dir()


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows nested staging guards")
def test_windows_nested_staging_directory_is_guarded_only_during_copy(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "source" / "config"
    source_file = source / "nested" / "characters.json"
    target = tmp_path / "transaction" / "staged" / "config"
    source_file.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    original_copy = storage_migration_module._copy_staged_file_durably
    attempted = False

    def assert_parent_guarded(source_path, target_path, **kwargs):
        nonlocal attempted
        target_parent = Path(target_path).parent
        moved_parent = target_parent.with_name(target_parent.name + "-moved")
        with pytest.raises(OSError):
            target_parent.rename(moved_parent)
        attempted = True
        return original_copy(source_path, target_path, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_copy_staged_file_durably",
        assert_parent_guarded,
    )
    storage_migration_module._copy_runtime_entry(source, target)

    assert attempted is True
    moved_nested = target / "nested-moved"
    (target / "nested").rename(moved_nested)
    assert (moved_nested / "characters.json").read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows single-pass staging copy")
def test_windows_late_source_directory_is_not_copied_unguarded(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "source" / "config"
    source_file = source / "characters.json"
    target = tmp_path / "transaction" / "staged" / "config"
    source.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    original_copy = storage_migration_module._copy_staged_file_durably
    injected = False

    def inject_late_directory(source_path, target_path, **kwargs):
        nonlocal injected
        if not injected:
            late_file = source / "late" / "outside.json"
            late_file.parent.mkdir()
            late_file.write_text("LATE", encoding="utf-8")
            injected = True
        return original_copy(source_path, target_path, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_copy_staged_file_durably",
        inject_late_directory,
    )
    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._copy_runtime_entry(source, target)

    assert injected is True
    assert caught.value.error_code == "source_changed_during_migration"
    assert not (target / "late").exists()
    target.rename(target.with_name("config-moved"))


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows staging guard release")
def test_windows_nested_staging_guards_release_after_copy_failure(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source = tmp_path / "source" / "config"
    source_file = source / "nested" / "characters.json"
    target = tmp_path / "transaction" / "staged" / "config"
    source_file.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source_file.write_text("SOURCE", encoding="utf-8")

    def fail_copy(*_args, **_kwargs):
        raise StorageMigrationError("copy_failed", "injected copy failure")

    monkeypatch.setattr(
        storage_migration_module,
        "_copy_staged_file_durably",
        fail_copy,
    )
    with pytest.raises(StorageMigrationError, match="injected copy failure"):
        storage_migration_module._copy_runtime_entry(source, target)

    moved_nested = target / "nested-moved"
    (target / "nested").rename(moved_nested)
    target.rename(target.with_name("config-moved"))


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows workshop config guard")
def test_windows_workshop_rewrite_guards_config_directory_and_releases_it(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    content_root = tmp_path / "transaction" / "staged"
    target_root = tmp_path / "target"
    config_directory = content_root / "config"
    config_path = config_directory / "workshop_config.json"
    config_directory.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"default_workshop_folder": str(source_root / "workshop")}),
        encoding="utf-8",
    )
    original_rename = storage_migration_module._rename_windows_open_file
    original_snapshot = (
        storage_migration_module._snapshot_windows_config_with_open_workshop
    )
    attempted = False
    write_blocked_during_snapshot = False

    def assert_config_guarded(fd, target_name, **kwargs):
        nonlocal attempted
        if not attempted:
            with pytest.raises(OSError):
                config_directory.rename(config_directory.with_name("config-moved"))
            attempted = True
        return original_rename(fd, target_name, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_rename_windows_open_file",
        assert_config_guarded,
    )

    def assert_rewritten_file_guarded(config_path, workshop_path, workshop_fd):
        nonlocal write_blocked_during_snapshot
        with pytest.raises(OSError):
            workshop_path.write_text('{"attacker":true}', encoding="utf-8")
        write_blocked_during_snapshot = True
        return original_snapshot(config_path, workshop_path, workshop_fd)

    monkeypatch.setattr(
        storage_migration_module,
        "_snapshot_windows_config_with_open_workshop",
        assert_rewritten_file_guarded,
    )
    snapshot = storage_migration_module._rewrite_migrated_runtime_config_paths(
        source_root=source_root,
        content_root=content_root,
        target_root=target_root,
    )

    assert attempted is True
    assert write_blocked_during_snapshot is True
    assert snapshot is not None
    moved_config = config_directory.with_name("config-moved")
    config_directory.rename(moved_config)
    rewritten = json.loads((moved_config / "workshop_config.json").read_text(encoding="utf-8"))
    assert rewritten["default_workshop_folder"] == str(target_root / "workshop")


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows workshop config CAS")
def test_windows_workshop_rewrite_preserves_concurrent_name_winner(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    content_root = tmp_path / "transaction" / "staged"
    target_root = tmp_path / "target"
    config_directory = content_root / "config"
    config_path = config_directory / "workshop_config.json"
    config_directory.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"default_workshop_folder": str(source_root / "workshop")}),
        encoding="utf-8",
    )
    rival_bytes = b'{"generation":"concurrent-new","new_field":true}'
    original_rename = storage_migration_module._rename_windows_open_file
    injected = False

    def inject_winner_after_exact_source_rename(fd, target_name, **kwargs):
        nonlocal injected
        original_rename(fd, target_name, **kwargs)
        if not injected and target_name.endswith(".old"):
            injected = True
            config_path.write_bytes(rival_bytes)

    monkeypatch.setattr(
        storage_migration_module,
        "_rename_windows_open_file",
        inject_winner_after_exact_source_rename,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._rewrite_migrated_runtime_config_paths(
            source_root=source_root,
            content_root=content_root,
            target_root=target_root,
        )

    assert injected is True
    assert caught.value.error_code == "staging_entry_changed"
    assert config_path.read_bytes() == rival_bytes
    assert sorted(path.name for path in config_directory.iterdir()) == [
        "workshop_config.json"
    ]


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows workshop config CAS")
def test_windows_workshop_rewrite_removes_readonly_source_backup(tmp_path):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    content_root = tmp_path / "transaction" / "staged"
    target_root = tmp_path / "target"
    config_directory = content_root / "config"
    config_path = config_directory / "workshop_config.json"
    config_directory.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"default_workshop_folder": str(source_root / "workshop")}),
        encoding="utf-8",
    )
    config_path.chmod(stat.S_IREAD)

    snapshot = storage_migration_module._rewrite_migrated_runtime_config_paths(
        source_root=source_root,
        content_root=content_root,
        target_root=target_root,
    )

    assert snapshot is not None
    rewritten = json.loads(config_path.read_text(encoding="utf-8"))
    assert rewritten["default_workshop_folder"] == str(target_root / "workshop")
    assert sorted(path.name for path in config_directory.iterdir()) == [
        "workshop_config.json"
    ]


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows workshop config CAS")
def test_windows_workshop_rewrite_preserves_backup_when_publish_and_restore_fail(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source_root = tmp_path / "source"
    content_root = tmp_path / "transaction" / "staged"
    target_root = tmp_path / "target"
    config_directory = content_root / "config"
    config_path = config_directory / "workshop_config.json"
    config_directory.mkdir(parents=True)
    original_bytes = json.dumps(
        {"default_workshop_folder": str(source_root / "workshop")}
    ).encode("utf-8")
    config_path.write_bytes(original_bytes)
    original_rename = storage_migration_module._rename_windows_open_file

    def fail_public_name(fd, target_name, **kwargs):
        if target_name == config_path.name:
            raise OSError("injected public-name failure")
        return original_rename(fd, target_name, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_rename_windows_open_file",
        fail_public_name,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._rewrite_migrated_runtime_config_paths(
            source_root=source_root,
            content_root=content_root,
            target_root=target_root,
        )

    assert caught.value.error_code == "target_flush_failed"
    assert not config_path.exists()
    backup_paths = list(config_directory.glob(".neko-storage-rewrite-*.old"))
    assert len(backup_paths) == 1
    assert backup_paths[0].read_bytes() == original_bytes
    assert not list(config_directory.glob(".neko-storage-rewrite-*.tmp"))


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows publication guards")
def test_windows_publication_guards_target_root_and_nested_parent(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    score_file = source_root / "state" / "game_scores" / "score.json"
    score_file.parent.mkdir(parents=True)
    score_file.write_text("SOURCE", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    original_publish = storage_migration_module._durable_publish_without_replacing
    checked = False

    def assert_publish_parents_guarded(source, target):
        nonlocal checked
        source = Path(source)
        target = Path(target)
        if target == target_root / "state" / "game_scores":
            with pytest.raises(OSError):
                target_root.rename(target_root.with_name("N.E.K.O-moved"))
            with pytest.raises(OSError):
                target_root.parent.rename(
                    target_root.parent.with_name("target-selected-moved")
                )
            with pytest.raises(OSError):
                target.parent.rename(target.parent.with_name("state-moved"))
            checked = True
        return original_publish(source, target)

    monkeypatch.setattr(
        storage_migration_module,
        "_durable_publish_without_replacing",
        assert_publish_parents_guarded,
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    assert checked is True
    moved_state = target_root / "state-moved"
    (target_root / "state").rename(moved_state)
    assert (moved_state / "game_scores" / "score.json").read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows declared-entry parent guards")
def test_windows_copy_guards_declared_entry_parent_from_first_write(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    score_file = source_root / "state" / "game_scores" / "score.json"
    score_file.parent.mkdir(parents=True)
    score_file.write_text("SOURCE", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    original_copy = storage_migration_module._copy_windows_directory_tree_durably
    checked = False

    def assert_declared_parent_guarded(source_path, target_path, source_identity):
        nonlocal checked
        target_path = Path(target_path)
        if target_path.parts[-2:] == ("state", "game_scores"):
            with pytest.raises(OSError):
                target_path.parent.rename(
                    target_path.parent.with_name("state-moved")
                )
            checked = True
        return original_copy(source_path, target_path, source_identity)

    monkeypatch.setattr(
        storage_migration_module,
        "_copy_windows_directory_tree_durably",
        assert_declared_parent_guarded,
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    assert checked is True
    assert (target_root / "state" / "game_scores" / "score.json").read_text(
        encoding="utf-8"
    ) == "SOURCE"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="BSD flag filtering is POSIX-only")
def test_copy_metadata_filters_blocking_bsd_flags(monkeypatch):
    from utils import storage_migration as storage_migration_module

    blocking_flags = 0
    for name in (
        "UF_IMMUTABLE",
        "SF_IMMUTABLE",
        "UF_APPEND",
        "SF_APPEND",
        "UF_NOUNLINK",
        "SF_NOUNLINK",
    ):
        blocking_flags |= int(getattr(stat, name, 0) or 0)
    if blocking_flags == 0:
        pytest.skip("platform exposes no BSD blocking flags")
    harmless_flag = 1 << 29
    captured = []
    monkeypatch.setattr(storage_migration_module, "_copy_open_file_xattrs", lambda *_: None)
    monkeypatch.setattr(storage_migration_module.os, "utime", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(storage_migration_module.os, "fchmod", lambda *_: None)
    monkeypatch.setattr(
        storage_migration_module.os,
        "fchflags",
        lambda _fd, flags: captured.append(flags),
        raising=False,
    )
    metadata = SimpleNamespace(
        st_atime_ns=1,
        st_mtime_ns=2,
        st_mode=stat.S_IFREG | 0o600,
        st_flags=blocking_flags | harmless_flag,
    )

    storage_migration_module._copy_open_file_metadata(10, 11, metadata)

    assert captured == [harmless_flag]


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX name replacement injection")
def test_staged_copy_metadata_never_follows_a_replaced_target_name(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source.json"
    staged_file = tmp_path / "staged.json"
    external_file = tmp_path / "external.json"
    source_file.write_text("source", encoding="utf-8")
    external_file.write_text("external", encoding="utf-8")
    os.chmod(source_file, 0o600)
    os.chmod(external_file, 0o644)
    external_mode = stat.S_IMODE(external_file.stat().st_mode)
    real_copy_metadata = storage_migration_module._copy_open_file_metadata

    def replace_name_then_copy_metadata(source_fd, target_fd, metadata):
        staged_file.unlink()
        staged_file.symlink_to(external_file)
        return real_copy_metadata(source_fd, target_fd, metadata)

    monkeypatch.setattr(
        storage_migration_module,
        "_copy_open_file_metadata",
        replace_name_then_copy_metadata,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._copy_staged_file_durably(source_file, staged_file)

    assert caught.value.error_code == "staging_entry_changed"
    assert staged_file.is_symlink()
    assert external_file.read_text(encoding="utf-8") == "external"
    assert stat.S_IMODE(external_file.stat().st_mode) == external_mode


@pytest.mark.unit
@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX unlink permits a stable replacement while the original handle remains open",
)
def test_staged_copy_failure_preserves_a_late_target_competitor(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source.json"
    staged_file = tmp_path / "staged.json"
    source_file.write_text("source", encoding="utf-8")

    def replace_target_then_fail(_source_fd, _target_fd, _metadata):
        staged_file.unlink()
        staged_file.write_text("late-winner", encoding="utf-8")
        raise OSError("metadata restore failed")

    monkeypatch.setattr(
        storage_migration_module,
        "_copy_open_file_metadata",
        replace_target_then_fail,
    )

    with pytest.raises(StorageMigrationError) as caught:
        storage_migration_module._copy_staged_file_durably(source_file, staged_file)

    assert caught.value.error_code == "target_flush_failed"
    assert staged_file.read_text(encoding="utf-8") == "late-winner"


@pytest.mark.unit
def test_staged_copy_rejects_fifo_without_blocking(tmp_path):
    from utils import storage_migration as storage_migration_module

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    source_fifo = tmp_path / "source.fifo"
    staged_file = tmp_path / "staged.bin"
    os.mkfifo(source_fifo)
    outcome = []

    def copy_fifo():
        try:
            storage_migration_module._copy_staged_file_durably(source_fifo, staged_file)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=copy_fifo, daemon=True)
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive(), "FIFO copy must fail instead of blocking maintenance"
    assert len(outcome) == 1
    assert isinstance(outcome[0], StorageMigrationError)
    assert outcome[0].error_code == "path_type_unsupported"
    assert not staged_file.exists()


@pytest.mark.unit
def test_staged_copy_does_not_block_when_source_becomes_fifo_before_open(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    if os.name == "nt" or not hasattr(os, "mkfifo"):
        pytest.skip("POSIX FIFO creation is unavailable")
    source_file = tmp_path / "source.bin"
    staged_file = tmp_path / "staged.bin"
    source_file.write_bytes(b"regular-before-open")
    real_open = storage_migration_module.os.open
    opened_flags = []
    replaced = False

    def replace_with_fifo_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if Path(path) == source_file and not replaced:
            replaced = True
            source_file.unlink()
            os.mkfifo(source_file)
            opened_flags.append(flags)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage_migration_module.os, "open", replace_with_fifo_before_open)
    outcome = []

    def copy_raced_source():
        try:
            storage_migration_module._copy_staged_file_durably(source_file, staged_file)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=copy_raced_source, daemon=True)
    worker.start()
    worker.join(timeout=1)
    if worker.is_alive():
        # Make a regressed blocking reader finish so it cannot leak into later tests.
        writer_fd = real_open(source_file, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer_fd)
        worker.join(timeout=1)

    assert not worker.is_alive(), "opening a raced FIFO must be non-blocking"
    assert len(outcome) == 1
    assert isinstance(outcome[0], StorageMigrationError)
    assert outcome[0].error_code == "path_type_unsupported"
    assert opened_flags and opened_flags[0] & os.O_NONBLOCK
    assert opened_flags[0] & os.O_NOFOLLOW
    assert not staged_file.exists()


@pytest.mark.unit
def test_snapshot_opens_regular_files_with_platform_safe_flags(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source.bin"
    source_file.write_bytes(b"snapshot")
    real_open = storage_migration_module.os.open
    opened_flags = []

    def record_open_flags(path, flags, mode=0o777, *, dir_fd=None):
        if Path(path) == source_file:
            opened_flags.append(flags)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage_migration_module.os, "open", record_open_flags)

    snapshot = storage_migration_module._snapshot_path(source_file)

    assert snapshot["kind"] == "file"
    assert snapshot["total_bytes"] == len(b"snapshot")
    assert len(opened_flags) == 1
    if os.name == "nt":
        assert opened_flags[0] & getattr(os, "O_BINARY", 0)
        assert opened_flags[0] & getattr(os, "O_NOINHERIT", 0)
    else:
        assert opened_flags[0] & os.O_NONBLOCK
        assert opened_flags[0] & os.O_NOFOLLOW


@pytest.mark.unit
def test_snapshot_rejects_different_opened_file_descriptor(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source.bin"
    other_file = tmp_path / "other.bin"
    source_file.write_bytes(b"source")
    other_file.write_bytes(b"other")
    real_open = storage_migration_module.os.open

    def substitute_opened_file(path, flags, mode=0o777, *, dir_fd=None):
        opened_path = other_file if Path(path) == source_file else path
        return real_open(opened_path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage_migration_module.os, "open", substitute_opened_file)

    with pytest.raises(StorageMigrationError) as exc_info:
        storage_migration_module._snapshot_path(source_file)

    assert exc_info.value.error_code == "source_changed_during_migration"


@pytest.mark.unit
@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "mkfifo"),
    reason="POSIX FIFO replacement is unavailable",
)
def test_snapshot_does_not_block_when_file_becomes_fifo_after_is_file(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source.bin"
    source_file.write_bytes(b"regular-before-hash")
    real_hash_file = storage_migration_module._hash_file
    real_open = storage_migration_module.os.open
    replaced = False

    def replace_with_fifo_before_hash(path):
        nonlocal replaced
        if Path(path) == source_file and not replaced:
            replaced = True
            source_file.unlink()
            os.mkfifo(source_file)
        return real_hash_file(path)

    monkeypatch.setattr(
        storage_migration_module,
        "_hash_file",
        replace_with_fifo_before_hash,
    )
    outcome = []

    def snapshot_raced_source():
        try:
            storage_migration_module._snapshot_path(source_file)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=snapshot_raced_source, daemon=True)
    worker.start()
    worker.join(timeout=1)
    if worker.is_alive():
        # Make a regressed Path.open reader finish so it cannot leak into later tests.
        writer_fd = real_open(source_file, os.O_WRONLY | os.O_NONBLOCK)
        os.close(writer_fd)
        worker.join(timeout=1)

    assert replaced
    assert not worker.is_alive(), "a FIFO swapped in after is_file must not block"
    assert len(outcome) == 1
    assert isinstance(outcome[0], StorageMigrationError)
    assert outcome[0].error_code == "path_type_unsupported"


@pytest.mark.unit
def test_staged_copy_rejects_source_symlink_without_copying_target(tmp_path):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source-link"
    external_file = tmp_path / "external.bin"
    staged_file = tmp_path / "staged.bin"
    external_file.write_bytes(b"external")
    try:
        source_file.symlink_to(external_file)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(StorageMigrationError) as exc_info:
        storage_migration_module._copy_staged_file_durably(source_file, staged_file)

    assert exc_info.value.error_code == "path_symlink_unsupported"
    assert not staged_file.exists()


@pytest.mark.unit
def test_staged_copy_exclusive_target_preserves_a_late_winner(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    source_file = tmp_path / "source.bin"
    staged_file = tmp_path / "staged.bin"
    source_file.write_bytes(b"source")
    real_open = storage_migration_module.os.open
    occupied = False
    target_flags_seen = []

    def occupy_target_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal occupied
        if (
            (Path(path) == staged_file or (dir_fd is not None and path == staged_file.name))
            and flags & os.O_CREAT
            and not occupied
        ):
            occupied = True
            target_flags_seen.append(flags)
            staged_file.write_bytes(b"late-winner")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage_migration_module.os, "open", occupy_target_before_open)

    with pytest.raises(StorageMigrationError) as exc_info:
        storage_migration_module._copy_staged_file_durably(source_file, staged_file)

    assert exc_info.value.error_code == "staging_entry_exists"
    assert target_flags_seen and target_flags_seen[0] & os.O_RDWR
    assert staged_file.read_bytes() == b"late-winner"


@pytest.mark.unit
def test_windows_fsync_staged_tree_does_not_reopen_durable_copies_read_only(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    staged_root = tmp_path / "transaction" / "staged"
    staged_root.mkdir(parents=True)
    staged_file = staged_root / "readonly.json"
    staged_file.write_text("{}", encoding="utf-8")
    os.chmod(staged_file, stat.S_IRUSR)
    flushed_directories = []

    monkeypatch.setattr(storage_migration_module.sys, "platform", "win32")
    monkeypatch.setattr(
        storage_migration_module.os,
        "fsync",
        lambda _fd: pytest.fail("Windows must not reopen a copied file read-only for fsync"),
    )
    monkeypatch.setattr(
        storage_migration_module,
        "fsync_directory_best_effort",
        lambda path: flushed_directories.append(Path(path)),
    )

    storage_migration_module._fsync_staged_tree(staged_root)

    assert flushed_directories == [staged_root]


@pytest.mark.unit
def test_run_pending_storage_migration_flushes_staged_root_before_verifying_checkpoint(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "state" / "game_scores" / "scores.db"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"score")
    events = []
    real_persist = storage_migration_module._persist_migration_payload

    def record_staged_flush(path, **kwargs):
        events.append(("flush", Path(path), kwargs.get("root_fd")))

    def record_persist(*args, **kwargs):
        events.append(("checkpoint", kwargs.get("status")))
        return real_persist(*args, **kwargs)

    monkeypatch.setattr(storage_migration_module, "_fsync_staged_tree", record_staged_flush)
    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", record_persist)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    flush_events = [event for event in events if event[0] == "flush"]
    assert len(flush_events) == 1
    assert flush_events[0][1].name == "staged"
    if os.name == "nt":
        assert flush_events[0][2] is None
    else:
        assert flush_events[0][2] is not None
    assert events.index(flush_events[0]) < events.index(
        ("checkpoint", storage_migration_module.STORAGE_MIGRATION_STATUS_VERIFYING)
    )


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync semantics")
def test_posix_directory_flush_failure_never_reaches_verifying_or_publishing(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "state" / "game_scores" / "scores.db"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"source-score")
    persisted_statuses = []
    real_fsync = storage_migration_module.os.fsync
    real_persist = storage_migration_module._persist_migration_payload

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory flush failed")
        return real_fsync(fd)

    def record_persist(*args, **kwargs):
        if kwargs.get("status"):
            persisted_statuses.append(kwargs["status"])
        return real_persist(*args, **kwargs)

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    monkeypatch.setattr(storage_migration_module.os, "fsync", fail_directory_fsync)
    monkeypatch.setattr(storage_migration_module, "_persist_migration_payload", record_persist)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_flush_failed"
    assert storage_migration_module.STORAGE_MIGRATION_STATUS_VERIFYING not in persisted_statuses
    assert storage_migration_module.STORAGE_MIGRATION_STATUS_PUBLISHING not in persisted_statuses
    assert source_file.read_bytes() == b"source-score"
    assert not (target_root / "state").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rechecks_space_with_safety_margin(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"source")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    monkeypatch.setattr(
        storage_migration_module.shutil,
        "disk_usage",
        lambda _path: type("DiskUsage", (), {"free": source_file.stat().st_size})(),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "insufficient_space"
    assert not (target_root / "config").exists()


@pytest.mark.unit
def test_empty_source_still_reserves_transaction_metadata_space(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_root.mkdir(parents=True, exist_ok=True)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_filesystem_allocation_unit",
        lambda _path: 4096,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_filesystem_free_entry_count",
        lambda _path: None,
    )
    monkeypatch.setattr(
        storage_migration_module.shutil,
        "disk_usage",
        lambda _path: type(
            "DiskUsage",
            (),
            {"free": 64 * 1024 * 1024 + 8 * 4096 - 1},
        )(),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "insufficient_space"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX free-inode capacity contract")
def test_empty_source_still_reserves_transaction_metadata_entries(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_root.mkdir(parents=True, exist_ok=True)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_filesystem_allocation_unit",
        lambda _path: 4096,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_filesystem_free_entry_count",
        lambda _path: 7,
    )
    monkeypatch.setattr(
        storage_migration_module.shutil,
        "disk_usage",
        lambda _path: type("DiskUsage", (), {"free": 1024 * 1024 * 1024})(),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "insufficient_space"


@pytest.mark.unit
def test_run_pending_storage_migration_reserves_space_for_zero_byte_entries(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    empty_file = source_root / "config" / "empty.json"
    empty_file.parent.mkdir(parents=True, exist_ok=True)
    empty_file.touch()
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_filesystem_allocation_unit",
        lambda _path: 4096,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_filesystem_free_entry_count",
        lambda _path: None,
    )
    monkeypatch.setattr(
        storage_migration_module.shutil,
        "disk_usage",
        lambda _path: type("DiskUsage", (), {"free": 64 * 1024 * 1024 + 8191})(),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "insufficient_space"
    assert not (target_root / "config").exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX free-inode capacity contract")
def test_run_pending_storage_migration_rejects_insufficient_target_entries(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    empty_file = source_root / "config" / "empty.json"
    empty_file.parent.mkdir(parents=True, exist_ok=True)
    empty_file.touch()
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_filesystem_allocation_unit",
        lambda _path: 4096,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_filesystem_free_entry_count",
        lambda _path: 1,
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "insufficient_space"
    assert not (target_root / "config").exists()


@pytest.mark.unit
def test_unexpected_failure_keeps_persisted_error_message_within_anchor_limit(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("source", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    huge_error = "copy failed: " + "x" * (5 * 1024 * 1024)

    def fail_copy(*_args, **_kwargs):
        raise OSError(huge_error)

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", fail_copy)
    monkeypatch.setattr(storage_migration_module.logger, "exception", lambda *_args, **_kwargs: None)

    result = run_pending_storage_migration(config_manager)
    reloaded = load_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "storage_migration_unexpected"
    assert result["error_message"] == reloaded["error_message"]
    assert len(reloaded["error_message"].encode("utf-8")) <= 16 * 1024
    assert reloaded["error_message"].endswith("…[truncated]")


@pytest.mark.unit
def test_unexpected_failure_escapes_surrogate_filename_before_checkpoint(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("source", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    surrogate_path = "config/invalid\udcff.json"

    def fail_copy(*_args, **_kwargs):
        raise OSError(f"copy failed: {surrogate_path}")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", fail_copy)
    monkeypatch.setattr(storage_migration_module.logger, "exception", lambda *_args, **_kwargs: None)

    result = run_pending_storage_migration(config_manager)
    reloaded = load_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "storage_migration_unexpected"
    assert result["error_message"] == reloaded["error_message"]
    assert "\\udcff" in result["error_message"]
    result["error_message"].encode("utf-8")


@pytest.mark.unit
def test_run_pending_storage_migration_detects_same_size_copy_corruption(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "memory" / "history.bin"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"AAAA")
    original_copy = storage_migration_module._copy_runtime_entry

    def corrupt_copy(source_path, target_path, **kwargs):
        original_copy(source_path, target_path, **kwargs)
        (target_path / "history.bin").write_bytes(b"BBBB")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", corrupt_copy)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "verification_failed"
    assert not (target_root / "memory").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_source_change_after_entry_copy(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "memory" / "history.bin"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"BEFORE")

    def mutate_source_after_copy(**_kwargs):
        source_file.write_bytes(b"AFTER")

    monkeypatch.setattr(
        storage_migration_module,
        "_rewrite_migrated_runtime_config_paths",
        mutate_source_after_copy,
    )
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "source_changed_during_migration"
    assert not (target_root / "memory").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_nested_source_symlink(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external_root = tmp_path / "external"
    external_root.mkdir()
    (external_root / "secret.txt").write_text("outside", encoding="utf-8")
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    try:
        (source_root / "config" / "linked").symlink_to(external_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "path_symlink_unsupported"
    assert not (target_root / "config" / "linked" / "secret.txt").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_source_state_symlink_with_missing_leaf(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external_state = tmp_path / "external-source-state"
    external_state.mkdir()
    external_marker = external_state / "keep.txt"
    external_marker.write_text("KEEP", encoding="utf-8")
    source_state = source_root / "state"
    source_root.mkdir(parents=True, exist_ok=True)
    if source_state.exists():
        shutil.rmtree(source_state)
    try:
        source_state.symlink_to(external_state, target_is_directory=True)
    except NotImplementedError:
        pytest.skip("symbolic links are unavailable on this platform")
    except OSError as exc:
        if exc.errno in {
            errno.EACCES,
            errno.EPERM,
            getattr(errno, "ENOTSUP", errno.EPERM),
        }:
            pytest.skip("symbolic links are unavailable on this platform")
        raise

    with pytest.raises(StorageMigrationError) as caught:
        create_pending_storage_migration(
            config_manager,
            source_root=source_root,
            target_root=target_root,
            selection_source="custom",
        )

    assert caught.value.error_code == "runtime_entry_path_unsafe"
    assert external_marker.read_text(encoding="utf-8") == "KEEP"
    assert not (external_state / "game_scores").exists()
    assert not (target_root / "state" / "game_scores").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_target_state_symlink_before_missing_leaf_publish(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external_state = tmp_path / "external-target-state"
    source_score = source_root / "state" / "game_scores" / "score.db"
    source_score.parent.mkdir(parents=True)
    source_score.write_bytes(b"SOURCE")
    external_state.mkdir()
    external_marker = external_state / "keep.txt"
    external_marker.write_text("KEEP", encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    target_root.mkdir(parents=True)
    try:
        (target_root / "state").symlink_to(external_state, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "runtime_entry_path_unsafe"
    assert external_marker.read_text(encoding="utf-8") == "KEEP"
    assert not (external_state / "game_scores").exists()


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_target_replaced_by_symlink(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external_root = tmp_path / "external-target"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    external_root.mkdir()
    target_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_root.symlink_to(external_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_path_symlink_unsupported"
    assert list(external_root.iterdir()) == []


@pytest.mark.unit
def test_run_pending_storage_migration_does_not_follow_symlinked_transaction_root(tmp_path):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    external_root = tmp_path / "external-transaction"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    external_root.mkdir()
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    transaction_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        transaction_root.symlink_to(external_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this platform")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_symlink_unsupported"
    assert transaction_root.is_symlink()
    assert list(external_root.iterdir()) == []


@pytest.mark.unit
def test_transaction_staging_lives_on_selected_target_filesystem(tmp_path):
    from utils import storage_migration as storage_migration_module

    target_root = tmp_path / "mounted-volume" / "N.E.K.O"
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        "a" * 32,
    )

    assert transaction_root.parent == target_root


@pytest.mark.unit
def test_pending_migration_never_deletes_unowned_transaction_path(tmp_path):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    transaction_root.mkdir(parents=True)
    sentinel = transaction_root / "user-content.txt"
    sentinel.write_text("KEEP", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_occupied"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
def test_checkpoint_bound_transaction_path_without_marker_preserves_late_occupant(
    tmp_path,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    payload["transaction_root"] = str(transaction_root)
    storage_migration_module.save_storage_migration(config_manager, payload)

    transaction_root.mkdir(parents=True)
    sentinel = transaction_root / "third-party.txt"
    sentinel.write_text("KEEP", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_occupied"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
def test_completed_checkpoint_preserves_unmarked_transaction_path(tmp_path):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    transaction_root.mkdir(parents=True)
    sentinel = transaction_root / "third-party.txt"
    sentinel.write_text("KEEP", encoding="utf-8")
    payload.update(
        {
            "status": "completed",
            "transaction_root": str(transaction_root),
        }
    )
    storage_migration_module.save_storage_migration(config_manager, payload)

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is False
    assert sentinel.read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
@pytest.mark.parametrize("marker_state", ("mismatch", "symlink"))
def test_checkpoint_bound_transaction_rejects_untrusted_marker(
    tmp_path,
    marker_state,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    transaction_root.mkdir(parents=True)
    marker = transaction_root / storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
    if marker_state == "mismatch":
        marker.write_text(
            json.dumps(
                {
                    "version": 1,
                    "txid": payload["txid"],
                    "owner_token": "0" * 64,
                }
            ),
            encoding="utf-8",
        )
    else:
        external = tmp_path / "external-marker.json"
        external.write_text("{}", encoding="utf-8")
        try:
            marker.symlink_to(external)
        except (OSError, NotImplementedError):
            pytest.skip("marker symlinks are unavailable")
    sentinel = transaction_root / "third-party.txt"
    sentinel.write_text("KEEP", encoding="utf-8")
    payload["transaction_root"] = str(transaction_root)
    storage_migration_module.save_storage_migration(config_manager, payload)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_occupied"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
def test_transaction_marker_write_failure_never_publishes_unowned_root(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    if os.name == "nt":
        monkeypatch.setattr(
            storage_migration_module,
            "_write_transaction_owner_marker",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker denied")),
        )
    else:
        monkeypatch.setattr(
            storage_migration_module.os,
            "write",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker denied")),
        )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "storage_migration_unexpected"
    assert not transaction_root.exists()
    assert source_file.read_text(encoding="utf-8") == "SOURCE"
    assert not (target_root / "config").exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX prepared-directory binding")
def test_posix_prepared_transaction_replacement_with_unknown_content_is_preserved(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        pending["txid"],
    )
    real_open_child = storage_migration_module._open_or_create_posix_child_directory
    injected: dict[str, Path] = {}

    def _replace_prepared_before_open(parent_fd, name, display_path, **kwargs):
        display_path = Path(display_path)
        if (
            not injected
            and display_path.parent == target_root
            and name.startswith(f".{transaction_root.name}.")
            and name.endswith(".tmp")
        ):
            created_aside = display_path.with_name(f"{display_path.name}.created-aside")
            display_path.rename(created_aside)
            display_path.mkdir()
            sentinel = display_path / "third-party.txt"
            sentinel.write_text("KEEP", encoding="utf-8")
            injected.update(
                prepared=display_path,
                aside=created_aside,
                sentinel=sentinel,
            )
        return real_open_child(parent_fd, name, display_path, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_open_or_create_posix_child_directory",
        _replace_prepared_before_open,
    )

    result = run_pending_storage_migration(config_manager)

    assert injected
    assert result["completed"] is False
    assert result["error_code"] == "transaction_ownership_changed"
    assert source_file.read_text(encoding="utf-8") == "SOURCE"
    assert injected["sentinel"].read_text(encoding="utf-8") == "KEEP"
    assert injected["aside"].is_dir()
    assert not transaction_root.exists()


@pytest.mark.unit
def test_windows_reparse_metadata_is_never_treated_as_owned_file():
    from utils import storage_migration as storage_migration_module

    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
    )

    assert storage_migration_module._is_link_like_metadata(metadata) is True


@pytest.mark.unit
def test_windows_open_file_metadata_never_combines_normal_with_readonly():
    from utils import storage_migration as storage_migration_module

    readonly = 0x00000001
    normal = 0x00000080
    archive = 0x00000020

    assert storage_migration_module._windows_copied_file_attributes(normal, readonly) == readonly
    assert storage_migration_module._windows_copied_file_attributes(normal, 0) == normal
    assert storage_migration_module._windows_copied_file_attributes(
        archive | normal,
        readonly,
    ) == archive | readonly


@pytest.mark.unit
def test_pending_migration_recovers_transaction_created_before_copying_checkpoint(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    class SimulatedProcessLoss(BaseException):
        pass

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    original_persist = storage_migration_module._persist_migration_payload
    interrupted = {"value": False}

    def interrupt_before_copying_checkpoint(*args, **kwargs):
        if (
            kwargs.get("status") == storage_migration_module.STORAGE_MIGRATION_STATUS_COPYING
            and not interrupted["value"]
        ):
            interrupted["value"] = True
            raise SimulatedProcessLoss
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_persist_migration_payload",
        interrupt_before_copying_checkpoint,
    )
    with pytest.raises(SimulatedProcessLoss):
        run_pending_storage_migration(config_manager)

    crashed_checkpoint = load_storage_migration(config_manager)
    assert crashed_checkpoint["status"] == "preflight"
    assert crashed_checkpoint["transaction_root"] == str(transaction_root)
    assert transaction_root.is_dir()

    monkeypatch.setattr(
        storage_migration_module,
        "_persist_migration_payload",
        original_persist,
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    assert (target_root / "config" / "characters.json").read_text(encoding="utf-8") == "SOURCE"
    assert not transaction_root.exists()


@pytest.mark.unit
def test_run_pending_storage_migration_restores_existing_target_when_commit_fails(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    (target_root / "config").mkdir(parents=True, exist_ok=True)
    target_file = target_root / "config" / "characters.json"
    target_file.write_text("TARGET", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )

    monkeypatch.setattr(
        storage_migration_module,
        "save_storage_policy",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("commit failed")),
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "storage_migration_unexpected"
    assert target_file.read_text(encoding="utf-8") == "TARGET"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX mount descriptor contract")
@pytest.mark.parametrize("mounted_side", ("source", "target"))
def test_migration_rejects_nested_mount_before_transaction_or_publish(
    tmp_path,
    monkeypatch,
    mounted_side,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "source.json"
    target_file = target_root / "config" / "target.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    target_file.write_text("TARGET", encoding="utf-8")
    mounted_root = (source_root if mounted_side == "source" else target_root) / "config" / "mounted"
    mounted_root.mkdir()
    mounted_file = mounted_root / "external.json"
    mounted_file.write_text("EXTERNAL", encoding="utf-8")

    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        pending["txid"],
    )
    mounted_inode = mounted_root.stat().st_ino

    def simulated_mount_identity(fd):
        identity = os.fstat(fd)
        return "test-mount", 2 if identity.st_ino == mounted_inode else 1

    monkeypatch.setattr(
        storage_migration_module,
        "_opened_mount_identity",
        simulated_mount_identity,
    )
    mutation_calls = []
    monkeypatch.setattr(
        storage_migration_module,
        "_create_owned_transaction_root",
        lambda *args, **kwargs: mutation_calls.append("transaction"),
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_durable_replace",
        lambda *args, **kwargs: mutation_calls.append("replace"),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "nested_mount_unsupported"
    assert mutation_calls == []
    assert not transaction_root.exists()
    assert source_file.read_text(encoding="utf-8") == "SOURCE"
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert mounted_file.read_text(encoding="utf-8") == "EXTERNAL"


@pytest.mark.unit
def test_launcher_rechecks_mount_boundaries_before_creating_transaction(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "source.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        pending["txid"],
    )

    monkeypatch.setattr(
        storage_migration_module,
        "validate_storage_migration_preflight_boundaries",
        lambda *_args: (_ for _ in ()).throw(
            StorageMigrationError(
                "nested_mount_unsupported",
                "runtime entry contains nested mount",
            )
        ),
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_create_owned_transaction_root",
        lambda *_args, **_kwargs: pytest.fail(
            "launcher boundary failure must precede transaction creation"
        ),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "nested_mount_unsupported"
    assert not transaction_root.exists()
    assert source_file.read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
def test_recovery_metadata_write_failures_keep_transaction_and_force_source_layout(
    tmp_path,
    monkeypatch,
):
    from utils import cloudsave_runtime as cloudsave_runtime_module
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    (target_root / "config").mkdir(parents=True, exist_ok=True)
    target_file = target_root / "config" / "characters.json"
    target_file.write_text("TARGET", encoding="utf-8")
    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        pending["txid"],
    )

    real_save_policy = storage_migration_module.save_storage_policy
    policy_calls = 0

    def fail_recovery_policy(*args, **kwargs):
        nonlocal policy_calls
        policy_calls += 1
        if policy_calls == 1:
            return real_save_policy(*args, **kwargs)
        raise OSError("source policy unavailable")

    real_save_migration = storage_migration_module.save_storage_migration

    def fail_terminal_checkpoint(manager, payload, **kwargs):
        if str(payload.get("status") or "") in {
            "failed",
            "recovery_required",
            "rollback_required",
        }:
            raise OSError("terminal checkpoint unavailable")
        return real_save_migration(manager, payload, **kwargs)

    monkeypatch.setattr(storage_migration_module, "save_storage_policy", fail_recovery_policy)
    monkeypatch.setattr(storage_migration_module, "save_storage_migration", fail_terminal_checkpoint)
    monkeypatch.setattr(
        cloudsave_runtime_module,
        "set_root_mode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("root state unavailable")),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["force_recovery_layout"] is True
    assert result["recovery_policy_persisted"] is False
    assert result["recovery_root_state_persisted"] is False
    assert result["recovery_checkpoint_persisted"] is False
    assert result["source_root"] == str(source_root.resolve())
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert transaction_root.exists(), "uncommitted recovery evidence must survive"
    assert load_storage_migration(config_manager)["status"] == "committing"
    assert load_storage_policy(config_manager)["selected_root"] == str(target_root.resolve())


@pytest.mark.unit
def test_run_pending_storage_migration_rejects_target_changed_after_confirmation(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    (target_root / "jukebox").mkdir(parents=True, exist_ok=True)
    target_file = target_root / "jukebox" / "library.json"
    target_file.write_text("BEFORE", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )
    target_file.write_text("AFTER", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_changed_since_confirmation"
    assert target_file.read_text(encoding="utf-8") == "AFTER"


@pytest.mark.unit
def test_publish_preserves_target_created_after_final_snapshot(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    concurrent_file = target_root / "config" / "external.json"
    if os.name != "nt":
        real_snapshot_entries = storage_migration_module._snapshot_posix_runtime_entries

        def _snapshot_then_create_target(roots, root):
            snapshot = real_snapshot_entries(roots, root)
            concurrent_file.parent.mkdir(parents=True, exist_ok=True)
            concurrent_file.write_text("EXTERNAL", encoding="utf-8")
            return snapshot

        monkeypatch.setattr(
            storage_migration_module,
            "_snapshot_posix_runtime_entries",
            _snapshot_then_create_target,
        )
    else:
        real_snapshot_entries = storage_migration_module._snapshot_runtime_entries
        target_snapshot_calls = 0

        def _snapshot_then_create_target(root, **kwargs):
            nonlocal target_snapshot_calls
            is_target_snapshot = Path(root) == target_root.resolve()
            if is_target_snapshot:
                target_snapshot_calls += 1
                if target_snapshot_calls == 2:
                    with pytest.raises(OSError):
                        target_root.rename(target_root.with_name("N.E.K.O-moved"))
            snapshot = real_snapshot_entries(root, **kwargs)
            if is_target_snapshot and target_snapshot_calls == 2:
                concurrent_file.parent.mkdir(parents=True, exist_ok=True)
                concurrent_file.write_text("EXTERNAL", encoding="utf-8")
            return snapshot

        monkeypatch.setattr(
            storage_migration_module,
            "_snapshot_runtime_entries",
            _snapshot_then_create_target,
        )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert concurrent_file.read_text(encoding="utf-8") == "EXTERNAL"
    assert not (target_root / "config" / "characters.json").exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX target-root identity binding")
def test_posix_target_root_replaced_between_writable_probe_and_open_is_rejected(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    moved_target = tmp_path / "moved-probed-target"
    sentinel = target_root / "external-sentinel.txt"
    real_open_directory = storage_migration_module._open_verified_directory
    replaced = False

    def _replace_target_before_open(path):
        nonlocal replaced
        if Path(path) == target_root and not replaced:
            target_root.rename(moved_target)
            target_root.mkdir()
            sentinel.write_text("KEEP", encoding="utf-8")
            replaced = True
        return real_open_directory(path)

    monkeypatch.setattr(
        storage_migration_module,
        "_open_verified_directory",
        _replace_target_before_open,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_create_owned_posix_transaction_root_at",
        lambda *_args, **_kwargs: pytest.fail(
            "replaced target root must be rejected before transaction creation"
        ),
    )

    result = run_pending_storage_migration(config_manager)

    assert replaced is True
    assert result["completed"] is False
    assert result["error_code"] == "migration_path_changed"
    assert source_file.read_text(encoding="utf-8") == "SOURCE"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"
    assert moved_target.is_dir()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX target-root identity binding")
def test_posix_target_root_replaced_after_pinned_baseline_never_stages_data(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    moved_target = tmp_path / "moved-original-target"
    sentinel = target_root / "external-sentinel.txt"
    real_has_user_content = storage_migration_module._root_has_user_content
    replaced = False

    def _replace_target_before_content_check(root, **kwargs):
        nonlocal replaced
        target_root.rename(moved_target)
        target_root.mkdir()
        sentinel.write_text("KEEP", encoding="utf-8")
        replaced = True
        return real_has_user_content(root, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_root_has_user_content",
        _replace_target_before_content_check,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_create_owned_transaction_root",
        lambda *_args, **_kwargs: pytest.fail(
            "replaced target root must be rejected before transaction creation"
        ),
    )

    result = run_pending_storage_migration(config_manager)

    assert replaced is True
    assert result["completed"] is False
    assert result["error_code"] == "migration_path_changed"
    assert source_file.read_text(encoding="utf-8") == "SOURCE"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"
    assert moved_target.is_dir()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX target-root identity binding")
def test_posix_transaction_creation_stays_on_pinned_target_after_path_replacement(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    moved_target = tmp_path / "moved-pinned-target"
    sentinel = target_root / "external-sentinel.txt"
    real_create = storage_migration_module._create_owned_posix_transaction_root_at
    replaced = False

    def _replace_named_target_then_create(*args, **kwargs):
        nonlocal replaced
        target_root.rename(moved_target)
        target_root.mkdir()
        sentinel.write_text("KEEP", encoding="utf-8")
        replaced = True
        return real_create(*args, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_create_owned_posix_transaction_root_at",
        _replace_named_target_then_create,
    )

    result = run_pending_storage_migration(config_manager)

    assert replaced is True
    assert result["completed"] is False
    assert source_file.read_text(encoding="utf-8") == "SOURCE"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"
    assert not (target_root / "config").exists()
    assert (
        moved_target
        / storage_migration_module._transaction_root_for(
            target_root,
            pending["txid"],
        ).name
    ).is_dir()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX target-root identity binding")
def test_posix_publish_rejects_target_root_replaced_before_descriptor_pin(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        pending["txid"],
    )
    moved_target = tmp_path / "moved-original-target"
    sentinel = target_root / "external-sentinel.txt"
    real_open_publish_roots = storage_migration_module._open_posix_publish_roots
    replaced = False

    def _replace_target_then_open(*args, **kwargs):
        nonlocal replaced
        target_root.rename(moved_target)
        target_root.mkdir()
        (moved_target / transaction_root.name).rename(transaction_root)
        sentinel.write_text("KEEP", encoding="utf-8")
        replaced = True
        return real_open_publish_roots(*args, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_open_posix_publish_roots",
        _replace_target_then_open,
    )

    result = run_pending_storage_migration(config_manager)

    assert replaced is True
    assert result["completed"] is False
    assert result["error_code"] == "migration_path_changed"
    assert source_file.read_text(encoding="utf-8") == "SOURCE"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"
    assert not (target_root / "config" / "characters.json").exists()
    assert moved_target.is_dir()


@pytest.mark.unit
def test_jukebox_only_target_requires_overwrite_confirmation(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "jukebox").mkdir(parents=True, exist_ok=True)
    (source_root / "jukebox" / "library.json").write_text("SOURCE", encoding="utf-8")
    target_file = target_root / "jukebox" / "library.json"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("TARGET", encoding="utf-8")
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "target_confirmation_required"
    assert target_file.read_text(encoding="utf-8") == "TARGET"


@pytest.mark.unit
def test_selection_source_recovered_cannot_adopt_target_instead_of_copying(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    (source_root / "config").mkdir(parents=True, exist_ok=True)
    (source_root / "config" / "characters.json").write_text("SOURCE", encoding="utf-8")
    (target_root / "config").mkdir(parents=True, exist_ok=True)
    target_file = target_root / "config" / "characters.json"
    target_file.write_text("TARGET", encoding="utf-8")

    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="recovered",
        confirmed_existing_target_content=True,
    )
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    assert target_file.read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
def test_interrupted_publish_is_rolled_back_before_retry(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    target_file = target_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    target_file.write_text("TARGET", encoding="utf-8")
    source_runtime_baseline = storage_migration_module._snapshot_runtime_entries(
        source_root
    )
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )

    transaction_root = storage_migration_module._transaction_root_for(target_root, payload["txid"])
    staged_file = transaction_root / "staged" / "config" / "characters.json"
    backup_entry = transaction_root / "backup" / "config"
    staged_file.parent.mkdir(parents=True)
    backup_entry.parent.mkdir(parents=True)
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        payload["txid"],
    )
    shutil.copy2(source_file, staged_file)
    os.replace(target_root / "config", backup_entry)
    os.replace(transaction_root / "staged" / "config", target_root / "config")
    payload.update(
        {
            "status": "publishing",
            "original_target_entries": ["config"],
            "publish_entry_names": ["config"],
            "publish_entry_snapshots": {
                "config": storage_migration_module._snapshot_path(target_root / "config"),
            },
            "source_runtime_baseline": source_runtime_baseline,
            "transaction_root": str(transaction_root),
        }
    )
    storage_migration_module.save_storage_migration(config_manager, payload)

    def fail_retry_copy(*args, **kwargs):
        raise StorageMigrationError("copy_failed", "stop after crash rollback")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", fail_retry_copy)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "copy_failed"
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not transaction_root.exists()


def _prepare_interrupted_publish(tmp_path, *, legacy_transaction_layout=False):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    target_file = target_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    target_file.write_text("TARGET", encoding="utf-8")
    source_runtime_baseline = storage_migration_module._snapshot_runtime_entries(
        source_root
    )
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )
    transaction_root = (
        storage_migration_module._legacy_transaction_root_for(target_root, payload["txid"])
        if legacy_transaction_layout
        else storage_migration_module._transaction_root_for(target_root, payload["txid"])
    )
    staged_file = transaction_root / "staged" / "config" / "characters.json"
    backup_entry = transaction_root / "backup" / "config"
    staged_file.parent.mkdir(parents=True)
    backup_entry.parent.mkdir(parents=True)
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        payload["txid"],
    )
    shutil.copy2(source_file, staged_file)
    os.replace(target_root / "config", backup_entry)
    os.replace(transaction_root / "staged" / "config", target_root / "config")
    payload.update(
        {
            "status": "publishing",
            "original_target_entries": ["config"],
            "publish_entry_names": ["config"],
            "publish_entry_snapshots": {
                "config": storage_migration_module._snapshot_path(target_root / "config"),
            },
            "source_runtime_baseline": source_runtime_baseline,
            "transaction_root": str(transaction_root),
        }
    )
    storage_migration_module.save_storage_migration(config_manager, payload)
    return config_manager, target_root, target_file, transaction_root, backup_entry, payload


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows recovery directory guards")
def test_windows_interrupted_publish_holds_transaction_guard_through_rollback(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager, _, _, transaction_root, _, _ = _prepare_interrupted_publish(
        tmp_path
    )
    moved_transaction = tmp_path / "moved-transaction"
    real_rollback = storage_migration_module._rollback_published_entries
    rename_blocked = False

    def rollback_while_transaction_is_guarded(*args, **kwargs):
        nonlocal rename_blocked
        try:
            transaction_root.rename(moved_transaction)
        except OSError:
            rename_blocked = True
        else:
            pytest.fail("Windows recovery must deny transaction-root rename")
        return real_rollback(*args, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_rollback_published_entries",
        rollback_while_transaction_is_guarded,
    )

    run_pending_storage_migration(config_manager)

    assert rename_blocked is True


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX mount-table contract")
def test_interrupted_publish_rejects_transaction_mount_before_rollback_scan(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager, _, _, transaction_root, _, _ = _prepare_interrupted_publish(
        tmp_path
    )
    monkeypatch.setattr(
        storage_migration_module,
        "validate_storage_migration_preflight_boundaries",
        lambda *_args: None,
    )

    def reject_transaction_mount(path):
        assert path == transaction_root
        raise StorageMigrationError(
            "nested_mount_unsupported",
            "transaction contains nested mount",
        )

    monkeypatch.setattr(
        storage_migration_module,
        "_preflight_named_mounts_below",
        reject_transaction_mount,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_rollback_published_entries",
        lambda *_args, **_kwargs: pytest.fail(
            "known transaction mount must be rejected before rollback traversal"
        ),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "nested_mount_unsupported"
    assert result["payload"]["status"] == "rollback_required"
    assert transaction_root.is_dir()


@pytest.mark.unit
@pytest.mark.parametrize("marker_state", ("missing", "mismatch", "symlink"))
def test_interrupted_publish_with_untrusted_marker_stays_rollback_required(
    tmp_path,
    marker_state,
):
    from utils import storage_migration as storage_migration_module

    config_manager, _, target_file, transaction_root, backup_entry, payload = (
        _prepare_interrupted_publish(tmp_path)
    )
    marker = transaction_root / storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
    if marker_state == "missing":
        marker.unlink()
    elif marker_state == "mismatch":
        marker.write_text(
            json.dumps(
                {
                    "version": 1,
                    "txid": payload["txid"],
                    "owner_token": "0" * 64,
                }
            ),
            encoding="utf-8",
        )
    else:
        marker.unlink()
        external = tmp_path / "external-owner-marker.json"
        external.write_text("{}", encoding="utf-8")
        try:
            marker.symlink_to(external)
        except (OSError, NotImplementedError):
            pytest.skip("marker symlinks are unavailable")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "transaction_path_occupied"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"
    assert (backup_entry / "characters.json").read_text(encoding="utf-8") == "TARGET"
    assert transaction_root.exists()


@pytest.mark.unit
@pytest.mark.parametrize("crash_step", ("published_to_staged", "backup_to_target"))
def test_interrupted_publish_rollback_rename_steps_are_crash_recoverable(
    tmp_path,
    monkeypatch,
    crash_step,
):
    from utils import storage_migration as storage_migration_module

    class SimulatedProcessLoss(BaseException):
        pass

    config_manager, target_root, target_file, transaction_root, backup_entry, _ = (
        _prepare_interrupted_publish(tmp_path)
    )
    staged_entry = transaction_root / "staged" / "config"
    interrupted = {"value": False}
    if os.name == "nt":
        original_publish = storage_migration_module._durable_publish_without_replacing

        def _interrupt_rollback_rename(source, target):
            original_publish(source, target)
            is_selected_step = (
                crash_step == "published_to_staged"
                and source == target_root / "config"
                and target == staged_entry
            ) or (
                crash_step == "backup_to_target"
                and source == backup_entry
                and target == target_root / "config"
            )
            if is_selected_step and not interrupted["value"]:
                interrupted["value"] = True
                raise SimulatedProcessLoss

        publish_helper_name = "_durable_publish_without_replacing"
    else:
        original_publish = storage_migration_module._durable_publish_without_replacing_at

        def _interrupt_rollback_rename(
            source_parent_fd,
            source_name,
            source_parent_display,
            target_parent_fd,
            target_name,
            target_parent_display,
        ):
            original_publish(
                source_parent_fd,
                source_name,
                source_parent_display,
                target_parent_fd,
                target_name,
                target_parent_display,
            )
            is_selected_step = (
                crash_step == "published_to_staged"
                and source_parent_display == target_root
                and source_name == "config"
                and target_parent_display == staged_entry.parent
                and target_name == "config"
            ) or (
                crash_step == "backup_to_target"
                and source_parent_display == backup_entry.parent
                and source_name == "config"
                and target_parent_display == target_root
                and target_name == "config"
            )
            if is_selected_step and not interrupted["value"]:
                interrupted["value"] = True
                raise SimulatedProcessLoss

        publish_helper_name = "_durable_publish_without_replacing_at"

    monkeypatch.setattr(
        storage_migration_module,
        publish_helper_name,
        _interrupt_rollback_rename,
    )
    with pytest.raises(SimulatedProcessLoss):
        run_pending_storage_migration(config_manager)

    assert load_storage_migration(config_manager)["status"] == "publishing"
    assert staged_entry.exists()
    if crash_step == "published_to_staged":
        assert not target_file.exists()
        assert (backup_entry / "characters.json").read_text(encoding="utf-8") == "TARGET"
    else:
        assert target_file.read_text(encoding="utf-8") == "TARGET"
        assert not backup_entry.exists()

    monkeypatch.setattr(
        storage_migration_module,
        publish_helper_name,
        original_publish,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_copy_runtime_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            StorageMigrationError("copy_failed", "stop after rollback")
        ),
    )
    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "copy_failed"
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not transaction_root.exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX publication durability")
def test_posix_publish_flushes_new_nested_parent_names_before_entry_rename(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    score_file = source_root / "state" / "game_scores" / "score.json"
    score_file.parent.mkdir(parents=True)
    score_file.write_text("SOURCE", encoding="utf-8")
    pending = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        pending["txid"],
    )
    backup_root = transaction_root / "backup"
    flushed_parents: list[Path] = []
    real_flush = storage_migration_module._fsync_opened_migration_directory
    real_publish = storage_migration_module._durable_publish_without_replacing_at
    checked = False

    def _record_flush(fd, display_path):
        real_flush(fd, display_path)
        flushed_parents.append(Path(display_path))

    def _assert_parent_names_are_durable(
        source_parent_fd,
        source_name,
        source_parent_display,
        target_parent_fd,
        target_name,
        target_parent_display,
    ):
        nonlocal checked
        if target_name == "game_scores" and Path(target_parent_display) == target_root / "state":
            assert target_root in flushed_parents
            assert backup_root in flushed_parents
            checked = True
        return real_publish(
            source_parent_fd,
            source_name,
            source_parent_display,
            target_parent_fd,
            target_name,
            target_parent_display,
        )

    monkeypatch.setattr(
        storage_migration_module,
        "_fsync_opened_migration_directory",
        _record_flush,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_durable_publish_without_replacing_at",
        _assert_parent_names_are_durable,
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    assert checked is True
    assert (target_root / "state" / "game_scores" / "score.json").read_text(
        encoding="utf-8"
    ) == "SOURCE"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-bound publication")
@pytest.mark.parametrize("replace_after", ("target_to_backup", "staged_to_target"))
def test_posix_publish_root_replacement_rolls_back_without_external_writes(
    tmp_path,
    monkeypatch,
    replace_after,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_file = source_root / "config" / "characters.json"
    target_file = target_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    target_file.write_text("TARGET", encoding="utf-8")
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
        confirmed_existing_target_content=True,
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    moved_transaction = target_root / "detached-transaction"
    external = tmp_path / "external-transaction"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("KEEP", encoding="utf-8")
    original_publish = storage_migration_module._durable_publish_without_replacing_at
    replaced = False

    def replace_transaction_name_after_rename(
        source_parent_fd,
        source_name,
        source_parent_display,
        target_parent_fd,
        target_name,
        target_parent_display,
    ):
        nonlocal replaced
        original_publish(
            source_parent_fd,
            source_name,
            source_parent_display,
            target_parent_fd,
            target_name,
            target_parent_display,
        )
        is_selected_step = (
            replace_after == "target_to_backup"
            and source_parent_display == target_root
            and target_parent_display == transaction_root / "backup"
        ) or (
            replace_after == "staged_to_target"
            and source_parent_display == transaction_root / "staged"
            and target_parent_display == target_root
        )
        if is_selected_step and not replaced:
            transaction_root.rename(moved_transaction)
            transaction_root.symlink_to(external, target_is_directory=True)
            replaced = True

    monkeypatch.setattr(
        storage_migration_module,
        "_durable_publish_without_replacing_at",
        replace_transaction_name_after_rename,
    )

    result = run_pending_storage_migration(config_manager)

    assert replaced is True
    assert result["completed"] is False
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED
    assert source_file.read_text(encoding="utf-8") == "SOURCE"
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert sentinel.read_text(encoding="utf-8") == "KEEP"
    assert moved_transaction.is_dir()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-bound roots")
def test_posix_publish_root_check_rejects_symlinked_ancestor(tmp_path):
    from utils import storage_migration as storage_migration_module

    parent = tmp_path / "selected-parent"
    target_root = parent / "N.E.K.O"
    target_root.mkdir(parents=True)
    payload = {
        "transaction_owner_token": "b" * 64,
    }
    txid = "a" * 32
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        txid,
    )
    transaction_root.mkdir()
    (transaction_root / "staged").mkdir()
    (transaction_root / "backup").mkdir()
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        txid,
    )
    roots = storage_migration_module._open_posix_publish_roots(
        payload,
        target_root,
        transaction_root,
        txid,
    )
    moved_parent = tmp_path / "selected-parent-moved"
    try:
        parent.rename(moved_parent)
        parent.symlink_to(moved_parent, target_is_directory=True)
        with pytest.raises(StorageMigrationError) as caught:
            storage_migration_module._ensure_posix_publish_roots_still_named(
                roots,
                target_root,
                transaction_root,
            )
    finally:
        roots.close()

    assert caught.value.error_code == "migration_path_changed"


@pytest.mark.unit
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux surrogateescape filename contract",
)
def test_linux_invalid_utf8_filename_migrates_without_digest_failure(tmp_path):
    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    source_config = source_root / "config"
    source_config.mkdir(parents=True, exist_ok=True)
    raw_name = b"invalid-\xff.json"
    source_bytes = os.fsencode(source_config)
    target_bytes = os.fsencode(target_root / "config")
    file_fd = os.open(
        source_bytes + b"/" + raw_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        os.write(file_fd, b"SOURCE")
    finally:
        os.close(file_fd)
    create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is True
    with open(target_bytes + b"/" + raw_name, "rb") as migrated:
        assert migrated.read() == b"SOURCE"
    with open(source_bytes + b"/" + raw_name, "rb") as retained:
        assert retained.read() == b"SOURCE"


@pytest.mark.unit
@pytest.mark.parametrize(
    "crash_step",
    ("before_transaction_cleanup", "during_transaction_cleanup", "after_cleanup"),
)
def test_rollback_completion_checkpoint_precedes_transaction_cleanup(
    tmp_path,
    monkeypatch,
    crash_step,
):
    from utils import storage_migration as storage_migration_module

    class SimulatedProcessLoss(BaseException):
        pass

    config_manager, _, target_file, transaction_root, _, _ = _prepare_interrupted_publish(
        tmp_path
    )
    original_remove = storage_migration_module._remove_transaction_root_if_owned
    original_remove_existing = storage_migration_module._remove_existing_path
    original_persist = storage_migration_module._persist_migration_payload
    interrupted = {"value": False}
    preflight_writes = {"count": 0}

    if crash_step == "before_transaction_cleanup":
        def _interrupt_before_cleanup(*args, **kwargs):
            if not interrupted["value"]:
                interrupted["value"] = True
                raise SimulatedProcessLoss
            return original_remove(*args, **kwargs)

        monkeypatch.setattr(
            storage_migration_module,
            "_remove_transaction_root_if_owned",
            _interrupt_before_cleanup,
        )
    elif crash_step == "during_transaction_cleanup":
        quarantine = storage_migration_module._private_directory_quarantine_path(
            transaction_root
        )
        if os.name != "nt":
            original_remove_children = (
                storage_migration_module._remove_posix_directory_children
            )

            def _interrupt_during_posix_cleanup(directory_fd, **kwargs):
                if (
                    Path(kwargs["display_path"]) == quarantine
                    and kwargs.get("preserve_names")
                    and not interrupted["value"]
                ):
                    interrupted["value"] = True
                    raise SimulatedProcessLoss
                return original_remove_children(directory_fd, **kwargs)

            monkeypatch.setattr(
                storage_migration_module,
                "_remove_posix_directory_children",
                _interrupt_during_posix_cleanup,
            )
        else:
            def _interrupt_during_cleanup(path):
                if (
                    Path(path).parent == quarantine
                    and Path(path).name
                    != storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
                    and not interrupted["value"]
                ):
                    interrupted["value"] = True
                    raise SimulatedProcessLoss
                return original_remove_existing(path)

            monkeypatch.setattr(
                storage_migration_module,
                "_remove_existing_path",
                _interrupt_during_cleanup,
            )
    else:
        def _interrupt_after_cleanup(*args, **kwargs):
            if kwargs.get("status") == STORAGE_MIGRATION_STATUS_PREFLIGHT:
                preflight_writes["count"] += 1
                if preflight_writes["count"] == 2:
                    raise SimulatedProcessLoss
            return original_persist(*args, **kwargs)

        monkeypatch.setattr(
            storage_migration_module,
            "_persist_migration_payload",
            _interrupt_after_cleanup,
        )

    with pytest.raises(SimulatedProcessLoss):
        run_pending_storage_migration(config_manager)

    checkpoint = load_storage_migration(config_manager)
    assert checkpoint["status"] == STORAGE_MIGRATION_STATUS_PREFLIGHT
    assert checkpoint["publish_entry_names"] == []
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert transaction_root.exists() is (crash_step == "before_transaction_cleanup")
    quarantine = storage_migration_module._private_directory_quarantine_path(
        transaction_root
    )
    assert quarantine.exists() is (crash_step == "during_transaction_cleanup")

    monkeypatch.setattr(
        storage_migration_module,
        "_remove_transaction_root_if_owned",
        original_remove,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_persist_migration_payload",
        original_persist,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_remove_existing_path",
        original_remove_existing,
    )
    monkeypatch.setattr(
        storage_migration_module,
        "_copy_runtime_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            StorageMigrationError("copy_failed", "stop after rollback")
        ),
    )
    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "copy_failed"
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not quarantine.exists()


@pytest.mark.unit
def test_transaction_cleanup_recovers_crash_after_owner_marker_is_deleted(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    class SimulatedProcessLoss(BaseException):
        pass

    config_manager, _, target_file, transaction_root, _, _ = (
        _prepare_interrupted_publish(tmp_path)
    )
    quarantine = storage_migration_module._private_directory_quarantine_path(
        transaction_root
    )
    quarantine_marker = (
        quarantine / storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
    )
    if os.name != "nt":
        real_rmdir = storage_migration_module.os.rmdir

        def _interrupt_empty_quarantine_rmdir(path, *, dir_fd=None):
            if (
                path == quarantine.name
                and dir_fd is not None
                and not quarantine_marker.exists()
            ):
                raise SimulatedProcessLoss
            return real_rmdir(path, dir_fd=dir_fd)

        monkeypatch.setattr(
            storage_migration_module.os,
            "rmdir",
            _interrupt_empty_quarantine_rmdir,
        )
    else:
        real_rmdir = Path.rmdir

        def _interrupt_empty_quarantine_rmdir(path):
            if path == quarantine and not quarantine_marker.exists():
                raise SimulatedProcessLoss
            return real_rmdir(path)

        monkeypatch.setattr(Path, "rmdir", _interrupt_empty_quarantine_rmdir)
    with pytest.raises(SimulatedProcessLoss):
        run_pending_storage_migration(config_manager)

    checkpoint = load_storage_migration(config_manager)
    assert checkpoint["status"] == STORAGE_MIGRATION_STATUS_PREFLIGHT
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not transaction_root.exists()
    assert quarantine.is_dir()
    assert list(quarantine.iterdir()) == []

    if os.name != "nt":
        monkeypatch.setattr(storage_migration_module.os, "rmdir", real_rmdir)
    else:
        monkeypatch.setattr(Path, "rmdir", real_rmdir)
    monkeypatch.setattr(
        storage_migration_module,
        "_copy_runtime_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            StorageMigrationError("copy_failed", "stop after quarantine recovery")
        ),
    )
    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "copy_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not quarantine.exists()


@pytest.mark.unit
@pytest.mark.parametrize("replacement_kind", ("nonempty_directory", "symlink"))
def test_transaction_cleanup_preserves_unowned_markerless_quarantine(
    tmp_path,
    replacement_kind,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    target_root.mkdir(parents=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    quarantine = storage_migration_module._private_directory_quarantine_path(
        transaction_root
    )
    if replacement_kind == "nonempty_directory":
        quarantine.mkdir(parents=True)
        sentinel = quarantine / "third-party.txt"
    else:
        external = tmp_path / "external-third-party"
        external.mkdir()
        sentinel = external / "third-party.txt"
        try:
            quarantine.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("directory symlinks are unavailable")
    sentinel.write_text("KEEP", encoding="utf-8")

    removed = storage_migration_module._remove_transaction_root_if_owned(
        payload,
        transaction_root,
        payload["txid"],
    )

    assert removed is False
    assert sentinel.read_text(encoding="utf-8") == "KEEP"
    assert quarantine.exists() or quarantine.is_symlink()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX parent directory durability")
def test_markerless_empty_quarantine_never_reports_success_when_parent_flush_fails(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    quarantine = tmp_path / ".neko-storage-migration-test.deleting"
    quarantine.mkdir()

    def fail_parent_flush(_fd):
        raise OSError(errno.EIO, "injected parent flush failure")

    monkeypatch.setattr(
        storage_migration_module.os,
        "fsync",
        fail_parent_flush,
    )

    with pytest.raises(storage_migration_module._TransactionCleanupDurabilityUnknown):
        storage_migration_module._remove_owned_transaction_quarantine(
            {},
            quarantine,
            "a" * 32,
        )

    assert not quarantine.exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX transaction marker contract")
def test_transaction_owner_marker_rejects_same_inode_rewrite_during_read(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    txid = "a" * 32
    payload = {"transaction_owner_token": "b" * 64}
    transaction_root = tmp_path / "transaction"
    transaction_root.mkdir()
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        txid,
    )
    marker = transaction_root / storage_migration_module._TRANSACTION_OWNER_MARKER_FILENAME
    replacement = marker.read_text(encoding="utf-8").replace("b" * 64, "c" * 64)
    old_timestamp = marker.stat().st_mtime - 60
    os.utime(marker, (old_timestamp, old_timestamp))
    original_identity = marker.stat()
    real_read = storage_migration_module.os.read
    replaced = False

    def rewrite_after_read(fd, size):
        nonlocal replaced
        chunk = real_read(fd, size)
        if chunk and not replaced:
            replaced = True
            marker.write_text(replacement, encoding="utf-8")
            os.utime(marker, (old_timestamp, old_timestamp))
        return chunk

    monkeypatch.setattr(storage_migration_module.os, "read", rewrite_after_read)
    directory_fd = os.open(transaction_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        owned = storage_migration_module._transaction_directory_fd_is_owned(
            payload,
            directory_fd,
            txid,
        )
    finally:
        os.close(directory_fd)

    assert replaced is True
    assert os.path.samestat(original_identity, marker.stat())
    assert owned is False


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX parent directory durability")
def test_transaction_cleanup_flush_failure_remains_recoverable_until_absence_is_durable(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager, _, target_file, transaction_root, _, _ = _prepare_interrupted_publish(
        tmp_path
    )
    quarantine = storage_migration_module._private_directory_quarantine_path(
        transaction_root
    )
    real_rmdir = storage_migration_module.os.rmdir
    real_fsync = storage_migration_module.os.fsync
    awaiting_parent_flush = False
    injected = False
    intent_seen_before_rmdir = False

    def track_transaction_rmdir(path, *args, **kwargs):
        nonlocal awaiting_parent_flush, intent_seen_before_rmdir
        if path == quarantine.name and kwargs.get("dir_fd") is not None:
            checkpoint = load_storage_migration(config_manager)
            intent_seen_before_rmdir = bool(
                checkpoint and checkpoint.get("transaction_cleanup_pending")
            )
        result = real_rmdir(path, *args, **kwargs)
        if path == quarantine.name and kwargs.get("dir_fd") is not None:
            awaiting_parent_flush = True
        return result

    def fail_cleanup_parent_flush_once(fd):
        nonlocal awaiting_parent_flush, injected
        if awaiting_parent_flush and not injected:
            awaiting_parent_flush = False
            injected = True
            raise OSError(errno.EIO, "injected transaction parent flush failure")
        return real_fsync(fd)

    monkeypatch.setattr(storage_migration_module.os, "rmdir", track_transaction_rmdir)
    monkeypatch.setattr(storage_migration_module.os, "fsync", fail_cleanup_parent_flush_once)

    result = run_pending_storage_migration(config_manager)

    assert injected is True
    assert intent_seen_before_rmdir is True
    assert result["completed"] is False
    assert result["error_code"] == "transaction_cleanup_durability_unknown"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED
    assert result["payload"]["transaction_cleanup_pending"] is True
    assert not transaction_root.exists()
    assert not quarantine.exists()
    assert target_file.read_text(encoding="utf-8") == "TARGET"

    monkeypatch.setattr(storage_migration_module.os, "rmdir", real_rmdir)
    monkeypatch.setattr(storage_migration_module.os, "fsync", real_fsync)
    original_confirm = storage_migration_module._confirm_transaction_names_absent_durably
    confirmed = False

    def record_absence_confirmation(path):
        nonlocal confirmed
        original_confirm(path)
        confirmed = True

    monkeypatch.setattr(
        storage_migration_module,
        "_confirm_transaction_names_absent_durably",
        record_absence_confirmation,
    )
    shutil.rmtree(config_manager.app_docs_dir)

    retry = run_pending_storage_migration(config_manager)

    assert confirmed is True
    assert retry["error_code"] == "source_root_missing"
    assert retry["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED
    assert retry["payload"]["transaction_cleanup_pending"] is False
    assert target_file.read_text(encoding="utf-8") == "TARGET"


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="POSIX transaction cleanup intent")
def test_publishing_checkpoint_never_confirms_an_unexpected_missing_transaction(
    tmp_path,
    monkeypatch,
):
    from utils import storage_migration as storage_migration_module

    config_manager, _, _, transaction_root, _, _ = _prepare_interrupted_publish(
        tmp_path
    )
    parked_transaction = transaction_root.with_name(f"{transaction_root.name}.parked")
    transaction_root.rename(parked_transaction)
    monkeypatch.setattr(
        storage_migration_module,
        "_confirm_transaction_names_absent_durably",
        lambda *_args, **_kwargs: pytest.fail(
            "publishing evidence must not be made durably absent"
        ),
    )

    result = run_pending_storage_migration(config_manager)

    assert result["error_code"] == "rollback_transaction_missing"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert parked_transaction.is_dir()


@pytest.mark.unit
def test_transaction_cleanup_preserves_name_replacement_race(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    transaction_root.mkdir(parents=True)
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        payload["txid"],
    )
    owned_aside = target_root / "owned-aside"
    replacement = target_root / "third-party"
    replacement.mkdir()
    sentinel = replacement / "third-party.txt"
    sentinel.write_text("KEEP", encoding="utf-8")
    original_remove_private = (
        storage_migration_module._remove_private_directory_via_quarantine
    )

    def _swap_before_quarantine(path, expected_identity, **kwargs):
        path.rename(owned_aside)
        replacement.rename(path)
        return original_remove_private(path, expected_identity, **kwargs)

    monkeypatch.setattr(
        storage_migration_module,
        "_remove_private_directory_via_quarantine",
        _swap_before_quarantine,
    )

    removed = storage_migration_module._remove_transaction_root_if_owned(
        payload,
        transaction_root,
        payload["txid"],
    )

    assert removed is False
    assert (transaction_root / sentinel.name).read_text(encoding="utf-8") == "KEEP"
    assert owned_aside.exists()


@pytest.mark.unit
def test_transaction_cleanup_restores_replacement_symlink_without_following_it(tmp_path):
    from utils import storage_migration as storage_migration_module

    transaction_root = tmp_path / ".neko-storage-migration-owned"
    transaction_root.mkdir()
    owned_identity = transaction_root.lstat()
    owned_aside = tmp_path / "owned-aside"
    transaction_root.rename(owned_aside)
    external = tmp_path / "external.txt"
    external.write_text("KEEP", encoding="utf-8")
    try:
        transaction_root.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks are unavailable")

    removed = storage_migration_module._remove_private_directory_via_quarantine(
        transaction_root,
        owned_identity,
    )

    assert removed is False
    assert transaction_root.is_symlink()
    assert os.path.samefile(transaction_root, external)
    assert external.read_text(encoding="utf-8") == "KEEP"
    assert owned_aside.is_dir()


@pytest.mark.unit
def test_prepared_transaction_identity_replacement_is_preserved(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    target_root.mkdir(parents=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    original_write_marker = storage_migration_module._write_transaction_owner_marker
    replaced = {}

    def _replace_prepared_root(marker_payload, prepared_root, txid):
        owned_aside = prepared_root.with_name(f"{prepared_root.name}.owned-aside")
        prepared_root.rename(owned_aside)
        prepared_root.mkdir()
        sentinel = prepared_root / "third-party.txt"
        sentinel.write_text("KEEP", encoding="utf-8")
        replaced.update(root=prepared_root, aside=owned_aside, sentinel=sentinel)
        original_write_marker(marker_payload, prepared_root, txid)

    monkeypatch.setattr(
        storage_migration_module,
        "_write_transaction_owner_marker",
        _replace_prepared_root,
    )

    with pytest.raises(OSError, match="identity changed"):
        storage_migration_module._create_owned_transaction_root(
            payload,
            transaction_root,
            payload["txid"],
        )

    assert not transaction_root.exists()
    assert replaced["root"].is_dir()
    assert replaced["aside"].is_dir()
    assert replaced["sentinel"].read_text(encoding="utf-8") == "KEEP"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("terminal_status", "attempted", "completed"),
    (
        (STORAGE_MIGRATION_STATUS_COMPLETED, False, False),
        (STORAGE_MIGRATION_STATUS_FAILED, True, True),
    ),
)
def test_terminal_checkpoint_recovers_interrupted_transaction_quarantine(
    tmp_path,
    terminal_status,
    attempted,
    completed,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    config_manager.app_docs_dir.mkdir(parents=True, exist_ok=True)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    target_root.mkdir(parents=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=config_manager.app_docs_dir,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    transaction_root.mkdir()
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        payload["txid"],
    )
    quarantine = storage_migration_module._private_directory_quarantine_path(
        transaction_root
    )
    storage_migration_module._durable_rename_without_replacing(
        transaction_root,
        quarantine,
    )
    payload.update(
        status=terminal_status,
        transaction_root=str(transaction_root),
        source_runtime_baseline={},
    )
    storage_migration_module.save_storage_migration(config_manager, payload)

    result = run_pending_storage_migration(config_manager)

    assert result["attempted"] is attempted
    assert result["completed"] is completed, result
    assert not transaction_root.exists()
    assert not quarantine.exists()


@pytest.mark.unit
@pytest.mark.parametrize("detached", (False, True))
@pytest.mark.parametrize(
    ("source_loss", "expected_error"),
    (
        ("root_missing", "source_root_missing"),
        ("entry_missing", "source_recovery_unverifiable"),
    ),
)
def test_copying_checkpoint_preserves_staged_data_when_source_disappears(
    tmp_path,
    detached,
    source_loss,
    expected_error,
):
    from utils import storage_migration as storage_migration_module

    config_manager = _make_config_manager(tmp_path)
    source_root = config_manager.app_docs_dir
    source_file = source_root / "config" / "characters.json"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    source_baseline = storage_migration_module._snapshot_runtime_entries(source_root)
    target_root = tmp_path / "target-selected" / "N.E.K.O"
    target_root.mkdir(parents=True)
    payload = create_pending_storage_migration(
        config_manager,
        source_root=source_root,
        target_root=target_root,
        selection_source="custom",
    )
    transaction_root = storage_migration_module._transaction_root_for(
        target_root,
        payload["txid"],
    )
    staged_file = transaction_root / "staged" / "config" / "characters.json"
    staged_file.parent.mkdir(parents=True)
    staged_file.write_text("ONLY-COPY", encoding="utf-8")
    storage_migration_module._write_transaction_owner_marker(
        payload,
        transaction_root,
        payload["txid"],
    )
    payload.update(
        status="copying",
        transaction_root=str(transaction_root),
        source_runtime_baseline=source_baseline,
    )
    storage_migration_module.save_storage_migration(config_manager, payload)
    evidence_root = transaction_root
    if detached:
        evidence_root = storage_migration_module._private_directory_quarantine_path(
            transaction_root
        )
        storage_migration_module._durable_rename_without_replacing(
            transaction_root,
            evidence_root,
        )
    if source_loss == "root_missing":
        shutil.rmtree(source_root)
    else:
        source_file.unlink()

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == expected_error
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED
    assert (evidence_root / "staged" / "config" / "characters.json").read_text(
        encoding="utf-8"
    ) == "ONLY-COPY"

    # Legacy builds wrote this evidence-owning state as terminal ``failed``.
    # The next launcher must upgrade it back into an active recovery attempt.
    legacy_payload = dict(load_storage_migration(config_manager))
    legacy_payload["status"] = STORAGE_MIGRATION_STATUS_FAILED
    storage_migration_module.save_storage_migration(config_manager, legacy_payload)

    # The active recovery checkpoint still cannot prove this copy is redundant.
    retry = run_pending_storage_migration(config_manager)
    assert retry["payload"]["status"] == STORAGE_MIGRATION_STATUS_RECOVERY_REQUIRED
    assert (evidence_root / "staged" / "config" / "characters.json").read_text(
        encoding="utf-8"
    ) == "ONLY-COPY"

    # Once the original source is restored byte-for-byte, the next launcher can
    # securely retire the owned transaction and retry the migration.
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("SOURCE", encoding="utf-8")
    recovered = run_pending_storage_migration(config_manager)
    assert recovered["completed"] is True
    assert not evidence_root.exists()
    assert (target_root / "config" / "characters.json").read_text(
        encoding="utf-8"
    ) == "SOURCE"


@pytest.mark.unit
def test_interrupted_publish_from_legacy_transaction_layout_is_recovered(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, _, target_file, transaction_root, _, _ = _prepare_interrupted_publish(
        tmp_path,
        legacy_transaction_layout=True,
    )

    def fail_retry_copy(*args, **kwargs):
        raise StorageMigrationError("copy_failed", "stop after legacy rollback")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", fail_retry_copy)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "copy_failed"
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_missing_backup_stays_rollback_required_with_evidence(tmp_path):
    config_manager, _, target_file, transaction_root, backup_entry, _ = _prepare_interrupted_publish(tmp_path)
    shutil.rmtree(backup_entry)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_damaged_backup_stays_rollback_required_without_overwriting_target(tmp_path):
    config_manager, _, target_file, transaction_root, backup_entry, _ = _prepare_interrupted_publish(tmp_path)
    (backup_entry / "characters.json").write_text("DAMAGED", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_concurrent_target_write_fails_final_baseline_check(tmp_path):
    config_manager, target_root, target_file, transaction_root, _, _ = _prepare_interrupted_publish(tmp_path)
    concurrent_file = target_root / "jukebox" / "library.json"
    concurrent_file.parent.mkdir(parents=True)
    concurrent_file.write_text("CONCURRENT", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert concurrent_file.read_text(encoding="utf-8") == "CONCURRENT"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_concurrent_rewrite_is_not_overwritten_by_rollback(tmp_path):
    config_manager, _, target_file, transaction_root, backup_entry, _ = _prepare_interrupted_publish(tmp_path)
    target_file.write_text("CONCURRENT", encoding="utf-8")

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "CONCURRENT"
    assert (backup_entry / "characters.json").read_text(encoding="utf-8") == "TARGET"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_without_target_baseline_preserves_transaction(tmp_path):
    config_manager, _, target_file, transaction_root, _, payload = _prepare_interrupted_publish(tmp_path)
    payload.pop("target_baseline", None)
    from utils import storage_migration as storage_migration_module

    storage_migration_module.save_storage_migration(config_manager, payload)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_baseline_missing"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_with_missing_source_and_manifest_stays_recoverable(tmp_path):
    config_manager, _, target_file, transaction_root, _, payload = _prepare_interrupted_publish(tmp_path)
    payload.pop("publish_entry_snapshots", None)
    from utils import storage_migration as storage_migration_module

    storage_migration_module.save_storage_migration(config_manager, payload)
    shutil.rmtree(config_manager.app_docs_dir)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"
    assert transaction_root.exists()


@pytest.mark.unit
def test_interrupted_publish_without_transaction_evidence_stays_rollback_required(tmp_path):
    config_manager, _, target_file, transaction_root, _, _ = _prepare_interrupted_publish(tmp_path)
    shutil.rmtree(transaction_root)

    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "rollback_transaction_missing"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_ROLLBACK_REQUIRED
    assert target_file.read_text(encoding="utf-8") == "SOURCE"


@pytest.mark.unit
def test_interrupted_publish_accepts_already_restored_baseline_before_retry(tmp_path, monkeypatch):
    from utils import storage_migration as storage_migration_module

    config_manager, target_root, target_file, transaction_root, backup_entry, _ = _prepare_interrupted_publish(tmp_path)
    shutil.rmtree(target_root / "config")
    os.replace(backup_entry, target_root / "config")

    def fail_retry_copy(*args, **kwargs):
        raise StorageMigrationError("copy_failed", "stop after verified idempotent rollback")

    monkeypatch.setattr(storage_migration_module, "_copy_runtime_entry", fail_retry_copy)
    result = run_pending_storage_migration(config_manager)

    assert result["completed"] is False
    assert result["error_code"] == "copy_failed"
    assert result["payload"]["status"] == STORAGE_MIGRATION_STATUS_FAILED
    assert target_file.read_text(encoding="utf-8") == "TARGET"
    assert not transaction_root.exists()
