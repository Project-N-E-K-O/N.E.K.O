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
    # double-write or shadow our assertions. Track the handler set so we
    # can prune the root logger below WITHOUT touching unrelated handlers
    # (e.g. pytest's own log-capture handler — see CodeRabbit feedback on
    # PR #912).
    bridged_handlers: set[logging.Handler] = set()
    for name in list(logging.Logger.manager.loggerDict.keys()):
        if name == "N.E.K.O" or name.startswith("N.E.K.O."):
            lg = logging.getLogger(name)
            bridged_handlers.update(lg.handlers)
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

    # Reset root logger ONLY for plugin-bridged handlers:
    # ``_install_root_bridge`` re-attaches each ``N.E.K.O.<service>`` file
    # handler onto root so plain stdlib third-party loggers (uvicorn /
    # aiohttp) reach the same file — those exact handler objects are
    # already captured in ``bridged_handlers`` above. Removing them by
    # identity (``h not in bridged_handlers → skip``) keeps unrelated
    # root handlers intact (pytest LogCaptureHandler, asyncio's debug
    # handler, anything a sibling test installed). Stale-handler reasons
    # for clearing them anyway:
    #   * stale handlers point at a previous test's tmp_path (now deleted),
    #     so the next test's INFO writes go to a no-longer-existing file;
    #   * the ``_neko_plugin_root_bridged`` marker would short-circuit the
    #     new bridge install and the "third-party logger reaches our file"
    #     test would silently regress.
    root = logging.getLogger()
    for h in list(root.handlers):
        if h not in bridged_handlers:
            continue
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            # Best-effort cleanup. Same rationale as the per-service loop
            # above: a handler may already be closed (RotatingFileHandler
            # rotation, or the per-service loop already drained shared
            # references) — swallow so fixture teardown doesn't mask a
            # real test failure.
            pass
    if hasattr(root, "_neko_plugin_root_bridged"):
        delattr(root, "_neko_plugin_root_bridged")

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

    # PluginServer 是宿主进程，绝对不能被收纳到 logs/plugin/ —— 否则 Electron
    # 那侧按顶层路径读 PluginServer 日志时会一片空白。
    plugin_subdir = isolated_log_dir / "plugin"
    if plugin_subdir.exists():
        assert not list(plugin_subdir.glob("N.E.K.O_PluginServer_*.log")), (
            "PluginServer 宿主日志被误路由到 logs/plugin/ 子目录了。"
        )


def test_plugin_subprocess_logs_to_its_own_service_file(isolated_log_dir, monkeypatch):
    """Simulate _setup_plugin_logger from plugin/core/host.py:
    1. set NEKO_PLUGIN_SERVICE_NAME=Plugin_<safe_pid>
    2. utils.logger_config.setup_logging(service_name="Plugin_<safe_pid>",
       log_subdir="plugin")
    3. plugin.logging_config.get_logger(f"plugin.{safe_pid}").bind(plugin_id=...)

    Expectation: logs land in ``logs/plugin/N.E.K.O_Plugin_demo_*.log`` — NOT
    at the ``logs/`` top level where PluginServer / Main / Memory / Agent live.
    """
    monkeypatch.setenv("NEKO_PLUGIN_SERVICE_NAME", "Plugin_demo")

    from utils.logger_config import setup_logging as bootstrap_setup_logging

    bootstrap_setup_logging(
        service_name="Plugin_demo",
        log_level=logging.INFO,
        silent=True,
        log_subdir="plugin",
    )

    plogging = importlib.import_module("plugin.logging_config")

    bound = plogging.get_logger("plugin.demo").bind(plugin_id="demo")
    bound.info("hello-from-plugin-demo")
    bound.warning("warn-from-plugin-demo")

    for h in logging.getLogger("N.E.K.O.Plugin_demo").handlers:
        h.flush()

    plugin_subdir = isolated_log_dir / "plugin"
    text = _read_logs(plugin_subdir, "Plugin_demo")
    assert "hello-from-plugin-demo" in text
    assert "warn-from-plugin-demo" in text

    # error log file gets opened on setup; nothing should be written there
    # for INFO/WARNING traffic.
    error_file = plugin_subdir / "N.E.K.O_Plugin_demo_error.log"
    if error_file.exists():
        assert "hello-from-plugin-demo" not in error_file.read_text(encoding="utf-8")

    # Plugin_* 子进程日志绝对不能再污染顶层 logs/。顶层是留给 PluginServer /
    # Main / Memory / Agent 宿主进程的。如果未来又有人忘了传 log_subdir，
    # 这个反向断言会在 CI 直接报错。
    assert not list(isolated_log_dir.glob("N.E.K.O_Plugin_demo_*.log")), (
        "Plugin_demo 子进程日志落到 logs/ 顶层了；应收纳到 logs/plugin/。"
    )


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
    bootstrap_setup_logging(
        service_name="Plugin_lazy",
        log_level=logging.INFO,
        silent=True,
        log_subdir="plugin",
    )

    # Step 4: emit through the loggers grabbed BEFORE step 3.
    early_logger.info("late-routed-shared")
    early_default.info("late-routed-default")
    early_bound.warning("late-routed-bound")

    for h in logging.getLogger("N.E.K.O.Plugin_lazy").handlers:
        h.flush()

    text = _read_logs(isolated_log_dir / "plugin", "Plugin_lazy")
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


