"""Pure validation for trusted, prebuilt community knowledge vectors."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping, Sequence

import numpy as np

from .chunking import CHUNKER_VERSION, EMBEDDING_INPUT_VERSION, derive_knowledge_chunks
from .limits import (
    MAX_PACK_BYTES,
    MAX_PREBUILT_CHUNKS,
    MAX_PREBUILT_MANIFEST_BYTES,
    MAX_PREBUILT_VECTOR_BYTES,
    PREBUILT_DIMENSIONS,
    PREBUILT_VECTOR_ROW_BYTES,
)
from .packs import KnowledgePack, pack_payload, validate_pack
from .subscriptions import canonical_pack_bytes, load_canonical_pack_artifact


PREBUILT_INDEX_SCHEMA_VERSION = 1
PREBUILT_MODEL_ID = "local-text-retrieval-v1-256d-int8-mlen1024"
PREBUILT_ENCODING = "float16-le-row-major"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = frozenset(
    {
        "index_schema_version",
        "pack_id",
        "pack_sha256",
        "embedding_model_id",
        "embedding_input_version",
        "chunker_version",
        "embedding_dimensions",
        "vector_encoding",
        "vectors_sha256",
        "chunk_count",
        "chunks",
    }
)
_CHUNK_KEYS = frozenset({"chunk_id", "content_hash", "vector_index"})


@dataclass(frozen=True, slots=True)
class PrebuiltChunkReference:
    chunk_id: str
    content_hash: str
    vector_index: int


@dataclass(frozen=True, slots=True)
class ValidatedPrebuiltIndex:
    pack: KnowledgePack
    chunks: tuple[PrebuiltChunkReference, ...]
    vectors: bytes
    pack_sha256: str
    manifest_sha256: str
    vectors_sha256: str

    def prepared_embeddings(self) -> tuple[dict[str, object], ...]:
        """Return validated records accepted by the knowledge staging store."""
        return tuple(
            {
                "chunk_id": chunk.chunk_id,
                "content_hash": chunk.content_hash,
                "model_id": PREBUILT_MODEL_ID,
                "dimensions": PREBUILT_DIMENSIONS,
                "embedding": self.vectors[
                    chunk.vector_index * PREBUILT_VECTOR_ROW_BYTES : (
                        chunk.vector_index + 1
                    )
                    * PREBUILT_VECTOR_ROW_BYTES
                ],
            }
            for chunk in self.chunks
        )


@dataclass(frozen=True, slots=True)
class PrebuiltIndexArtifacts:
    manifest: bytes
    vectors: bytes
    pack_sha256: str
    manifest_sha256: str
    vectors_sha256: str


def canonical_prebuilt_manifest_bytes(payload: object) -> bytes:
    """Encode a prebuilt-index manifest in its sole accepted JSON form."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_prebuilt_index_artifacts(
    pack_artifact: bytes,
    embedding_records: Sequence[Mapping[str, object]],
) -> PrebuiltIndexArtifacts:
    """Build canonical publication artifacts from already generated vectors."""
    pack, pack_digest, expected_chunks = _load_pack_and_chunks(pack_artifact)
    if len(embedding_records) != len(expected_chunks):
        raise ValueError("prebuilt index must contain every derived chunk exactly once")

    vector_rows: list[bytes] = []
    chunk_rows: list[dict[str, object]] = []
    for vector_index, (record, expected) in enumerate(
        zip(embedding_records, expected_chunks, strict=True)
    ):
        if str(record.get("chunk_id") or "") != expected.chunk_id:
            raise ValueError("prebuilt chunk order or chunk_id does not match the pack")
        if str(record.get("content_hash") or "") != expected.content_hash:
            raise ValueError("prebuilt chunk content_hash does not match the pack")
        if str(record.get("model_id") or "") != PREBUILT_MODEL_ID:
            raise ValueError("prebuilt vector model_id is unsupported")
        if _strict_int(record.get("dimensions"), "dimensions") != PREBUILT_DIMENSIONS:
            raise ValueError("prebuilt vector dimensions are unsupported")
        embedding = record.get("embedding")
        if (
            not isinstance(embedding, bytes)
            or len(embedding) != PREBUILT_VECTOR_ROW_BYTES
        ):
            raise ValueError("prebuilt vector row has an invalid byte length")
        vector_rows.append(embedding)
        chunk_rows.append(
            {
                "chunk_id": expected.chunk_id,
                "content_hash": expected.content_hash,
                "vector_index": vector_index,
            }
        )

    vectors = b"".join(vector_rows)
    _validate_vectors(vectors, len(expected_chunks))
    vectors_digest = _digest(vectors)
    manifest_payload = {
        "index_schema_version": PREBUILT_INDEX_SCHEMA_VERSION,
        "pack_id": pack.pack_id,
        "pack_sha256": pack_digest,
        "embedding_model_id": PREBUILT_MODEL_ID,
        "embedding_input_version": EMBEDDING_INPUT_VERSION,
        "chunker_version": CHUNKER_VERSION,
        "embedding_dimensions": PREBUILT_DIMENSIONS,
        "vector_encoding": PREBUILT_ENCODING,
        "vectors_sha256": vectors_digest,
        "chunk_count": len(expected_chunks),
        "chunks": chunk_rows,
    }
    manifest = canonical_prebuilt_manifest_bytes(manifest_payload)
    if len(manifest) > MAX_PREBUILT_MANIFEST_BYTES:
        raise ValueError("prebuilt index manifest exceeds the size limit")
    return PrebuiltIndexArtifacts(
        manifest=manifest,
        vectors=vectors,
        pack_sha256=pack_digest,
        manifest_sha256=_digest(manifest),
        vectors_sha256=vectors_digest,
    )


