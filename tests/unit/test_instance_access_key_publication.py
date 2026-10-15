"""Keep the shared access key stable across Windows publication races."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from threading import Event

import pytest

import utils.instance_access as access


@pytest.mark.parametrize("winerror", [None, 5, 32, 33])
def test_reader_waits_for_publisher_and_reuses_complete_key(monkeypatch, tmp_path, winerror):
    monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    path = tmp_path / "instance_access.key"
    path.write_text("", encoding="utf-8")
    key = "published-instance-key-" + "x" * 40
    read_text = Path.read_text
    file_lock = access.FileLock
    reader_waiting = Event()
    published = Event()
    reads = []

    def publication_conflict(self, *args, **kwargs):
        if self == path:
            reads.append(1)
            if len(reads) == 1:
                error = PermissionError("Windows publication sharing conflict")
                if winerror is not None:
                    error.winerror = winerror
                raise error
            assert published.is_set(), "Reader must wait for the publisher's lock"
        return read_text(self, *args, **kwargs)

    def reader_lock(*args, **kwargs):
        reader_waiting.set()
        return file_lock(*args, **kwargs)

    monkeypatch.setattr(Path, "read_text", publication_conflict)
    monkeypatch.setattr(access, "FileLock", reader_lock)
    monkeypatch.setattr(access.secrets, "token_urlsafe", lambda *_args: pytest.fail("Published key must be reused"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with file_lock(str(path) + ".lock", timeout=5):
            future = executor.submit(access.instance_key)
            assert reader_waiting.wait(timeout=5), "Reader must fall back to the publication lock"
            temporary = tmp_path / "publisher-key"
            temporary.write_text(key, encoding="utf-8")
            temporary.replace(path)
            published.set()
        assert future.result(timeout=5) == key
    assert read_text(path, encoding="utf-8") == key
    assert len(reads) == 2


@pytest.mark.parametrize("winerror", [None, 13, 5, 32, 33])
def test_unreadable_existing_key_is_never_replaced(monkeypatch, tmp_path, winerror):
    monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    path = tmp_path / "instance_access.key"
    key = "existing-instance-key-" + "x" * 40
    path.write_text(key, encoding="utf-8")
    read_text = Path.read_text
    reads = []
    error = PermissionError("Persistent access denial")
    if winerror is not None:
        error.winerror = winerror

    def denied(self, *args, **kwargs):
        if self == path:
            reads.append(1)
            raise error
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    monkeypatch.setattr(access.secrets, "token_urlsafe", lambda *_args: pytest.fail("Unreadable key must not rotate"))
    with pytest.raises(PermissionError) as raised:
        access.instance_key()
    assert raised.value is error
    assert len(reads) == 2
    assert read_text(path, encoding="utf-8") == key
    assert not list(tmp_path.glob(".instance-key-*"))


@pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows file sharing semantics")
def test_real_windows_read_conflict_reuses_published_key(monkeypatch, tmp_path):
    import ctypes
    from ctypes import wintypes

    monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    path = tmp_path / "instance_access.key"
    path.write_text("", encoding="utf-8")
    key = "published-instance-key-" + "x" * 40
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    file_lock = access.FileLock
    reader_waiting = Event()

    def reader_lock(*args, **kwargs):
        reader_waiting.set()
        return file_lock(*args, **kwargs)

    monkeypatch.setattr(access, "FileLock", reader_lock)
    monkeypatch.setattr(access.secrets, "token_urlsafe", lambda *_args: pytest.fail("Published key must be reused"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with file_lock(str(path) + ".lock", timeout=5):
            # Exclusive native handle reproduces the CRT read error, which has
            # errno=13 and no winerror despite being a Windows sharing conflict.
            handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
            if handle == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                with pytest.raises(PermissionError):
                    path.read_text(encoding="utf-8")
                future = executor.submit(access.instance_key)
                assert reader_waiting.wait(timeout=5), "Reader must wait for publication"
            finally:
                kernel.CloseHandle(handle)
            temporary = tmp_path / "publisher-key"
            temporary.write_text(key, encoding="utf-8")
            temporary.replace(path)
        assert future.result(timeout=5) == key
    assert path.read_text(encoding="utf-8") == key
