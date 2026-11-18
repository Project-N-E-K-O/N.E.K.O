"""Validate bounded, data-only personal knowledge packages without inference."""

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

from .models import LibraryError


PACKAGE_FORMAT = "neko.personal-knowledge-package"
FORMAT_VERSION = 1
PACKAGE_SUFFIX = ".neko-knowledge-pack.zip"
CHUNKING_VERSION = "neko-paragraph-token-windows-v2-title-v1"
# Older personal packages remain readable; only new builds promise token-aware
# source coverage. This does not change the separate public-knowledge protocol.
SUPPORTED_CHUNKING_VERSIONS = {"neko-paragraph-windows-v1-title-v1", CHUNKING_VERSION}
PROCESSING_VERSION = "neko-onnx-last-token-truncate-l2-v1"
_HASH = re.compile(r"[0-9a-f]{64}")
_IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,100}")
_MEMBERS = ("documents.jsonl", "chunks.jsonl", "vectors.f32")
_MANIFEST_KEYS = {
    "format", "format_version", "package_id", "name", "version", "source",
    "chunking_version", "embedding_spec", "counts", "files",
}


@dataclass(frozen=True)
class PackageLimits:
    """Trial safety bounds, not a performance or minimum-hardware promise."""

    max_archive_bytes: int = 64 * 1024 * 1024
    max_expanded_bytes: int = 128 * 1024 * 1024
    max_documents: int = 200
    max_chunks: int = 10_000
    max_dimension: int = 768
    max_record_bytes: int = 2 * 1024 * 1024
    max_manifest_bytes: int = 64 * 1024
    max_document_chars: int = 256_000
    # Mirror the local library's existing aggregate bound for installable packages.
    max_library_chars: int = 2_000_000


DEFAULT_LIMITS = PackageLimits()


def canonical_json(value: Any) -> bytes:
    """Encode portable JSON; non-finite numbers are never package data."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise LibraryError("package_invalid_json") from exc


def _object(pairs: list[tuple[str, Any]]) -> dict:
    """Reject duplicate keys instead of silently selecting one value."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise LibraryError("package_duplicate_key")
        result[key] = value
    return result


def _json(raw: bytes) -> Any:
    def reject_constant(value: str) -> None:
        raise LibraryError("package_invalid_json")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, LibraryError):
            raise
        raise LibraryError("package_invalid_json") from exc


def _mapping(value: Any, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise LibraryError("package_invalid_fields")
    return value


def _text(value: Any, maximum: int, *, empty: bool = False) -> str:
    # NUL is not portable through PostgreSQL JSON/text metadata or local indexes.
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum or (not empty and not value.strip()):
        raise LibraryError("package_invalid_text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise LibraryError("package_invalid_text") from exc
    return value


def _identity(value: Any) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise LibraryError("package_invalid_identity")
    return value


def _hash(value: Any) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise LibraryError("package_invalid_hash")
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
        raise LibraryError("package_unsupported_embedding_spec")
    expected_id = f"{spec['profile']}-{dim}d-{spec['quantization']}-mlen{spec['max_length']}"
    if spec["model_id"] != expected_id:
        raise LibraryError("package_embedding_identity_mismatch")
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
    except LibraryError:
        return None
    # Both canonical IDs have already been checked; dimension is their only
    # permitted difference. In particular, quantized weights cannot cross FP32.
    if any(a[key] != b[key] for key in a if key not in {"model_id", "dimension"}):
        return None
    return min(a["dimension"], b["dimension"])


def bind_source_offsets(documents: list[dict], chunks: list[dict], *, require_offsets: bool) -> list[dict]:
    """Bind excerpts to exact source spans and reject uncovered content.

    External v2 packages must declare offsets. Internal legacy snapshots may
    infer them only at the current cursor or the known 120-character window
    overlap, never by an unbounded substring search. Whitespace between source
    sections may remain outside inputs. Explicit spans can arrive out of order;
    sorted interval union handles repeated text and overlap without ambiguity.
    """
    by_id = {row["document_id"]: row for row in documents}
    cursors, previous, intervals, result = {}, {}, {}, []
    for chunk in chunks:
        identity = chunk["document_id"]
        source, text = by_id[identity]["content"], chunk["text"]
        location = dict(chunk["location"])
        cursor = cursors.get(identity, 0)
        start, end = location.get("source_start"), location.get("source_end")
        if "source_start" not in location and "source_end" not in location and not require_offsets:
            next_text = cursor
            while next_text < len(source) and source[next_text].isspace():
                next_text += 1
            old_location = previous.get(identity, {})
            group_keys = set(location) | set(old_location)
            group_keys -= {"part", "token_part", "token_path", "source_start", "source_end"}
            next_window = (old_location and location.get("part") != old_location.get("part")
                           and all(location.get(key) == old_location.get(key) for key in group_keys))
            overlap = max(0, cursor - 120)
            candidates = [overlap, cursor, next_text] if next_window else [cursor, next_text, overlap]
            start = next((position for position in candidates
                          if source[position:position + len(text)] == text), None)
            end = start + len(text) if start is not None else None
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source)
                or source[start:end] != text):
            raise LibraryError("package_source_incomplete")
        cursors[identity] = max(cursor, end)
        previous[identity] = location
        intervals.setdefault(identity, []).append((start, end))
        result.append({**chunk, "location": {**location, "source_start": start, "source_end": end}})
    for identity, row in by_id.items():
        cursor = 0
        for start, end in sorted(intervals.get(identity, [])):
            if start > cursor and row["content"][cursor:start].strip():
                raise LibraryError("package_source_incomplete")
            cursor = max(cursor, end)
        if row["content"][cursor:].strip():
            raise LibraryError("package_source_incomplete")
    return result


