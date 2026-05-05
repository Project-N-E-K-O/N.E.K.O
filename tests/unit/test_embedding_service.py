# -*- coding: utf-8 -*-
"""Unit tests for memory.embeddings — the EmbeddingService fallback gate.

Coverage focuses on the contract that lets callers (fact dedup,
persona/reflection retrieval) treat vectors as a strictly optional
optimization:

  * is_available() reflects every disable reason (user opt-out, low RAM,
    no onnxruntime, no model file)
  * model_id() encodes dim + quantization, so a config flip invalidates
    the embedding cache via fingerprint mismatch
  * cache helpers (is_cached_embedding_valid / clear / stamp) round-trip
    correctly and detect every staleness mode
  * cosine_similarity is forgiving on missing/mismatched inputs
  * hardware-detection auto-resolution maps RAM bands and VNNI presence
    to the documented dim / quantization steps

We do NOT exercise the actual ONNX inference path — that requires the
model file, which lives outside the repo. The tests assert that the
fallback path is correct for the NO_MODEL_FILE case so the missing-file
deploy still works end-to-end.
"""
from __future__ import annotations

import asyncio
import base64

import pytest

from memory.embeddings import (
    DEFAULT_VECTORS_MIN_RAM_GB,
    EmbeddingService,
    EmbeddingState,
    _coerce_dim,
    _embedding_text_sha256,
    _encode_vector_int8,
    _resolve_quantization,
    _select_model_dir,
    build_model_id,
    clear_embedding_fields,
    cosine_similarity,
    decode_embedding,
    is_cached_embedding_valid,
    resolve_dim_for_ram,
    stamp_embedding_fields,
)


# ── pure helpers ─────────────────────────────────────────────────────


def test_resolve_dim_for_ram_below_threshold_disables():
    """Sub-threshold RAM ⇒ None (caller treats as disabled)."""
    assert resolve_dim_for_ram(2.0) is None
    assert resolve_dim_for_ram(DEFAULT_VECTORS_MIN_RAM_GB - 0.1) is None


def test_resolve_dim_for_ram_band_mapping():
    """4-8 / 8-16 / 16+ map to the documented Matryoshka steps."""
    assert resolve_dim_for_ram(4.5) == 64
    assert resolve_dim_for_ram(7.9) == 64
    assert resolve_dim_for_ram(8.0) == 128
    assert resolve_dim_for_ram(15.9) == 128
    assert resolve_dim_for_ram(16.0) == 256
    assert resolve_dim_for_ram(64.0) == 256


def test_resolve_dim_for_ram_none_input_disables():
    """When psutil detection failed we should NOT silently pick a dim."""
    assert resolve_dim_for_ram(None) is None


def test_coerce_dim_auto_delegates_to_ram():
    """`embedding_dim='auto'` mirrors resolve_dim_for_ram exactly."""
    assert _coerce_dim("auto", 8.0) == 128
    assert _coerce_dim("auto", 2.0) is None


def test_coerce_dim_explicit_pinning_preserves_value():
    """An explicit dim must be honoured (even if RAM is higher) — that's
    the override the user opts into to fix a Matryoshka level."""
    assert _coerce_dim(32, 64.0) == 32
    assert _coerce_dim(64, 64.0) == 64
    assert _coerce_dim(128, 4.5) == 128
    assert _coerce_dim(256, 8.0) == 256
    assert _coerce_dim(512, 16.0) == 512
    assert _coerce_dim(768, 16.0) == 768


def test_coerce_dim_invalid_value_falls_back_to_auto():
    """Typos in settings (e.g. dim=100) should not crash; warn + auto."""
    assert _coerce_dim(100, 8.0) == 128
    assert _coerce_dim("not-a-number", 8.0) == 128


def test_resolve_quantization_auto_branches_on_vnni():
    """VNNI present ⇒ INT8; confirmed absent ⇒ None; inconclusive ⇒ INT8."""
    assert _resolve_quantization("auto", has_vnni=True) == "int8"
    assert (
        _resolve_quantization("auto", has_vnni=False, vnni_absence_confirmed=True)
        is None
    )
    assert (
        _resolve_quantization("auto", has_vnni=False, vnni_absence_confirmed=False)
        == "int8"
    )


