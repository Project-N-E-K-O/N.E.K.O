import os
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.storage_layout import (
    NEKO_STORAGE_ANCHOR_ROOT_ENV,
    NEKO_STORAGE_CLOUDSAVE_ROOT_ENV,
    NEKO_STORAGE_SELECTED_ROOT_ENV,
    export_storage_layout_to_env,
    resolve_storage_layout,
)
from utils.storage_policy import save_storage_policy
from utils.file_utils import atomic_write_json


def _record_workspace_in_ledger(config_manager, workspace):
    """Write the ledger line the migration writes when it mints one.

    Exemption is keyed on this file, so a hand-made workspace directory is
    not a workspace as far as the import is concerned -- which is the whole
    point of the criterion.
    """
    from utils.config_manager.migrations import (
        _MIGRATION_LEDGER_NAME,
        _MIGRATION_STAGING_DIR,
    )

    ledger = (
        Path(config_manager.app_docs_dir)
        / _MIGRATION_STAGING_DIR
        / _MIGRATION_LEDGER_NAME
    )
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a", encoding="utf-8") as handle:
        handle.write(str(Path(workspace).resolve(strict=False)) + "\n")


def _make_config_manager(tmp_path: Path):
    from utils.config_manager import ConfigManager

    standard_root = tmp_path / "anchor-base"
    with patch.object(
        ConfigManager,
        "_get_documents_directory",
        return_value=tmp_path / "runtime-parent",
    ), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[standard_root],
    ):
        config_manager = ConfigManager("N.E.K.O")
    # Preserve the mocked candidate list after __init__ so subsequent layout calls stay deterministic.
    config_manager._get_standard_data_directory_candidates = lambda: [standard_root]
    return config_manager


@pytest.mark.unit
def test_export_storage_layout_to_env_clears_empty_values(tmp_path):
    environ = {
        NEKO_STORAGE_SELECTED_ROOT_ENV: "stale-selected",
        NEKO_STORAGE_ANCHOR_ROOT_ENV: "stale-anchor",
        NEKO_STORAGE_CLOUDSAVE_ROOT_ENV: "stale-cloudsave",
    }

    export_storage_layout_to_env(
        {
            "selected_root": tmp_path / "selected",
            "anchor_root": "",
            "cloudsave_root": None,
        },
        environ=environ,
    )

    assert environ[NEKO_STORAGE_SELECTED_ROOT_ENV] == str(tmp_path / "selected")
    assert NEKO_STORAGE_ANCHOR_ROOT_ENV not in environ
    assert NEKO_STORAGE_CLOUDSAVE_ROOT_ENV not in environ


@pytest.mark.unit
def test_config_manager_uses_committed_storage_policy_for_selected_and_anchor_roots(tmp_path, monkeypatch):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    config_manager = _make_config_manager(tmp_path)
    selected_root = tmp_path / "custom-selected" / "N.E.K.O"
    selected_root.mkdir(parents=True, exist_ok=True)
    save_storage_policy(
        config_manager,
        selected_root=selected_root,
        selection_source="custom",
    )

    reloaded_manager = _make_config_manager(tmp_path)

    assert reloaded_manager.app_docs_dir == selected_root.resolve()
    assert reloaded_manager.anchor_root == (tmp_path / "anchor-base" / "N.E.K.O").resolve()
    assert reloaded_manager.cloudsave_dir == reloaded_manager.anchor_root / "cloudsave"
    assert reloaded_manager.local_state_dir == reloaded_manager.anchor_root / "state"


@pytest.mark.unit
def test_config_manager_keeps_fixed_anchor_when_policy_load_fails(tmp_path, monkeypatch):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    with patch("utils.storage_policy.load_storage_policy", side_effect=OSError("policy unreadable")):
        config_manager = _make_config_manager(tmp_path)

    assert config_manager.app_docs_dir == (tmp_path / "runtime-parent" / "N.E.K.O")
    assert config_manager.committed_selected_root == config_manager.app_docs_dir
    assert config_manager.anchor_root == (tmp_path / "anchor-base" / "N.E.K.O").resolve()
    assert config_manager.cloudsave_dir == config_manager.anchor_root / "cloudsave"


@pytest.mark.unit
def test_config_manager_env_overrides_committed_layout(tmp_path, monkeypatch):
    override_selected_root = (tmp_path / "override-selected" / "N.E.K.O").resolve()
    override_anchor_root = (tmp_path / "override-anchor" / "N.E.K.O").resolve()
    monkeypatch.setenv(NEKO_STORAGE_SELECTED_ROOT_ENV, str(override_selected_root))
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, str(override_anchor_root))

    config_manager = _make_config_manager(tmp_path)

    assert config_manager.app_docs_dir == override_selected_root
    assert config_manager.anchor_root == override_anchor_root
    assert config_manager.cloudsave_dir == override_anchor_root / "cloudsave"
    assert config_manager.local_state_dir == override_anchor_root / "state"


@pytest.mark.unit
def test_config_manager_env_anchor_takes_precedence_over_policy_anchor(tmp_path, monkeypatch):
    override_selected_root = (tmp_path / "override-selected" / "N.E.K.O").resolve()
    override_anchor_root = (tmp_path / "override-anchor" / "N.E.K.O").resolve()
    stale_policy_anchor = (tmp_path / "stale-policy-anchor" / "N.E.K.O").resolve()
    atomic_write_json(
        override_anchor_root / "state" / "storage_policy.json",
        {
            "version": 1,
            "anchor_root": str(stale_policy_anchor),
            "selected_root": str(override_selected_root),
            "selection_source": "custom",
            "cloudsave_strategy": "fixed_anchor",
            "first_run_completed": True,
        },
        ensure_ascii=False,
        indent=2,
    )
    monkeypatch.setenv(NEKO_STORAGE_SELECTED_ROOT_ENV, str(override_selected_root))
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, str(override_anchor_root))

    config_manager = _make_config_manager(tmp_path)

    assert config_manager.app_docs_dir == override_selected_root
    assert config_manager.anchor_root == override_anchor_root
    assert config_manager.anchor_root != stale_policy_anchor


@pytest.mark.unit
def test_resolve_storage_layout_keeps_default_anchor_when_policy_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    config_manager = _make_config_manager(tmp_path)
    layout = resolve_storage_layout(config_manager)

    assert layout["selected_root"] == str((tmp_path / "runtime-parent" / "N.E.K.O").resolve())
    assert layout["anchor_root"] == str((tmp_path / "anchor-base" / "N.E.K.O").resolve())
    assert layout["source"] == "runtime_default"


@pytest.mark.unit
def test_config_manager_uses_anchor_runtime_layout_when_committed_selected_root_is_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    config_manager = _make_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        config_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )

    reloaded_manager = _make_config_manager(tmp_path)
    anchor_root = (tmp_path / "anchor-base" / "N.E.K.O").resolve()

    assert reloaded_manager.recovery_committed_root_unavailable is True
    assert reloaded_manager.app_docs_dir == anchor_root
    assert reloaded_manager.anchor_root == anchor_root
    assert reloaded_manager.selected_root == unavailable_selected_root.resolve()
    assert reloaded_manager.reported_current_root == unavailable_selected_root.resolve()

    root_state = reloaded_manager.load_root_state()
    assert root_state["mode"] == "deferred_init"
    assert root_state["current_root"] == str(unavailable_selected_root.resolve())
    assert root_state["last_known_good_root"] == str(unavailable_selected_root.resolve())
    assert root_state["last_migration_backup"] == ""
    assert root_state["legacy_cleanup_pending"] is False

    layout = resolve_storage_layout(reloaded_manager)
    assert layout["selected_root"] == str(anchor_root)
    assert layout["anchor_root"] == str(anchor_root)
    assert layout["source"] == "recovery_runtime"


@pytest.mark.unit
def test_config_manager_uses_env_anchor_when_policy_selected_root_is_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    env_anchor_root = (tmp_path / "env-anchor" / "N.E.K.O").resolve()
    unavailable_selected_root = (tmp_path / "offline-selected" / "N.E.K.O").resolve()
    stale_policy_anchor = (tmp_path / "stale-policy-anchor" / "N.E.K.O").resolve()
    atomic_write_json(
        env_anchor_root / "state" / "storage_policy.json",
        {
            "version": 1,
            "anchor_root": str(stale_policy_anchor),
            "selected_root": str(unavailable_selected_root),
            "selection_source": "custom",
            "cloudsave_strategy": "fixed_anchor",
            "first_run_completed": True,
        },
        ensure_ascii=False,
        indent=2,
    )
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, str(env_anchor_root))

    reloaded_manager = _make_config_manager(tmp_path)

    assert reloaded_manager.recovery_committed_root_unavailable is True
    assert reloaded_manager.app_docs_dir == env_anchor_root
    assert reloaded_manager.anchor_root == env_anchor_root
    assert reloaded_manager.selected_root == unavailable_selected_root
    assert reloaded_manager.reported_current_root == unavailable_selected_root


@pytest.mark.unit
def test_config_manager_recovery_state_persist_failure_is_best_effort(
    tmp_path,
    monkeypatch,
):
    from utils.config_manager import ConfigManager

    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    initial_manager = _make_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        initial_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )

    with patch.object(ConfigManager, "save_root_state", side_effect=OSError("disk unavailable")):
        reloaded_manager = _make_config_manager(tmp_path)

    assert reloaded_manager.recovery_committed_root_unavailable is True
    assert reloaded_manager.recovery_committed_root_unavailable_override is True
    assert reloaded_manager.selected_root == unavailable_selected_root.resolve()
    assert reloaded_manager.reported_current_root == unavailable_selected_root.resolve()
    root_state = reloaded_manager.load_root_state()
    assert root_state["mode"] == "deferred_init"
    assert root_state["current_root"] == str(unavailable_selected_root.resolve())
    assert root_state["last_known_good_root"] == str(unavailable_selected_root.resolve())
    assert root_state["last_migration_result"].startswith("selected_root_unavailable:")


@pytest.mark.unit
def test_config_manager_preserves_recovery_context_when_launcher_exports_anchor_runtime_layout(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    initial_manager = _make_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        initial_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )

    recovery_manager = _make_config_manager(tmp_path)
    layout = resolve_storage_layout(recovery_manager)
    monkeypatch.setenv(NEKO_STORAGE_SELECTED_ROOT_ENV, layout["selected_root"])
    monkeypatch.setenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, layout["anchor_root"])

    reloaded_manager = _make_config_manager(tmp_path)
    anchor_root = (tmp_path / "anchor-base" / "N.E.K.O").resolve()

    assert reloaded_manager.app_docs_dir == anchor_root
    assert reloaded_manager.anchor_root == anchor_root
    assert reloaded_manager.committed_selected_root == unavailable_selected_root.resolve()
    assert reloaded_manager.reported_current_root == unavailable_selected_root.resolve()
    assert reloaded_manager.recovery_committed_root_unavailable is True


