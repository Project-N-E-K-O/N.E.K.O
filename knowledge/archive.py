"""Export existing pack vectors and install portable archives without inference."""

from __future__ import annotations

from contextlib import suppress
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Callable
import zipfile

from utils.knowledge_package import (
    CHUNKING_VERSION, DEFAULT_LIMITS, FORMAT_VERSION, PACKAGE_FORMAT, PACKAGE_SUFFIX,
    PackageError, canonical_json, compatible_dimension, hash_file,
    validate_package, validate_specification,
)

from .models import KnowledgePackError, canonical_pack_bytes, parse_pack

_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}")


def read_archive(path: Path, *, expected_spec: dict | None = None) -> dict:
    """Project validated v2 text into the same pack installed by the v1 path.

    Seed only an identical processing identity at the local width or a genuine
    shorter prefix. Missing models or wider local dimensions keep text usable;
    the existing indexer can fill those vectors later without inventing zeros.
    """
    try:
        checked = validate_package(Path(path), expected_spec=expected_spec)
        pack = parse_pack(checked.pack)
        local = validate_specification(expected_spec) if expected_spec is not None else None
        source = checked.manifest["embedding_spec"]
        common = compatible_dimension(source, local)
        seeds = []
        if local is not None and common == local["dimension"] and checked.semantic_available:
            source_dim, target_dim = source["dimension"], local["dimension"]
            with zipfile.ZipFile(checked.path) as archive, archive.open("vectors.f32") as stream:
                for chunk in checked.chunks:
                    raw = stream.read(source_dim * 4)
                    values = struct.unpack(f"<{source_dim}f", raw)[:target_dim]
                    norm = math.hypot(*values)
                    if not norm:
                        continue
                    seeds.append({**chunk, "vector": struct.pack(f"<{target_dim}f", *(v / norm for v in values)),
                                  "model_id": local["model_id"], "specification": local})
        return {"pack": json.loads(canonical_pack_bytes(pack)), "seed_vectors": seeds,
                "manifest": checked.manifest, "artifact_sha256": checked.artifact_sha256}
    except PackageError as error:
        raise KnowledgePackError(error.reason) from error


def _sync(path: Path) -> None:
    """Flush through a writable handle, including Windows FlushFileBuffers."""
    with path.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _descriptor(raw: bytes) -> dict:
    """Bind each data member to its exact bytes, not a normalized variant."""
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def export_archive(output_dir: Path, pack_payload: dict, *, version: str = "1",
                   specification: dict | None = None, vectors: list[dict] | None = None,
                   format_version: int = 2, check_cancelled: Callable[[], None] | None = None,
                   commit_gate: Callable[[], bool] | None = None) -> dict:
    """Freeze a pack plus its existing vectors; never load or call an encoder."""
    if not isinstance(version, str) or not _VERSION.fullmatch(version) or type(format_version) is not int or format_version not in {1, 2}:
        raise KnowledgePackError("invalid_request")
    pack = parse_pack(pack_payload)
    raw_pack = canonical_pack_bytes(pack)
    if len(raw_pack) > DEFAULT_LIMITS.max_pack_bytes:
        raise KnowledgePackError("pack_too_large")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=".pack-export-", dir=output_dir)
    os.close(descriptor)
    stage = Path(filename)
    suffix = ".neko-knowledge.json" if format_version == 1 else PACKAGE_SUFFIX
    warnings = []
    try:
        if check_cancelled:
            check_cancelled()
        if format_version == 1:
            stage.write_bytes(raw_pack)
        else:
            if specification is None:
                raise KnowledgePackError("embedding_unavailable")
            spec = validate_specification(specification)
            if not vectors or len(vectors) > DEFAULT_LIMITS.max_chunks:
                raise KnowledgePackError("vectors_not_ready")
            dim = spec["dimension"]
            rows, size, vector_digest = [], 0, hashlib.sha256()
            for row in vectors:
                if check_cancelled:
                    check_cancelled()
                blob = row.get("vector")
                if not isinstance(blob, bytes) or len(blob) != dim * 4:
                    raise KnowledgePackError("vectors_not_ready")
                item = {key: row[key] for key in ("entry_title", "chunk_index", "text_hash", "embedding_text")}
                rows.append(canonical_json(item) + b"\n")
                vector_digest.update(blob)
                size += len(blob)
            raw_chunks = b"".join(rows)
            files = {"pack.json": _descriptor(raw_pack), "chunks.jsonl": _descriptor(raw_chunks),
                     "vectors.f32": {"bytes": size, "sha256": vector_digest.hexdigest()}}
            if sum(item["bytes"] for item in files.values()) > DEFAULT_LIMITS.max_expanded_bytes:
                raise KnowledgePackError("package_expanded_too_large")
            manifest = {"format": PACKAGE_FORMAT, "format_version": FORMAT_VERSION,
                        "package_id": pack.pack_id, "name": pack.source.name, "version": version,
                        "source": {"author": pack.source.name, "license": pack.source.license, "url": pack.source.homepage},
                        "chunking_version": CHUNKING_VERSION, "embedding_spec": spec,
                        "counts": {"documents": len(pack.entries), "chunks": len(vectors)}, "files": files}
            with zipfile.ZipFile(stage, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as archive:
                archive.writestr("manifest.json", canonical_json(manifest))
                archive.writestr("pack.json", raw_pack)
                archive.writestr("chunks.jsonl", raw_chunks)
                # Vectors remain packed float32 throughout export. Do not turn
                # a large library into millions of temporary Python floats.
                with archive.open("vectors.f32", "w") as stream:
                    for row in vectors:
                        if check_cancelled:
                            check_cancelled()
                        stream.write(row["vector"])
            validate_package(stage, expected_spec=spec)
        digest, size = hash_file(stage, DEFAULT_LIMITS.max_archive_bytes)
        if check_cancelled:
            check_cancelled()
        _sync(stage)
        target = output_dir / (digest + suffix)
        # The owner seals cancellation under its task lock only after durable
        # staging, just before publication. A cancellation accepted during
        # fsync must never publish a supposedly cancelled artifact.
        if commit_gate is not None:
            if not commit_gate():
                raise InterruptedError("cancelled")
        elif check_cancelled:
            check_cancelled()
        if target.exists():
            if target.is_symlink() or hash_file(target, DEFAULT_LIMITS.max_archive_bytes)[0] != digest:
                raise KnowledgePackError("package_artifact_hash_mismatch")
            _sync(target)
        else:
            os.replace(stage, target)
        # The artifact is already committed. Directory durability and leftover
        # cleanup cannot turn that success into a failed export task.
        if os.name != "nt":
            try:
                fd = os.open(output_dir, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                warnings.append("storage_sync_unconfirmed")
        result = {"artifact_id": digest, "sha256": digest, "bytes": size,
                  "filename": f"{pack.pack_id}.{version}{suffix}", "format_version": format_version,
                  "pack_id": pack.pack_id, "version": version}
        if warnings:
            result["warnings"] = warnings
        return result
    except PackageError as error:
        raise KnowledgePackError(error.reason) from error
    finally:
        with suppress(OSError):
            stage.unlink(missing_ok=True)