def test_resolve_quantization_explicit_pinning():
    """User pinning overrides auto — but a warning surfaces when they
    pinned int8 on non-VNNI hardware (asserted via behaviour, not log)."""
    assert _resolve_quantization("int8", has_vnni=False) == "int8"
    assert _resolve_quantization("fp32", has_vnni=True) == "fp32"
    assert _resolve_quantization("fp32", has_vnni=False) == "fp32"


def test_resolve_quantization_invalid_falls_back_to_auto():
    assert _resolve_quantization("garbage", has_vnni=True) == "int8"
    assert (
        _resolve_quantization("garbage", has_vnni=False, vnni_absence_confirmed=True)
        is None
    )
    assert (
        _resolve_quantization("garbage", has_vnni=False, vnni_absence_confirmed=False)
        == "int8"
    )


def test_parse_dim_from_model_id_picks_runtime_dim_under_ambiguous_profile():
    """Codex review PR #1147: ``parse_dim_from_model_id`` must anchor on
    the trailing ``-<dim>d-<quant>`` segment that ``build_model_id``
    emits, not the first ``-<n>d-`` it encounters.  If a profile name
    itself contains a ``-Nd-`` segment (e.g. an upstream variant like
    ``model-384d-v2``), a non-anchored parser would pick the profile
    dim (384) instead of the runtime dim (256), and
    ``is_cached_embedding_valid`` would reject every freshly stamped
    vector forever.  Lock the contract here."""
    from memory.embeddings import build_model_id, parse_dim_from_model_id

    ambiguous_id = build_model_id("model-384d-v2", 256, "int8")
    assert ambiguous_id == "model-384d-v2-256d-int8"
    assert parse_dim_from_model_id(ambiguous_id) == 256
    # Same with fp32 quant (the other valid suffix):
    assert (
        parse_dim_from_model_id(build_model_id("foo-128d-bar", 64, "fp32"))
        == 64
    )
    # Unparseable / unknown shape returns None — caller falls back to
    # whatever signal it can find without crashing.
    assert parse_dim_from_model_id(None) is None
    assert parse_dim_from_model_id("") is None
    assert parse_dim_from_model_id("not-a-model-id") is None
    # Trailing form without a recognised quant suffix → None (so a
    # future quantization keyword opts out gracefully rather than
    # silently mis-extracting).
    assert parse_dim_from_model_id("local-text-retrieval-v1-256d-fp16") is None


def test_decode_vector_int8_rejects_garbage_suffix_and_nan_scale():
    """CodeRabbit review PR #1147: ``_decode_vector_int8`` must reject
    payloads with non-base64 suffix bytes and non-finite scales.

    ``base64.b64decode(..., validate=False)`` (the previous default)
    silently skips characters outside the alphabet, so an attacker /
    bit-rot scenario that appends garbage would still decode to a
    plausible-looking vector. ``np.frombuffer(...)`` on a bad scale
    can produce NaN or ±Inf, which then propagates through every dot
    product — a single corrupt cache row poisoning the entire recall
    score distribution."""
    from memory.embeddings import _decode_vector_int8, _encode_vector_int8
    import numpy as np

    good = _encode_vector_int8([0.1, 0.2, 0.3, 0.4])
    # Sanity: the well-formed payload still decodes.
    assert _decode_vector_int8(good) is not None

    # Suffix garbage (! is not in the base64 alphabet) → reject.
    assert _decode_vector_int8(good + "!!!!") is None
    assert _decode_vector_int8("not base 64 at all") is None

    # NaN scale: hand-build the wire format with the canonical scale
    # bytes replaced by NaN bits in fp16. Without the isfinite gate,
    # decoding would return an array full of NaN.
    nan_payload = (
        np.array([np.nan], dtype=np.float16).tobytes()
        + np.array([1, 2, 3, 4], dtype=np.int8).tobytes()
    )
    nan_b64 = base64.b64encode(nan_payload).decode("ascii")
    assert _decode_vector_int8(nan_b64) is None

    # +Inf scale rejected too.
    inf_payload = (
        np.array([np.inf], dtype=np.float16).tobytes()
        + np.array([1, 2, 3, 4], dtype=np.int8).tobytes()
    )
    inf_b64 = base64.b64encode(inf_payload).decode("ascii")
    assert _decode_vector_int8(inf_b64) is None


def test_build_model_id_encodes_axes():
    """Cache fingerprint must change with any of base / dim / quant."""
    a = build_model_id("local-text-retrieval-v1", 128, "int8")
    b = build_model_id("local-text-retrieval-v1", 256, "int8")
    c = build_model_id("local-text-retrieval-v1", 128, "fp32")
    d = build_model_id("other", 128, "int8")
    assert a == "local-text-retrieval-v1-128d-int8"
    assert len({a, b, c, d}) == 4


