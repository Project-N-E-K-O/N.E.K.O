from __future__ import annotations

from pathlib import Path

import pytest

from plugin.sdk_v2.public.core import base_runtime
from plugin.sdk_v2.shared.logging import LogLevel


class _Ctx:
    config_path = Path('/tmp/demo/plugin.toml')
    _effective_config = {
        'plugin': {
            'store': {'enabled': False},
            'database': {'enabled': True, 'name': 'data.db'},
        },
        'plugin_state': {'backend': 'memory'},
    }


def test_base_runtime_resolution_helpers() -> None:
    assert base_runtime.resolve_plugin_dir(_Ctx()) == Path('/tmp/demo')
    assert base_runtime.resolve_plugin_dir(object()) == Path.cwd()
    assert base_runtime.resolve_effective_config(_Ctx()) == _Ctx._effective_config
    assert base_runtime.resolve_effective_config(object()) == {}
    assert base_runtime.resolve_store_enabled(_Ctx._effective_config) is False
    assert base_runtime.resolve_store_enabled({}) is True
    assert base_runtime.resolve_db_config(_Ctx._effective_config) == (True, 'data.db')
    assert base_runtime.resolve_db_config({}) == (True, 'plugin.db')
    assert base_runtime.resolve_state_backend(_Ctx._effective_config) == 'memory'
    assert base_runtime.resolve_state_backend({}) == 'file'


def test_setup_plugin_file_logging_branches(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setattr(base_runtime, 'setup_sdk_logging', lambda **kwargs: calls.setdefault('setup', kwargs))
    monkeypatch.setattr(base_runtime._loguru_logger, 'remove', lambda sink_id: calls.setdefault('removed', sink_id))
    monkeypatch.setattr(base_runtime._loguru_logger, 'add', lambda *args, **kwargs: (calls.setdefault('added', (args, kwargs)), 123)[1])

    same_sink = base_runtime.setup_plugin_file_logging(
        component='plugin.demo',
        parsed_level=LogLevel.INFO,
        log_dir=None,
        max_bytes=None,
        backup_count=None,
        previous_sink_id=7,
    )
    assert same_sink == 7

    sink_id = base_runtime.setup_plugin_file_logging(
        component='plugin.demo',
        parsed_level=LogLevel.INFO,
        log_dir=tmp_path,
        max_bytes=10,
        backup_count=2,
        previous_sink_id=7,
    )
    assert sink_id == 123
    assert calls['removed'] == 7
    assert 'added' in calls
