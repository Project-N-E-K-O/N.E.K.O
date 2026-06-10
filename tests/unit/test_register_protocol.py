from scripts import register_protocol


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
    monkeypatch.setattr(register_protocol.os, "system", lambda _cmd: 0)

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
    monkeypatch.setattr(register_protocol.os, "system", lambda _cmd: 0)

    assert register_protocol._register_macos() is True

    handler = tmp_path / "Applications" / "NekoProtocolHandler.app" / "Contents" / "MacOS" / "handler"
    content = handler.read_text(encoding="utf-8")
    # str(project_root) keeps the expected separators portable across hosts.
    assert f"cd '{project_root}' || exit 1" in content
    assert f"export PYTHONPATH='{project_root}'${{PYTHONPATH:+:$PYTHONPATH}}" in content
    assert "exec '/opt/N E K O/bin/python' -m plugin.server.market_protocol_handler \"$@\"" in content
