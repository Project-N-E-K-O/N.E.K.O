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

    def __fspath__(self) -> str:
        return str(self)


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
    from datetime import date as real_date
    import utils.logging_layout as logging_layout
    from utils.logger_config import RobustLoggerConfig, _SafeRotatingFileHandler
    from utils.logging_layout import log_filename

    class _FixedDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 23)

    monkeypatch.setattr(logging_layout, "date", _FixedDate)

    selected_root = tmp_path / "storage"
    log_dir = selected_root / "logs"
    log_dir.mkdir(parents=True)
    outside_main = tmp_path / "outside_main.log"
    outside_error = tmp_path / "outside_error.log"
    outside_main.write_text("UNTOUCHED_MAIN", encoding="utf-8")
    outside_error.write_text("UNTOUCHED_ERROR", encoding="utf-8")

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(selected_root))
    config = RobustLoggerConfig(service_name="Main")
    main_target = Path(config.log_file)
    error_target = config.log_dir / log_filename("Main", kind="error")
    try:
        main_target.symlink_to(outside_main)
        error_target.symlink_to(outside_error)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")

    handlers = list(config.iter_handlers(delay=delay))

    rotating_handlers = [h for h in handlers if isinstance(h, _SafeRotatingFileHandler)]
    for h in handlers:
        h.close()
    assert rotating_handlers == []
    assert outside_main.read_text(encoding="utf-8") == "UNTOUCHED_MAIN"
    assert outside_error.read_text(encoding="utf-8") == "UNTOUCHED_ERROR"


@pytest.mark.unit
def test_safe_rotating_file_handler_blocks_symlink_swap(tmp_path):
    """_SafeRotatingFileHandler refuses delayed/swap symlinks without touching outside files."""
    from utils.logger_config import _SafeRotatingFileHandler

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


@pytest.mark.unit
def test_safe_rotating_file_handler_supports_rollover(tmp_path, monkeypatch):
    """Normal logging and size rollover keep working and write expected records to .1 and active logs."""
    import logging
    from utils.logger_config import RobustLoggerConfig, _SafeRotatingFileHandler

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

    active_file = Path(config.log_file)
    backup_file = config.log_dir / f"{config.log_file.name}.1"
    assert active_file.exists()
    assert backup_file.exists()
    active_text = active_file.read_text(encoding="utf-8")
    backup_text = backup_file.read_text(encoding="utf-8")
    assert "line-4-" in active_text
    assert "line-3-" in backup_text


