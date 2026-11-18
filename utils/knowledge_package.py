"""Portable, bounded data validation for unified knowledge-pack archives.

This module has no application, database or model dependencies. The market
vendors these exact bytes, and runtime installation uses the same contract.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import struct
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKAGE_FORMAT = "neko.knowledge-package"
FORMAT_VERSION = 2
PACKAGE_SUFFIX = ".neko-knowledge.zip"
CHUNKING_VERSION = "neko-pack-chunks-v1"
PROCESSING_VERSION = "neko-onnx-last-token-truncate-l2-v1"
_HASH = re.compile(r"[0-9a-f]{64}")
_PACK_ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_MEMBERS = ("pack.json", "chunks.jsonl", "vectors.f32")
_MANIFEST_KEYS = {"format", "format_version", "package_id", "name", "version", "source", "chunking_version", "embedding_spec", "counts", "files"}


class PackageError(ValueError):
    """Expose stable reason codes without paths, source text or parser details."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = self.code = reason


@dataclass(frozen=True)
class PackageLimits:
    """Share existing pack capacities while bounding archive-only overhead."""

    max_archive_bytes: int = 64 * 1024 * 1024
    max_expanded_bytes: int = 128 * 1024 * 1024
    max_documents: int = 5000
    max_chunks: int = 10000
    max_dimension: int = 768
    max_record_bytes: int = 2 * 1024 * 1024
    max_manifest_bytes: int = 64 * 1024
    max_pack_bytes: int = 10 * 1024 * 1024


DEFAULT_LIMITS = PackageLimits()


def _preflight_archive(path: Path, size: int) -> None:
    """Bound the central directory and reject ZIP64 before ZipFile allocates."""
    if size < 22:
        raise PackageError("package_invalid_archive")
    with path.open("rb") as stream:
        if stream.read(4) != b"PK\x03\x04":
            raise PackageError("package_invalid_archive")
        stream.seek(-22, 2)
        values = struct.unpack("<4s4H2LH", stream.read(22))
        if size >= 42:
            stream.seek(-42, 2)
            if stream.read(4) == b"PK\x06\x07":
                raise PackageError("package_invalid_archive")
    signature, disk, directory_disk, disk_entries, entries, directory_size, offset, comment = values
    if (signature != b"PK\x05\x06" or disk or directory_disk or comment
            or entries != 4 or disk_entries != 4 or directory_size > 16 * 1024
            or offset + directory_size != size - 22):
        raise PackageError("package_invalid_archive")


def _manifest(archive: zipfile.ZipFile, limits: PackageLimits) -> dict:
    """Accept only the four canonical data members, with no ZIP64 aliases."""
    infos = archive.infolist()
    if len(infos) != 4 or {item.orig_filename for item in infos} != {"manifest.json", *_MEMBERS} or archive.comment:
        raise PackageError("package_invalid_members")
    total = 0
    for info in infos:
        # Our writer needs no ZIP extensions. Checking both central and local
        # headers closes force_zip64's small-file bypass of EOCD-only checks.
        if (info.extra or info.comment or info.extract_version >= 45 or info.is_dir()
                or info.flag_bits & 1 or stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFREG}
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            raise PackageError("package_invalid_members")
        with Path(archive.filename).open("rb") as stream:
            stream.seek(info.header_offset)
            header = stream.read(30)
            if len(header) != 30 or header[:4] != b"PK\x03\x04" or struct.unpack_from("<H", header, 4)[0] >= 45:
                raise PackageError("package_invalid_archive")
            name_size, extra_size = struct.unpack_from("<HH", header, 26)
            stream.seek(name_size, 1)
            if extra_size or stream.read(extra_size):
                raise PackageError("package_invalid_archive")
        total += info.file_size
        if total > limits.max_expanded_bytes:
            raise PackageError("package_expanded_too_large")
    with archive.open("manifest.json") as stream:
        raw = stream.read(limits.max_manifest_bytes + 1)
    if len(raw) > limits.max_manifest_bytes:
        raise PackageError("package_manifest_too_large")
    manifest = _mapping(_json(raw), _MANIFEST_KEYS)
    if manifest["format"] != PACKAGE_FORMAT or type(manifest["format_version"]) is not int or manifest["format_version"] != 2:
        raise PackageError("package_unsupported_format")
    if not isinstance(manifest["package_id"], str) or not _PACK_ID.fullmatch(manifest["package_id"]):
        raise PackageError("package_invalid_identity")
    _text(manifest["name"], 500)
    _text(manifest["version"], 100)
    for value in _mapping(manifest["source"], {"author", "license", "url"}).values():
        _text(value, 500, empty=True)
    if manifest["chunking_version"] != CHUNKING_VERSION:
        raise PackageError("package_unsupported_chunking")
    spec = validate_specification(manifest["embedding_spec"], limits)
    counts = _mapping(manifest["counts"], {"documents", "chunks"})
    for key, maximum in (("documents", limits.max_documents), ("chunks", limits.max_chunks)):
        if type(counts[key]) is not int or not 1 <= counts[key] <= maximum:
            raise PackageError("package_capacity_exceeded")
    for name, item in _mapping(manifest["files"], set(_MEMBERS)).items():
        descriptor = _mapping(item, {"bytes", "sha256"})
        _hash(descriptor["sha256"])
        if type(descriptor["bytes"]) is not int or descriptor["bytes"] != archive.getinfo(name).file_size:
            raise PackageError("package_member_size_mismatch")
    if manifest["files"]["vectors.f32"]["bytes"] != counts["chunks"] * spec["dimension"] * 4:
        raise PackageError("package_vector_shape_mismatch")
    return manifest


