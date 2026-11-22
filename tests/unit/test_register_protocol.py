import subprocess
import sys

import pytest

from scripts import register_protocol


def _fake_commands(monkeypatch, returncodes=None, missing=()):
    """Replace subprocess.run; record argv and return per-command exit codes."""
    calls: list[list[str]] = []
    returncodes = returncodes or {}

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        name = args[0].rsplit("/", 1)[-1]
        if name in missing:
            raise FileNotFoundError(2, "No such file or directory", args[0])
        return subprocess.CompletedProcess(args, returncodes.get(name, 0))

    monkeypatch.setattr(register_protocol.subprocess, "run", fake_run)
    return calls


def test_linux_desktop_exec_quotes_python_path_with_spaces() -> None:
    quoted = register_protocol._desktop_exec_quote('/opt/N E K O/bin/python"3')

    assert quoted == '"/opt/N E K O/bin/python\\"3"'


def test_linux_desktop_entry_sets_repo_working_directory(
    monkeypatch, tmp_path,
) -> None:
    apps_dir = tmp_path / ".local" / "share" / "applications"
    project_root = tmp_path / "N E K O"

    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(register_protocol, "PYTHON_EXE", "/opt/N E K O/bin/python")
    monkeypatch.setattr(register_protocol, "PROJECT_ROOT", project_root)
    _fake_commands(monkeypatch)

    assert register_protocol._register_linux() is True

    desktop_file = apps_dir / "neko-protocol-handler.desktop"
    content = desktop_file.read_text(encoding="utf-8")
    assert 'Exec="/opt/N E K O/bin/python" -m plugin.server.market_protocol_handler %u' in content
    # str(project_root) keeps the test portable: separators differ between
    # POSIX and Windows hosts, and Path= values escape backslashes.
    expected_path = str(project_root).replace("\\", "\\\\")
    assert f"Path={expected_path}" in content


def test_linux_desktop_path_uses_entry_value_escaping_without_quotes() -> None:
    escaped = register_protocol._desktop_entry_value_escape("/tmp/N E K O\\repo")

    assert escaped == "/tmp/N E K O\\\\repo"


def test_windows_registry_command_sets_repo_context(monkeypatch, tmp_path) -> None:
    written: dict[str, str] = {}

    class _Key:
        def __init__(self, path: str):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class _WinReg:
        HKEY_CURRENT_USER = object()
        REG_SZ = object()

        @staticmethod
        def CreateKey(_root, path):
            return _Key(path)

        @staticmethod
        def SetValueEx(key, name, _reserved, _value_type, value):
            if key.path.endswith(r"shell\open\command") and name == "":
                written["command"] = value

    monkeypatch.setitem(__import__("sys").modules, "winreg", _WinReg)
    monkeypatch.setattr(register_protocol, "PYTHON_EXE", r"C:\Program Files\Python\python.exe")
    monkeypatch.setattr(register_protocol, "PROJECT_ROOT", tmp_path / "N E K O")

    assert register_protocol._register_windows() is True

    command = written["command"]
    assert command.startswith("cmd.exe /d /c cd /d ")
    assert f'"{tmp_path / "N E K O"}"' in command
    # The assignment is quoted as ``set "PYTHONPATH=...;%PYTHONPATH%"`` so
    # repo paths containing spaces do not leak literal quotes into the value.
    assert f'set "PYTHONPATH={tmp_path / "N E K O"};%PYTHONPATH%"' in command
    assert r'"C:\Program Files\Python\python.exe" -m plugin.server.market_protocol_handler "%1"' in command


