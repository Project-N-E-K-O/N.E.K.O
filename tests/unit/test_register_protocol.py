from scripts import register_protocol


def test_linux_desktop_exec_quotes_python_path_with_spaces() -> None:
    quoted = register_protocol._desktop_exec_quote('/opt/N E K O/bin/python"3')

    assert quoted == '"/opt/N E K O/bin/python\\"3"'


def test_linux_desktop_entry_sets_repo_working_directory(
    monkeypatch, tmp_path,
) -> None:
    apps_dir = tmp_path / ".local" / "share" / "applications"

    monkeypatch.setattr(register_protocol.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(register_protocol, "PYTHON_EXE", "/opt/N E K O/bin/python")
    monkeypatch.setattr(register_protocol, "PROJECT_ROOT", tmp_path / "N E K O")
    monkeypatch.setattr(register_protocol.os, "system", lambda _cmd: 0)

    assert register_protocol._register_linux() is True

    desktop_file = apps_dir / "neko-protocol-handler.desktop"
    content = desktop_file.read_text(encoding="utf-8")
    assert 'Exec="/opt/N E K O/bin/python" -m plugin.server.market_protocol_handler %u' in content
    assert f"Path={tmp_path}/N E K O" in content


def test_linux_desktop_path_uses_entry_value_escaping_without_quotes() -> None:
    escaped = register_protocol._desktop_entry_value_escape("/tmp/N E K O\\repo")

    assert escaped == "/tmp/N E K O\\\\repo"