def validate_prebuilt_index(
    pack_artifact: bytes,
    manifest_artifact: bytes,
    vectors_artifact: bytes,
    *,
    expected_pack_sha256: str,
    expected_manifest_sha256: str,
    expected_vectors_sha256: str,
) -> ValidatedPrebuiltIndex:
    """Strictly validate untrusted prebuilt artifacts without filesystem writes."""
    expected_pack_digest = _required_digest(expected_pack_sha256, "pack")
    expected_manifest_digest = _required_digest(expected_manifest_sha256, "manifest")
    expected_vectors_digest = _required_digest(expected_vectors_sha256, "vectors")

    pack, pack_digest, expected_chunks = _load_pack_and_chunks(pack_artifact)
    if pack_digest != expected_pack_digest:
        raise ValueError("prebuilt index pack SHA-256 does not match")
    if len(manifest_artifact) > MAX_PREBUILT_MANIFEST_BYTES:
        raise ValueError("prebuilt index manifest exceeds the size limit")
    if _digest(manifest_artifact) != expected_manifest_digest:
        raise ValueError("prebuilt index manifest SHA-256 does not match")
    if len(vectors_artifact) > MAX_PREBUILT_VECTOR_BYTES:
        raise ValueError("prebuilt vector artifact exceeds the size limit")
    if _digest(vectors_artifact) != expected_vectors_digest:
        raise ValueError("prebuilt vector SHA-256 does not match")

    payload = _load_canonical_manifest(manifest_artifact)
    _require_exact_keys(payload, _MANIFEST_KEYS, "prebuilt index manifest")
    _require_fixed_contract(payload)
    if payload.get("pack_id") != pack.pack_id:
        raise ValueError("prebuilt manifest refers to a different pack_id")
    if _required_digest(payload.get("pack_sha256"), "manifest pack") != pack_digest:
        raise ValueError("prebuilt manifest refers to a different pack")
    manifest_vectors_digest = _required_digest(
        payload.get("vectors_sha256"),
        "manifest vectors",
    )
    if manifest_vectors_digest != expected_vectors_digest:
        raise ValueError("prebuilt manifest refers to different vectors")

    chunk_count = _strict_int(payload.get("chunk_count"), "chunk_count")
    if chunk_count != len(expected_chunks) or chunk_count > MAX_PREBUILT_CHUNKS:
        raise ValueError("prebuilt chunk_count does not match the pack")
    chunk_payloads = payload.get("chunks")
    if not isinstance(chunk_payloads, list) or len(chunk_payloads) != chunk_count:
        raise ValueError("prebuilt chunks do not match chunk_count")

    references: list[PrebuiltChunkReference] = []
    for position, (row, expected) in enumerate(
        zip(chunk_payloads, expected_chunks, strict=True)
    ):
        if not isinstance(row, dict):
            raise ValueError("prebuilt chunk reference must be an object")
        _require_exact_keys(row, _CHUNK_KEYS, "prebuilt chunk reference")
        chunk_id = _required_digest(row.get("chunk_id"), "chunk_id")
        content_hash = _required_digest(row.get("content_hash"), "content_hash")
        vector_index = _strict_int(row.get("vector_index"), "vector_index")
        if vector_index != position:
            raise ValueError("prebuilt vector_index must match chunk order")
        if chunk_id != expected.chunk_id or content_hash != expected.content_hash:
            raise ValueError("prebuilt chunk identity does not match the pack")
        references.append(PrebuiltChunkReference(chunk_id, content_hash, vector_index))

    _validate_vectors(vectors_artifact, chunk_count)
    return ValidatedPrebuiltIndex(
        pack=pack,
        chunks=tuple(references),
        vectors=vectors_artifact,
        pack_sha256=pack_digest,
        manifest_sha256=expected_manifest_digest,
        vectors_sha256=expected_vectors_digest,
    )


