# -*- coding: utf-8 -*-
"""Unit tests for the token-budget sub-batching in
``EmbeddingService._infer_blocking``.

Background: ``_infer_blocking`` previously used pad-to-longest + a fixed batch
(BATCH_SIZE=16), so a single long text pasted into recent would pad all 16
rows to thousands of tokens and spike activation memory into multiple GB
(measured RSS rising from 1.1 GB to 12.4 GB). The fix switches to bucketing:
``batch_size × max_len ≤ _INFER_BATCH_MAX_TOKENS``.

Test goals:
- bucket boundaries are correct (full bucket vs. must-flush)
- a single item whose token count > budget still runs (an empty bucket must
  accept it)
- output order matches input (sorted by length within a bucket, restored by
  original idx on flush)
- no dependency on onnxruntime/tokenizers/numpy (uses a monkeypatched session
  + fake encoded objects), so it runs without those heavy deps installed.
"""
from __future__ import annotations

import asyncio
import threading
import types

import pytest

# numpy required for real embedding inference; skip when absent, matching
# test_embeddings_fallback.py's policy: test only capable environments.
np = pytest.importorskip("numpy")


class _FakeEncoded:
    """Minimal tokenizers.Encoding fake using ids / attention_mask only."""
    def __init__(self, n_tokens: int):
        self.ids = list(range(1, n_tokens + 1))
        self.attention_mask = [1] * n_tokens


class _FakeSession:
    """Fake ort.InferenceSession.run returning fixed-hidden_dim token embeddings.

    Record (batch_size, seq_len) per run, exposing bucketing for assertions.

    Design: each row chooses a unique nonzero dimension from ids[i, 0], sets it 1.0,
    all others zero. Thus:

    - L2 normalization retains that unit-vector direction, distinct rather than collapsing
      to one direction. CodeRabbit PR #1585 caught prior single-dimension differing
      amplitudes all normalizing to [1, 0, ..., 0], letting reordered outputs
      pass assertions.
    - Tests read marker dimensions via ``argmax`` to reconstruct sample→output
      mapping and verify bucketing/restoration preserves indices.
    """
    HIDDEN = 32  # Small hidden size for tests, unrelated to production 256/768.

    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def get_inputs(self):
        # Expose input_ids only, skipping attention_mask / token_type_ids
        # branches independently covered in production tests; focus on bucketing.
        inp = types.SimpleNamespace(name="input_ids")
        return [inp]

    def run(self, output_names, feeds):
        ids = feeds["input_ids"]
        batch, seq = ids.shape
        self.calls.append((batch, seq))
        out = np.zeros((batch, seq, self.HIDDEN), dtype=np.float32)
        for i in range(batch):
            # marker ∈ [1, HIDDEN-1], avoiding zero for unique argmax; IDs start one,
            # mod (HIDDEN-1) + 1 ensures valid range.
            marker = ((int(ids[i, 0]) - 1) % (self.HIDDEN - 1)) + 1
            out[i, :, marker] = 1.0
        return [out]


@pytest.fixture
def service_with_fake_session(monkeypatch):
    """Return EmbeddingService with monkeypatched session/tokenizer.

    Use ``object.__new__`` to bypass ``__init__`` and config_manager/file-path/RAM
    side effects, testing _infer_blocking bucketing only.
    """
    # ``object.__new__`` bypasses __init__; fixture invokes neither _build_default_service
    # nor _load_session_blocking, requiring no tokenizers/onnxruntime.
    # Top-level ``import memory.embeddings`` loads pure Python only, without heavy dependency
    # side effects; importorskip covers rare stripped-package builds.
    embeddings = pytest.importorskip("memory.embeddings")
    svc = object.__new__(embeddings.EmbeddingService)
    fake_sess = _FakeSession()
    svc._session = fake_sess
    svc._tokenizer = object()  # Non-None placeholder, never called.
    svc._dim = None  # No Matryoshka truncation.
    svc._load_lock = asyncio.Lock()
    svc._lifecycle_condition = asyncio.Condition()
    svc._active_operations = 0
    svc._closing = False
    return svc, fake_sess, embeddings


