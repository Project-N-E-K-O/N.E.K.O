"""Public logging contracts across standalone, embedded and forked hosts."""
from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

import pytest

from plugin import logging_config as plogging
from utils.logger_config import setup_logging
from plugin.logging_layout import plugin_log_service_name, sanitize_log_identifier
from utils.logging_layout import resolve_log_directory


@pytest.fixture
def isolated_log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    monkeypatch.setattr("utils.logger_config._get_development_log_directory", lambda: tmp_path / "application" / "logs")
    monkeypatch.setattr("utils.logger_config._get_writable_application_directory", lambda: tmp_path / "application")
    monkeypatch.delenv("NEKO_PLUGIN_SERVICE_NAME", raising=False)
    monkeypatch.setattr(plogging, "_runtime_pid", None)
    monkeypatch.setattr(plogging, "_runtime_service", None)
    monkeypatch.setattr(plogging, "_runtime_result", None)
    from plugin._logging_runtime import all_loggers as _all_loggers
    saved = {lg: (list(lg.handlers), lg.level, lg.propagate) for lg in _all_loggers()}
    original = {h for handlers, _, _ in saved.values() for h in handlers}
    # Each case starts without a previous test's canonical project writer.
    for lg in saved:
        lg.handlers = [h for h in lg.handlers if not getattr(h, "_neko_owned", False)]
    yield tmp_path / "logs"
    created = {h for lg in _all_loggers() for h in lg.handlers
               if h not in original and getattr(h, "_neko_owned", False)}
    for lg in _all_loggers():
        lg.handlers = [h for h in lg.handlers if h not in created]
    for handler in created:
        handler.close()
    for lg, (handlers, level, propagate) in saved.items():
        lg.handlers = handlers
        lg.setLevel(level)
        lg.propagate = propagate


def _read(directory: Path, service: str) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in directory.glob(f"N.E.K.O_{service}_*.log"))