@pytest.mark.unit
def test_get_config_manager_skips_runtime_file_migration_while_recovery_layout_is_active(
    tmp_path,
    monkeypatch,
):
    from utils.config_manager import ConfigManager, get_config_manager, reset_config_manager_cache

    monkeypatch.delenv(NEKO_STORAGE_SELECTED_ROOT_ENV, raising=False)
    monkeypatch.delenv(NEKO_STORAGE_ANCHOR_ROOT_ENV, raising=False)

    initial_manager = _make_config_manager(tmp_path)
    unavailable_selected_root = tmp_path / "offline-selected" / "N.E.K.O"
    save_storage_policy(
        initial_manager,
        selected_root=unavailable_selected_root,
        selection_source="custom",
    )

    reset_config_manager_cache()
    standard_root = tmp_path / "anchor-base"
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path / "runtime-parent"), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[standard_root],
    ):
        manager = get_config_manager("N.E.K.O")

    try:
        assert manager.recovery_committed_root_unavailable is True
        assert not (unavailable_selected_root / "config").exists()
        assert not (unavailable_selected_root / "memory").exists()
        manager.recovery_committed_root_unavailable = False
        with patch.object(manager, "migrate_config_files") as migrate_config, patch.object(
            manager,
            "migrate_memory_files",
        ) as migrate_memory:
            assert get_config_manager("N.E.K.O") is manager
        migrate_config.assert_called_once()
        migrate_memory.assert_called_once()
    finally:
        reset_config_manager_cache()
@pytest.mark.unit
def test_one_failing_entry_does_not_strand_the_rest(tmp_path):
    """A single entry blowing up must not decide the fate of the ones after it.

    The handler used to wrap the whole loop, so the first failure left every
    later character and loose file behind in the project root -- worse than the
    gap this change set out to fix. Each entry now has its own.

    The failure is induced rather than staged through a filesystem collision:
    with the destination-exists rule, a runtime file of the same name is simply
    skipped and never raises, so a collision no longer exercises this at all.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    for name in ("Carol", "Dave"):
        (project_root / name).mkdir(parents=True)
        (project_root / name / "facts.json").write_text("[1]", encoding="utf-8")
    (project_root / "zz_loose.json").write_text("[3]", encoding="utf-8")

    real_copytree = migrations_module.shutil.copytree

    def _fail_for_carol(source, destination, *args, **kwargs):
        if Path(source).name == "Carol":
            raise OSError("no space left on device")
        return real_copytree(source, destination, *args, **kwargs)

    with patch.object(migrations_module.shutil, "copytree", _fail_for_carol):
        config_manager.migrate_memory_files()

    assert not (runtime_root / "Carol").exists()
    assert (runtime_root / "Dave" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "an earlier entry's failure aborted the whole migration"
    assert (runtime_root / "zz_loose.json").read_text(
        encoding="utf-8"
    ) == "[3]", "loose files after the failure were stranded too"

    # And the project side is untouched, so a later start can retry it.
    assert (project_root / "Carol" / "facts.json").exists()


@pytest.mark.unit
def test_an_existing_destination_of_any_type_is_left_alone(tmp_path):
    """Whatever is already in the runtime root wins, whatever shape it is.

    A directory in the project root against a plain file of the same name in
    the runtime root is not a case to resolve -- neither side should be
    destroyed for the other, and the seed is not authoritative over live data.
    """
    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")
    (runtime_root / "Carol").write_text("not-a-directory", encoding="utf-8")
    (project_root / "Dave").mkdir()
    (project_root / "Dave" / "facts.json").write_text("[2]", encoding="utf-8")

    config_manager.migrate_memory_files()

    assert (runtime_root / "Carol").is_file()
    assert (runtime_root / "Carol").read_text(
        encoding="utf-8"
    ) == "not-a-directory"
    assert (project_root / "Carol" / "facts.json").exists()
    assert (runtime_root / "Dave" / "facts.json").exists()
@pytest.mark.unit
def test_deliberately_removed_files_are_not_resurrected(tmp_path):
    """A file missing from the runtime root is not proof it was never copied.

    A cloud import intentionally omits managed files and unlinks them, and
    users delete things. Filling every gap would put them back on the next
    start, every start -- trading data left stranded for data that will not
    stay deleted, which is the worse of the two.

    So the rule is simply: if the destination exists at all, do not touch it.
    The copy is staged and renamed into place, so a directory that exists is
    a complete one and there is never a gap to be tempted into filling.
    """
    config_manager = _make_config_manager(tmp_path)

    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")
    (project_root / "Carol" / "time_indexed.db").write_bytes(b"stale")

    # A live character. The cloud import removed facts.json on purpose and
    # left no marker, because no copy is in progress.
    (runtime_root / "Carol").mkdir()
    (runtime_root / "Carol" / "recent.json").write_text("[]", encoding="utf-8")

    config_manager.migrate_memory_files()

    assert not (runtime_root / "Carol" / "facts.json").exists(), (
        "a file deleted on purpose came back, and would come back on every "
        "start"
    )
    assert not (runtime_root / "Carol" / "time_indexed.db").exists()
    assert (runtime_root / "Carol" / "recent.json").exists()


@pytest.mark.unit
def test_an_interrupted_copy_leaves_nothing_and_retries_whole(tmp_path):
    """An interrupted copy must leave no half-built character behind.

    Copying straight into ``memory/<name>/`` meant a crash mid-copy left a
    partial directory there, and the top-level skip then refused to look at
    that character ever again -- while no reader consults the project root,
    so the rest of its memory became unreachable.

    Staging into a sibling directory and renaming makes the outcome binary:
    the destination is either complete or absent. Absent means the next
    start copies the whole thing again, which also repairs a file the
    interrupted run had truncated -- something a gap-filling pass cannot do,
    because a truncated file still satisfies ``exists()``.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "recent.json").write_text("[]", encoding="utf-8")
    (project_root / "Carol" / "time_indexed.db").write_bytes(b"seeded-db")

    real_copytree = migrations_module.shutil.copytree

    def _die_partway(source, destination, *args, **kwargs):
        Path(destination).mkdir(parents=True, exist_ok=True)
        (Path(destination) / "time_indexed.db").write_bytes(b"trunc")
        raise OSError("interrupted partway through the copy")

    with patch.object(migrations_module.shutil, "copytree", _die_partway):
        config_manager.migrate_memory_files()

    assert not (runtime_root / "Carol").exists(), (
        "a half-copied character was published, and the top-level skip would "
        "then refuse to touch it again"
    )
    leftovers = [p.name for p in runtime_root.iterdir()]
    assert leftovers == [], (
        "the staging directory was left behind: %s" % leftovers
    )

    # Next start, with the copy working again.
    config_manager.migrate_memory_files()
    assert (runtime_root / "Carol" / "recent.json").exists()
    assert (runtime_root / "Carol" / "time_indexed.db").read_bytes() == b"seeded-db", (
        "the retry kept the truncated file from the interrupted attempt"
    )
    assert [p.name for p in runtime_root.iterdir()] == ["Carol"]
    assert real_copytree is migrations_module.shutil.copytree


@pytest.mark.unit
def test_symlinked_entries_are_left_alone(tmp_path):
    """Neither side of a link is followed, in either direction.

    A BROKEN link at the destination reports ``exists() is False`` while
    still being the runtime entry, and copying onto it writes through to
    wherever it points -- outside ``memory_dir``. A link on the project side
    is the same hazard mirrored: copying it drags in whatever it targets.
    """
    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)

    outside = tmp_path / "outside.json"
    (project_root / "linked.json").write_text("[1]", encoding="utf-8")
    try:
        (runtime_root / "linked.json").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this platform will not create symlinks unprivileged")

    # The mirrored hazard: a link on the PROJECT side. is_file() follows it,
    # so without a guard the migration would copy whatever it points at into
    # the runtime root -- content from outside memory_dir entirely.
    secret = tmp_path / "outside_secret.json"
    secret.write_text('["from-outside"]', encoding="utf-8")
    try:
        (project_root / "seeded_link.json").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("this platform will not create symlinks unprivileged")

    # Sorted after both, so the loop is shown to continue.
    (project_root / "zz.json").write_text("[2]", encoding="utf-8")

    config_manager.migrate_memory_files()

    assert not outside.exists(), (
        "the seeded file was written through a broken link, outside the "
        "memory root entirely"
    )
    assert (runtime_root / "linked.json").is_symlink()
    assert not (runtime_root / "seeded_link.json").exists(), (
        "a link in the project root was followed, dragging content from "
        "outside the memory root into it"
    )
    assert (runtime_root / "zz.json").read_text(encoding="utf-8") == "[2]"


