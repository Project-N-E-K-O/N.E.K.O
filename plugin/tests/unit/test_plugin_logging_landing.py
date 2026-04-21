"""End-to-end smoke test: confirm plugin logs actually land in the unified
RobustLoggerConfig directory after the loguru rip-out.

Why this test exists
--------------------
After deleting loguru we kept seeing two failure modes during the migration:
  1. plugin/logging_config got imported BEFORE user_plugin_server.py ran
     setup_logging("PluginServer"), so it auto-initialised under
     service_name="Plugin" and split logs across N.E.K.O_PluginServer_*.log
     and N.E.K.O_Plugin_*.log.
  2. Plugin subprocesses set NEKO_PLUGIN_SERVICE_NAME but never had their
     RobustLoggerConfig handler chain attached, so messages were swallowed.

This test redirects the Documents/N.E.K.O/logs root to a tmp dir (via
NEKO_LOG_ROOT_OVERRIDE if it ever lands; for now we patch RobustLoggerConfig
to use an explicit log_dir), simulates the two startup orders, and asserts
each scenario's log file actually contains the expected lines.
"""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated_log_dir(tmp_path, monkeypatch):
    """Force RobustLoggerConfig to write to ``tmp_path / "Documents"``.

    Two parts:
      * Patch ``_get_documents_directory`` so the first writable-fallback step
        lands in ``tmp_path``.
      * Reset the ``_root_initialised`` flag in plugin.logging_config and
        wipe handlers off all N.E.K.O.* loggers so each test starts clean.
    """
    documents = tmp_path / "Documents"
    documents.mkdir()

    # Each test gets a fresh import of plugin.logging_config so module-level
    # singletons (the brace-compat patch, _root_initialised) don't leak.
    for mod in [m for m in list(sys.modules) if m.startswith("plugin.logging_config")]:
        del sys.modules[mod]

    from utils import logger_config as ulc

    monkeypatch.setattr(
        ulc.RobustLoggerConfig,
        "_get_documents_directory",
        lambda self: documents,
    )

    # Reset every N.E.K.O.* logger's state — pytest workers persist process
    # state across tests, so leftover handlers from a prior test would
    # double-write or shadow our assertions.
    for name in list(logging.Logger.manager.loggerDict.keys()):
        if name == "N.E.K.O" or name.startswith("N.E.K.O."):
            lg = logging.getLogger(name)
            for h in list(lg.handlers):
                lg.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    # Best-effort cleanup. A handler may already be closed
                    # (e.g. RotatingFileHandler swapped during a prior test
                    # rotation) — swallow so fixture teardown doesn't mask
                    # the actual test failure.
                    pass

    yield documents / "N.E.K.O" / "logs"


def _read_logs(log_dir: Path, service_name: str) -> str:
    """Read all log files for a given service into one string."""
    files = sorted(log_dir.glob(f"N.E.K.O_{service_name}_*.log"))
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def test_plugin_server_main_process_unifies_logs_under_pluginserver(
    isolated_log_dir, monkeypatch
):
    """Simulate user_plugin_server.py boot order:
    1. set NEKO_PLUGIN_SERVICE_NAME=PluginServer
    2. setup_logging(service_name="PluginServer")
    3. import plugin.logging_config (and thus get_logger)

    Expectation: ALL plugin module logs (message_plane, runs, host…) land in
    N.E.K.O_PluginServer_*.log — not in a parallel N.E.K.O_Plugin_*.log.
    """
    monkeypatch.setenv("NEKO_PLUGIN_SERVICE_NAME", "PluginServer")

    from utils.logger_config import setup_logging as bootstrap_setup_logging

    bootstrap_setup_logging(service_name="PluginServer", log_level=logging.INFO, silent=True)

    plogging = importlib.import_module("plugin.logging_config")

    # Mirror real plugin module usage: communication / host / runs all do
    # `from plugin.logging_config import logger` or `get_logger(...)`.
    plogging.logger.info("from-plugin-default-logger")
    plogging.get_logger("message_plane.ingest").info("from-message-plane")
    plogging.get_logger("core.host").info("from-host")

    for h in logging.getLogger("N.E.K.O.PluginServer").handlers:
        h.flush()

    server_text = _read_logs(isolated_log_dir, "PluginServer")
    assert "from-plugin-default-logger" in server_text
    assert "from-message-plane" in server_text
    assert "from-host" in server_text

    # Crucial: no split file. If the short-circuit in _ensure_root_logger
    # broke, plugin.logging_config would have called setup_logging("Plugin")
    # and a stray N.E.K.O_Plugin_*.log would exist.
    assert not list(isolated_log_dir.glob("N.E.K.O_Plugin_*.log"))


