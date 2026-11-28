import os
import tarfile
from io import BytesIO
from zipfile import ZipFile

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from launcher_core import bootstrap
from plugin.neko_plugin_cli.tool_resources import plugin_tool_resources_dir
from scripts import build_plugin_tools, check_plugin_tools


def test_tool_build_failure_leaves_destination_available_for_retry(tmp_path, monkeypatch):
    destination = tmp_path / "plugin-tools"

    def fail(directory):
        (directory / "partial.whl").touch()
        raise RuntimeError("wheel build failed")

    monkeypatch.setattr(build_plugin_tools, "_build_resources", fail)
    with pytest.raises(RuntimeError, match="wheel build failed"):
        build_plugin_tools.stage(destination)
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setattr(build_plugin_tools, "_build_resources", lambda directory: (directory / "manifest.json").touch())
    build_plugin_tools.stage(destination)
    assert (destination / "manifest.json").is_file()


def test_windows_zip_preserves_backend_layout(tmp_path):
    archive = tmp_path / "desktop.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("N.E.K.O.exe", "")
        bundle.writestr("resources/bin/projectneko_server.exe", "")
        bundle.writestr("resources/bin/plugin-tools/manifest.json", "{}")

    with check_plugin_tools._packaged_backend(archive) as backend:
        assert backend.name == "projectneko_server.exe"
        assert (plugin_tool_resources_dir(backend) / "manifest.json").is_file()
    assert not backend.exists()


@pytest.mark.parametrize(
    ("platform", "desktop", "backend_path", "resources", "outer_signed"),
    [
        ("linux", "n.e.k.o", "resources/bin/projectneko_server", "resources/bin/plugin-tools", False),
        (
            "darwin", "Contents/MacOS/N.E.K.O",
            "Contents/Resources/bin/projectneko_server.app/Contents/MacOS/projectneko_server",
            "Contents/Resources/bin/projectneko_server.app/Contents/Resources/plugin-tools",
            True,
        ),
    ],
)
def test_portable_tar_preserves_backend_layout(
    tmp_path, monkeypatch, platform, desktop, backend_path, resources, outer_signed,
):
    root = tmp_path / "app"
    for name in (desktop, backend_path, f"{resources}/manifest.json"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    if outer_signed:
        seal = root / "Contents/_CodeSignature/CodeResources"
        seal.parent.mkdir()
        seal.touch()

    library = root / backend_path
    library.chmod(0o755)
    if os.name == "posix":
        library.with_name("backend-link").symlink_to(library.name)

    archive = tmp_path / "desktop.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(root, arcname=".")

    calls = []
    monkeypatch.setattr(check_plugin_tools.sys, "platform", platform)
    monkeypatch.setattr(check_plugin_tools.subprocess, "run", lambda command, **kwargs: calls.append(command))
    with check_plugin_tools._packaged_backend(archive) as backend:
        assert backend.as_posix().endswith(f"/{backend_path}")
        assert plugin_tool_resources_dir(backend).as_posix().endswith(f"/{resources}")
        assert (plugin_tool_resources_dir(backend) / "manifest.json").read_text() == "fixture"
        if os.name == "posix":
            assert backend.stat().st_mode & 0o100
            assert backend.with_name("backend-link").resolve() == backend
        if platform == "darwin":
            verify = ["codesign", "--verify", "--deep", "--strict"]
            assert calls == [[*verify, str(backend.parents[2])], [*verify, str(backend.parents[6])]]
        else:
            assert calls == []
    assert not backend.exists()


def test_appimage_resolves_the_extracted_backend(tmp_path, monkeypatch):
    artifact = tmp_path / "desktop.AppImage"
    artifact.touch()

    def extract(command, *, cwd, **kwargs):
        assert command == [str(artifact), "--appimage-extract"]
        backend = cwd / "squashfs-root/resources/bin/projectneko_server"
        backend.parent.mkdir(parents=True)
        backend.touch()

    monkeypatch.setattr(check_plugin_tools.subprocess, "run", extract)
    with check_plugin_tools._packaged_backend(artifact) as backend:
        assert backend.as_posix().endswith("/squashfs-root/resources/bin/projectneko_server")
    assert not backend.exists()


def test_portable_tar_rejects_paths_outside_the_extraction_root(tmp_path):
    archive = tmp_path / "desktop.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        entry = tarfile.TarInfo("../escaped")
        entry.size = 1
        bundle.addfile(entry, BytesIO(b"x"))

    with pytest.raises(tarfile.OutsideDestinationError):
        with check_plugin_tools._packaged_backend(archive):
            pytest.fail("The invalid archive should never expose a backend")


def test_packaged_desktop_path_preserves_existing_tool_precedence(tmp_path, monkeypatch):
    home = tmp_path / "home"
    user_bin = home / ".local" / "bin"
    user_bin.mkdir(parents=True)
    existing = str(tmp_path / "system" / "bin")
    monkeypatch.setattr(bootstrap, "IS_FROZEN", True)
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.Path, "home", lambda: home)
    monkeypatch.setenv("PATH", existing)

    bootstrap._configure_desktop_tool_path()
    bootstrap._configure_desktop_tool_path()

    assert os.get_exec_path() == [existing, str(user_bin)]


