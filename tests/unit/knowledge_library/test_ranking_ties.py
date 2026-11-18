"""Rank fusion ties must not depend on random document or chunk identities."""

import math

import pytest

from services.knowledge_library import indexing
from services.knowledge_library.models import LibraryLimits


pytestmark = pytest.mark.unit_fast


def test_equal_fused_ranks_prefer_actual_semantic_affinity(tmp_path):
    """A short generic keyword match cannot win a tie solely by its opaque ID."""
    spec = {"dimension": 2, "model_id": "isolated-ranking-fixture"}
    documents = []
    for identity, text, vector in (
        ("a-garden", "the garden", [0.6, 0.8]),
        ("z-lighthouse", "the lighthouse stands on a rocky coast", [0.95, math.sqrt(1 - 0.95 ** 2)]),
    ):
        documents.append({"document_id": identity, "filename": identity + ".txt", "embedding_spec": spec,
                          "chunks": [{"chunk_id": identity, "text": text, "vector": vector,
                                      "location": {"paragraph": 1}}]})
    path = tmp_path / "index.sqlite"
    indexing.build_index(path, documents, LibraryLimits())
    hits = indexing.rank(path, documents, "the beacon", [1.0, 0.0], spec, 5)
    assert hits[0]["score"] == hits[1]["score"]
    assert hits[0]["chunk_id"] == "z-lighthouse"


def _document(identity, vector, spec, text="coastal navigation records"):
    """Create one isolated searchable row without native model dependencies."""
    return {"document_id": identity, "filename": identity + ".txt", "embedding_spec": spec,
            "chunks": [{"chunk_id": identity, "text": text, "vector": vector,
                        "location": {"paragraph": 1}}]}


def test_positive_semantic_candidates_below_uncalibrated_half_threshold_are_ranked(tmp_path):
    spec = {"dimension": 2, "model_id": "isolated-ranking-fixture"}
    documents = [_document("beacon", [0.4, math.sqrt(1 - 0.4 ** 2)], spec),
                 _document("harbor", [0.2, math.sqrt(1 - 0.2 ** 2)], spec)]
    path = tmp_path / "index.sqlite"
    indexing.build_index(path, documents, LibraryLimits())
    hits = indexing.rank(path, documents, "signal tower", [1.0, 0.0], spec, 5)
    assert [hit["chunk_id"] for hit in hits] == ["beacon", "harbor"]
    assert all(hit["match_type"] == "semantic" for hit in hits)


@pytest.mark.parametrize("vector,compatible", [
    ([0.0, 1.0], True), ([-0.4, math.sqrt(1 - 0.4 ** 2)], True),
    ([float("nan"), 1.0], True), ([1.0], True), ([1.0, 0.0], False),
])
def test_zero_negative_invalid_or_incompatible_vectors_are_not_semantic_candidates(tmp_path, vector, compatible):
    spec = {"dimension": 2, "model_id": "isolated-ranking-fixture"}
    document_spec = spec if compatible else {**spec, "model_id": "different-encoder"}
    documents = [_document("source", vector, document_spec)]
    path = tmp_path / "index.sqlite"
    indexing.build_index(path, documents, LibraryLimits())
    assert indexing.rank(path, documents, "signal tower", [1.0, 0.0], spec, 5) == []


def test_semantic_candidate_pool_is_bounded_before_rank_fusion(tmp_path):
    spec = {"dimension": 2, "model_id": "isolated-ranking-fixture"}
    documents = [_document(str(index), [0.4 - index * 0.001,
                           math.sqrt(1 - (0.4 - index * 0.001) ** 2)], spec)
                 for index in range(60)]
    path = tmp_path / "index.sqlite"
    indexing.build_index(path, documents, LibraryLimits())
    hits = indexing.rank(path, documents, "signal tower", [1.0, 0.0], spec, 50)
    assert [hit["chunk_id"] for hit in hits] == [str(index) for index in range(40)]


def test_keyword_match_remains_available_when_semantic_affinity_is_zero(tmp_path):
    spec = {"dimension": 2, "model_id": "isolated-ranking-fixture"}
    documents = [_document("source", [0.0, 1.0], spec, text="signal tower")]
    path = tmp_path / "index.sqlite"
    indexing.build_index(path, documents, LibraryLimits())
    hits = indexing.rank(path, documents, "signal tower", [1.0, 0.0], spec, 5)
    assert len(hits) == 1
    assert hits[0]["match_type"] == "keyword"