def _run_with_lengths(svc, fake_sess, embeddings_mod, lengths):
    """Feed pretokenized encoded lists directly to _run_bucket / _infer_blocking
    for bucketing, bypassing tokenizer.encode_batch via monkeypatch.

    Each encoded gets unique first-token ID (1, 2, 3, ...) matching _FakeSession
    marker dimensions; argmax(output[i]) identifies original sample
    for slot i, giving order-regression assertions real force.
    """
    encoded = [_FakeEncoded(n) for n in lengths]
    for i, enc in enumerate(encoded, start=1):
        if enc.ids:
            enc.ids[0] = i  # Inject stable marker; later tokens remain dummy.
    svc._tokenizer = types.SimpleNamespace(encode_batch=lambda texts: encoded)
    # texts is placeholder only; match encoded length.
    texts = ["x"] * len(lengths)
    return svc._infer_blocking(texts)


def test_embedding_service_close_releases_native_owners(service_with_fake_session):
    svc, _session, embeddings_mod = service_with_fake_session
    svc._state = embeddings_mod.EmbeddingState.READY

    asyncio.run(svc.close())

    assert svc._session is None
    assert svc._tokenizer is None
    assert svc._state is embeddings_mod.EmbeddingState.CLOSED
    assert asyncio.run(svc.request_load()) is False


@pytest.mark.asyncio
async def test_embedding_close_waits_for_inflight_inference(
    service_with_fake_session,
) -> None:
    svc, _session, embeddings_mod = service_with_fake_session
    inference_started = threading.Event()
    release_inference = threading.Event()
    svc._state = embeddings_mod.EmbeddingState.READY

    def _blocking_inference(_texts):
        inference_started.set()
        assert release_inference.wait(timeout=2.0)
        return [[1.0]]

    svc._infer_blocking = _blocking_inference
    embed_task = asyncio.create_task(svc.embed("hello"))
    assert await asyncio.to_thread(inference_started.wait, 2.0)
    embed_task.cancel()
    await asyncio.sleep(0)
    close_task = asyncio.create_task(svc.close())
    await asyncio.sleep(0)

    assert svc._closing is True
    assert svc._active_operations == 1
    assert not close_task.done()
    release_inference.set()

    cancelled_result = await asyncio.gather(embed_task, return_exceptions=True)
    assert isinstance(cancelled_result[0], asyncio.CancelledError)
    assert await close_task is None
    assert svc._session is None
    assert svc._tokenizer is None
    assert svc._state is embeddings_mod.EmbeddingState.CLOSED


@pytest.mark.asyncio
async def test_embedding_load_cannot_publish_after_close_starts(
    service_with_fake_session,
) -> None:
    svc, _session, embeddings_mod = service_with_fake_session
    load_started = threading.Event()
    release_load = threading.Event()
    svc._state = embeddings_mod.EmbeddingState.INIT

    def _blocking_load() -> None:
        load_started.set()
        assert release_load.wait(timeout=2.0)
        svc._session = object()
        svc._tokenizer = object()

    svc._load_session_blocking = _blocking_load
    load_task = asyncio.create_task(svc.request_load())
    assert await asyncio.to_thread(load_started.wait, 2.0)
    load_task.cancel()
    await asyncio.sleep(0)
    close_task = asyncio.create_task(svc.close())
    await asyncio.sleep(0)

    assert svc._closing is True
    assert svc._active_operations == 1
    assert not close_task.done()
    release_load.set()

    cancelled_result = await asyncio.gather(load_task, return_exceptions=True)
    assert isinstance(cancelled_result[0], asyncio.CancelledError)
    assert await close_task is None
    assert svc._session is None
    assert svc._tokenizer is None
    assert svc._state is embeddings_mod.EmbeddingState.CLOSED


def test_short_batch_runs_in_single_bucket(service_with_fake_session):
    """All short texts total far below budget fit one bucket, matching old fast path."""
    svc, sess, emb = service_with_fake_session
    out = _run_with_lengths(svc, sess, emb, [10, 12, 15, 8])
    assert len(out) == 4
    assert len(sess.calls) == 1
    batch, seq = sess.calls[0]
    assert batch == 4
    assert seq == 15  # Pad to longest in bucket.


def test_long_entries_split_into_multiple_buckets(service_with_fake_session):
    """Sixteen 1024-token entries exactly fill 16384 budget;
    one more splits buckets, proving ``batch × max_len ≤ budget`` gate."""
    svc, sess, emb = service_with_fake_session
    lengths = [1024] * 17
    out = _run_with_lengths(svc, sess, emb, lengths)
    assert len(out) == 17
    # First bucket 16 × 1024 = 16384 = budget exactly; seventeenth occupies second alone.
    assert len(sess.calls) == 2
    batches = sorted(c[0] for c in sess.calls)
    assert batches == [1, 16]


