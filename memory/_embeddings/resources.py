"""Describe embedding resource files consistently for loading and fingerprint caches."""

import os
import sys


def _change_stamp(path, info):
    """Use Windows ChangeTime, not Python 3.11's creation-time st_ctime."""
    if sys.platform != "win32":
        return info.st_ctime_ns
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in ("creation", "access", "write", "change")] + [
            ("attributes", wintypes.DWORD)]

    query = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    query.restype = wintypes.BOOL
    with open(path, "rb") as stream:
        basic = FileBasicInfo()
        if not query(msvcrt.get_osfhandle(stream.fileno()), 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise ctypes.WinError(ctypes.get_last_error())
        return basic.change


def resource_file_identity(paths) -> tuple:
    """Invalidate digest caches even after equal-size writes restore mtime.

    This is a cache key, not semantic identity: metadata-only changes can
    rehash resources, but only actual digest changes invalidate a loaded model.
    """
    identities = []
    for source in paths:
        path = os.path.normpath(os.fspath(source))
        info = os.stat(path)
        identities.append((path, info.st_dev, info.st_ino, info.st_size,
                           info.st_mtime_ns, _change_stamp(path, info)))
    return tuple(identities)