def test_macos_helper_sets_repo_context(monkeypatch, tmp_path) -> None:
    project_root = tmp_path / "N E K O"

    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(register_protocol, "PYTHON_EXE", "/opt/N E K O/bin/python")
    monkeypatch.setattr(register_protocol, "PROJECT_ROOT", project_root)
    _fake_commands(monkeypatch)

    assert register_protocol._register_macos() is True

    handler = tmp_path / "Applications" / "NekoProtocolHandler.app" / "Contents" / "MacOS" / "handler"
    content = handler.read_text(encoding="utf-8")
    # str(project_root) keeps the expected separators portable across hosts.
    assert f"cd '{project_root}' || exit 1" in content
    assert f"export PYTHONPATH='{project_root}'${{PYTHONPATH:+:$PYTHONPATH}}" in content
    assert "exec '/opt/N E K O/bin/python' -m plugin.server.market_protocol_handler \"$@\"" in content


def test_linux_register_runs_xdg_mime_and_optional_refresh(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    calls = _fake_commands(monkeypatch)

    assert register_protocol._register_linux() is True

    apps_dir = tmp_path / ".local" / "share" / "applications"
    assert calls == [
        ["update-desktop-database", str(apps_dir)],
        ["xdg-mime", "default", "neko-protocol-handler.desktop", "x-scheme-handler/neko"],
    ]


@pytest.mark.parametrize(
    ("returncodes", "missing"),
    [({"xdg-mime": 3}, ()), ({}, ("xdg-mime",))],
    ids=["nonzero", "missing"],
)
def test_linux_register_fails_when_xdg_mime_fails(
    monkeypatch, tmp_path, capsys, returncodes, missing,
) -> None:
    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    _fake_commands(monkeypatch, returncodes=returncodes, missing=missing)

    assert register_protocol._register_linux() is False

    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "[OK]" not in out


@pytest.mark.parametrize(
    ("returncodes", "missing"),
    [({"update-desktop-database": 1}, ()), ({}, ("update-desktop-database",))],
    ids=["nonzero", "missing"],
)
def test_linux_register_tolerates_optional_refresh_failure(
    monkeypatch, tmp_path, returncodes, missing,
) -> None:
    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    _fake_commands(monkeypatch, returncodes=returncodes, missing=missing)

    assert register_protocol._register_linux() is True


def test_macos_register_runs_lsregister_without_shell(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    calls = _fake_commands(monkeypatch)

    assert register_protocol._register_macos() is True

    app_dir = tmp_path / "Applications" / "NekoProtocolHandler.app"
    assert calls == [[register_protocol.LSREGISTER, "-R", str(app_dir)]]


@pytest.mark.parametrize(
    ("returncodes", "missing"),
    [({"lsregister": 1}, ()), ({}, ("lsregister",))],
    ids=["nonzero", "missing"],
)
def test_macos_register_fails_when_lsregister_fails(
    monkeypatch, tmp_path, capsys, returncodes, missing,
) -> None:
    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    _fake_commands(monkeypatch, returncodes=returncodes, missing=missing)

    assert register_protocol._register_macos() is False

    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "[OK]" not in out


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_cli_exits_nonzero_when_required_command_fails(
    monkeypatch, tmp_path, capsys, system,
) -> None:
    import runpy

    monkeypatch.setattr(register_protocol.platform, "system", lambda: system)
    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["register_protocol.py"])
    calls = _fake_commands(monkeypatch, returncodes={"xdg-mime": 1, "lsregister": 1})

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(register_protocol.__file__, run_name="__main__")

    assert exc_info.value.code == 1
    required = "xdg-mime" if system == "Linux" else "lsregister"
    assert any(call[0].rsplit("/", 1)[-1] == required for call in calls)
    out = capsys.readouterr().out
    assert f"[FAIL] {required} exited with status 1" in out
    assert "[OK]" not in out


_MIMEAPPS_BEFORE = (
    "[Default Applications]\n"
    "text/html=firefox.desktop\n"
    "x-scheme-handler/neko=neko-protocol-handler.desktop\n"
    "\n"
    "[Added Associations]\n"
    "x-scheme-handler/neko=other-neko.desktop;neko-protocol-handler.desktop;\n"
    "image/png=viewer.desktop;\n"
)
_MIMEAPPS_AFTER = (
    "[Default Applications]\n"
    "text/html=firefox.desktop\n"
    "\n"
    "[Added Associations]\n"
    "x-scheme-handler/neko=other-neko.desktop;\n"
    "image/png=viewer.desktop;\n"
)


def _linux_home(monkeypatch, tmp_path):
    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    apps_dir = tmp_path / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True)
    desktop_file = apps_dir / "neko-protocol-handler.desktop"
    desktop_file.write_text("[Desktop Entry]\n", encoding="utf-8")
    return apps_dir, desktop_file


