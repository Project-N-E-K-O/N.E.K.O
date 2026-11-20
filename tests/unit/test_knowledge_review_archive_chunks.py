"""Install verified archive inputs without silently invalidating their vectors."""

import struct

import pytest

from knowledge.archive import export_archive, read_archive
from knowledge.chunking import derive_chunks
from knowledge.models import canonical_pack_bytes, pack_sha256, parse_pack
from knowledge.store import KnowledgeStore, count_pack_chunks


pytestmark = pytest.mark.unit_fast


def _archive(tmp_path, *, reverse=False):
    """Model an actual export after a tokenizer-free import was backfilled."""
    spec = {
        "model_id": "local-text-retrieval-v1-64d-int8-mlen1024",
        "profile": "local-text-retrieval-v1", "dimension": 64,
        "quantization": "int8", "max_length": 1024,
        "processing_version": "neko-onnx-last-token-truncate-l2-v1",
        "weights_sha256": {key: "a" * 64 for key in ("model", "external_data", "tokenizer")},
    }
    payload = {
        "schema_version": 1, "pack_id": "archive-original-inputs", "material_type": "knowledge",
        "source": {"name": "Original inputs", "homepage": "", "license": "CC0"},
        "entries": [
            {"title": "Reference", "content": "\n\n".join(f"{index:03d} " + "x" * 696 for index in range(100))},
            {"title": "Short", "content": "The short entry also keeps its own vector."},
        ],
    }
    pack = parse_pack(payload)
    blob = struct.pack("<64f", 1.0, *([0.0] * 63))
    rows = [
        {"entry_title": entry.title, "chunk_index": chunk.chunk_index, "text_hash": chunk.text_hash,
         "embedding_text": chunk.embed_text, "vector": blob}
        for entry in pack.entries for chunk in derive_chunks(entry)
    ]
    assert len(rows) == 97  # The long entry used the legacy 96-chunk fallback.
    if reverse:
        rows.reverse()  # Valid archive row order need not match chunk_index order.
    artifact = export_archive(tmp_path / "exports", payload, specification=spec, vectors=rows)
    path = tmp_path / "exports" / (artifact["sha256"] + ".neko-knowledge.zip")
    checked = read_archive(path, expected_spec=spec)
    assert len(checked["seed_vectors"]) == 97
    return pack, path, checked, spec


@pytest.mark.parametrize("reverse", [False, True])
def test_compatible_archive_keeps_verified_inputs_and_complete_vectors(tmp_path, reverse):
    pack, _path, checked, spec = _archive(tmp_path, reverse=reverse)
    def fit(text):
        return len(text) <= 2_000
    assert count_pack_chunks(pack, fit=fit) == 101
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    installed = store.replace_pack(pack, pack_sha256=pack_sha256(canonical_pack_bytes(pack)),
                                   fit=fit, seed_vectors=checked["seed_vectors"])
    assert installed == 97
    assert count_pack_chunks(pack, fit=fit, seed_vectors=checked["seed_vectors"]) == installed
    assert store.chunk_stats(spec["model_id"], specification=spec)[pack.pack_id] == {
        "total": 97, "ready": 97, "failed": 0,
    }
    assert store.pending_chunks(model_id=spec["model_id"], specification=spec,
                                pack_ids=[pack.pack_id], limit=200) == []
    actual = {(row["entry_title"], row["chunk_index"]): row
              for row in store.vectors_for_pack(pack.pack_id, spec["model_id"])}
    expected = {(row["entry_title"], row["chunk_index"]): {
        key: row[key] for key in ("entry_title", "chunk_index", "text_hash", "embedding_text", "vector")
    } for row in checked["seed_vectors"]}
    assert actual == expected


def test_oversized_archive_entry_replans_without_discarding_other_entry_vectors(tmp_path):
    pack, _path, checked, spec = _archive(tmp_path)
    def fit(text):
        return len(text) <= 300
    planned = sum(len(derive_chunks(entry, fit=fit)) for entry in pack.entries)
    assert planned > 97
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    installed = store.replace_pack(pack, pack_sha256=pack_sha256(canonical_pack_bytes(pack)),
                                   fit=fit, seed_vectors=checked["seed_vectors"])
    assert installed == planned == count_pack_chunks(pack, fit=fit, seed_vectors=checked["seed_vectors"])
    stats = store.chunk_stats(spec["model_id"], specification=spec)[pack.pack_id]
    assert stats == {"total": planned, "ready": 1, "failed": 0}
    pending = store.pending_chunks(model_id=spec["model_id"], specification=spec,
                                   pack_ids=[pack.pack_id], limit=1_000)
    assert len(pending) == planned - 1 and all(fit(text) for _id, _hash, text in pending)


def test_incompatible_archive_uses_normal_local_chunking_without_false_ready_rows(tmp_path):
    pack, path, _checked, spec = _archive(tmp_path)
    wider = {**spec, "model_id": spec["model_id"].replace("64d", "128d"), "dimension": 128}
    checked = read_archive(path, expected_spec=wider)
    assert checked["seed_vectors"] == []
    def fit(text):
        return len(text) <= 2_000
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    installed = store.replace_pack(pack, pack_sha256=pack_sha256(canonical_pack_bytes(pack)),
                                   fit=fit, seed_vectors=checked["seed_vectors"])
    assert installed == 101
    assert count_pack_chunks(pack, fit=fit, seed_vectors=checked["seed_vectors"]) == installed
    assert store.chunk_stats(wider["model_id"], specification=wider)[pack.pack_id] == {
        "total": 101, "ready": 0, "failed": 0,
    }
