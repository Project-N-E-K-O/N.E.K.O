"""Portable v1 model bundle validation, mirrored in the Market service.

This validates the envelope and resource bytes without extracting anything.
Native model admission and rendering are separate checks.
"""

import hashlib
import json
import os
import re
import stat
import struct
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class BundleError(ValueError):
    """Stable container failure code; callers translate it at their UI boundary."""


@dataclass(frozen=True)
class BundleLimits:
    """Independent byte/member budgets, checked before allocating ZIP metadata."""

    archive_bytes: int = 500 * 1024 * 1024
    unpacked_bytes: int = 2 * 1024 * 1024 * 1024
    resource_bytes: int = 500 * 1024 * 1024
    vrm_bytes: int = 200 * 1024 * 1024
    manifest_bytes: int = 8 * 1024 * 1024
    directory_bytes: int = 8 * 1024 * 1024
    members: int = 10001  # At most 10000 resources plus the manifest.


@dataclass(frozen=True)
class BundleFile:
    path: str
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class ValidatedBundle:
    """Identity and resource inventory from one stable, fully verified input."""

    model_format: str
    entrypoint: str
    display_name: str
    attribution: str
    license: str
    files: tuple[BundleFile, ...]
    byte_size: int
    sha256: str


_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{index}" for prefix in ("COM", "LPT") for index in "123456789¹²³"
}
_SUFFIXES = {"live2d": (".model3.json",), "vrm": (".vrm",), "mmd": (".pmx", ".pmd")}
_MANIFEST_FIELDS = {"schema", "model_format", "entrypoint", "display_name", "attribution", "license", "files"}


def _path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise BundleError("invalid_bundle_path")
    parts = value.split("/")
    if len(parts) > 32:
        raise BundleError("invalid_bundle_path")
    for part in parts:
        if (
            not part or part in {".", ".."} or part.endswith((".", " "))
            or len(part.encode("utf-8")) > 255
            or part.split(".", 1)[0].rstrip(" ").upper() in _RESERVED
            or any(c in '<>:"\\|?*' or unicodedata.category(c).startswith("C") for c in part)
        ):
            raise BundleError("invalid_bundle_path")
    return value


def _key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _check_extra(raw: bytes) -> None:
    """Reject ZIP64 fields even when ordinary sizes make the field redundant."""
    offset = 0
    while offset < len(raw):
        if len(raw) - offset < 4:
            raise BundleError("invalid_bundle_extra")
        kind, size = struct.unpack_from("<HH", raw, offset)
        offset += 4
        if kind == 1 or offset + size > len(raw):
            raise BundleError("invalid_bundle_extra")
        offset += size


def _check_local_header(source, offset: int, directory_start: int, name: bytes, flags: int, method: int) -> None:
    """Inspect raw local metadata before ZipFile can normalize member names."""
    resume = source.tell()
    if not 0 <= offset <= directory_start - 30:
        raise BundleError("invalid_bundle_member")
    source.seek(offset)
    header = source.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise BundleError("invalid_bundle_member")
    version, local_flags, local_method = struct.unpack_from("<3H", header, 4)
    compressed, unpacked = struct.unpack_from("<2L", header, 18)
    name_size, extra_size = struct.unpack_from("<2H", header, 26)
    if (
        version == 45 or compressed == 0xFFFFFFFF or unpacked == 0xFFFFFFFF
        or local_flags != flags or local_method != method
        or source.tell() + name_size + extra_size > directory_start
    ):
        raise BundleError("invalid_bundle_member")
    # Exact raw equality also rejects inconsistent encodings in the two headers.
    if source.read(name_size) != name:
        raise BundleError("invalid_bundle_member")
    _check_extra(source.read(extra_size))
    source.seek(resume)


