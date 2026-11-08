"""Project-local dependency editing, inspection and clean synchronization."""
from __future__ import annotations

import subprocess
import json
from pathlib import Path

import pytest

from plugin.neko_plugin_cli.core import vendor_sync
from plugin.neko_plugin_cli.core.python_packages import (
    PythonPackagesError,
    check_python_packages,
    inspect_python_packages,
    save_python_requirements,
)
from plugin.neko_plugin_cli.core.toml_utils import tomllib


def _project(tmp_path: Path, text: str) -> Path:
    project = tmp_path / "example"
    project.mkdir()
    (project / "pyproject.toml").write_text(text, encoding="utf-8")
    return project


def _distribution(project: Path, name: str, version: str, *headers: str) -> None:
    info = project / "vendor" / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n" + "\n".join(headers) + "\n",
        encoding="utf-8",
    )


def test_inventory_never_uses_host_distributions(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["packaging", "N.E.K.O", "demo; python_version < \'2\'"]\n')
    snapshot = inspect_python_packages(project)
    assert [row["status"] for row in snapshot["requirements"]] == ["missing", "host_provided", "not_applicable"]
    assert snapshot["installed"] == []


def test_inventory_reports_versions_and_direct_relationship(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["demo>=2"]\n')
    _distribution(project, "demo", "1.0", "Summary: Demonstration", "Requires-Dist: helper>=1")
    _distribution(project, "helper", "1.5")
    snapshot = inspect_python_packages(project)
    assert snapshot["requirements"][0]["status"] == "version_mismatch"
    assert snapshot["installed"][0]["direct"] is True
    assert snapshot["installed"][0]["requires_dist"] == ["helper>=1"]
    assert snapshot["installed"][1]["direct"] is False


def test_prerelease_requirement_status_agrees_with_runtime_check(tmp_path):
    from plugin.core.python_dependencies import find_missing_python_requirements

    project = _project(tmp_path, '[project]\ndependencies = ["demo>=1"]\n')
    _distribution(project, "demo", "2.0rc1")
    assert inspect_python_packages(project)["requirements"][0]["status"] == "version_mismatch"
    assert find_missing_python_requirements(["demo>=1"], search_paths=[project / "vendor"]) == ["demo>=1"]


def test_closure_tracks_extras_cycles_and_platform_markers(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["demo[socks]"]\n')
    _distribution(project, "demo", "1.0", 'Requires-Dist: helper>=2; extra == "socks"',
                  'Requires-Dist: inactive; python_version < "2"',
                  'Requires-Dist: optional; extra == "other"')
    _distribution(project, "helper", "1.0", "Requires-Dist: demo[socks]")
    snapshot = check_python_packages(project)
    assert [(row["code"], row["package"]) for row in snapshot["issues"]] == [("dependency_version_mismatch", "demo")]


def test_closure_reports_missing_transitive_dependency_and_python_constraint(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["demo"]\n')
    _distribution(project, "demo", "1.0", "Requires-Dist: missing", "Requires-Python: <2")
    codes = {row["code"] for row in check_python_packages(project)["issues"]}
    assert codes == {"dependency_missing", "python_version_mismatch"}


def test_graph_preserves_shared_dependencies_and_unrelated_installed_packages(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["alpha", "beta"]\n')
    _distribution(project, "alpha", "1.0", "Requires-Dist: shared>=1")
    _distribution(project, "beta", "1.0", "Requires-Dist: shared>=1")
    _distribution(project, "shared", "1.5")
    _distribution(project, "standalone", "1.0", "Requires-Dist: helper")
    _distribution(project, "helper", "1.0")
    graph = inspect_python_packages(project)["dependency_graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert graph["roots"] == ["alpha", "beta"]
    assert graph["other_roots"] == ["standalone"]
    assert nodes["shared"]["required_by"] == ["alpha", "beta"]
    assert nodes["alpha"]["dependencies"] == [{"id": "shared", "requirements": ["shared>=1"], "status": "satisfied"}]


def test_graph_uses_active_extras_markers_and_missing_nodes(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["demo[socks]", "skipped; python_version < \'2\'"]\n')
    _distribution(project, "demo", "1.0", 'Requires-Dist: socks_lib>=2; extra == "socks"',
                  'Requires-Dist: inactive; python_version < "2"', 'Requires-Dist: unselected; extra == "other"')
    graph = inspect_python_packages(project)["dependency_graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert graph["roots"] == ["demo"]
    assert set(nodes) == {"demo", "socks-lib"}
    assert nodes["socks-lib"]["version"] is None
    assert nodes["socks-lib"]["status"] == "missing"
    assert nodes["socks-lib"]["required_by"] == ["demo"]


def test_graph_keeps_a_disconnected_cycle_reachable_without_recursive_data(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = []\n')
    _distribution(project, "alpha", "1.0", "Requires-Dist: beta")
    _distribution(project, "beta", "1.0", "Requires-Dist: alpha")
    graph = inspect_python_packages(project)["dependency_graph"]
    assert graph["roots"] == []
    assert graph["other_roots"] == ["alpha"]
    assert len(graph["nodes"]) == 2
    assert {node["id"]: node["required_by"] for node in graph["nodes"]} == {"alpha": ["beta"], "beta": ["alpha"]}


def test_graph_reports_requirement_conflicts_on_the_installed_node(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["alpha>=2", "beta"]\n')
    _distribution(project, "alpha", "1.0", "Requires-Dist: child>=2")
    _distribution(project, "beta", "1.0", "Requires-Dist: child<2")
    _distribution(project, "child", "1.0")
    graph = inspect_python_packages(project)["dependency_graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["alpha"]["declarations"] == ["alpha>=2"]
    assert nodes["alpha"]["status"] == "version_mismatch"
    assert nodes["child"]["status"] == "version_mismatch"
    assert nodes["child"]["required_by"] == ["alpha", "beta"]


def test_graph_reports_invalid_distribution_names_without_breaking_inventory(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = []\n')
    _distribution(project, "not a package name", "1.0")
    snapshot = inspect_python_packages(project)
    assert snapshot["dependency_graph"]["nodes"] == []
    assert snapshot["issues"][0]["code"] == "invalid_metadata"


def test_market_check_uses_signatures_and_build_exclusions(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = []\n[tool.neko.build]\nexclude = ["vendor/excluded.so"]\n')
    vendor = project / "vendor"
    vendor.mkdir()
    (vendor / "extension.txt").write_bytes(b"\x7fELFexecutable")
    (vendor / "excluded.so").write_bytes(b"binary")
    (vendor / "__pycache__").mkdir()
    (vendor / "__pycache__" / "cache.pyc").write_bytes(b"cache")
    assert [row["path"] for row in check_python_packages(project)["market_issues"]] == ["vendor/extension.txt"]


def test_save_preserves_comments_and_other_tables(tmp_path):
    original = '''# User configuration
[project] # Keep this comment
name = 'example'
dependencies = [
    "old", # Needed for old functionality
    # Keep this explanation
]

[tool.example]
message = "do not reformat this"
'''
    project = _project(tmp_path, original)
    snapshot = inspect_python_packages(project)
    saved = save_python_requirements(project, ["new>=2"], snapshot["revision"])
    text = (project / "pyproject.toml").read_text()
    assert text.startswith("# User configuration\n[project] # Keep this comment\nname = 'example'\n")
    assert "# Needed for old functionality" in text
    assert "# Keep this explanation" in text
    assert text.endswith('[tool.example]\nmessage = "do not reformat this"\n')
    assert tomllib.loads(text)["project"]["dependencies"] == ["new>=2"]
    assert saved["revision"] != snapshot["revision"]


@pytest.mark.parametrize("text", ["", "# Empty project\n", "[project]", '[project]\nname="example"\n[tool.test]\nvalue=1\n'])
def test_save_adds_missing_dependencies_without_losing_configuration(tmp_path, text):
    project = _project(tmp_path, text)
    snapshot = inspect_python_packages(project)
    save_python_requirements(project, ["demo"], snapshot["revision"])
    parsed = tomllib.loads((project / "pyproject.toml").read_text())
    assert parsed["project"]["dependencies"] == ["demo"]
    if "tool.test" in text:
        assert parsed["tool"]["test"]["value"] == 1


def test_save_rejects_external_editor_conflict(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["old"]\n')
    snapshot = inspect_python_packages(project)
    changed = '[project]\ndependencies = ["editor-change"]\n'
    (project / "pyproject.toml").write_text(changed)
    with pytest.raises(PythonPackagesError, match="changed") as error:
        save_python_requirements(project, ["new"], snapshot["revision"])
    assert error.value.code == "PYTHON_PACKAGES_REVISION_CONFLICT"
    assert (project / "pyproject.toml").read_text() == changed


def test_save_preserves_existing_urls_but_rejects_new_source_edits(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["demo @ https://example.invalid/demo.whl"]\n')
    snapshot = inspect_python_packages(project)
    assert snapshot["requirements"][0]["editable"] is False
    snapshot = save_python_requirements(project, [snapshot["requirements"][0]["requirement"], "other"], snapshot["revision"])
    with pytest.raises(PythonPackagesError) as error:
        save_python_requirements(project, ["new @ https://example.invalid/new.whl"], snapshot["revision"])
    assert error.value.code == "PYTHON_PACKAGES_REQUIREMENT_SOURCE_UNSUPPORTED"


def test_save_refuses_inline_project_instead_of_reformatting_it(tmp_path):
    text = 'project = {dependencies = ["old"], name = "example"}\n'
    project = _project(tmp_path, text)
    with pytest.raises(PythonPackagesError) as error:
        save_python_requirements(project, ["new"], inspect_python_packages(project)["revision"])
    assert error.value.code == "PYTHON_PACKAGES_FORMAT_UNSUPPORTED"
    assert (project / "pyproject.toml").read_text() == text


def test_clean_with_last_dependency_removed_empties_vendor_without_installer(tmp_path, monkeypatch):
    project = _project(tmp_path, '[project]\ndependencies = []\n')
    _distribution(project, "old", "1.0")
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr(vendor_sync, "_lock_dir", lambda: locks)

    def no_installer(*args, **kwargs):
        pytest.fail("An empty dependency set must not invoke an installer")

    assert vendor_sync.sync_vendor(project, clean=True, installer_runner=no_installer) == 0
    assert list((project / "vendor").iterdir()) == []


def test_injected_installer_uses_staging_and_failure_preserves_existing_vendor(tmp_path, monkeypatch):
    project = _project(tmp_path, '[project]\ndependencies = ["new"]\n')
    _distribution(project, "old", "1.0")
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr(vendor_sync, "_lock_dir", lambda: locks)
    commands = []

    def failed(command, *, label):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="resolver failed")

    assert vendor_sync.sync_vendor(project, clean=True, installer_runner=failed, uv_executable="managed-uv") == 1
    assert commands[0][0] == "managed-uv"
    assert ".vendor.staging-" in commands[0][commands[0].index("--target") + 1]
    assert inspect_python_packages(project)["installed"][0]["name"] == "old"


def test_save_and_sync_use_same_project_lock(tmp_path, monkeypatch):
    project = _project(tmp_path, '[project]\ndependencies = []\n')
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr(vendor_sync, "_lock_dir", lambda: locks)
    revision = inspect_python_packages(project)["revision"]
    with vendor_sync.dependency_lock(project):
        with pytest.raises(PythonPackagesError) as error:
            save_python_requirements(project, ["demo"], revision)
    assert error.value.code == "PYTHON_PACKAGES_BUSY"


def test_task_cancelled_after_installer_keeps_old_vendor(tmp_path, monkeypatch):
    project = _project(tmp_path, '[project]\ndependencies = ["new"]\n')
    _distribution(project, "old", "1.0")
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr(vendor_sync, "_lock_dir", lambda: locks)
    cancel = False
    phases = []

    class Cancelled(Exception):
        pass

    def run(command, *, label):
        nonlocal cancel
        cancel = True
        return subprocess.CompletedProcess(command, 0, stdout="completed")

    def checkpoint():
        if cancel:
            raise Cancelled

    with pytest.raises(Cancelled):
        vendor_sync.sync_vendor(project, installer_runner=run, uv_executable="uv",
                                progress=phases.append, check_cancelled=checkpoint)
    assert phases == ["preparing", "installing"]
    assert inspect_python_packages(project)["installed"][0]["name"] == "old"
    assert not list(project.glob(".vendor.staging-*"))


def test_task_report_receives_lock_failure_without_global_output_redirection(tmp_path, monkeypatch):
    project = _project(tmp_path, '[project]\ndependencies = []\n')
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr(vendor_sync, "_lock_dir", lambda: locks)
    messages = []
    with vendor_sync.dependency_lock(project):
        assert vendor_sync.sync_vendor(project, report=messages.append) == 1
    assert len(messages) == 1
    assert "already in progress" in messages[0]


@pytest.mark.parametrize("requirement", [
    'demo; python_version ~= "not-a-version"', 'demo; "feature" in dependency_groups',
])
def test_save_rejects_marker_evaluation_errors_before_writing(tmp_path, requirement):
    project = _project(tmp_path, '[project]\ndependencies = []\n')
    original = (project / "pyproject.toml").read_bytes()
    with pytest.raises(PythonPackagesError) as error:
        save_python_requirements(project, [requirement],
                                 inspect_python_packages(project)["revision"])
    assert error.value.code == "PYTHON_PACKAGES_REQUIREMENTS_INVALID"
    assert (project / "pyproject.toml").read_bytes() == original


@pytest.mark.parametrize("invalid", [
    'broken; python_version ~= "not-a-version"', 'broken; "feature" in dependency_groups',
])
def test_inventory_reports_marker_evaluation_errors_and_keeps_valid_packages(tmp_path, invalid):
    project = _project(tmp_path, '[project]\ndependencies = ' + json.dumps([invalid, "demo"]) + '\n')
    _distribution(project, "demo", "1.0", f"Requires-Dist: {invalid}")
    snapshot = check_python_packages(project)
    assert snapshot["requirements"][0]["status"] == "invalid_metadata"
    assert snapshot["requirements"][1]["status"] == "satisfied"
    assert {issue["code"] for issue in snapshot["issues"]} == {"invalid_requirement", "invalid_metadata"}
    assert [node["id"] for node in snapshot["dependency_graph"]["nodes"]] == ["demo"]


def test_checks_include_manually_installed_packages_outside_declared_closure(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["demo"]\n')
    _distribution(project, "demo", "1.0")
    _distribution(project, "manual", "1.0", "Requires-Dist: missing", "Requires-Python: <2")
    snapshot = check_python_packages(project)
    assert snapshot["dependency_graph"]["other_roots"] == ["manual"]
    assert {(issue["code"], issue["package"]) for issue in snapshot["issues"]} == {
        ("dependency_missing", "manual"), ("python_version_mismatch", "manual"),
    }


def test_market_check_uses_release_size_limit_and_asset_exemptions(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = []\n[tool.neko.build]\nexclude=["vendor/excluded.txt"]\n')
    vendor = project / "vendor"
    vendor.mkdir()
    for name in ("large.txt", "excluded.txt", "model.onnx", "image.png", "boundary.txt"):
        with (vendor / name).open("wb") as stream:
            stream.truncate(2 * 1024 * 1024 + (name != "boundary.txt"))
    assert [issue["path"] for issue in check_python_packages(project)["market_issues"]] == ["vendor/large.txt"]


def test_save_refuses_to_replace_a_symlinked_pyproject(tmp_path):
    project = _project(tmp_path, '[project]\ndependencies = ["old"]\n')
    path = project / "pyproject.toml"
    shared = tmp_path / "shared.toml"
    path.replace(shared)
    try:
        path.symlink_to(shared)
    except OSError:
        pytest.skip("symlink creation requires OS permission")
    with pytest.raises(PythonPackagesError) as error:
        save_python_requirements(project, ["new"], inspect_python_packages(project)["revision"])
    assert error.value.code == "PYTHON_PACKAGES_FORMAT_UNSUPPORTED"
    assert path.is_symlink()
    assert tomllib.loads(shared.read_text())["project"]["dependencies"] == ["old"]


def test_save_refuses_a_project_replaced_while_acquiring_its_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from plugin.neko_plugin_cli.core import python_packages

    project = _project(tmp_path, '[project]\ndependencies = ["old"]\n')
    revision = inspect_python_packages(project)["revision"]
    moved = tmp_path / "moved"

    @contextmanager
    def lock(directory):
        directory.rename(moved)
        directory.mkdir()
        (directory / "pyproject.toml").write_bytes((moved / "pyproject.toml").read_bytes())
        yield

    monkeypatch.setattr(python_packages, "dependency_lock", lock)
    with pytest.raises(PythonPackagesError) as error:
        save_python_requirements(project, ["new"], revision)
    assert error.value.code == "PYTHON_PACKAGES_REVISION_CONFLICT"
    assert tomllib.loads((project / "pyproject.toml").read_text())["project"]["dependencies"] == ["old"]


def test_save_rechecks_external_editor_changes_after_staging(tmp_path, monkeypatch):
    from plugin.neko_plugin_cli.core import python_packages

    project = _project(tmp_path, '[project]\ndependencies = ["old"]\n')
    revision = inspect_python_packages(project)["revision"]
    path = project / "pyproject.toml"
    changed = '[project]\ndependencies = ["editor-change"]\n'
    real_fsync = python_packages.os.fsync

    def fsync(descriptor):
        real_fsync(descriptor)
        path.write_text(changed, encoding="utf-8")

    monkeypatch.setattr(python_packages.os, "fsync", fsync)
    with pytest.raises(PythonPackagesError) as error:
        save_python_requirements(project, ["new"], revision)
    assert error.value.code == "PYTHON_PACKAGES_REVISION_CONFLICT"
    assert path.read_text() == changed
    assert not list(project.glob(".python-packages-*"))


@pytest.mark.parametrize("invalid", [
    'legacy; python_version ~= "not-a-version"', 'legacy; "feature" in dependency_groups',
])
def test_save_keeps_unchanged_legacy_markers_that_cannot_be_evaluated(tmp_path, invalid):
    project = _project(tmp_path, '[project]\ndependencies = ' + json.dumps([invalid]) + '\n')
    saved = save_python_requirements(project, [invalid, "new-package"], inspect_python_packages(project)["revision"])
    assert [row["requirement"] for row in saved["requirements"]] == [invalid, "new-package"]
    assert saved["requirements"][0]["status"] == "invalid_metadata"


@pytest.mark.parametrize("declarations", [
    ["demo_lib>=1", "Demo-Lib>=1"],
    ["demo[extra_feature]>=1", "Demo[EXTRA.Feature]>=1"],
])
def test_save_rejects_normalized_duplicate_distribution_names_and_extras(tmp_path, declarations):
    project = _project(tmp_path, '[project]\ndependencies = []\n')
    original = (project / "pyproject.toml").read_bytes()
    with pytest.raises(PythonPackagesError) as error:
        save_python_requirements(project, declarations, inspect_python_packages(project)["revision"])
    assert error.value.code == "PYTHON_PACKAGES_REQUIREMENTS_INVALID"
    assert (project / "pyproject.toml").read_bytes() == original


@pytest.mark.parametrize("declarations", [
    ["demo_lib>=1", "Demo-Lib<3"],
    ['demo; python_version < "3.11"', 'Demo; python_version >= "3.11"'],
    ["demo[feature_one]", "Demo[feature_two]"],
])
def test_save_preserves_distinct_specifiers_markers_and_extras(tmp_path, declarations):
    project = _project(tmp_path, '[project]\ndependencies = []\n')
    saved = save_python_requirements(project, declarations, inspect_python_packages(project)["revision"])
    assert [row["requirement"] for row in saved["requirements"]] == declarations


@pytest.mark.parametrize("dynamic", ["false", '"dependencies"', "[false]", "{}"])
def test_inspection_reports_invalid_dynamic_field_as_a_project_error(tmp_path, dynamic):
    project = _project(tmp_path, '[project]\ndynamic = ' + dynamic + '\ndependencies = []\n')
    with pytest.raises(PythonPackagesError) as error:
        inspect_python_packages(project)
    assert error.value.code == "PYTHON_PACKAGES_PROJECT_INVALID"


@pytest.mark.parametrize("invalid", [
    "legacy ???", 'legacy; python_version ~= "not-a-version"', 'legacy; "feature" in dependency_groups',
])
@pytest.mark.parametrize("old_count", [1, 2])
def test_save_cannot_add_copies_of_legacy_invalid_declarations(tmp_path, invalid, old_count):
    project = _project(tmp_path, '[project]\ndependencies = ' + json.dumps([invalid] * old_count) + '\n')
    path = project / "pyproject.toml"
    original = path.read_bytes()
    with pytest.raises(PythonPackagesError) as error:
        save_python_requirements(project, [invalid] * (old_count + 1), inspect_python_packages(project)["revision"])
    assert error.value.code == "PYTHON_PACKAGES_REQUIREMENTS_INVALID"
    assert path.read_bytes() == original


@pytest.mark.parametrize("invalid", [
    "legacy ???", 'legacy; python_version ~= "not-a-version"', 'legacy; "feature" in dependency_groups',
])
@pytest.mark.parametrize("old_count", [1, 2])
@pytest.mark.parametrize("remove_one", [False, True])
def test_save_can_keep_or_reduce_original_invalid_declaration_occurrences(tmp_path, invalid, old_count, remove_one):
    project = _project(tmp_path, '[project]\ndependencies = ' + json.dumps([invalid] * old_count) + '\n')
    declarations = [invalid] * (old_count - remove_one) + ["valid-package"]
    saved = save_python_requirements(project, declarations, inspect_python_packages(project)["revision"])
    assert [item["requirement"] for item in saved["requirements"]] == declarations