def test_third_party_logger_propagates_to_plugin_log_file(
    isolated_log_dir, monkeypatch
):
    """Root-bridge regression test (PR #912 coderabbit finding):

    uvicorn / aiohttp / urllib3 etc. don't know about ``N.E.K.O.*``
    namespacing — they call plain ``logging.getLogger("uvicorn.error")``
    and rely on the propagation chain to reach root. ``RobustLoggerConfig``
    by default only attaches handlers to ``N.E.K.O`` and
    ``N.E.K.O.<service>``, not root. With the loguru rip-out (which used
    to install a root forwarder via ``intercept_standard_logging``),
    those messages were silently dropped.

    ``_install_root_bridge`` (called from ``_ensure_root_logger``) re-adds
    the file handlers onto root so plain stdlib third-party loggers land
    in the same plugin log file.
    """
    monkeypatch.setenv("NEKO_PLUGIN_SERVICE_NAME", "PluginServer")
    from utils.logger_config import setup_logging as bootstrap_setup_logging
    bootstrap_setup_logging(service_name="PluginServer", log_level=logging.INFO, silent=True)

    # Importing plugin.logging_config triggers _ensure_root_logger →
    # _install_root_bridge.
    importlib.import_module("plugin.logging_config")

    # Simulate a third-party library: NOT under the N.E.K.O.* namespace.
    # ``logging.getLogger(name)`` returns a process-global singleton, so
    # mutating ``handlers`` / ``propagate`` here would leak into any test
    # that runs later in the same pytest worker (CodeRabbit feedback on
    # PR #912). Snapshot the original state and restore in ``finally``.
    third_party = logging.getLogger("uvicorn.error")
    aiohttp_like = logging.getLogger("aiohttp.access")
    tp_handlers, tp_propagate = list(third_party.handlers), third_party.propagate
    ah_handlers, ah_propagate = list(aiohttp_like.handlers), aiohttp_like.propagate

    try:
        third_party.handlers.clear()  # uvicorn defaults sometimes attach a handler
        third_party.propagate = True
        third_party.info("uvicorn-bridged-into-plugin-log")

        aiohttp_like.handlers.clear()
        aiohttp_like.propagate = True
        aiohttp_like.warning("aiohttp-bridged-into-plugin-log")

        for h in logging.getLogger().handlers:
            h.flush()

        text = _read_logs(isolated_log_dir, "PluginServer")
        assert "uvicorn-bridged-into-plugin-log" in text, (
            "Root bridge missing — third-party stdlib logger output is being "
            "silently dropped instead of landing in the plugin log file."
        )
        assert "aiohttp-bridged-into-plugin-log" in text
    finally:
        third_party.handlers[:] = tp_handlers
        third_party.propagate = tp_propagate
        aiohttp_like.handlers[:] = ah_handlers
        aiohttp_like.propagate = ah_propagate


def test_bootstrap_failure_does_not_lock_root_initialised(
    isolated_log_dir, monkeypatch
):
    """Retry regression test (PR #912 coderabbit finding):

    If the very first ``_ensure_root_logger()`` call hits a transient
    failure — utils not on sys.path during a unit test, or
    setup_logging raises — the OLD code set
    ``_root_initialised = True`` anyway and short-circuited every
    subsequent call, permanently disabling the file-handler hookup.

    The fix: only flip the flag after a successful bootstrap.
    """
    monkeypatch.setenv("NEKO_PLUGIN_SERVICE_NAME", "PluginServer")

    # Capture the real setup_logging BEFORE we patch it so the retry leg
    # below has something to restore.
    from utils.logger_config import setup_logging as _real_setup_logging

    def _exploding_setup(*_a, **_kw):
        raise RuntimeError("simulated bootstrap failure")

    # Patch BEFORE re-importing plugin.logging_config — the module top-level
    # ``logger = get_logger("plugin")`` triggers _ensure_root_logger on
    # import, so the patch must already be in place. Use string-based
    # target so we keep a single ``from utils.logger_config import ...``
    # style in this file (avoids the CodeQL "import + import from" smell).
    monkeypatch.setattr("utils.logger_config.setup_logging", _exploding_setup)

    plogging = importlib.import_module("plugin.logging_config")

    # _ensure_root_logger ran via the module-level get_logger("plugin") and
    # should have caught the RuntimeError without flipping the flag.
    assert not plogging._root_initialised, (
        "_root_initialised got locked to True despite bootstrap failure — "
        "subsequent retries will be silently short-circuited and the "
        "plugin log file will never be hooked up."
    )

    # Restore working setup_logging, retry: must succeed and set the flag.
    monkeypatch.setattr("utils.logger_config.setup_logging", _real_setup_logging)
    plogging._ensure_root_logger()
    assert plogging._root_initialised
    plogging.get_logger("retry.test").info("post-retry-success")
    for h in logging.getLogger("N.E.K.O.PluginServer").handlers:
        h.flush()
    text = _read_logs(isolated_log_dir, "PluginServer")
    assert "post-retry-success" in text