def _directory_budget(source, size: int, limits: BundleLimits) -> None:
    """Bound and count the central directory before ZipFile allocates its entries.

    The v1 envelope uses ordinary single-disk ZIP, stored or deflated members.
    Its size/member limits do not require ZIP64 or split archives.
    """
    source.seek(max(0, size - 65557))
    tail = source.read(65557)
    marker = tail.rfind(b"PK\x05\x06")
    if marker < 0 or len(tail) - marker < 22:
        raise BundleError("invalid_bundle_archive")
    _, disk, start_disk, disk_count, count, directory_size, offset, comment = struct.unpack(
        "<4s4H2LH", tail[marker:marker + 22]
    )
    end_offset = size - len(tail) + marker
    if (
        disk or start_disk or disk_count != count or not 0 < count <= limits.members
        or directory_size > limits.directory_bytes or offset + directory_size != end_offset
        or len(tail) - marker != 22 + comment
    ):
        raise BundleError("invalid_bundle_directory")
    source.seek(offset)
    for _ in range(count):
        header = source.read(46)
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise BundleError("invalid_bundle_directory")
        lengths = struct.unpack_from("<3H", header, 28)
        if source.tell() + sum(lengths) > end_offset:
            raise BundleError("invalid_bundle_directory")
        version, flags, method = struct.unpack_from("<3H", header, 6)
        compressed, unpacked = struct.unpack_from("<2L", header, 20)
        local_offset = struct.unpack_from("<L", header, 42)[0]
        if (
            version == 45 or compressed == 0xFFFFFFFF or unpacked == 0xFFFFFFFF
            or local_offset == 0xFFFFFFFF or struct.unpack_from("<H", header, 34)[0]
        ):
            raise BundleError("invalid_bundle_member")
        name = source.read(lengths[0])
        # ZipInfo truncates NUL names; inspect bytes before that information is lost.
        if not name or b"\x00" in name:
            raise BundleError("invalid_bundle_member")
        _check_extra(source.read(lengths[1]))
        source.seek(lengths[2], 1)
        _check_local_header(source, local_offset, offset, name, flags, method)
    if source.tell() != end_offset:
        raise BundleError("invalid_bundle_directory")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError("duplicate_manifest_field")
        result[key] = value
    return result


def _constant(_):
    raise BundleError("invalid_manifest_number")


def _text(value, maximum):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= maximum:
        raise BundleError("invalid_manifest_text")
    value.encode("utf-8")
    return value.strip()