# ── EmbeddingService construction / disable matrix ─────────────────


def _service(**overrides) -> EmbeddingService:
    """Build a service with safe defaults for unit tests — no real model
    file, RAM injected, VNNI injected. The model_dir is a path that
    won't exist so request_load() consistently hits NO_MODEL_FILE."""
    defaults = dict(
        model_dir="/nonexistent/embedding_models",
        enabled=True,
        embedding_dim_setting="auto",
        quantization_setting="auto",
        min_ram_gb=DEFAULT_VECTORS_MIN_RAM_GB,
        ram_gb=8.0,
        has_vnni=True,
    )
    defaults.update(overrides)
    return EmbeddingService(**defaults)


def test_service_user_disabled_short_circuits_at_construction():
    svc = _service(enabled=False)
    assert svc.is_disabled()
    assert svc.is_available() is False
    assert svc.disable_reason() == "user_disabled_via_config"
    assert svc.model_id() is None


def test_service_low_ram_disables_with_correct_reason():
    svc = _service(ram_gb=2.0)
    assert svc.is_disabled()
    assert svc.disable_reason() == "ram_below_threshold"
    assert svc.model_id() is None


def test_service_unknown_ram_disables(monkeypatch):
    """psutil failure ⇒ detect_total_ram_gb returns None ⇒ disabled.
    We treat 'unknown' as 'unsafe to load on a possibly-tiny VM'.

    Patches the symbol at the module level via the same `from memory.embeddings`
    import path the test file already uses — keeps a single import style."""
    from memory import embeddings as embeddings_module
    monkeypatch.setattr(embeddings_module, "detect_total_ram_gb", lambda: None)
    svc = EmbeddingService(
        model_dir="/nonexistent/embedding_models",
        enabled=True,
        embedding_dim_setting="auto",
        quantization_setting="auto",
        min_ram_gb=DEFAULT_VECTORS_MIN_RAM_GB,
        has_vnni=False,
    )
    assert svc.is_disabled()
    assert svc.disable_reason() == "ram_below_threshold"


def test_service_healthy_construction_stays_init_until_load():
    """Healthy hardware + enabled config ⇒ INIT, not READY. The actual
    READY transition only happens after request_load() succeeds."""
    svc = _service(ram_gb=16.0)
    assert svc.is_disabled() is False
    assert svc.is_available() is False
    assert svc._state == EmbeddingState.INIT
    assert svc.model_id() == "local-text-retrieval-v1-256d-int8"
    assert svc.dim() == 256
    assert svc.quantization() == "int8"


def test_service_dim_pinning_overrides_auto():
    svc = _service(ram_gb=4.5, embedding_dim_setting=256)
    # Even on 4.5 GB RAM, the pinned dim wins.
    assert svc.dim() == 256
    assert svc.model_id().endswith("256d-int8")


def test_service_fp32_setting_uses_fp32_onnx_path():
    svc = _service(
        ram_gb=16.0,
        quantization_setting="fp32",
        model_dir="/models",
        has_vnni=True,
    )
    assert not svc.is_disabled()
    assert svc.quantization() == "fp32"
    assert svc.model_id() == "local-text-retrieval-v1-256d-fp32"
    assert svc._model_file_path().replace("\\", "/").endswith(
        "/local-text-retrieval-v1/onnx/model.onnx",
    )


def test_service_auto_without_vnni_disables():
    svc = _service(
        ram_gb=16.0,
        has_vnni=False,
        vnni_absence_confirmed=True,
        quantization_setting="auto",
    )
    assert svc.is_disabled()
    assert svc.disable_reason() == "avx_vnni_required_for_int8_bundle"


def test_service_auto_inconclusive_vnni_stays_int8():
    """Windows/macOS without cpuinfo: absence not confirmed → still try INT8."""
    svc = _service(
        ram_gb=16.0,
        has_vnni=False,
        vnni_absence_confirmed=False,
        quantization_setting="auto",
    )
    assert not svc.is_disabled()
    assert svc.quantization() == "int8"


