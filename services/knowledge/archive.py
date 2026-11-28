"""Export existing pack vectors and install portable archives without inference."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Callable
import unicodedata
import zipfile

from utils.knowledge_package import (
    CHUNKING_VERSION, DEFAULT_LIMITS, FORMAT_VERSION, PACKAGE_FORMAT, PACKAGE_SUFFIX,
    PackageError, canonical_json, compatible_dimension, hash_file,
    validate_package, validate_specification,
)

from .models import KnowledgePackError, canonical_pack_bytes, parse_pack

_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}")


def export_filename(name: str, *, created_at: str | None = None) -> str:
    """Keep download names readable and valid across desktop file systems."""
    # The suffix keeps this separate from the immutable hash-named storage path.
    # Use the task timestamp rather than download time, so repeated downloads
    # of a retained artifact keep the same suggested name.
    stamp = datetime.fromisoformat(created_at.replace("Z", "+00:00")) if created_at else datetime.now().astimezone()
    tail = stamp.astimezone().strftime("_%Y-%m-%d_%H-%M-%S") + PACKAGE_SUFFIX
    clean = "".join(char for char in unicodedata.normalize("NFC", name)
                    if not unicodedata.category(char).startswith("C"))
    clean = re.sub(r'[<>:"/\\|?*]+', "_", clean)
    clean = " ".join(clean.split()).strip(" ._") or "knowledge-pack"
    # Windows reserves device names even before an extension, such as CON.txt.
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])", clean.split(".", 1)[0].rstrip(" "), re.I):
        clean = "_" + clean
    # Bound UTF-8 bytes, not characters: CJK names must fit a 255-byte component
    # too. The margin also avoids platform-specific filename conversion limits.
    budget = 240 - len(tail.encode("utf-8"))
    clean = clean.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .") or "knowledge-pack"
    return clean + tail


def archive_download_filename(path: Path, *, created_at: str | None = None) -> str:
    """Name retained exports from their frozen manifest, never a mutable pack."""
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("manifest.json")
            with archive.open(info) as stream:
                raw = stream.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise ValueError("oversized manifest")
            name = json.loads(raw)["name"]
            if not isinstance(name, str):
                raise ValueError("invalid manifest name")
            stamp = created_at or datetime(*info.date_time).astimezone().isoformat()
        return export_filename(name, created_at=stamp)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        # A damaged local artifact can still be downloaded for inspection. Do
        # not reveal its hash or revive user-supplied paths as a fallback name.
        stamp = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
        return export_filename("knowledge-pack", created_at=stamp)


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
            # Validation proves coverage of the original package text. Local
            # sanitization/NFC may change that text, invalidating its inputs;
            # only unchanged entries may retain their complete vector plan.
            unchanged = {
                original["title"] for original, entry in zip(checked.pack["entries"], pack.entries)
                if original["title"] == entry.title and original.get("summary", "") == entry.summary
                and original["content"] == entry.content
            }
            with zipfile.ZipFile(checked.path) as archive, archive.open("vectors.f32") as stream:
                for chunk in checked.chunks:
                    raw = stream.read(source_dim * 4)
                    if chunk["entry_title"] not in unchanged:
                        continue  # Consume its bytes so later entry vectors stay aligned.
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
                   created_at: str | None = None,
                   check_cancelled: Callable[[], None] | None = None,
                   commit_gate: Callable[[], bool] | None = None) -> dict:
    """Freeze one complete v2 package; never load or call an encoder."""
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise KnowledgePackError("invalid_request")
    pack = parse_pack(pack_payload)
    filename = export_filename(pack.source.name, created_at=created_at)
    raw_pack = canonical_pack_bytes(pack)
    if len(raw_pack) > DEFAULT_LIMITS.max_pack_bytes:
        raise KnowledgePackError("pack_too_large")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pack-export-", dir=output_dir)
    os.close(descriptor)
    stage = Path(temporary)
    warnings = []
    try:
        if check_cancelled:
            check_cancelled()
        # Exports are complete packages. Missing vectors must never silently
        # fall back to the retired text-only JSON export.
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
        target = output_dir / (digest + PACKAGE_SUFFIX)
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
                  "filename": filename, "format_version": FORMAT_VERSION,
                  # Display metadata is frozen beside the task, not added to
                  # the archive or resolved from a later mutable pack name.
                  "name": pack.source.name, "pack_id": pack.pack_id, "version": version}
        if warnings:
            result["warnings"] = warnings
        return result
    except PackageError as error:
        raise KnowledgePackError(error.reason) from error
    finally:
        with suppress(OSError):
            stage.unlink(missing_ok=True)