def _load_pack_and_chunks(pack_artifact: bytes):
    if len(pack_artifact) > MAX_PACK_BYTES:
        raise ValueError("knowledge pack exceeds the size limit")
    pack = validate_pack(load_canonical_pack_artifact(pack_artifact))
    if canonical_pack_bytes(pack_payload(pack)) != pack_artifact:
        raise ValueError("knowledge pack is not validation-stable canonical JSON")
    chunks = tuple(
        chunk
        for entry in pack.entries
        for chunk in derive_knowledge_chunks(
            entry,
            entry_key=f"{entry.source_tag}:{entry.title}",
        )
    )
    if not chunks or len(chunks) > MAX_PREBUILT_CHUNKS:
        raise ValueError("prebuilt pack has an unsupported derived chunk count")
    return pack, _digest(pack_artifact), chunks


def _load_canonical_manifest(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("prebuilt index manifest is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("prebuilt index manifest root must be an object")
    if raw != canonical_prebuilt_manifest_bytes(payload):
        raise ValueError("prebuilt index manifest is not canonical JSON")
    return payload


def _require_fixed_contract(payload: Mapping[str, object]) -> None:
    fixed = {
        "index_schema_version": PREBUILT_INDEX_SCHEMA_VERSION,
        "embedding_model_id": PREBUILT_MODEL_ID,
        "embedding_input_version": EMBEDDING_INPUT_VERSION,
        "chunker_version": CHUNKER_VERSION,
        "embedding_dimensions": PREBUILT_DIMENSIONS,
        "vector_encoding": PREBUILT_ENCODING,
    }
    for field, expected in fixed.items():
        value = payload.get(field)
        if isinstance(expected, int):
            value = _strict_int(value, field)
        if value != expected:
            raise ValueError(f"prebuilt index {field} is unsupported")


def _validate_vectors(raw: bytes, chunk_count: int) -> None:
    expected_bytes = chunk_count * PREBUILT_VECTOR_ROW_BYTES
    if len(raw) != expected_bytes:
        raise ValueError("prebuilt vector artifact has an invalid byte length")
    vectors = np.frombuffer(raw, dtype="<f2").reshape(chunk_count, PREBUILT_DIMENSIONS)
    if not bool(np.isfinite(vectors).all()):
        raise ValueError("prebuilt vectors must contain only finite values")
    if not bool(np.all(np.any(vectors != 0, axis=1))):
        raise ValueError("prebuilt vectors must have a non-zero norm")


def _required_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"prebuilt index {field} SHA-256 is invalid")
    return value


def _strict_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise ValueError(f"prebuilt index {field} must be an integer")
    return value


def _require_exact_keys(
    payload: Mapping[str, object],
    expected: frozenset[str],
    field: str,
) -> None:
    if set(payload) != expected:
        raise ValueError(f"{field} fields do not match the protocol")


def _digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()
