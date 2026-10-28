"""Exercise the real scanner/host fork race without starting user plugins."""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from plugin.core.host import PluginHost
from plugin.server.application.plugins.metadata_scanner import PluginMetadataScanError, scan_plugin_metadata_isolated
from utils import process_creation


def _hold_inherited_descriptors(release) -> None:
    release.wait(15)


@pytest.mark.skipif(
    not hasattr(os, "fork") or not hasattr(subprocess, "_fork_exec"),
    reason="requires the CPython POSIX fork/exec pipe implementation",
)
def test_scanner_finishes_while_a_concurrent_plugin_process_stays_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sibling must not retain Popen's temporary exec-error pipe writer."""
    (tmp_path / "race_plugin.py").write_text("class Plugin:\n    pass\n", encoding="utf-8")
    config = tmp_path / "plugin.toml"
    config.write_text("[plugin]\nid='race_plugin'\n", encoding="utf-8")
    context = multiprocessing.get_context("fork")
    release_child = context.Event()
    exec_window = threading.Event()
    start_attempted = threading.Event()
    host_started = threading.Event()
    scan_finished = threading.Event()
    errors: list[BaseException] = []
    results: list[object] = []
    real_fork_exec = subprocess._fork_exec

    def overlap_host_fork(*args, **kwargs):
        pid = real_fork_exec(*args, **kwargs)
        # At this point Popen still owns its close-on-exec error-pipe writer.
        # A bare multiprocessing fork copies it even though exec would close it.
        exec_window.set()
        if not start_attempted.wait(5):
            raise AssertionError("host did not attempt concurrent creation")
        # Unfixed code can finish that fork inside this window. A coordinated
        # host waits outside it; keep only creation serialized, not scan I/O.
        host_started.wait(0.5)
        return pid

    monkeypatch.setattr(subprocess, "_fork_exec", overlap_host_fork)
    host = object.__new__(PluginHost)
    host.process = context.Process(target=_hold_inherited_descriptors, args=(release_child,))

    def scan():
        try:
            results.append(scan_plugin_metadata_isolated(
                plugin_id="race_plugin", module_path="race_plugin", class_name="Plugin",
                config_path=config, conf={}, pdata={}, python_requirement_paths=[tmp_path], timeout=10,
            ))
        except BaseException as error:
            errors.append(error)
        finally:
            scan_finished.set()

    def start_host():
        start_attempted.set()
        try:
            host._start_process()
            host_started.set()
        except BaseException as error:
            errors.append(error)

    scanner = threading.Thread(target=scan, daemon=True)
    starter = threading.Thread(target=start_host, daemon=True)
    scanner.start()
    try:
        assert exec_window.wait(5), "scanner did not enter process creation"
        starter.start()
        assert scan_finished.wait(5), (
            "metadata scan is stuck in Popen while a live sibling owns its exec-error pipe"
        )
        assert host_started.wait(5)
        assert host.process.is_alive(), "scan must complete before the sibling exits"
        assert not errors, errors
        assert len(results) == 1
    finally:
        start_attempted.set()
        release_child.set()
        if starter.ident is not None:
            starter.join(5)
        if host.process.pid is not None:
            host.process.join(5)
            if host.process.is_alive():
                host.process.kill()
                host.process.join(5)
            host.process.close()
        scanner.join(5)
    assert not scanner.is_alive()
    assert not starter.is_alive()


def test_scan_budget_includes_waiting_for_a_sibling_creation(tmp_path, monkeypatch):
    occupied = threading.Event()
    release = threading.Event()

    def occupy():
        occupied.set()
        assert release.wait(5)

    owner = threading.Thread(target=lambda: process_creation.start(SimpleNamespace(start=occupy)), daemon=True)
    owner.start()
    assert occupied.wait(3)

    def forbidden(*args, **kwargs):
        raise AssertionError("expired scan must not create a metadata worker later")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    try:
        with pytest.raises(PluginMetadataScanError) as caught:
            scan_plugin_metadata_isolated(
                plugin_id="demo", module_path="unused", class_name="Plugin",
                config_path=tmp_path / "plugin.toml", conf={}, pdata={}, timeout=0.05,
            )
        assert caught.value.error_type == "TimeoutExpired"
    finally:
        release.set()
        owner.join(3)
    assert not owner.is_alive()


def test_plugin_import_does_not_hold_the_creation_window(tmp_path):
    ready = tmp_path / "import-ready"
    release_import = tmp_path / "release-import"
    (tmp_path / "slow_import.py").write_text(
        "from pathlib import Path\nimport time\n"
        f"Path({str(ready)!r}).touch()\n"
        "deadline = time.monotonic() + 10\n"
        f"while not Path({str(release_import)!r}).exists():\n"
        "    if time.monotonic() > deadline: raise RuntimeError('test import not released')\n"
        "    time.sleep(0.01)\n"
        "class Plugin:\n    pass\n", encoding="utf-8",
    )
    config = tmp_path / "plugin.toml"
    config.write_text("[plugin]\nid='slow_import'\n", encoding="utf-8")
    errors = []

    def scan():
        try:
            scan_plugin_metadata_isolated(
                plugin_id="slow_import", module_path="slow_import", class_name="Plugin",
                config_path=config, conf={}, pdata={}, python_requirement_paths=[tmp_path], timeout=10,
            )
        except BaseException as error:
            errors.append(error)

    scanner = threading.Thread(target=scan, daemon=True)
    scanner.start()
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        assert ready.exists(), "metadata worker did not begin importing the plugin"
        created = []
        process_creation.start(SimpleNamespace(start=lambda: created.append(True)), deadline=time.monotonic() + 1)
        assert created == [True]
        assert scanner.is_alive(), "creation must be allowed before the slow import completes"
    finally:
        release_import.touch()
        scanner.join(5)
    assert not scanner.is_alive()
    assert not errors, errors


def test_scan_reaps_a_process_returned_after_its_creation_deadline(tmp_path, monkeypatch):
    from plugin.server.application.plugins import metadata_scanner

    processes = []

    def create(*args, **kwargs):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        processes.append(process)
        return process

    # Only the scanner's clock advances; never replace the stdlib clock shared
    # by the creation lock, psutil, timers and background framework threads.
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(metadata_scanner, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    monkeypatch.setattr(process_creation, "popen", create)
    try:
        with pytest.raises(PluginMetadataScanError) as caught:
            scan_plugin_metadata_isolated(
                plugin_id="demo", module_path="unused", class_name="Plugin",
                config_path=tmp_path / "plugin.toml", conf={}, pdata={}, timeout=1,
            )
        assert caught.value.error_type == "TimeoutExpired"
        assert processes[0].poll() is not None
        assert processes[0].stdin.closed
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