def test_service_uses_profile_onnx_layout():
    """The profile uses task ONNX exports under onnx/model*.onnx."""
    int8 = _service(
        ram_gb=16.0,
        quantization_setting="int8",
        model_dir="/models",
    )
    fp32 = _service(
        ram_gb=16.0,
        quantization_setting="fp32",
        model_dir="/models",
        has_vnni=True,
    )
    assert int8._model_file_path().replace("\\", "/").endswith(
        "/local-text-retrieval-v1/onnx/model_quantized.onnx",
    )
    assert fp32._model_file_path().replace("\\", "/").endswith(
        "/local-text-retrieval-v1/onnx/model.onnx",
    )
    assert int8._tokenizer_file_path().replace("\\", "/").endswith(
        "/local-text-retrieval-v1/tokenizer.json",
    )


def _populate_complete_profile(
    model_dir, profile_id="local-text-retrieval-v1", *, variants=("fp32", "int8"),
):
    """Create the minimum file set _profile_is_complete accepts (tokenizer
    + one model variant + its onnx_data sidecar). ``variants`` controls
    which ONNX variant files get written so callers can simulate
    half-bundled / quantization-mismatched profiles."""
    profile = model_dir / profile_id
    (profile / "onnx").mkdir(parents=True)
    (profile / "tokenizer.json").write_bytes(b"{}")
    if "fp32" in variants:
        (profile / "onnx" / "model.onnx").write_bytes(b"x")
        (profile / "onnx" / "model.onnx_data").write_bytes(b"x")
    if "int8" in variants:
        (profile / "onnx" / "model_quantized.onnx").write_bytes(b"x")
        (profile / "onnx" / "model_quantized.onnx_data").write_bytes(b"x")
    return profile


def test_select_model_dir_prefers_app_data_profile(tmp_path):
    app_model_dir = tmp_path / "app" / "embedding_models"
    bundled_model_dir = tmp_path / "bundle" / "data" / "embedding_models"
    _populate_complete_profile(app_model_dir)
    _populate_complete_profile(bundled_model_dir)

    assert _select_model_dir(str(app_model_dir), "local-text-retrieval-v1") == str(app_model_dir)


def test_select_model_dir_falls_back_to_bundled_profile(tmp_path, monkeypatch):
    app_model_dir = tmp_path / "app" / "embedding_models"
    bundled_model_dir = tmp_path / "bundle" / "data" / "embedding_models"
    _populate_complete_profile(bundled_model_dir)

    from memory import embeddings as embeddings_module

    monkeypatch.setattr(
        embeddings_module,
        "_bundled_model_dirs",
        lambda: [str(bundled_model_dir)],
    )
    assert (
        _select_model_dir(str(app_model_dir), "local-text-retrieval-v1")
        == str(bundled_model_dir)
    )


def test_select_model_dir_skips_incomplete_app_data_for_bundled(tmp_path, monkeypatch):
    """A half-downloaded app-data profile (e.g. tokenizer present but onnx
    sidecar missing) must NOT short-circuit the bundled fallback —
    selecting it would just sticky-disable vectors at session load even
    though the bundled profile on disk is complete."""
    app_model_dir = tmp_path / "app" / "embedding_models"
    bundled_model_dir = tmp_path / "bundle" / "data" / "embedding_models"

    half_downloaded = app_model_dir / "local-text-retrieval-v1" / "onnx"
    half_downloaded.mkdir(parents=True)
    (app_model_dir / "local-text-retrieval-v1" / "tokenizer.json").write_bytes(b"{}")
    (half_downloaded / "model.onnx").write_bytes(b"x")
    # missing model.onnx_data — incomplete profile

    _populate_complete_profile(bundled_model_dir)

    from memory import embeddings as embeddings_module

    monkeypatch.setattr(
        embeddings_module,
        "_bundled_model_dirs",
        lambda: [str(bundled_model_dir)],
    )
    assert (
        _select_model_dir(str(app_model_dir), "local-text-retrieval-v1")
        == str(bundled_model_dir)
    )


def test_select_model_dir_skips_app_data_missing_runtime_variant(tmp_path, monkeypatch):
    """If app-data only has the fp32 variant but the runtime resolved to
    int8, _select_model_dir must fall through to a bundled profile that
    actually has int8 — otherwise _load_session_blocking would just
    sticky-disable vectors looking for model_quantized.onnx in app-data."""
    app_model_dir = tmp_path / "app" / "embedding_models"
    bundled_model_dir = tmp_path / "bundle" / "data" / "embedding_models"

    _populate_complete_profile(app_model_dir, variants=("fp32",))
    _populate_complete_profile(bundled_model_dir, variants=("fp32", "int8"))

    from memory import embeddings as embeddings_module

    monkeypatch.setattr(
        embeddings_module,
        "_bundled_model_dirs",
        lambda: [str(bundled_model_dir)],
    )
    assert (
        _select_model_dir(str(app_model_dir), "local-text-retrieval-v1", "int8")
        == str(bundled_model_dir)
    )
    # Without quantization, only the shipped INT8 variant counts — fp32-only
    # app-data must not mask a complete bundled int8 profile.
    assert (
        _select_model_dir(str(app_model_dir), "local-text-retrieval-v1")
        == str(bundled_model_dir)
    )


