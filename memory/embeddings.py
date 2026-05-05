# -*- coding: utf-8 -*-
"""
EmbeddingService — Tier 0 of the memory hierarchy: vector embeddings.

Provides ``embed(text)`` / ``embed_batch(texts)`` over the local CPU ONNX
text-retrieval embedding profile. Used by:

  * fact dedup at write time (cosine > threshold → LLM arbitration queue)
  * persona / reflection retrieval (cosine top-K → LLM rerank precandidates)

This module owns the *fallback gate*. The whole feature degrades to
zero-cost if any of the following holds:

  * ``onnxruntime`` cannot be imported
  * the ONNX model file is missing on disk
  * detected RAM < ``VECTORS_MIN_RAM_GB``
  * the user set ``VECTORS_ENABLED = False``
  * ``auto`` quantization when AVX-VNNI is **confirmed absent** (no INT8
    fast-path; default installs omit the large FP32 ONNX bundle — operators
    who need vectors then pin ``int8`` or ship FP32 weights + ``fp32``)
  * loading or any per-call inference raised an exception (sticky disable)

Explicit ``fp32`` loads ``model.onnx`` when present (manual / optional bundle).

When disabled, ``is_available()`` returns False; callers MUST check it
before invoking ``embed()`` / ``embed_batch()`` and fall back to the
pre-vector code path. The disable is process-local and final — once
``DISABLED`` we don't retry within the same process.

Lazy load: the model file is NOT loaded at startup. The
warmup is gated on the first ``request_load()`` call from
memory_server's post-ready hook (after the frontend has finished its
greeting / prominent drain). Until ``READY``, ``embed()`` returns None.

Embedding cache invalidation lives on the entry dict itself:

  * ``embedding``: list[float] | None
  * ``embedding_text_sha256``: str | None
  * ``embedding_model_id``: str | None

A reader treats the cached embedding as valid only when both fingerprints
match the current text + service ``model_id()`` — same pattern as the
``token_count`` cache PR-3 introduced.
"""
from __future__ import annotations

import asyncio
import base64
import enum
import hashlib
import logging
import os
import platform
import sys
from typing import Any

logger = logging.getLogger(__name__)


# ── on-disk vector encoding ──────────────────────────────────────────
#
# A 256d float vector serialized as JSON ``list[float]`` runs ~5.3 KB
# (each float prints to ~21 chars after Python's repr).  We instead
# store a **base64-wrapped int8 quantization** with a per-vector fp16
# scale prefix.  The wire format is ``base64( fp16_scale | int8_dims )``,
# decoded via:
#
#     raw   = b64decode(s)
#     scale = float(np.frombuffer(raw[:2],  dtype=np.float16)[0])
#     vec   = np.frombuffer(raw[2:], dtype=np.int8) * (scale / 127.0)
#
# Total bytes ≈ ``2 + dim``; base64 expands to ``≈ ceil((2+dim) * 4/3)``.
# At 256d that is ~344 chars vs ~5.3 KB before — ~15× smaller, plus the
# decoder lands in a contiguous numpy buffer so the recall path can
# stack candidates into a matrix and use ``M @ q.T`` instead of the
# pure-Python cosine loop.
#
# Per-vector scale = ``max(|v|)`` preserves precision for L2-normalized
# vectors whose dimensional magnitudes are typically << 1 (e.g. 1/√256).
# Without the scale, int8 step would be 1/127 ≈ 0.79% of full range,
# wasting ~6× of the available precision on each axis.
#
# Quantization noise is ~0.4% per dim, but the 256-dim dot product
# averages it out — empirical cosine error vs fp32 is well under 1% on
# standard retrieval benchmarks, which is invisible after the LLM
# rerank stage in our pipeline.
_QUANT_SCALE = 127.0


# ── Config knobs (mirrored in config/__init__.py for centralised tuning) ──
# These default values are kept in this module so the service stays
# importable in test harnesses that bypass the full app config.

DEFAULT_VECTORS_ENABLED = True
DEFAULT_VECTORS_EMBEDDING_DIM = "auto"            # "auto" | 32 | 64 | 128 | 256 | 512 | 768
DEFAULT_VECTORS_MODEL_PROFILE_ID = "local-text-retrieval-v1"
DEFAULT_VECTORS_QUANTIZATION = "auto"             # "auto" | "int8" | "fp32"
DEFAULT_VECTORS_MIN_RAM_GB = 4.0
DEFAULT_VECTORS_MODEL_DIR_NAME = "embedding_models"
DEFAULT_VECTORS_MAX_LENGTH = 8192

# Matryoshka discrete steps supported by the default local profile.
_DIM_STEPS = (32, 64, 128, 256, 512, 768)