@pytest.mark.unit
def test_open_log_file_nofollow_rejects_fifo_and_validates_root_directory(tmp_path, monkeypatch):
    """Readerless FIFOs fail fast; invalid init roots fail closed; symlinked roots fail even with st_ino=0; recreated real dirs recover."""
    import os
    from utils import logger_config
    from utils.logger_config import _SafeRotatingFileHandler, _open_log_file_nofollow

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    if hasattr(os, "mkfifo"):
        fifo_path = logs_dir / "pipe.log"
        os.mkfifo(fifo_path)
        with pytest.raises(OSError):
            _open_log_file_nofollow(fifo_path, "a", "utf-8", None, logs_dir)

    # 1. Fail closed at handler init when allowed_root is missing, a regular file, or a symlink.
    missing_root = tmp_path / "missing_root"
    with pytest.raises(OSError):
        _SafeRotatingFileHandler(missing_root / "app.log", allowed_root=missing_root, delay=True)

    file_root = tmp_path / "file_root"
    file_root.write_text("not a dir", encoding="utf-8")
    with pytest.raises(OSError):
        _SafeRotatingFileHandler(file_root / "app.log", allowed_root=file_root, delay=True)

    real_target = tmp_path / "real_target"
    real_target.mkdir()
    sym_root = tmp_path / "sym_root"
    try:
        sym_root.symlink_to(real_target, target_is_directory=True)
    except OSError:
        sym_root = None
    if sym_root is not None:
        with pytest.raises(OSError):
            _SafeRotatingFileHandler(sym_root / "app.log", allowed_root=sym_root, delay=True)

    # 2. Legitimate delete + recreate of non-symlink allowed_root at same canonical path refreshes identity and recovers.
    recreate_root = tmp_path / "recreate_logs"
    recreate_root.mkdir()
    log_path = recreate_root / "app.log"
    handler = _SafeRotatingFileHandler(
        log_path,
        allowed_root=recreate_root,
        maxBytes=128,
        backupCount=1,
        encoding="utf-8",
        delay=True,
    )
    try:
        recreate_root.rmdir()
        recreate_root.mkdir()
        with handler._open() as stream:
            stream.write("recovered\n")
        assert (recreate_root / "app.log").read_text(encoding="utf-8") == "recovered\n"
    finally:
        handler.close()

    # 3. Replacing allowed_root or an ancestor with a symlink to outside raises OSError even when st_ino == 0.
    parent_dir = tmp_path / "parent_base"
    nested_root = parent_dir / "nested_logs"
    nested_root.mkdir(parents=True)
    outside_parent = tmp_path / "outside_parent"
    (outside_parent / "nested_logs").mkdir(parents=True)
    nested_log = nested_root / "app.log"
    nested_log.write_text("initial\n", encoding="utf-8")

    orig_lstat = os.lstat

    class _ZeroInoStatProxy:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.st_ino = 0

        def __getattr__(self, name: str):
            return getattr(self._wrapped, name)

        def __getitem__(self, idx):
            return 0 if idx == 1 else self._wrapped[idx]

    def _zero_ino_lstat(p):
        return _ZeroInoStatProxy(orig_lstat(p))

    monkeypatch.setattr(logger_config.os, "lstat", _zero_ino_lstat)
    zero_ino_handler = _SafeRotatingFileHandler(
        nested_log,
        allowed_root=nested_root,
        maxBytes=128,
        backupCount=1,
        encoding="utf-8",
        delay=True,
    )
    assert zero_ino_handler._allowed_root_identity is None
    try:
        if sym_root is not None:
            # Swap ancestor parent_dir with symlink -> outside_parent
            (nested_root / "app.log").unlink()
            nested_root.rmdir()
            parent_dir.rmdir()
            parent_dir.symlink_to(outside_parent, target_is_directory=True)
            with pytest.raises(OSError):
                zero_ino_handler._open()
            with pytest.raises(OSError):
                zero_ino_handler.doRollover()
    finally:
        zero_ino_handler.close()


@pytest.mark.unit
def test_is_symlink_or_reparse_point_allows_onedrive_cloud_tags_and_blocks_symlinks_junctions(
    tmp_path, monkeypatch
):
    """Windows reparse check allows OneDrive cloud placeholder tags while blocking symlinks and junctions."""
    import stat as stat_mod
    from types import SimpleNamespace
    from utils import logger_config

    sample = tmp_path / "onedrive.log"
    sample.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(logger_config.os.path, "islink", lambda _p: False)
    monkeypatch.setattr(stat_mod, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400, raising=False)
    monkeypatch.setattr(stat_mod, "IO_REPARSE_TAG_SYMLINK", 0xA000000C, raising=False)
    monkeypatch.setattr(stat_mod, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003, raising=False)

    monkeypatch.setattr(
        logger_config.os,
        "lstat",
        lambda _p: SimpleNamespace(st_file_attributes=0x0400, st_reparse_tag=0x9000001A),
    )
    assert logger_config._is_symlink_or_reparse_point(sample) is False

    for bad_tag in (0xA000000C, 0xA0000003):
        monkeypatch.setattr(
            logger_config.os,
            "lstat",
            lambda _p, tag=bad_tag: SimpleNamespace(st_file_attributes=0x0400, st_reparse_tag=tag),
        )
        assert logger_config._is_symlink_or_reparse_point(sample) is True


