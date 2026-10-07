"""Standard-library file locks shared by configuration and resource delivery."""

from contextlib import contextmanager
import os
from pathlib import Path
import stat


class ResourceFileLockBusy(OSError):
    pass


def canonical_resource_root(root: Path) -> Path:
    """Resolve redirected ancestors while rejecting a replaced cache root."""
    if root.is_symlink():
        raise ValueError("resource_cache_unsafe")
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        pass
    else:
        # Python 3.11 has no Path.is_junction(). Include NTFS junctions and
        # other reparse points at the cache boundary itself.
        if getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError("resource_cache_unsafe")
    return root.resolve()


@contextmanager
def resource_file_lock(path: Path):
    """The OS releases this lock when a worker is killed or the process exits."""
    if path.is_symlink():
        raise OSError("unsafe resource lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "r+b") as handle:
        if (not stat.S_ISREG(os.fstat(handle.fileno()).st_mode)
                or os.fstat(handle.fileno()).st_size > 1):
            raise OSError("unsafe resource lock")
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ResourceFileLockBusy("resource operation busy") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
