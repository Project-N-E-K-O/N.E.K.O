"""Install verified archive inputs without silently invalidating their vectors.

Legacy archive construction is shared with backfill recovery through the
non-collected archive fixture module; test cases remain owned here.
"""

import struct

import pytest

from services.knowledge.archive import export_archive, read_archive
from services.knowledge.chunking import derive_chunks
from services.knowledge.models import canonical_pack_bytes, pack_sha256, parse_pack
from services.knowledge.store import KnowledgeStore, count_pack_chunks
from tests.knowledge_archive_fixtures import make_chunk_plan_archive as _archive


pytestmark = pytest.mark.unit_fast


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


def test_a_zero_prefix_vector_disables_archive_reuse_for_the_whole_package(tmp_path):
    """A 128-d chunk whose first 64 values are zero cannot leave a partial plan that drops its text.

    The package validator marks such a package semantically incompatible for a
    64-d encoder, so no entry offers seeds (and thus no archive plan); every
    entry is re-chunked locally and backfilled, with all text kept.
    """
    local = {
        "model_id": "local-text-retrieval-v1-64d-int8-mlen1024",
        "profile": "local-text-retrieval-v1", "dimension": 64,
        "quantization": "int8", "max_length": 1024,
        "processing_version": "neko-onnx-last-token-truncate-l2-v1",
        "weights_sha256": {key: "a" * 64 for key in ("model", "external_data", "tokenizer")},
    }
    source = {**local, "model_id": "local-text-retrieval-v1-128d-int8-mlen1024", "dimension": 128}
    payload = {
        "schema_version": 1, "pack_id": "archive-prefix", "material_type": "knowledge",
        "source": {"name": "Prefix", "homepage": "", "license": "CC0"},
        "entries": [
            {"title": "Split", "content": "Head paragraph." + "\n\n" + "Tail paragraph that must stay searchable."},
            {"title": "Kept", "content": "An entry whose vector survives the prefix."},
        ],
    }
    pack = parse_pack(payload)
    head = struct.pack("<128f", 1.0, *([0.0] * 127))
    tail = struct.pack("<128f", *([0.0] * 64), 1.0, *([0.0] * 63))

    def fit(text):
        return len(text) <= 30  # Split "Split" into a head and tail chunk.

    rows = []
    for entry in pack.entries:
        for chunk in derive_chunks(entry, fit=fit):
            last = entry.title == "Split" and chunk.chunk_index > 0
            rows.append({"entry_title": entry.title, "chunk_index": chunk.chunk_index, "text_hash": chunk.text_hash,
                         "embedding_text": chunk.embed_text, "vector": tail if last else head})
    artifact = export_archive(tmp_path / "exports", payload, specification=source, vectors=rows)
    path = tmp_path / "exports" / (artifact["sha256"] + ".neko-knowledge.zip")
    assert read_archive(path, expected_spec=source)["seed_vectors"]  # Usable at full dimension.
    checked = read_archive(path, expected_spec=local)
    assert checked["seed_vectors"] == []
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    # Install with the same split: a partial archive plan for "Split" (head
    # only) would leave no row carrying its tail paragraph.
    store.replace_pack(pack, pack_sha256=pack_sha256(canonical_pack_bytes(pack)), fit=fit,
                       seed_vectors=checked["seed_vectors"])
    texts = [text for _id, _hash, text in store.pending_chunks(model_id=local["model_id"], specification=local,
                                                                 pack_ids=[pack.pack_id], limit=50)]
    split = [text for text in texts if text.startswith("Split\n")]
    assert len(split) >= 2 and "".join(text.split("\n", 1)[1] for text in split).replace(" ", "").endswith(
        "searchable.")