@pytest.mark.unit
def test_test_directory_writable_rejects_symlink_and_falls_back_to_next_candidate(tmp_path, monkeypatch):
    """Symlinked primary logs/ candidate is skipped by _get_log_directory and RobustLoggerConfig."""
    import logging

    from utils import logger_config as backend

    selected_root = tmp_path / "selected-root"
    selected_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    symlinked_logs = selected_root / "logs"
    try:
        symlinked_logs.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")

    dangling_root = tmp_path / "dangling-root"
    dangling_root.mkdir()
    dangling_logs = dangling_root / "logs"
    dangling_target = tmp_path / "nonexistent-target"
    dangling_logs.symlink_to(dangling_target, target_is_directory=True)
    assert backend.RobustLoggerConfig._test_directory_writable(None, dangling_logs) is False
    assert not dangling_target.exists()

    fallback_docs = tmp_path / "documents"
    fallback_docs.mkdir()
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(selected_root))
    monkeypatch.setattr(
        backend.RobustLoggerConfig,
        "_get_documents_directory",
        lambda self: fallback_docs,
    )

    config = backend.RobustLoggerConfig(service_name="Main", log_level=logging.INFO)
    expected_dir = fallback_docs / "N.E.K.O" / "logs"
    assert config.log_dir == expected_dir
    assert Path(config.get_log_directory_path()) == expected_dir
    assert not any(outside.iterdir()), "must not write probe files or logs into symlinked target"

    handlers = list(config.iter_handlers())
    try:
        file_handlers = [h for h in handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 2
        assert all(Path(h.baseFilename).parent == expected_dir for h in file_handlers)
    finally:
        for handler in handlers:
            handler.close()


@pytest.mark.unit
def test_is_symlink_or_reparse_point_fails_closed_on_non_path_type():
    """Non-path objects must fail closed via TypeError instead of returning False."""
    from utils import logger_config, logging_layout

    for bad_path in (object(), 123):
        with pytest.raises(TypeError):
            logging_layout.is_symlink_or_reparse_point(bad_path)
        with pytest.raises(TypeError):
            logger_config._is_symlink_or_reparse_point(bad_path)


@pytest.mark.unit
def test_select_log_directory_cold_reader_skips_symlinked_primary_candidate(tmp_path):
    """Cold reader _can_use / select_log_directory must skip symlinked and dangling logs/ candidates."""
    from utils import logging_layout

    selected_root = tmp_path / "selected-root"
    selected_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    symlinked_logs = selected_root / "logs"
    try:
        symlinked_logs.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")

    dangling_root = tmp_path / "dangling-root"
    dangling_root.mkdir()
    dangling_logs = dangling_root / "logs"
    dangling_logs.symlink_to(tmp_path / "nonexistent-target", target_is_directory=True)

    fallback_docs = tmp_path / "documents"
    fallback_docs.mkdir()
    fallback_logs = fallback_docs / "N.E.K.O" / "logs"

    assert logging_layout._can_use(symlinked_logs) is False
    assert logging_layout._can_use(dangling_logs) is False
    chosen = logging_layout.select_log_directory([symlinked_logs, dangling_logs, fallback_logs])
    assert chosen == fallback_logs


@pytest.mark.unit
@pytest.mark.parametrize("delay", [False, True])
def test_iter_handlers_delay_probe_creates_no_files_when_last_resort_fallback_is_symlink(
    tmp_path, monkeypatch, delay
):
    """When all candidates fail and last-resort fallback logs/ is a symlink, delay/live probes must not create files."""
    import logging
    from utils import logger_config as backend

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    fallback_logs = app_dir / "logs"
    try:
        fallback_logs.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")

    monkeypatch.setattr(backend, "log_directory_candidates", lambda *args, **kwargs: iter([]))
    monkeypatch.setattr(backend, "_get_writable_application_directory", lambda: app_dir)

    config = backend.RobustLoggerConfig(service_name="Main", log_level=logging.INFO)
    handlers = list(config.iter_handlers(delay=delay))
    try:
        file_handlers = [h for h in handlers if isinstance(h, logging.FileHandler)]
        stream_handlers = [h for h in handlers if isinstance(h, logging.StreamHandler)]
        assert file_handlers == []
        assert len(stream_handlers) >= 1
        assert list(outside.iterdir()) == []
    finally:
        for handler in handlers:
            handler.close()
