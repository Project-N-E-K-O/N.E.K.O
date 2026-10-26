"""Public logging contracts across standalone, embedded and forked hosts."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from plugin import logging_config as plogging
from utils.logger_config import setup_logging
from utils.logging_layout import plugin_log_service_name, resolve_log_directory, sanitize_log_identifier


@pytest.fixture
def isolated_log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    monkeypatch.delenv("NEKO_PLUGIN_SERVICE_NAME", raising=False)
    monkeypatch.setattr(plogging, "_runtime_pid", None)
    monkeypatch.setattr(plogging, "_runtime_service", None)
    monkeypatch.setattr(plogging, "_runtime_result", None)
    root = logging.getLogger()
    old_root_level = root.level
    # Restore external handlers/levels; dispose only handlers installed by this test.
    original = {h for value in logging.Logger.manager.loggerDict.values()
                if isinstance(value, logging.Logger) for h in value.handlers}
    original.update(root.handlers)
    yield tmp_path / "logs"
    from utils.logger_config import _all_loggers, _detach_handler
    handlers = {h for lg in _all_loggers() for h in lg.handlers
                if h not in original and getattr(h, "_neko_owned", False)}
    for handler in handlers:
        _detach_handler(handler)
    root.setLevel(old_root_level)


def _read(directory: Path, service: str) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in directory.glob(f"N.E.K.O_{service}_*.log"))


def test_sdk_import_and_lookup_never_initialize_handlers(isolated_log_dir, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("SDK lookup must not initialize logging")
    monkeypatch.setattr("utils.logger_config.setup_logging", forbidden)
    from plugin.sdk.shared.logging import get_sdk_logger, get_plugin_logger, setup_sdk_logging
    from plugin.core.plugin_logger import PluginFileLogger
    get_sdk_logger("test")
    get_plugin_logger("demo")
    setup_sdk_logging()
    manager = PluginFileLogger("demo", isolated_log_dir / "not-a-plugin-log-root")
    manager.setup()
    assert manager.get_log_directory() == isolated_log_dir / "plugin"
    assert manager.get_log_file_path().name.startswith("N.E.K.O_Plugin_demo_")
    assert not isolated_log_dir.exists()


def test_standalone_sdk_and_standard_logging_share_service(isolated_log_dir):
    early = plogging.get_logger("early")
    plogging.setup_plugin_logging("PluginServer", own_root=True)
    early.info("early-marker value={}", 42)
    logging.getLogger("runtime-test.standalone").info("stdlib-marker")
    text = _read(isolated_log_dir, "PluginServer")
    assert text.count("early-marker value=42") == 1
    assert text.count("stdlib-marker") == 1
    assert not list(isolated_log_dir.glob("N.E.K.O_Plugin_*.log"))


@pytest.mark.parametrize("message,args,expected", [
    ("progress {} at 50%", ("ready",), "progress ready at 50%"),
    ("named {state}", ({"state": "ready"},), "named ready"),
    ("standard %(state)s", ({"state": "ready"},), "standard ready"),
])
def test_sdk_and_standard_logging_share_format_policy(isolated_log_dir, message, args, expected):
    plogging.setup_plugin_logging("PluginServer", own_root=True)
    plogging.get_logger("format-contract").info(message, *args)
    logging.getLogger("format-contract.standard").info(message, *args)
    assert _read(isolated_log_dir, "PluginServer").count(expected) == 2


def test_disabled_sdk_records_do_not_interpolate_arguments(isolated_log_dir):
    plogging.setup_plugin_logging("PluginServer", own_root=True)

    class Unrenderable:
        def __str__(self):
            pytest.fail("Disabled log arguments must stay lazy")

    adapter = plogging.get_logger("disabled-format")
    adapter.debug("unused %s", Unrenderable())
    adapter.debug("unused {}", Unrenderable())


def test_writer_reader_and_public_file_path_share_product_name(isolated_log_dir, monkeypatch):
    from plugin.core.plugin_logger import PluginFileLogger
    from plugin.server.application.logs import LogService
    from utils import logging_layout
    from utils.logger_config import RobustLoggerConfig

    monkeypatch.setattr(logging_layout, "APP_NAME", "TestProduct")
    writer = RobustLoggerConfig(app_name="TestProduct", service_name="Plugin_demo", log_subdir="plugin")
    writer.log_file.write_text("2026-10-27 12:00:00 - INFO - shared-name\n", encoding="utf-8")
    reader = LogService()
    assert [row["message"] for row in reader.get_plugin_logs(plugin_id="demo").logs] == ["shared-name"]
    assert reader.files_for_export("demo") == [writer.log_file]
    assert PluginFileLogger("demo", isolated_log_dir).get_log_file_path() == writer.log_file


def test_embedded_service_keeps_agent_handlers_and_routes_its_namespace(isolated_log_dir):
    agent, _ = setup_logging(service_name="Agent", silent=True)
    root = logging.getLogger()
    before = list(root.handlers)
    app_before = list(logging.getLogger("N.E.K.O").handlers)
    plogging.setup_plugin_logging("PluginServer", own_root=False)
    plogging.logger.info("embedded-plugin-marker")
    agent.info("agent-marker")
    logging.getLogger("runtime-test.agent-library").info("agent-library-marker")
    assert root.handlers == before
    assert logging.getLogger("N.E.K.O").handlers == app_before
    assert "embedded-plugin-marker" in _read(isolated_log_dir, "PluginServer")
    assert "embedded-plugin-marker" not in _read(isolated_log_dir, "Agent")
    assert "agent-library-marker" in _read(isolated_log_dir, "Agent")
    assert "agent-marker" not in _read(isolated_log_dir, "PluginServer")


def test_explicit_bootstrap_is_idempotent(isolated_log_dir):
    first = plogging.setup_plugin_logging("PluginServer", own_root=True)
    handlers = list(first[0].handlers)
    second = plogging.setup_plugin_logging("PluginServer", own_root=True)
    assert second is first
    assert list(second[0].handlers) == handlers
    plogging.logger.info("once-only-marker")
    assert _read(isolated_log_dir, "PluginServer").count("once-only-marker") == 1


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_parent_initialization_then_real_fork_rebinds_both_logger_apis(isolated_log_dir):
    plogging.setup_plugin_logging("PluginServer", own_root=True)
    inherited_sdk_logger = plogging.get_logger("early.sdk")
    logging.getLogger("runtime-test.before-fork").info("parent-marker")
    pid = os.fork()
    if pid == 0:
        try:
            from plugin.core.host import _setup_plugin_logger
            _setup_plugin_logger("demo", Path(__file__).resolve().parents[3])
            inherited_sdk_logger.info("child-sdk-marker")
            logging.getLogger("runtime-test.child-library").info("child-stdlib-marker")
            os._exit(0)
        except BaseException:
            import traceback
            traceback.print_exc()
            os._exit(1)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    parent = _read(isolated_log_dir, "PluginServer")
    child = _read(isolated_log_dir / "plugin", "Plugin_demo")
    assert "parent-marker" in parent
    assert "child-sdk-marker" not in parent
    assert "child-stdlib-marker" not in parent
    assert child.count("child-sdk-marker") == 1
    assert child.count("child-stdlib-marker") == 1
    assert "parent-marker" not in child
    plogging.logger.info("parent-after-fork-marker")
    assert "parent-after-fork-marker" in _read(isolated_log_dir, "PluginServer")


def test_public_file_logger_and_sdk_helpers_use_host_sink(isolated_log_dir):
    from plugin.core.plugin_logger import PluginFileLogger, enable_plugin_file_logging
    from plugin.sdk.shared.logging import get_plugin_logger
    plugin_id = "demo.with spaces"
    service = plugin_log_service_name(plugin_id)
    plogging.setup_plugin_logging(service, own_root=True)
    manager = PluginFileLogger(plugin_id, isolated_log_dir / "ignored-plugin-path")
    manager.setup().info("file-wrapper-marker")
    enable_plugin_file_logging(plugin_id, isolated_log_dir).warning("enable-wrapper-marker")
    get_plugin_logger(plugin_id).info("sdk-wrapper-marker")
    assert manager.get_log_directory() == isolated_log_dir / "plugin"
    assert manager.get_log_file_path().exists()
    text = manager.get_log_file_path().read_text(encoding="utf-8")
    assert "file-wrapper-marker" in text
    assert "enable-wrapper-marker" in text
    assert "sdk-wrapper-marker" in text
    assert not (isolated_log_dir / "ignored-plugin-path").exists()


def test_layout_is_pure_and_sanitization_retains_existing_names(isolated_log_dir):
    assert resolve_log_directory() == isolated_log_dir
    assert resolve_log_directory(log_subdir="plugin") == isolated_log_dir / "plugin"
    assert not isolated_log_dir.exists()
    assert sanitize_log_identifier("__demo .game--") == "demo__game"
    assert sanitize_log_identifier("x" * 100) == "x" * 51 + "_09ecb6ebc8bc"
    assert len(sanitize_log_identifier("???")) == 16
    with pytest.raises(ValueError):
        resolve_log_directory(log_subdir="../escape")


def test_explicit_bootstrap_can_take_root_after_namespace_bootstrap(isolated_log_dir):
    plogging.setup_plugin_logging("PluginServer", own_root=False)
    plogging.setup_plugin_logging("PluginServer", own_root=True)
    logging.getLogger("runtime-test.upgraded-ownership").info("captured-after-upgrade")
    assert "captured-after-upgrade" in _read(isolated_log_dir, "PluginServer")


def test_repeated_setup_rebinds_shared_parent_and_preserves_foreign_handlers(isolated_log_dir):
    app = logging.getLogger("N.E.K.O")
    foreign = logging.NullHandler()
    app.addHandler(foreign)
    try:
        setup_logging(service_name="Agent", silent=True)
        setup_logging(service_name="Agent", silent=True)
        logging.getLogger("N.E.K.O.runtime-test.shared").info("shared-after-repeat")
        assert foreign in app.handlers
        assert _read(isolated_log_dir, "Agent").count("shared-after-repeat") == 1
    finally:
        app.removeHandler(foreign)


def test_reader_and_writer_use_identical_directory_fallback(isolated_log_dir, monkeypatch):
    from utils import logging_layout
    from utils.logger_config import RobustLoggerConfig
    original_can_use = logging_layout._can_use
    monkeypatch.setattr(logging_layout, "_can_use", lambda path: path != isolated_log_dir and original_can_use(path))
    # Both take the next candidate, without a writer-only probe redirecting logs.
    fallback_documents = isolated_log_dir.parent / "fallback-documents"
    monkeypatch.setattr(logging_layout, "documents_directory", lambda: fallback_documents)
    monkeypatch.setattr(RobustLoggerConfig, "_get_documents_directory", lambda self: fallback_documents)
    directory = resolve_log_directory(log_subdir="plugin")
    config = RobustLoggerConfig(service_name="Plugin_demo", log_subdir="plugin")
    assert config.log_dir == directory
    assert directory == fallback_documents / "N.E.K.O" / "logs" / "plugin"


def test_macos_documents_ignores_linux_xdg_override(monkeypatch):
    from utils import logging_layout
    monkeypatch.setattr(logging_layout.sys, "platform", "darwin")
    monkeypatch.setenv("XDG_DOCUMENTS_DIR", "/linux-only-documents")
    assert logging_layout.documents_directory() == Path.home() / "Documents"
