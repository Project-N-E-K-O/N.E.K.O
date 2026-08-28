from __future__ import annotations

import json
from hashlib import sha256

import numpy as np
import pytest

from knowledge.chunking import derive_knowledge_chunks
from knowledge.packs import validate_pack
from knowledge.prebuilt_index import (
    MAX_PREBUILT_MANIFEST_BYTES,
    PREBUILT_DIMENSIONS,
    PREBUILT_ENCODING,
    PREBUILT_MODEL_ID,
    build_prebuilt_index_artifacts,
    canonical_prebuilt_manifest_bytes,
    validate_prebuilt_index,
)
from knowledge.subscriptions import canonical_pack_bytes


def _pack_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "pack_id": "fixture-pack",
        "material_type": "corpus",
        "source": {
            "name": "Fixture",
            "homepage": "https://example.invalid",
            "license": "CC0",
        },
        "entries": [
            {
                "title": "First entry",
                "terms": {"alias": ["first"], "recognition": ["one"]},
                "tags": ["fixture"],
                "summary": "A first summary.",
                "content": "# Origin\n\nThe first document has an answer.",
            },
            {
                "title": "Second entry",
                "terms": {"alias": [], "recognition": []},
                "tags": [],
                "summary": "",
                "content": "The second document also has an answer.",
            },
        ],
    }


def _artifacts():
    payload = _pack_payload()
    pack_artifact = canonical_pack_bytes(payload)
    pack = validate_pack(payload)
    chunks = tuple(
        chunk
        for entry in pack.entries
        for chunk in derive_knowledge_chunks(
            entry,
            entry_key=f"{entry.source_tag}:{entry.title}",
        )
    )
    records = tuple(
        {
            "chunk_id": chunk.chunk_id,
            "content_hash": chunk.content_hash,
            "model_id": PREBUILT_MODEL_ID,
            "dimensions": PREBUILT_DIMENSIONS,
            "embedding": np.full(
                PREBUILT_DIMENSIONS,
                index + 1,
                dtype="<f2",
            ).tobytes(),
        }
        for index, chunk in enumerate(chunks)
    )
    return pack_artifact, build_prebuilt_index_artifacts(pack_artifact, records)


def _validate(pack_artifact, artifacts):
    return validate_prebuilt_index(
        pack_artifact,
        artifacts.manifest,
        artifacts.vectors,
        expected_pack_sha256=artifacts.pack_sha256,
        expected_manifest_sha256=artifacts.manifest_sha256,
        expected_vectors_sha256=artifacts.vectors_sha256,
    )


def _changed_manifest(artifacts, change):
    payload = json.loads(artifacts.manifest)
    change(payload)
    manifest = canonical_prebuilt_manifest_bytes(payload)
    return manifest, sha256(manifest).hexdigest()


def test_build_and_validate_prebuilt_index_artifacts():
    pack_artifact, artifacts = _artifacts()

    validated = _validate(pack_artifact, artifacts)
    prepared = validated.prepared_embeddings()

    assert validated.pack.pack_id == "fixture-pack"
    assert len(prepared) == len(validated.chunks) == 2
    assert artifacts.manifest == canonical_prebuilt_manifest_bytes(
        json.loads(artifacts.manifest)
    )
    assert json.loads(artifacts.manifest)["vector_encoding"] == PREBUILT_ENCODING
    assert prepared[0]["embedding"] == artifacts.vectors[: PREBUILT_DIMENSIONS * 2]
    assert all(row["model_id"] == PREBUILT_MODEL_ID for row in prepared)


def test_prebuilt_index_preserves_corpus_classification():
    payload = _pack_payload()
    pack_artifact = canonical_pack_bytes(payload)
    pack = validate_pack(payload)
    chunks = tuple(
        chunk
        for entry in pack.entries
        for chunk in derive_knowledge_chunks(
            entry,
            entry_key=f"{entry.source_tag}:{entry.title}",
        )
    )
    vector = np.ones(PREBUILT_DIMENSIONS, dtype="<f2").tobytes()
    artifacts = build_prebuilt_index_artifacts(
        pack_artifact,
        tuple(
            {
                "chunk_id": chunk.chunk_id,
                "content_hash": chunk.content_hash,
                "model_id": PREBUILT_MODEL_ID,
                "dimensions": PREBUILT_DIMENSIONS,
                "embedding": vector,
            }
            for chunk in chunks
        ),
    )

    validated = _validate(pack_artifact, artifacts)

    assert validated.pack.material_type == "corpus"
    assert len(validated.prepared_embeddings()) == len(chunks)