def hash_file(path: Path, maximum: int) -> tuple[str, int]:
    """Hash files in bounded blocks and reject growth past the supplied limit."""
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as stream:
        while block := stream.read(64 * 1024):
            size += len(block)
            if size > maximum:
                raise LibraryError("package_too_large")
            digest.update(block)
    return digest.hexdigest(), size


def _preflight_archive(path: Path, size: int) -> None:
    """Bound the central directory before ZipFile allocates member objects."""
    if size < 22:
        raise LibraryError("package_invalid_archive")
    with path.open("rb") as stream:
        if stream.read(4) != b"PK\x03\x04":
            raise LibraryError("package_invalid_archive")
        stream.seek(-22, 2)
        signature, disk, directory_disk, disk_entries, entries, directory_size, offset, comment = struct.unpack("<4s4H2LH", stream.read(22))
    # 本格式固定四个成员且不使用 ZIP64、分卷、归档注释或自解压前缀。
    if (signature != b"PK\x05\x06" or disk or directory_disk or comment
            or entries != 4 or disk_entries != 4 or directory_size > 16 * 1024
            or offset + directory_size != size - 22):
        raise LibraryError("package_invalid_archive")


def _manifest(archive: zipfile.ZipFile, limits: PackageLimits) -> dict:
    infos = archive.infolist()
    # ZipInfo.filename removes a NUL suffix. Validate the unmodified wire
    # namespace so a hidden name cannot alias one of the four allowed members.
    names = [item.orig_filename for item in infos]
    if len(names) != 4 or set(names) != {"manifest.json", *_MEMBERS} or archive.comment:
        raise LibraryError("package_invalid_members")
    total = 0
    for info in infos:
        mode = info.external_attr >> 16
        if (info.is_dir() or info.flag_bits & 1
                or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            raise LibraryError("package_invalid_members")
        total += info.file_size
        if total > limits.max_expanded_bytes:
            raise LibraryError("package_expanded_too_large")
    info = archive.getinfo("manifest.json")
    if info.file_size > limits.max_manifest_bytes:
        raise LibraryError("package_manifest_too_large")
    with archive.open(info) as stream:
        raw = stream.read(limits.max_manifest_bytes + 1)
    if len(raw) > limits.max_manifest_bytes:
        raise LibraryError("package_manifest_too_large")
    manifest = _mapping(_json(raw), _MANIFEST_KEYS)
    if manifest["format"] != PACKAGE_FORMAT or type(manifest["format_version"]) is not int or manifest["format_version"] != FORMAT_VERSION:
        raise LibraryError("package_unsupported_format")
    _identity(manifest["package_id"])
    _text(manifest["name"], 160)
    _text(manifest["version"], 100)
    source = _mapping(manifest["source"], {"author", "license", "url"})
    for value in source.values():
        _text(value, 2_000, empty=True)
    if not isinstance(manifest["chunking_version"], str) or manifest["chunking_version"] not in SUPPORTED_CHUNKING_VERSIONS:
        raise LibraryError("package_unsupported_chunking")
    validate_specification(manifest["embedding_spec"], limits)
    counts = _mapping(manifest["counts"], {"documents", "chunks"})
    for name, maximum in (("documents", limits.max_documents), ("chunks", limits.max_chunks)):
        if type(counts[name]) is not int or not 1 <= counts[name] <= maximum:
            raise LibraryError("package_capacity_exceeded")
    files = _mapping(manifest["files"], set(_MEMBERS))
    for name, descriptor in files.items():
        _mapping(descriptor, {"bytes", "sha256"})
        _hash(descriptor["sha256"])
        if type(descriptor["bytes"]) is not int or descriptor["bytes"] != archive.getinfo(name).file_size:
            raise LibraryError("package_member_size_mismatch")
    vector_bytes = counts["chunks"] * manifest["embedding_spec"]["dimension"] * 4
    if files["vectors.f32"]["bytes"] != vector_bytes:
        raise LibraryError("package_vector_shape_mismatch")
    return manifest


def _rows(archive: zipfile.ZipFile, name: str, maximum: int) -> Iterator[dict]:
    with archive.open(name) as stream:
        while raw := stream.readline(maximum + 1):
            if len(raw) > maximum or not raw.endswith(b"\n"):
                raise LibraryError("package_record_too_large")
            value = _json(raw)
            if not isinstance(value, dict):
                raise LibraryError("package_invalid_record")
            yield value


def _check_compressed_stream(path: Path, info: zipfile.ZipInfo) -> None:
    """Reject forged expansion lengths and trailing compressed-stream payloads."""
    # ZipExtFile stops at the declared file_size. An attacker can also forge
    # the CRC for that prefix, hiding a longer DEFLATE stream from normal ZIP
    # reads. Count the actual output independently, with bounded allocations.
    if info.compress_type == zipfile.ZIP_STORED:
        if info.compress_size != info.file_size:
            raise LibraryError("package_invalid_archive")
        return
    decoder = zlib.decompressobj(-zlib.MAX_WBITS)
    expanded = 0
    with path.open("rb") as stream:
        stream.seek(info.header_offset)
        header = stream.read(30)
        if len(header) != 30 or header[:4] != b"PK\x03\x04":
            raise LibraryError("package_invalid_archive")
        name_size, extra_size = struct.unpack_from("<HH", header, 26)
        stream.seek(name_size + extra_size, 1)
        remaining = info.compress_size
        while remaining:
            block = stream.read(min(remaining, 64 * 1024))
            if not block:
                raise LibraryError("package_invalid_archive")
            remaining -= len(block)
            while True:
                output = decoder.decompress(block, 64 * 1024)
                expanded += len(output)
                if expanded > info.file_size or decoder.unused_data:
                    raise LibraryError("package_invalid_archive")
                block = decoder.unconsumed_tail
                # zlib can consume all input while retaining output beyond
                # max_length. Drain with another bounded call, never flush().
                if not block and (len(output) < 64 * 1024 or decoder.eof):
                    break
        if not decoder.eof or expanded != info.file_size:
            raise LibraryError("package_invalid_archive")


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
                    raise LibraryError("package_expanded_too_large")
                digest.update(block)
        if size != manifest["files"][name]["bytes"] or digest.hexdigest() != manifest["files"][name]["sha256"]:
            raise LibraryError("package_member_hash_mismatch")


@dataclass(frozen=True)
class ValidatedPackage:
    """Validated bounded records; vector rows remain streamed from the artifact."""

    path: Path
    manifest: dict
    documents: tuple[dict, ...]
    chunks: tuple[dict, ...]
    artifact_sha256: str
    size: int
    semantic_available: bool

    def metadata(self) -> dict:
        return {"artifact_id": self.artifact_sha256, "sha256": self.artifact_sha256,
                "artifact_sha256": self.artifact_sha256, "artifact_path": str(self.path),
                "package_id": self.manifest["package_id"], "name": self.manifest["name"],
                "version": self.manifest["version"], "size": self.size,
                "embedding_spec": self.manifest["embedding_spec"]}

    def to_snapshot(self) -> dict:
        """Return domain installation records without parsing or generating vectors."""
        dim = self.manifest["embedding_spec"]["dimension"]
        chunks = []
        with zipfile.ZipFile(self.path) as archive, archive.open("vectors.f32") as stream:
            for chunk in self.chunks:
                raw = stream.read(dim * 4)
                if len(raw) != dim * 4:
                    raise LibraryError("package_vector_shape_mismatch")
                chunks.append({**chunk, "vector": list(struct.unpack(f"<{dim}f", raw))})
        return {"package_id": self.manifest["package_id"], "name": self.manifest["name"],
                "version": self.manifest["version"], "source": self.manifest["source"],
                "chunking_version": self.manifest["chunking_version"],
                "documents": [{key: value for key, value in row.items() if key != "content_sha256"}
                              for row in self.documents], "chunks": chunks,
                "embedding_spec": self.manifest["embedding_spec"]}


def validate_package(path: Path, *, expected_spec: dict | None = None,
                     expected_sha256: str | None = None, expected_package_id: str | None = None,
                     expected_version: str | None = None,
                     limits: PackageLimits = DEFAULT_LIMITS) -> ValidatedPackage:
    """Validate structure, source/hash bindings and every finite normalized vector row."""
    path = Path(path)
    digest, size = hash_file(path, limits.max_archive_bytes)
    if expected_sha256 is not None and digest != _hash(expected_sha256):
        raise LibraryError("package_artifact_hash_mismatch")
    try:
        _preflight_archive(path, size)
        with zipfile.ZipFile(path) as archive:
            manifest = _manifest(archive, limits)
            if ((expected_package_id is not None and manifest["package_id"] != expected_package_id)
                    or (expected_version is not None and manifest["version"] != expected_version)):
                raise LibraryError("package_identity_mismatch")
            _check_members(archive, manifest, limits)
            documents, document_chars = [], 0
            by_id = {}
            for document in _rows(archive, "documents.jsonl", limits.max_record_bytes):
                _mapping(document, {"document_id", "name", "version", "sha256", "content", "content_sha256"})
                identity = _identity(document["document_id"])
                if identity in by_id or len(documents) >= limits.max_documents:
                    raise LibraryError("package_invalid_documents")
                _text(document["name"], 160)
                _text(document["version"], 128)
                _hash(document["sha256"])
                content = _text(document["content"], limits.max_document_chars)
                document_chars += len(content)
                if document_chars > limits.max_library_chars:
                    raise LibraryError("package_capacity_exceeded")
                if hashlib.sha256(content.encode("utf-8")).hexdigest() != _hash(document["content_sha256"]):
                    raise LibraryError("package_document_hash_mismatch")
                by_id[identity] = document
                documents.append(document)
            chunks, seen, referenced = [], set(), set()
            for chunk in _rows(archive, "chunks.jsonl", limits.max_record_bytes):
                _mapping(chunk, {"chunk_id", "document_id", "location", "text", "embedding_text", "input_sha256"})
                identity = _identity(chunk["chunk_id"])
                document_id = _identity(chunk["document_id"])
                if identity in seen or document_id not in by_id or len(chunks) >= limits.max_chunks:
                    raise LibraryError("package_invalid_chunks")
                text = _text(chunk["text"], limits.max_document_chars)
                if manifest["chunking_version"] != CHUNKING_VERSION and text not in by_id[document_id]["content"]:
                    raise LibraryError("package_source_mismatch")
                if not isinstance(chunk["location"], dict) or not chunk["location"] or len(canonical_json(chunk["location"])) > 4096:
                    raise LibraryError("package_invalid_location")
                embedding_text = _text(chunk["embedding_text"], 2000)
                if hashlib.sha256(embedding_text.encode("utf-8")).hexdigest() != _hash(chunk["input_sha256"]):
                    raise LibraryError("package_input_hash_mismatch")
                if (manifest["chunking_version"] == CHUNKING_VERSION
                        and embedding_text != by_id[document_id]["name"] + "\n" + text):
                    # A self-reported hash cannot bind unrelated text to the
                    # actual v2 document/title input. Legacy v1 stays readable.
                    raise LibraryError("package_source_mismatch")
                seen.add(identity)
                referenced.add(document_id)
                chunks.append(chunk)
            if (len(documents) != manifest["counts"]["documents"] or len(chunks) != manifest["counts"]["chunks"]
                    or referenced != set(by_id)):
                raise LibraryError("package_count_mismatch")
            if manifest["chunking_version"] == CHUNKING_VERSION:
                # Explicit offsets make completeness independently checkable
                # without the original parser, tokenizer or an O(N²) search.
                bind_source_offsets(documents, chunks, require_offsets=True)
            dim = manifest["embedding_spec"]["dimension"]
            view_dimension = (compatible_dimension(validate_specification(expected_spec, limits), manifest["embedding_spec"])
                              if expected_spec is not None else None)
            compatible = view_dimension is not None
            with archive.open("vectors.f32") as stream:
                for _chunk in chunks:
                    raw = stream.read(dim * 4)
                    if len(raw) != dim * 4:
                        raise LibraryError("package_vector_shape_mismatch")
                    values = struct.unpack(f"<{dim}f", raw)
                    if not all(math.isfinite(value) for value in values) or abs(math.sqrt(math.fsum(value * value for value in values)) - 1.0) > 0.001:
                        raise LibraryError("package_invalid_vector")
                    if view_dimension is not None and math.hypot(*values[:view_dimension]) <= 0:
                        compatible = False  # A valid full vector may have an unusable shorter prefix.
            return ValidatedPackage(path, manifest, tuple(documents), tuple(chunks), digest, size, compatible)
    except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, UnicodeError, struct.error, zlib.error) as exc:
        raise LibraryError("package_invalid_archive") from exc
