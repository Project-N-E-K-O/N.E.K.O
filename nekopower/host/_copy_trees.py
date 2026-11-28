# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Directory-link helpers, 3-way text merge, and non-git working copy storage.

Synchronous filesystem and merge helpers used by ``nekopower.host.worktrees``
inside worker threads. Keeps all non-git working copy metadata outside the
user's project directory (under ``<copies_base>/.nekopower-copies/<id>/``),
never mutates the project directory except during an atomic landing with full
backup/restore, and always unlinks directory junctions/symlinks before any
recursive deletion so a project's dependency directories are never deleted.
"""

from __future__ import annotations

import contextlib
import difflib
import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import threading
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

COPY_META_DIR = ".nekopower-copies"
_META_FILE = "meta.json"
_BASE_MANIFEST_FILE = "base_manifest.json"
_BLOBS_DIR = "blobs"
_PENDING_DIR = "pending"
_CONFLICTS_DIR = "conflicts"
_ROLLBACK_INCOMPLETE_FILE = "rollback_incomplete.json"
_XATTR_MISSING_ERRNOS = frozenset(
    code for code in (getattr(errno, "ENODATA", None), getattr(errno, "ENOATTR", None)) if code is not None
)


class CopyCancelled(RuntimeError):
    """Raised inside a synchronous copy worker thread when cancellation is requested."""


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CopyCancelled("copy operation cancelled")
_CHUNK_BYTES = 256 * 1024
_BINARY_PROBE_BYTES = 8000


class CopyTooLarge(Exception):
    """Raised when a non-git project directory exceeds copy limits."""


# -- path & link helpers ------------------------------------------------------


def fs_path(path: Path | str) -> str:
    """Return ``path`` in a form Windows opens past 260 characters; unchanged elsewhere."""
    text = os.path.abspath(str(path))
    if sys.platform != "win32" or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def same_path(left: Path | str, right: Path | str) -> bool:
    """Return whether two paths normalize to the same absolute path."""
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def relative_posix(path: Path, root: Path) -> str | None:
    """Return ``path`` relative to ``root`` with ``/`` separators, ``""`` for equal, or None if outside."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return None
    if rel == ".":
        return ""
    rel = rel.replace(os.sep, "/")
    if rel == ".." or rel.startswith("../"):
        return None
    return rel


def is_dir_link(path: Path | str) -> bool:
    """Return whether ``path`` is a symbolic link or a Windows directory junction/symlink reparse point."""
    raw = fs_path(path)
    if os.path.islink(raw):
        return True
    if sys.platform != "win32":
        return False
    try:
        info = os.lstat(raw)
    except OSError:
        return False
    reparse_attr = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not (getattr(info, "st_file_attributes", 0) & reparse_attr):
        return False
    tag = getattr(info, "st_reparse_tag", 0)
    mount_point = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
    symlink_tag = getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C)
    return tag in (mount_point, symlink_tag)


def make_dir_link(source: Path, destination: Path) -> None:
    """Create a directory link from ``destination`` to ``source`` (junction on Windows, symlink on POSIX)."""
    src = os.path.abspath(os.path.realpath(str(source)))
    dst = os.path.abspath(str(destination))
    if sys.platform == "win32":
        import _winapi  # type: ignore[import-not-found]

        # CreateJunction prepends \??\ internally; never pass a \\?\ extended prefix to it.
        _winapi.CreateJunction(src, dst)
        return
    os.symlink(src, dst, target_is_directory=True)


def remove_dir_link(path: Path | str) -> None:
    """Remove a single directory symlink or Windows junction without touching its target."""
    raw = fs_path(path)
    if os.path.islink(raw):
        os.unlink(raw)
        return
    if is_dir_link(path):
        os.rmdir(raw)


def unlink_dependencies(root: Path, links: Iterable[str]) -> list[str]:
    """Unlink recorded dependency directory links under ``root`` without touching their targets.

    Returns the relative paths that could not be unlinked.
    """
    failed: list[str] = []
    ordered = sorted(set(links), key=lambda item: item.count("/"), reverse=True)
    for rel in ordered:
        if has_link_ancestor(root, rel):
            # Refuse to unlink through a parent directory that was replaced with a symlink/junction.
            continue
        candidate = root / rel
        if not os.path.lexists(fs_path(candidate)):
            continue
        if not is_dir_link(candidate):
            # User or agent replaced it with a real directory/file: do not special-case unlink.
            continue
        try:
            remove_dir_link(candidate)
        except OSError:
            failed.append(rel)
    return failed


