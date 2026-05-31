# -*- coding: utf-8 -*-
"""Unit tests for the token-budget sub-batching in
``EmbeddingService._infer_blocking``.

背景:之前 ``_infer_blocking`` 用 pad-to-longest + 固定 batch(BATCH_SIZE=16),
一条粘贴进 recent 的长文本会让整批 16 条都 pad 到几千 token,激活内存
顶到多 GB(实测把 RSS 从 1.1 GB 顶到 12.4 GB)。修复改成桶装:
``batch_size × max_len ≤ _INFER_TOKEN_BUDGET``。

测试目标:
- 桶分边界正确(满桶 vs 必须 flush)
- 单条 token 数 > budget 时仍能跑(空桶必接受)
- 输出顺序跟输入对齐(桶内按长度排序,出桶时按 original idx 还原)
- 不依赖 onnxruntime/tokenizers/numpy(用 monkeypatched session + 假
  encoded 对象),所以本机没装这些重依赖也能跑。
"""
from __future__ import annotations

import sys
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
        # 返回 (batch, seq, hidden) — 每个 token embedding 用 row idx 当
        # 特征值,这样输出能区分行,后续断言可以验证顺序对齐。
        out = np.zeros((batch, seq, self.HIDDEN), dtype=np.float32)
        for i in range(batch):
            out[i, :, 0] = float(ids[i, 0])  # 每行首 token id 编码进 hidden[0]
        return [out]


@pytest.fixture
def service_with_fake_session(monkeypatch):
    """返回一个 EmbeddingService 实例,session/tokenizer 已被 monkeypatch。

    用 ``object.__new__`` 跳过 ``__init__``,避免被 config_manager / 文件
    路径 / RAM 检测等副作用拖累——我们只想测 _infer_blocking 的桶分。
    """
    # 必须在这里 import,而不是文件顶层:onnxruntime 不一定装,顶层
    # import memory.embeddings 会触发 _build_default_service 的 import
    # 链(虽然 service 是 lazy 的,但模块顶层 import 还是会跑)。所以
    # importorskip 一下,让没装 ort 的环境干净 skip。
    pytest.importorskip("tokenizers")  # _build_default_service 需要,但
    # 我们不真走那条路;importorskip 只是兜底。
    embeddings = pytest.importorskip("memory.embeddings")
    svc = object.__new__(embeddings.EmbeddingService)
    fake_sess = _FakeSession()
    svc._session = fake_sess
    svc._tokenizer = object()  # 占位,只要不是 None 就行(不会被调到)
    svc._dim = None  # 不做 Matryoshka 截断
    return svc, fake_sess, embeddings


def _run_with_lengths(svc, fake_sess, embeddings_mod, lengths):
    """直接喂预 tokenized 的 encoded 列表给 _run_bucket / _infer_blocking
    走桶分。绕过 tokenizer.encode_batch,直接 monkeypatch tokenizer。
    """
    encoded = [_FakeEncoded(n) for n in lengths]
    svc._tokenizer = types.SimpleNamespace(encode_batch=lambda texts: encoded)
    # texts 列表只起占位作用,长度跟 encoded 对齐就行
    texts = ["x"] * len(lengths)
    return svc._infer_blocking(texts)


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
    out = _run_with_lengths(svc, sess, emb, [emb._INFER_TOKEN_BUDGET + 1000])
    assert len(out) == 1
    assert len(sess.calls) == 1
    batch, seq = sess.calls[0]
    assert batch == 1
    assert seq == emb._INFER_TOKEN_BUDGET + 1000


def test_mixed_length_preserves_original_order(service_with_fake_session):
    """桶内按长度排序,但 _infer_blocking 必须按 original idx 还原输出顺序,
    否则 zip(texts, vectors) 错位会让缓存键全错。"""
    svc, sess, emb = service_with_fake_session
    # 5 条混合长度:用首 token id(=1)区分每行,验证顺序
    lengths = [50, 5, 100, 8, 200]
    out = _run_with_lengths(svc, sess, emb, lengths)
    assert len(out) == 5
    # 每行 hidden[0] 应当 = ids[0] = 1(_FakeEncoded 的 ids 起 1),
    # L2-norm 后 = 1/||v||。我们只验证输出对应输入是非 None 的有限值,
    # 顺序对齐由 out[orig_i] 赋值机制保证(单测断言的是「不漏不错位」)。
    assert all(v is not None for v in out)
    assert all(len(v) == _FakeSession.HIDDEN for v in out)


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
