from __future__ import annotations

import io
import multiprocessing
import os
from pathlib import Path

import pytest

from plugin.server.infrastructure import config_locking as module


pytestmark = pytest.mark.plugin_unit


def _read_config_in_other_process(config_path: str, connection) -> None:
    path = Path(config_path)
    lock_path = path.with_name(f"{path.name}.lock")
    with lock_path.open("a+b") as lock_file:
        try:
            if module._msvcrt is not None:
                module._msvcrt.locking(lock_file.fileno(), module._msvcrt.LK_NBLCK, 1)
            else:
                module._fcntl.flock(lock_file.fileno(), module._fcntl.LOCK_EX | module._fcntl.LOCK_NB)
        except OSError:
            connection.send("blocked")
        else:
            if module._msvcrt is not None:
                lock_file.seek(0, os.SEEK_SET)
                module._msvcrt.locking(lock_file.fileno(), module._msvcrt.LK_UNLCK, 1)
            else:
                module._fcntl.flock(lock_file.fileno(), module._fcntl.LOCK_UN)
            connection.send("unprotected")
    connection.recv()
    with module.plugin_config_file_lock(path):
        connection.send(path.read_text(encoding="utf-8"))
    connection.close()


def test_config_snapshot_lock_coordinates_spawned_processes(tmp_path: Path) -> None:
    if module._msvcrt is None and module._fcntl is None:
        pytest.skip("OS file locking unavailable")
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("old", encoding="utf-8")
    spawn = multiprocessing.get_context("spawn")
    parent, child = spawn.Pipe()
    process = spawn.Process(target=_read_config_in_other_process, args=(str(config_path), child))
    try:
        with module.plugin_config_file_lock(config_path):
            process.start()
            child.close()
            assert parent.poll(15), "child did not attempt the lock"
            assert parent.recv() == "blocked"
            config_path.write_text("committed", encoding="utf-8")
        parent.send("read")
        assert parent.poll(15), "child did not finish its snapshot"
        assert parent.recv() == "committed"
        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        parent.close()
        child.close()


def test_config_snapshot_lock_fails_closed_for_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        with module.plugin_config_file_lock(tmp_path / "missing" / "plugin.toml"):
            pytest.fail("missing lock directory must not bypass synchronization")


class _FakeFile(io.BytesIO):
    def fileno(self) -> int:
        return 123


def test_windows_file_lock_unlocks_from_locked_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int, int]] = []

    class _FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd: int, mode: int, size: int) -> None:
            calls.append((mode, fake_file.tell(), size))

    fake_file = _FakeFile(b"abcdef")

    monkeypatch.setattr(module, "_msvcrt", _FakeMsvcrt)
    monkeypatch.setattr(module, "_fcntl", None)

    with module.file_lock(fake_file):
        fake_file.seek(3)

    assert calls == [
        (_FakeMsvcrt.LK_LOCK, 0, 6),
        (_FakeMsvcrt.LK_UNLCK, 0, 6),
    ]
