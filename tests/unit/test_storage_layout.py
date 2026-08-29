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

    real_copy2 = migrations_module.shutil.copy2

    def _die_partway(source, destination, *args, **kwargs):
        Path(destination).write_text('["trun', encoding="utf-8")
        raise OSError("interrupted partway through the copy")

    with patch.object(migrations_module.shutil, "copy2", _die_partway):
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
    assert real_copy2 is migrations_module.shutil.copy2


@pytest.mark.unit
def test_only_staging_we_own_is_reclaimed(tmp_path):
    """Ownership is proven by a sentinel, never inferred from the name.

    Sweeping every ``.migrating-*`` entry was destructive. A dot-prefixed
    character name is accepted by the runtime, so a real character called
    ``.migrating-Carol`` had its live memory recursively deleted -- and with no
    seed of that name, lost outright. The sweep now reclaims only a staging
    root carrying the file this migration wrote.
    """
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_DIR,
        _MIGRATION_STAGING_SENTINEL,
    )

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)

    # Ours: a staging root left by a run that was killed outright.
    owned = runtime_root / _MIGRATION_STAGING_DIR
    owned.mkdir()
    (owned / _MIGRATION_STAGING_SENTINEL).write_text("", encoding="utf-8")
    (owned / "half-copied.json").write_text("half", encoding="utf-8")

    # NOT ours: a character whose name merely looks like staging, and its
    # memory is the only copy that exists.
    impostor = runtime_root / ".migrating-Carol"
    impostor.mkdir()
    (impostor / "facts.json").write_text('["only copy"]', encoding="utf-8")

    config_manager.migrate_memory_files()

    assert not owned.exists(), "our own staging root was not reclaimed"
    assert (impostor / "facts.json").read_text(
        encoding="utf-8"
    ) == '["only copy"]', (
        "a real character was deleted because its name looked like staging"
    )


@pytest.mark.unit
def test_a_stranger_holding_the_staging_name_is_left_alone(tmp_path):
    """If the staging name is taken by something not ours, work around it.

    Reclaiming it would be the same destruction one step earlier, so a unique
    name is used instead and the stranger is untouched -- while the migration
    still has to complete.
    """
    from utils.config_manager.migrations import _MIGRATION_STAGING_DIR

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    stranger = runtime_root / _MIGRATION_STAGING_DIR
    stranger.mkdir()
    (stranger / "facts.json").write_text('["not ours"]', encoding="utf-8")

    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    config_manager.migrate_memory_files()

    assert (stranger / "facts.json").read_text(
        encoding="utf-8"
    ) == '["not ours"]', "the stranger holding the staging name was destroyed"
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "the migration did not complete around it"
    leftovers = sorted(
        p.name for p in runtime_root.iterdir()
        if p.name.startswith(_MIGRATION_STAGING_DIR)
    )
    assert leftovers == [_MIGRATION_STAGING_DIR], (
        "a staging root was left behind: %s" % leftovers
    )


