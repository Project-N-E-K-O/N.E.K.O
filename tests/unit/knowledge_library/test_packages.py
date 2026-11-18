"""Offline package behavior, hostile inputs and inference-free recovery boundaries."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import threading
import zipfile

import pytest

from services.knowledge_library.models import LibraryError, LibraryLimits
from services.knowledge_library.package_build import build_package, run_package_io
from services.knowledge_library.package_install import get_artifact, install_package, list_packages
from services.knowledge_library.package_validate import (
    CHUNKING_VERSION, DEFAULT_LIMITS, PACKAGE_SUFFIX, PROCESSING_VERSION,
    canonical_json, validate_package,
)
from services.knowledge_library.service import LibraryService


pytestmark = [pytest.mark.unit, pytest.mark.unit_fast]


def _spec() -> dict:
    """Describe an explicit test encoder; production receives actual model file hashes."""
    return {"model_id": "local-text-retrieval-v1-64d-int8-mlen1024",
            "profile": "local-text-retrieval-v1", "dimension": 64, "quantization": "int8",
            "max_length": 1024, "processing_version": PROCESSING_VERSION,
            "weights_sha256": {name: hashlib.sha256(name.encode()).hexdigest()
                               for name in ("model", "external_data", "tokenizer")}}


class Encoder:
    """Deterministic normalized test vectors, never a production fallback encoder."""

    def __init__(self):
        self.calls = []
        self.spec = _spec()

    async def specification(self):
        return deepcopy(self.spec)

    async def plan_inputs(self, inputs, **_kwargs):
        """Treat these deliberately short fixtures as one validated token span."""
        return [[{"start": 0, "end": len(row["body"])}] for row in inputs]

    async def embed_batch(self, texts, **_kwargs):
        self.calls.append(list(texts))
        result = []
        for text in texts:
            row = [0.0] * 64
            row[hashlib.sha256(text.encode()).digest()[0] % 64] = 1.0
            result.append(row)
        return result


def _snapshot(version="1") -> dict:
    documents, chunks = [], []
    for index, text in enumerate(("A private note about a blue lighthouse.", "A second note about orange trees.")):
        identity = f"document-{index}"
        documents.append({"document_id": identity, "name": f"note-{index}.txt", "version": "doc-version-1",
                          "sha256": hashlib.sha256(text.encode()).hexdigest(), "content": text})
        chunks.append({"chunk_id": f"chunk-{index}", "document_id": identity,
                       "location": {"paragraph": 1}, "text": text,
                       "embedding_text": f"note-{index}.txt\n{text}"})
    return {"snapshot_id": "not-public-task-identity", "library_id": "private-library",
            "package_id": "stable-work", "name": "Selected notes", "version": version,
            "source": {"author": "Author", "license": "CC0-1.0", "url": ""},
            "chunking_version": CHUNKING_VERSION, "documents": documents, "chunks": chunks}


async def _built(tmp_path, snapshot=None, encoder=None):
    return await build_package(snapshot or _snapshot(), tmp_path / "artifacts" / "requested.zip", encoder or Encoder())


def _rewrite(path: Path, target: Path, *, member=None, transform=None, extra=None, manifest_raw=None):
    """Rewrite tiny controlled fixtures while keeping unrelated manifest hashes valid."""
    with zipfile.ZipFile(path) as archive:
        records = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(records["manifest.json"])
    if member is not None:
        records[member] = transform(records[member])
        manifest["files"][member] = {"bytes": len(records[member]),
                                      "sha256": hashlib.sha256(records[member]).hexdigest()}
    records["manifest.json"] = manifest_raw or canonical_json(manifest)
    if extra:
        records.update(extra)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in records.items():
            archive.writestr(name, raw)
    return target


@pytest.mark.asyncio
async def test_build_is_complete_deterministic_and_excludes_private_state(tmp_path):
    encoder = Encoder()
    first = await _built(tmp_path, encoder=encoder)
    second = await _built(tmp_path, encoder=Encoder())
    assert first["sha256"] == second["sha256"]
    assert Path(first["artifact_path"]).name == first["sha256"] + PACKAGE_SUFFIX
    checked = validate_package(Path(first["artifact_path"]), expected_spec=_spec())
    assert checked.semantic_available is True
    assert checked.manifest["counts"] == {"documents": 2, "chunks": 2}
    assert len(encoder.calls) == 1
    assert "snapshot_id" not in checked.manifest and "library_id" not in checked.manifest
    assert checked.to_snapshot()["chunks"][0]["text"] == _snapshot()["chunks"][0]["text"]
    assert set(list_packages(tmp_path)[0]) >= {"artifact_id", "name", "version", "sha256", "size", "embedding_spec"}


@pytest.mark.asyncio
async def test_missing_encoder_or_incomplete_vectors_never_produces_a_package(tmp_path):
    with pytest.raises(LibraryError, match="embedding_unavailable"):
        await build_package(_snapshot(), tmp_path / "none.zip", None)
    encoder = Encoder()

    async def incomplete(texts):
        return [None] * len(texts)

    encoder.embed_batch = incomplete
    with pytest.raises(LibraryError, match="package_incomplete_vectors"):
        await _built(tmp_path, encoder=encoder)
    assert not list(tmp_path.rglob("*" + PACKAGE_SUFFIX))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["nan", "zero", "dimension", "not-normalized"])
async def test_encoder_cannot_publish_invalid_vector_rows(tmp_path, kind):
    encoder = Encoder()

    async def malformed(texts):
        row = [0.0] * (63 if kind == "dimension" else 64)
        if kind != "zero":
            row[0] = float("nan") if kind == "nan" else (2.0 if kind == "not-normalized" else 1.0)
        return [row for _text in texts]

    encoder.embed_batch = malformed
    with pytest.raises(LibraryError):
        await _built(tmp_path, encoder=encoder)
    assert not list(tmp_path.rglob("*" + PACKAGE_SUFFIX))


@pytest.mark.asyncio
async def test_encoder_identity_must_remain_frozen_during_build(tmp_path):
    encoder = Encoder()
    original = encoder.embed_batch

    async def changed(texts):
        rows = await original(texts)
        encoder.spec["weights_sha256"]["model"] = "f" * 64
        return rows

    encoder.embed_batch = changed
    with pytest.raises(LibraryError, match="package_embedding_identity_changed"):
        await _built(tmp_path, encoder=encoder)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["nul\x00version", "surrogate\ud800version"])
async def test_manifest_text_must_be_portable_utf8_without_nul(tmp_path, invalid):
    """Reject JSON escapes that cannot survive the marketplace database boundary."""
    built = await _built(tmp_path / "author")
    with zipfile.ZipFile(built["artifact_path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["version"] = invalid
    target = _rewrite(Path(built["artifact_path"]), tmp_path / "invalid.zip",
                      manifest_raw=json.dumps(manifest, ensure_ascii=True).encode())
    with pytest.raises(LibraryError, match="package_invalid_text"):
        validate_package(target)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["deflate", "filename-utf8"])
async def test_broken_archive_bytes_are_controlled_validation_failures(tmp_path, damage):
    """ZIP decoder failures must not escape the package admission error contract."""
    built = await _built(tmp_path / "author")
    path = Path(built["artifact_path"])
    raw = bytearray(path.read_bytes())
    if damage == "deflate":
        with zipfile.ZipFile(path) as archive:
            header = archive.getinfo("documents.jsonl").header_offset
        name_size, extra_size = struct.unpack_from("<HH", raw, header + 26)
        raw[header + 30 + name_size + extra_size] = 0xff
    else:
        central = raw.index(b"PK\x01\x02")
        flags = struct.unpack_from("<H", raw, central + 8)[0] | 0x800
        struct.pack_into("<H", raw, central + 8, flags)
        raw[central + 46] = 0xff
    target = tmp_path / "corrupt.zip"
    target.write_bytes(raw)
    with pytest.raises(LibraryError, match="package_invalid_archive"):
        validate_package(target)


@pytest.mark.asyncio
async def test_aggregate_document_bound_matches_library_and_checks_before_embedding(tmp_path):
    """A package admitted by default must fit the existing aggregate library bound."""
    assert DEFAULT_LIMITS.max_library_chars == LibraryLimits().max_library_chars
    snapshot = _snapshot()
    total = sum(len(row["content"]) for row in snapshot["documents"])
    limits = replace(DEFAULT_LIMITS, max_library_chars=total)
    built = await build_package(snapshot, tmp_path / "exact" / "pack.zip", Encoder(), limits=limits)
    validate_package(Path(built["artifact_path"]), limits=limits)
    with pytest.raises(LibraryError, match="package_capacity_exceeded"):
        validate_package(Path(built["artifact_path"]), limits=replace(limits, max_library_chars=total - 1))
    encoder = Encoder()
    with pytest.raises(LibraryError, match="package_capacity_exceeded"):
        await build_package(snapshot, tmp_path / "oversized" / "pack.zip", encoder,
                            limits=replace(limits, max_library_chars=total - 1))
    assert encoder.calls == []


@pytest.mark.asyncio
async def test_install_and_recovery_need_no_encoder_and_keep_the_exact_artifact(tmp_path):
    built = await _built(tmp_path / "author")
    consumer = tmp_path / "consumer"
    prepared = await install_package(Path(built["artifact_path"]), consumer, expected_spec=_spec())
    repeated = await install_package(Path(built["artifact_path"]), consumer, expected_spec=_spec())
    assert prepared["installation"] == repeated["installation"]
    retained = get_artifact(consumer, built["artifact_id"])
    Path(built["artifact_path"]).unlink()
    recovered = validate_package(retained, expected_sha256=built["sha256"]).to_snapshot()
    assert recovered == prepared["snapshot"]
    assert recovered["embedding_spec"] == _spec()
    assert all(len(chunk["vector"]) == 64 for chunk in recovered["chunks"])


@pytest.mark.asyncio
async def test_same_dimension_different_weights_is_keyword_only_not_reembedded(tmp_path):
    built = await _built(tmp_path / "author")
    other_spec = _spec()
    other_spec["weights_sha256"]["model"] = "e" * 64
    result = await install_package(Path(built["artifact_path"]), tmp_path / "consumer", expected_spec=other_spec)
    assert result["semantic_available"] is False
    assert result["installation"]["semantic_available"] is False
    assert result["snapshot"]["embedding_spec"] == _spec()
    assert result["snapshot"]["chunks"][0]["vector"]


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_name", ["../outside", "/absolute", "C:\\escape", "hook.py"])
async def test_unlisted_paths_and_code_members_are_rejected_without_extraction(tmp_path, extra_name):
    built = await _built(tmp_path)
    hostile = _rewrite(Path(built["artifact_path"]), tmp_path / "hostile.zip", extra={extra_name: b"untrusted"})
    with pytest.raises(LibraryError):
        await install_package(hostile, tmp_path / "consumer")
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "consumer" / "artifacts").exists()


@pytest.mark.asyncio
async def test_member_hashes_do_not_replace_embedding_input_and_source_bindings(tmp_path):
    built = await _built(tmp_path)

    def wrong_input(raw):
        rows = [json.loads(line) for line in raw.splitlines()]
        rows[0]["embedding_text"] = "A substituted embedding input"
        return b"".join(canonical_json(row) + b"\n" for row in rows)

    hostile = _rewrite(Path(built["artifact_path"]), tmp_path / "input.zip", member="chunks.jsonl", transform=wrong_input)
    with pytest.raises(LibraryError, match="package_input_hash_mismatch"):
        validate_package(hostile)

    def wrong_source(raw):
        rows = [json.loads(line) for line in raw.splitlines()]
        rows[0]["text"] = "Not contained in the saved document"
        return b"".join(canonical_json(row) + b"\n" for row in rows)

    hostile = _rewrite(Path(built["artifact_path"]), tmp_path / "source.zip", member="chunks.jsonl", transform=wrong_source)
    with pytest.raises(LibraryError, match="package_source_mismatch"):
        validate_package(hostile)


@pytest.mark.asyncio
async def test_tampered_vector_and_duplicate_json_keys_are_rejected(tmp_path):
    built = await _built(tmp_path)
    source = Path(built["artifact_path"])
    bad_vector = _rewrite(source, tmp_path / "vector.zip", member="vectors.f32",
                          transform=lambda raw: struct.pack("<f", float("nan")) + raw[4:])
    with pytest.raises(LibraryError, match="package_invalid_vector"):
        validate_package(bad_vector)
    with zipfile.ZipFile(source) as archive:
        manifest = archive.read("manifest.json")
    duplicated = b'{"format":"ignored",' + manifest[1:]
    bad_json = _rewrite(source, tmp_path / "json.zip", manifest_raw=duplicated)
    with pytest.raises(LibraryError, match="package_duplicate_key"):
        validate_package(bad_json)


@pytest.mark.asyncio
async def test_archive_and_expansion_limits_fail_before_installation(tmp_path):
    built = await _built(tmp_path)
    path = Path(built["artifact_path"])
    with pytest.raises(LibraryError, match="package_too_large"):
        validate_package(path, limits=replace(DEFAULT_LIMITS, max_archive_bytes=path.stat().st_size - 1))
    with pytest.raises(LibraryError, match="package_expanded_too_large"):
        validate_package(path, limits=replace(DEFAULT_LIMITS, max_expanded_bytes=100))
    with pytest.raises(LibraryError, match="package_record_too_large"):
        validate_package(path, limits=replace(DEFAULT_LIMITS, max_record_bytes=50))
    with pytest.raises(LibraryError, match="package_identity_mismatch"):
        await install_package(path, tmp_path / "consumer", expected_package_id="different-work")
    assert not (tmp_path / "consumer" / "artifacts").exists()


@pytest.mark.asyncio
async def test_validator_streams_members_instead_of_reading_a_whole_archive_member(tmp_path, monkeypatch):
    built = await _built(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("whole-member read is not permitted")

    monkeypatch.setattr(zipfile.ZipFile, "read", forbidden)
    original_read = zipfile.ZipExtFile.read

    def bounded_read(self, n=-1):
        assert n >= 0, "unbounded zip member read"
        return original_read(self, n)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", bounded_read)
    validated = validate_package(Path(built["artifact_path"]))
    assert len(validated.to_snapshot()["chunks"]) == 2


@pytest.mark.asyncio
async def test_cancellation_waits_for_file_worker_before_releasing_ownership():
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def worker():
        entered.set()
        assert release.wait(timeout=5)
        exited.set()

    task = asyncio.create_task(run_package_io(worker))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not exited.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()


@pytest.mark.asyncio
async def test_storage_write_guard_refusal_publishes_no_artifact(tmp_path):
    @contextmanager
    def denied():
        raise LibraryError("storage_root_changed")
        yield  # pragma: no cover - declare the rejected context-manager shape

    with pytest.raises(LibraryError, match="storage_root_changed"):
        await build_package(_snapshot(), tmp_path / "author" / "build.zip", Encoder(), write_guard=denied)
    assert not (tmp_path / "author").exists()
    built = await _built(tmp_path / "author")
    with pytest.raises(LibraryError, match="storage_root_changed"):
        await install_package(Path(built["artifact_path"]), tmp_path / "consumer", write_guard=denied)
    assert not (tmp_path / "consumer").exists()


@pytest.mark.asyncio
async def test_domain_commit_is_single_authority_and_failed_update_keeps_old_vectors(tmp_path, monkeypatch):
    root = tmp_path / "consumer"
    built = await _built(tmp_path / "author")
    prepared = await install_package(Path(built["artifact_path"]), root, expected_spec=_spec())
    service = LibraryService(root)
    await service.start()
    try:
        installed = await service.install_snapshot(prepared["snapshot"], installation=prepared["installation"])
        identifier = installed["library_id"]
        assert await service.install_snapshot(prepared["snapshot"], installation=prepared["installation"]) == installed
        manifest = service.store.manifest(identifier)
        original = await service.get_library(identifier)
        service.store._index_path(manifest).unlink()
        assert (await service.search(identifier, "lighthouse"))["hits"]

        altered = _snapshot()
        altered["name"] = "Changed bytes, same published version"
        conflict = await _built(tmp_path / "conflict", altered)
        conflict = await install_package(Path(conflict["artifact_path"]), root)
        with pytest.raises(LibraryError, match="immutable_version_conflict"):
            await service.install_snapshot(conflict["snapshot"], installation=conflict["installation"])

        update = await _built(tmp_path / "update", _snapshot(version="2"))
        update = await install_package(Path(update["artifact_path"]), root)

        def fail_commit(_manifest):
            raise OSError("simulated manifest storage failure")

        monkeypatch.setattr(service.store, "_write_manifest", fail_commit)
        with pytest.raises(OSError, match="simulated"):
            await service.install_snapshot(update["snapshot"], installation=update["installation"], library_id=identifier)
        assert await service.get_library(identifier) == original
        assert (await service.search(identifier, "lighthouse"))["hits"]
        assert service.store.documents(service.store.manifest(identifier))[0]["chunks"][0]["vector"] == prepared["snapshot"]["chunks"][0]["vector"]
    finally:
        await service.close()