def test_sdk_import_and_lookup_never_initialize_handlers(isolated_log_dir, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("SDK lookup must not initialize logging")
    monkeypatch.setattr("utils.logger_config.setup_logging", forbidden)
    from plugin.sdk.shared.logging import get_sdk_logger, get_plugin_logger
    from plugin.core.plugin_logger import PluginFileLogger
    get_sdk_logger("test")
    get_plugin_logger("demo")
    manager = PluginFileLogger("demo", isolated_log_dir / "not-a-plugin-log-root")
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
def test_sdk_preserves_legacy_format_policy(isolated_log_dir, message, args, expected):
    plogging.setup_plugin_logging("PluginServer", own_root=True)
    plogging.get_logger("format-contract").info(message, *args)
    assert _read(isolated_log_dir, "PluginServer").count(expected) == 1


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
    # Host root policy is outside plugin ownership; embedding must preserve it.
    assert "agent-library-marker" not in _read(isolated_log_dir, "PluginServer")
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


_POSIX_FORK = pytest.mark.skipif(not hasattr(os, "fork") or not hasattr(os, "mkfifo"),
                                 reason="requires POSIX fork")


@contextlib.contextmanager
def _parked_fifo_stream(path: Path):
    """Yield (stream, drain) where a parent thread holds the stream's buffer lock.

    A FIFO nobody drains blocks the raw write while BufferedWriter's internal
    lock is held, which is the state a fork may copy into a child.
    """
    import fcntl
    import select
    import threading

    os.mkfifo(path)
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    readable = select.poll()  # Unlike select(), poll() has no descriptor-number limit.
    readable.register(reader, select.POLLIN)

    def drain(timeout_ms=0):
        """Read one chunk if available, keeping it in drain.received; return its size."""
        if not readable.poll(timeout_ms):
            return 0
        chunk = os.read(reader, 1 << 16)
        drain.received += chunk
        return len(chunk)

    drain.received = bytearray()

    writer = stream = None
    try:
        stream = open(path, "w", encoding="utf-8")
        # Exceed the pipe capacity (1 MiB on 64 KiB-page kernels) so the write stays parked.
        payload = 1 << 20
        if hasattr(fcntl, "F_GETPIPE_SZ"):
            payload = max(payload, 2 * fcntl.fcntl(reader, fcntl.F_GETPIPE_SZ))
        # Bind the FIFO now so a late-starting writer can never hit a restored stream.
        writer = threading.Thread(target=stream.write, args=("x" * payload,), daemon=True)
        writer.start()
        assert readable.poll(10_000), "parent writer never reached the raw write"
        yield stream, drain
    finally:
        try:
            while writer is not None and writer.is_alive():
                drain(100)
        finally:
            # Without a reader, any write still pending fails with EPIPE instead of blocking.
            os.close(reader)
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()


def _run_forked(child_main, drain, hang_message):
    """Run child_main() in a fork; its return value is the exit code. Never hangs."""
    import signal
    import time

    child = os.fork()
    if child == 0:
        try:
            os._exit(child_main())
        except BaseException:  # Nothing may unwind into the parent's pytest session.
            import traceback
            traceback.print_exc()
            os._exit(1)
    try:
        deadline = time.monotonic() + 20
        while True:
            done, status = os.waitpid(child, os.WNOHANG)
            if done:
                child = None
                return os.waitstatus_to_exitcode(status)
            if time.monotonic() > deadline:
                pytest.fail(hang_message)
            drain(50)  # Child output may share the FIFO; never let it fill.
    finally:
        if child is not None:  # Timed out or interrupted: never leave the child behind.
            with contextlib.suppress(ChildProcessError):  # Already reaped.
                if os.waitpid(child, os.WNOHANG)[0] == 0:
                    os.kill(child, signal.SIGKILL)
                    os.waitpid(child, 0)


@_POSIX_FORK
def test_fork_while_parent_thread_writes_does_not_flush_inherited_file_stream(isolated_log_dir, tmp_path):
    """A child must not flush a file buffer whose lock a parent thread held at fork."""
    from plugin.core.host import _setup_plugin_logger

    plogging.setup_plugin_logging("PluginServer", own_root=True)
    logging.getLogger("runtime-test.before-fork").info("parent-marker")
    handler = plogging._active_file_handler("PluginServer")
    assert handler is not None and handler.stream is not None
    original_stream, original_max_bytes = handler.stream, handler.maxBytes

    def child_main():
        inherited = handler.stream
        _setup_plugin_logger("demo", Path(__file__).resolve().parents[3])
        logging.getLogger("runtime-test.child").info("child-marker")
        # Reading .closed takes no buffer lock; the child's descriptor is released.
        return 0 if handler.stream is None and inherited.closed else 2

    with _parked_fifo_stream(tmp_path / "parent-sink.fifo") as (fifo_stream, drain):
        try:
            handler.maxBytes = 0  # A FIFO cannot report a rollover position.
            handler.stream = fifo_stream
            code = _run_forked(child_main, drain, "child blocked flushing a file stream inherited over fork")
        finally:
            handler.stream, handler.maxBytes = original_stream, original_max_bytes
    assert code == 0
    assert _read(isolated_log_dir / "plugin", "Plugin_demo").count("child-marker") == 1
    plogging.logger.info("parent-after-fork-marker")
    assert "parent-after-fork-marker" in _read(isolated_log_dir, "PluginServer")


@_POSIX_FORK
def test_fork_while_parent_thread_writes_stdout_gives_child_fresh_std_streams(
        isolated_log_dir, tmp_path):
    """stdout shares the inherited-buffer hazard, and the child keeps writing to it."""
    import sys
    from plugin.core.host import _setup_plugin_logger

    with _parked_fifo_stream(tmp_path / "stdout.fifo") as (fifo_stream, drain):
        saved_stdout, sys.stdout = sys.stdout, fifo_stream
        console = logging.StreamHandler(fifo_stream)
        library = logging.getLogger("runtime-test.inherited-console")
        library.addHandler(console)
        try:
            def child_main():
                print("child-print-marker", flush=True)
                library.warning("child-inherited-console-marker")  # Retargeted handler.
                _setup_plugin_logger("demo", Path(__file__).resolve().parents[3])
                logging.getLogger("runtime-test.child").info("child-console-marker")
                return 0 if sys.stdout is not fifo_stream and console.stream is sys.stdout else 2

            code = _run_forked(child_main, drain, "child blocked writing to stdout inherited over fork")
            while drain():
                pass  # Collect child output still queued behind the parent's payload.
        finally:
            library.removeHandler(console)
            sys.stdout = saved_stdout
    assert code == 0
    received = bytes(drain.received)
    for marker in (b"child-print-marker", b"child-inherited-console-marker", b"child-console-marker"):
        assert received.count(marker) == 1, marker  # Console output reached the real descriptor.
    assert _read(isolated_log_dir / "plugin", "Plugin_demo").count("child-console-marker") == 1


@_POSIX_FORK
def test_fork_hook_keeps_unbuffered_std_streams():
    """python -u streams have no buffer lock; reopening them would add buffering."""
    import io
    import sys
    from plugin._logging_runtime import reopen_inherited_std_streams

    def child_main():
        unbuffered = io.TextIOWrapper(io.FileIO(os.dup(1), "w"), write_through=True)
        sys.stdout = unbuffered
        reopen_inherited_std_streams()
        return 0 if sys.stdout is unbuffered else 2

    assert _run_forked(child_main, lambda timeout_ms=0: None, "fork hook blocked") == 0


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


def test_plugin_bootstrap_preserves_foreign_handlers(isolated_log_dir):
    app = logging.getLogger("N.E.K.O")
    foreign = logging.NullHandler()
    app.addHandler(foreign)
    try:
        plogging.setup_plugin_logging("Plugin_demo", own_root=True)
        plogging.setup_plugin_logging("Plugin_demo", own_root=True)
        logging.getLogger("N.E.K.O.runtime-test.shared").info("shared-after-repeat")
        assert foreign in app.handlers
        assert _read(isolated_log_dir / "plugin", "Plugin_demo").count("shared-after-repeat") == 1
    finally:
        app.removeHandler(foreign)


def test_reader_and_writer_use_identical_directory_fallback(isolated_log_dir, monkeypatch):
    from plugin.core.plugin_logger import PluginFileLogger
    from plugin.server.application.logs import LogService
    from utils.logger_config import RobustLoggerConfig
    original_probe = RobustLoggerConfig._test_directory_writable
    monkeypatch.setattr(RobustLoggerConfig, "_test_directory_writable",
                        lambda self, path: path != isolated_log_dir and original_probe(self, path))
    # A real writer probe can reject a path that a read-only access estimate
    # considers writable (for example a protected Documents directory).
    fallback_documents = isolated_log_dir.parent / "fallback-documents"
    monkeypatch.setattr(RobustLoggerConfig, "_get_documents_directory", lambda self: fallback_documents)
    _, config = plogging.setup_plugin_logging("PluginServer", own_root=False)
    plogging.get_logger("fallback-contract").info("fallback-marker")
    service = LogService()
    assert service.get_plugin_log_directory("_server") == config.log_dir
    assert config.log_dir == fallback_documents / "N.E.K.O" / "logs"
    assert "fallback-marker" in [row["message"] for row in service.get_plugin_logs(plugin_id="_server").logs]
    assert PluginFileLogger("demo", isolated_log_dir).get_log_directory() == config.log_dir / "plugin"


def test_macos_documents_ignores_linux_xdg_override(monkeypatch):
    from utils import logging_layout
    monkeypatch.setattr(logging_layout.sys, "platform", "darwin")
    monkeypatch.setenv("XDG_DOCUMENTS_DIR", "/linux-only-documents")
    assert logging_layout.documents_directory() == Path.home() / "Documents"


@pytest.mark.parametrize("template", ["external={}", "external=%s"])
def test_plugin_controls_are_escaped_after_interpolation(isolated_log_dir, capsys, template):
    plogging.setup_plugin_logging("PluginServer", own_root=True)
    adapter = plogging.get_logger("external-plugin").bind(plugin_id="demo")
    adapter.error(template, "response\x07\x1b[2J")
    console = capsys.readouterr().out
    output = _read(isolated_log_dir, "PluginServer")
    for text in (console, output):
        assert r"external=response\x07\x1b[2J" in text
        assert "\x07" not in text
        assert "\x1b" not in text


@pytest.mark.parametrize("template", ["external={}", "external=%s"])
def test_degraded_plugin_logging_escapes_controls(isolated_log_dir, capsys, template):
    isolated_log_dir.mkdir(parents=True, exist_ok=True)
    (isolated_log_dir / "plugin").write_text("blocked")
    _, config = plogging.setup_plugin_logging("Plugin_demo", own_root=True)
    assert config is None
    plogging.get_logger("external-plugin").error(template, "response\x07\x1b[2J")
    output = capsys.readouterr().err
    assert r"external=response\x07\x1b[2J" in output
    assert "\x07" not in output
    assert "\x1b" not in output
