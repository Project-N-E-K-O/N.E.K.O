"""Build immutable packages from frozen library records and a real encoder adapter."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext, suppress
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import struct
import tempfile
from typing import Any, Callable
import zipfile

from .models import LibraryError
from .indexing import plan_chunks
from .jobs import wait_for_embedding
from .persistence import ensure_directory, sync_directory, sync_file
from .package_validate import (
    CHUNKING_VERSION, DEFAULT_LIMITS, FORMAT_VERSION, PACKAGE_FORMAT, PACKAGE_SUFFIX,
    SUPPORTED_CHUNKING_VERSIONS,
    PackageLimits, _hash, _identity, _text, bind_source_offsets, canonical_json, hash_file,
    validate_package, validate_specification,
)


async def run_package_io(operation: Callable, *args: Any) -> Any:
    """Drain a worker before propagating cancellation and releasing its files."""
    worker = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # 取消等待不代表线程已经退出；文件及临时目录仍由该 worker 持有。
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        with suppress(BaseException):
            worker.result()
        raise


def _guarded(write_guard: Callable | None, operation: Callable, *args: Any) -> Any:
    """Hold the storage-root fence for the complete synchronous mutation."""
    with write_guard() if write_guard is not None else nullcontext():
        return operation(*args)


def _prepare(snapshot: dict, limits: PackageLimits) -> tuple[dict, list[dict], list[dict]]:
    """Copy only publishable fields; local paths, task state and cached vectors stay out."""
    try:
        manifest = {"format": PACKAGE_FORMAT, "format_version": FORMAT_VERSION,
                    "package_id": _identity(snapshot["package_id"]),
                    "name": _text(snapshot["name"], 160),
                    "version": _text(snapshot["version"], 100),
                    "source": {key: _text(snapshot["source"].get(key, ""), 2000, empty=True)
                               for key in ("author", "license", "url")},
                    "chunking_version": snapshot["chunking_version"]}
        if manifest["chunking_version"] not in SUPPORTED_CHUNKING_VERSIONS:
            raise LibraryError("package_unsupported_chunking")
        documents, chunks = [], []
        if (not isinstance(snapshot["documents"], list) or not 1 <= len(snapshot["documents"]) <= limits.max_documents
                or not isinstance(snapshot["chunks"], list) or not 1 <= len(snapshot["chunks"]) <= limits.max_chunks):
            raise LibraryError("package_capacity_exceeded")
        by_id, document_chars = {}, 0
        for row in snapshot["documents"]:
            content = _text(row["content"], limits.max_document_chars)
            document_chars += len(content)
            if document_chars > limits.max_library_chars:
                raise LibraryError("package_capacity_exceeded")
            document = {"document_id": _identity(row["document_id"]), "name": _text(row["name"], 160),
                        "version": _text(row["version"], 128), "sha256": _hash(row["sha256"]),
                        "content": content, "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
            if document["document_id"] in by_id:
                raise LibraryError("package_invalid_documents")
            by_id[document["document_id"]] = document
            documents.append(document)
        seen, referenced = set(), set()
        for row in snapshot["chunks"]:
            identity, document_id = _identity(row["chunk_id"]), _identity(row["document_id"])
            text = _text(row["text"], limits.max_document_chars)
            embedding_text = _text(row["embedding_text"], 2000)
            location = row["location"]
            if identity in seen or document_id not in by_id:
                raise LibraryError("package_invalid_chunks")
            if embedding_text != by_id[document_id]["name"] + "\n" + text:
                # New builds derive inputs from actual frozen source, not a
                # caller-provided unrelated string with a self-consistent hash.
                raise LibraryError("package_invalid_chunks")
            if not isinstance(location, dict) or not location or len(canonical_json(location)) > 4096:
                raise LibraryError("package_invalid_location")
            chunks.append({"chunk_id": identity, "document_id": document_id, "text": text,
                           "location": json.loads(canonical_json(location)),
                           "embedding_text": embedding_text,
                           "input_sha256": hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()})
            seen.add(identity)
            referenced.add(document_id)
        if referenced != set(by_id):
            raise LibraryError("package_count_mismatch")
        chunks = bind_source_offsets(documents, chunks, require_offsets=False)
        manifest["counts"] = {"documents": len(documents), "chunks": len(chunks)}
        return manifest, documents, chunks
    except (KeyError, TypeError, AttributeError) as exc:
        raise LibraryError("package_invalid_snapshot") from exc


def _write_rows(path: Path, rows: list[dict], limits: PackageLimits) -> dict:
    digest, size = hashlib.sha256(), 0
    with path.open("xb") as stream:
        for row in rows:
            raw = canonical_json(row) + b"\n"
            if len(raw) > limits.max_record_bytes:
                raise LibraryError("package_record_too_large")
            size += len(raw)
            if size > limits.max_expanded_bytes:
                raise LibraryError("package_expanded_too_large")
            stream.write(raw)
            digest.update(raw)
    return {"bytes": size, "sha256": digest.hexdigest()}


def _append_vectors(path: Path, values: list, count: int, dimension: int) -> None:
    """Keep adapter output order and reject missing, malformed or non-unit rows."""
    if not isinstance(values, list) or len(values) != count:
        raise LibraryError("package_incomplete_vectors")
    with path.open("ab") as stream:
        for vector in values:
            if not isinstance(vector, (list, tuple)) or len(vector) != dimension:
                raise LibraryError("package_incomplete_vectors")
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                raise LibraryError("package_invalid_vector")
            norm = math.sqrt(math.fsum(value * value for value in vector))
            if not math.isfinite(norm) or norm <= 0 or abs(norm - 1.0) > 0.001:
                raise LibraryError("package_invalid_vector")
            # 模型提供单位向量；这里只固定成品的 little-endian float32 编码。
            stream.write(struct.pack(f"<{dimension}f", *vector))


def _archive(path: Path, folder: Path, manifest: dict, limits: PackageLimits) -> None:
    total = sum(item["bytes"] for item in manifest["files"].values())
    raw = canonical_json(manifest)
    if len(raw) > limits.max_manifest_bytes or total + len(raw) > limits.max_expanded_bytes:
        raise LibraryError("package_expanded_too_large")
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in (*manifest["files"], "manifest.json"):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            if name == "manifest.json":
                archive.writestr(info, raw)
            else:
                with (folder / name).open("rb") as source, archive.open(info, "w") as destination:
                    shutil.copyfileobj(source, destination, length=64 * 1024)
    if path.stat().st_size > limits.max_archive_bytes:
        raise LibraryError("package_too_large")


def _publish(stage: Path, output_directory: Path, spec: dict, limits: PackageLimits,
             check_cancelled: Callable[[], None] | None, commit_gate: Callable[[], Any] | None) -> dict:
    validated = validate_package(stage, expected_spec=spec, limits=limits)
    target = output_directory / (validated.artifact_sha256 + PACKAGE_SUFFIX)
    if check_cancelled is not None:
        check_cancelled()
    sync_file(stage)
    if commit_gate is not None and commit_gate() is False:
        raise LibraryError("job_cancelled")
    warnings = []
    if target.exists():
        if target.is_symlink() or hash_file(target, limits.max_archive_bytes)[0] != validated.artifact_sha256:
            raise LibraryError("package_artifact_hash_mismatch")
        sync_file(target)
    else:
        os.replace(stage, target)
    try:
        sync_directory(output_directory)
    except OSError:
        # The immutable artifact is already visible and validated. Keep
        # that committed outcome distinct from unconfirmed durability.
        warnings.append("storage_sync_unconfirmed")
    result = {**validated.metadata(), "artifact_path": str(target)}
    if warnings:
        result["warnings"] = warnings
    return result


async def build_package(snapshot: dict, output_path: Path, embedder: Any, *,
                        expected_spec: dict | None = None,
                        limits: PackageLimits = DEFAULT_LIMITS,
                        check_cancelled: Callable[[], None] | None = None,
                        commit_gate: Callable[[], Any] | None = None,
                        write_guard: Callable | None = None) -> dict:
    """Build a complete package and retain it under its immutable SHA-256 filename.

    ``output_path.parent`` is the destination directory; the requested basename
    is never an installed identity. The encoder is host-injected and must expose
    asynchronous ``specification`` and ``embed_batch`` methods.
    """
    if embedder is None:
        raise LibraryError("embedding_unavailable")
    manifest, documents, chunks = await run_package_io(_prepare, snapshot, limits)
    spec = validate_specification(await wait_for_embedding(embedder.specification, check_cancelled=check_cancelled), limits)
    if expected_spec is not None and spec != validate_specification(expected_spec, limits):
        raise LibraryError("package_embedding_identity_changed")
    # Even legacy snapshots are replanned with the actual tokenizer before a
    # v2 artifact can be stamped complete. Failed planning publishes nothing.
    chunks = await plan_chunks(chunks, embedder, specification=spec, max_chunks=limits.max_chunks,
                               check_cancelled=check_cancelled)
    # Domain plans carry an empty vector slot; package vectors have a separate
    # binary member and must not leak into the strict chunks JSONL schema.
    chunks = [{key: value for key, value in chunk.items() if key != "vector"} for chunk in chunks]
    manifest["chunking_version"] = CHUNKING_VERSION
    manifest["counts"]["chunks"] = len(chunks)
    manifest["embedding_spec"] = spec
    output_directory = Path(output_path).parent
    await run_package_io(_guarded, write_guard, ensure_directory, output_directory)
    folder = Path(await run_package_io(_guarded, write_guard, tempfile.mkdtemp, ".building", "knowledge-package-", output_directory))
    try:
        files = {}
        for name, rows in (("documents.jsonl", documents), ("chunks.jsonl", chunks)):
            files[name] = await run_package_io(_guarded, write_guard, _write_rows, folder / name, rows, limits)
        if sum(item["bytes"] for item in files.values()) + len(chunks) * spec["dimension"] * 4 > limits.max_expanded_bytes:
            raise LibraryError("package_expanded_too_large")
        vector_path = folder / "vectors.f32"
        await run_package_io(_guarded, write_guard, vector_path.touch)
        for start in range(0, len(chunks), 4):
            if check_cancelled is not None:
                check_cancelled()
            batch = chunks[start:start + 4]
            if validate_specification(await wait_for_embedding(embedder.specification, check_cancelled=check_cancelled), limits) != spec:
                raise LibraryError("package_embedding_identity_changed")
            compute = getattr(embedder, "embed_background", None)
            texts = [row["embedding_text"] for row in batch]
            # Resume the same batch after pressure clears, leaving already
            # written vector rows and the original expected identity intact.
            vectors = await wait_for_embedding(
                lambda: (compute(texts, expected_spec=spec) if callable(compute)
                         else embedder.embed_batch(texts)),
                check_cancelled=check_cancelled,
            )
            await run_package_io(_guarded, write_guard, _append_vectors, vector_path, vectors, len(batch), spec["dimension"])
        if validate_specification(await wait_for_embedding(embedder.specification, check_cancelled=check_cancelled), limits) != spec:
            raise LibraryError("package_embedding_identity_changed")
        digest, size = await run_package_io(hash_file, vector_path, limits.max_expanded_bytes)
        files["vectors.f32"] = {"bytes": size, "sha256": digest}
        manifest["files"] = files
        stage = folder / "artifact.zip"
        await run_package_io(_guarded, write_guard, _archive, stage, folder, manifest, limits)
        return await run_package_io(_guarded, write_guard, _publish, stage, output_directory, spec, limits, check_cancelled, commit_gate)
    finally:
        # 根切换后宁可留下自己的暂存物，也不绕过写入栅栏执行删除。
        with suppress(LibraryError, OSError):
            await run_package_io(_guarded, write_guard, shutil.rmtree, folder, True)