class EmbeddingState(enum.Enum):
    """Service lifecycle. Transitions are forward-only except DISABLED,
    which is sticky: once we decide vectors are off we never re-enable
    within the same process (otherwise a transient OOM at load could
    flip on/off mid-session and corrupt cache invariants)."""
    INIT = "init"
    LOADING = "loading"
    READY = "ready"
    DISABLED = "disabled"


class _DisableReason(enum.Enum):
    """Why ``is_available()`` is False. Surfaced in the startup log so
    operators can tell apart "user opted out" from "we couldn't load"."""
    NONE = "none"
    USER_DISABLED = "user_disabled_via_config"
    NO_ONNXRUNTIME = "onnxruntime_not_importable"
    # Distinct from NO_ONNXRUNTIME so operators see exactly which dep
    # is missing in the startup log — the two libs ship separately and
    # the install commands diverge.
    NO_TOKENIZERS = "tokenizers_not_importable"
    NO_MODEL_FILE = "model_file_missing"
    # Default bundle is INT8; ``auto`` picks INT8 only when VNNI is present or
    # detection is inconclusive — confirmed absence disables local vectors.
    AVX_VNNI_REQUIRED_FOR_INT8 = "avx_vnni_required_for_int8_bundle"
    LOW_RAM = "ram_below_threshold"
    LOAD_ERROR = "load_raised"
    INFERENCE_ERROR = "inference_raised"


# ── helpers ──────────────────────────────────────────────────────────


def _encode_vector_int8(vector) -> str:
    """Quantize a float vector to ``base64(fp16_scale | int8_dims)``.

    Accepts list/tuple/numpy. Returns the canonical on-disk
    representation. Empty / zero-norm inputs degrade to an all-zero
    payload (with scale=1.0) rather than raising — keeps the stamp
    path total even when an upstream embed returned a degenerate row.
    """
    import numpy as np
    arr = np.asarray(vector, dtype=np.float32).ravel()
    if arr.size == 0:
        # Should not happen in practice (callers gate on ``embed()`` returning
        # a vector), but a zero-length payload is at least decodable as an
        # empty array on the read side.
        return base64.b64encode(np.array([1.0], dtype=np.float16).tobytes()).decode("ascii")
    max_abs = float(np.max(np.abs(arr)))
    if max_abs == 0.0:
        # All-zero vector → keep scale=1.0 sentinel and emit zeros so
        # the round-trip yields the same all-zero array.
        scale = 1.0
        quantized = np.zeros_like(arr, dtype=np.int8)
    else:
        scale = max_abs
        quantized = np.clip(
            np.round(arr / max_abs * _QUANT_SCALE), -127, 127,
        ).astype(np.int8)
    scale_bytes = np.array([scale], dtype=np.float16).tobytes()
    return base64.b64encode(scale_bytes + quantized.tobytes()).decode("ascii")


def _decode_vector_int8(encoded: str):
    """Inverse of :func:`_encode_vector_int8`. Returns a numpy fp32 array.

    Returns None on any decoding failure — corrupt cache fields fall
    through to the "no embedding" path rather than raising up into the
    retrieval/dedup loops.
    """
    import numpy as np
    try:
        raw = base64.b64decode(encoded, validate=False)
    except Exception:  # noqa: BLE001 — malformed base64 → treat as missing
        return None
    if len(raw) < 2:
        return None
    scale = float(np.frombuffer(raw[:2], dtype=np.float16)[0])
    quantized = np.frombuffer(raw[2:], dtype=np.int8)
    if quantized.size == 0:
        return np.zeros(0, dtype=np.float32)
    return quantized.astype(np.float32) * (scale / _QUANT_SCALE)


def decode_embedding(emb: Any):
    """Public helper: turn a persisted ``embedding`` field into a numpy
    fp32 array, regardless of whether the row carries the new base64
    form, a legacy ``list[float]``, an already-decoded numpy array, or
    None / empty.

    Returns None when the field is missing or unreadable. Used by
    cosine helpers and by recall's batched dot-product path.
    """
    if emb is None:
        return None
    import numpy as np
    if isinstance(emb, np.ndarray):
        if emb.size == 0:
            return None
        return emb.astype(np.float32, copy=False)
    if isinstance(emb, str):
        if not emb:
            return None
        return _decode_vector_int8(emb)
    if isinstance(emb, (list, tuple)):
        if not emb:
            return None
        try:
            return np.asarray(emb, dtype=np.float32)
        except (TypeError, ValueError):
            return None
    return None


