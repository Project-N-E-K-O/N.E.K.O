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

# numpy 是 embedding service 推理必备,缺失就 skip——跟现有
# test_embeddings_fallback.py 的态度一致(测试只跑在能真测的环境)。
np = pytest.importorskip("numpy")


class _FakeEncoded:
    """模拟 tokenizers.Encoding 接口的最小对象。只用 ids / attention_mask。"""
    def __init__(self, n_tokens: int):
        self.ids = list(range(1, n_tokens + 1))
        self.attention_mask = [1] * n_tokens


class _FakeSession:
    """模拟 ort.InferenceSession.run:返回固定 hidden_dim 的 token embeddings。

    记录每次 run 的 (batch_size, seq_len),让测试断言桶分行为。

    关键设计:每行根据 ids[i, 0] 选一个**唯一的非零维度**打成 1.0,其余
    维度为 0。这样:

    - L2 归一化后向量保持「在该维度上是单位向量」(方向可区分,不会被
      normalize 塌成同一个方向)— CodeRabbit 在 PR #1585 指出原来用
      「单维度上的不同幅值」做区分,过 norm 后全变成 [1, 0, ..., 0],
      错位也能通过断言。
    - 测试可以用 ``argmax`` 直接读回 marker 维度,反推 sample → output
      的映射,验证桶装-还原没把 idx 搞错位。
    """
    HIDDEN = 32  # 测试用小 hidden,跟生产 256/768 无关

    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def get_inputs(self):
        # 只暴露 input_ids 一个入口,跳过 attention_mask / token_type_ids
        # 分支(那部分单独有 prod 路径覆盖,这里专注桶分)。
        inp = types.SimpleNamespace(name="input_ids")
        return [inp]

    def run(self, output_names, feeds):
        ids = feeds["input_ids"]
        batch, seq = ids.shape
        self.calls.append((batch, seq))
        out = np.zeros((batch, seq, self.HIDDEN), dtype=np.float32)
        for i in range(batch):
            # marker ∈ [1, HIDDEN-1],避开 0 让 argmax 唯一;ids 从 1 起、
            # mod (HIDDEN-1) + 1 保证落进合法区间。
            marker = ((int(ids[i, 0]) - 1) % (self.HIDDEN - 1)) + 1
            out[i, :, marker] = 1.0
        return [out]


@pytest.fixture
def service_with_fake_session(monkeypatch):
    """返回一个 EmbeddingService 实例,session/tokenizer 已被 monkeypatch。

    用 ``object.__new__`` 跳过 ``__init__``,避免被 config_manager / 文件
    路径 / RAM 检测等副作用拖累——我们只想测 _infer_blocking 的桶分。
    """
    # ``object.__new__`` 绕过 __init__,fixture 既不走 _build_default_service
    # 也不走 _load_session_blocking — tokenizers / onnxruntime 都不需要。
    # 顶层 ``import memory.embeddings`` 本身是纯 Python 模块加载(没有
    # 副作用 import 重依赖),importorskip 一下兜底罕见的打包剥离场景。
    embeddings = pytest.importorskip("memory.embeddings")
    svc = object.__new__(embeddings.EmbeddingService)
    fake_sess = _FakeSession()
    svc._session = fake_sess
    svc._tokenizer = object()  # 占位,只要不是 None 就行(不会被调到)
    svc._dim = None  # 不做 Matryoshka 截断
    svc._load_lock = asyncio.Lock()
    svc._lifecycle_condition = asyncio.Condition()
    svc._active_operations = 0
    svc._closing = False
    return svc, fake_sess, embeddings


def _run_with_lengths(svc, fake_sess, embeddings_mod, lengths):
    """直接喂预 tokenized 的 encoded 列表给 _run_bucket / _infer_blocking
    走桶分。绕过 tokenizer.encode_batch,直接 monkeypatch tokenizer。

    给每个 encoded 注入唯一首 token id(1, 2, 3, ...),跟 _FakeSession
    的 marker 维度方案配合 — argmax(output[i]) 直接还原"第 i 个槽对应
    哪个原始样本",顺序回归用例才有真断言力。
    """
    encoded = [_FakeEncoded(n) for n in lengths]
    for i, enc in enumerate(encoded, start=1):
        if enc.ids:
            enc.ids[0] = i  # 注入稳定 marker;后续 token 仍是 dummy
    svc._tokenizer = types.SimpleNamespace(encode_batch=lambda texts: encoded)
    # texts 列表只起占位作用,长度跟 encoded 对齐就行
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
    close_task = asyncio.create_task(svc.close())
    await asyncio.sleep(0)

    assert svc._closing is True
    assert not close_task.done()
    release_inference.set()

    assert await embed_task == [1.0]
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
    close_task = asyncio.create_task(svc.close())
    await asyncio.sleep(0)

    assert svc._closing is True
    release_load.set()

    assert await load_task is False
    assert await close_task is None
    assert svc._session is None
    assert svc._tokenizer is None
    assert svc._state is embeddings_mod.EmbeddingState.CLOSED