@pytest.mark.parametrize("digest_field", ["pack", "manifest", "vectors"])
def test_validation_rejects_external_digest_mismatch(digest_field):
    pack_artifact, artifacts = _artifacts()
    kwargs = {
        "expected_pack_sha256": artifacts.pack_sha256,
        "expected_manifest_sha256": artifacts.manifest_sha256,
        "expected_vectors_sha256": artifacts.vectors_sha256,
    }
    kwargs[f"expected_{digest_field}_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="SHA-256 does not match"):
        validate_prebuilt_index(
            pack_artifact,
            artifacts.manifest,
            artifacts.vectors,
            **kwargs,
        )


def test_validation_requires_canonical_bounded_manifest():
    pack_artifact, artifacts = _artifacts()
    noncanonical = artifacts.manifest + b"\n"

    with pytest.raises(ValueError, match="not canonical JSON"):
        validate_prebuilt_index(
            pack_artifact,
            noncanonical,
            artifacts.vectors,
            expected_pack_sha256=artifacts.pack_sha256,
            expected_manifest_sha256=sha256(noncanonical).hexdigest(),
            expected_vectors_sha256=artifacts.vectors_sha256,
        )

    oversized = b"x" * (MAX_PREBUILT_MANIFEST_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds the size limit"):
        validate_prebuilt_index(
            pack_artifact,
            oversized,
            artifacts.vectors,
            expected_pack_sha256=artifacts.pack_sha256,
            expected_manifest_sha256=sha256(oversized).hexdigest(),
            expected_vectors_sha256=artifacts.vectors_sha256,
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda payload: payload["chunks"][0].update(vector_index=1),
        lambda payload: payload["chunks"][0].update(chunk_id="0" * 64),
        lambda payload: payload["chunks"][0].update(content_hash="0" * 64),
        lambda payload: payload["chunks"].reverse(),
    ],
)
def test_validation_rejects_chunk_order_or_identity_changes(change):
    pack_artifact, artifacts = _artifacts()
    manifest, manifest_digest = _changed_manifest(artifacts, change)

    with pytest.raises(ValueError, match="vector_index|identity"):
        validate_prebuilt_index(
            pack_artifact,
            manifest,
            artifacts.vectors,
            expected_pack_sha256=artifacts.pack_sha256,
            expected_manifest_sha256=manifest_digest,
            expected_vectors_sha256=artifacts.vectors_sha256,
        )


@pytest.mark.parametrize("kind", ["short", "nan", "zero"])
def test_validation_rejects_invalid_vector_rows(kind):
    pack_artifact, artifacts = _artifacts()
    if kind == "short":
        vectors = artifacts.vectors[:-2]
    else:
        matrix = (
            np.frombuffer(artifacts.vectors, dtype="<f2")
            .copy()
            .reshape(
                -1,
                PREBUILT_DIMENSIONS,
            )
        )
        matrix[0] = np.nan if kind == "nan" else 0
        vectors = matrix.astype("<f2", copy=False).tobytes()
    vectors_digest = sha256(vectors).hexdigest()
    manifest, manifest_digest = _changed_manifest(
        artifacts,
        lambda payload: payload.update(vectors_sha256=vectors_digest),
    )

    with pytest.raises(ValueError, match="byte length|finite|non-zero norm"):
        validate_prebuilt_index(
            pack_artifact,
            manifest,
            vectors,
            expected_pack_sha256=artifacts.pack_sha256,
            expected_manifest_sha256=manifest_digest,
            expected_vectors_sha256=vectors_digest,
        )


def test_validation_rejects_changed_contract_and_manifest_pack_digest():
    pack_artifact, artifacts = _artifacts()
    model_manifest, model_digest = _changed_manifest(
        artifacts,
        lambda payload: payload.update(embedding_model_id="different-model"),
    )
    with pytest.raises(ValueError, match="embedding_model_id is unsupported"):
        validate_prebuilt_index(
            pack_artifact,
            model_manifest,
            artifacts.vectors,
            expected_pack_sha256=artifacts.pack_sha256,
            expected_manifest_sha256=model_digest,
            expected_vectors_sha256=artifacts.vectors_sha256,
        )

    pack_manifest, pack_digest = _changed_manifest(
        artifacts,
        lambda payload: payload.update(pack_sha256="0" * 64),
    )
    with pytest.raises(ValueError, match="different pack"):
        validate_prebuilt_index(
            pack_artifact,
            pack_manifest,
            artifacts.vectors,
            expected_pack_sha256=artifacts.pack_sha256,
            expected_manifest_sha256=pack_digest,
            expected_vectors_sha256=artifacts.vectors_sha256,
        )