def _pack(raw: bytes, manifest: dict, limits: PackageLimits) -> dict:
    """Check the portable v1 payload without importing the knowledge domain."""
    if len(raw) > limits.max_pack_bytes:
        raise PackageError("pack_too_large")
    pack = _mapping(_json(raw), {"schema_version", "pack_id", "material_type", "source", "entries"})
    if (type(pack["schema_version"]) is not int or pack["schema_version"] != 1
            or pack["pack_id"] != manifest["package_id"] or not isinstance(pack["material_type"], str)
            or pack["material_type"] not in {"knowledge", "corpus"}):
        raise PackageError("invalid_pack")
    source = _mapping(pack["source"], {"name", "homepage", "license"})
    for key, value in source.items():
        _text(value, 500, empty=key != "name")
    if (source["name"] != manifest["name"] or source["name"] != manifest["source"]["author"]
            or source["homepage"] != manifest["source"]["url"] or source["license"] != manifest["source"]["license"]):
        raise PackageError("package_identity_mismatch")
    entries = pack["entries"]
    if not isinstance(entries, list) or len(entries) != manifest["counts"]["documents"]:
        raise PackageError("package_count_mismatch")
    titles = set()
    for value in entries:
        if not isinstance(value, dict) or not {"title", "content"} <= set(value) or set(value) - {"title", "terms", "tags", "summary", "content"}:
            raise PackageError("invalid_entry")
        title = _text(value["title"], 500)
        if title in titles:
            raise PackageError("duplicate_title")
        titles.add(title)
        _text(value["content"], 80000)
        _text(value.get("summary", ""), 4000, empty=True)
        terms, tags = value.get("terms", {}), value.get("tags", [])
        if not isinstance(terms, dict) or set(terms) - {"alias", "recognition"} or not isinstance(tags, list) or len(tags) > 32:
            raise PackageError("invalid_entry")
        for tag in tags:
            _text(tag, 100)
        for words in terms.values():
            if not isinstance(words, list) or len(words) > 64:
                raise PackageError("invalid_entry")
            for word in words:
                _text(word, 300)
    return pack


@dataclass(frozen=True)
class ValidatedPackage:
    """Validated v1 text and bounded chunk metadata; vectors stay on disk."""

    path: Path
    manifest: dict
    pack: dict
    chunks: tuple[dict, ...]
    artifact_sha256: str
    size: int
    semantic_available: bool

    def metadata(self) -> dict:
        """Expose artifact identities without including any original source path."""
        return {"artifact_id": self.artifact_sha256, "sha256": self.artifact_sha256,
                "artifact_sha256": self.artifact_sha256, "size": self.size, "bytes": self.size,
                "package_id": self.manifest["package_id"], "name": self.manifest["name"],
                "version": self.manifest["version"], "format": PACKAGE_FORMAT, "format_version": FORMAT_VERSION,
                "embedding_spec": self.manifest["embedding_spec"]}