def test_short_batch_runs_in_single_bucket(service_with_fake_session):
    """全部短文本(总和远小于 budget)→ 一个桶搞定,行为等同旧 fast path。"""
    svc, sess, emb = service_with_fake_session
    out = _run_with_lengths(svc, sess, emb, [10, 12, 15, 8])
    assert len(out) == 4
    assert len(sess.calls) == 1
    batch, seq = sess.calls[0]
    assert batch == 4
    assert seq == 15  # pad 到桶内最长


def test_long_entries_split_into_multiple_buckets(service_with_fake_session):
    """16 条全顶 1024 token → 16×1024=16384 正好等于 budget 16384,装得下;
    再多一条就拆桶。验证桶分按 ``batch × max_len ≤ budget`` 触发。"""
    svc, sess, emb = service_with_fake_session
    lengths = [1024] * 17
    out = _run_with_lengths(svc, sess, emb, lengths)
    assert len(out) == 17
    # 第一桶 16 条 × 1024 = 16384 = budget,刚好装下;第 17 条独占第二桶
    assert len(sess.calls) == 2
    batches = sorted(c[0] for c in sess.calls)
    assert batches == [1, 16]


def test_single_overlong_entry_still_runs(service_with_fake_session):
    """单条 > budget 时,空桶必接受(否则永远 flush 不出去)。"""
    svc, sess, emb = service_with_fake_session
    out = _run_with_lengths(svc, sess, emb, [emb._INFER_BATCH_MAX_TOKENS + 1000])
    assert len(out) == 1
    assert len(sess.calls) == 1
    batch, seq = sess.calls[0]
    assert batch == 1
    assert seq == emb._INFER_BATCH_MAX_TOKENS + 1000


def test_mixed_length_preserves_original_order(service_with_fake_session):
    """桶内按长度排序,但 _infer_blocking 必须按 original idx 还原输出顺序,
    否则 zip(texts, vectors) 错位会让缓存键全错。

    断言机制:_FakeSession.run 给每行在 marker=ids[i,0] 维度上打 1.0(L2
    归一化后该维度仍是单位向量、其余维度 0),所以 argmax(out[i]) ==
    marker 维度,而 _run_with_lengths 给样本 i 注入 ids[0]=i+1 — 因此
    output 顺序正确时,argmax 序列必须是 [1, 2, 3, 4, 5](mod HIDDEN-1
    后)。错位 / 漏填都会被这个断言抓到。
    """
    svc, sess, emb = service_with_fake_session
    lengths = [50, 5, 100, 8, 200]
    out = _run_with_lengths(svc, sess, emb, lengths)
    assert len(out) == 5
    assert all(v is not None for v in out)
    assert all(len(v) == _FakeSession.HIDDEN for v in out)
    # 输入样本 i(0-indexed)注入的 first-token-id 是 i+1,marker 维度 =
    # ((i+1 - 1) % (HIDDEN-1)) + 1 = (i % 31) + 1。
    expected_markers = [(i % (_FakeSession.HIDDEN - 1)) + 1 for i in range(5)]
    actual_markers = [max(range(len(v)), key=v.__getitem__) for v in out]
    assert actual_markers == expected_markers, (
        f"输出顺序跟输入错位了: expected {expected_markers}, got {actual_markers}"
    )


def test_one_long_entry_does_not_pollute_short_batch(service_with_fake_session):
    """关键回归用例:一条 8000 token + 15 条 100 token 不应该让 15 条短的
    一起 pad 到 8000(那正是修复前的内存炸点)。"""
    svc, sess, emb = service_with_fake_session
    lengths = [100] * 15 + [8000]
    out = _run_with_lengths(svc, sess, emb, lengths)
    assert len(out) == 16
    # 至少一个桶的 seq_len < 8000(短文本桶),验证长文本被隔离
    max_seqs = [c[1] for c in sess.calls]
    assert min(max_seqs) <= 100, (
        f"短文本不应被长文本带飞 padding: {sess.calls}"
    )
    # 长文本必须独占一桶 batch=1,否则同桶其他条目又被拖到 8000
    long_calls = [c for c in sess.calls if c[1] >= 8000]
    assert long_calls and all(c[0] == 1 for c in long_calls)


def test_empty_input_returns_empty(service_with_fake_session):
    svc, sess, emb = service_with_fake_session
    out = _run_with_lengths(svc, sess, emb, [])
    assert out == []
    assert sess.calls == []


# ── Codex PR #1585 P2:max_length 进 model_id 防止跨截断长度复用 cache ──

