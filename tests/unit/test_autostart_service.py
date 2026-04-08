import importlib
import plistlib
import subprocess
from pathlib import Path

import pytest


autostart_service = importlib.import_module("utils.autostart_service")


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: service tests do not need it."""
    yield


def _patch_home(monkeypatch, home_path: Path):
    monkeypatch.setattr(
        autostart_service.Path,
        "home",
        classmethod(lambda cls: home_path),
    )


@pytest.mark.unit
def test_get_launch_command_prefers_project_venv_in_dev_mode(tmp_path, monkeypatch):
    launcher_script = tmp_path / "launcher.py"
    pyproject = tmp_path / "pyproject.toml"
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python_base = tmp_path / "shared-python"
    fallback_executable = tmp_path / "python"

    launcher_script.write_text("", encoding="utf-8")
    pyproject.write_text("[project]\nname='neko'\nversion='0.0.0'\n", encoding="utf-8")
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python_base.write_text("", encoding="utf-8")
    venv_python.symlink_to(venv_python_base)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /tmp\n", encoding="utf-8")
    fallback_executable.write_text("", encoding="utf-8")

    monkeypatch.setattr(autostart_service.sys, "executable", str(fallback_executable))
    monkeypatch.setattr(autostart_service.sys, "frozen", False, raising=False)
    monkeypatch.setattr(autostart_service, "_get_project_root", lambda: tmp_path)

    command = autostart_service._get_launch_command()

    assert command == [str(venv_python), str(launcher_script)]


@pytest.mark.unit
def test_get_launch_command_falls_back_to_uv_run_when_project_venv_missing(tmp_path, monkeypatch):
    launcher_script = tmp_path / "launcher.py"
    pyproject = tmp_path / "pyproject.toml"
    executable = tmp_path / "python"
    uv_executable = tmp_path / "uv"

    launcher_script.write_text("", encoding="utf-8")
    pyproject.write_text("[project]\nname='neko'\nversion='0.0.0'\n", encoding="utf-8")
    executable.write_text("", encoding="utf-8")
    uv_executable.write_text("", encoding="utf-8")

    monkeypatch.setattr(autostart_service.sys, "executable", str(executable))
    monkeypatch.setattr(autostart_service.sys, "frozen", False, raising=False)
    monkeypatch.setattr(autostart_service, "_get_project_root", lambda: tmp_path)
    monkeypatch.setattr(autostart_service, "_get_uv_executable_path", lambda: uv_executable.resolve())

    command = autostart_service._get_launch_command()

    assert command == [
        str(uv_executable.resolve()),
        "run",
        "--project",
        str(tmp_path),
        "python",
        str(launcher_script),
    ]


@pytest.mark.unit
def test_get_launch_command_falls_back_to_current_interpreter_without_venv_or_uv(tmp_path, monkeypatch):
    executable = tmp_path / "python"
    launcher_script = tmp_path / "launcher.py"
    executable.write_text("", encoding="utf-8")
    launcher_script.write_text("", encoding="utf-8")

    monkeypatch.setattr(autostart_service.sys, "executable", str(executable))
    monkeypatch.setattr(autostart_service.sys, "frozen", False, raising=False)
    monkeypatch.setattr(autostart_service, "_get_project_root", lambda: tmp_path)
    monkeypatch.setattr(autostart_service, "_get_uv_executable_path", lambda: None)

    command = autostart_service._get_launch_command()

    assert command == [str(executable.resolve()), str(launcher_script)]


@pytest.mark.unit
def test_get_current_executable_path_preserves_symlink_path(tmp_path, monkeypatch):
    target = tmp_path / "python-base"
    executable = tmp_path / "python"
    target.write_text("", encoding="utf-8")

    try:
        executable.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink unsupported in this environment: {exc}")

    monkeypatch.setattr(autostart_service.sys, "executable", str(executable))

    result = autostart_service._get_current_executable_path()

    assert result == executable
    assert result != executable.resolve()


@pytest.mark.unit
def test_quote_posix_command_uses_desktop_entry_exec_escaping():
    quoted = autostart_service._quote_posix_command([
        "/usr/bin/neko",
        "hello world",
        'quote"tick`dollar$slash\\',
        "50% done",
        "%f",
        "plain",
    ])

    expected = (
        '/usr/bin/neko '
        '"hello world" '
        + '"quote\\"tick\\`dollar\\$slash'
        + ("\\\\" * 1)
        + '" '
        '"50%% done" '
        '%f '
        'plain'
    )

    assert quoted == expected


@pytest.mark.unit
def test_quote_posix_command_preserves_placeholders_and_escapes_literal_percent():
    quoted = autostart_service._quote_posix_command([
        "/usr/bin/neko",
        "%u",
        "100%",
        "already%%escaped",
        "prefix%x",
    ])

    assert quoted == "/usr/bin/neko %u 100%% already%%escaped prefix%%x"


@pytest.mark.unit
def test_enable_autostart_writes_linux_desktop_entry(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart_service.sys, "platform", "linux")
    monkeypatch.setattr(autostart_service, "_get_launch_command", lambda: ["/usr/bin/neko", "--flag"])
    monkeypatch.setattr(autostart_service, "_get_working_directory", lambda: tmp_path / "app")

    result = autostart_service.enable_autostart()

    desktop_entry = tmp_path / ".config" / "autostart" / "project-neko-autostart.desktop"
    assert result["ok"] is True
    assert result["enabled"] is True
    assert desktop_entry.exists()
    content = desktop_entry.read_text(encoding="utf-8")
    assert "Exec=/usr/bin/neko --flag" in content
    assert f"Path={tmp_path / 'app'}" in content


@pytest.mark.unit
def test_enable_autostart_writes_linux_desktop_entry_to_xdg_config_home(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart_service.sys, "platform", "linux")
    monkeypatch.setattr(autostart_service, "_get_launch_command", lambda: ["/usr/bin/neko", "--flag"])
    monkeypatch.setattr(autostart_service, "_get_working_directory", lambda: tmp_path / "app")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))

    result = autostart_service.enable_autostart()

    desktop_entry = tmp_path / "xdg-config" / "autostart" / "project-neko-autostart.desktop"
    assert result["ok"] is True
    assert result["enabled"] is True
    assert desktop_entry.exists()
    assert result["path"] == str(desktop_entry)


@pytest.mark.unit
def test_enable_autostart_writes_macos_launch_agent_with_logs_and_environment(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart_service.sys, "platform", "darwin")
    monkeypatch.setattr(autostart_service, "_MAC_LAUNCHCTL_EXECUTABLE", "/bin/launchctl")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("TMPDIR", "/tmp/neko")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    venv_root = tmp_path / ".venv"
    command = [str(venv_root / "bin" / "python"), str(tmp_path / "launcher.py")]
    (venv_root / "bin").mkdir(parents=True, exist_ok=True)
    base_python = tmp_path / "python-base"
    base_python.write_text("", encoding="utf-8")
    (venv_root / "bin" / "python").symlink_to(base_python)
    (venv_root / "pyvenv.cfg").write_text("home = /tmp\n", encoding="utf-8")
    (tmp_path / "launcher.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(autostart_service, "_get_launch_command", lambda: command)
    monkeypatch.setattr(autostart_service, "_get_working_directory", lambda: tmp_path)

    launchctl_calls: list[list[str]] = []
    print_calls = {"count": 0}

    def fake_run(args, capture_output, text, check):
        launchctl_calls.append(list(args))
        if args[:2] == ["/bin/launchctl", "bootout"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["/bin/launchctl", "bootstrap"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["/bin/launchctl", "print"]:
            print_calls["count"] += 1
            return subprocess.CompletedProcess(
                args,
                0,
                "state = not running\nlast exit code = 0\n",
                "",
            )
        raise AssertionError(f"Unexpected subprocess call: {args!r}")

    monkeypatch.setattr(autostart_service.subprocess, "run", fake_run)

    result = autostart_service.enable_autostart()

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.project-neko.autostart.plist"
    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["configured"] is True
    assert result["loaded"] is True
    assert plist_path.exists()
    assert ["/bin/launchctl", "bootstrap", f"gui/{autostart_service.os.getuid()}", str(plist_path)] in launchctl_calls
    assert print_calls["count"] >= 1

    payload = plistlib.loads(plist_path.read_bytes())
    log_dir = tmp_path / "Library" / "Application Support" / "N.E.K.O" / "logs"
    stdout_path = log_dir / "com.project-neko.autostart.stdout.log"
    stderr_path = log_dir / "com.project-neko.autostart.stderr.log"

    assert payload["Label"] == "com.project-neko.autostart"
    assert payload["ProgramArguments"] == command
    assert payload["WorkingDirectory"] == str(tmp_path)
    assert payload["StandardOutPath"] == str(stdout_path)
    assert payload["StandardErrorPath"] == str(stderr_path)
    assert log_dir.exists()

    env = payload["EnvironmentVariables"]
    assert env["HOME"] == str(tmp_path)
    assert env["TMPDIR"] == "/tmp/neko"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["VIRTUAL_ENV"] == str(venv_root)
    assert env["PATH"].startswith(f"{venv_root / 'bin'}:")


@pytest.mark.unit
def test_macos_status_requires_launchctl_loaded_service(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart_service.sys, "platform", "darwin")
    monkeypatch.setattr(autostart_service, "_MAC_LAUNCHCTL_EXECUTABLE", "/bin/launchctl")
    monkeypatch.setattr(autostart_service, "_get_launch_command", lambda: ["/usr/bin/neko"])

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.project-neko.autostart.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text("<plist />", encoding="utf-8")

    def fake_run(args, capture_output, text, check):
        if args[:2] == ["/bin/launchctl", "print"]:
            return subprocess.CompletedProcess(args, 113, "", "service not found")
        raise AssertionError(f"Unexpected subprocess call: {args!r}")

    monkeypatch.setattr(autostart_service.subprocess, "run", fake_run)

    result = autostart_service.get_autostart_status()

    assert result["ok"] is True
    assert result["supported"] is True
    assert result["configured"] is True
    assert result["loaded"] is False
    assert result["enabled"] is False
    assert result["reason"] == "launch_agent_not_loaded"


@pytest.mark.unit
def test_status_reports_launch_command_unavailable(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart_service.sys, "platform", "linux")
    monkeypatch.setattr(autostart_service, "_get_launch_command", lambda: [])

    result = autostart_service.get_autostart_status()

    assert result["ok"] is True
    assert result["supported"] is False
    assert result["enabled"] is False
    assert result["reason"] == "launch_command_unavailable"


@pytest.mark.unit
def test_status_preserves_existing_enabled_state_when_launch_command_is_unavailable(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart_service.sys, "platform", "linux")
    monkeypatch.setattr(autostart_service, "_get_launch_command", lambda: [])

    desktop_entry = tmp_path / ".config" / "autostart" / "project-neko-autostart.desktop"
    desktop_entry.parent.mkdir(parents=True, exist_ok=True)
    desktop_entry.write_text("[Desktop Entry]\n", encoding="utf-8")

    result = autostart_service.get_autostart_status()

    assert result["ok"] is True
    assert result["supported"] is False
    assert result["manageable"] is False
    assert result["enabled"] is True
    assert result["reason"] == "launch_command_unavailable"


@pytest.mark.unit
def test_disable_autostart_removes_linux_desktop_entry(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart_service.sys, "platform", "linux")
    monkeypatch.setattr(autostart_service, "_get_launch_command", lambda: ["/usr/bin/neko"])

    desktop_entry = tmp_path / ".config" / "autostart" / "project-neko-autostart.desktop"
    desktop_entry.parent.mkdir(parents=True, exist_ok=True)
    desktop_entry.write_text("[Desktop Entry]\n", encoding="utf-8")

    result = autostart_service.disable_autostart()

    assert result["ok"] is True
    assert result["supported"] is True
    assert result["enabled"] is False
    assert not desktop_entry.exists()


@pytest.mark.unit
def test_linux_status_and_disable_respect_xdg_config_home(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart_service.sys, "platform", "linux")
    monkeypatch.setattr(autostart_service, "_get_launch_command", lambda: ["/usr/bin/neko"])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))

    desktop_entry = tmp_path / "xdg-config" / "autostart" / "project-neko-autostart.desktop"
    desktop_entry.parent.mkdir(parents=True, exist_ok=True)
    desktop_entry.write_text("[Desktop Entry]\n", encoding="utf-8")

    status = autostart_service.get_autostart_status()
    assert status["ok"] is True
    assert status["enabled"] is True
    assert status["path"] == str(desktop_entry)

    result = autostart_service.disable_autostart()
    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["path"] == str(desktop_entry)
    assert not desktop_entry.exists()


@pytest.mark.unit
def test_disable_autostart_boots_out_macos_launch_agent(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart_service.sys, "platform", "darwin")
    monkeypatch.setattr(autostart_service, "_MAC_LAUNCHCTL_EXECUTABLE", "/bin/launchctl")
    monkeypatch.setattr(autostart_service, "_get_launch_command", lambda: ["/usr/bin/neko"])

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.project-neko.autostart.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text("<plist />", encoding="utf-8")

    launchctl_calls: list[list[str]] = []

    def fake_run(args, capture_output, text, check):
        launchctl_calls.append(list(args))
        if args[:2] == ["/bin/launchctl", "bootout"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["/bin/launchctl", "print"]:
            return subprocess.CompletedProcess(args, 113, "", "service not found")
        raise AssertionError(f"Unexpected subprocess call: {args!r}")

    monkeypatch.setattr(autostart_service.subprocess, "run", fake_run)

    result = autostart_service.disable_autostart()

    assert result["ok"] is True
    assert result["supported"] is True
    assert result["configured"] is False
    assert result["loaded"] is False
    assert result["enabled"] is False
    assert not plist_path.exists()
    assert any(call[:2] == ["/bin/launchctl", "bootout"] for call in launchctl_calls)


@pytest.mark.unit
def test_disable_autostart_reports_unsupported_platform(monkeypatch):
    monkeypatch.setattr(autostart_service.sys, "platform", "plan9")

    result = autostart_service.disable_autostart()

    assert result["ok"] is False
    assert result["supported"] is False
    assert result["enabled"] is False
    assert result["error_code"] == "unsupported_platform"