@pytest.mark.unit
def test_an_interrupted_loose_file_copy_publishes_nothing(tmp_path):
    """The directory branch was made atomic; this one had the same premise.

    A plain ``copy2`` interrupted partway leaves a truncated file at the
    destination, and "destination exists, leave it alone" then records that
    damaged file as migrated for good -- exactly what staging fixed for
    directories, in the sibling branch that was left behind.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)

    (project_root / "recent_Carol.json").write_text(
        '["the whole thing"]', encoding="utf-8"
    )

    # Patched at ``_copy_with_heartbeat``, which is the seam the flat-file
    # branch uses -- it copies in chunks so the workspace mtime keeps moving
    # during a large file, and no longer calls ``shutil.copy2`` for the
    # bytes. Patching copy2 here intercepted nothing and the test passed
    # against a copy that was never interrupted.
    def _dies_partway(_beat):
        def _copy(source, destination, *args, **kwargs):
            Path(destination).write_text('["trun', encoding="utf-8")
            raise OSError("interrupted partway through the copy")

        return _copy

    with patch.object(
        migrations_module, "_copy_with_heartbeat", _dies_partway
    ):
        config_manager.migrate_memory_files()

    assert not (runtime_root / "recent_Carol.json").exists(), (
        "a truncated file was published, and the destination-exists skip "
        "would record it as migrated for good"
    )
    assert [p.name for p in runtime_root.iterdir()] == [], (
        "the staging file was left behind: %s"
        % [p.name for p in runtime_root.iterdir()]
    )

    # Next start, with the copy working again.
    config_manager.migrate_memory_files()
    assert (runtime_root / "recent_Carol.json").read_text(
        encoding="utf-8"
    ) == '["the whole thing"]'
    # The patch really came off, so the second run above was the real copier
    # rather than the fake one still installed.
    assert migrations_module._copy_with_heartbeat is not _dies_partway


@pytest.mark.unit
def test_a_source_name_near_the_filesystem_limit_still_migrates(tmp_path):
    """The staged name must not grow with the source name.

    Building it as ``<hex>-<item.name>`` meant a source already close to the
    filesystem name limit produced a staged name over it, and the copy died
    with ENAMETOOLONG -- so the very files most likely to matter would be the
    ones that never migrated. mkstemp generates a short name of its own.
    """
    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)

    # Long enough that any prefix would have pushed it over on a filesystem
    # with a 255-byte limit, which is the common case.
    long_name = "recent_" + ("x" * 230) + ".json"
    assert len(long_name) > 200
    try:
        (project_root / long_name).write_text("[1]", encoding="utf-8")
    except OSError:
        pytest.skip("this filesystem will not hold a name that long")

    config_manager.migrate_memory_files()

    assert (runtime_root / long_name).read_text(encoding="utf-8") == "[1]", (
        "a source name near the limit never migrated -- the staged name grew "
        "with it"
    )
@pytest.mark.unit
def test_staging_failure_does_not_take_startup_down(tmp_path):
    """A migration that cannot start is skipped, not a broken launch.

    Preparing the staging root touches the disk, so a full or read-only
    runtime root makes it raise. This runs on the startup path from
    ``get_config_manager()``; outside the handler it would take the launch
    down with it and migrate nothing.
    """
    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    def _no_room(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    with patch.object(
        type(config_manager), "_prepare_migration_staging_root", _no_room
    ):
        # Must not raise. The caller is startup.
        config_manager.migrate_memory_files()

    assert not (runtime_root / "Carol").exists(), (
        "nothing should have migrated, but nothing should have crashed either"
    )

    # And a later start, with room again, still completes.
    config_manager.migrate_memory_files()
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]"


@pytest.mark.unit
def test_staging_never_appears_in_the_character_namespace(tmp_path):
    """Staging lives beside memory/, not inside it.

    Every problem found with staging in this branch traced back to keeping it
    inside the character namespace: a dot-prefixed character name is legal, so
    any name there could collide with a real character, a name-matched sweep
    deleted one, and the ownership marker meant to replace that was a filename
    ordinary contents could reproduce. Out of the namespace, there is nothing
    to collide with and nothing to forge.
    """
    from utils.config_manager.migrations import _MIGRATION_STAGING_DIR

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    # A character whose name looks exactly like the staging directory, holding
    # the only copy of its memory.
    impostor = runtime_root / _MIGRATION_STAGING_DIR
    impostor.mkdir()
    (impostor / "facts.json").write_text('["only copy"]', encoding="utf-8")

    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    config_manager.migrate_memory_files()

    assert (impostor / "facts.json").read_text(
        encoding="utf-8"
    ) == '["only copy"]', "a real character was touched by staging cleanup"
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]"
    assert sorted(p.name for p in runtime_root.iterdir()) == [
        _MIGRATION_STAGING_DIR,
        "Carol",
    ], "staging left something in the character namespace"


@pytest.mark.unit
def test_staging_workspaces_do_not_accumulate_across_kills(tmp_path):
    """A run killed outright leaves a workspace; a later run clears it.

    Both directions, because they pull against each other. The parent is
    reachable by a second PROCESS -- _MIGRATION_LOCK covers threads only and
    the single-instance lock can fail open -- so removing the parent whole,
    which is what this used to do, deleted the other run's live copy out from
    under it. But leaving everything would let full memory copies pile up
    until the disk fills.

    So: this run's own workspace goes, stale siblings go, a FRESH sibling
    stays, and the parent goes only once nothing is left in it.
    """
    from utils.config_manager import migrations as migrations_module
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_DIR,
        _MIGRATION_STAGING_STALE_SECONDS,
        _MIGRATION_WORKSPACE_LOCK_NAME,
        _MIGRATION_WORKSPACE_PREFIX,
    )

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    parent = Path(config_manager.app_docs_dir) / _MIGRATION_STAGING_DIR

    # Two runs killed before cleanup. Only the final cleanup is disabled: a
    # real kill does not stop the NEXT run from using rmtree, and neutering it
    # for the whole run would disable the clearing under test.
    for _ in range(2):
        with patch.object(
            migrations_module.shutil, "rmtree", lambda *a, **k: None
        ):
            config_manager.migrate_memory_files()

    assert parent.is_dir(), "the kills left no staging parent at all"
    abandoned = sorted(q.name for q in parent.iterdir())
    assert len(abandoned) >= 1, "the kills left no workspace"
    # Age them past the threshold. A kill does not date its leavings, so the
    # test has to -- and a fresh sibling is indistinguishable from a live one,
    # which is the whole reason the sweep goes by age.
    stale = time.time() - _MIGRATION_STAGING_STALE_SECONDS - 60
    for entry in parent.iterdir():
        os.utime(entry, (stale, stale))

    # A concurrent process, mid-copy. It must survive a run that finishes
    # first, along with the parent it is still using.
    live = parent / (_MIGRATION_WORKSPACE_PREFIX + "other-run")
    live.mkdir()
    (live / "d").mkdir()
    # Marked and aged out on paper -- so what saves it below is its mtime,
    # not a missing marker.
    (live / _MIGRATION_WORKSPACE_LOCK_NAME).write_bytes(b"1")

    # And something that is NOT the shape mkdtemp gives us, aged past the
    # threshold. This name is reserved by the app, but "reserved" is not
    # proof, and ordinary contents are not ours to age out.
    stranger = parent / "not-ours"
    stranger.mkdir()
    os.utime(stranger, (stale, stale))

    config_manager.migrate_memory_files()

    survivors = sorted(q.name for q in parent.iterdir())
    assert survivors == [live.name, "not-ours"], (
        "expected only the concurrent run's workspace and the stranger to "
        "survive, saw %r (kills had left %r)" % (survivors, abandoned)
    )
    assert (live / "d").is_dir(), "a concurrent run's staged copy was deleted"
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]"

    # And once nothing is live, nothing of the migration persists at all.
    shutil.rmtree(live)
    shutil.rmtree(stranger)
    config_manager.migrate_memory_files()
    assert not parent.exists(), (
        "staging survived a clean run: %s"
        % (sorted(q.name for q in parent.iterdir()) if parent.is_dir() else parent)
    )


@pytest.mark.unit
def test_a_memory_dir_on_another_volume_stages_on_that_volume(tmp_path):
    """os.replace and Path.rename raise EXDEV across filesystems.

    memory_dir is normally app_docs_dir/"memory", so the staging sibling is
    the same volume. But memory can be a junction or a mount onto another
    one, and then the sibling is not on the destination's filesystem at all:
    every publish raises EXDEV, the per-entry handler swallows it, and NOTHING
    migrates -- worse than the plain copy2 this replaced, which never needed
    them to match.

    Driven through _same_device rather than a real mount, because a test
    cannot make one. What is pinned is that the workspace moves onto the
    destination's volume and claims no NAME while it is there: ".mig-staging"
    passes validate_character_name(allow_dots=True), so a reserved path
    inside memory_dir is a path that can already be somebody's character.
    Reclaiming them is handled by proving ownership instead -- see
    test_a_cross_device_workspace_is_reclaimed_like_any_other.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")
    (project_root / "loose.json").write_text("[2]", encoding="utf-8")

    staged_in = []
    real_mkdtemp = migrations_module.tempfile.mkdtemp

    def _record(*args, **kwargs):
        staged_in.append(Path(kwargs["dir"]))
        return real_mkdtemp(*args, **kwargs)

    with patch.object(migrations_module.tempfile, "mkdtemp", _record), \
            patch.object(migrations_module, "_same_device", lambda *a: False):
        config_manager.migrate_memory_files()

    assert staged_in == [runtime_root], (
        "the workspace was not put on the destination's volume: %r"
        % (staged_in,)
    )
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]"
    assert (runtime_root / "loose.json").read_text(encoding="utf-8") == "[2]"
    assert sorted(q.name for q in runtime_root.iterdir()) == [
        "Carol",
        "loose.json",
    ], "staging was left behind in the character namespace"

    # The default layout still stages OUTSIDE it -- the fallback is reached
    # only when the devices actually differ, not always.
    staged_in.clear()
    (project_root / "Dave").mkdir()
    (project_root / "Dave" / "facts.json").write_text("[3]", encoding="utf-8")
    with patch.object(migrations_module.tempfile, "mkdtemp", _record):
        config_manager.migrate_memory_files()
    assert staged_in and runtime_root not in staged_in[0].parents, (
        "the default layout staged inside the character namespace: %r"
        % (staged_in,)
    )


@pytest.mark.unit
def test_a_writeback_failure_abandons_the_entry_instead_of_publishing_it(tmp_path):
    """ENOSPC and EIO are what the flush exists to catch.

    Swallowing them publishes a tree whose delayed writes never reached
    storage -- and because the migration skips a destination that exists,
    every later start then treats that damaged copy as authoritative and
    never retries it. That is the exact failure staging was added to remove,
    reintroduced one level down.

    Both branches: the loose file flushes its staged copy directly, the
    directory branch flushes a whole tree, and neither may rename afterwards.
    """
    import errno

    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")
    (project_root / "loose.json").write_text("[2]", encoding="utf-8")

    def _no_space(path):
        raise OSError(errno.ENOSPC, "No space left on device")

    with patch.object(migrations_module, "_fsync_file", _no_space):
        # Must not raise either -- the caller is startup.
        config_manager.migrate_memory_files()

    assert not (runtime_root / "Carol").exists(), (
        "a tree whose writeback failed was published anyway"
    )
    assert not (runtime_root / "loose.json").exists(), (
        "a file whose writeback failed was published anyway"
    )

    # And it is a real abandonment, not a permanent one: the same seed
    # migrates on the next start once the disk is healthy again.
    config_manager.migrate_memory_files()
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]"
    assert (runtime_root / "loose.json").read_text(encoding="utf-8") == "[2]"