def validate_package(path: Path, *, expected_spec: dict | None = None,
                     expected_sha256: str | None = None, expected_package_id: str | None = None,
                     expected_version: str | None = None,
                     limits: PackageLimits = DEFAULT_LIMITS) -> ValidatedPackage:
    """Validate bytes, pack/chunk identity and each finite unit vector in order."""
    path = Path(path)
    digest, size = hash_file(path, limits.max_archive_bytes)
    if expected_sha256 is not None and digest != _hash(expected_sha256):
        raise PackageError("package_artifact_hash_mismatch")
    try:
        _preflight_archive(path, size)
        with zipfile.ZipFile(path) as archive:
            manifest = _manifest(archive, limits)
            if ((expected_package_id is not None and manifest["package_id"] != expected_package_id)
                    or (expected_version is not None and manifest["version"] != expected_version)):
                raise PackageError("package_identity_mismatch")
            _check_members(archive, manifest, limits)
            with archive.open("pack.json") as stream:
                pack = _pack(stream.read(limits.max_pack_bytes + 1), manifest, limits)
            titles = {entry["title"] for entry in pack["entries"]}
            chunks, seen = [], set()
            by_title = {title: [] for title in titles}
            for value in _rows(archive, "chunks.jsonl", limits.max_record_bytes):
                row = _mapping(value, {"entry_title", "chunk_index", "text_hash", "embedding_text"})
                index = row["chunk_index"]
                if (not isinstance(row["entry_title"], str) or row["entry_title"] not in titles or type(index) is not int or index < 0
                        or (row["entry_title"], index) in seen or len(chunks) >= limits.max_chunks):
                    raise PackageError("package_invalid_chunks")
                text = _text(row["embedding_text"], 2000)
                if hashlib.sha256(text.encode()).hexdigest() != _hash(row["text_hash"]):
                    raise PackageError("package_input_hash_mismatch")
                seen.add((row["entry_title"], index))
                chunks.append(row)
                by_title[row["entry_title"]].append(row)
            if len(chunks) != manifest["counts"]["chunks"] or {row["entry_title"] for row in chunks} != titles:
                raise PackageError("package_count_mismatch")
            for entry in pack["entries"]:
                rows = sorted(by_title[entry["title"]], key=lambda row: row["chunk_index"])
                if [row["chunk_index"] for row in rows] != list(range(len(rows))):
                    raise PackageError("package_invalid_chunks")
                # The fixed chunking contract renders a 600-character header.
                # Sentence reflow may alter whitespace, but every non-whitespace
                # source character must remain covered, in order, by excerpts.
                content = "".join(entry["content"].split())
                cursor = 0
                for row in rows:
                    header = entry["title"]
                    if row["chunk_index"] == 0 and entry.get("summary"):
                        header += "\n" + entry["summary"]
                    prefix = header[:600] + "\n"
                    if not row["embedding_text"].startswith(prefix):
                        raise PackageError("package_source_mismatch")
                    body = "".join(row["embedding_text"][len(prefix):].split())
                    # Repeated prose can match an earlier covered excerpt.
                    # Choose the furthest contiguous match, allowing only the
                    # declared overlap and never skipping an uncovered gap.
                    start = content.rfind(body, max(0, cursor - 120), cursor + len(body)) if body else -1
                    if start < 0 or start > cursor:
                        raise PackageError("package_source_mismatch")
                    cursor = max(cursor, start + len(body))
                if cursor != len(content):
                    raise PackageError("package_source_mismatch")
            dim = manifest["embedding_spec"]["dimension"]
            common = compatible_dimension(expected_spec, manifest["embedding_spec"]) if expected_spec else None
            compatible = common is not None
            with archive.open("vectors.f32") as stream:
                for _row in chunks:
                    raw = stream.read(dim * 4)
                    if len(raw) != dim * 4:
                        raise PackageError("package_vector_shape_mismatch")
                    vector = struct.unpack(f"<{dim}f", raw)
                    if not all(math.isfinite(v) for v in vector) or abs(math.hypot(*vector) - 1.0) > 0.001:
                        raise PackageError("package_invalid_vector")
                    if common and math.hypot(*vector[:common]) <= 0:
                        compatible = False
            return ValidatedPackage(path, manifest, pack, tuple(chunks), digest, size, compatible)
    except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, UnicodeError, struct.error, zlib.error) as exc:
        raise PackageError("package_invalid_archive") from exc


def canonical_json(value: Any) -> bytes:
    """Encode portable JSON; non-finite numbers are never package data."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise PackageError("package_invalid_json") from exc


def _object(pairs: list[tuple[str, Any]]) -> dict:
    """Reject duplicate keys instead of silently selecting one value."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageError("package_duplicate_key")
        result[key] = value
    return result


def _json(raw: bytes) -> Any:
    def reject_constant(value: str) -> None:
        raise PackageError("package_invalid_json")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, PackageError):
            raise
        raise PackageError("package_invalid_json") from exc


