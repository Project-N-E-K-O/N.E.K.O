"""Read exact encoder identity and full token counts without loading ONNX."""

from __future__ import annotations

import hashlib
from pathlib import Path
import threading

from .resources import resource_file_identity

PROCESSING_VERSION = "neko-onnx-last-token-truncate-l2-v1"
_cache_lock = threading.Lock()
_fingerprints: dict[tuple, dict[str, str]] = {}
_tokenizers: dict[tuple, object] = {}


def specification(service, *, max_length: int) -> dict | None:
    """Fingerprint trusted local resources; callers run this IO off-loop."""
    if service.is_disabled() or not service.model_id():
        return None
    model_path = Path(service._model_file_path())
    paths = {"model": model_path, "external_data": Path(str(model_path) + "_data"),
             "tokenizer": Path(service._tokenizer_file_path())}
    try:
        identity = resource_file_identity(paths.values())
        loaded = getattr(service, "_loaded_file_identity", None)
        loaded_hashes = getattr(service, "_loaded_weights_sha256", None)
        if loaded is not None and loaded_hashes is None and identity != loaded:
            return None
        with _cache_lock:
            hashes = _fingerprints.get(identity)
            if hashes is None:
                hashes = {}
                for role, path in paths.items():
                    if not path.is_file() or not path.stat().st_size:
                        return None
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                    hashes[role] = digest.hexdigest()
                # Reject replacement while hashing instead of publishing a mixed spec.
                if resource_file_identity(paths.values()) != identity:
                    return None
                _fingerprints.clear()
                _fingerprints[identity] = hashes
            # A permission change only invalidates the cheap cache key. Bind
            # compatibility to the bytes loaded by ONNX, not metadata alone.
            if loaded_hashes is not None and hashes != loaded_hashes:
                return None
            return {"model_id": service.model_id(), "profile": service._profile_id,
                    "dimension": service.dim(), "quantization": service.quantization(),
                    "max_length": max_length, "weights_sha256": dict(hashes),
                    "processing_version": PROCESSING_VERSION}
    except OSError:
        return None


def count_tokens(service, texts: list[str], *, max_length: int) -> list[int] | None:
    """Use an independent untruncated tokenizer, never a second model session."""
    spec = specification(service, max_length=max_length)
    if spec is None:
        return None
    path = str(service._tokenizer_file_path())
    key = (path, spec["weights_sha256"]["tokenizer"])
    try:
        with _cache_lock:
            tokenizer = _tokenizers.get(key)
            if tokenizer is None:
                from tokenizers import Tokenizer

                # Read once and parse those same digest-checked bytes. A file
                # replacement between fingerprinting and native loading must
                # never publish a tokenizer under another resource's key.
                raw = Path(path).read_bytes()
                if hashlib.sha256(raw).hexdigest() != key[1]:
                    return None
                tokenizer = Tokenizer.from_str(raw.decode("utf-8"))
                tokenizer.no_truncation()
                tokenizer.no_padding()
                _tokenizers.clear()
                _tokenizers[key] = tokenizer
        # Published tokenizers are immutable and safe for concurrent readers.
        return [len(value.ids) for value in tokenizer.encode_batch(texts)]
    except Exception:
        # Native tokenizer errors can contain private inputs or absolute paths.
        return None