@pytest.mark.unit
def test_staging_is_reclaimed_even_when_the_seed_root_is_gone(tmp_path):
    """The missing-seed return happens before anything is staged.

    A run killed after copying a large character tree leaves it in the
    user-data root. If the next installed build no longer ships memory/store,
    a reclaim that only ran when THIS run had staged something would never be
    reached again, and the copy would sit there indefinitely.
    """
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_DIR,
        _MIGRATION_STAGING_STALE_SECONDS,
        _MIGRATION_WORKSPACE_LOCK_NAME,
        _MIGRATION_WORKSPACE_PREFIX,
    )

    config_manager = _make_config_manager(tmp_path)
    runtime_root = tmp_path / "runtime-memory"
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    # This build ships no seed at all.
    config_manager.project_memory_dir = tmp_path / "no-such-project-memory"

    parent = Path(config_manager.app_docs_dir) / _MIGRATION_STAGING_DIR
    abandoned = parent / (_MIGRATION_WORKSPACE_PREFIX + "killed")
    (abandoned / "d" / "Carol").mkdir(parents=True)
    (abandoned / "d" / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")
    # The marker a real run leaves. Reclamation asks for it in BOTH parents,
    # so a fixture without one is not testing the reclaim at all.
    (abandoned / _MIGRATION_WORKSPACE_LOCK_NAME).write_bytes(b"1")
    stale = time.time() - _MIGRATION_STAGING_STALE_SECONDS - 60
    for path in (abandoned / "d" / "Carol", abandoned / "d", abandoned):
        os.utime(path, (stale, stale))

    config_manager.migrate_memory_files()

    assert not parent.exists(), (
        "a killed run's copy survived a build with no seed root: %s"
        % (sorted(q.name for q in parent.iterdir()) if parent.is_dir() else parent)
    )


@pytest.mark.unit
def test_a_symlinked_staging_parent_is_not_followed(tmp_path):
    """The staging parent must not be a route out of the writable tree."""
    from utils.config_manager.migrations import _MIGRATION_STAGING_DIR

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    outside = tmp_path / "outside-target"
    outside.mkdir()
    parent = Path(config_manager.app_docs_dir) / _MIGRATION_STAGING_DIR
    parent.parent.mkdir(parents=True, exist_ok=True)
    try:
        parent.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform will not create symlinks unprivileged")

    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    from utils.config_manager import migrations as migrations_module

    staged_in = []
    real_mkdtemp = migrations_module.tempfile.mkdtemp

    def _record(*args, **kwargs):
        staged_in.append(Path(kwargs["dir"]))
        return real_mkdtemp(*args, **kwargs)

    with patch.object(migrations_module.tempfile, "mkdtemp", _record):
        config_manager.migrate_memory_files()

    # An empty target at the end proves nothing on its own -- the workspace is
    # removed in the finally either way -- so what is pinned is where it was
    # MINTED. Not through the link; and the link is not deleted either, being
    # data the migration cannot identify. On Windows that needs saying twice:
    # rmdir removes a directory symlink outright, so reclamation has to refuse
    # the same path preparation refused.
    assert staged_in and staged_in[0] == Path(config_manager.app_docs_dir), (
        "the workspace was not minted beside the link: %r" % (staged_in,)
    )
    assert parent.is_symlink(), (
        "the migration deleted whatever was holding the reserved name"
    )
    assert outside.is_dir(), "the link target itself was destroyed"
    assert list(outside.iterdir()) == [], (
        "content was staged through the link: %s"
        % [q.name for q in outside.iterdir()]
    )
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "the migration did not complete after replacing the link"


@pytest.mark.unit
def test_the_migration_body_runs_under_the_lock(tmp_path):
    """Two threads can reach the migration, and they must not overlap.

    ``config_manager/__init__.py`` notes that ``_config_manager_migrated``
    is not thread-safe, so both can enter. Unserialised they clear each
    other's staging while it is still being copied.

    Asserted by observing the lock from inside the body rather than by
    racing two threads, which could pass by luck.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    config_manager.project_memory_dir = tmp_path / "project-memory"
    config_manager.memory_dir = tmp_path / "runtime-memory"
    (tmp_path / "runtime-memory").mkdir(parents=True, exist_ok=True)

    seen = {}

    def _record(self):
        seen["locked"] = migrations_module._MIGRATION_LOCK.locked()

    with patch.object(
        type(config_manager), "_migrate_memory_files_unlocked", _record
    ):
        config_manager.migrate_memory_files()

    assert seen.get("locked") is True, (
        "the body ran without the migration lock held"
    )
    assert not migrations_module._MIGRATION_LOCK.locked(), (
        "the lock was not released"
    )


@pytest.mark.unit
def test_an_unreadable_project_root_does_not_take_startup_down(tmp_path):
    """Probing a path can raise, and this runs on the startup path.

    A permission problem or an unreadable component makes ``exists()`` throw
    rather than return False. Outside the handler that failed the launch
    instead of skipping one migration.
    """
    config_manager = _make_config_manager(tmp_path)
    runtime_root = tmp_path / "runtime-memory"
    runtime_root.mkdir(parents=True, exist_ok=True)
    config_manager.memory_dir = runtime_root

    class _Unreadable:
        def exists(self):
            raise PermissionError("cannot stat the project memory root")

        def iterdir(self):
            raise PermissionError("cannot list the project memory root")

        def __truediv__(self, other):
            return self

    config_manager.project_memory_dir = _Unreadable()

    # Must not raise. The caller is startup.
    config_manager.migrate_memory_files()


@pytest.mark.unit
def test_a_plain_file_at_the_staging_name_does_not_stop_the_migration(tmp_path):
    """mkdir(exist_ok=True) raises FileExistsError when the path is a FILE.

    That ends the whole loop, so nothing migrates -- on every start, forever,
    because nothing clears the squatter. Verified: mkdir(parents=True,
    exist_ok=True) over a plain file raises rather than passing.

    And the squatter SURVIVES. Removing it would be the migration destroying
    data it cannot identify, and no ownership marker settles that -- a fixed
    filename is something ordinary contents can reproduce, which is the
    argument that removed the sentinel earlier in this branch. The workspace
    is minted one level up instead, on the same volume, under a name that
    claims nothing.
    """
    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")
    (project_root / "loose.json").write_text("[2]", encoding="utf-8")

    squatter = Path(config_manager.app_docs_dir) / ".mig-staging"
    squatter.parent.mkdir(parents=True, exist_ok=True)
    squatter.write_text("not a directory", encoding="utf-8")

    config_manager.migrate_memory_files()

    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "a squatting file stopped the whole migration"
    assert (runtime_root / "loose.json").read_text(encoding="utf-8") == "[2]"
    assert squatter.read_text(encoding="utf-8") == "not a directory", (
        "the migration deleted whatever was holding the reserved name"
    )
    # And it worked around it rather than beside it: the fallback workspace
    # is gone, leaving only the squatter itself.
    assert sorted(
        q.name
        for q in Path(config_manager.app_docs_dir).iterdir()
        if q.name.startswith(".mig-")
    ) == [".mig-staging"], "the fallback workspace was left behind"


@pytest.mark.unit
def test_the_publish_is_flushed_on_both_branches(tmp_path):
    """os.replace publishes a NAME, and the name lives in the parent directory.

    Flushing the staged data is only half of it -- a power loss can still lose
    the entry. And since the migration skips a destination that exists, a
    half-published name is not something a later start repairs.

    Asserted through the calls rather than by pulling the power: the file
    branch flushes its staged file and then the destination's parent, and the
    directory branch flushes the staged tree and then the parent. Directory
    flushing is best effort -- Windows cannot open a directory for reading at
    all -- so what is pinned is that it is ATTEMPTED, not that it succeeds.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")
    (project_root / "loose.json").write_text("[2]", encoding="utf-8")

    flushed_dirs = []
    flushed_trees = []
    real_dir = migrations_module._fsync_directory
    real_tree = migrations_module._fsync_tree

    def _record_dir(path):
        flushed_dirs.append(Path(path))
        return real_dir(path)

    def _record_tree(path, *args, **kwargs):
        flushed_trees.append(Path(path))
        return real_tree(path, *args, **kwargs)

    with patch.object(migrations_module, "_fsync_directory", _record_dir),             patch.object(migrations_module, "_fsync_tree", _record_tree):
        config_manager.migrate_memory_files()

    assert (runtime_root / "Carol" / "facts.json").exists()
    assert (runtime_root / "loose.json").exists()
    assert flushed_trees, "the staged directory tree was never flushed"
    # ONCE PER BRANCH -- the fixture publishes one directory and one loose
    # file, and each has to flush the destination parent after its own
    # rename. Asserting mere membership would pass with either branch
    # missing it, which is how the first version of this guard let a
    # removal through.
    assert flushed_dirs.count(runtime_root) == 2, (
        "expected the destination parent flushed once per publish, saw %r"
        % (flushed_dirs,)
    )


@pytest.mark.unit
def test_a_read_only_seed_still_migrates(tmp_path):
    """copy2 preserves the mode, so the staged copy of a read-only seed is too.

    Opening it "rb+" to flush then raises PermissionError, the per-entry handler
    discards the stage, and the file never migrates -- which the plain copy2
    this replaced handled fine. Packaged and checked-out seeds are read-only
    often enough that this is the common case, not the exotic one.

    Opening READ-ONLY instead does not work here, which is why the fix widens
    the mode temporarily: measured on Windows, both open(path, "rb") and
    os.open(O_RDONLY) followed by fsync raise OSError EBADF.

    Both branches are covered -- a loose file and one inside a directory -- and
    the published mode is asserted, because widening it permanently would be a
    quieter bug than the one being fixed.
    """
    import os
    import stat

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    (project_root / "Carol").mkdir(parents=True)
    nested = project_root / "Carol" / "facts.json"
    nested.write_text("[1]", encoding="utf-8")
    loose = project_root / "loose.json"
    loose.write_text("[2]", encoding="utf-8")
    for path in (nested, loose):
        os.chmod(path, stat.S_IREAD)

    config_manager.migrate_memory_files()

    assert (runtime_root / "loose.json").read_text(encoding="utf-8") == "[2]", (
        "a read-only loose seed never migrated"
    )
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "a read-only file inside a seed directory never migrated"
    assert not (
        stat.S_IMODE(os.stat(runtime_root / "loose.json").st_mode) & stat.S_IWRITE
    ), "the published file was left writable"


@pytest.mark.unit
def test_widening_a_staged_file_grants_read_as_well_as_write(tmp_path):
    """"rb+" needs BOTH bits, so adding only the write one changes nothing.

    A seed installed by another user can arrive group- or other-readable with
    the owner bit clear. copy2 reads it fine through the bit it does have and
    hands back a staged copy WE own and cannot open -- and widening it to
    "read-only plus write" leaves the second open failing exactly like the
    first, so the file branch drops the entry and the directory branch aborts
    the tree.

    Driven by making the first open fail rather than by a real mode, because
    which modes reject "rb+" is a platform question -- Windows honours only
    the write bit, and POSIX run as root honours neither.
    """
    import stat as stat_module

    from utils.config_manager import migrations as migrations_module

    staged = tmp_path / "seed.json"
    staged.write_text("[1]", encoding="utf-8")

    # Group-read only: what copy2 hands back from a seed another user
    # installed. The mode is REPORTED rather than applied -- Windows chmod
    # honours nothing but the write bit, so the real file could never carry
    # it, and a guard built on the real mode passes whatever the code does.
    # That is not hypothetical: the first version of this test did exactly
    # that and survived its own mutation.
    borrowed = 0o040

    widened = []
    real_stat = migrations_module.os.stat
    real_open = open
    opens = []

    def _record_chmod(path, mode):
        widened.append(mode)

    def _borrowed_mode(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        return os.stat_result((borrowed,) + tuple(result)[1:10])

    def _refused_until_widened(path, mode="r", *args, **kwargs):
        opens.append(mode)
        if not widened:
            raise PermissionError(13, "Permission denied")
        return real_open(path, mode, *args, **kwargs)

    with patch("builtins.open", _refused_until_widened), patch.object(
        migrations_module.os, "chmod", _record_chmod
    ), patch.object(migrations_module.os, "stat", _borrowed_mode):
        migrations_module._fsync_file(str(staged))

    assert widened, "the mode was never widened, so nothing here is tested"
    assert widened[0] & stat_module.S_IREAD, (
        "widened to %o, which still cannot be opened for READING -- 'rb+' "
        "needs both bits and this mode arrived without the owner read one"
        % widened[0]
    )
    assert widened[0] & stat_module.S_IWRITE, (
        "widened to %o, which still cannot be opened for writing" % widened[0]
    )
    assert widened[-1] == borrowed, (
        "the widened mode was not put back: %o" % widened[-1]
    )
    assert len(opens) == 2, (
        "expected one refused open and one retry, saw %r" % (opens,)
    )


@pytest.mark.unit
def test_a_busy_publish_is_retried_rather_than_dropped(tmp_path):
    """Windows lets a scanner hold the staged file for a moment.

    os.replace then fails with a sharing violation, the per-entry handler
    discards a stage that was already complete, and because migration is
    marked done for the process the seed never arrives for the whole session.
    utils/file_utils already backs off over exactly this window, so the
    publish goes through it rather than carrying a second copy of the error
    codes and delays.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")
    (project_root / "loose.json").write_text("[2]", encoding="utf-8")

    # Per DESTINATION, not one shared budget. With a shared one the first
    # entry spends it and the second publishes cleanly, so removing the
    # backoff from either branch still passed -- which is how the first
    # version of this guard survived its own mutation.
    #
    # The two branches publish through different primitives: a directory
    # replaces, a loose FILE must not, so it goes through the no-replace
    # publish -- os.rename on Windows, os.link on POSIX. All three are
    # wrapped, and the assertion below is on destinations rather than on
    # which call fired, so it holds on either platform.
    real = {name: getattr(os, name) for name in ("replace", "rename", "link")}
    attempts = {}

    def _held_open(name):
        def _call(src, dst, *args, **kwargs):
            key = str(dst)
            attempts[key] = attempts.get(key, 0) + 1
            if attempts[key] <= 2:
                error = OSError(13, "The process cannot access the file")
                error.winerror = 32
                raise error
            return real[name](src, dst, *args, **kwargs)

        return _call

    with patch.object(
        migrations_module.os, "replace", _held_open("replace")
    ), patch.object(
        migrations_module.os, "rename", _held_open("rename")
    ), patch.object(migrations_module.os, "link", _held_open("link")):
        config_manager.migrate_memory_files()

    assert sorted(attempts) == sorted(
        [str(runtime_root / "Carol"), str(runtime_root / "loose.json")]
    ), "a publish did not go through the shared backoff: %r" % (attempts,)
    assert set(attempts.values()) == {3}, (
        "expected two refusals then success on each publish, saw %r"
        % (attempts,)
    )
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "a directory publish was dropped on a transient sharing error"
    assert (runtime_root / "loose.json").read_text(encoding="utf-8") == "[2]", (
        "a file publish was dropped on a transient sharing error"
    )


@pytest.mark.unit
def test_a_cross_device_workspace_is_reclaimed_like_any_other(tmp_path):
    """A killed run on the junction layout must not leave its copy for good.

    A killed run must not leave its copy of a character tree in the
    destination volume for good -- every run picks a fresh name, so repeated
    interrupted starts would fill it. But the parent here IS the character
    namespace, and NOTHING on disk inside it can prove a directory was ever
    a workspace: a legal character name may start with ".mig-", and a
    character directory may hold a file called ".lock". Name and marker are
    shapes anyone can reproduce, and reclaiming on the strength of them
    means deleting a whole character.

    So the only claim a character cannot make is the one used: a previous
    run WROTE THE PATH DOWN, in the directory beside the namespace that is
    ours. Both directions are pinned -- a recorded workspace is reclaimed,
    and a directory carrying the prefix AND the marker but no record is left
    exactly where it is.
    """
    from utils.config_manager import migrations as migrations_module
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_STALE_SECONDS,
        _MIGRATION_WORKSPACE_LOCK_NAME,
        _MIGRATION_WORKSPACE_PREFIX,
    )

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    # A run KILLED before cleanup, driven through the real code so the record
    # is written by the production path rather than by this test -- writing
    # it here is what let the first version of this guard survive removing
    # the write entirely. Only the final cleanup is disabled: a real kill
    # does not stop the NEXT run from using rmtree.
    with patch.object(migrations_module, "_same_device", lambda *a: False), \
            patch.object(migrations_module.shutil, "rmtree", lambda *a, **k: None):
        config_manager.migrate_memory_files()

    leavings = [
        entry
        for entry in runtime_root.iterdir()
        if entry.name.startswith(_MIGRATION_WORKSPACE_PREFIX)
    ]
    assert len(leavings) == 1, (
        "the kill left %r, so there is nothing to reclaim below" % (leavings,)
    )
    abandoned = leavings[0]
    assert (abandoned / _MIGRATION_WORKSPACE_LOCK_NAME).exists()

    # A character that looks EXACTLY like a workspace -- the prefix and the
    # marker -- and was never recorded, because we never made it. This is
    # the case name-and-marker could not tell apart.
    impostor = runtime_root / (_MIGRATION_WORKSPACE_PREFIX + "character")
    (impostor / "semantic_memory").mkdir(parents=True)
    (impostor / _MIGRATION_WORKSPACE_LOCK_NAME).write_bytes(b"1")
    (impostor / "facts.json").write_text("[keep]", encoding="utf-8")

    stale = time.time() - _MIGRATION_STAGING_STALE_SECONDS - 60
    for path in (abandoned, impostor):
        os.utime(path, (stale, stale))

    with patch.object(migrations_module, "_same_device", lambda *a: False):
        config_manager.migrate_memory_files()

    assert not abandoned.exists(), (
        "a killed cross-device run's copy was never reclaimed"
    )
    assert (impostor / "facts.json").read_text(encoding="utf-8") == "[keep]", (
        "a character carrying the workspace prefix AND a .lock was deleted "
        "on the strength of its shape"
    )
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "the seed never arrived across the two runs"
    assert sorted(q.name for q in runtime_root.iterdir()) == sorted(
        [impostor.name, "Carol"]
    ), "staging was left behind in the character namespace"
    # And the record is forgotten with it, rather than growing forever.
    ledger = config_manager._migration_ledger_path()
    assert not ledger.exists() or str(abandoned) not in ledger.read_text(
        encoding="utf-8"
    ), "the reclaimed workspace is still recorded"


@pytest.mark.unit
def test_a_locked_workspace_is_never_aged_out(tmp_path):
    """Age alone cannot tell a stale workspace from a slow one.

    A run that spends longer than the threshold on a single entry -- or one
    suspended along with the machine -- looks exactly like one that was
    killed, and its top-level mtime does not move on its own during a deep
    copytree. A second process, which the fail-open single-instance lock
    makes reachable, would then delete a workspace still being written into.

    So a sibling has to be BOTH aged out and unlocked. Either condition
    saying "leave it" is enough, which is why both are kept.
    """
    from utils.config_manager import migrations as migrations_module
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_DIR,
        _MIGRATION_STAGING_STALE_SECONDS,
        _MIGRATION_WORKSPACE_LOCK_NAME,
        _MIGRATION_WORKSPACE_PREFIX,
    )

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    parent = Path(config_manager.app_docs_dir) / _MIGRATION_STAGING_DIR
    parent.mkdir(parents=True, exist_ok=True)

    # Two aged workspaces. One is held by a "live" run, one is not.
    held = parent / (_MIGRATION_WORKSPACE_PREFIX + "still-running")
    dropped = parent / (_MIGRATION_WORKSPACE_PREFIX + "killed-run")
    # And one that carries the name but not the marker, so nothing proves it
    # was ever ours. The rule is the same in both parents, and here it costs
    # only a directory; inside memory_dir the same rule is what stands
    # between the sweep and somebody's character.
    unproven = parent / (_MIGRATION_WORKSPACE_PREFIX + "not-proven")
    for workspace in (held, dropped, unproven):
        (workspace / "d").mkdir(parents=True)
        (workspace / "d" / "big.json").write_text("[9]", encoding="utf-8")

    handle = migrations_module._claim_workspace(held)
    if handle is None:
        pytest.skip("this filesystem will not hold an advisory lock")
    # A killed run leaves its marker behind and holds nothing, which is
    # exactly what the age check is for.
    (dropped / _MIGRATION_WORKSPACE_LOCK_NAME).write_bytes(b"1")

    stale = time.time() - _MIGRATION_STAGING_STALE_SECONDS - 60
    for workspace in (held, dropped, unproven):
        os.utime(workspace, (stale, stale))

    try:
        config_manager.migrate_memory_files()
    finally:
        handle.close()

    assert held.is_dir() and (held / "d" / "big.json").exists(), (
        "an aged but LOCKED workspace was deleted out from under its owner"
    )
    assert unproven.is_dir(), (
        "a directory with no marker was swept on the strength of its name"
    )
    assert not dropped.exists(), (
        "an aged, unlocked workspace was not reclaimed"
    )
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]"


@pytest.mark.unit
def test_one_read_only_entry_does_not_strand_the_entries_after_it(tmp_path):
    """Windows will not unlink a read-only file, and ignore_errors hides it.

    Every directory entry stages under the same name inside the run
    workspace, so a failed entry that leaves its tree standing makes every
    LATER character fail with FileExistsError -- one bad entry stranding all
    the rest, which is the exact failure the per-entry handler exists to
    prevent. Measured before the fix: the second entry died on WinError 183.
    """
    import stat as stat_module

    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    # "Alpha" sorts first, holds a read-only file, and fails to publish.
    (project_root / "Alpha" / "sub").mkdir(parents=True)
    locked = project_root / "Alpha" / "sub" / "facts.json"
    locked.write_text("[1]", encoding="utf-8")
    os.chmod(locked, stat_module.S_IREAD)
    for name in ("Beta", "Gamma"):
        (project_root / name).mkdir(parents=True)
        (project_root / name / "facts.json").write_text("[2]", encoding="utf-8")

    real_replace = migrations_module.replace_with_busy_retry

    def _alpha_cannot_publish(source, destination, *args, **kwargs):
        if Path(destination).name == "Alpha":
            raise OSError(28, "No space left on device")
        return real_replace(source, destination, *args, **kwargs)

    parent = Path(config_manager.app_docs_dir) / ".mig-staging"
    try:
        with patch.object(
            migrations_module, "replace_with_busy_retry", _alpha_cannot_publish
        ):
            config_manager.migrate_memory_files()

        assert not (runtime_root / "Alpha").exists(), (
            "the entry whose publish failed was published anyway"
        )
        for name in ("Beta", "Gamma"):
            assert (runtime_root / name / "facts.json").read_text(
                encoding="utf-8"
            ) == "[2]", (
                "%s was stranded by the read-only leftovers of an earlier "
                "entry" % name
            )
        # And the leftovers are GONE, not merely stepped around. The run
        # continuing is the jam guard's doing; this is the removal's, and
        # without asserting it the two are indistinguishable -- which is
        # how the first version of this guard survived its own mutation.
        assert not parent.exists(), (
            "read-only leftovers kept the staging parent alive: %s"
            % (
                sorted(str(q.relative_to(parent)) for q in parent.rglob("*"))
                if parent.is_dir()
                else parent
            )
        )
    finally:
        os.chmod(locked, stat_module.S_IREAD | stat_module.S_IWRITE)
        _force = migrations_module._force_rmtree
        if parent.exists():
            _force(parent)


@pytest.mark.unit
def test_a_destination_appearing_while_staging_is_still_authoritative(tmp_path):
    """"Not in the runtime root" never meant "it was never migrated".

    A cloud import deliberately deletes managed files, and users delete them
    too, so an existing runtime entry is authoritative and is left alone. The
    check for that ran before the copy, and the copy is the slow part -- so
    an entry created during it was overwritten by the seed. Both branches
    re-check at the last moment now.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[seed]", encoding="utf-8")
    (project_root / "loose.json").write_text("[seed]", encoding="utf-8")

    real_copytree = migrations_module.shutil.copytree
    real_replace = migrations_module.replace_with_busy_retry
    published = []

    def _someone_else_gets_there_first_tree(source, destination, *a, **k):
        result = real_copytree(source, destination, *a, **k)
        winner = runtime_root / "Carol"
        winner.mkdir(parents=True, exist_ok=True)
        (winner / "facts.json").write_text("[live]", encoding="utf-8")
        return result

    # The flat-file branch copies through ``_copy_with_heartbeat`` now, so
    # the real copier comes from there rather than from ``shutil.copy2``.
    # Patching copy2 stopped intercepting the loose-file copy entirely.
    # Captured BEFORE the patch: reaching for it inside the factory calls the
    # patched name and recurses until the migration reports "maximum
    # recursion depth exceeded" and swallows the entry.
    _real_heartbeat_copy = migrations_module._copy_with_heartbeat(lambda: None)

    def _copier_that_lets_someone_else_win(_beat):
        real_copy = _real_heartbeat_copy

        def _copy(source, destination, *a, **k):
            result = real_copy(source, destination, *a, **k)
            (runtime_root / "loose.json").write_text("[live]", encoding="utf-8")
            return result

        return _copy

    def _record_publish(source, destination, *a, **k):
        published.append(Path(destination).name)
        return real_replace(source, destination, *a, **k)

    with patch.object(
        migrations_module.shutil, "copytree", _someone_else_gets_there_first_tree
    ), patch.object(
        migrations_module, "_copy_with_heartbeat",
        _copier_that_lets_someone_else_win,
    ), patch.object(
        migrations_module, "replace_with_busy_retry", _record_publish
    ):
        config_manager.migrate_memory_files()

    # The DECISION is what is pinned, not what the platform happens to do
    # with the rename afterwards. Windows refuses a directory destination
    # outright, so an outcome-only assertion passed with the re-check
    # removed -- and the case the finding is really about, POSIX replacing
    # an EMPTY directory, cannot be reproduced here at all.
    assert published == [], (
        "the migration tried to publish over entries that appeared while it "
        "was staging: %r" % (published,)
    )
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[live]", "the seed replaced a runtime entry that appeared while staging"
    assert (runtime_root / "loose.json").read_text(
        encoding="utf-8"
    ) == "[live]", "the seed replaced a runtime file that appeared while staging"


@pytest.mark.unit
def test_a_leftover_at_the_staging_name_does_not_stop_the_run(tmp_path):
    """Every directory entry stages under the same name inside the workspace.

    Sharing one name is what keeps the staged path short -- a per-entry
    subdirectory put the longest component of all on every descendant -- but
    it means a leftover at that name jams every entry after it. A run killed
    mid-copy leaves exactly that.

    BOTH ways out are pinned here, because they are layered: the leftover is
    removed, and if it will not go, the entry mints its own name rather than
    letting one poisoned path take the whole run down. The second is tested
    by making removal do nothing at all, which is what an unremovable tree
    amounts to.
    """
    from utils.config_manager import migrations as migrations_module

    def _run(disable_removal):
        config_manager = _make_config_manager(tmp_path / str(disable_removal))
        project_root = tmp_path / str(disable_removal) / "project-memory"
        runtime_root = tmp_path / str(disable_removal) / "runtime-memory"
        config_manager.project_memory_dir = project_root
        config_manager.memory_dir = runtime_root
        runtime_root.mkdir(parents=True, exist_ok=True)
        for name in ("Alpha", "Beta"):
            (project_root / name).mkdir(parents=True)
            (project_root / name / "facts.json").write_text(
                "[1]", encoding="utf-8"
            )

        real_prepare = type(config_manager)._prepare_migration_staging_root

        def _prepare_with_a_leftover(manager):
            workspace = real_prepare(manager)
            # What a run killed mid-copy leaves behind.
            (workspace / "d" / "half-copied").mkdir(parents=True)
            return workspace

        patches = [
            patch.object(
                type(config_manager),
                "_prepare_migration_staging_root",
                _prepare_with_a_leftover,
            )
        ]
        if disable_removal:
            patches.append(
                patch.object(
                    migrations_module, "_force_rmtree", lambda *a, **k: None
                )
            )
        for entered in patches:
            entered.start()
        try:
            config_manager.migrate_memory_files()
        finally:
            for entered in patches:
                entered.stop()
        return runtime_root

    for disable_removal in (False, True):
        runtime_root = _run(disable_removal)
        for name in ("Alpha", "Beta"):
            assert (runtime_root / name / "facts.json").read_text(
                encoding="utf-8"
            ) == "[1]", (
                "%s was jammed by a leftover at the staging name "
                "(removal disabled: %s)" % (name, disable_removal)
            )


@pytest.mark.unit
def test_a_loose_file_that_appears_at_the_last_moment_is_not_overwritten(
    tmp_path,
):
    """os.replace overwrites, and the destination can appear after the check.

    Another application process, or a cloud import writing back the managed
    file it is restoring. The rule this migration works to is that an
    existing runtime entry is authoritative -- "not in the runtime root"
    never meant "never migrated" -- so losing that race has to mean
    abandoning the seed, not overwriting the winner.

    The re-check narrows the window; it cannot close it, which is why the
    publish itself refuses an existing name.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "loose.json").write_text("[seed]", encoding="utf-8")

    real_publish = migrations_module.publish_without_replacing

    def _the_winner_arrives_first(source, destination, *args, **kwargs):
        # After every check this migration makes, and before the move.
        Path(destination).write_text("[live]", encoding="utf-8")
        return real_publish(source, destination, *args, **kwargs)

    with patch.object(
        migrations_module,
        "publish_without_replacing",
        _the_winner_arrives_first,
    ):
        # Must not raise: the caller is startup.
        config_manager.migrate_memory_files()

    assert (runtime_root / "loose.json").read_text(encoding="utf-8") == "[live]", (
        "the seed overwrote a runtime file that appeared during publication"
    )


@pytest.mark.unit
def test_preparing_the_workspace_survives_a_concurrent_cleanup(tmp_path):
    """The parent is empty between creating it and minting inside it.

    So another run's reclamation can rmdir it in that window -- its rmdir
    only succeeds when it IS empty, which is exactly then. Without the retry
    mkdtemp raises FileNotFoundError, every seed entry is skipped, and the
    process is still marked migrated for the rest of its session.
    """
    from utils.config_manager import migrations as migrations_module
    from utils.config_manager.migrations import _MIGRATION_STAGING_DIR

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    parent = Path(config_manager.app_docs_dir) / _MIGRATION_STAGING_DIR
    real_mkdtemp = migrations_module.tempfile.mkdtemp
    interfered = []

    def _somebody_else_reclaims_it(*args, **kwargs):
        if not interfered and Path(kwargs.get("dir", "")) == parent:
            interfered.append(True)
            # Exactly what the other run's cleanup does to an empty parent.
            parent.rmdir()
        return real_mkdtemp(*args, **kwargs)

    with patch.object(
        migrations_module.tempfile, "mkdtemp", _somebody_else_reclaims_it
    ):
        config_manager.migrate_memory_files()

    assert interfered, "the race this guards was never triggered"
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "a concurrent cleanup skipped the whole migration"


@pytest.mark.unit
def test_a_long_copy_keeps_its_workspace_from_ageing_out(tmp_path):
    """A directory's mtime does not move while a deep copytree fills it.

    The lock is the real answer to "is this workspace live", but it can fail
    to be taken at all -- a filesystem that will not hold one, a marker that
    cannot be created. Age is then the only thing left, and a single large
    character could pass the threshold while it is still being written and
    be reclaimed out from under itself. Touching the workspace once per
    ENTRY does not help: the run can spend the whole time inside one.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    for index in range(4):
        (project_root / "Carol" / ("part%d.json" % index)).write_text(
            "[1]", encoding="utf-8"
        )

    touched = []
    real_utime = migrations_module.os.utime
    clock = [0.0]

    def _record(path, times=None, **kwargs):
        touched.append(Path(path))
        return real_utime(path, times, **kwargs)

    def _every_call_is_a_minute_later():
        clock[0] += 60.0
        return clock[0]

    with patch.object(migrations_module.os, "utime", _record), patch.object(
        migrations_module.time, "monotonic", _every_call_is_a_minute_later
    ):
        config_manager.migrate_memory_files()

    assert (runtime_root / "Carol" / "part0.json").exists()
    # The WORKSPACE, during the copy -- not just the per-entry touch. There
    # are four files in the one entry, so a heartbeat that only fires
    # between entries cannot produce more than one.
    workspaces = [path for path in touched if path.name.startswith(".mig-")]
    assert len(workspaces) >= 3, (
        "the workspace was not kept alive during the copy itself: %r"
        % (touched,)
    )


@pytest.mark.unit
def test_a_workspace_recorded_before_the_junction_went_is_still_reclaimed(
    tmp_path,
):
    """``memory`` is a junction until the user removes it.

    A cross-device run mints its workspace inside memory_dir and writes the
    path down. If the layout then goes back to same-device, the branch that
    reads the ledger stopped being reached -- so those records had no reader
    left and the workspaces sat in the character namespace for good, which
    is exactly the accumulation the ledger exists to prevent.

    The ledger holds only paths we minted, so reading it on every layout is
    both safe and the only way it can be complete.
    """
    from utils.config_manager import migrations as migrations_module
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_STALE_SECONDS,
        _MIGRATION_WORKSPACE_PREFIX,
    )

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    # A cross-device run, killed before cleanup.
    with patch.object(migrations_module, "_same_device", lambda *a: False), \
            patch.object(migrations_module.shutil, "rmtree", lambda *a, **k: None):
        config_manager.migrate_memory_files()

    leavings = [
        entry
        for entry in runtime_root.iterdir()
        if entry.name.startswith(_MIGRATION_WORKSPACE_PREFIX)
    ]
    assert len(leavings) == 1, "the kill left %r" % (leavings,)
    abandoned = leavings[0]
    stale = time.time() - _MIGRATION_STAGING_STALE_SECONDS - 60
    os.utime(abandoned, (stale, stale))

    # The junction is gone. Same-device from here on.
    config_manager.migrate_memory_files()

    assert not abandoned.exists(), (
        "a workspace recorded under the old layout was left in the character "
        "namespace with no reader for its record"
    )


@pytest.mark.unit
def test_the_migration_lock_is_replaced_after_a_fork():
    """app/main_server sets the multiprocessing start method to "fork".

    So this is reachable rather than theoretical. fork copies the lock in
    whatever state it was in but not the thread holding it, and a child that
    forked mid-migration would block in migrate_memory_files for good with
    nothing left to release it.

    Driven through the hook rather than through a real fork, because the
    platform this runs on has none -- what is pinned is that the child ends
    up with a lock it can take.
    """
    from utils.config_manager import migrations as migrations_module

    original = migrations_module._MIGRATION_LOCK
    original.acquire()
    try:
        assert not original.acquire(blocking=False), (
            "the fixture did not actually hold the lock"
        )
        migrations_module._reset_migration_lock_after_fork()
        inherited = migrations_module._MIGRATION_LOCK
        assert inherited is not original, "the child kept the parent's lock"
        assert inherited.acquire(blocking=False), (
            "the child inherited a HELD lock, so every migration there blocks"
        )
        inherited.release()
    finally:
        original.release()
        migrations_module._MIGRATION_LOCK = original


@pytest.mark.unit
def test_a_stray_file_in_the_staging_parent_is_left_alone(tmp_path):
    """Nothing here ever mints a FILE in the parent.

    Staged files go inside the run workspace, so the arm that unlinked aged
    dot-prefixed non-directories could only ever have deleted something that
    was not ours -- and a marker is not a thing a file can carry, so the
    ownership rule that protects directories had nothing to say about it.

    The cost is that a stray file keeps the parent alive, because rmdir
    wants it empty. That is the right way round.
    """
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_DIR,
        _MIGRATION_STAGING_STALE_SECONDS,
        _MIGRATION_WORKSPACE_PREFIX,
    )

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    parent = Path(config_manager.app_docs_dir) / _MIGRATION_STAGING_DIR
    parent.mkdir(parents=True, exist_ok=True)
    # Named like a workspace, because anything else never reaches the arm
    # under test -- the prefix check drops it first. This is the file that
    # WOULD have been unlinked, and the one a marker cannot speak for.
    stray = parent / (_MIGRATION_WORKSPACE_PREFIX + "someone-elses-cache")
    stray.write_text("not ours", encoding="utf-8")
    stale = time.time() - _MIGRATION_STAGING_STALE_SECONDS - 60
    os.utime(stray, (stale, stale))

    config_manager.migrate_memory_files()

    assert stray.read_text(encoding="utf-8") == "not ours", (
        "an aged file in the staging parent was deleted"
    )
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "the stray file stopped the migration"


