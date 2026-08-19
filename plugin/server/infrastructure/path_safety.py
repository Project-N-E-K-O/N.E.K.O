from __future__ import annotations

import os
from pathlib import Path
import stat


def is_link_or_reparse_point(path: Path) -> bool:
    """Return whether *path* redirects filesystem traversal elsewhere."""

    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def require_resolved_within(path: Path, root: Path, *, field: str) -> Path:
    """Resolve a path and reject parents that escape the controlled root."""

    resolved_root = root.resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise OSError(f"{field} resolves outside the controlled root") from exc
    return resolved


def ensure_tree_has_no_links_or_reparse_points(root: Path, *, field: str) -> None:
    """Walk a tree without following links and reject every reparse point."""

    if is_link_or_reparse_point(root):
        raise OSError(f"{field} contains an unsupported linked path: {root.name}")
    stack = [root]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                if is_link_or_reparse_point(path):
                    relative = path.relative_to(root).as_posix()
                    raise OSError(
                        f"{field} contains an unsupported linked path: {relative}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
