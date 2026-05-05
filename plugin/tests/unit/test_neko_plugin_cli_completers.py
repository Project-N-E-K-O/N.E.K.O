from __future__ import annotations

from pathlib import Path


from plugin.neko_plugin_cli.commands._completers import (
    _make_package_file_completer,
    _make_plugin_name_completer,
)


def test_plugin_name_bash_completer_checks_direct_plugin_toml() -> None:
    completer = _make_plugin_name_completer()["bash"]

    assert "plugin.toml" in completer
    assert "grep -l" not in completer
    assert "for d in" in completer


def test_package_file_zsh_completer_uses_single_glob_alternation() -> None:
    completer = _make_package_file_completer()["zsh"]

    assert completer == "_files -g '*.(neko-plugin|neko-bundle)'"