@pytest.mark.unit
def test_a_ledger_line_is_not_a_licence_to_delete(tmp_path):
    """The record is the one claim a character cannot forge. That is ALL it is.

    Every nonempty line was taken as a path to remove recursively, so a
    "minted" file that predates this migration, is written by something
    else, or is simply corrupted could name any directory on the machine --
    including one outside the application root -- and startup would delete
    it.

    Shape and containment are required as well now. Each condition can only
    ever refuse, so together they are strictly safer than any one alone.
    """
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_STALE_SECONDS,
        _MIGRATION_WORKSPACE_LOCK_NAME,
        _MIGRATION_WORKSPACE_PREFIX,
    )

    config_manager = _make_config_manager(tmp_path)
    runtime_root = tmp_path / "runtime-memory"
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    config_manager.project_memory_dir = tmp_path / "no-such-project-memory"

    # Somewhere else entirely, holding something that matters -- and shaped
    # EXACTLY like a workspace, prefix and marker included. Anything less and
    # the marker check refuses it on its own, so containment would never be
    # the reason and could be removed with this test still passing.
    elsewhere = tmp_path / (_MIGRATION_WORKSPACE_PREFIX + "not-the-app-root")
    elsewhere.mkdir()
    (elsewhere / _MIGRATION_WORKSPACE_LOCK_NAME).write_bytes(b"1")
    (elsewhere / "important.txt").write_text("keep", encoding="utf-8")

    # And a shaped-but-unmarked neighbour inside the namespace, to show the
    # conditions are all required rather than any one of them sufficing.
    unmarked = runtime_root / (_MIGRATION_WORKSPACE_PREFIX + "no-marker")
    unmarked.mkdir()
    (unmarked / "facts.json").write_text("keep", encoding="utf-8")

    ledger = config_manager._migration_ledger_path()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "\n".join([str(elsewhere), str(unmarked)]) + "\n", encoding="utf-8"
    )
    stale = time.time() - _MIGRATION_STAGING_STALE_SECONDS - 60
    for path in (elsewhere, unmarked):
        os.utime(path, (stale, stale))

    config_manager.migrate_memory_files()

    assert (elsewhere / "important.txt").read_text(encoding="utf-8") == "keep", (
        "a ledger line naming a directory outside the namespace was obeyed"
    )
    assert (unmarked / "facts.json").read_text(encoding="utf-8") == "keep", (
        "a recorded directory with no marker was removed"
    )

    # The dual: recorded, contained, shaped AND marked really is reclaimed,
    # so none of the above is passing by refusing everything.
    proper = runtime_root / (_MIGRATION_WORKSPACE_PREFIX + "real")
    proper.mkdir()
    (proper / _MIGRATION_WORKSPACE_LOCK_NAME).write_bytes(b"1")
    # The first run consumed every line and took the empty parent with
    # it, which is itself the tidy-up working.
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(str(proper) + "\n", encoding="utf-8")
    os.utime(proper, (stale, stale))

    config_manager.migrate_memory_files()

    assert not proper.exists(), "a properly recorded workspace was not reclaimed"