def test_select_model_dir_skips_zero_byte_app_data_for_bundled(tmp_path, monkeypatch):
    """Zero-byte residue (e.g. an interrupted download that left empty
    files behind) must be treated the same as half-downloaded — the
    profile dir exists with the right names but no usable bytes, and
    selecting it would just sticky-disable at session load."""
    app_model_dir = tmp_path / "app" / "embedding_models"
    bundled_model_dir = tmp_path / "bundle" / "data" / "embedding_models"

    onnx_dir = app_model_dir / "local-text-retrieval-v1" / "onnx"
    onnx_dir.mkdir(parents=True)
    (app_model_dir / "local-text-retrieval-v1" / "tokenizer.json").write_bytes(b"")
    (onnx_dir / "model.onnx").write_bytes(b"")
    (onnx_dir / "model.onnx_data").write_bytes(b"")

    _populate_complete_profile(bundled_model_dir)

    from memory import embeddings as embeddings_module

    monkeypatch.setattr(
        embeddings_module,
        "_bundled_model_dirs",
        lambda: [str(bundled_model_dir)],
    )
    assert (
        _select_model_dir(str(app_model_dir), "local-text-retrieval-v1")
        == str(bundled_model_dir)
    )


@pytest.mark.asyncio
async def test_request_load_missing_model_file_disables_sticky():
    """No model file on disk ⇒ DISABLED with NO_MODEL_FILE. Subsequent
    request_load() calls must not retry — the disable is sticky for the
    process lifetime, by design."""
    svc = _service(ram_gb=8.0)
    ready = await svc.request_load()
    assert ready is False
    assert svc.is_disabled()
    assert svc.disable_reason() == "model_file_missing"
    # Sticky: a second call must short-circuit on the state guard, not
    # repeat the file-existence check (which also returns False).
    ready2 = await svc.request_load()
    assert ready2 is False
    assert svc.disable_reason() == "model_file_missing"


@pytest.mark.asyncio
async def test_request_load_concurrent_callers_single_flight():
    """Two coroutines call request_load() at the same time → both
    converge on the same final state without double-loading."""
    svc = _service(ram_gb=8.0)
    results = await asyncio.gather(svc.request_load(), svc.request_load())
    assert results == [False, False]
    assert svc.is_disabled()


@pytest.mark.asyncio
async def test_embed_returns_none_when_service_not_ready():
    """Caller contract: embed() MUST return None when not available so
    callers fall back to the pre-vector code path."""
    svc = _service(enabled=False)
    assert await svc.embed("hello") is None
    batch = await svc.embed_batch(["a", "b"])
    assert batch == [None, None]


@pytest.mark.asyncio
async def test_embed_batch_preserves_index_alignment_with_empty_inputs():
    """Empty strings get None at the right slot — callers keying off
    list index (e.g. zip with the input list) must not desync."""
    svc = _service(enabled=False)
    out = await svc.embed_batch(["a", "", "b", ""])
    assert out == [None, None, None, None]


@pytest.mark.asyncio
async def test_embed_empty_string_returns_none_even_when_disabled():
    svc = _service(enabled=False)
    assert await svc.embed("") is None