def test_build_model_id_includes_max_length_when_provided():
    """新调用必须把 max_length 编进 id 末尾,这样降 max_length 时旧 cache
    会因 id 字符串不等被 ``is_cached_embedding_valid`` 判为 stale。"""
    embeddings = pytest.importorskip("memory.embeddings")
    mid = embeddings.build_model_id("local-text-retrieval-v1", 256, "int8", 1024)
    assert mid == "local-text-retrieval-v1-256d-int8-mlen1024"


def test_build_model_id_omits_max_length_when_none():
    """legacy 调用(没传 max_length)保留旧格式 —— 不破坏老测试 fixture。"""
    embeddings = pytest.importorskip("memory.embeddings")
    mid = embeddings.build_model_id("local-text-retrieval-v1", 128, "fp32")
    assert mid == "local-text-retrieval-v1-128d-fp32"


def test_parse_dim_handles_both_id_formats():
    """parse_dim_from_model_id 必须同时解析老格式(磁盘上已有 cache)和
    新格式(新写入)。否则升级时老 cache 全部解析失败 → 全部判 stale →
    一次大规模重 embed,虽不致命但浪费 CPU。"""
    embeddings = pytest.importorskip("memory.embeddings")
    # 老格式
    assert embeddings.parse_dim_from_model_id("local-text-retrieval-v1-256d-int8") == 256
    # 新格式
    assert embeddings.parse_dim_from_model_id("local-text-retrieval-v1-256d-int8-mlen1024") == 256
    assert embeddings.parse_dim_from_model_id("foo-bar-128d-fp32-mlen8192") == 128


def test_truncation_failure_aborts_load_with_distinct_reason():
    """`enable_truncation` 失败时 _load_session_blocking 必须 raise
    _DisabledError(TRUNCATION_SETUP_FAILED),而不是 ready 后继续 stamp
    错配的 mlen cache id(Codex + CodeRabbit 在 PR #1585 联合指出的 P2)。

    跑法:monkeypatch Tokenizer.from_file 返回一个 enable_truncation 抛错
    的假 tokenizer,然后调 _load_session_blocking,断言它 raises 正确的
    DisabledError + reason。session 创建那段也 monkeypatch 掉避免依赖
    真模型文件。
    """
    embeddings = pytest.importorskip("memory.embeddings")
    # 准备假 service 实例(同前)
    svc = object.__new__(embeddings.EmbeddingService)
    svc._profile_id = "test-profile"
    svc._model_dir = "/nonexistent"  # 路径检查会过(monkeypatch 掉)
    svc._quantization = "int8"
    svc._dim = 256

    # monkeypatch 文件检查 + onnxruntime + tokenizers,只让 enable_truncation
    # 失败那一步成为决定性 failure。
    import sys as _sys
    monkey = {}

    def fake_nonempty(_path):
        return True
    monkey["_is_nonempty_file"] = embeddings._is_nonempty_file
    embeddings._is_nonempty_file = fake_nonempty

    # 伪 ort 模块。SessionOptions 用一个轻量类而不是 SimpleNamespace,因为
    # _load_session_blocking 会调 add_session_config_entry(关 spin),
    # SimpleNamespace 没有这个方法会 AttributeError。
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

    # 伪 tokenizers:from_file 返回一个 enable_truncation 必抛的对象
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
        # 还原 monkeypatch
        embeddings._is_nonempty_file = monkey["_is_nonempty_file"]
        for k in ("onnxruntime", "tokenizers"):
            if monkey[k] is None:
                _sys.modules.pop(k, None)
            else:
                _sys.modules[k] = monkey[k]


def test_old_cache_id_invalidated_after_max_length_change():
    """端到端:max_length 从 8192 降到 1024 时,老 cache row(id 含 mlen8192)
    应被 ``is_cached_embedding_valid`` 判为 stale,触发 worker 重新 embed。
    这是 Codex P2 的核心防御点 —— 否则新 query(1024 截前缀)会跟老
    embedding(8192 截全量)做 cosine,得到偏移的相似度。"""
    embeddings = pytest.importorskip("memory.embeddings")
    old_id = "local-text-retrieval-v1-256d-int8-mlen8192"
    new_id = "local-text-retrieval-v1-256d-int8-mlen1024"
    # 构造一个看似有效的 cache row(填上 256-d 的假 base64 vector)
    import base64, struct
    fake_vec = base64.b64encode(struct.pack(f"<{256}e", *([0.1] * 256))).decode()
    entry = {
        "embedding": fake_vec,
        "embedding_text_sha256": embeddings._embedding_text_sha256("hello"),
        "embedding_model_id": old_id,
    }
    # 同样的 text,但运行时 model_id 已经升级 → 应该 stale
    assert not embeddings.is_cached_embedding_valid(entry, "hello", new_id)
    # sanity:同 id 仍然有效(不是把所有 cache 都误杀)
    entry_match = dict(entry, embedding_model_id=new_id)
    assert embeddings.is_cached_embedding_valid(entry_match, "hello", new_id)
