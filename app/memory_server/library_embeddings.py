"""Bounded vector computation for the built-in personal library.

This API shares only the encoder. It neither imports knowledge services nor
reads memory records, and never changes the encoder's active specification.
Main uses HTTP in both split and merged deployments, so it cannot accidentally
create a second process-local encoder or copy the memory service's lifecycle.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from memory._embeddings.resources import resource_file_identity
from memory._embeddings.scheduling import InferenceBusy
from utils.http.knowledge_proxy import BodyTooLarge, capped_body

PROCESSING_VERSION = "neko-onnx-last-token-truncate-l2-v1"
MAX_BATCH_SIZE = 16
MAX_INPUT_CHARS = 2000
MAX_BODY_BYTES = 256 * 1024
# Cache hashes by the actual file identity, never by a friendly profile name.
_fingerprints: dict[tuple, dict[str, str]] = {}
_tokenizers: dict[tuple, object] = {}
_tokenizer_lock = threading.Lock()


class _InputTokenLimit(ValueError):
    """Reject overflow instead of accepting an encoder-truncated document."""


class _ComputationBusy(RuntimeError):
    """Report temporary background preprocessing pressure without loading models."""


@contextmanager
def _computation(request: Request, priority: str):
    """Track preprocessing ownership with a bounded background lane.

    Background work has one slot, while foreground requests have no admission
    cap. All slot changes happen on the owning event loop without awaits. A
    request holds its slot until its actual worker exits, including cancellation,
    rather than just until its HTTP caller stops awaiting it.
    """
    owner = getattr(request.app.state, "knowledge_library_computation", None)
    if owner is None:
        owner = {"foreground": 0, "background": 0}
        request.app.state.knowledge_library_computation = owner
    if priority == "background" and owner[priority] >= 1:
        raise _ComputationBusy()
    owner[priority] += 1
    try:
        yield
    finally:
        owner[priority] -= 1


async def _run_blocking(function, *args):
    """Borrow the model owner's cancellation-draining thread primitive."""
    from memory.embeddings import EmbeddingService

    return await EmbeddingService._run_blocking(function, *args)


def _full_tokenizer(service, spec: dict):
    """Read one independent tokenizer without changing the model's truncation.

    Token planning and validation share an untruncated tokenizer, not another
    ONNX session. The resource fingerprint is rechecked around every request.
    Once published in the one-entry cache, this tokenizer is never mutated.
    """
    path = str(service._tokenizer_file_path())
    key = (path, spec["weights_sha256"]["tokenizer"])
    with _tokenizer_lock:
        tokenizer = _tokenizers.get(key)
        if tokenizer is None:
            try:
                from tokenizers import Tokenizer

                tokenizer = Tokenizer.from_file(path)
                tokenizer.no_truncation()
                tokenizer.no_padding()
            except Exception as error:
                # Native tokenizers use a generic exception for corrupt JSON.
                # Expose only a stable unavailable reason, never paths or text.
                raise ValueError("embedding_unavailable") from error
            _tokenizers.clear()
            _tokenizers[key] = tokenizer
        return tokenizer


def _token_count(tokenizer, text: str) -> int:
    """Normalize native tokenizer failures without exposing a source input."""
    try:
        return len(tokenizer.encode(text).ids)
    except Exception as error:
        raise ValueError("embedding_unavailable") from error


def _plan_parts(service, spec: dict, inputs: list[dict]) -> list[list[dict]]:
    """Cover every Unicode character with nonempty, actually token-bounded spans."""
    tokenizer = _full_tokenizer(service, spec)
    results = []
    for value in inputs:
        prefix, body = value["prefix"], value["body"]
        start, parts = 0, []
        while start < len(body):
            # Prefix token count alone is insufficient: the tokenizer can merge
            # at the prefix/body boundary and adds its own post-processor tokens.
            low, high, accepted = 1, len(body) - start, 0
            while low <= high:
                count = (low + high) // 2
                text = prefix + body[start:start + count]
                if _token_count(tokenizer, text) <= spec["max_length"]:
                    accepted, low = count, count + 1
                else:
                    high = count - 1
            if not accepted:
                raise _InputTokenLimit()
            end = start + accepted
            parts.append({"start": start, "end": end})
            start = end
        results.append(parts)
    return results


def _check_token_lengths(service, spec: dict, texts: list[str]) -> None:
    """Validate exact model inputs without changing the encoder's lifecycle."""
    tokenizer = _full_tokenizer(service, spec)
    if any(_token_count(tokenizer, text) > spec["max_length"] for text in texts):
        raise _InputTokenLimit()


async def _internal_only(request: Request) -> None:
    """Reject browser requests before reading any potentially private text."""
    if "origin" in request.headers or "referer" in request.headers:
        raise HTTPException(403, detail={"ok": False, "reason": "untrusted_origin"})


router = APIRouter(
    prefix="/internal/knowledge-library/embeddings",
    tags=["knowledge-library-computation"],
    dependencies=[Depends(_internal_only)],
)


def _encoder():
    """Resolve the owning process's singleton off the event loop."""
    from memory.embeddings import get_embedding_service

    return get_embedding_service()