def test_single_overlong_entry_still_runs(service_with_fake_session):
    """Empty bucket must accept a single entry >budget, or can never flush."""
    svc, sess, emb = service_with_fake_session
    out = _run_with_lengths(svc, sess, emb, [emb._INFER_BATCH_MAX_TOKENS + 1000])
    assert len(out) == 1
    assert len(sess.calls) == 1
    batch, seq = sess.calls[0]
    assert batch == 1
    assert seq == emb._INFER_BATCH_MAX_TOKENS + 1000


def test_mixed_length_preserves_original_order(service_with_fake_session):
    """Bucket sorts lengths, but _infer_blocking restores original-index output order,
    or zip(texts, vectors) misaligns every cache key.

    _FakeSession.run sets marker=ids[i,0] dimension 1.0 (unit vector retained after L2
    normalization, all others zero), so argmax(out[i]) equals
    marker. _run_with_lengths injects ids[0]=i+1 into sample i; correct
    output therefore yields argmax [1, 2, 3, 4, 5] modulo HIDDEN-1.
    Misalignment/missing outputs fail this assertion.
    """
    svc, sess, emb = service_with_fake_session
    lengths = [50, 5, 100, 8, 200]
    out = _run_with_lengths(svc, sess, emb, lengths)
    assert len(out) == 5
    assert all(v is not None for v in out)
    assert all(len(v) == _FakeSession.HIDDEN for v in out)
    # Input sample i (zero-based) gets first-token ID i+1; marker dimension =
    # ((i+1 - 1) % (HIDDEN-1)) + 1 = (i % 31) + 1。
    expected_markers = [(i % (_FakeSession.HIDDEN - 1)) + 1 for i in range(5)]
    actual_markers = [max(range(len(v)), key=v.__getitem__) for v in out]
    assert actual_markers == expected_markers, (
        f"输出顺序跟输入错位了: expected {expected_markers}, got {actual_markers}"
    )


def test_one_long_entry_does_not_pollute_short_batch(service_with_fake_session):
    """Core regression: one 8000-token entry + fifteen 100-token entries must not pad all short
    entries to 8000, the former memory explosion."""
    svc, sess, emb = service_with_fake_session
    lengths = [100] * 15 + [8000]
    out = _run_with_lengths(svc, sess, emb, lengths)
    assert len(out) == 16
    # At least one short-text bucket has seq_len < 8000, proving long-text isolation.
    max_seqs = [c[1] for c in sess.calls]
    assert min(max_seqs) <= 100, (
        f"短文本不应被长文本带飞 padding: {sess.calls}"
    )
    # Long text must occupy batch=1, or others would pad to 8000 again.
    long_calls = [c for c in sess.calls if c[1] >= 8000]
    assert long_calls and all(c[0] == 1 for c in long_calls)


def test_empty_input_returns_empty(service_with_fake_session):
    svc, sess, emb = service_with_fake_session
    out = _run_with_lengths(svc, sess, emb, [])
    assert out == []
    assert sess.calls == []


# ── Codex PR #1585 P2: max_length in model_id prevents cache reuse across truncation lengths ──

def test_build_model_id_includes_max_length_when_provided():
    """New calls encode max_length in ID suffix; lowering max_length makes old cache
    fail ID equality and ``is_cached_embedding_valid`` classify stale."""
    embeddings = pytest.importorskip("memory.embeddings")
    mid = embeddings.build_model_id("local-text-retrieval-v1", 256, "int8", 1024)
    assert mid == "local-text-retrieval-v1-256d-int8-mlen1024"


def test_build_model_id_omits_max_length_when_none():
    """Legacy calls omitting max_length retain old format, preserving existing fixtures."""
    embeddings = pytest.importorskip("memory.embeddings")
    mid = embeddings.build_model_id("local-text-retrieval-v1", 128, "fp32")
    assert mid == "local-text-retrieval-v1-128d-fp32"


def test_parse_dim_handles_both_id_formats():
    """parse_dim_from_model_id parses both old on-disk cache format and
    new writes; otherwise upgrades invalidate every old cache and trigger
    mass re-embedding, nonfatal but wasteful."""
    embeddings = pytest.importorskip("memory.embeddings")
    # Old format.
    assert embeddings.parse_dim_from_model_id("local-text-retrieval-v1-256d-int8") == 256
    # New format.
    assert embeddings.parse_dim_from_model_id("local-text-retrieval-v1-256d-int8-mlen1024") == 256
    assert embeddings.parse_dim_from_model_id("foo-bar-128d-fp32-mlen8192") == 128


