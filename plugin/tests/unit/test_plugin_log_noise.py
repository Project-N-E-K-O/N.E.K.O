from __future__ import annotations

import pytest

from plugin import logging_config as plogging
from plugin.server.routes import market_bridge

pytestmark = pytest.mark.plugin_unit


class _RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple]] = []

    def _record(self, level: str, msg, *args, **_kwargs) -> None:
        self.calls.append((level, msg, args))

    def debug(self, msg, *args, **kwargs) -> None:
        self._record("debug", msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs) -> None:
        self._record("info", msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs) -> None:
        self._record("warning", msg, *args, **kwargs)


def test_info_once_logs_info_first_then_debug(monkeypatch):
    monkeypatch.setattr(plogging, "_INFO_ONCE_KEYS", set())
    log = _RecordingLogger()

    plogging.info_once(log, ("k", "a"), "msg {}", 1)
    plogging.info_once(log, ("k", "a"), "msg {}", 2)
    plogging.info_once(log, ("k", "b"), "msg {}", 3)

    assert [(level, args) for level, _msg, args in log.calls] == [
        ("info", (1,)),
        ("debug", (2,)),
        ("info", (3,)),
    ]


def test_missing_plugin_manifest_is_debug(tmp_path, monkeypatch):
    log = _RecordingLogger()
    monkeypatch.setattr(market_bridge, "logger", log)

    result = market_bridge._read_plugin_toml_metadata(tmp_path / "absent" / "plugin.toml")

    assert result == (None, "")
    assert [level for level, _msg, _args in log.calls] == ["debug"]


def test_malformed_plugin_manifest_still_warns(tmp_path, monkeypatch):
    log = _RecordingLogger()
    monkeypatch.setattr(market_bridge, "logger", log)
    manifest = tmp_path / "plugin.toml"
    manifest.write_text("[plugin\nid = ", encoding="utf-8")

    result = market_bridge._read_plugin_toml_metadata(manifest)

    assert result == (None, "")
    assert [level for level, _msg, _args in log.calls] == ["warning"]
