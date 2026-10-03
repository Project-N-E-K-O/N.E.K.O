"""Bounded, no-follow reads that prove a file through one OS handle."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def read_bounded_regular_file(path: str | Path, *, max_bytes: int) -> bytes:
    path = Path(path)
    if os.name == "nt":
        descriptor = _open_windows_regular_file(path, max_bytes=max_bytes)
    else:
        descriptor = _open_posix_regular_file(path, max_bytes=max_bytes)
    try:
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) > max_bytes:
        raise ValueError("file exceeds its size limit")
    return raw


def _open_posix_regular_file(path: Path, *, max_bytes: int) -> int:
    try:
        link_metadata = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError("file is unavailable") from exc
    if stat.S_ISLNK(link_metadata.st_mode):
        raise ValueError("file is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("file is not a regular file")
        if metadata.st_size > max_bytes:
            raise ValueError("file exceeds its size limit")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _open_windows_regular_file(path: Path, *, max_bytes: int) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(
        str(path),
        0x80000000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x00200000 | 0x08000000,
        None,
    )
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if error in {2, 3}:
            raise FileNotFoundError(error, os.strerror(error), str(path))
        raise OSError(error, os.strerror(error), str(path))
    try:
        get_file_type = kernel32.GetFileType
        get_file_type.argtypes = (wintypes.HANDLE,)
        get_file_type.restype = wintypes.DWORD
        if get_file_type(handle) != 0x0001:
            raise ValueError("file is not a regular disk file")

        information = _ByHandleFileInformation()
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        )
        get_information.restype = wintypes.BOOL
        if not get_information(handle, ctypes.byref(information)):
            error = ctypes.get_last_error()
            raise OSError(error, os.strerror(error), str(path))
        if information.file_attributes & (0x00000400 | 0x00000010):
            raise ValueError("file is a reparse point or directory")
        size = (int(information.file_size_high) << 32) | int(
            information.file_size_low
        )
        if size > max_bytes:
            raise ValueError("file exceeds its size limit")

        get_final_path = kernel32.GetFinalPathNameByHandleW
        get_final_path.argtypes = (
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        get_final_path.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_final_path(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            error = ctypes.get_last_error()
            raise OSError(error, os.strerror(error), str(path))
        final_path = buffer.value
        if final_path.startswith("\\\\?\\UNC\\"):
            final_path = "\\\\" + final_path[8:]
        elif final_path.startswith("\\\\?\\"):
            final_path = final_path[4:]
        declared = os.path.normcase(os.path.normpath(os.path.abspath(path)))
        opened = os.path.normcase(os.path.normpath(os.path.abspath(final_path)))
        if opened != declared:
            raise ValueError("file final path does not match its declared leaf")

        descriptor = msvcrt.open_osfhandle(
            int(handle),
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        handle = None
        return descriptor
    finally:
        if handle is not None:
            kernel32.CloseHandle(handle)