def test_infer_uses_last_token_pooling_and_matryoshka_truncation():
    """The default profile specifies last-token pooling. This locks the
    runtime against accidentally reverting to mean pooling."""
    import numpy as np

    class _Encoding:
        def __init__(self, ids):
            self.ids = ids
            self.attention_mask = [1] * len(ids)

    class _Tokenizer:
        def encode_batch(self, texts):
            return [_Encoding([1, 2, 3]), _Encoding([4])]

    class _Input:
        def __init__(self, name):
            self.name = name

    class _Session:
        def __init__(self):
            self.feeds = None

        def get_inputs(self):
            return [_Input("input_ids"), _Input("attention_mask"), _Input("token_type_ids")]

        def run(self, _outputs, feeds):
            self.feeds = feeds
            return [np.array([
                [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [3.0, 4.0, 0.0]],
                [[5.0, 0.0, 0.0], [9.0, 9.0, 9.0], [8.0, 8.0, 8.0]],
            ], dtype=np.float32)]

    svc = _service(ram_gb=16.0, has_vnni=True)
    svc._dim = 2  # private override keeps the fixture tiny while testing truncation
    session = _Session()
    svc._session = session
    svc._tokenizer = _Tokenizer()
    out = svc._infer_blocking(["abc", "d"])

    assert out[0] == pytest.approx([0.6, 0.8])
    assert out[1] == pytest.approx([1.0, 0.0])
    assert "token_type_ids" in session.feeds


# ── cosine + cache helpers ────────────────────────────────────────


