from pathlib import Path

import pytest


@pytest.mark.unit
def test_logger_config_prefers_selected_storage_root_for_new_logs(tmp_path, monkeypatch):
    from utils.logger_config import RobustLoggerConfig

    selected_root = tmp_path / "selected-root"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(selected_root))

    config = RobustLoggerConfig(service_name="Main")

    assert Path(config.get_log_directory_path()) == selected_root / "logs"
    assert Path(config.get_log_file_path()).parent == selected_root / "logs"


@pytest.mark.unit
def test_logger_config_keeps_plugin_logs_under_selected_storage_root(tmp_path, monkeypatch):
    from utils.logger_config import RobustLoggerConfig

    selected_root = tmp_path / "selected-root"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(selected_root))

    config = RobustLoggerConfig(service_name="Plugin_demo", log_subdir="plugin")

    assert Path(config.get_log_directory_path()) == selected_root / "logs" / "plugin"
    assert Path(config.get_log_file_path()).parent == selected_root / "logs" / "plugin"


@pytest.mark.unit
def test_plugin_log_reader_uses_selected_storage_root(tmp_path, monkeypatch):
    from plugin.server.application.logs import LogService

    selected_root = tmp_path / "selected-root"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(selected_root))

    service = LogService()
    assert service.get_plugin_log_directory("_server") == selected_root / "logs"
    assert service.get_plugin_log_directory("demo") == selected_root / "logs" / "plugin"
    assert not selected_root.exists(), "A log-directory query must not initialize storage"