def _manifest(raw: bytes, limits: BundleLimits) -> tuple[dict, tuple[BundleFile, ...]]:
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(data, dict) or set(data) != _MANIFEST_FIELDS or data["schema"] != "neko.model.package.v1":
        raise BundleError("unsupported_model_bundle_schema")
    kind = data["model_format"]
    if not isinstance(kind, str) or kind not in _SUFFIXES:
        raise BundleError("invalid_model_format")
    entry = _path(data["entrypoint"])
    if not entry.endswith(_SUFFIXES[kind]) or (kind in {"live2d", "vrm"} and "/" in entry):
        raise BundleError("invalid_model_entrypoint")
    for field, maximum in (("display_name", 120), ("attribution", 5000), ("license", 5000)):
        data[field] = _text(data[field], maximum)
    if not isinstance(data["files"], list) or not 0 < len(data["files"]) < limits.members:
        raise BundleError("invalid_bundle_files")
    files = []
    seen = set()
    for item in data["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "byte_size", "sha256"}:
            raise BundleError("invalid_bundle_file")
        path = _path(item["path"])
        if (
            _key(path) in seen or type(item["byte_size"]) is not int or item["byte_size"] < 0
            or not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise BundleError("invalid_bundle_file")
        seen.add(_key(path))
        files.append(BundleFile(path, item["byte_size"], item["sha256"]))
    paths = {item.path for item in files}
    mains = [p for p in paths if p.lower().endswith((".model3.json", ".vrm", ".pmx", ".pmd"))]
    if entry not in paths or mains != [entry]:
        raise BundleError("invalid_model_entrypoint")
    return data, tuple(files)


def _source_stamp(source) -> tuple:
    """Exclude access time: reads may change it without changing input bytes."""
    info = os.fstat(source.fileno())
    if not stat.S_ISREG(info.st_mode):
        raise BundleError("invalid_model_bundle")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _same_input(source, path: Path | None, stamp: tuple) -> None:
    """Check the open file and, for pathname inputs, reject later path replacement."""
    try:
        linked = True
        if path is not None:
            current = path.stat(follow_symlinks=False)
            linked = stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == stamp[:2]
        if not linked or _source_stamp(source) != stamp:
            raise BundleError("model_bundle_changed")
    except OSError as exc:
        raise BundleError("model_bundle_changed") from exc


def _hash_source(source, limit: int) -> tuple[int, str]:
    """Read the whole archive in bounded blocks without keeping a second copy."""
    source.seek(0)
    size = 0
    digest = hashlib.sha256()
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise BundleError("model_bundle_too_large")
        digest.update(chunk)
    return size, digest.hexdigest()


@contextmanager
def inspect_bundle(path: str | Path | BinaryIO, *, limits: BundleLimits = BundleLimits()):
    """Give read-only inspectors the same archive; publish only after context exit.

    Native admission reads through this context rather than reopening a pathname
    after container validation. The final identity check covers those reads too.
    """
    try:
        if isinstance(path, (str, os.PathLike)):
            path = Path(path)
            # No-follow/nonblocking open prevents link aliases and FIFO input where supported.
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        else:
            # A private object store can lend its checked descriptor; never reopen its path.
            # Duplicate it to retain caller ownership and bypass any stale read buffer.
            descriptor = os.dup(path.fileno())
            path = None
        # Unbuffered reads ensure the final rehash cannot reuse old BufferedReader bytes.
        with os.fdopen(descriptor, "rb", buffering=0) as source:
            stamp = _source_stamp(source)
            _same_input(source, path, stamp)
            size, digest = _hash_source(source, limits.archive_bytes)
            _same_input(source, path, stamp)
            _directory_budget(source, size, limits)
            source.seek(0)
            with zipfile.ZipFile(source) as archive:
                infos = archive.infolist()
                if len(infos) > limits.members:
                    raise BundleError("too_many_bundle_members")
                if not infos or min(info.header_offset for info in infos) != 0:
                    raise BundleError("invalid_bundle_archive")
                members = {}
                seen = set()
                spellings = {}
                for info in infos:
                    name = _path(info.filename.removesuffix("/") if info.is_dir() else info.filename)
                    parts = name.split("/")
                    for depth in range(1, len(parts) + 1):
                        prefix = "/".join(parts[:depth])
                        key = _key(prefix)
                        if key in spellings and spellings[key] != prefix:
                            raise BundleError("conflicting_bundle_paths")
                        spellings[key] = prefix
                    mode = stat.S_IFMT(info.external_attr >> 16)
                    if (
                        _key(name) in seen or info.flag_bits & 1
                        or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
                        or (mode == stat.S_IFDIR) != info.is_dir() and mode != 0
                        or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or (name != "manifest.json" and name != "model" and not name.startswith("model/"))
                    ):
                        raise BundleError("invalid_bundle_member")
                    seen.add(_key(name))
                    if not info.is_dir():
                        members[name] = info
                for name in members:
                    parts = name.split("/")
                    if any("/".join(parts[:depth]) in members for depth in range(1, len(parts))):
                        raise BundleError("conflicting_bundle_paths")
                manifest_info = members.pop("manifest.json", None)
                if manifest_info is None or manifest_info.file_size > limits.manifest_bytes:
                    raise BundleError("invalid_bundle_manifest")
                with archive.open(manifest_info) as handle:
                    raw = handle.read(limits.manifest_bytes + 1)
                if len(raw) > limits.manifest_bytes:
                    raise BundleError("invalid_bundle_manifest")
                data, files = _manifest(raw, limits)
                if set(members) != {"model/" + item.path for item in files}:
                    raise BundleError("bundle_file_list_mismatch")
                declared = sum(item.byte_size for item in files)
                if declared > limits.unpacked_bytes:
                    raise BundleError("model_bundle_unpacked_too_large")
                if any(item.byte_size > limits.resource_bytes for item in files):
                    raise BundleError("bundle_resource_too_large")
                entry_size = next(item.byte_size for item in files if item.path == data["entrypoint"])
                if data["model_format"] == "vrm" and entry_size > limits.vrm_bytes:
                    raise BundleError("vrm_resource_too_large")
                total = 0
                for item in files:
                    info = members["model/" + item.path]
                    if info.file_size != item.byte_size:
                        raise BundleError("bundle_file_size_mismatch")
                    resource_hash = hashlib.sha256()
                    actual = 0
                    with archive.open(info) as handle:
                        while chunk := handle.read(1024 * 1024):
                            actual += len(chunk)
                            total += len(chunk)
                            if actual > item.byte_size or total > limits.unpacked_bytes:
                                raise BundleError("model_bundle_unpacked_too_large")
                            resource_hash.update(chunk)
                    if actual != item.byte_size or resource_hash.hexdigest() != item.sha256:
                        raise BundleError("bundle_file_hash_mismatch")
                result = ValidatedBundle(
                    data["model_format"], data["entrypoint"], data["display_name"],
                    data["attribution"], data["license"], files, size, digest,
                )
                # Consumers may inspect bytes, but must not write final resources here.
                yield result, archive
            # A single descriptor alone does not prevent in-place rewrites mid-parse.
            _same_input(source, path, stamp)
            final_size, final_digest = _hash_source(source, limits.archive_bytes)
            _same_input(source, path, stamp)
            if (final_size, final_digest) != (size, digest):
                raise BundleError("model_bundle_changed")
    except BundleError:
        raise
    except (OSError, ValueError, EOFError, zipfile.BadZipFile, zlib.error, RuntimeError) as exc:
        raise BundleError("invalid_model_bundle") from exc


def validate_bundle(path: str | Path | BinaryIO, *, limits: BundleLimits = BundleLimits()) -> ValidatedBundle:
    """Validate the container only; native admission is an independent consumer."""
    with inspect_bundle(path, limits=limits) as (result, _archive):
        return result