def test_cosine_similarity_dot_product_for_unit_vectors():
    """Service emits L2-normalized vectors → cosine = dot product.
    Accepts both legacy ``list[float]`` and the canonical base64
    string form on either side, so fact-dedup and tests can pass in
    raw fp32 vectors without round-tripping through stamp."""
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    c = [0.0, 1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(1.0)
    assert cosine_similarity(a, c) == pytest.approx(0.0)
    # Mixed: encoded string ↔ raw list must agree within int8 tolerance.
    encoded_a = _encode_vector_int8(a)
    assert cosine_similarity(encoded_a, b) == pytest.approx(1.0, abs=0.01)
    assert cosine_similarity(encoded_a, _encode_vector_int8(c)) == pytest.approx(0.0, abs=0.01)


def test_cosine_similarity_safe_on_missing_or_mismatched_inputs():
    """Caller supplies vectors from arbitrary entries — None / empty /
    dim mismatch / corrupt base64 must yield 0.0 rather than raising.
    Stale dim is the likely real-world cause (entry from a previous
    model_id)."""
    assert cosine_similarity(None, [1.0]) == 0.0
    assert cosine_similarity([1.0], None) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0
    # Empty / corrupt base64 both fall through to 0.0 instead of
    # raising up into the retrieval / dedup loops.
    assert cosine_similarity("", _encode_vector_int8([1.0, 0.0])) == 0.0
    assert cosine_similarity("not-base64!!!", _encode_vector_int8([1.0, 0.0])) == 0.0


def test_encode_vector_int8_round_trip_within_tolerance():
    """The base64+int8 wire format must decode within the documented
    quantization noise envelope (~0.4% × max-abs per dim).  Locks the
    format so a future encoder change doesn't silently widen the
    cosine error past what callers tolerate."""
    import numpy as np

    rng = np.random.default_rng(seed=0xDEADBEEF)
    # L2-normalized fp32 vector, 256d — matches the default Matryoshka
    # level on a 16+ GB machine.
    vec = rng.standard_normal(256).astype(np.float32)
    vec = vec / float(np.linalg.norm(vec))

    encoded = _encode_vector_int8(vec)
    assert isinstance(encoded, str) and encoded
    decoded = decode_embedding(encoded)
    assert decoded is not None and decoded.shape == (256,)
    # Per-dim error bounded by half a quantization step × scale.
    max_abs = float(np.max(np.abs(vec)))
    assert np.max(np.abs(decoded - vec)) <= (max_abs / 127.0) + 1e-3
    # Cosine against the original is essentially 1.0 — small deviation
    # from quantization, but well inside what the LLM rerank can absorb.
    cos = float(np.dot(decoded, vec) / (
        np.linalg.norm(decoded) * np.linalg.norm(vec)
    ))
    assert cos >= 0.999


def test_encode_vector_int8_handles_zero_and_empty():
    """Degenerate inputs — empty list, all-zero vector — must encode
    to a decodable payload rather than crashing the stamp path. An
    upstream embed() should never return these, but a defence is
    cheap and keeps the encoder total."""
    import numpy as np
    encoded_empty = _encode_vector_int8([])
    decoded_empty = decode_embedding(encoded_empty)
    assert decoded_empty is not None and decoded_empty.size == 0

    encoded_zero = _encode_vector_int8([0.0] * 8)
    decoded_zero = decode_embedding(encoded_zero)
    assert decoded_zero is not None
    assert np.allclose(decoded_zero, np.zeros(8, dtype=np.float32))


def test_decode_embedding_accepts_legacy_list_and_numpy():
    """The decode helper is the single entry point used by recall and
    by cosine_similarity — it must accept all three live shapes:
    canonical base64 string, legacy list[float], and an already-
    decoded numpy array (so the matmul path can pass through values
    it produced earlier in the same call)."""
    import numpy as np

    raw = [0.5, 0.5, 0.5, 0.5]
    via_list = decode_embedding(raw)
    assert via_list is not None
    assert via_list.tolist() == pytest.approx(raw)

    via_str = decode_embedding(_encode_vector_int8(raw))
    assert via_str is not None and via_str.shape == (4,)
    assert via_str.tolist() == pytest.approx(raw, abs=0.005)

    via_np = decode_embedding(np.asarray(raw, dtype=np.float32))
    assert via_np is not None
    assert via_np.tolist() == pytest.approx(raw)

    assert decode_embedding(None) is None
    assert decode_embedding("") is None
    assert decode_embedding([]) is None


def test_is_cached_embedding_valid_full_match():
    """Vector + sha + model_id all match → valid.  Vector length must
    match the dim segment of model_id (3 here) or the strengthened
    validity check rejects it as a wrong-dim payload."""
    text = "主人喜欢猫"
    entry = {
        "text": text,
        "embedding": _encode_vector_int8([0.1, 0.2, 0.3]),
        "embedding_text_sha256": _embedding_text_sha256(text),
        "embedding_model_id": "local-text-retrieval-v1-3d-int8",
    }
    assert is_cached_embedding_valid(
        entry, text, "local-text-retrieval-v1-3d-int8",
    )


def test_is_cached_embedding_valid_text_mismatch():
    """Stored sha encodes old_text; a rewrite (text change) must
    invalidate so the next sweep re-embeds the new text."""
    entry = {
        "text": "new",
        "embedding": _encode_vector_int8([0.1, 0.2]),
        "embedding_text_sha256": _embedding_text_sha256("old"),
        "embedding_model_id": "local-text-retrieval-v1-128d-int8",
    }
    assert not is_cached_embedding_valid(
        entry, "new", "local-text-retrieval-v1-128d-int8",
    )


def test_is_cached_embedding_valid_model_id_mismatch():
    """Dim or quant flip → invalidate. Same vector under a different
    projection is not comparable."""
    text = "x"
    entry = {
        "text": text,
        "embedding": _encode_vector_int8([0.1, 0.2]),
        "embedding_text_sha256": _embedding_text_sha256(text),
        "embedding_model_id": "local-text-retrieval-v1-128d-int8",
    }
    assert not is_cached_embedding_valid(
        entry, text, "local-text-retrieval-v1-256d-int8",
    )
    assert not is_cached_embedding_valid(
        entry, text, "local-text-retrieval-v1-128d-fp32",
    )


def test_is_cached_embedding_valid_missing_or_empty_embedding():
    text = "x"
    base = {
        "text": text,
        "embedding_text_sha256": _embedding_text_sha256(text),
        "embedding_model_id": "local-text-retrieval-v1-128d-int8",
    }
    assert not is_cached_embedding_valid(
        {**base, "embedding": None},
        text,
        "local-text-retrieval-v1-128d-int8",
    )
    assert not is_cached_embedding_valid(
        {**base, "embedding": ""},
        text,
        "local-text-retrieval-v1-128d-int8",
    )


def test_is_cached_embedding_valid_corrupt_base64_invalidates():
    """Codex review PR #1147 P1: a row whose ``embedding`` is a non-empty
    string but doesn't actually decode must be treated as invalid so
    the worker re-stamps it.  Without this, corrupt cache values stick
    forever (typeof check passes, worker skips) while recall/dedup
    silently treat them as missing — a permanent retrieval-quality
    regression for affected entries."""
    text = "x"
    entry = {
        "text": text,
        "embedding": "this-is-not-valid-base64-!!!",
        "embedding_text_sha256": _embedding_text_sha256(text),
        "embedding_model_id": "local-text-retrieval-v1-128d-int8",
    }
    assert not is_cached_embedding_valid(
        entry, text, "local-text-retrieval-v1-128d-int8",
    )


def test_is_cached_embedding_valid_wrong_dim_payload_invalidates():
    """A truncated or mis-quantized payload that decodes successfully
    but to the wrong length must also invalidate. ``model_id`` encodes
    dim by construction (build_model_id) so we can verify the decoded
    length without re-running the model."""
    text = "x"
    # Encode at 4d but claim 128d via model_id — simulates a payload
    # written under one config and read under another.
    entry = {
        "text": text,
        "embedding": _encode_vector_int8([0.1, 0.2, 0.3, 0.4]),
        "embedding_text_sha256": _embedding_text_sha256(text),
        "embedding_model_id": "local-text-retrieval-v1-128d-int8",
    }
    assert not is_cached_embedding_valid(
        entry, text, "local-text-retrieval-v1-128d-int8",
    )


def test_is_cached_embedding_valid_legacy_list_form_invalidates():
    """Pre-quantization persisted shape is ``list[float]``. We treat
    those as invalid so the warmup worker re-stamps them in the new
    base64+int8 form on its next sweep — otherwise a mixed-shape
    corpus would persist forever, and downstream code paths would
    have to handle two on-disk schemas indefinitely."""
    text = "x"
    entry = {
        "text": text,
        "embedding": [0.1, 0.2, 0.3],
        "embedding_text_sha256": _embedding_text_sha256(text),
        "embedding_model_id": "local-text-retrieval-v1-128d-int8",
    }
    assert not is_cached_embedding_valid(
        entry, text, "local-text-retrieval-v1-128d-int8",
    )


def test_is_cached_embedding_valid_disabled_service_never_valid():
    """When the running service has no model_id (disabled) every cached
    vector is considered invalid — callers shouldn't compare cosines
    using a frozen vector against nothing."""
    text = "x"
    entry = {
        "text": text,
        "embedding": _encode_vector_int8([0.1, 0.2]),
        "embedding_text_sha256": _embedding_text_sha256(text),
        "embedding_model_id": "local-text-retrieval-v1-128d-int8",
    }
    assert not is_cached_embedding_valid(entry, text, None)


def test_clear_embedding_fields_in_place():
    """clear() wipes the whole triple — half-cleared state would look
    like a legacy entry on the next read and trigger an unwanted reload."""
    entry = {
        "embedding": _encode_vector_int8([0.1, 0.2]),
        "embedding_text_sha256": "abc",
        "embedding_model_id": "x",
    }
    clear_embedding_fields(entry)
    assert entry["embedding"] is None
    assert entry["embedding_text_sha256"] is None
    assert entry["embedding_model_id"] is None


def test_stamp_embedding_fields_writes_full_triple():
    """stamp() must set vector + sha + model_id atomically (from the
    callsite's POV) — partial writes break is_cached_embedding_valid.
    The on-disk vector form is base64 (canonical), and the helper
    decodes back to the original values within int8 quantization
    tolerance."""
    entry: dict = {}
    stamp_embedding_fields(
        entry,
        [0.1, 0.2, 0.3],
        "hello",
        "local-text-retrieval-v1-128d-int8",
    )
    assert isinstance(entry["embedding"], str) and entry["embedding"]
    decoded = decode_embedding(entry["embedding"])
    assert decoded is not None and decoded.shape == (3,)
    # int8 quantization with per-vector scale → max-abs axis is exact,
    # the rest are within ~0.4% × scale.
    assert decoded.tolist() == pytest.approx([0.1, 0.2, 0.3], abs=0.002)
    assert entry["embedding_text_sha256"] == _embedding_text_sha256("hello")
    assert (
        entry["embedding_model_id"]
        == "local-text-retrieval-v1-128d-int8"
    )
    # Source isn't aliased through to disk — mutating after stamp must
    # not corrupt the cache (the encode pass copies into a numpy buffer).
    src = [9.0, 9.0]
    stamp_embedding_fields(entry, src, "hello", "x")
    src.append(99.0)
    decoded2 = decode_embedding(entry["embedding"])
    assert decoded2 is not None and decoded2.shape == (2,)


def test_stamp_then_check_round_trips():
    text = "round-trip text"
    # Vector dim and model_id's dim segment must agree under the
    # strengthened validity check — use a 3d model_id to match the
    # 3d test vector.
    model_id = "local-text-retrieval-v1-3d-fp32"
    entry: dict = {"text": text}
    stamp_embedding_fields(entry, [1.0, 0.0, 0.0], text, model_id)
    assert is_cached_embedding_valid(entry, text, model_id)
    # Text changes → invalid.
    assert not is_cached_embedding_valid(entry, text + "!", model_id)
    # Model change → invalid.
    assert not is_cached_embedding_valid(
        entry, text, "local-text-retrieval-v1-128d-fp32",
    )
