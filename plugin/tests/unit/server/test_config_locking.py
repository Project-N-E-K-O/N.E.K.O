from __future__ import annotations

import io

import pytest

from plugin.server.infrastructure import config_locking as module


pytestmark = pytest.mark.plugin_unit


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
