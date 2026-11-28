"""Archive vectors must bind to the canonical text actually installed locally."""

import hashlib
import json
import struct

import pytest

from services.knowledge.archive import read_archive
from services.knowledge.chunking import derive_chunks
from services.knowledge.models import canonical_pack_bytes, pack_sha256, parse_pack
from services.knowledge.store import KnowledgeStore
from tests.knowledge_archive_fixtures import make_archive, specification
from utils.knowledge_package import canonical_json, validate_package


pytestmark = pytest.mark.unit_fast


def _mutated_archive(path, *, field=None, value=None, omit_summary=False):
    """Rehash a real ZIP with matching raw excerpts and distinct vector rows."""
    def mutate(manifest, members):
        payload = json.loads(members["pack.json"])
        first = payload["entries"][0]
        first.update(title="Changed", summary="A summary.", content="First source body.")
        if field is not None:
            first[field] = value
        if omit_summary:
            first.pop("summary")
        payload["entries"].append({"title": "Unchanged", "content": "Second source body."})
        rows = []
        for entry in payload["entries"]:
            header = entry["title"] + ("\n" + entry["summary"] if entry.get("summary") else "")
            text = header[:600] + "\n" + entry["content"]
            rows.append({"entry_title": entry["title"], "chunk_index": 0, "embedding_text": text,
                         "text_hash": hashlib.sha256(text.encode()).hexdigest()})
        members["pack.json"] = canonical_json(payload)
        members["chunks.jsonl"] = b"".join(canonical_json(row) + b"\n" for row in rows)
        members["vectors.f32"] = (struct.pack("<64f", 1.0, *([0.0] * 63))
                                  + struct.pack("<64f", 0.0, 1.0, *([0.0] * 62)))
        manifest["counts"] = {"documents": 2, "chunks": 2}
        manifest["files"] = {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                             for name, raw in members.items()}
    return make_archive(path, mutate=mutate)


@pytest.mark.parametrize("field", ["title", "summary", "content"])
@pytest.mark.parametrize("value", ["Al\x01pha", "Al<|im_start|>pha", "Cafe\u0301"])
@pytest.mark.parametrize("dimension", [32, 64])
def test_changed_canonical_text_discards_only_its_own_archive_vectors(tmp_path, field, value, dimension):
    path = _mutated_archive(tmp_path / "raw.zip", field=field, value=value)
    spec = specification(dimension)
    # The original archive is internally valid; local canonicalization changes
    # its input identity after validation, so that one entry needs fresh inputs.
    assert validate_package(path, expected_spec=spec).semantic_available
    checked = read_archive(path, expected_spec=spec)
    pack = parse_pack(checked["pack"])
    assert getattr(pack.entries[0], field) != value
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    store.replace_pack(pack, pack_sha256=pack_sha256(canonical_pack_bytes(pack)),
                       seed_vectors=checked["seed_vectors"])
    pending = store.pending_chunks(model_id=spec["model_id"], specification=spec,
                                   pack_ids=[pack.pack_id], limit=10)
    expected = derive_chunks(pack.entries[0])
    assert [(digest, text) for _id, digest, text in pending] == [
        (chunk.text_hash, chunk.embed_text) for chunk in expected
    ]
    assert [row["entry_title"] for row in checked["seed_vectors"]] == ["Unchanged"]
    snapshot = store.load_vectors(spec["model_id"], specification=spec)
    assert snapshot is not None
    # Skipping the first entry must still consume its source vector bytes.
    assert snapshot.matrix.tolist() == [[0.0, 1.0, *([0.0] * (dimension - 2))]]


@pytest.mark.parametrize("omit_summary", [False, True])
def test_unchanged_canonical_text_retains_all_archive_vectors(tmp_path, omit_summary):
    path = _mutated_archive(tmp_path / "unchanged.zip", omit_summary=omit_summary)
    checked = read_archive(path, expected_spec=specification())
    assert [row["entry_title"] for row in checked["seed_vectors"]] == ["Changed", "Unchanged"]