@pytest.mark.unit
def test_a_hard_kill_leaves_staging_the_next_run_can_claim(tmp_path):
    """The sentinel is only load-bearing across a process that never cleaned up.

    In an ordinary run the ``finally`` removes the staging root, so whether
    the sentinel was ever written is invisible. It matters exactly once: a
    run killed outright leaves the root behind, and the next run has to be
    able to tell that root apart from a character whose name happens to
    match. Without the sentinel it would decline to reclaim its own leavings
    and stage under a fresh unique name forever.

    The kill is simulated by neutering the cleanup, not by killing python.
    """
    from utils.config_manager import migrations as migrations_module
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_DIR,
        _MIGRATION_STAGING_SENTINEL,
    )

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    with patch.object(migrations_module.shutil, "rmtree", lambda *a, **k: None):
        config_manager.migrate_memory_files()

    staging = runtime_root / _MIGRATION_STAGING_DIR
    assert staging.is_dir(), "the simulated kill did not leave staging behind"
    assert (staging / _MIGRATION_STAGING_SENTINEL).exists(), (
        "the staging root carries no proof of ownership, so the next run "
        "cannot tell it from a character of the same name"
    )

    # Next run: it reclaims its own leavings rather than working around them.
    config_manager.migrate_memory_files()
    remaining = sorted(
        p.name for p in runtime_root.iterdir()
        if p.name.startswith(_MIGRATION_STAGING_DIR)
    )
    assert remaining == [], (
        "the next run staged under a new name instead of reclaiming: %s"
        % remaining
    )


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
def test_a_symlink_on_the_staging_path_is_never_treated_as_ours(tmp_path):
    """rmtree leaves a directory symlink in place, and mkdir then succeeds
    through it -- so staging would be written wherever it points, outside
    the memory root. A link is never ours, whatever it targets.
    """
    from utils.config_manager.migrations import _MIGRATION_STAGING_DIR

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    # A BROKEN link, which is the case the sentinel check cannot cover: for a
    # link to an existing directory, exists() is true and the missing
    # sentinel already routes us around it. For a broken one exists() is
    # FALSE, so without the symlink test we would try to rmtree it (which
    # leaves a link alone) and then mkdir through it -- and this helper runs
    # BEFORE the try, so the whole migration would raise out of the method.
    outside = tmp_path / "outside-target-that-does-not-exist"
    try:
        (runtime_root / _MIGRATION_STAGING_DIR).symlink_to(
            outside, target_is_directory=True
        )
    except (OSError, NotImplementedError):
        pytest.skip("this platform will not create symlinks unprivileged")

    (project_root / "Carol").mkdir(parents=True)
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    config_manager.migrate_memory_files()

    assert not outside.exists(), (
        "staging was created through the broken link, outside the memory root"
    )
    assert (runtime_root / _MIGRATION_STAGING_DIR).is_symlink(), (
        "the link itself was destroyed"
    )
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]", "the migration did not complete around it"


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
def test_a_project_entry_named_like_staging_still_migrates(tmp_path):
    """The staging root must not squat on a destination a seed needs.

    With the staging root at ``memory/<staging name>``, a project entry of
    the same name finds its destination already occupied, is skipped as
    "already there", and then the ``finally`` deletes the staging root -- so
    it never migrates while looking as though it did.
    """
    from utils.config_manager.migrations import _MIGRATION_STAGING_DIR

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    (project_root / _MIGRATION_STAGING_DIR).mkdir(parents=True)
    (project_root / _MIGRATION_STAGING_DIR / "facts.json").write_text(
        '["a real character"]', encoding="utf-8"
    )

    config_manager.migrate_memory_files()

    assert (
        runtime_root / _MIGRATION_STAGING_DIR / "facts.json"
    ).read_text(encoding="utf-8") == '["a real character"]', (
        "the staging root squatted on this entry's destination, so it was "
        "skipped and then deleted with the staging root"
    )
    leftovers = sorted(
        p.name for p in runtime_root.iterdir()
        if p.name.startswith(_MIGRATION_STAGING_DIR + "-")
    )
    assert leftovers == [], "a staging root was left behind: %s" % leftovers


@pytest.mark.unit
def test_fallback_staging_roots_do_not_accumulate(tmp_path):
    """The unique-name path has to reclaim its own leavings too.

    When the base staging name is unavailable every run mints a fresh uuid, so
    a run killed after creating one leaves a root nobody will ever name again
    -- and full staging copies pile up, run after run. The ownership rule is
    the same as for the base name: a sentinel we wrote, never a symlink.

    This is the combination the earlier tests missed: a project-side name
    collision AND a hard kill AND a retry.
    """
    from utils.config_manager import migrations as migrations_module
    from utils.config_manager.migrations import (
        _MIGRATION_STAGING_DIR,
        _MIGRATION_STAGING_SENTINEL,
    )

    config_manager = _make_config_manager(tmp_path)
    project_root = tmp_path / "project-memory"
    runtime_root = tmp_path / "runtime-memory"
    config_manager.project_memory_dir = project_root
    config_manager.memory_dir = runtime_root
    runtime_root.mkdir(parents=True, exist_ok=True)

    # Holding the base name on the project side forces the unique-name path.
    (project_root / _MIGRATION_STAGING_DIR).mkdir(parents=True)
    (project_root / _MIGRATION_STAGING_DIR / "facts.json").write_text(
        '["seed"]', encoding="utf-8"
    )
    (project_root / "Carol").mkdir()
    (project_root / "Carol" / "facts.json").write_text("[1]", encoding="utf-8")

    def fallback_roots():
        return sorted(
            path.name for path in runtime_root.iterdir()
            if path.name.startswith(_MIGRATION_STAGING_DIR + "-")
        )

    # A run killed before it could clean up. Only the FINAL cleanup is
    # disabled: a real kill does not stop the NEXT run from using rmtree, and
    # neutering it for the whole run would disable the reclaim as well and test
    # nothing -- which is exactly how the first version of this test failed.
    with patch.object(migrations_module.shutil, "rmtree", lambda *a, **k: None):
        config_manager.migrate_memory_files()

    killed = fallback_roots()
    assert len(killed) == 1, "the kill left no staging root: %s" % killed
    assert (runtime_root / killed[0] / _MIGRATION_STAGING_SENTINEL).exists()

    # Something that is NOT ours, which the reclaim has to leave alone.
    stranger = runtime_root / (_MIGRATION_STAGING_DIR + "-not-ours")
    stranger.mkdir()
    (stranger / "facts.json").write_text('["keep"]', encoding="utf-8")

    config_manager.migrate_memory_files()

    assert fallback_roots() == [_MIGRATION_STAGING_DIR + "-not-ours"], (
        "either our roots accumulated or the stranger was reclaimed: %s"
        % fallback_roots()
    )
    assert (stranger / "facts.json").read_text(encoding="utf-8") == '["keep"]'
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]"