def test_linux_remove_drops_default_association(monkeypatch, tmp_path) -> None:
    apps_dir, desktop_file = _linux_home(monkeypatch, tmp_path)
    config_list = tmp_path / "xdg-config" / "mimeapps.list"
    config_list.parent.mkdir()
    config_list.write_text(_MIMEAPPS_BEFORE, encoding="utf-8")
    legacy_list = apps_dir / "mimeapps.list"
    legacy_list.write_text(
        "[Default Applications]\nx-scheme-handler/neko = neko-protocol-handler.desktop\n",
        encoding="utf-8",
    )
    calls = _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert config_list.read_text(encoding="utf-8") == _MIMEAPPS_AFTER
    assert legacy_list.read_text(encoding="utf-8") == "[Default Applications]\n"
    assert not desktop_file.exists()
    assert calls == [["update-desktop-database", str(apps_dir)]]
    assert sorted(p.name for p in config_list.parent.iterdir()) == ["mimeapps.list"]


def test_linux_remove_leaves_unrelated_mimeapps_untouched(monkeypatch, tmp_path) -> None:
    _linux_home(monkeypatch, tmp_path)
    config_list = tmp_path / "xdg-config" / "mimeapps.list"
    config_list.parent.mkdir()
    # Our handler in an unrelated group or for another type is not ours to drop.
    original = (
        "[Removed Associations]\n"
        "x-scheme-handler/neko=neko-protocol-handler.desktop\n"
        "[Default Applications]\n"
        "x-scheme-handler/other=neko-protocol-handler.desktop\n"
    )
    config_list.write_text(original, encoding="utf-8")
    before = config_list.stat().st_mtime_ns
    _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert config_list.read_text(encoding="utf-8") == original
    assert config_list.stat().st_mtime_ns == before


def test_linux_mimeapps_paths_ignore_relative_xdg_config_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/config")

    assert register_protocol._linux_mimeapps_paths()[0] == tmp_path / ".config" / "mimeapps.list"


def test_linux_remove_reports_association_write_failure(
    monkeypatch, tmp_path, capsys,
) -> None:
    _, desktop_file = _linux_home(monkeypatch, tmp_path)
    config_list = tmp_path / "xdg-config" / "mimeapps.list"
    config_list.parent.mkdir()
    config_list.write_text(_MIMEAPPS_BEFORE, encoding="utf-8")
    _fake_commands(monkeypatch)

    def fail_replace(*_args):
        raise PermissionError("read-only")

    monkeypatch.setattr(register_protocol.os, "replace", fail_replace)

    assert register_protocol._remove_linux() is False

    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "[OK]" not in out
    # The user's file is intact and no temporary file is left behind.
    assert config_list.read_text(encoding="utf-8") == _MIMEAPPS_BEFORE
    assert sorted(p.name for p in config_list.parent.iterdir()) == ["mimeapps.list"]
    assert not desktop_file.exists()


def _macos_app(monkeypatch, tmp_path):
    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    app_dir = tmp_path / "Applications" / "NekoProtocolHandler.app"
    (app_dir / "Contents").mkdir(parents=True)
    return app_dir


def test_macos_remove_unregisters_before_deleting(monkeypatch, tmp_path) -> None:
    app_dir = _macos_app(monkeypatch, tmp_path)
    seen_existing: list[bool] = []
    calls = _fake_commands(monkeypatch)
    original_run = register_protocol.subprocess.run

    def run_and_check_bundle(args, **kwargs):
        seen_existing.append(app_dir.exists())
        return original_run(args, **kwargs)

    monkeypatch.setattr(register_protocol.subprocess, "run", run_and_check_bundle)

    assert register_protocol._remove_macos() is True

    assert calls == [[register_protocol.LSREGISTER, "-u", str(app_dir)]]
    assert seen_existing == [True]
    assert not app_dir.exists()