def test_truncation_failure_aborts_load_with_distinct_reason():
    """`enable_truncation` failure makes _load_session_blocking raise
    _DisabledError(TRUNCATION_SETUP_FAILED), never become ready and stamp
    mismatched mlen cache IDs (joint Codex/CodeRabbit P2 on PR #1585).

    Patch Tokenizer.from_file to return fake tokenizer raising from enable_truncation,
    then invoke _load_session_blocking and assert correct DisabledError
    and reason. Patch session creation too, avoiding real-model
    file dependencies.
    """
    embeddings = pytest.importorskip("memory.embeddings")
    # Prepare fake service as above.
    svc = object.__new__(embeddings.EmbeddingService)
    svc._profile_id = "test-profile"
    svc._model_dir = "/nonexistent"  # Patched path checks succeed.
    svc._quantization = "int8"
    svc._dim = 256

    # Patch file checks + onnxruntime + tokenizers, making failed enable_truncation
    # the decisive failure only.
    import sys as _sys
    monkey = {}

    def fake_nonempty(_path):
        return True
    monkey["_is_nonempty_file"] = embeddings._is_nonempty_file
    embeddings._is_nonempty_file = fake_nonempty

    # Fake ort module. SessionOptions is lightweight class, not SimpleNamespace,
    # because _load_session_blocking calls add_session_config_entry to disable spin;
    # SimpleNamespace lacking it would raise AttributeError.
    class _FakeSessOpts:
        def __init__(self):
            self.intra_op_num_threads = 0
            self.graph_optimization_level = 0
            self.enable_cpu_mem_arena = True

        def add_session_config_entry(self, _key, _value):
            pass

    fake_ort = types.SimpleNamespace(
        SessionOptions=_FakeSessOpts,
        InferenceSession=lambda *a, **kw: object(),
        GraphOptimizationLevel=types.SimpleNamespace(ORT_ENABLE_ALL=0),
    )
    monkey["onnxruntime"] = _sys.modules.get("onnxruntime")
    _sys.modules["onnxruntime"] = fake_ort

    # Fake tokenizers: from_file returns object whose enable_truncation always raises.
    class _BadTokenizer:
        def enable_truncation(self, **_):
            raise RuntimeError("simulated truncation failure")
    fake_tk_mod = types.SimpleNamespace(
        Tokenizer=types.SimpleNamespace(from_file=lambda _: _BadTokenizer()),
    )
    monkey["tokenizers"] = _sys.modules.get("tokenizers")
    _sys.modules["tokenizers"] = fake_tk_mod

    try:
        with pytest.raises(embeddings._DisabledError) as exc_info:
            svc._load_session_blocking()
        assert exc_info.value.reason == embeddings._DisableReason.TRUNCATION_SETUP_FAILED
    finally:
        # Restore monkeypatch.
        embeddings._is_nonempty_file = monkey["_is_nonempty_file"]
        for k in ("onnxruntime", "tokenizers"):
            if monkey[k] is None:
                _sys.modules.pop(k, None)
            else:
                _sys.modules[k] = monkey[k]


def test_old_cache_id_invalidated_after_max_length_change():
    """End-to-end: lowering max_length 8192→1024 makes old mlen8192 cache rows
    stale via ``is_cached_embedding_valid``, triggering worker re-embedding.
    Core Codex P2 defense: otherwise 1024-token-prefix queries compare against old
    8192-token embeddings, distorting cosine similarity."""
    embeddings = pytest.importorskip("memory.embeddings")
    old_id = "local-text-retrieval-v1-256d-int8-mlen8192"
    new_id = "local-text-retrieval-v1-256d-int8-mlen1024"
    # Construct seemingly valid cache row with fake 256d base64 vector.
    import base64, struct
    fake_vec = base64.b64encode(struct.pack(f"<{256}e", *([0.1] * 256))).decode()
    entry = {
        "embedding": fake_vec,
        "embedding_text_sha256": embeddings._embedding_text_sha256("hello"),
        "embedding_model_id": old_id,
    }
    # Same text but upgraded runtime model_id → stale.
    assert not embeddings.is_cached_embedding_valid(entry, "hello", new_id)
    # Sanity: matching ID stays valid, not blanket cache invalidation.
    entry_match = dict(entry, embedding_model_id=new_id)
    assert embeddings.is_cached_embedding_valid(entry_match, "hello", new_id)