def test_plugin_subprocess_logs_to_its_own_service_file(isolated_log_dir, monkeypatch):
    """Simulate _setup_plugin_logger from plugin/core/host.py:
    1. set NEKO_PLUGIN_SERVICE_NAME=Plugin_<safe_pid>
    2. utils.logger_config.setup_logging(service_name="Plugin_<safe_pid>")
    3. plugin.logging_config.get_logger(f"plugin.{safe_pid}").bind(plugin_id=...)

    Expectation: logs land in N.E.K.O_Plugin_demo_*.log and nowhere else.
    """
    monkeypatch.setenv("NEKO_PLUGIN_SERVICE_NAME", "Plugin_demo")

    from utils.logger_config import setup_logging as bootstrap_setup_logging

    bootstrap_setup_logging(service_name="Plugin_demo", log_level=logging.INFO, silent=True)

    plogging = importlib.import_module("plugin.logging_config")

    bound = plogging.get_logger("plugin.demo").bind(plugin_id="demo")
    bound.info("hello-from-plugin-demo")
    bound.warning("warn-from-plugin-demo")

    for h in logging.getLogger("N.E.K.O.Plugin_demo").handlers:
        h.flush()

    text = _read_logs(isolated_log_dir, "Plugin_demo")
    assert "hello-from-plugin-demo" in text
    assert "warn-from-plugin-demo" in text

    # error log file gets opened on setup; nothing should be written there
    # for INFO/WARNING traffic.
    error_file = isolated_log_dir / "N.E.K.O_Plugin_demo_error.log"
    if error_file.exists():
        assert "hello-from-plugin-demo" not in error_file.read_text(encoding="utf-8")


def test_module_level_logger_follows_late_service_name_change(
    isolated_log_dir, monkeypatch
):
    """Subprocess race regression test (PR #912 codex finding):

    In a real spawned plugin subprocess the boot order is:
      1. Python imports some shared plugin module (e.g. plugin.message_plane)
      2. that module's top says ``from plugin.logging_config import logger``
         → triggers ``get_logger("plugin")`` while NEKO_PLUGIN_SERVICE_NAME
         is still empty / inherited from the parent
      3. THEN host's ``_setup_plugin_logger`` sets
         NEKO_PLUGIN_SERVICE_NAME=Plugin_<id> + setup_logging(...)
      4. shared module later does ``logger.info(...)``

    If PluginLoggerAdapter froze its stdlib logger at step 2, step 4 would
    write to N.E.K.O_Plugin_*.log (or wherever step 2's env pointed), NOT
    N.E.K.O_Plugin_<id>_*.log. The lazy ``_resolve_logger`` fix is what
    makes this work.
    """
    # Step 2: import + grab a module-level logger BEFORE service_name is set.
    monkeypatch.delenv("NEKO_PLUGIN_SERVICE_NAME", raising=False)
    plogging = importlib.import_module("plugin.logging_config")
    early_logger = plogging.get_logger("shared.module")
    early_default = plogging.logger  # the module-level singleton
    early_bound = plogging.logger.bind(plugin_id="lazy_test")  # propagated through bind

    # Step 3: host belatedly sets env + boots its logger sink.
    monkeypatch.setenv("NEKO_PLUGIN_SERVICE_NAME", "Plugin_lazy")
    from utils.logger_config import setup_logging as bootstrap_setup_logging
    bootstrap_setup_logging(service_name="Plugin_lazy", log_level=logging.INFO, silent=True)

    # Step 4: emit through the loggers grabbed BEFORE step 3.
    early_logger.info("late-routed-shared")
    early_default.info("late-routed-default")
    early_bound.warning("late-routed-bound")

    for h in logging.getLogger("N.E.K.O.Plugin_lazy").handlers:
        h.flush()

    text = _read_logs(isolated_log_dir, "Plugin_lazy")
    assert "late-routed-shared" in text, (
        "PluginLoggerAdapter froze its stdlib logger at construction — "
        "subprocess-style late env change no longer routes to the right file."
    )
    assert "late-routed-default" in text
    assert "late-routed-bound" in text


def test_logger_adapter_braces_route_to_pluginserver_file(isolated_log_dir, monkeypatch):
    """Most plugin code still uses loguru-style braces:
        logger.info("foo {}", x)
    The PluginLoggerAdapter must route those into the PluginServer file
    after .format(*args) substitution — not lose them, not raise.
    """
    monkeypatch.setenv("NEKO_PLUGIN_SERVICE_NAME", "PluginServer")
    from utils.logger_config import setup_logging as bootstrap_setup_logging

    bootstrap_setup_logging(service_name="PluginServer", log_level=logging.INFO, silent=True)

    plogging = importlib.import_module("plugin.logging_config")
    plogging.logger.info("braces value={}", 42)
    plogging.logger.warning("two={} three={}", "a", "b")

    for h in logging.getLogger("N.E.K.O.PluginServer").handlers:
        h.flush()

    text = _read_logs(isolated_log_dir, "PluginServer")
    assert "braces value=42" in text
    assert "two=a three=b" in text