def _embedding_text_sha256(text: str) -> str:
    """Stable fingerprint used for ``embedding_text_sha256`` cache keys.

    Same scheme as ``token_count_text_sha256`` — utf-8 then full sha256.
    Truncation lives at consumer sites only; we keep the full hex so a
    future migration to a longer prefix doesn't require recomputing all
    cached values.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def detect_total_ram_gb() -> float | None:
    """Return total system RAM in GiB or None on detection failure.

    Detection failure is treated as "unknown" upstream — we conservatively
    assume insufficient RAM and disable vectors, since a runaway load on
    a tiny VM is worse than missing a feature on a workstation that
    happens to lack psutil.
    """
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception as e:  # noqa: BLE001 — psutil should always be available
        logger.warning("EmbeddingService: psutil RAM detection failed: %s", e)
        return None


def detect_avx_vnni_details() -> tuple[bool, bool]:
    """Return ``(has_vnni, vnni_absence_confirmed)``.

    When ``vnni_absence_confirmed`` is False we could not read CPU flags
    (should be rare now that ``py-cpuinfo`` is a required dependency).
    ``auto`` quantization
    still selects INT8 in that case — we only skip vectors when we are
    *confident* the CPU lacks AVX-VNNI (INT8 would be slow and FP32 weights
    are not shipped).

    Detection priority:
      1. ``py-cpuinfo``
      2. ``/proc/cpuinfo`` on Linux
      3. Otherwise inconclusive → ``(False, False)``
    """
    try:
        import cpuinfo  # type: ignore
        flags = cpuinfo.get_cpu_info().get("flags", []) or []
        # Empty flags (e.g. some virtualised hosts) is *not* a confirmed
        # absence — fall through so /proc/cpuinfo can have a try.
        if flags:
            has = any("vnni" in f for f in flags)
            return has, True
    except Exception:
        pass

    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("flags") and "vnni" in line:
                        return True, True
            return False, True
        except Exception:
            return False, False

    return False, False


def detect_avx_vnni() -> bool:
    """Backward-compatible: whether AVX-VNNI was detected."""
    has_vnni, _confirmed = detect_avx_vnni_details()
    return has_vnni


def resolve_dim_for_ram(ram_gb: float | None) -> int | None:
    """Pick a Matryoshka dim from detected RAM. None ⇒ disabled.

    The bands match the design contract in the PR description — they're
    not a hard performance cliff, but a conservative budget that leaves
    headroom for the rest of the app (LLM client, websocket pool, TTS
    buffers, frontend renderer if collocated).

    ≥ 16 GB → 256. Higher Matryoshka levels (512/768) are reserved for
    opt-in overrides until we have enough latency data from real installs.
    """
    if ram_gb is None or ram_gb < DEFAULT_VECTORS_MIN_RAM_GB:
        return None
    if ram_gb < 8:
        return 64
    if ram_gb < 16:
        return 128
    return 256


def _coerce_dim(value, ram_gb: float | None) -> int | None:
    """Resolve a config value to an integer dim, or None if disabled.

    "auto" delegates to :func:`resolve_dim_for_ram`. Explicit values must
    be one of the supported Matryoshka steps; an invalid value falls
    back to "auto" with a warning rather than crashing — safer than
    refusing to start because of a typo in settings.
    """
    if value == "auto" or value is None:
        return resolve_dim_for_ram(ram_gb)
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "EmbeddingService: invalid embedding_dim=%r, falling back to auto", value,
        )
        return resolve_dim_for_ram(ram_gb)
    if as_int not in _DIM_STEPS:
        logger.warning(
            "EmbeddingService: dim=%d not in supported %s, falling back to auto",
            as_int, _DIM_STEPS,
        )
        return resolve_dim_for_ram(ram_gb)
    return as_int


def _resolve_quantization(
    value: str | None,
    has_vnni: bool,
    *,
    vnni_absence_confirmed: bool = True,
) -> str | None:
    """Map ``\"auto\"`` / ``\"int8\"`` / ``\"fp32\"`` after VNNI policy.

    Returns ``\"int8\"``, ``\"fp32\"``, or ``None``. ``None`` means local
    embeddings are off for ``auto`` when AVX-VNNI is confidently absent.
    Explicit ``\"fp32\"`` always loads the FP32 ONNX when files exist.

    Explicit ``\"int8\"`` without VNNI is still honoured (with a warning)
    so operators can force INT8 on slow CPUs if they accept the cost.
    """
    if value == "fp32":
        return "fp32"
    if value == "auto" or value is None:
        if has_vnni:
            return "int8"
        if not vnni_absence_confirmed:
            return "int8"
        return None
    if value not in ("int8", "fp32"):
        if has_vnni:
            return "int8"
        if not vnni_absence_confirmed:
            return "int8"
        return None
    if value == "int8" and not has_vnni:
        logger.warning(
            "EmbeddingService: int8 requested but AVX-VNNI not detected — "
            "expect slower inference than a hypothetical fp32 build",
        )
    return "int8"


def build_model_id(profile: str, dim: int, quantization: str) -> str:
    """Return the canonical id used in ``embedding_model_id`` cache fields.

    Format: ``<profile>-<dim>d-<quant>`` (e.g.
    ``local-text-retrieval-v1-128d-int8``).
    A change to any axis flips the id, which invalidates cached
    embeddings on the next read — same idea as ``tokenizer_identity``.
    """
    return f"{profile}-{dim}d-{quantization}"


def _profile_exists(model_dir: str, profile_id: str) -> bool:
    return os.path.isdir(os.path.join(model_dir, profile_id))


def _is_nonempty_file(path: str) -> bool:
    """File present AND >0 bytes. Zero-byte residue from an interrupted
    download passes plain ``isfile`` but trips the loader downstream — we
    treat it as missing so the bundled fallback still kicks in."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _profile_is_complete(
    model_dir: str, profile_id: str, quantization: str | None = None,
) -> bool:
    """A profile dir is usable only if it has a non-empty tokenizer plus
    a full (model + onnx_data sidecar) variant the runtime can actually
    load.

    ``quantization`` lets callers narrow the variant requirement to the
    one ``_load_session_blocking`` will actually open. With ``None``, only
    the shipped INT8 variant is considered complete — so a legacy fp32-only
    app-data tree does not mask a good bundled int8 profile. Pass ``None``
    only when the runtime quantization is not yet pinned to a single file.

    Why stricter than ``_profile_exists``: a half-downloaded or partially
    deleted app-data profile would otherwise satisfy the existence check,
    short-circuit the bundled fallback, and then trip
    ``NO_MODEL_FILE`` at session load — leaving the user with vectors
    sticky-disabled even though the bundle on disk is fine.
    """
    profile_dir = os.path.join(model_dir, profile_id)
    if not os.path.isdir(profile_dir):
        return False
    if not _is_nonempty_file(os.path.join(profile_dir, "tokenizer.json")):
        return False
    if quantization == "int8":
        stems: tuple[str, ...] = ("model_quantized.onnx",)
    elif quantization == "fp32":
        stems = ("model.onnx",)
    else:
        # Only the INT8 bundle is shipped; fp32 ONNX is optional / omitted.
        stems = ("model_quantized.onnx",)
    for stem in stems:
        model_path = os.path.join(profile_dir, "onnx", stem)
        sidecar_path = model_path + "_data"
        if _is_nonempty_file(model_path) and _is_nonempty_file(sidecar_path):
            return True
    return False


