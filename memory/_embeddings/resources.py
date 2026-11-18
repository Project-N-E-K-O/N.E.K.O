"""Describe embedding resource files consistently for loading and fingerprint caches."""

import os


def resource_file_identity(paths) -> tuple:
    """Detect replacements and in-place changes even when size/mtime are retained.

    Archive extraction and copy operations may preserve modification timestamps.
    Device/inode identity distinguishes replacements; change time also detects
    same-inode updates that restore the old mtime. All fields come from one stat
    snapshot per path, and callers retain their existing ordered resource roles.
    This is a cache/lifecycle identity, not a substitute for the SHA-256 hashes.
    """
    identities = []
    for source in paths:
        path = os.fspath(source)
        info = os.stat(path)
        identities.append((path, info.st_dev, info.st_ino, info.st_size,
                           info.st_mtime_ns, info.st_ctime_ns))
    return tuple(identities)