def remove_tree_safe(path: Path | str) -> None:
    """Remove ``path`` recursively, unlinking every directory junction/symlink first so targets are never touched."""
    raw = fs_path(path)
    if not os.path.lexists(raw):
        return
    if is_dir_link(path):
        remove_dir_link(path)
        return
    if os.path.isfile(raw) or os.path.islink(raw):
        with contextlib.suppress(OSError):
            os.chmod(raw, stat.S_IWRITE | stat.S_IREAD)
        os.unlink(raw)
        return

    # Unlink nested junctions/symlinks top-down before rmtree ever traverses them.
    for current, dirs, _files in os.walk(raw, topdown=True, followlinks=False):
        for name in list(dirs):
            full = os.path.join(current, name)
            if is_dir_link(full):
                dirs.remove(name)
                remove_dir_link(full)

    def clear_read_only(function: Any, failed: str, _info: Any) -> None:
        with contextlib.suppress(OSError):
            os.chmod(failed, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            function(failed)

    shutil.rmtree(raw, onerror=clear_read_only)


def link_dependency_paths(
    source_root: Path,
    dest_root: Path,
    candidates: Iterable[str],
    *,
    limit: int,
) -> tuple[str, ...]:
    """Link existing dependency directories from ``source_root`` into ``dest_root`` (best effort per entry)."""
    linked: list[str] = []
    seen: set[str] = set()
    for raw in sorted(candidates):
        rel = raw.strip().strip("/")
        if not rel or rel in seen or len(linked) >= limit:
            continue
        seen.add(rel)
        src = source_root / rel
        dst = dest_root / rel
        try:
            if not os.path.isdir(fs_path(src)):
                continue
            # Ensure resolved target is an actual directory (not a broken link).
            real_src = Path(os.path.realpath(str(src)))
            if not real_src.is_dir():
                continue
            # Do not link if destination already exists (tracked file/dir) or parent is missing/escaping.
            if os.path.lexists(fs_path(dst)):
                continue
            if not dst.parent.is_dir() or is_dir_link(dst.parent):
                continue
            if relative_posix(Path(os.path.realpath(str(dst.parent))), Path(os.path.realpath(str(dest_root)))) is None:
                continue
            make_dir_link(real_src, dst)
        except OSError:
            continue
        linked.append(rel)
    return tuple(linked)


def active_links(root: Path, recorded: Iterable[str]) -> tuple[str, ...]:
    """Return those of ``recorded`` that still exist as directory links under ``root``."""
    return tuple(rel for rel in recorded if is_dir_link(root / rel))


# -- 3-way text merge ---------------------------------------------------------


def is_binary_bytes(data: bytes) -> bool:
    """Return whether ``data`` looks binary (contains a NUL byte in its prefix, matching git's heuristic)."""
    return b"\x00" in data[:_BINARY_PROBE_BYTES]


_MAX_DIFF_LINES = 25_000
_MAX_DIFF_SPAN = 2_000
_MAX_DIFF_PAIR_WORK = 1_500_000


class _DiffWorkExceeded(Exception):
    """Raised internally when SequenceMatcher pair work exceeds the diff budget."""


def _trim_common_edges(left: list[bytes], right: list[bytes]) -> tuple[int, list[bytes], list[bytes]]:
    limit = min(len(left), len(right))
    prefix = 0
    while prefix < limit and left[prefix] == right[prefix]:
        prefix += 1
    suffix = 0
    rem = limit - prefix
    while suffix < rem and left[len(left) - 1 - suffix] == right[len(right) - 1 - suffix]:
        suffix += 1
    return prefix, left[prefix : len(left) - suffix], right[prefix : len(right) - suffix]


def _diff_work_exceeds_budget(left: list[bytes], right: list[bytes], budget: int | None = None) -> bool:
    limit = _MAX_DIFF_PAIR_WORK if budget is None else budget
    if not left or not right:
        return False
    left_counts = Counter(left)
    right_counts = Counter(right)
    work = 0
    for line, count in left_counts.items():
        other = right_counts.get(line)
        if other:
            work += count * other
            if work > limit:
                return True
    return False


def _prepare_diff_inputs(left: list[bytes], right: list[bytes]) -> tuple[int, list[bytes], list[bytes]] | None:
    """Return ``(prefix_offset, left_slice, right_slice)`` within SequenceMatcher budget, or None if over budget."""
    if max(len(left), len(right)) <= _MAX_DIFF_SPAN and not _diff_work_exceeds_budget(left, right):
        return 0, left, right
    prefix, left_mid, right_mid = _trim_common_edges(left, right)
    if max(len(left_mid), len(right_mid)) > _MAX_DIFF_SPAN or _diff_work_exceeds_budget(left_mid, right_mid):
        return None
    return prefix, left_mid, right_mid


def line_diff_counts(old: bytes | None, new: bytes | None) -> tuple[int | None, int | None]:
    """Return ``(added_lines, deleted_lines)`` between ``old`` and ``new``, or ``(None, None)`` for binary/oversized data."""
    old_bytes = old or b""
    new_bytes = new or b""
    if is_binary_bytes(old_bytes) or is_binary_bytes(new_bytes):
        return None, None
    old_lines = old_bytes.splitlines()
    new_lines = new_bytes.splitlines()
    if len(old_lines) + len(new_lines) > _MAX_DIFF_LINES:
        return None, None
    prepared = _prepare_diff_inputs(old_lines, new_lines)
    if prepared is None:
        return None, None
    _prefix, old_cmp, new_cmp = prepared
    matcher = difflib.SequenceMatcher(None, old_cmp, new_cmp, autojunk=False)
    added = 0
    deleted = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            deleted += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, deleted


@dataclass(frozen=True)
class _Hunk:
    start: int
    end: int
    lines: tuple[bytes, ...]


def _diff_hunks(base: list[bytes], side: list[bytes]) -> list[_Hunk]:
    prepared = _prepare_diff_inputs(base, side)
    if prepared is None:
        raise _DiffWorkExceeded()
    prefix, base_cmp, side_cmp = prepared
    matcher = difflib.SequenceMatcher(None, base_cmp, side_cmp, autojunk=False)
    hunks: list[_Hunk] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            hunks.append(_Hunk(i1 + prefix, i2 + prefix, tuple(side_cmp[j1:j2])))
    return hunks


def _reconstruct_region(base: list[bytes], hunks: list[_Hunk], region_start: int, region_end: int) -> list[bytes]:
    out: list[bytes] = []
    pos = region_start
    for hunk in hunks:
        if hunk.start > pos:
            out.extend(base[pos : hunk.start])
        out.extend(hunk.lines)
        pos = hunk.end
    if pos < region_end:
        out.extend(base[pos:region_end])
    return out


def _hunks_overlap(a: _Hunk, b: _Hunk) -> bool:
    # Two zero-length insertions at the exact same base offset overlap.
    if a.start == a.end and b.start == b.end:
        return a.start == b.start
    if a.start == a.end:
        return b.start <= a.start <= b.end
    if b.start == b.end:
        return a.start <= b.start <= a.end
    return a.start < b.end and b.start < a.end


def _ensure_trailing_nl(chunks: list[bytes]) -> list[bytes]:
    if not chunks:
        return []
    if chunks[-1].endswith((b"\n", b"\r")):
        return chunks
    return [*chunks[:-1], chunks[-1] + b"\n"]


def _whole_file_conflict(
    current: bytes,
    worktree: bytes,
    current_label: bytes,
    worktree_label: bytes,
) -> bytes:
    c_body = current if (not current or current.endswith((b"\n", b"\r"))) else current + b"\n"
    w_body = worktree if (not worktree or worktree.endswith((b"\n", b"\r"))) else worktree + b"\n"
    return (
        b"<<<<<<< "
        + current_label
        + b"\n"
        + c_body
        + b"=======\n"
        + w_body
        + b">>>>>>> "
        + worktree_label
        + b"\n"
    )


def merge3_bytes(
    base: bytes | None,
    current: bytes | None,
    worktree: bytes | None,
    *,
    current_label: bytes = b"project",
    worktree_label: bytes = b"worktree",
) -> tuple[bytes | None, bool]:
    """Perform a 3-way merge of ``(base, current, worktree)`` bytes.

    Returns ``(merged_bytes_or_None_for_delete, has_conflict)``.
    Binary files or modify/delete clashes return ``has_conflict=True``; for
    text files with overlapping divergent edits, standard conflict markers
    (``<<<<<<<``, ``=======``, ``>>>>>>>``) are embedded.
    """
    if worktree == base:
        return current, False
    if current == base:
        return worktree, False
    if current == worktree:
        return current, False

    # Deletion / creation clashes.
    if current is None or worktree is None:
        # One side deleted (or never had) what the other side modified/added.
        # Keep the worktree's version when present so preparing conflicts does not discard it.
        keep = worktree if worktree is not None else current
        return keep, True
    if base is None:
        # Both sides independently added different content at the same path.
        if is_binary_bytes(current) or is_binary_bytes(worktree):
            return worktree, True
        base_lines: list[bytes] = []
    else:
        if is_binary_bytes(base) or is_binary_bytes(current) or is_binary_bytes(worktree):
            return worktree, True
        base_lines = base.splitlines(keepends=True)

    curr_lines = current.splitlines(keepends=True)
    wt_lines = worktree.splitlines(keepends=True)
    if max(len(base_lines), len(curr_lines), len(wt_lines)) > _MAX_DIFF_LINES:
        # Avoid worst-case quadratic SequenceMatcher stalls when both sides edit huge files;
        # embed whole-file conflict markers in O(N) so marker checks still guard landing.
        return _whole_file_conflict(current, worktree, current_label, worktree_label), True
    try:
        curr_hunks = _diff_hunks(base_lines, curr_lines)
        wt_hunks = _diff_hunks(base_lines, wt_lines)
    except _DiffWorkExceeded:
        return _whole_file_conflict(current, worktree, current_label, worktree_label), True

    out: list[bytes] = []
    has_conflict = False
    ci = 0
    wi = 0
    pos = 0

    while ci < len(curr_hunks) or wi < len(wt_hunks):
        if ci < len(curr_hunks) and wi < len(wt_hunks):
            ch = curr_hunks[ci]
            wh = wt_hunks[wi]
            if not _hunks_overlap(ch, wh):
                if (ch.start, ch.end) < (wh.start, wh.end):
                    if ch.start > pos:
                        out.extend(base_lines[pos : ch.start])
                    out.extend(ch.lines)
                    pos = ch.end
                    ci += 1
                    continue
                if wh.start > pos:
                    out.extend(base_lines[pos : wh.start])
                out.extend(wh.lines)
                pos = wh.end
                wi += 1
                continue

            # Expand region to capture all transitively overlapping hunks.
            reg_start = min(ch.start, wh.start)
            reg_end = max(ch.end, wh.end)
            c_group = [ch]
            w_group = [wh]
            ci += 1
            wi += 1
            changed = True
            while changed:
                changed = False
                while ci < len(curr_hunks) and (
                    curr_hunks[ci].start < reg_end
                    or (curr_hunks[ci].start == curr_hunks[ci].end == reg_end)
                ):
                    c_group.append(curr_hunks[ci])
                    reg_end = max(reg_end, curr_hunks[ci].end)
                    ci += 1
                    changed = True
                while wi < len(wt_hunks) and (
                    wt_hunks[wi].start < reg_end
                    or (wt_hunks[wi].start == wt_hunks[wi].end == reg_end)
                ):
                    w_group.append(wt_hunks[wi])
                    reg_end = max(reg_end, wt_hunks[wi].end)
                    wi += 1
                    changed = True

            if reg_start > pos:
                out.extend(base_lines[pos:reg_start])
            c_chunk = _reconstruct_region(base_lines, c_group, reg_start, reg_end)
            w_chunk = _reconstruct_region(base_lines, w_group, reg_start, reg_end)
            if c_chunk == w_chunk:
                out.extend(c_chunk)
            else:
                has_conflict = True
                out.append(b"<<<<<<< " + current_label + b"\n")
                out.extend(_ensure_trailing_nl(c_chunk))
                out.append(b"=======\n")
                out.extend(_ensure_trailing_nl(w_chunk))
                out.append(b">>>>>>> " + worktree_label + b"\n")
            pos = reg_end
        elif ci < len(curr_hunks):
            ch = curr_hunks[ci]
            if ch.start > pos:
                out.extend(base_lines[pos : ch.start])
            out.extend(ch.lines)
            pos = ch.end
            ci += 1
        else:
            wh = wt_hunks[wi]
            if wh.start > pos:
                out.extend(base_lines[pos : wh.start])
            out.extend(wh.lines)
            pos = wh.end
            wi += 1

    if pos < len(base_lines):
        out.extend(base_lines[pos:])
    return b"".join(out), has_conflict


# -- non-git working copy storage & scanning ----------------------------------


@dataclass(frozen=True)
class CopyScan:
    """Files, skipped items, dependency dirs, and totals found under a directory."""

    files: dict[str, dict[str, Any]]
    skipped: tuple[str, ...]
    dependency_dirs: tuple[str, ...]
    total_bytes: int
    digest: str


def copy_meta_root(base_dir: Path) -> Path:
    """Return the hidden metadata root for non-git copies inside ``base_dir``."""
    return base_dir / COPY_META_DIR


def copy_meta_dir(copy_path: Path) -> Path:
    """Return the metadata directory for the non-git copy at ``copy_path``."""
    return copy_meta_root(copy_path.parent) / copy_path.name


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def manifest_digest(files: Mapping[str, Mapping[str, Any]]) -> str:
    """Return a deterministic SHA-256 digest of a file manifest."""
    normalized = []
    for name in sorted(files):
        entry = files[name]
        kind = entry.get("kind", "file")
        if kind == "symlink":
            normalized.append((name, "symlink", str(entry.get("target", ""))))
        else:
            exec_bit = bool(int(entry.get("mode", 0)) & stat.S_IXUSR) if sys.platform != "win32" else False
            normalized.append((name, "file", str(entry.get("sha256", "")), exec_bit))
    raw = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def scan_copy_directory(
    root: Path,
    *,
    dependency_dirs: Iterable[str],
    max_file_bytes: int,
    max_files: int | None = None,
    max_total_bytes: int | None = None,
    active_dep_links: Iterable[str] = (),
    include_large: Iterable[str] = (),
    hash_files: bool = True,
    discover_dependencies: bool = True,
) -> CopyScan:
    """Walk ``root`` and catalog its files, skipped items, and linkable dependency directories."""
    dep_names = frozenset(dependency_dirs)
    active_set = frozenset(active_dep_links)
    keep_large = frozenset(include_large)
    tracked_dirs = {up for path in keep_large for up in _parents(path)}
    top = fs_path(root)
    files: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    found_deps: list[str] = []
    total_bytes = 0

    def _raise_walk_error(err: OSError) -> None:
        raise err

    for current, dirs, filenames in os.walk(top, topdown=True, followlinks=False, onerror=_raise_walk_error):
        dirs.sort()
        filenames.sort()
        rel_dir = os.path.relpath(current, top)
        prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"

        for dname in list(dirs):
            rel = f"{prefix}{dname}"
            full = os.path.join(current, dname)
            link = is_dir_link(full)
            if rel in active_set and link:
                dirs.remove(dname)
                continue
            if not link and os.path.lexists(os.path.join(full, ".git")):
                dirs.remove(dname)
                skipped.append(rel + "/")
                continue
            if discover_dependencies and dname in dep_names:
                if rel in tracked_dirs and not link:
                    # Baseline already tracks real files inside this directory (e.g. an agent-created
                    # `vendor/` directory), so keep scanning it rather than treating it as a dep dir.
                    continue
                dirs.remove(dname)
                if not link:
                    found_deps.append(rel)
                elif os.path.isdir(full):
                    # Existing valid directory link in source project: link its resolved target too.
                    found_deps.append(rel)
                continue
            if link:
                dirs.remove(dname)
                if os.path.islink(full):
                    files[rel] = {"kind": "symlink", "target": os.readlink(full)}
                    if max_files is not None and len(files) > max_files:
                        raise CopyTooLarge(f"{root} has more than {max_files} files")
                else:
                    skipped.append(rel + "/")
                continue

        for fname in filenames:
            rel = f"{prefix}{fname}"
            full = os.path.join(current, fname)
            if os.path.islink(full):
                files[rel] = {"kind": "symlink", "target": os.readlink(full)}
                if max_files is not None and len(files) > max_files:
                    raise CopyTooLarge(f"{root} has more than {max_files} files")
                continue
            try:
                info = os.lstat(full)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                # Skip special files (named pipes/FIFOs, sockets, device nodes) so opening never stalls.
                skipped.append(rel)
                continue
            if info.st_size > max_file_bytes and rel not in keep_large:
                skipped.append(rel)
                continue
            total_bytes += info.st_size
            if max_total_bytes is not None and total_bytes > max_total_bytes:
                raise CopyTooLarge(f"{root} exceeds {max_total_bytes} bytes of copyable files")
            digest = _hash_file(full) if hash_files else ""
            files[rel] = {
                "kind": "file",
                "sha256": digest,
                "size": info.st_size,
                "mode": stat.S_IMODE(info.st_mode),
            }
            if max_files is not None and len(files) > max_files:
                raise CopyTooLarge(f"{root} has more than {max_files} files")

    return CopyScan(
        files=files,
        skipped=tuple(sorted(skipped)),
        dependency_dirs=tuple(sorted(found_deps)),
        total_bytes=total_bytes,
        digest=manifest_digest(files) if hash_files else "",
    )


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with open(fs_path(tmp), "wb") as handle:
            handle.write(data)
        os.replace(fs_path(tmp), fs_path(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(fs_path(tmp))
        raise


def _atomic_write_json(path: Path, payload: Any) -> None:
    data = json.dumps(payload, indent=1, sort_keys=True).encode("utf-8")
    _atomic_write_bytes(path, data)


def _store_blob_file(src: Path, sha: str, blobs_dir: Path) -> None:
    blobs_dir.mkdir(parents=True, exist_ok=True)
    dst = blobs_dir / sha
    if os.path.isfile(fs_path(dst)):
        return
    tmp = blobs_dir / f".{sha}.{uuid.uuid4().hex}.tmp"
    shutil.copyfile(fs_path(src), fs_path(tmp))
    try:
        actual_sha = _hash_file(fs_path(tmp))
        if actual_sha != sha:
            raise OSError(f"file changed while storing blob for {src}")
        os.replace(fs_path(tmp), fs_path(dst))
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(fs_path(tmp))
        if not os.path.isfile(fs_path(dst)):
            raise


def _store_blob_bytes(data: bytes, blobs_dir: Path) -> str:
    sha = hashlib.sha256(data).hexdigest()
    blobs_dir.mkdir(parents=True, exist_ok=True)
    dst = blobs_dir / sha
    if not os.path.isfile(fs_path(dst)):
        tmp = blobs_dir / f".{sha}.{uuid.uuid4().hex}.tmp"
        with open(fs_path(tmp), "wb") as handle:
            handle.write(data)
        try:
            os.replace(fs_path(tmp), fs_path(dst))
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(fs_path(tmp))
            if not os.path.isfile(fs_path(dst)):
                raise
    return sha


def _read_blob(blobs_dir: Path, sha: str) -> bytes:
    with open(fs_path(blobs_dir / sha), "rb") as handle:
        return handle.read()


def _gc_blobs(blobs_dir: Path, keep: set[str]) -> None:
    if not blobs_dir.is_dir():
        return
    for entry in list(blobs_dir.iterdir()):
        if entry.name not in keep:
            with contextlib.suppress(OSError):
                os.unlink(fs_path(entry))


def read_copy_meta(copy_path: Path) -> dict[str, Any] | None:
    """Read ``meta.json`` for a non-git copy at ``copy_path``, or return None."""
    meta_file = copy_meta_dir(copy_path) / _META_FILE
    try:
        raw = json.loads(Path(meta_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("kind") != "copy":
        return None
    return raw


def read_base_manifest(copy_path: Path) -> dict[str, Any] | None:
    """Read ``base_manifest.json`` for a non-git copy at ``copy_path``, or return None."""
    manifest_file = copy_meta_dir(copy_path) / _BASE_MANIFEST_FILE
    try:
        raw = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) and isinstance(raw.get("files"), dict) else None


_COPY_XATTR_NAME = "user.nekopower.copy_token"


def _set_copy_xattr(path: Path, token: str) -> bool:
    """Bind ``token`` to ``path`` via an extended attribute on platforms without birth time; return whether stored."""
    if is_dir_link(path):
        return False
    setxattr = getattr(os, "setxattr", None)
    if setxattr is None:
        return False
    raw = fs_path(path)
    encoded = token.encode("utf-8")
    try:
        try:
            setxattr(raw, _COPY_XATTR_NAME, encoded, follow_symlinks=False)
        except TypeError:
            setxattr(raw, _COPY_XATTR_NAME, encoded)
        return True
    except OSError:
        return False


def _read_copy_xattr_state(path: Path) -> tuple[Literal["ok", "missing", "error"], str | None]:
    """Read the copy token extended attribute, distinguishing absent/invalid attributes from I/O errors."""
    if is_dir_link(path):
        return "missing", None
    getxattr = getattr(os, "getxattr", None)
    if getxattr is None:
        return "error", None
    raw = fs_path(path)
    try:
        try:
            data = bytes(getxattr(raw, _COPY_XATTR_NAME, follow_symlinks=False))
        except TypeError:
            data = bytes(getxattr(raw, _COPY_XATTR_NAME))
    except OSError as exc:
        if exc.errno in _XATTR_MISSING_ERRNOS:
            return "missing", None
        return "error", None
    try:
        return "ok", data.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return "missing", None


def _get_copy_xattr(path: Path) -> str | None:
    """Read the copy token extended attribute on ``path``, or return None."""
    state, value = _read_copy_xattr_state(path)
    return value if state == "ok" else None


def _has_incomplete_rollback(meta_dir: Path) -> bool:
    pending_root = meta_dir / _PENDING_DIR
    if not pending_root.is_dir():
        return False
    for entry in pending_root.iterdir():
        if entry.name.startswith("prep-bak-") and (entry / _ROLLBACK_INCOMPLETE_FILE).is_file():
            return True
    return False


def populate_copy(
    project_dir: Path,
    target: Path,
    *,
    token: str,
    dependency_dirs: tuple[str, ...],
    max_file_bytes: int,
    max_files: int,
    max_total_bytes: int,
    link_dependencies: bool,
    link_max: int,
    identity: dict[str, int] | None,
    cancel_event: threading.Event | None = None,
) -> tuple[str, tuple[str, ...], CopyScan]:
    """Copy ``project_dir`` into ``target``, store its baseline blobs/metadata, and link dependency dirs."""
    _check_cancelled(cancel_event)
    scan = scan_copy_directory(
        project_dir,
        dependency_dirs=dependency_dirs,
        max_file_bytes=max_file_bytes,
        max_files=max_files,
        max_total_bytes=max_total_bytes,
        hash_files=False,
    )
    _check_cancelled(cancel_event)
    meta_dir = copy_meta_dir(target)
    blobs_dir = meta_dir / _BLOBS_DIR
    blobs_dir.mkdir(parents=True, exist_ok=True)

    # Copy all manifest entries into target, then hash the copied bytes themselves
    # so the baseline manifest and stored blobs always match what landed in the copy.
    actual_files: dict[str, dict[str, Any]] = {}
    actual_bytes = 0
    for rel, entry in sorted(scan.files.items()):
        _check_cancelled(cancel_event)
        src = project_dir / rel
        dst = target / rel
        os.makedirs(fs_path(dst.parent), exist_ok=True)
        if entry["kind"] == "symlink":
            try:
                link_target = os.readlink(fs_path(src))
            except OSError:
                continue
            os.symlink(link_target, fs_path(dst))
            actual_files[rel] = {"kind": "symlink", "target": link_target}
            continue
        try:
            src_stat = os.lstat(fs_path(src))
            if not stat.S_ISREG(src_stat.st_mode):
                continue
            shutil.copy2(fs_path(src), fs_path(dst), follow_symlinks=False)
            copied_stat = os.lstat(fs_path(dst))
            copied_sha = _hash_file(fs_path(dst))
        except FileNotFoundError:
            with contextlib.suppress(OSError):
                if os.path.lexists(fs_path(dst)):
                    os.remove(fs_path(dst))
            continue
        actual_bytes += copied_stat.st_size
        if max_total_bytes is not None and actual_bytes > max_total_bytes:
            raise CopyTooLarge(f"{project_dir} exceeds {max_total_bytes} bytes of copyable files")
        _store_blob_file(dst, copied_sha, blobs_dir)
        actual_files[rel] = {
            "kind": "file",
            "sha256": copied_sha,
            "size": copied_stat.st_size,
            "mode": stat.S_IMODE(copied_stat.st_mode),
        }

    actual_digest = manifest_digest(actual_files)
    actual_scan = CopyScan(
        files=actual_files,
        skipped=scan.skipped,
        dependency_dirs=scan.dependency_dirs,
        total_bytes=actual_bytes,
        digest=actual_digest,
    )

    _check_cancelled(cancel_event)
    linked: tuple[str, ...] = ()
    if link_dependencies and scan.dependency_dirs:
        # Ensure parent dirs exist for nested dependency directories (e.g. frontend/app/node_modules)
        # only when the parent directory existed in the project and has copied files (or create if project has it).
        for rel in scan.dependency_dirs:
            parent = (target / rel).parent
            if not os.path.lexists(fs_path(parent)):
                # Only link nested deps if their parent exists in the copied tree.
                if (project_dir / rel).parent.is_dir():
                    os.makedirs(fs_path(parent), exist_ok=True)
        linked = link_dependency_paths(project_dir, target, scan.dependency_dirs, limit=link_max)

    _atomic_write_json(
        meta_dir / _BASE_MANIFEST_FILE,
        {"digest": actual_digest, "files": actual_files, "skipped": list(scan.skipped)},
    )
    meta_payload: dict[str, Any] = {
        "version": 1,
        "kind": "copy",
        "id": target.name,
        "project_dir": os.path.abspath(str(project_dir)),
        "path": os.path.abspath(str(target)),
        "token": token,
        "start": actual_digest,
        "links": list(linked),
    }
    if identity:
        meta_payload["identity"] = identity
    elif _set_copy_xattr(target, token):
        meta_payload["xattr"] = True
    with contextlib.suppress(OSError):
        st = os.lstat(fs_path(target))
        if st.st_ino:
            meta_payload["stat"] = {"dev": st.st_dev, "ino": st.st_ino}
    _atomic_write_json(meta_dir / _META_FILE, meta_payload)
    return actual_digest, linked, actual_scan


def copy_has_changes(
    copy_path: Path,
    *,
    dependency_dirs: tuple[str, ...],
    max_file_bytes: int,
) -> bool:
    """Return whether a non-git working copy differs from its stored baseline."""
    if not copy_path.is_dir():
        return False
    meta = read_copy_meta(copy_path)
    base = read_base_manifest(copy_path)
    if meta is None or base is None:
        return True
    links = active_links(copy_path, meta.get("links") or ())
    base_files = base.get("files") or {}
    try:
        now = scan_copy_directory(
            copy_path,
            dependency_dirs=dependency_dirs,
            max_file_bytes=max_file_bytes,
            active_dep_links=links,
            include_large=base_files.keys(),
            hash_files=True,
            discover_dependencies=False,
        )
    except OSError:
        # Unreadable entries inside the copy cannot be verified unchanged; treat as changed so
        # cleanup/reconcile never deletes a copy whose files could not be read.
        return True
    # Any skipped item inside the copy (oversized file, nested repo, special file) was created
    # there and was never carried over to the project, so it always counts as unlanded changes.
    return bool(now.skipped) or now.digest != str(base.get("digest") or "")


def _parents(rel: str) -> list[str]:
    parts = rel.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def has_link_ancestor(root: Path, rel: str) -> bool:
    """Return True if any ancestor directory of ``rel`` inside ``root`` is a symlink or directory junction."""
    for up in _parents(rel):
        candidate = root / up
        raw = fs_path(candidate)
        if os.path.islink(raw) or is_dir_link(candidate):
            return True
    return False


def verify_project_unchanged(
    project_dir: Path, expected_current: Mapping[str, Mapping[str, Any] | None]
) -> list[str]:
    """Return relative paths whose on-disk state in ``project_dir`` changed since merge calculation (or has a link ancestor)."""
    changed: list[str] = []
    for rel, expected in expected_current.items():
        if has_link_ancestor(project_dir, rel):
            changed.append(rel)
            continue
        target = fs_path(project_dir / rel)
        if expected is None:
            if os.path.lexists(target):
                changed.append(rel)
            continue
        if expected.get("kind") == "replaced_dir":
            if not os.path.isdir(target) or is_dir_link(project_dir / rel):
                changed.append(rel)
                continue
            allowed = set(expected.get("children") or ())
            try:
                def _raise_dir_err(err: OSError) -> None:
                    raise err

                root_fs = fs_path(project_dir)
                dir_drifted = False
                for cur_dir, subdirs, subfiles in os.walk(
                    target, topdown=True, followlinks=False, onerror=_raise_dir_err
                ):
                    for dname in subdirs:
                        if is_dir_link(os.path.join(cur_dir, dname)):
                            dir_drifted = True
                            break
                    if dir_drifted:
                        break
                    rel_cur = os.path.relpath(cur_dir, root_fs).replace(os.sep, "/")
                    for fname in subfiles:
                        child_rel = f"{rel_cur}/{fname}"
                        if child_rel not in allowed:
                            dir_drifted = True
                            break
                    if dir_drifted:
                        break
                if dir_drifted:
                    changed.append(rel)
            except OSError:
                changed.append(rel)
            continue
        if expected.get("kind") == "symlink":
            if not os.path.islink(target):
                changed.append(rel)
                continue
            try:
                if os.readlink(target) != expected.get("target"):
                    changed.append(rel)
            except OSError:
                changed.append(rel)
            continue
        if not os.path.lexists(target) or os.path.islink(target):
            changed.append(rel)
            continue
        try:
            st = os.lstat(target)
            if not stat.S_ISREG(st.st_mode) or st.st_size != int(expected.get("size", -1)):
                changed.append(rel)
                continue
            if _hash_file(target) != str(expected.get("sha256", "")):
                changed.append(rel)
            elif (
                sys.platform != "win32"
                and "mode" in expected
                and bool(st.st_mode & stat.S_IXUSR) != bool(int(expected["mode"]) & stat.S_IXUSR)
            ):
                changed.append(rel)
        except OSError:
            changed.append(rel)
    return changed


def _path_type_conflicts(paths: Iterable[str]) -> set[str]:
    """Return paths that clash because one path is a strict prefix (directory) of another (file)."""
    path_set = set(paths)
    clashed: set[str] = set()
    for rel in path_set:
        for up in _parents(rel):
            if up in path_set:
                clashed.add(rel)
                clashed.add(up)
    return clashed


def _read_entry_bytes(root: Path, entry: Mapping[str, Any] | None, rel: str, blobs_dir: Path | None = None) -> bytes | None:
    if entry is None:
        return None
    if entry.get("kind") == "symlink":
        return None
    if blobs_dir is not None:
        return _read_blob(blobs_dir, str(entry["sha256"]))
    with open(fs_path(root / rel), "rb") as handle:
        return handle.read()


def compute_copy_merge(
    project_dir: Path,
    copy_path: Path,
    *,
    dependency_dirs: tuple[str, ...],
    max_file_bytes: int,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Compute a 3-way merge of ``(base, current_project, copy_result)`` without touching ``project_dir``."""
    _check_cancelled(cancel_event)
    meta = read_copy_meta(copy_path)
    base = read_base_manifest(copy_path)
    if meta is None or base is None:
        return {"status": "failed", "detail": f"{copy_path} has no working-copy baseline metadata"}

    meta_dir = copy_meta_dir(copy_path)
    if _has_incomplete_rollback(meta_dir):
        return {
            "status": "failed",
            "detail": f"working copy {copy_path} has an incomplete rollback backup under {meta_dir / _PENDING_DIR}; recover or discard it before landing",
        }
    blobs_dir = meta_dir / _BLOBS_DIR
    base_files: dict[str, dict[str, Any]] = dict(base.get("files") or {})
    base_digest = str(base.get("digest") or "")
    meta_start = str(meta.get("start") or "")
    if not meta_start or meta_start != base_digest:
        if base_digest and base.get("source") == "landed":
            # Self-heal a crash between base_manifest.json and meta.json inside advance_copy_baseline.
            meta["start"] = base_digest
            try:
                _atomic_write_json(meta_dir / _META_FILE, meta)
            except OSError as exc:
                return {
                    "status": "failed",
                    "detail": f"working copy {copy_path} baseline metadata repair failed: {exc}",
                }
        else:
            return {
                "status": "failed",
                "detail": (
                    f"working copy {copy_path} baseline metadata is inconsistent "
                    "(meta.start != base_manifest.digest); recover or discard it before landing"
                ),
            }
    links = active_links(copy_path, meta.get("links") or ())

    try:
        result_scan = scan_copy_directory(
            copy_path,
            dependency_dirs=dependency_dirs,
            max_file_bytes=max_file_bytes,
            active_dep_links=links,
            include_large=base_files.keys(),
            hash_files=True,
            discover_dependencies=False,
        )
        _check_cancelled(cancel_event)
        current_scan = scan_copy_directory(
            project_dir,
            dependency_dirs=dependency_dirs,
            max_file_bytes=max_file_bytes,
            include_large=base_files.keys(),
            hash_files=True,
        )
    except OSError as exc:
        return {"status": "failed", "detail": f"reading directory failed: {exc}"}
    _check_cancelled(cancel_event)

    all_paths = sorted(set(base_files) | set(current_scan.files) | set(result_scan.files))
    merged_entries: dict[str, dict[str, Any]] = {}
    merged_contents: dict[str, bytes] = {}
    conflicts: set[str] = set()
    conflict_versions: dict[str, dict[str, Any]] = {}
    changes: list[tuple[str, Literal["added", "modified", "deleted"], int | None, int | None]] = []

    blocked_current = {name.rstrip("/") for name in current_scan.skipped} | set(current_scan.dependency_dirs)

    def _collides_with_blocked(rel_path: str) -> bool:
        if not blocked_current:
            return False
        if rel_path in blocked_current:
            return True
        if any(up in blocked_current for up in _parents(rel_path)):
            return True
        prefix = rel_path + "/"
        return any(item.startswith(prefix) for item in blocked_current)

    for rel in all_paths:
        _check_cancelled(cancel_event)
        b_ent = base_files.get(rel)
        c_ent = current_scan.files.get(rel)
        w_ent = result_scan.files.get(rel)

        # Fast-path equality using manifest entries (kind + sha256/target + executable bit).
        def _same_entry(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
            if left is None or right is None:
                return left is right
            if left.get("kind") != right.get("kind"):
                return False
            if left.get("kind") == "symlink":
                return left.get("target") == right.get("target")
            if left.get("sha256") != right.get("sha256"):
                return False
            if sys.platform != "win32":
                return bool(int(left.get("mode", 0)) & stat.S_IXUSR) == bool(int(right.get("mode", 0)) & stat.S_IXUSR)
            return True

        if _same_entry(w_ent, b_ent):
            if c_ent is not None:
                merged_entries[rel] = dict(c_ent)
            continue

        if _collides_with_blocked(rel) or has_link_ancestor(project_dir, rel):
            # Project holds a skipped file (oversized, special file, nested repo, dependency dir)
            # or a link/junction ancestor at or overlapping this path: never overwrite or traverse it.
            conflicts.add(rel)
            if w_ent is not None:
                merged_entries[rel] = dict(w_ent)
            conflict_versions[rel] = {
                "project": dict(c_ent) if c_ent is not None else None,
                "worktree": dict(w_ent) if w_ent is not None else None,
                "project_skipped": True,
            }
            continue

        if _same_entry(c_ent, b_ent):
            if w_ent is None:
                # Clean deletion.
                if c_ent is not None:
                    old_b = _read_entry_bytes(project_dir, c_ent, rel)
                    added, deleted = line_diff_counts(old_b, b"") if c_ent.get("kind") != "symlink" else (None, None)
                    changes.append((rel, "deleted", added, deleted))
            else:
                merged_entries[rel] = dict(w_ent)
                if w_ent.get("kind") != "symlink":
                    new_b = _read_entry_bytes(copy_path, w_ent, rel)
                    assert new_b is not None
                    merged_contents[rel] = new_b
                    old_b = _read_entry_bytes(project_dir, c_ent, rel) if c_ent is not None else b""
                    added, deleted = line_diff_counts(old_b, new_b)
                else:
                    added, deleted = None, None
                status: Literal["added", "modified", "deleted"] = "added" if c_ent is None else "modified"
                changes.append((rel, status, added, deleted))
            continue

        if _same_entry(c_ent, w_ent):
            if c_ent is not None:
                merged_entries[rel] = dict(c_ent)
            continue

        # Divergent changes on both sides.
        if (
            (b_ent is not None and b_ent.get("kind") == "symlink")
            or (c_ent is not None and c_ent.get("kind") == "symlink")
            or (w_ent is not None and w_ent.get("kind") == "symlink")
        ):
            conflicts.add(rel)
            # Keep the worktree's version in place (if present) and preserve both sides in conflict metadata.
            chosen = w_ent if w_ent is not None else c_ent
            if chosen is not None:
                merged_entries[rel] = dict(chosen)
            conflict_versions[rel] = {
                "project": dict(c_ent) if c_ent is not None else None,
                "worktree": dict(w_ent) if w_ent is not None else None,
            }
            continue

        if c_ent is None or w_ent is None:
            # Modify/delete clash (including POSIX executable-bit-only edits vs deletion):
            # handle on full manifest entries before byte-level merge3_bytes.
            conflicts.add(rel)
            chosen = w_ent if w_ent is not None else c_ent
            if chosen is not None:
                merged_entries[rel] = dict(chosen)
                chosen_bytes = _read_entry_bytes(copy_path if w_ent is not None else project_dir, chosen, rel)
                if chosen_bytes is not None:
                    merged_contents[rel] = chosen_bytes
            conflict_versions[rel] = {
                "project": dict(c_ent) if c_ent is not None else None,
                "worktree": dict(w_ent) if w_ent is not None else None,
            }
            continue

        b_bytes = _read_entry_bytes(project_dir, b_ent, rel, blobs_dir=blobs_dir) if b_ent is not None else None
        c_bytes = _read_entry_bytes(project_dir, c_ent, rel) if c_ent is not None else None
        w_bytes = _read_entry_bytes(copy_path, w_ent, rel) if w_ent is not None else None
        merged_b, clashed = merge3_bytes(b_bytes, c_bytes, w_bytes)
        if clashed:
            conflicts.add(rel)
            if (
                c_bytes is None
                or w_bytes is None
                or (b_bytes is not None and is_binary_bytes(b_bytes))
                or is_binary_bytes(c_bytes)
                or is_binary_bytes(w_bytes)
                or merged_b == w_bytes
            ):
                conflict_versions[rel] = {
                    "project": dict(c_ent) if c_ent is not None else None,
                    "worktree": dict(w_ent) if w_ent is not None else None,
                }
        if merged_b is not None:
            sha = hashlib.sha256(merged_b).hexdigest()
            if sys.platform != "win32" and b_ent is not None and c_ent is not None and w_ent is not None:
                b_exec = bool(int(b_ent.get("mode", 0)) & stat.S_IXUSR)
                c_exec = bool(int(c_ent.get("mode", 0)) & stat.S_IXUSR)
                w_exec = bool(int(w_ent.get("mode", 0)) & stat.S_IXUSR)
                if w_exec == b_exec and c_exec != b_exec:
                    mode = int(c_ent.get("mode", 0o644))
                else:
                    mode = int(w_ent.get("mode", 0o644))
            else:
                mode = int((w_ent or c_ent or b_ent or {}).get("mode", 0o644))
            merged_entries[rel] = {"kind": "file", "sha256": sha, "size": len(merged_b), "mode": mode}
            merged_contents[rel] = merged_b
            if not clashed:
                old_b = c_bytes if c_bytes is not None else b""
                added, deleted = line_diff_counts(old_b, merged_b)
                changes.append((rel, "added" if c_ent is None else "modified", added, deleted))

    # File vs directory clashes across paths.
    type_clashes = _path_type_conflicts(merged_entries.keys())
    if type_clashes:
        conflicts.update(type_clashes)

    new_skipped = tuple(sorted(result_scan.skipped))

    if conflicts:
        _check_cancelled(cancel_event)
        pending_root = meta_dir / _PENDING_DIR
        # Prune stale pending snapshots from earlier conflict attempts so repeated landing
        # retries never accumulate unbounded copies of project blobs on disk.
        if pending_root.is_dir():
            for old_entry in list(pending_root.iterdir()):
                if not old_entry.name.startswith("prep-bak-"):
                    with contextlib.suppress(OSError):
                        remove_tree_safe(old_entry)

        merge_id = uuid.uuid4().hex
        pending_dir = pending_root / merge_id
        pending_blobs = pending_dir / _BLOBS_DIR
        pending_blobs.mkdir(parents=True, exist_ok=True)
        try:
            def _store_if_new(src: Path, sha: str) -> None:
                _check_cancelled(cancel_event)
                if os.path.isfile(fs_path(blobs_dir / sha)):
                    return
                _store_blob_file(src, sha, pending_blobs)

            def _store_bytes_if_new(data: bytes) -> str:
                _check_cancelled(cancel_event)
                sha = hashlib.sha256(data).hexdigest()
                if os.path.isfile(fs_path(blobs_dir / sha)):
                    return sha
                return _store_blob_bytes(data, pending_blobs)

            # Store only current project blobs not already present in the baseline blob store.
            for rel, entry in current_scan.files.items():
                if entry.get("kind") == "file":
                    _store_if_new(project_dir / rel, str(entry["sha256"]))
            # Also store worktree blobs for conflicted entries so both sides survive prepare_conflicts.
            for rel, versions in conflict_versions.items():
                wt_entry = versions.get("worktree")
                if isinstance(wt_entry, dict) and wt_entry.get("kind") == "file":
                    src = copy_path / rel
                    if os.path.isfile(fs_path(src)):
                        _store_if_new(src, str(wt_entry["sha256"]))
            # Store merged file contents (with conflict markers where applicable).
            merged_manifest_entries: dict[str, dict[str, Any]] = {}
            for rel, entry in merged_entries.items():
                _check_cancelled(cancel_event)
                if rel in merged_contents:
                    sha = _store_bytes_if_new(merged_contents[rel])
                    merged_manifest_entries[rel] = {**entry, "sha256": sha, "size": len(merged_contents[rel])}
                elif entry.get("kind") == "file":
                    sha = str(entry["sha256"])
                    if not os.path.isfile(fs_path(blobs_dir / sha)):
                        src = (
                            copy_path / rel
                            if rel in result_scan.files and result_scan.files[rel].get("sha256") == sha
                            else project_dir / rel
                        )
                        if os.path.isfile(fs_path(src)):
                            _store_blob_file(src, sha, pending_blobs)
                    merged_manifest_entries[rel] = dict(entry)
                else:
                    merged_manifest_entries[rel] = dict(entry)
            _check_cancelled(cancel_event)
            _atomic_write_json(
                pending_dir / "pending.json",
                {
                    "base": base_digest,
                    "current": current_scan.digest,
                    "current_files": current_scan.files,
                    "current_skipped": list(current_scan.skipped),
                    "result": result_scan.digest,
                    "result_files": result_scan.files,
                    "merged_files": merged_manifest_entries,
                    "conflicts": sorted(conflicts),
                    "conflict_versions": conflict_versions,
                },
            )
        except OSError as exc:
            with contextlib.suppress(OSError):
                remove_tree_safe(pending_dir)
            return {"status": "failed", "detail": f"files changed while recording conflict snapshot: {exc}; merge again"}
        except BaseException:
            with contextlib.suppress(OSError):
                remove_tree_safe(pending_dir)
            raise
        return {
            "status": "conflict",
            "conflicts": tuple(sorted(conflicts)),
            "skipped": new_skipped,
            "base": base_digest,
            "current": current_scan.digest,
            "result": result_scan.digest,
            "result_tree": result_scan.digest,
            "merged_tree": merge_id,
        }

    deleted_paths = {rel for rel, kind, _a, _d in changes if kind == "deleted"}
    replaced_dirs = {up for rel in deleted_paths for up in _parents(rel)}
    remaining_parents = {up for rel in merged_entries for up in _parents(rel)}
    expected_current: dict[str, Any] = {}
    for rel, kind, _a, _d in changes:
        cur = current_scan.files.get(rel)
        if cur is None and kind == "added" and rel in replaced_dirs and rel not in remaining_parents:
            prefix = rel + "/"
            expected_current[rel] = {
                "kind": "replaced_dir",
                "children": sorted(p for p in current_scan.files if p.startswith(prefix)),
            }
        else:
            expected_current[rel] = cur
    return {
        "status": "ready" if changes else "unchanged",
        "changes": tuple(changes),
        "merged_entries": merged_entries,
        "merged_contents": merged_contents,
        "expected_current": expected_current,
        "skipped": new_skipped,
        "base": base_digest,
        "current": current_scan.digest,
        "result": result_scan.digest,
        "result_tree": result_scan.digest,
        "merged_tree": manifest_digest(merged_entries),
        "result_scan": result_scan,
    }


def apply_copy_changes(
    project_dir: Path,
    changes: Iterable[tuple[str, Literal["added", "modified", "deleted"], int | None, int | None]],
    merged_entries: Mapping[str, Mapping[str, Any]],
    merged_contents: Mapping[str, bytes],
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    """Write merged file changes into ``project_dir``."""
    deletes = [rel for rel, kind, _a, _d in changes if kind == "deleted"]
    writes = [(rel, kind) for rel, kind, _a, _d in changes if kind != "deleted"]

    for rel in deletes:
        _check_cancelled(cancel_event)
        if has_link_ancestor(project_dir, rel):
            raise OSError(f"refusing to delete through directory link: {rel}")
        target = fs_path(project_dir / rel)
        if os.path.islink(target) or os.path.isfile(target):
            os.remove(target)
        # Prune newly emptied directories inside project_dir.
        parent = (project_dir / rel).parent
        while parent != project_dir and relative_posix(parent, project_dir):
            try:
                os.rmdir(fs_path(parent))
            except OSError:
                break
            parent = parent.parent

    for rel, _kind in writes:
        _check_cancelled(cancel_event)
        if has_link_ancestor(project_dir, rel):
            raise OSError(f"refusing to write through directory link: {rel}")
        entry = merged_entries[rel]
        target_path = project_dir / rel
        target = fs_path(target_path)
        os.makedirs(fs_path(target_path.parent), exist_ok=True)
        if entry.get("kind") == "symlink":
            if os.path.lexists(target):
                if os.path.isdir(target) and not is_dir_link(target_path):
                    # Only remove an empty directory; never wipe a non-empty user directory.
                    os.rmdir(target)
                elif is_dir_link(target_path):
                    remove_dir_link(target_path)
                else:
                    os.remove(target)
            os.symlink(str(entry["target"]), target)
            continue
        if os.path.lexists(target) and (os.path.islink(target) or os.path.isdir(target)):
            if os.path.isdir(target) and not is_dir_link(target_path):
                # Only remove an empty directory; never wipe a non-empty user directory.
                os.rmdir(target)
            elif is_dir_link(target_path):
                remove_dir_link(target_path)
            else:
                os.remove(target)
        data = merged_contents[rel]
        with open(target, "wb") as handle:
            handle.write(data)
        if sys.platform != "win32" and "mode" in entry:
            with contextlib.suppress(OSError):
                os.chmod(target, int(entry["mode"]))


def verify_copy_changes(
    project_dir: Path,
    changes: Iterable[tuple[str, Literal["added", "modified", "deleted"], int | None, int | None]],
    merged_entries: Mapping[str, Mapping[str, Any]],
    merged_contents: Mapping[str, bytes],
) -> bool:
    """Verify every changed file in ``project_dir`` reads back as merged."""
    for rel, kind, _a, _d in changes:
        target = fs_path(project_dir / rel)
        if kind == "deleted":
            if os.path.islink(target) or os.path.isfile(target):
                return False
            continue
        entry = merged_entries[rel]
        if entry.get("kind") == "symlink":
            if not os.path.islink(target) or os.readlink(target) != entry["target"]:
                return False
            continue
        if not os.path.isfile(target) or os.path.islink(target):
            return False
        try:
            with open(target, "rb") as handle:
                actual = handle.read()
        except OSError:
            return False
        if actual != merged_contents[rel]:
            return False
        if sys.platform != "win32" and "mode" in entry:
            try:
                actual_exec = bool(os.lstat(target).st_mode & stat.S_IXUSR)
            except OSError:
                return False
            if actual_exec != bool(int(entry["mode"]) & stat.S_IXUSR):
                return False
    return True


def advance_copy_baseline(copy_path: Path, new_scan: CopyScan) -> None:
    """Update a non-git copy's stored baseline to ``new_scan`` and prune unreferenced blobs."""
    meta = read_copy_meta(copy_path)
    if meta is None:
        return
    meta_dir = copy_meta_dir(copy_path)
    blobs_dir = meta_dir / _BLOBS_DIR
    blobs_dir.mkdir(parents=True, exist_ok=True)
    keep: set[str] = set()
    for rel, entry in new_scan.files.items():
        if entry.get("kind") == "file":
            sha = str(entry["sha256"])
            keep.add(sha)
            src = copy_path / rel
            if os.path.isfile(fs_path(src)):
                _store_blob_file(src, sha, blobs_dir)
    prev_base_file = meta_dir / _BASE_MANIFEST_FILE
    old_base_bytes = prev_base_file.read_bytes() if prev_base_file.is_file() else None
    prev_base = read_base_manifest(copy_path)
    prev_skipped = list(prev_base.get("skipped") or ()) if prev_base is not None else []
    _atomic_write_json(
        prev_base_file,
        {"digest": new_scan.digest, "files": new_scan.files, "skipped": prev_skipped, "source": "landed"},
    )
    meta["start"] = new_scan.digest
    try:
        _atomic_write_json(meta_dir / _META_FILE, meta)
    except BaseException:
        if old_base_bytes is not None:
            with contextlib.suppress(OSError):
                _atomic_write_bytes(prev_base_file, old_base_bytes)
        raise
    _gc_blobs(blobs_dir, keep)
    pending_root = meta_dir / _PENDING_DIR
    if pending_root.is_dir():
        remove_tree_safe(pending_root)
    conflicts_root = meta_dir / _CONFLICTS_DIR
    if conflicts_root.is_dir():
        remove_tree_safe(conflicts_root)


def prepare_copy_conflicts_sync(
    copy_path: Path,
    *,
    expected_base: str,
    expected_result: str,
    merge_id: str,
    dependency_dirs: tuple[str, ...],
    max_file_bytes: int,
    cancel_event: threading.Event | None = None,
) -> tuple[str, list[str]]:
    """Write conflict-marked files into ``copy_path`` and advance its baseline to the project snapshot.

    Returns ``(new_start_digest, conflicts)``. Raises ``ValueError`` if stale or unsafe.
    """
    _check_cancelled(cancel_event)
    meta = read_copy_meta(copy_path)
    base = read_base_manifest(copy_path)
    if meta is None or base is None:
        raise ValueError(f"{copy_path} has no working-copy baseline metadata")
    if str(meta.get("start") or "") != expected_base:
        raise ValueError("the worktree landed or was prepared since this merge was tried; merge again")
    meta_dir = copy_meta_dir(copy_path)
    if _has_incomplete_rollback(meta_dir):
        raise ValueError(
            f"working copy {copy_path} has an incomplete rollback backup under {meta_dir / _PENDING_DIR}; recover or discard it"
        )
    pending_dir = meta_dir / _PENDING_DIR / merge_id
    pending_file = pending_dir / "pending.json"
    try:
        pending = json.loads(pending_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("the conflict snapshot is no longer available; merge again") from exc

    links = active_links(copy_path, meta.get("links") or ())
    base_files = base.get("files") or {}
    try:
        now = scan_copy_directory(
            copy_path,
            dependency_dirs=dependency_dirs,
            max_file_bytes=max_file_bytes,
            active_dep_links=links,
            include_large=base_files.keys(),
            hash_files=True,
            discover_dependencies=False,
        )
    except OSError as exc:
        raise ValueError(f"reading the working copy failed: {exc}") from exc
    if now.digest != expected_result:
        raise ValueError("the worktree changed after the merge was tried; merge again")

    pending_blobs = pending_dir / _BLOBS_DIR
    merged_files: dict[str, dict[str, Any]] = pending["merged_files"]
    held = set(now.files)
    to_delete = sorted(held - set(merged_files))
    merged_keys = set(merged_files)
    merged_parents = {up for other in merged_keys for up in _parents(other)}
    to_write = [(rel, entry) for rel, entry in sorted(merged_files.items()) if rel not in merged_parents]
    touched_set = sorted(set(to_delete) | {rel for rel, _e in to_write})
    for rel in touched_set:
        if has_link_ancestor(copy_path, rel):
            raise ValueError(f"preparing conflicts would traverse a directory link in the working copy: {rel}")
    # Check that writing new files will not clobber skipped files or nested repos in the copy.
    skipped_set = {name.rstrip("/") for name in now.skipped}
    for rel in sorted(set(merged_files) - held):
        if rel in skipped_set or any(up in skipped_set for up in _parents(rel)):
            raise ValueError(f"preparing would overwrite skipped path in the working copy: {rel}")

    # Back up every copy path we are about to delete or overwrite plus the old metadata on disk,
    # and persist the transaction marker BEFORE the first mutation so even a crash or triple-fault
    # during rollback still blocks subsequent merges until recovered or discarded.
    old_base_bytes = (meta_dir / _BASE_MANIFEST_FILE).read_bytes()
    old_meta_bytes = (meta_dir / _META_FILE).read_bytes()
    prep_backup_dir = meta_dir / _PENDING_DIR / f"prep-bak-{uuid.uuid4().hex}"
    prep_backup_dir.mkdir(parents=True, exist_ok=True)
    saved_entries: list[tuple[str, str, str | None]] = []
    mutated_copy = False
    mutated_base = False
    mutated_meta = False
    rollback_failed = False
    # (rel, kind: 'absent' | 'file' | 'symlink', payload: backup filename or link target)
    try:
        _atomic_write_bytes(prep_backup_dir / "old_base_manifest.json", old_base_bytes)
        _atomic_write_bytes(prep_backup_dir / "old_meta.json", old_meta_bytes)
        for idx, rel in enumerate(touched_set):
            src_path = copy_path / rel
            src = fs_path(src_path)
            if not os.path.lexists(src):
                saved_entries.append((rel, "absent", None))
            elif os.path.islink(src):
                saved_entries.append((rel, "symlink", os.readlink(src)))
            elif os.path.isfile(src):
                bak_name = f"b{idx}"
                shutil.copy2(src, fs_path(prep_backup_dir / bak_name), follow_symlinks=False)
                saved_entries.append((rel, "file", bak_name))
        _atomic_write_json(
            prep_backup_dir / _ROLLBACK_INCOMPLETE_FILE,
            {
                "status": "in_progress",
                "merge_id": merge_id,
                "copy_path": str(copy_path),
                "entries": [{"rel": rel, "kind": kind, "payload": payload} for rel, kind, payload in saved_entries],
            },
        )
        mutated_copy = True

        # Remove files deleted by the merge.
        for rel in to_delete:
            _check_cancelled(cancel_event)
            if has_link_ancestor(copy_path, rel):
                raise ValueError(f"refusing to delete through directory link in the working copy: {rel}")
            target = fs_path(copy_path / rel)
            if os.path.islink(target) or os.path.isfile(target):
                os.remove(target)

        # Write merged / conflict-marked files into the copy.
        for rel, entry in to_write:
            _check_cancelled(cancel_event)
            if has_link_ancestor(copy_path, rel):
                raise ValueError(f"refusing to write through directory link in the working copy: {rel}")
            target_path = copy_path / rel
            target = fs_path(target_path)
            os.makedirs(fs_path(target_path.parent), exist_ok=True)
            if entry.get("kind") == "symlink":
                if os.path.lexists(target):
                    if os.path.isdir(target) and not is_dir_link(target_path):
                        os.rmdir(target)
                    elif is_dir_link(target_path):
                        remove_dir_link(target_path)
                    else:
                        os.remove(target)
                os.symlink(str(entry["target"]), target)
                continue
            sha = str(entry["sha256"])
            blob = pending_blobs / sha
            if not os.path.isfile(fs_path(blob)):
                blob = meta_dir / _BLOBS_DIR / sha
            if not os.path.isfile(fs_path(blob)):
                continue
            if os.path.lexists(target) and (os.path.islink(target) or os.path.isdir(target)):
                if os.path.isdir(target) and not is_dir_link(target_path):
                    # Only remove an empty directory; never wipe a non-empty user directory.
                    os.rmdir(target)
                elif is_dir_link(target_path):
                    remove_dir_link(target_path)
                else:
                    os.remove(target)
            shutil.copyfile(fs_path(blob), target)
            if sys.platform != "win32" and "mode" in entry:
                with contextlib.suppress(OSError):
                    os.chmod(target, int(entry["mode"]))

        # Advance baseline to current project snapshot and preserve both sides of binary/symlink conflicts.
        blobs_dir = meta_dir / _BLOBS_DIR
        blobs_dir.mkdir(parents=True, exist_ok=True)
        current_files: dict[str, dict[str, Any]] = pending["current_files"]
        keep: set[str] = set()
        for _rel, entry in current_files.items():
            if entry.get("kind") == "file":
                sha = str(entry["sha256"])
                keep.add(sha)
                src_blob = pending_blobs / sha
                if os.path.isfile(fs_path(src_blob)):
                    _store_blob_file(src_blob, sha, blobs_dir)
        conflict_versions = pending.get("conflict_versions") or {}
        if conflict_versions:
            saved_dir = meta_dir / _CONFLICTS_DIR / merge_id
            for side_map in conflict_versions.values():
                if not isinstance(side_map, dict):
                    continue
                for side_key in ("project", "worktree"):
                    side_entry = side_map.get(side_key)
                    if isinstance(side_entry, dict) and side_entry.get("kind") == "file":
                        sha = str(side_entry["sha256"])
                        keep.add(sha)
                        src_blob = pending_blobs / sha
                        if os.path.isfile(fs_path(src_blob)):
                            _store_blob_file(src_blob, sha, blobs_dir)
            _atomic_write_json(saved_dir / "versions.json", conflict_versions)
        new_start = str(pending["current"])
        mutated_base = True
        _atomic_write_json(
            meta_dir / _BASE_MANIFEST_FILE,
            {"digest": new_start, "files": current_files, "skipped": list(pending.get("current_skipped") or ())},
        )
        meta["start"] = new_start
        mutated_meta = True
        _atomic_write_json(meta_dir / _META_FILE, meta)
    except BaseException as exc:
        # Roll back partial copy mutations AND baseline metadata so copy files and baseline stay in sync.
        # Two-phase order: first undo newly added paths deepest-first (removing child files and empty
        # replacement directories), then restore original files/symlinks that may share those paths.
        rollback_order = (
            sorted(
                (item for item in saved_entries if item[1] == "absent"),
                key=lambda item: item[0].count("/"),
                reverse=True,
            )
            + [item for item in saved_entries if item[1] != "absent"]
            if mutated_copy
            else []
        )
        for rel, orig_kind, payload in rollback_order:
            dst_path = copy_path / rel
            dst = fs_path(dst_path)
            try:
                if orig_kind == "absent":
                    if os.path.islink(dst) or os.path.isfile(dst):
                        os.remove(dst)
                    parent = dst_path.parent
                    while parent != copy_path and relative_posix(parent, copy_path):
                        try:
                            os.rmdir(fs_path(parent))
                        except OSError:
                            break
                        parent = parent.parent
                elif orig_kind == "symlink" and payload is not None:
                    os.makedirs(fs_path(dst_path.parent), exist_ok=True)
                    if os.path.lexists(dst):
                        if is_dir_link(dst_path):
                            remove_dir_link(dst_path)
                        elif os.path.isdir(dst):
                            os.rmdir(dst)
                        else:
                            os.remove(dst)
                    os.symlink(payload, dst)
                elif orig_kind == "file" and payload is not None:
                    os.makedirs(fs_path(dst_path.parent), exist_ok=True)
                    if os.path.lexists(dst) and (os.path.islink(dst) or os.path.isdir(dst)):
                        if is_dir_link(dst_path):
                            remove_dir_link(dst_path)
                        elif os.path.isdir(dst):
                            os.rmdir(dst)
                        else:
                            os.remove(dst)
                    shutil.copy2(fs_path(prep_backup_dir / payload), dst, follow_symlinks=False)
            except OSError as rb_err:
                rollback_failed = True
                exc.add_note(f"rollback failed to restore {rel}: {rb_err}")
        if mutated_base:
            try:
                _atomic_write_bytes(meta_dir / _BASE_MANIFEST_FILE, old_base_bytes)
            except OSError as rb_err:
                rollback_failed = True
                exc.add_note(f"rollback failed to restore {_BASE_MANIFEST_FILE}: {rb_err}")
        if mutated_meta:
            try:
                _atomic_write_bytes(meta_dir / _META_FILE, old_meta_bytes)
            except OSError as rb_err:
                rollback_failed = True
                exc.add_note(f"rollback failed to restore {_META_FILE}: {rb_err}")
        if rollback_failed:
            with contextlib.suppress(OSError):
                _atomic_write_json(
                    prep_backup_dir / _ROLLBACK_INCOMPLETE_FILE,
                    {
                        "status": "rollback_failed",
                        "merge_id": merge_id,
                        "copy_path": str(copy_path),
                        "entries": [
                            {"rel": rel, "kind": kind, "payload": payload} for rel, kind, payload in saved_entries
                        ],
                    },
                )
        raise
    finally:
        if not rollback_failed:
            marker = prep_backup_dir / _ROLLBACK_INCOMPLETE_FILE
            with contextlib.suppress(OSError):
                if os.path.lexists(fs_path(marker)):
                    os.unlink(fs_path(marker))
            with contextlib.suppress(OSError):
                if prep_backup_dir.exists():
                    remove_tree_safe(prep_backup_dir)
    # Post-commit cleanup: never roll back committed copy files/baseline if temp cleanup fails.
    with contextlib.suppress(OSError):
        _gc_blobs(blobs_dir, keep)
    with contextlib.suppress(OSError):
        remove_tree_safe(pending_dir)
    return new_start, list(pending.get("conflicts") or ())


def list_copy_metas_for_project(project_dir: Path, base_dir: Path) -> list[tuple[str, Path, dict[str, Any]]]:
    """Return ``(worktree_id, copy_path, meta)`` for non-git copies under ``base_dir`` belonging to ``project_dir``."""
    meta_root = copy_meta_root(base_dir)
    if not meta_root.is_dir():
        return []
    found: list[tuple[str, Path, dict[str, Any]]] = []
    for entry in sorted(meta_root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        meta_file = entry / _META_FILE
        try:
            raw = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict) or raw.get("kind") != "copy":
            continue
        recorded_project = raw.get("project_dir")
        if isinstance(recorded_project, str) and same_path(recorded_project, project_dir):
            found.append((entry.name, base_dir / entry.name, raw))
    return found


def copy_matches_identity(
    copy_path: Path,
    meta: Mapping[str, Any],
    now_identity: Mapping[str, int] | None,
) -> Literal["ours", "theirs", "unknown"]:
    """Return whether ``copy_path`` is the directory recorded in ``meta``."""
    raw = fs_path(copy_path)
    if not os.path.lexists(raw):
        return "ours"
    if is_dir_link(copy_path) or not os.path.isdir(raw):
        return "theirs"
    noted = meta.get("identity")
    keys = {"dev", "ino", "birth"}
    if isinstance(noted, dict) and keys <= set(noted):
        if now_identity is None:
            return "unknown"
        return "ours" if all(noted[key] == now_identity[key] for key in keys) else "theirs"
    # On platforms without birth time (e.g. Linux), require extended-attribute token match when stored.
    if meta.get("xattr") is True:
        expected_token = str(meta.get("token") or "")
        if not expected_token:
            return "theirs"
        state, actual_token = _read_copy_xattr_state(copy_path)
        if state == "error":
            return "unknown"
        if state != "ok" or actual_token != expected_token:
            return "theirs"
        noted_stat = meta.get("stat")
        if isinstance(noted_stat, dict) and {"dev", "ino"} <= set(noted_stat):
            try:
                st = os.lstat(raw)
            except OSError:
                return "unknown"
            if st.st_ino and (st.st_dev != noted_stat["dev"] or st.st_ino != noted_stat["ino"]):
                return "theirs"
        return "ours"
    # Neither birth time nor xattr is available on this filesystem: never delete on reusable inode alone.
    noted_stat = meta.get("stat")
    if isinstance(noted_stat, dict) and {"dev", "ino"} <= set(noted_stat):
        try:
            st = os.lstat(raw)
        except OSError:
            return "unknown"
        if st.st_ino and (st.st_dev != noted_stat["dev"] or st.st_ino != noted_stat["ino"]):
            return "theirs"
    return "unknown"
