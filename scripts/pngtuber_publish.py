"""Serialize package publication and recover interrupted multi-file updates."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import tempfile
import threading
import time


_HELD_LOCKS: set[tuple[str, int]] = set()


@contextmanager
def publication_lock(root: Path, key: str, *, timeout: float = 10.0):
    identity = str(root.resolve()).casefold() + ":" + key
    owner = (identity, threading.get_ident())
    if owner in _HELD_LOCKS:
        raise BlockingIOError("PNGTuber publication lock is not reentrant")
    name = hashlib.sha256(identity.encode()).hexdigest()
    # A shared deterministic path also works when installed resource packs are read-only.
    path = Path(tempfile.gettempdir()) / ("neko-pngtuber-" + name + ".lock")
    if path.is_symlink():
        raise ValueError("unsafe PNGTuber lock file")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "r+b") as stream:
        deadline = time.monotonic() + max(0, timeout)
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for PNGTuber publication lock") from error
                time.sleep(min(0.05, remaining))
        # Never inspect a byte held by another process on Windows.
        stream.seek(0)
        stream.write(b"0")
        stream.truncate(1)
        stream.flush()
        _HELD_LOCKS.add(owner)
        try:
            yield
        finally:
            _HELD_LOCKS.discard(owner)
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _target(root: Path, name: str) -> Path:
    path = root / name
    path.resolve().relative_to(root.resolve())
    if path.is_symlink() or not (name == "manifest.json" or re.fullmatch(r"yui-chibi-[0-9]{3}\.zip", name)
        or name in {"yui-chibi-source/model.json", "yui-chibi-source/metadata.actions.json", "yui-chibi-source/reviewed-resource-manifest.json"}):
        raise ValueError("invalid package publication target")
    return path


def recover_publication(root: Path):
    transaction = root / ".yui-chibi-publish"
    if transaction.is_symlink():
        raise ValueError("unsafe publication transaction directory")
    journal = transaction / "journal.json"
    if journal.is_file():
        entries = json.loads(journal.read_text(encoding="utf-8"))
        for row in entries:
            target = _target(root, row["path"])
            backup = transaction / "old" / row["path"]
            backup.resolve().relative_to(transaction.resolve())
            if row["existed"]:
                # Keep backups until the whole rollback has finished; recovery is repeatable.
                temporary = target.with_name(target.name + ".restore")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(backup, temporary)
                os.replace(temporary, target)
            elif target.exists():
                target.unlink()
        # Recovery is complete. A later cleanup interruption must not replay missing backups.
        journal.unlink()
    if transaction.exists():
        shutil.rmtree(transaction)


def publish_files(root: Path, files: dict[str, Path], obsolete: list[str]):
    """Call under publication_lock. Roll back exceptions and recover a killed publish next time."""
    recover_publication(root)
    transaction = root / ".yui-chibi-publish"
    transaction.mkdir()
    entries = []
    try:
        for name in [*files, *obsolete]:
            target = _target(root, name)
            row = {"path": name, "existed": target.is_file()}
            entries.append(row)
            if row["existed"]:
                backup = transaction / "old" / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, backup)
            if name in files:
                candidate = transaction / "new" / name
                candidate.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(files[name], candidate)
        temporary_journal = transaction / "journal.tmp"
        with temporary_journal.open("w", encoding="utf-8") as stream:
            json.dump(entries, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_journal, transaction / "journal.json")
        # The manifest is inserted last by the caller. Other readers use the same lock.
        for name in files:
            target = _target(root, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(transaction / "new" / name, target)
        for name in obsolete:
            _target(root, name).unlink(missing_ok=True)
        # Remove the journal only after successful publication. Leftover backups are disposable.
        (transaction / "journal.json").unlink()
    except BaseException:
        recover_publication(root)
        raise
    finally:
        if transaction.exists() and not (transaction / "journal.json").exists():
            shutil.rmtree(transaction, ignore_errors=True)
