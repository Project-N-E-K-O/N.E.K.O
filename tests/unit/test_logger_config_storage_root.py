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


@pytest.mark.unit
def test_concurrent_writability_probes_on_one_directory_all_succeed(tmp_path):
    # Plugin processes start concurrently against one log directory. With a
    # shared probe file name, one probe's unlink raced another's write and the
    # loser fell back to Documents/N.E.K.O/logs despite a selected root
    # (measured: 8 processes x 20 probes, 12-15 false per process).
    import threading

    from utils.logger_config import RobustLoggerConfig

    directory = tmp_path / "logs"
    workers = 8
    barrier = threading.Barrier(workers)
    results: list[bool] = []
    lock = threading.Lock()

    def probe():
        barrier.wait()
        outcomes = [RobustLoggerConfig._test_directory_writable(None, directory) for _ in range(30)]
        with lock:
            results.extend(outcomes)

    threads = [threading.Thread(target=probe) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(results) == workers * 30
    assert all(results), f"{results.count(False)} of {len(results)} probes reported unwritable"
    assert list(directory.iterdir()) == []


_PROBE_PROCESS = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from utils.logger_config import RobustLoggerConfig
directory = Path(sys.argv[2])
print("ready", flush=True)
sys.stdin.readline()
print(sum(not RobustLoggerConfig._test_directory_writable(None, directory) for _ in range(20)), flush=True)
"""


@pytest.mark.unit
def test_concurrent_writability_probes_from_separate_processes_all_succeed(tmp_path, tmp_path_factory):
    # The case the fix is for: plugin processes, not threads, share a log
    # directory. Thread ids can coincide across processes, so this pins the
    # per-process part of the probe name as well.
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor

    from tests.process_isolation import child_process_env

    repo_root = Path(__file__).resolve().parents[2]
    directory = tmp_path / "logs"
    env = child_process_env(tmp_path_factory.mktemp("child-runtime-root"))
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _PROBE_PROCESS, str(repo_root), str(directory)],
            cwd=str(repo_root), env=env, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for _ in range(8)
    ]
    # Handshake rather than a fixed delay: every child finishes its cold
    # imports and reports ready before any of them is told to probe, so the
    # probes overlap however slow the imports are. The ready lines are read on
    # worker threads so a hung child fails the test instead of blocking it;
    # killing the children in finally unblocks any read still pending.
    pool = ThreadPoolExecutor(max_workers=len(processes))
    try:
        ready = [pool.submit(process.stdout.readline) for process in processes]
        for index, future in enumerate(ready):
            line = future.result(timeout=120).strip()
            assert line == "ready", f"child {index} did not report ready: {line!r}"
        for process in processes:
            process.stdin.write("go" + chr(10))
            process.stdin.flush()
        outputs = [process.communicate(timeout=120) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        pool.shutdown(wait=False)

    for process, (stdout, stderr) in zip(processes, outputs):
        assert process.returncode == 0, stderr
    unwritable = [int(stdout.strip().splitlines()[-1]) for stdout, _ in outputs]
    assert unwritable == [0] * 8, f"probes reported unwritable per process: {unwritable}"
    assert list(directory.iterdir()) == []


class _MissingSelfParentPath:
    """Fake Path-like object simulating a non-existent root where parent == self."""

    def __init__(self, parts: tuple[str, ...], index: int | None = None):
        self._parts = parts
        self._index = len(parts) - 1 if index is None else index
        self.exists_calls = 0

    def exists(self) -> bool:
        self.exists_calls += 1
        return False

    @property
    def parent(self) -> "_MissingSelfParentPath":
        if self._index <= 0:
            return self
        return _MissingSelfParentPath(self._parts, self._index - 1)

    def mkdir(self, exist_ok: bool = False) -> None:
        raise AssertionError("mkdir must not be called for missing self-parent root")

    def __str__(self) -> str:
        return "/".join(self._parts[: self._index + 1])


@pytest.mark.unit
def test_test_directory_writable_terminates_on_missing_self_parent_root():
    from utils.logger_config import RobustLoggerConfig

    fake_dir = _MissingSelfParentPath(("Z:", "NEKO", "logs"))
    assert RobustLoggerConfig._test_directory_writable(None, fake_dir) is False
    assert fake_dir.exists_calls == 1


@pytest.mark.unit
def test_ensure_log_directory_raises_instead_of_looping_on_missing_self_parent_root():
    from utils.logger_config import RobustLoggerConfig

    fake_dir = _MissingSelfParentPath(("Z:", "NEKO", "logs"))
    stub = RobustLoggerConfig.__new__(RobustLoggerConfig)
    stub.log_dir = fake_dir
    with pytest.raises(FileNotFoundError, match="Root directory does not exist"):
        stub._ensure_log_directory()


@pytest.mark.unit
def test_get_log_directory_falls_back_when_first_candidate_has_missing_root(tmp_path, monkeypatch):
    from utils import logger_config as backend

    missing_selected = _MissingSelfParentPath(("Z:", "selected", "logs"))
    fallback_docs = tmp_path / "documents"

    monkeypatch.setattr(
        backend,
        "log_directory_candidates",
        lambda *args, **kwargs: iter([missing_selected, fallback_docs / "N.E.K.O" / "logs"]),
    )
    stub = backend.RobustLoggerConfig.__new__(backend.RobustLoggerConfig)
    stub.app_name = "N.E.K.O"
    chosen = stub._get_log_directory()
    assert chosen == fallback_docs / "N.E.K.O" / "logs"


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_subdir",
    ["../escape", "foo/../bar", "..\\escape", ".", "..", "C:evil", "C:/evil", "a\x00b", "one//two"],
)
def test_host_log_subdirectory_rejects_traversal_and_drive_escapes(tmp_path, monkeypatch, bad_subdir):
    """B21B-004: log_subdir must reject traversal segments, drive prefixes, and NUL bytes."""
    from utils.logger_config import RobustLoggerConfig

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    with pytest.raises(ValueError):
        RobustLoggerConfig(service_name="Main", log_subdir=bad_subdir)


@pytest.mark.unit
def test_host_log_subdirectory_rejects_symlink_escape(tmp_path, monkeypatch):
    """B21B-004: log_subdir resolving outside log root via symlink must be rejected."""
    from utils.logger_config import RobustLoggerConfig

    storage = tmp_path / "storage"
    logs_dir = storage / "logs"
    logs_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    escape_link = logs_dir / "escape_link"
    try:
        escape_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage))
    with pytest.raises(ValueError):
        RobustLoggerConfig(service_name="Main", log_subdir="escape_link")


@pytest.mark.unit
@pytest.mark.parametrize("bad_component", ["../bad", "a/b", "a\\b", "C:bad", "bad\x00", "..", "."])
def test_log_filename_and_logger_config_reject_unsafe_service_or_app_names(tmp_path, monkeypatch, bad_component):
    """B21B-004: service_name and app_name must reject path separators, drive syntax, and traversal."""
    from utils.logger_config import RobustLoggerConfig
    from utils.logging_layout import log_filename

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    with pytest.raises(ValueError):
        log_filename(bad_component)
    with pytest.raises(ValueError):
        log_filename("Main", app_name=bad_component)
    with pytest.raises(ValueError):
        RobustLoggerConfig(service_name=bad_component)
    with pytest.raises(ValueError):
        RobustLoggerConfig(service_name="Main", app_name=bad_component)


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_subdir",
    [" .. ", " . ", ".", "..", "", "   ", "C:evil", "sub:evil", "a\x00b", "a/b", "a\\b"],
)
def test_logging_layout_log_subdirectory_rejects_unsafe_inputs(bad_subdir):
    """B21B-004: log_subdirectory must reject whitespace-padded dots, empty strings, colons, drives, NUL, and slashes."""
    from utils.logging_layout import log_subdirectory, resolve_log_directory

    with pytest.raises(ValueError):
        log_subdirectory(bad_subdir)
    with pytest.raises(ValueError):
        resolve_log_directory(log_subdir=bad_subdir)


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_app",
    ["../evil", " .. ", " . ", ".", "..", "", "   ", "C:evil", "app:evil", "a\x00b", "a/b", "a\\b"],
)
def test_logging_layout_resolve_log_directory_rejects_unsafe_app_name(bad_app):
    """B21B-004: log_directory_candidates and resolve_log_directory must validate app_name."""
    from utils.logging_layout import log_directory_candidates, resolve_log_directory

    with pytest.raises(ValueError):
        log_directory_candidates(app_name=bad_app)
    with pytest.raises(ValueError):
        resolve_log_directory(app_name=bad_app)


@pytest.mark.unit
def test_logging_layout_log_subdirectory_allows_none_and_plain_name():
    """B21B-004: log_subdirectory keeps None -> Path() and valid single directory names."""
    from utils.logging_layout import log_subdirectory

    assert log_subdirectory(None) == Path()
    assert log_subdirectory("plugin") == Path("plugin")


@pytest.mark.unit
@pytest.mark.parametrize("delay", [False, True])
def test_iter_handlers_rejects_symlinked_main_and_error_log_targets(tmp_path, monkeypatch, delay):
    """Final log file targets must not follow symlinks outside the log root (delay=False and delay=True)."""
    from utils.logger_config import RobustLoggerConfig, _SafeRotatingFileHandler
    from utils.logging_layout import log_filename

    selected_root = tmp_path / "storage"
    log_dir = selected_root / "logs"
    log_dir.mkdir(parents=True)
    outside_main = tmp_path / "outside_main.log"
    outside_error = tmp_path / "outside_error.log"
    outside_main.write_text("UNTOUCHED_MAIN", encoding="utf-8")
    outside_error.write_text("UNTOUCHED_ERROR", encoding="utf-8")

    main_target = log_dir / log_filename("Main")
    error_target = log_dir / log_filename("Main", kind="error")
    try:
        main_target.symlink_to(outside_main)
        error_target.symlink_to(outside_error)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(selected_root))
    config = RobustLoggerConfig(service_name="Main")
    handlers = list(config.iter_handlers(delay=delay))

    rotating_handlers = [h for h in handlers if isinstance(h, _SafeRotatingFileHandler)]
    for h in handlers:
        h.close()
    assert rotating_handlers == []
    assert outside_main.read_text(encoding="utf-8") == "UNTOUCHED_MAIN"
    assert outside_error.read_text(encoding="utf-8") == "UNTOUCHED_ERROR"


@pytest.mark.unit
def test_safe_rotating_file_handler_blocks_symlink_swap_and_supports_rollover(tmp_path, monkeypatch):
    """_SafeRotatingFileHandler refuses delayed/swap symlinks and rotates normal log files cleanly."""
    import logging
    from utils.logger_config import RobustLoggerConfig, _SafeRotatingFileHandler

    # Prevent delayed symlink-swap case from writing to an outside file even after __init__.
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    outside = tmp_path / "outside_swap.log"
    outside.write_text("ORIGINAL", encoding="utf-8")
    swap_path = logs_dir / "swap.log"

    handler = _SafeRotatingFileHandler(
        swap_path,
        allowed_root=logs_dir,
        maxBytes=128,
        backupCount=2,
        encoding="utf-8",
        delay=True,
    )
    try:
        try:
            swap_path.symlink_to(outside)
        except OSError:
            pytest.skip("symlinks unavailable in this environment")
        with pytest.raises(OSError):
            handler._open()
        assert outside.read_text(encoding="utf-8") == "ORIGINAL"
    finally:
        handler.close()

    # Verify normal logging and rotation continue working end-to-end.
    selected_root = tmp_path / "storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(selected_root))
    config = RobustLoggerConfig(service_name="Main", max_bytes=80, backup_count=2)
    handlers = list(config.iter_handlers(delay=False))
    rotating = [h for h in handlers if isinstance(h, _SafeRotatingFileHandler)]
    assert len(rotating) >= 2
    test_logger = logging.getLogger("test.safe_rotation")
    test_logger.propagate = False
    test_logger.setLevel(logging.INFO)
    for h in rotating:
        test_logger.addHandler(h)
    try:
        for idx in range(5):
            test_logger.error(f"line-{idx}-" + ("x" * 40))
    finally:
        for h in list(handlers):
            test_logger.removeHandler(h)
            h.close()

    assert Path(config.log_file).exists()
    assert (config.log_dir / f"{config.log_file.name}.1").exists()
