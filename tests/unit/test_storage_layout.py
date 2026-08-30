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
    """A run killed outright leaves a workspace; the next run clears it.

    Each run gets its own workspace so concurrent runs cannot delete each
    other's, which means leavings can only be cleared by a later run. Without
    that, full memory copies pile up until the disk fills.
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

    # Two runs killed before cleanup. Only the final cleanup is disabled: a
    # real kill does not stop the NEXT run from using rmtree, and neutering it
    # for the whole run would disable the clearing under test.
    for _ in range(2):
        with patch.object(
            migrations_module.shutil, "rmtree", lambda *a, **k: None
        ):
            config_manager.migrate_memory_files()

    assert parent.is_dir(), "the kills left no staging parent at all"
    assert len(list(parent.iterdir())) >= 1, "the kills left no workspace"

    # A clean run clears what the kills left AND takes the parent with it,
    # so nothing of the migration persists between runs at all.
    config_manager.migrate_memory_files()
    assert not parent.exists(), (
        "staging survived a clean run: %s"
        % (sorted(q.name for q in parent.iterdir()) if parent.is_dir() else parent)
    )
    assert (runtime_root / "Carol" / "facts.json").read_text(
        encoding="utf-8"
    ) == "[1]"


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

    config_manager.migrate_memory_files()

    # The workspace is removed in the finally either way, so an empty target
    # at the end proves nothing. What the fix guarantees is that the link is
    # REPLACED by a real directory before anything is staged.
    assert not parent.is_symlink(), (
        "the staging parent is still a link, so everything staged went "
        "through it and outside the writable tree"
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

    Removing it rather than working around it is the point. Minting a unique
    name beside it would bring back the fallback roots this design removed, and
    with them the accumulate-after-a-kill problem. The name lives in
    app_docs_dir, outside the character namespace, where nothing but this
    migration has any business -- which is what makes removal safe here and
    would not have been inside memory/.
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
    # The staging parent comes down with everything else.
    assert not squatter.exists()


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

    def _record_tree(path):
        flushed_trees.append(Path(path))
        return real_tree(path)

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
