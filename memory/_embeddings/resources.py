"""Describe embedding resource files consistently for loading and fingerprint caches."""

import os


def resource_file_identity(paths) -> tuple:
    """Identify replacement/content metadata without treating chmod as an upgrade.

    Device/inode distinguishes replacement; size and mtime track ordinary file
    updates. ctime is intentionally excluded because permissions and metadata
    changes must not invalidate a loaded model. Exact package compatibility
    still uses resource SHA-256, rather than this local cache identity.
    """
    identities = []
    for source in paths:
        path = os.fspath(source)
        info = os.stat(path)
        identities.append((path, info.st_dev, info.st_ino, info.st_size,
                           info.st_mtime_ns))
    return tuple(identities)