def test_plugin_tools_group_carries_what_the_sdk_imports_in_plugin_tests():
    # The CLI wheel is installed with --no-deps, so the group is all the tool
    # environment gets. A real PluginContext probes ``images``, which imports PIL.
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    group = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["dependency-groups"]["plugin-tools"]
    names = {canonicalize_name(Requirement(entry).name) for entry in group}
    assert {"pillow", "pytest", "portalocker", "httpx"} <= names
    locked = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    (project,) = [package for package in locked["package"] if package.get("source", {}).get("editable") == "."]
    assert "pillow" in {entry["name"] for entry in project["dev-dependencies"]["plugin-tools"]}


def test_plugin_tools_group_carries_the_pytest_plugins_pytest_ini_configures():
    # The packaged precheck runs plugin tests with the repository pytest.ini; an
    # option whose plugin is missing only warns, so e.g. timeout = 300 would be
    # silently ignored.
    import configparser
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    config = configparser.ConfigParser(interpolation=None)
    config.read(root / "pytest.ini", encoding="utf-8")
    options = config["pytest"]
    needed = set()
    if "timeout" in options:
        needed.add("pytest-timeout")
    if any(name.startswith("asyncio_") for name in options):
        needed.add("pytest-asyncio")
    if "--randomly-" in options.get("addopts", ""):
        needed.add("pytest-randomly")
    assert needed == {"pytest-timeout", "pytest-asyncio", "pytest-randomly"}

    group = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["dependency-groups"]["plugin-tools"]
    names = {canonicalize_name(Requirement(entry).name) for entry in group}
    assert needed <= names
    locked = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    (project,) = [package for package in locked["package"] if package.get("source", {}).get("editable") == "."]
    assert needed <= {entry["name"] for entry in project["dev-dependencies"]["plugin-tools"]}


def test_manifest_records_the_interpreter_architecture_the_runtime_checks(tmp_path, monkeypatch):
    import json

    from plugin.server.infrastructure import plugin_tool_runtime

    def run(command, **kwargs):
        if command[:2] == ["uv", "build"]:
            (tmp_path / "cli-1.0.0-py3-none-any.whl").write_bytes(b"wheel")
        else:
            (tmp_path / "requirements.txt").write_text("pytest==0\n", encoding="utf-8")

    monkeypatch.setattr(build_plugin_tools.subprocess, "run", run)
    monkeypatch.setattr(build_plugin_tools.platform, "machine", lambda: "ARM64")
    monkeypatch.setattr(plugin_tool_runtime.sysconfig, "get_platform", lambda: "win-amd64")
    build_plugin_tools._build_resources(tmp_path)

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["architecture"] == "x86_64"