@pytest.mark.unit
def test_a_ledger_that_will_not_decode_does_not_fail_the_launch(tmp_path):
    """UnicodeDecodeError is not an OSError.

    So a ledger truncated mid-character by a kill -- or a "minted" file that
    was never ours -- escaped the handler, came out of a finally on the
    startup path, and would have failed the launch on every attempt.
    Reclamation is best effort and must never do that.
    """
    config_manager = _make_config_manager(tmp_path)
    runtime_root = tmp_path / "runtime-memory"
    project_root = tmp_path / "project-memory"
    config_manager.memory_dir = runtime_root
    config_manager.project_memory_dir = project_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    ledger = config_manager._migration_ledger_path()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    # A path cut in the middle of a multibyte character.
    ledger.write_bytes("C:/x/.mig-\u732b".encode("utf-8")[:-1])

    # Must not raise: the caller is startup.
    config_manager.migrate_memory_files()

    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "an undecodable ledger stopped the migration"


@pytest.mark.unit
def test_a_forked_child_releases_the_workspace_lock_it_inherited(tmp_path):
    """fork copies the open file description, not the reason for holding it.

    So the child goes on holding the advisory lock on a workspace it is not
    migrating into, and if the parent is killed every later run reads that
    workspace as LIVE for as long as the child survives. The earlier hook
    replaced the module lock and left this one.
    """
    from utils.config_manager import migrations as migrations_module
    from utils.config_manager.migrations import _MIGRATION_WORKSPACE_PREFIX

    config_manager = _make_config_manager(tmp_path)
    workspace = Path(config_manager.app_docs_dir) / (
        _MIGRATION_WORKSPACE_PREFIX + "inherited"
    )
    workspace.mkdir(parents=True, exist_ok=True)

    previous = migrations_module._MIGRATION_WORKSPACE_LOCK
    try:
        config_manager._claimed_workspace(workspace)
        if migrations_module._MIGRATION_WORKSPACE_LOCK is None:
            pytest.skip("this filesystem will not hold an advisory lock")
        assert migrations_module._workspace_is_live(workspace), (
            "the fixture never actually held the lock"
        )

        migrations_module._reset_migration_lock_after_fork()

        assert migrations_module._MIGRATION_WORKSPACE_LOCK is None
        assert not migrations_module._workspace_is_live(workspace), (
            "the child kept holding a lock on a workspace it does not own"
        )
    finally:
        migrations_module._release_inherited_workspace_lock()
        migrations_module._MIGRATION_WORKSPACE_LOCK = previous