def _bundled_model_dirs() -> list[str]:
    """Candidate roots for build-time packaged embedding assets.

    Developers and CI place model files under
    ``data/embedding_models/<profile_id>/...``. In source runs this is
    relative to the repo root; in PyInstaller/Nuitka builds it lives next
    to the bundled launcher resources.
    """
    roots: list[str] = []
    if hasattr(sys, "_MEIPASS"):
        roots.append(str(sys._MEIPASS))
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    seen: set[str] = set()
    model_dirs: list[str] = []
    for root in roots:
        path = os.path.join(root, "data", DEFAULT_VECTORS_MODEL_DIR_NAME)
        norm = os.path.abspath(path)
        if norm not in seen:
            seen.add(norm)
            model_dirs.append(norm)
    return model_dirs


def _select_model_dir(
    app_docs_model_dir: str,
    profile_id: str,
    quantization: str | None = None,
) -> str:
    """Prefer user-managed app-data models, otherwise use bundled assets.

    A half-downloaded app-data profile, or one that only has the
    *other* quantization variant from what the runtime resolved to, is
    treated as broken (see ``_profile_is_complete``) and we fall back to
    bundled — otherwise the presence-only check would prefer the broken
    dir and sticky-disable vectors at load even though the bundle is
    fine. Callers should pass the resolved ``quantization`` so the
    variant check matches what ``_load_session_blocking`` will open.
    """
    if _profile_is_complete(app_docs_model_dir, profile_id, quantization):
        return app_docs_model_dir
    for bundled_dir in _bundled_model_dirs():
        if _profile_is_complete(bundled_dir, profile_id, quantization):
            return bundled_dir
    return app_docs_model_dir


# ── service ──────────────────────────────────────────────────────────