def _mapping(value: Any, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise PackageError("package_invalid_fields")
    return value


def _text(value: Any, maximum: int, *, empty: bool = False) -> str:
    # NUL is not portable through PostgreSQL JSON/text metadata or local indexes.
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum or (not empty and not value.strip()):
        raise PackageError("package_invalid_text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise PackageError("package_invalid_text") from exc
    return value


def _hash(value: Any) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise PackageError("package_invalid_hash")
    return value


def validate_specification(value: Any, limits: PackageLimits = DEFAULT_LIMITS) -> dict:
    """Check the single supported encoder contract, including actual file hashes."""
    spec = _mapping(value, {"model_id", "profile", "dimension", "quantization",
                            "max_length", "weights_sha256", "processing_version"})
    dim = spec["dimension"]
    if (type(dim) is not int or dim not in {32, 64, 128, 256, 512, 768}
            or dim > limits.max_dimension or spec["profile"] != "local-text-retrieval-v1"
            or not isinstance(spec["quantization"], str) or spec["quantization"] not in {"int8", "fp32"}
            or type(spec["max_length"]) is not int or spec["max_length"] != 1024
            or spec["processing_version"] != PROCESSING_VERSION):
        raise PackageError("package_unsupported_embedding_spec")
    expected_id = f"{spec['profile']}-{dim}d-{spec['quantization']}-mlen{spec['max_length']}"
    if spec["model_id"] != expected_id:
        raise PackageError("package_embedding_identity_mismatch")
    weights = _mapping(spec["weights_sha256"], {"model", "external_data", "tokenizer"})
    for digest in weights.values():
        _hash(digest)
    return _json(canonical_json(spec))


def compatible_dimension(left: dict | None, right: dict | None) -> int | None:
    """Return the common prefix only for identical encoder processing identities.

    For this fixed prefix-truncate/L2 processing version, normalizing an existing
    vector's shorter prefix is algebraically the direct lower-dimensional output.
    This data-only check does not certify model recall or cross-CPU accuracy.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    if left == right:
        dimension = left.get("dimension")
        return dimension if type(dimension) is int and 1 <= dimension <= 768 else None
    try:
        a, b = validate_specification(left), validate_specification(right)
    except PackageError:
        return None
    # Both canonical IDs have already been checked; dimension is their only
    # permitted difference. In particular, quantized weights cannot cross FP32.
    if any(a[key] != b[key] for key in a if key not in {"model_id", "dimension"}):
        return None
    return min(a["dimension"], b["dimension"])


def hash_file(path: Path, maximum: int) -> tuple[str, int]:
    """Hash files in bounded blocks and reject growth past the supplied limit."""
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as stream:
        while block := stream.read(64 * 1024):
            size += len(block)
            if size > maximum:
                raise PackageError("package_too_large")
            digest.update(block)
    return digest.hexdigest(), size


def _rows(archive: zipfile.ZipFile, name: str, maximum: int) -> Iterator[dict]:
    with archive.open(name) as stream:
        while raw := stream.readline(maximum + 1):
            if len(raw) > maximum or not raw.endswith(b"\n"):
                raise PackageError("package_record_too_large")
            value = _json(raw)
            if not isinstance(value, dict):
                raise PackageError("package_invalid_record")
            yield value


def _check_compressed_stream(path: Path, info: zipfile.ZipInfo) -> None:
    """Reject forged expansion lengths and trailing compressed-stream payloads."""
    # ZipExtFile stops at the declared file_size. An attacker can also forge
    # the CRC for that prefix, hiding a longer DEFLATE stream from normal ZIP
    # reads. Count the actual output independently, with bounded allocations.
    if info.compress_type == zipfile.ZIP_STORED:
        if info.compress_size != info.file_size:
            raise PackageError("package_invalid_archive")
        return
    decoder = zlib.decompressobj(-zlib.MAX_WBITS)
    expanded = 0
    with path.open("rb") as stream:
        stream.seek(info.header_offset)
        header = stream.read(30)
        if len(header) != 30 or header[:4] != b"PK\x03\x04":
            raise PackageError("package_invalid_archive")
        name_size, extra_size = struct.unpack_from("<HH", header, 26)
        stream.seek(name_size + extra_size, 1)
        remaining = info.compress_size
        while remaining:
            block = stream.read(min(remaining, 64 * 1024))
            if not block:
                raise PackageError("package_invalid_archive")
            remaining -= len(block)
            while True:
                output = decoder.decompress(block, 64 * 1024)
                expanded += len(output)
                if expanded > info.file_size or decoder.unused_data:
                    raise PackageError("package_invalid_archive")
                block = decoder.unconsumed_tail
                # zlib can consume all input while retaining output beyond
                # max_length. Drain with another bounded call, never flush().
                if not block and (len(output) < 64 * 1024 or decoder.eof):
                    break
        if not decoder.eof or expanded != info.file_size:
            raise PackageError("package_invalid_archive")


def _check_members(archive: zipfile.ZipFile, manifest: dict, limits: PackageLimits) -> None:
    # Check all four streams, including the manifest; member hashes alone
    # cannot detect a self-consistent declared prefix with hidden expansion.
    for info in archive.infolist():
        _check_compressed_stream(Path(archive.filename), info)
    total = 0
    for name in _MEMBERS:
        digest, size = hashlib.sha256(), 0
        with archive.open(name) as stream:
            while block := stream.read(64 * 1024):
                size += len(block)
                total += len(block)
                if total > limits.max_expanded_bytes:
                    raise PackageError("package_expanded_too_large")
                digest.update(block)
        if size != manifest["files"][name]["bytes"] or digest.hexdigest() != manifest["files"][name]["sha256"]:
            raise PackageError("package_member_hash_mismatch")
