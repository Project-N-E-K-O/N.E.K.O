"""A zero vector prefix disables archive seeding without dropping source tails."""

import hashlib
import json
import struct

import pytest

from knowledge.archive import read_archive
from knowledge.models import canonical_pack_bytes, pack_sha256, parse_pack
from knowledge.store import KnowledgeStore, count_pack_chunks
from tests.unit.test_knowledge_archive_v2 import make_archive, specification
from utils.knowledge_package import canonical_json, validate_package


pytestmark = pytest.mark.unit_fast


def _two_chunk_archive(path, *, zero_prefix_at=None, normalized_body=False):
    """Write valid 128d rows whose short prefix may lose only one unit vector."""
    def mutate(manifest, members):
        payload = json.loads(members["pack.json"])
        first = "A violet lighthouse marks the coast."
        if normalized_body:
            first += "\x01"
        bodies = [first, "The final paragraph describes a copper bell."]
        payload["entries"][0]["content"] = "\n\n".join(bodies)
        rows, vectors = [], []
        for index, body in enumerate(bodies):
            text = "Example\n" + body
            rows.append({"entry_title": "Example", "chunk_index": index, "embedding_text": text,
                         "text_hash": hashlib.sha256(text.encode()).hexdigest()})
            values = [0.0] * 128
            values[96 if zero_prefix_at == index else index] = 1.0
            vectors.append(struct.pack("<128f", *values))
        members["pack.json"] = canonical_json(payload)
        members["chunks.jsonl"] = b"".join(canonical_json(row) + b"\n" for row in rows)
        members["vectors.f32"] = b"".join(vectors)
        manifest["embedding_spec"] = specification(128)
        manifest["counts"]["chunks"] = 2
        manifest["files"] = {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                             for name, raw in members.items()}
    return make_archive(path, mutate=mutate)


@pytest.mark.parametrize("dimension", [32, 64, 128])
@pytest.mark.parametrize("zero_prefix_at", [None, 0, 1])
def test_zero_prefix_cannot_produce_a_partial_entry_seed_plan(tmp_path, dimension, zero_prefix_at):
    path = _two_chunk_archive(tmp_path / "prefix.zip", zero_prefix_at=zero_prefix_at)
    local = specification(dimension)
    validated = validate_package(path, expected_spec=local)
    reusable = dimension == 128 or zero_prefix_at is None
    assert len(validated.chunks) == 2
    # One unusable prefix disables semantic reuse for the whole package,
    # before read_archive can enter its individual-vector conversion loop.
    assert validated.semantic_available is reusable
    checked = read_archive(path, expected_spec=local)
    assert [row["chunk_index"] for row in checked["seed_vectors"]] == ([0, 1] if reusable else [])
    pack = parse_pack(checked["pack"])
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    installed = store.replace_pack(pack, pack_sha256=pack_sha256(canonical_pack_bytes(pack)),
                                   seed_vectors=checked["seed_vectors"])
    assert installed == count_pack_chunks(pack, seed_vectors=checked["seed_vectors"]) == (2 if reusable else 1)
    stats = store.chunk_stats(local["model_id"], specification=local)[pack.pack_id]
    assert stats == {"total": installed, "ready": installed if reusable else 0, "failed": 0}
    pending = store.pending_chunks(model_id=local["model_id"], specification=local,
                                   pack_ids=[pack.pack_id], limit=10)
    if reusable:
        assert pending == []
    else:
        # Local fallback may combine the two short excerpts, but its pending
        # input contains the entire source, including the review's lost tail.
        assert len(pending) == 1
        assert "A violet lighthouse" in pending[0][2]
        assert "The final paragraph describes a copper bell." in pending[0][2]


def test_canonicalization_filters_the_entire_entry_instead_of_a_chunk_prefix(tmp_path):
    path = _two_chunk_archive(tmp_path / "normalized.zip", normalized_body=True)
    local = specification(64)
    assert validate_package(path, expected_spec=local).semantic_available
    checked = read_archive(path, expected_spec=local)
    # Only the first excerpt changed, but neither archived chunk remains a
    # seed: canonicalization must preserve whole-entry grouping as well.
    assert checked["seed_vectors"] == []
    assert "\x01" not in checked["pack"]["entries"][0]["content"]