class EmbeddingService:
    """Process-singleton vector encoder. Acquire via :func:`get_embedding_service`.

    Responsibilities (intentionally narrow — fact / persona / reflection
    subsystems own everything around this class):

      1. Resolve the runtime model id from hardware + config
      2. Lazy-load the ONNX session on first ``request_load()``
      3. Provide ``embed`` / ``embed_batch`` once READY
      4. Be a sticky kill switch: once DISABLED, every method returns
         the safe "no embedding" answer for the rest of the process

    Thread/coroutine safety: ``request_load()`` is idempotent under
    concurrent callers thanks to the asyncio.Lock; embedding calls are
    naturally serialized through ``asyncio.to_thread`` and the
    onnxruntime session itself releases the GIL during inference.
    """

    def __init__(
        self,
        *,
        model_dir: str,
        enabled: bool = DEFAULT_VECTORS_ENABLED,
        embedding_dim_setting=DEFAULT_VECTORS_EMBEDDING_DIM,
        quantization_setting: str = DEFAULT_VECTORS_QUANTIZATION,
        min_ram_gb: float = DEFAULT_VECTORS_MIN_RAM_GB,
        profile_id: str = DEFAULT_VECTORS_MODEL_PROFILE_ID,
        ram_gb: float | None = None,        # injected for tests
        has_vnni: bool | None = None,       # injected for tests
        vnni_absence_confirmed: bool | None = None,  # False = inconclusive detect
    ) -> None:
        self._model_dir = model_dir
        self._enabled = enabled
        self._embedding_dim_setting = embedding_dim_setting
        self._quantization_setting = quantization_setting
        self._min_ram_gb = min_ram_gb
        self._profile_id = profile_id

        # Resolved at construction so ``model_id()`` can return early
        # even before the session loads — callers reading
        # embedding_model_id at write time need a stable id.
        self._ram_gb = ram_gb if ram_gb is not None else detect_total_ram_gb()
        if has_vnni is not None:
            self._has_vnni = has_vnni
            self._vnni_absence_confirmed = (
                True if vnni_absence_confirmed is None else vnni_absence_confirmed
            )
        else:
            detected_vnni, absence_confirmed = detect_avx_vnni_details()
            self._has_vnni = detected_vnni
            self._vnni_absence_confirmed = absence_confirmed
        self._dim = _coerce_dim(embedding_dim_setting, self._ram_gb)
        if quantization_setting not in ("auto", "int8", "fp32"):
            logger.warning(
                "EmbeddingService: invalid quantization=%r, falling back to auto",
                quantization_setting,
            )
            norm_quant = "auto"
        else:
            norm_quant = quantization_setting
        self._quantization = _resolve_quantization(
            norm_quant,
            self._has_vnni,
            vnni_absence_confirmed=self._vnni_absence_confirmed,
        )

        self._state = EmbeddingState.INIT
        self._disable_reason = _DisableReason.NONE
        self._session = None
        self._tokenizer = None
        self._load_lock = asyncio.Lock()

        # Decide initial disable conditions (all but model file presence,
        # which we check at load time so a deferred download path can
        # still flip vectors on after first session).
        if not self._enabled:
            self._mark_disabled(_DisableReason.USER_DISABLED, log=False)
        elif self._ram_gb is None or self._ram_gb < self._min_ram_gb:
            self._mark_disabled(_DisableReason.LOW_RAM, log=False)
        elif self._dim is None:
            # _coerce_dim returns None when the resolved RAM is too low
            # for any band — defensive double-check; LOW_RAM should have
            # caught it already.
            self._mark_disabled(_DisableReason.LOW_RAM, log=False)
        elif self._quantization is None:
            self._mark_disabled(_DisableReason.AVX_VNNI_REQUIRED_FOR_INT8, log=True)

    # ── public API ────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """True iff a subsequent ``embed()`` call would actually return
        a vector. Callers MUST short-circuit to the pre-vector path
        when this is False."""
        return self._state == EmbeddingState.READY

    def is_disabled(self) -> bool:
        """True iff the service has reached the sticky DISABLED state.
        Distinct from ``not is_available()`` because INIT / LOADING also
        fail ``is_available`` but are not terminal."""
        return self._state == EmbeddingState.DISABLED

    def disable_reason(self) -> str:
        return self._disable_reason.value

    def model_id(self) -> str | None:
        """Canonical id stamped into ``embedding_model_id`` cache fields.
        Returns None when the service is permanently DISABLED — callers
        should not write embedding rows in that case."""
        if (
            self._state == EmbeddingState.DISABLED
            or self._dim is None
            or self._quantization not in ("int8", "fp32")
        ):
            return None
        return build_model_id(self._profile_id, self._dim, self._quantization)

    def dim(self) -> int | None:
        return self._dim

    def quantization(self) -> str | None:
        return self._quantization

    def ram_gb(self) -> float | None:
        return self._ram_gb

    def has_vnni(self) -> bool:
        return self._has_vnni

    async def request_load(self) -> bool:
        """Load the ONNX session if not already loaded. Returns
        ``is_available()`` after the attempt.

        Idempotent: safe to call from multiple coroutines (warmup task
        + first-use fallback). Single-flight via the load lock so we
        don't double-decompress the model file.

        On any failure, transitions to DISABLED and returns False — the
        service stays off for the lifetime of the process.
        """
        if self._state in (EmbeddingState.READY, EmbeddingState.DISABLED):
            return self.is_available()

        async with self._load_lock:
            if self._state in (EmbeddingState.READY, EmbeddingState.DISABLED):
                return self.is_available()
            self._state = EmbeddingState.LOADING
            try:
                await asyncio.to_thread(self._load_session_blocking)
            except _DisabledError as e:
                self._mark_disabled(e.reason)
                return False
            except Exception as e:  # noqa: BLE001 — any load failure → off
                logger.warning(
                    "EmbeddingService: load failed (%s: %s); vectors disabled",
                    type(e).__name__, e,
                )
                self._mark_disabled(_DisableReason.LOAD_ERROR)
                return False
            self._state = EmbeddingState.READY
            logger.info(
                "EmbeddingService: ready (model_id=%s, ram=%.1fGB, vnni=%s)",
                self.model_id(), self._ram_gb or 0.0, self._has_vnni,
            )
            return True

    async def embed(self, text: str) -> list[float] | None:
        """Single-text embedding. Returns None when not READY — caller
        must treat this as a cache miss and skip the vector path for
        this query."""
        if not text:
            return None
        if not self.is_available():
            return None
        try:
            vectors = await asyncio.to_thread(self._infer_blocking, [text])
        except Exception as e:  # noqa: BLE001 — sticky inference failure
            logger.warning(
                "EmbeddingService: inference failed (%s: %s); vectors disabled",
                type(e).__name__, e,
            )
            self._mark_disabled(_DisableReason.INFERENCE_ERROR)
            return None
        return vectors[0] if vectors else None

    async def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        """Batch embedding. Empty / None inputs and not-ready service
        both produce a None at the corresponding output index — keeps
        callers' index alignment with the input list intact."""
        if not texts:
            return []
        result: list[list[float] | None] = [None] * len(texts)
        if not self.is_available():
            return result
        # Filter out empty entries before inference but preserve
        # positional alignment in the output via index mapping.
        active_idx: list[int] = []
        active_texts: list[str] = []
        for i, t in enumerate(texts):
            if t:
                active_idx.append(i)
                active_texts.append(t)
        if not active_texts:
            return result
        try:
            vectors = await asyncio.to_thread(self._infer_blocking, active_texts)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "EmbeddingService: batch inference failed (%s: %s); vectors disabled",
                type(e).__name__, e,
            )
            self._mark_disabled(_DisableReason.INFERENCE_ERROR)
            return result
        for slot, vec in zip(active_idx, vectors):
            result[slot] = vec
        return result

    # ── internal: session load / inference ───────────────────────────

    def _model_file_path(self) -> str:
        """Resolve the on-disk ONNX file path for the active quantization.

        Layout mirrors the Hugging Face ONNX export:
        ``onnx/model_quantized.onnx`` (int8) or ``onnx/model.onnx`` (fp32),
        each with a matching ``*_data`` sidecar, plus ``tokenizer.json``.
        """
        filename = (
            "model.onnx"
            if self._quantization == "fp32"
            else "model_quantized.onnx"
        )
        return os.path.join(
            self._model_dir, self._profile_id, "onnx", filename,
        )

    def _tokenizer_file_path(self) -> str:
        return os.path.join(self._model_dir, self._profile_id, "tokenizer.json")

    def _load_session_blocking(self) -> None:
        """Synchronous load — runs under ``asyncio.to_thread``.

        Order of checks: file presence first (cheapest, cleanest disable
        reason), then onnxruntime import (heavyweight import deferred
        until we know the file exists), then session creation. Each
        failure mode raises ``_DisabledError`` with the right reason.
        """
        model_path = self._model_file_path()
        tokenizer_path = self._tokenizer_file_path()
        external_data_path = f"{model_path}_data"
        # Match _profile_is_complete: zero-byte residue from an interrupted
        # download passes os.path.exists but trips ort/tokenizers later. Reject
        # it here as NO_MODEL_FILE so the disable reason is the cleanest one.
        if (
            not _is_nonempty_file(model_path)
            or not _is_nonempty_file(tokenizer_path)
            or not _is_nonempty_file(external_data_path)
        ):
            raise _DisabledError(_DisableReason.NO_MODEL_FILE)
        try:
            import onnxruntime as ort  # type: ignore
        except ImportError as e:
            raise _DisabledError(_DisableReason.NO_ONNXRUNTIME) from e
        try:
            from tokenizers import Tokenizer  # type: ignore
        except ImportError as e:
            # huggingface tokenizers is the only sane way to load the
            # SentencePiece-style tokenizer offline. Distinct
            # disable reason so operators don't chase a phantom
            # onnxruntime install when it's actually tokenizers
            # that's missing.
            raise _DisabledError(_DisableReason.NO_TOKENIZERS) from e

        sess_opts = ort.SessionOptions()
        sess_opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            model_path, sess_options=sess_opts, providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        try:
            self._tokenizer.enable_truncation(max_length=DEFAULT_VECTORS_MAX_LENGTH)
        except Exception as e:  # noqa: BLE001 — old tokenizers can still run without it
            logger.warning("EmbeddingService: tokenizer truncation setup failed: %s", e)

    def _infer_blocking(self, texts: list[str]) -> list[list[float]]:
        """Tokenize + run ONNX session + L2-normalize + Matryoshka-trunc.

        The Matryoshka truncation is the crux of why ``model_id``
        encodes the dim: a 64-d cached vector and a 256-d freshly
        computed vector are NOT comparable, even though they come from
        the same checkpoint, so the cache key MUST contain the dim.
        """
        if self._session is None or self._tokenizer is None:
            raise RuntimeError("session not loaded")
        encoded = self._tokenizer.encode_batch(texts)
        ids = [e.ids for e in encoded]
        mask = [e.attention_mask for e in encoded]
        # Pad to longest. Only allocate as much as we need — model accepts
        # variable length within its 32K context.
        max_len = max(len(x) for x in ids)
        import numpy as np
        ids_arr = np.zeros((len(texts), max_len), dtype=np.int64)
        mask_arr = np.zeros((len(texts), max_len), dtype=np.int64)
        for i, (id_row, mask_row) in enumerate(zip(ids, mask)):
            ids_arr[i, : len(id_row)] = id_row
            mask_arr[i, : len(mask_row)] = mask_row
        input_names = {i.name for i in self._session.get_inputs()}
        feeds = {"input_ids": ids_arr}
        if "attention_mask" in input_names:
            feeds["attention_mask"] = mask_arr
        if "token_type_ids" in input_names:
            feeds["token_type_ids"] = np.zeros_like(ids_arr)
        outputs = self._session.run(None, feeds)
        # The default profile uses last-token pooling. Then L2-normalize
        # and Matryoshka-truncate to the active dim.
        token_embeddings = outputs[0]
        last_indices = np.maximum(mask_arr.sum(axis=1) - 1, 0)
        pooled = token_embeddings[np.arange(len(texts)), last_indices]
        if self._dim is not None and self._dim < pooled.shape[1]:
            pooled = pooled[:, : self._dim]
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        normalized = pooled / norms
        return [row.tolist() for row in normalized]

    # ── disable bookkeeping ──────────────────────────────────────────

    def _mark_disabled(self, reason: _DisableReason, *, log: bool = True) -> None:
        # Only log the first transition — re-entries from later
        # inference failures shouldn't spam logs.
        if self._state != EmbeddingState.DISABLED and log:
            logger.warning(
                "EmbeddingService: vectors disabled (%s)", reason.value,
            )
        self._state = EmbeddingState.DISABLED
        self._disable_reason = reason
        self._session = None
        self._tokenizer = None