def _specification(service) -> dict:
    """Fingerprint exact weights and tokenizer, with bounded streaming IO."""
    model_id = service.model_id()
    if not model_id or service.is_disabled():
        raise ValueError("embedding_unavailable")
    # These read-only paths belong to the existing encoder. Do not construct
    # another encoder or rewrite its profile/dimension to fit an imported pack.
    model_path = Path(service._model_file_path())
    paths = {
        "model": model_path,
        "external_data": Path(str(model_path) + "_data"),
        "tokenizer": Path(service._tokenizer_file_path()),
    }
    key = resource_file_identity(paths.values())
    loaded_identity = getattr(service, "_loaded_file_identity", None)
    if loaded_identity is not None and loaded_identity != key:
        # The existing ORT session still owns the old resources. Do not claim
        # it encodes with the newly replaced files until the service restarts.
        raise ValueError("embedding_unavailable")
    values = _fingerprints.get(key)
    if values is None:
        values = {}
        for role, path in paths.items():
            if not path.is_file() or not path.stat().st_size:
                raise ValueError("embedding_unavailable")
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            values[role] = hasher.hexdigest()
        # One active profile is sufficient; old file identities must not grow
        # this cache across repeated model upgrades in a long-running process.
        _fingerprints.clear()
        _fingerprints[key] = values
    from memory.embeddings import DEFAULT_VECTORS_MAX_LENGTH

    return {
        "model_id": model_id,
        "profile": service._profile_id,
        "dimension": service.dim(),
        "quantization": service.quantization(),
        "max_length": DEFAULT_VECTORS_MAX_LENGTH,
        # Retain the local value if another worker refreshes the one-entry
        # cache while this specification is being assembled.
        "weights_sha256": dict(values),
        "processing_version": PROCESSING_VERSION,
    }


def _failure(reason: str, status: int = 503) -> JSONResponse:
    """Return stable reasons without source text, paths, or exception details."""
    return JSONResponse({"ok": False, "reason": reason}, status_code=status)


@router.get("/spec")
async def read_specification(request: Request):
    """Read compatibility without loading an ONNX session as a side effect."""
    try:
        with _computation(request, "foreground"):
            service = await _run_blocking(_encoder)
            spec = await _run_blocking(_specification, service)
    except _ComputationBusy:
        return _failure("embedding_busy", 429)
    except (OSError, ValueError):
        return _failure("embedding_unavailable")
    return {"ok": True, "specification": spec}


async def _payload(request: Request):
    """Apply identical media and byte limits to planning and inference bodies."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        return _failure("invalid_request", 415)
    try:
        body = bytearray()
        async for block in capped_body(request, MAX_BODY_BYTES):
            body.extend(block)
        payload = json.loads(body)
    except BodyTooLarge:
        return _failure("payload_too_large", 413)
    except (ValueError, UnicodeError, RecursionError):
        return _failure("invalid_request", 400)
    if not isinstance(payload, dict):
        return _failure("invalid_request", 400)
    return payload


@router.post("/plan")
async def plan_inputs(request: Request):
    """Plan bounded source windows without loading a second native model."""
    payload = await _payload(request)
    if isinstance(payload, JSONResponse):
        return payload
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= MAX_BATCH_SIZE or any(
        not isinstance(value, dict) or set(value) != {"prefix", "body"}
        or not isinstance(value["prefix"], str) or len(value["prefix"]) > 200
        or not isinstance(value["body"], str) or not value["body"] or len(value["body"]) > 1200
        for value in inputs
    ):
        return _failure("invalid_request", 400)
    try:
        with _computation(request, "background"):
            service = await _run_blocking(_encoder)
            spec = await _run_blocking(_specification, service)
            if payload.get("specification") != spec:
                return _failure("embedding_incompatible", 409)
            parts = await _run_blocking(_plan_parts, service, spec, inputs)
            if await _run_blocking(_specification, service) != spec:
                return _failure("embedding_incompatible", 409)
    except _ComputationBusy:
        return _failure("embedding_busy", 429)
    except _InputTokenLimit:
        return _failure("input_token_limit", 422)
    except (OSError, ValueError):
        return _failure("embedding_unavailable")
    return {"ok": True, "specification": spec, "parts": parts}


@router.post("/batch")
async def embed_batch(request: Request):
    """Compute an exact token-bounded batch against the frozen resource identity."""
    payload = await _payload(request)
    if isinstance(payload, JSONResponse):
        return payload
    texts = payload.get("texts")
    priority = payload.get("priority", "foreground")
    if not isinstance(priority, str) or priority not in {"foreground", "background"}:
        return _failure("invalid_request", 400)
    if not isinstance(texts, list) or not 1 <= len(texts) <= MAX_BATCH_SIZE or any(
        not isinstance(value, str) or not value.strip() or len(value) > MAX_INPUT_CHARS
        for value in texts
    ):
        return _failure("invalid_request", 400)
    try:
        with _computation(request, priority):
            service = await _run_blocking(_encoder)
            spec = await _run_blocking(_specification, service)
            if payload.get("specification") != spec:
                return _failure("embedding_incompatible", 409)
            # Only the model owner's warmup may load it. Borrowers distinguish
            # a not-ready encoder from temporary scheduling pressure below.
            if not service.is_available():
                return _failure("embedding_unavailable")
            await _run_blocking(_check_token_lengths, service, spec, texts)
            vectors = await service.embed_batch(texts, sticky_failure=False, priority=priority)
            if len(vectors) != len(texts) or any(value is None for value in vectors):
                return _failure("embedding_failed")
            if await _run_blocking(_specification, service) != spec:
                return _failure("embedding_incompatible", 409)
    except (_ComputationBusy, InferenceBusy):
        return _failure("embedding_busy", 429)
    except _InputTokenLimit:
        return _failure("input_token_limit", 422)
    except (OSError, ValueError):
        return _failure("embedding_unavailable")
    return {"ok": True, "specification": spec, "vectors": vectors}
