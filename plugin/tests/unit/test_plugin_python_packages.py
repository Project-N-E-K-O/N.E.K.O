"""Project-local dependency editing, inspection and clean synchronization."""
from __future__ import annotations

import subprocess
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
