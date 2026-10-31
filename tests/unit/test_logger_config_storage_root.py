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
    import logging

    from plugin import logging_config
    from plugin.server.application.logs import LogService

    selected_root = tmp_path / "selected-root"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(selected_root))
    monkeypatch.setattr(logging_config, "_runtime_pid", None)
    monkeypatch.setattr(logging_config, "_runtime_result", None)
    monkeypatch.setenv("NEKO_PLUGIN_SERVICE_NAME", "PluginServer")
    monkeypatch.setattr(logging.getLogger("N.E.K.O.PluginServer"), "handlers", [])

    service = LogService()
    assert service.get_plugin_log_directory("_server") == selected_root / "logs"
    assert service.get_plugin_log_directory("demo") == selected_root / "logs" / "plugin"
    assert not selected_root.exists(), "A log-directory query must not initialize storage"


@pytest.mark.unit
@pytest.mark.parametrize("subdir,expected", [(None, ""), (" /one/two/ ", "one/two"), (7, "7"), ("///", "")])
def test_host_log_subdirectory_keeps_legacy_normalization(tmp_path, monkeypatch, subdir, expected):
    from utils.logger_config import RobustLoggerConfig
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    config = RobustLoggerConfig(service_name="Main", log_subdir=subdir)
    assert config.log_dir == tmp_path / "storage" / "logs" / expected


@pytest.mark.unit
@pytest.mark.parametrize("accepted", range(6))
def test_host_writer_probes_shared_candidates_in_upstream_order(tmp_path, monkeypatch, accepted):
    from utils import logger_config as backend
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "selected"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(backend.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(backend, "_get_writable_application_directory", lambda: tmp_path / "application")
    monkeypatch.setattr(backend.RobustLoggerConfig, "_get_documents_directory", lambda self: tmp_path / "documents")
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    expected = [tmp_path / "selected/logs", tmp_path / "documents/N.E.K.O/logs",
                tmp_path / "application/logs", tmp_path / "data/N.E.K.O/logs",
                tmp_path / "home/.N.E.K.O/logs", tmp_path / "temp/N.E.K.O/logs"]
    probed = []
    def writable(self, path):
        probed.append(path)
        return path == expected[accepted]
    monkeypatch.setattr(backend.RobustLoggerConfig, "_test_directory_writable", writable)
    monkeypatch.setattr(backend.RobustLoggerConfig, "_ensure_log_directory", lambda self: None)
    monkeypatch.setattr(backend.RobustLoggerConfig, "_cleanup_old_logs", lambda self: None)
    config = backend.RobustLoggerConfig(service_name="Main")
    assert config.log_dir == expected[accepted]
    assert probed == expected[:accepted + 1]


@pytest.mark.unit
@pytest.mark.parametrize("xdg", ["", "configured"])
def test_system_log_directory_preserves_empty_xdg_and_lazy_home(tmp_path, monkeypatch, xdg):
    from utils import logging_layout
    monkeypatch.delenv("NEKO_STORAGE_SELECTED_ROOT", raising=False)
    monkeypatch.setattr(logging_layout.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / xdg) if xdg else "")
    calls = []
    def home(cls):
        calls.append(True)
        return tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(home))
    candidates = logging_layout.log_directory_candidates(
        documents=tmp_path / "documents", application=lambda: tmp_path / "app")
    next(candidates)  # Documents
    next(candidates)  # Application
    system = next(candidates)
    expected = (tmp_path / "configured" if xdg else tmp_path / "home/.local/share") / "N.E.K.O/logs"
    assert system == expected
    assert bool(calls) == (not bool(xdg))