@pytest.mark.unit
def test_the_flush_phase_keeps_the_workspace_alive_too(tmp_path):
    """The flush is a second slow phase, and the copy does not bound it.

    I argued it did -- that `_fsync_tree` runs over a tree just written, so
    its cost follows the copy that filled it. A reviewer pointed out that a
    writeback stall is its own thing, which is right: the data can be handed
    to the kernel quickly and take arbitrarily long to reach the device.

    So on a filesystem where the lock cannot be taken, a workspace could age
    out while it was still being made durable, and a second process would
    delete it out from under the flush.

    Pinned independently of the copy's beats: what is asserted is that the
    workspace is touched WHILE `_fsync_tree` is running, not merely that it
    is touched at some point during the entry.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    for index in range(4):
        (project_root / "Carol" / ("part%d.json" % index)).write_text(
            "[1]", encoding="utf-8"
        )

    touched = []
    during_flush = []
    real_utime = migrations_module.os.utime
    real_tree = migrations_module._fsync_tree
    clock = [0.0]

    def _record(path, times=None, **kwargs):
        touched.append(Path(path))
        return real_utime(path, times, **kwargs)

    def _every_call_is_a_minute_later():
        clock[0] += 60.0
        return clock[0]

    def _watch_the_flush(root, *args, **kwargs):
        before = len(touched)
        result = real_tree(root, *args, **kwargs)
        during_flush.append(len(touched) - before)
        return result

    with patch.object(migrations_module.os, "utime", _record), patch.object(
        migrations_module.time, "monotonic", _every_call_is_a_minute_later
    ), patch.object(migrations_module, "_fsync_tree", _watch_the_flush):
        config_manager.migrate_memory_files()

    assert (runtime_root / "Carol" / "part0.json").exists()
    assert during_flush, "the directory branch never flushed a tree at all"
    assert sum(during_flush) >= 2, (
        "the workspace was not kept alive during the flush itself: %r beats "
        "inside _fsync_tree" % (during_flush,)
    )


@pytest.mark.unit
def test_a_symlinked_staging_parent_keeps_its_own_ledger(tmp_path):
    """Preparation refuses to use a link at the reserved name. So must this.

    A link there points at somebody else's directory, so a file called
    "minted" inside it is theirs -- and reading it is the smaller half of the
    problem: with no valid records in it, the tidy-up at the end of
    reclamation reaches `ledger.unlink()` and deletes it.
    """
    from utils.config_manager.migrations import (
        _MIGRATION_LEDGER_NAME,
        _MIGRATION_STAGING_DIR,
    )

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    outside = tmp_path / "somebody-elses"
    outside.mkdir()
    theirs = outside / _MIGRATION_LEDGER_NAME
    theirs.write_text("not ours at all", encoding="utf-8")

    parent = Path(config_manager.app_docs_dir) / _MIGRATION_STAGING_DIR
    parent.parent.mkdir(parents=True, exist_ok=True)
    try:
        parent.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform will not create symlinks unprivileged")

    config_manager.migrate_memory_files()

    assert theirs.read_text(encoding="utf-8") == "not ours at all", (
        "a ledger behind a symlinked staging parent was read and removed"
    )
    assert parent.is_symlink(), "the link itself was not preserved"
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "the migration did not complete around the link"


@pytest.mark.unit
def test_a_workspace_that_lost_its_marker_stays_recorded(tmp_path):
    """Cleanup can remove the marker and then fail on a file still open.

    The marker stays REQUIRED -- without it a corrupted ledger naming a
    ".mig-"-prefixed character directory would have it deleted, and a
    directory left behind is the cheaper mistake. But forgetting the RECORD
    as well throws away the only thing that points at a workspace still
    sitting in the namespace.
    """
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_STALE_SECONDS,
        _MIGRATION_WORKSPACE_PREFIX,
    )

    config_manager = _make_config_manager(tmp_path)
    runtime_root = tmp_path / "runtime-memory"
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    config_manager.project_memory_dir = tmp_path / "no-such-project-memory"

    # Recorded and shaped, but its marker went in a half-finished cleanup.
    stripped = runtime_root / (_MIGRATION_WORKSPACE_PREFIX + "half-cleaned")
    stripped.mkdir()
    (stripped / "still-here.json").write_text("[9]", encoding="utf-8")

    ledger = config_manager._migration_ledger_path()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(str(stripped) + "\n", encoding="utf-8")
    stale = time.time() - _MIGRATION_STAGING_STALE_SECONDS - 60
    os.utime(stripped, (stale, stale))

    config_manager.migrate_memory_files()

    assert stripped.is_dir(), "an unmarked directory was deleted after all"
    assert ledger.exists() and str(stripped) in ledger.read_text(
        encoding="utf-8"
    ), "the only record of a workspace still on disk was thrown away"


@pytest.mark.unit
def test_a_cloud_import_leaves_a_migration_workspace_alone(tmp_path):
    """The import treats every unimported directory in memory/ as stale.

    On the cross-device layout the migration has to stage INSIDE memory_dir,
    and an import can run while that copy is in flight -- so the sweep would
    remove a half-copied character tree out from under the process writing
    it.
    """
    from utils.cloudsave_runtime.operations import (
        bootstrap_local_cloudsave_environment,
        export_local_cloudsave_snapshot,
        import_local_cloudsave_snapshot,
    )
    from utils.config_manager.migrations import _MIGRATION_WORKSPACE_PREFIX

    config_manager = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(config_manager)
    memory_root = Path(config_manager.memory_dir)
    memory_root.mkdir(parents=True, exist_ok=True)
    export_local_cloudsave_snapshot(config_manager)

    # Both appear AFTER the snapshot, so neither is in the imported set.
    from utils.config_manager.migrations import _MIGRATION_WORKSPACE_LOCK_NAME

    workspace = memory_root / (_MIGRATION_WORKSPACE_PREFIX + "live")
    (workspace / "d").mkdir(parents=True)
    (workspace / "d" / "half.json").write_text("[1]", encoding="utf-8")
    (workspace / _MIGRATION_WORKSPACE_LOCK_NAME).write_bytes(b"1")
    # RECORDED, which is what makes it ours. A marker and a prefix are shapes
    # the data being swept can reproduce; only the ledger cannot.
    _record_workspace_in_ledger(config_manager, workspace)

    genuinely_stale = memory_root / "Ghost"
    genuinely_stale.mkdir()
    (genuinely_stale / "facts.json").write_text("[9]", encoding="utf-8")
    # A dot-prefixed directory is NOT a character: character names never
    # begin with one. So this is a second workspace, and the sweep leaves it
    # alone for the same reason as the first -- which is what makes the rule
    # a rule rather than a special case for the one we minted.
    second_workspace = memory_root / (_MIGRATION_WORKSPACE_PREFIX + "Dora")
    second_workspace.mkdir()
    (second_workspace / "d").mkdir()
    (second_workspace / "d" / "other.json").write_text("[7]", encoding="utf-8")

    import_local_cloudsave_snapshot(config_manager)

    assert (workspace / "d" / "half.json").exists(), (
        "the import deleted a migration workspace as stale runtime data"
    )
    # The duals, so this is not passing by the sweep having stopped working.
    assert not genuinely_stale.exists(), (
        "a directory that really was stale survived the import"
    )
    assert (second_workspace / "d" / "other.json").exists(), (
        "a dot-prefixed directory was swept as a character, and no character "
        "name begins with a dot"
    )


@pytest.mark.unit
def test_a_workspace_that_becomes_live_after_enumeration_survives(tmp_path):
    """The liveness answer can change between the sweep and the removal.

    A cross-device migration that has returned from ``mkdtemp()`` but has not
    yet created and locked its marker reads as inactive while the delete set
    is built, and can claim the workspace and begin copying during the
    file-apply phase -- which is long. Asking once scheduled it for deletion
    and never looked again.

    Driven by making the predicate answer False then True, because that is
    exactly the transition: what is asserted is that the second answer is
    taken, not how the race is won.
    """
    from utils.cloudsave_runtime.operations import (
        bootstrap_local_cloudsave_environment,
        export_local_cloudsave_snapshot,
        import_local_cloudsave_snapshot,
    )
    from utils.config_manager import migrations as migrations_module
    from utils.config_manager.migrations import _MIGRATION_WORKSPACE_PREFIX

    config_manager = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(config_manager)
    memory_root = Path(config_manager.memory_dir)
    memory_root.mkdir(parents=True, exist_ok=True)
    export_local_cloudsave_snapshot(config_manager)

    late = memory_root / (_MIGRATION_WORKSPACE_PREFIX + "late")
    (late / "d").mkdir(parents=True)
    (late / "d" / "half.json").write_text("[1]", encoding="utf-8")

    # NO evidence of any kind: no ledger line, no marker, no lock. This is
    # the window every previous criterion had -- mkdtemp has returned and
    # nothing has been written about the directory yet -- and the name is
    # what closes it.
    import_local_cloudsave_snapshot(config_manager)

    assert (late / "d" / "half.json").exists(), (
        "a workspace with no evidence yet was deleted mid-copy"
    )


@pytest.mark.unit
def test_the_recheck_does_not_spare_an_ordinary_stale_directory(tmp_path):
    """The dual: the re-check must not turn the sweep off.

    It is keyed on the workspace prefix, so a stale character directory is
    removed whatever the predicate would say about it.
    """
    from utils.cloudsave_runtime.operations import (
        bootstrap_local_cloudsave_environment,
        export_local_cloudsave_snapshot,
        import_local_cloudsave_snapshot,
    )
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(config_manager)
    memory_root = Path(config_manager.memory_dir)
    memory_root.mkdir(parents=True, exist_ok=True)
    export_local_cloudsave_snapshot(config_manager)

    stale = memory_root / "Ghost"
    stale.mkdir()
    (stale / "facts.json").write_text("[9]", encoding="utf-8")

    import_local_cloudsave_snapshot(config_manager)

    assert not stale.exists(), (
        "an ordinary stale character directory survived the sweep"
    )


@pytest.mark.unit
def test_an_unrecorded_but_live_workspace_survives(tmp_path):
    """The gap ownership cannot see, and why liveness is still consulted.

    ``_record_minted_workspace`` swallows OSError, and the migration goes on
    to lock and use the workspace regardless -- so a full disk or a read-only
    app_docs leaves an in-flight workspace with no ledger line. Deleting it
    destroys a copy in progress and takes that seed out for the session,
    which is the more expensive of the two misses.
    """
    from utils.cloudsave_runtime.operations import (
        bootstrap_local_cloudsave_environment,
        export_local_cloudsave_snapshot,
        import_local_cloudsave_snapshot,
    )
    from utils.config_manager.migrations import (
        _MIGRATION_WORKSPACE_LOCK_NAME,
        _MIGRATION_WORKSPACE_PREFIX,
        _hold_workspace_lock,
    )

    config_manager = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(config_manager)
    memory_root = Path(config_manager.memory_dir)
    memory_root.mkdir(parents=True, exist_ok=True)
    export_local_cloudsave_snapshot(config_manager)

    # Deliberately NOT recorded: this is the ledger-append-failed case.
    unrecorded = memory_root / (_MIGRATION_WORKSPACE_PREFIX + "unrecorded")
    unrecorded.mkdir()
    marker = unrecorded / _MIGRATION_WORKSPACE_LOCK_NAME
    marker.write_bytes(b"")

    handle = open(marker, "r+b")
    try:
        _hold_workspace_lock(handle)
        import_local_cloudsave_snapshot(config_manager)
    finally:
        handle.close()

    assert unrecorded.exists(), (
        "an unrecorded workspace with a HELD lock was deleted mid-copy"
    )


@pytest.mark.unit
def test_rollback_does_not_restore_over_a_workspace_that_became_ours(tmp_path):
    """The deletion loop re-checks; the rollback path did not.

    A workspace recorded after enumeration is skipped by the deletion loop but
    is still in ``backup_records``. If anything unrelated then fails, rollback
    removed every recorded target and restored its backup -- over a tree
    another process was writing, leaving that seed unavailable for the
    session.
    """
    from utils.cloudsave_runtime import operations as operations_module
    from utils.cloudsave_runtime.operations import (
        bootstrap_local_cloudsave_environment,
        export_local_cloudsave_snapshot,
        import_local_cloudsave_snapshot,
    )
    from utils.config_manager import migrations as migrations_module
    from utils.config_manager.migrations import _MIGRATION_WORKSPACE_PREFIX

    config_manager = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(config_manager)
    memory_root = Path(config_manager.memory_dir)
    memory_root.mkdir(parents=True, exist_ok=True)
    export_local_cloudsave_snapshot(config_manager)

    late = memory_root / (_MIGRATION_WORKSPACE_PREFIX + "late")
    (late / "d").mkdir(parents=True)
    (late / "d" / "in-flight.json").write_text("[fresh]", encoding="utf-8")

    seen = []

    def _unrecorded_then_recorded(app_docs_dir):
        seen.append(app_docs_dir)
        # Unknown while the sweep runs; ours by the time anything else looks.
        if len(seen) == 1:
            return set()
        return {late.resolve(strict=False)}

    def _boom(*args, **kwargs):
        raise RuntimeError("an unrelated apply failed after the backups")

    with patch.object(
        migrations_module, "recorded_workspace_paths", _unrecorded_then_recorded
    ), patch.object(
        migrations_module, "_workspace_is_live", lambda path: False
    ), patch.object(
        operations_module, "set_recent_pending_unlocked", _boom
    ):
        with pytest.raises(Exception):
            import_local_cloudsave_snapshot(config_manager)

    assert (late / "d" / "in-flight.json").read_text(encoding="utf-8") == "[fresh]", (
        "rollback restored a stale backup over a live migration workspace"
    )


@pytest.mark.unit
def test_a_tombstoned_character_is_not_republished_from_the_seed(tmp_path):
    """Deleting a character must not be undone by the next startup.

    A cloud snapshot that keeps the profile but omits every managed memory
    file has the import unlink them, and the emptied character directory is
    removed. "Destination missing" then looks exactly like "never migrated",
    so the whole stale project seed was published again -- restoring facts
    and history the user deleted.
    """
    from utils.config_manager import migrations as migrations_module

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)

    (project_root / "recent_Carol.json").write_text('["stale"]', encoding="utf-8")
    (project_root / "time_indexed_Carol.db").write_bytes(b"stale")
    (project_root / "Dora").mkdir()
    (project_root / "Dora" / "facts.json").write_text('["stale"]', encoding="utf-8")
    # A legacy vector store belongs to its owner, not to a character called
    # "semantic_memory_Carol" -- comparing the bare directory name missed it.
    (project_root / "semantic_memory_Carol").mkdir()
    (project_root / "semantic_memory_Carol" / "index.faiss").write_bytes(b"stale")
    # Not deleted, so this one must still be seeded.
    (project_root / "recent_Eve.json").write_text('["seed"]', encoding="utf-8")

    with patch.object(
        migrations_module,
        "_tombstoned_character_names",
        lambda cm: frozenset({"Carol", "Dora"}),
    ):
        config_manager.migrate_memory_files()

    assert not (runtime_root / "recent_Carol.json").exists(), (
        "a deleted character's flat seed was republished"
    )
    assert not (runtime_root / "time_indexed_Carol.db").exists(), (
        "the filename with an extension decoded to the wrong owner and slipped"
    )
    assert not (runtime_root / "Dora").exists(), (
        "a deleted character's seed directory was republished"
    )
    assert not (runtime_root / "semantic_memory_Carol").exists(), (
        "a deleted character's legacy vector store was republished"
    )
    assert (runtime_root / "recent_Eve.json").read_text(encoding="utf-8") == '["seed"]', (
        "the tombstone check stopped seeding characters that were never deleted"
    )


@pytest.mark.unit
def test_an_unreadable_tombstone_file_still_seeds(tmp_path):
    """The safe direction: a bad read republishes, as it did before this existed.

    Refusing to migrate would be a startup that silently stops seeding, which
    is worse than the case this guards.
    """
    from unittest.mock import MagicMock

    from utils.config_manager.migrations import _tombstoned_character_names

    broken = MagicMock()
    broken.load_character_tombstones_state.side_effect = OSError("unreadable")
    assert _tombstoned_character_names(broken) == frozenset()

    for shape in (None, [], {"tombstones": "not a list"}, {}):
        stub = MagicMock()
        stub.load_character_tombstones_state.return_value = shape
        assert _tombstoned_character_names(stub) == frozenset()