@pytest.mark.parametrize(
    ("returncodes", "missing"),
    [({"lsregister": 1}, ()), ({}, ("lsregister",))],
    ids=["nonzero", "missing"],
)
def test_macos_remove_reports_unregister_failure(
    monkeypatch, tmp_path, capsys, returncodes, missing,
) -> None:
    app_dir = _macos_app(monkeypatch, tmp_path)
    _fake_commands(monkeypatch, returncodes=returncodes, missing=missing)

    assert register_protocol._remove_macos() is False

    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "[OK]" not in out
    # The bundle is kept so a rerun of --remove can still unregister it.
    assert app_dir.exists()
    assert f"rm -rf '{app_dir}'" in out
    assert "Only if lsregister is" in out


def test_macos_remove_without_app_skips_lsregister(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    calls = _fake_commands(monkeypatch)

    assert register_protocol._remove_macos() is True
    assert calls == []


def test_linux_remove_preserves_crlf_and_missing_final_newline(monkeypatch, tmp_path) -> None:
    _linux_home(monkeypatch, tmp_path)
    config_list = tmp_path / "xdg-config" / "mimeapps.list"
    config_list.parent.mkdir()
    config_list.write_bytes(
        b"[Default Applications]\r\n"
        b"text/html=firefox.desktop\r\n"
        b"x-scheme-handler/neko=other.desktop;neko-protocol-handler.desktop\r\n"
        b"# comment \xc3\xa9\x0c kept\r\n"
        b"image/png=viewer.desktop"
    )
    _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert config_list.read_bytes() == (
        b"[Default Applications]\r\n"
        b"text/html=firefox.desktop\r\n"
        b"x-scheme-handler/neko=other.desktop\r\n"
        b"# comment \xc3\xa9\x0c kept\r\n"
        b"image/png=viewer.desktop"
    )


def test_linux_remove_edits_symlinked_mimeapps_in_place(monkeypatch, tmp_path) -> None:
    _linux_home(monkeypatch, tmp_path)
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "mimeapps.list"
    target.write_text(_MIMEAPPS_BEFORE, encoding="utf-8")
    link = tmp_path / "xdg-config" / "mimeapps.list"
    link.parent.mkdir()
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are not available on this host")
    _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == _MIMEAPPS_AFTER
    assert sorted(p.name for p in dotfiles.iterdir()) == ["mimeapps.list"]


def test_linux_remove_covers_desktop_specific_mimeapps(monkeypatch, tmp_path) -> None:
    _linux_home(monkeypatch, tmp_path)
    config_dir = tmp_path / "xdg-config"
    config_dir.mkdir()
    gnome_list = config_dir / "gnome-mimeapps.list"
    gnome_list.write_text(_MIMEAPPS_BEFORE, encoding="utf-8")
    _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert gnome_list.read_text(encoding="utf-8") == _MIMEAPPS_AFTER


def test_linux_remove_skips_non_utf8_file_without_neko_entry(monkeypatch, tmp_path) -> None:
    _linux_home(monkeypatch, tmp_path)
    config_dir = tmp_path / "xdg-config"
    config_dir.mkdir()
    kde_list = config_dir / "kde-mimeapps.list"
    original = b"[Default Applications]\ntext/plain=caf\xe9.desktop\n"
    kde_list.write_bytes(original)
    before = kde_list.stat().st_mtime_ns
    _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert kde_list.read_bytes() == original
    assert kde_list.stat().st_mtime_ns == before


def test_linux_remove_keeps_non_utf8_bytes_when_dropping_entry(monkeypatch, tmp_path) -> None:
    _linux_home(monkeypatch, tmp_path)
    config_list = tmp_path / "xdg-config" / "mimeapps.list"
    config_list.parent.mkdir()
    config_list.write_bytes(
        b"[Default Applications]\n"
        b"text/plain=caf\xe9.desktop\n"
        b"x-scheme-handler/neko=\xff.desktop;neko-protocol-handler.desktop\n"
    )
    _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert config_list.read_bytes() == (
        b"[Default Applications]\n"
        b"text/plain=caf\xe9.desktop\n"
        b"x-scheme-handler/neko=\xff.desktop\n"
    )


def test_linux_remove_crlf_unterminated_last_line_leaves_no_stray_cr(
    monkeypatch, tmp_path,
) -> None:
    _linux_home(monkeypatch, tmp_path)
    config_list = tmp_path / "xdg-config" / "mimeapps.list"
    config_list.parent.mkdir()
    config_list.write_bytes(
        b"[Default Applications]\r\n"
        b"text/html=firefox.desktop\r\n"
        b"x-scheme-handler/neko=neko-protocol-handler.desktop"
    )
    _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert config_list.read_bytes() == (
        b"[Default Applications]\r\n"
        b"text/html=firefox.desktop"
    )


def test_linux_remove_refreshes_database_when_desktop_file_already_gone(
    monkeypatch, tmp_path,
) -> None:
    apps_dir, desktop_file = _linux_home(monkeypatch, tmp_path)
    desktop_file.unlink()
    calls = _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True
    assert calls == [["update-desktop-database", str(apps_dir)]]


def test_linux_remove_keeps_surviving_handler_text(monkeypatch, tmp_path) -> None:
    _linux_home(monkeypatch, tmp_path)
    config_list = tmp_path / "xdg-config" / "mimeapps.list"
    config_list.parent.mkdir()
    config_list.write_text(
        "[Added Associations]\n"
        "x-scheme-handler/neko=other.desktop; another.desktop;neko-protocol-handler.desktop;\n"
        "[Default Applications]\n"
        "x-scheme-handler/neko= neko-protocol-handler.desktop ;other.desktop\n",
        encoding="utf-8",
    )
    _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert config_list.read_text(encoding="utf-8") == (
        "[Added Associations]\n"
        "x-scheme-handler/neko=other.desktop; another.desktop;\n"
        "[Default Applications]\n"
        "x-scheme-handler/neko=other.desktop\n"
    )


def test_linux_remove_does_not_follow_planted_temp_symlink(monkeypatch, tmp_path) -> None:
    _linux_home(monkeypatch, tmp_path)
    config_dir = tmp_path / "xdg-config"
    config_dir.mkdir()
    config_list = config_dir / "mimeapps.list"
    config_list.write_text(_MIMEAPPS_BEFORE, encoding="utf-8")
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    # The old fixed temporary name; a symlink there must not be written through.
    planted = config_dir / "mimeapps.list.neko-tmp"
    try:
        planted.symlink_to(victim)
    except OSError:
        pytest.skip("symlinks are not available on this host")
    _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert victim.read_text(encoding="utf-8") == "keep me"
    assert planted.is_symlink()
    assert config_list.read_text(encoding="utf-8") == _MIMEAPPS_AFTER
    assert sorted(p.name for p in config_dir.iterdir()) == [
        "mimeapps.list",
        "mimeapps.list.neko-tmp",
    ]


def test_linux_remove_handles_utf8_bom(monkeypatch, tmp_path) -> None:
    _linux_home(monkeypatch, tmp_path)
    config_list = tmp_path / "xdg-config" / "mimeapps.list"
    config_list.parent.mkdir()
    config_list.write_bytes(
        b"\xef\xbb\xbf[Default Applications]\n"
        b"x-scheme-handler/neko=neko-protocol-handler.desktop\n"
        b"text/html=firefox.desktop\n"
    )
    _fake_commands(monkeypatch)

    assert register_protocol._remove_linux() is True

    assert config_list.read_bytes() == (
        b"\xef\xbb\xbf[Default Applications]\n"
        b"text/html=firefox.desktop\n"
    )