class _DisabledError(Exception):
    """Internal control-flow exception used by the load path to signal
    'no need to log a stack trace, this is a known disable reason'."""

    def __init__(self, reason: _DisableReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


# ── module-level singleton accessor ──────────────────────────────────

_SERVICE: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """Return the process-wide singleton, lazily constructed.

    Construction reads from ``config`` and the user's app-data dir. The
    service ctor itself is cheap (no model load, no disk IO beyond psutil
    sampling), so we don't bother short-circuiting on the lock outside.
    """
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = _build_default_service()
    return _SERVICE


def reset_embedding_service_for_tests() -> None:
    """Test-only: drop the singleton so the next ``get_embedding_service``
    call rebuilds with whatever monkeypatched config / RAM the test set up."""
    global _SERVICE
    _SERVICE = None


def _build_default_service() -> EmbeddingService:
    """Construct the singleton from app config + app_docs_dir model path."""
    try:
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
        app_docs_model_dir = os.path.join(
            str(cm.app_docs_dir), DEFAULT_VECTORS_MODEL_DIR_NAME,
        )
    except Exception as e:
        # Outside the FastAPI context (e.g. some isolated test that
        # imports this module before bootstrapping config) we still
        # want a service, just one that's permanently disabled. The
        # alternative (raise) would cascade into every memory call site.
        logger.warning(
            "EmbeddingService: config_manager unavailable (%s); using disabled stub",
            e,
        )
        return EmbeddingService(
            model_dir="", enabled=False, ram_gb=0.0, has_vnni=False,
        )

    try:
        from config import (
            VECTORS_ENABLED,
            VECTORS_EMBEDDING_DIM,
            VECTORS_QUANTIZATION,
            VECTORS_MIN_RAM_GB,
            VECTORS_MODEL_PROFILE_ID,
        )
    except ImportError:
        # Config module hasn't been updated yet — fall back to defaults.
        # Lets the embedding module land in one PR before the
        # config-side knobs in another.
        VECTORS_ENABLED = DEFAULT_VECTORS_ENABLED
        VECTORS_EMBEDDING_DIM = DEFAULT_VECTORS_EMBEDDING_DIM
        VECTORS_QUANTIZATION = DEFAULT_VECTORS_QUANTIZATION
        VECTORS_MIN_RAM_GB = DEFAULT_VECTORS_MIN_RAM_GB
        VECTORS_MODEL_PROFILE_ID = DEFAULT_VECTORS_MODEL_PROFILE_ID

    # Resolve quantization here so _select_model_dir can require the exact
    # variant ``_load_session_blocking`` will open. Without this, an app-data
    # profile that only contains the *other* variant would still satisfy the
    # completeness check and short-circuit a complete bundled fallback.
    has_vnni, vnni_absence_confirmed = detect_avx_vnni_details()
    norm_q = (
        VECTORS_QUANTIZATION
        if VECTORS_QUANTIZATION in ("auto", "int8", "fp32")
        else "auto"
    )
    resolved_quantization = _resolve_quantization(
        norm_q, has_vnni, vnni_absence_confirmed=vnni_absence_confirmed,
    )

    model_dir = (
        app_docs_model_dir
        if resolved_quantization is None
        else _select_model_dir(
            app_docs_model_dir, VECTORS_MODEL_PROFILE_ID, resolved_quantization,
        )
    )

    return EmbeddingService(
        model_dir=model_dir,
        enabled=VECTORS_ENABLED,
        embedding_dim_setting=VECTORS_EMBEDDING_DIM,
        quantization_setting=VECTORS_QUANTIZATION,
        min_ram_gb=VECTORS_MIN_RAM_GB,
        profile_id=VECTORS_MODEL_PROFILE_ID,
        has_vnni=has_vnni,
        vnni_absence_confirmed=vnni_absence_confirmed,
    )


# ── cosine helpers (numpy-free for callers that only need scoring) ────


def cosine_similarity(a, b) -> float:
    """Cosine similarity between two unit-norm vectors.

    Both ``embed()`` outputs are L2-normalized, so this is a straight
    dot product — no division required. Accepts the canonical base64
    form, legacy ``list[float]``, or an already-decoded numpy array;
    decodes lazily so the per-pair API stays compatible with tests and
    fact-dedup's single-pair callsite. For hot loops over thousands of
    candidates, prefer building a stacked matrix once via
    :func:`decode_embedding` and using ``M @ q`` — the recall path does
    that.

    Out-of-band inputs (None, empty, dim mismatch, malformed base64)
    return 0.0 rather than raising; retrieval and dedup are happier
    ranking around an unrankable candidate than crashing because one
    entry was missing its embedding.
    """
    av = decode_embedding(a)
    bv = decode_embedding(b)
    if av is None or bv is None:
        return 0.0
    if av.size == 0 or bv.size == 0 or av.size != bv.size:
        return 0.0
    import numpy as np
    return float(np.dot(av, bv))


def is_cached_embedding_valid(
    entry: dict, current_text: str, current_model_id: str | None,
) -> bool:
    """Decide whether the persisted embedding on ``entry`` is still good.

    Match contract (mirrors ``token_count`` cache in persona.py):
      * embedding field is a non-empty base64 string (canonical form
        emitted by :func:`stamp_embedding_fields`)
      * sha256 of ``current_text`` matches stored ``embedding_text_sha256``
      * ``embedding_model_id`` matches the running service's id

    Legacy ``list[float]`` payloads are intentionally treated as invalid
    so the warmup worker re-stamps them in the new compact form. The
    one-time CPU cost is bounded (small N at migration time) and avoids
    carrying two on-disk shapes forward indefinitely.

    Any mismatch → False, callers should clear the embedding fields and
    re-enqueue the entry for the warmup worker.
    """
    if not isinstance(entry, dict):
        return False
    emb = entry.get("embedding")
    if not isinstance(emb, str) or not emb:
        return False
    if current_model_id is None:
        return False
    if entry.get("embedding_model_id") != current_model_id:
        return False
    if entry.get("embedding_text_sha256") != _embedding_text_sha256(current_text):
        return False
    return True


def clear_embedding_fields(entry: dict) -> None:
    """In-place wipe of the embedding cache. Call from any path that
    rewrites ``entry['text']`` so the next render/recall sees a clean
    cache miss instead of a stale vector tied to the old text."""
    if not isinstance(entry, dict):
        return
    entry["embedding"] = None
    entry["embedding_text_sha256"] = None
    entry["embedding_model_id"] = None


def stamp_embedding_fields(
    entry: dict, vector, text: str, model_id: str,
) -> None:
    """In-place write of an embedding triple onto an entry.

    Stamping all three fields together (vector + text-sha + model-id)
    in one helper prevents the half-written state where ``embedding`` is
    set but the fingerprints aren't, which would otherwise look like a
    legacy entry on the next read and trigger a recompute.

    The vector is encoded to the canonical base64 int8 form before
    storage (see :func:`_encode_vector_int8`). Callers pass the raw
    fp32 list returned by :meth:`EmbeddingService.embed` — this helper
    owns the on-disk encoding so the rest of the pipeline never sees
    it.
    """
    if not isinstance(entry, dict):
        return
    entry["embedding"] = _encode_vector_int8(vector)
    entry["embedding_text_sha256"] = _embedding_text_sha256(text)
    entry["embedding_model_id"] = model_id
