"""Embedding persistence schema and vector codec helpers."""
from __future__ import annotations

import base64
import hashlib
import re
from typing import Any


_MODEL_ID_DIM_RE = re.compile(r"-(\d+)d-(?:int8|fp32)(?:-mlen\d+)?$")


def encode_vector_fp16(vector) -> str:
    """Encode a float vector as strict base64-wrapped fp16 bytes."""
    import numpy as np

    arr = np.asarray(vector, dtype=np.float16).ravel()
    return base64.b64encode(arr.tobytes()).decode("ascii")


def decode_vector_fp16(encoded: str):
    """Decode base64 fp16 storage into a finite numpy fp32 array."""
    import numpy as np

    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:  # noqa: BLE001 - corrupt cache is treated as missing
        return None
    if len(raw) % 2 != 0:
        return None
    decoded = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
    if decoded.size == 0:
        return decoded
    if not np.isfinite(decoded).all():
        return None
    return decoded


def decode_embedding(embedding: Any, *, decode_string=None):
    """Normalize persisted and legacy embedding values to numpy fp32."""
    if embedding is None:
        return None
    import numpy as np

    if isinstance(embedding, np.ndarray):
        if embedding.size == 0:
            return None
        return embedding.astype(np.float32, copy=False)
    if isinstance(embedding, str):
        if not embedding:
            return None
        decoder = decode_vector_fp16 if decode_string is None else decode_string
        return decoder(embedding)
    if isinstance(embedding, (list, tuple)):
        if not embedding:
            return None
        try:
            return np.asarray(embedding, dtype=np.float32)
        except (TypeError, ValueError):
            return None
    return None


def parse_dim_from_model_id(model_id: str | None) -> int | None:
    """Extract the trailing embedding dimension from a canonical model id."""
    if not model_id or not isinstance(model_id, str):
        return None
    match = _MODEL_ID_DIM_RE.search(model_id)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def embedding_text_sha256(text: str) -> str:
    """Return the stable full-text fingerprint used by embedding caches."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_model_id(
    profile: str,
    dim: int,
    quantization: str,
    max_length: int | None = None,
) -> str:
    """Build the canonical embedding-space identity."""
    base = f"{profile}-{dim}d-{quantization}"
    if max_length is None:
        return base
    return f"{base}-mlen{max_length}"


def cosine_similarity(a, b, *, decoder=None) -> float:
    """Dot product for the service's L2-normalized vectors."""
    decode = decode_embedding if decoder is None else decoder
    av = decode(a)
    bv = decode(b)
    if av is None or bv is None:
        return 0.0
    if av.size == 0 or bv.size == 0 or av.size != bv.size:
        return 0.0
    import numpy as np
    return float(np.dot(av, bv))


def decode_valid_cached_embedding(
    entry: dict,
    current_text: str,
    current_model_id: str | None,
    *,
    hash_text=None,
    decode_string=None,
    parse_dim=None,
):
    """Validate every cache fingerprint and return the decoded vector, else None.

    The decoding twin of ``is_cached_embedding_valid`` — same checks, same
    verdict, but the vector it had to decode in order to check the dimension is
    handed back instead of thrown away. Scoring loops that need the vector right
    after the check (``hybrid_recall._cosine_rank``) would otherwise base64-decode
    and finite-scan every candidate twice per recall (#2550): ~26ms of the ~91ms
    cosine path at 5000 rows, entirely redundant.

    ``is_cached_embedding_valid`` is now a thin ``is not None`` wrapper over this,
    so the fingerprint rules keep exactly one definition.
    """
    if not isinstance(entry, dict):
        return None
    embedding = entry.get("embedding")
    if not isinstance(embedding, str) or not embedding:
        return None
    if current_model_id is None:
        return None
    if entry.get("embedding_model_id") != current_model_id:
        return None
    hasher = embedding_text_sha256 if hash_text is None else hash_text
    if entry.get("embedding_text_sha256") != hasher(current_text):
        return None
    decoder = decode_vector_fp16 if decode_string is None else decode_string
    vector = decoder(embedding)
    if vector is None or vector.size == 0:
        return None
    dim_parser = parse_dim_from_model_id if parse_dim is None else parse_dim
    expected_dim = dim_parser(current_model_id)
    if expected_dim is not None and vector.size != expected_dim:
        return None
    return vector


def is_cached_embedding_valid(
    entry: dict,
    current_text: str,
    current_model_id: str | None,
    *,
    hash_text=None,
    decode_string=None,
    parse_dim=None,
) -> bool:
    """Check all embedding cache fingerprints and the decoded dimension."""
    return decode_valid_cached_embedding(
        entry,
        current_text,
        current_model_id,
        hash_text=hash_text,
        decode_string=decode_string,
        parse_dim=parse_dim,
    ) is not None


def clear_embedding_fields(entry: dict) -> None:
    """Atomically invalidate the persisted embedding triple."""
    if not isinstance(entry, dict):
        return
    entry["embedding"] = None
    entry["embedding_text_sha256"] = None
    entry["embedding_model_id"] = None


def stamp_embedding_fields(
    entry: dict,
    vector,
    text: str,
    model_id: str,
    *,
    encoder=None,
    hash_text=None,
) -> None:
    """Persist a vector and its two cache fingerprints on an entry."""
    if not isinstance(entry, dict):
        return
    encode = encode_vector_fp16 if encoder is None else encoder
    hasher = embedding_text_sha256 if hash_text is None else hash_text
    entry["embedding"] = encode(vector)
    entry["embedding_text_sha256"] = hasher(text)
    entry["embedding_model_id"] = model_id
