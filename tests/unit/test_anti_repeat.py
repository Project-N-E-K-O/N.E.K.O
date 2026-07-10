# -*- coding: utf-8 -*-
"""Unit tests for memory.anti_repeat — proactive 防复读的 BM25 corpus +
scorer + soft-hint prompt 注入。

五类合同：

1. ``AntiRepeatCorpus.record_output`` 把 AI 输出 ngramize 后入库；过短文本
   被丢弃（不污染 DF）；超 ``ANTI_REPEAT_BG_WINDOW`` 自动滚出最老。
2. ``bm25_score`` 对高 IDF（unique）的 topic 词给高分，对高 DF 的公共词几乎
   不给分（避免误伤"今天/觉得"这种连接词）。
3. ``score_draft`` 端到端：空 corpus / 过短 draft → 0；连续重复同一 topic
   → 分数线性升高。
4. ``top_recent_topics`` 返回最近 5 条里 rank 最高的 K 个 ngram，提示模型
   "已经聊过这些"。
5. 持久化 round-trip + ``clear``。
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from config import ANTI_REPEAT_BG_WINDOW, ANTI_REPEAT_FG_TTL_SECONDS
from config.prompts.prompts_directives import (
    RECENT_TOPIC_HINT_PROMPT_BLOCK,
    PROACTIVE_REGEN_AVOID_INSTRUCTION,
    render_recent_topics_block,
    render_regen_avoid_instruction,
)
from memory.anti_repeat import (
    AntiRepeatCorpus,
    _ngrams,
    bm25_score,
)


# ── helpers ──────────────────────────────────────────────────


def _build_store(tmp_path) -> AntiRepeatCorpus:
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    s = AntiRepeatCorpus()
    s._config_manager = cm
    return s


# 一段长到能过 ANTI_REPEAT_MIN_DRAFT_TOKENS 的中文，主要用于 record_output 的"正常文本"测试。
LONG_TIGER = (
    "今天我又想到那只老虎了，那只在森林深处缓慢踱步的橙黄色生物，"
    "眼里的猎手与孤独都让我难忘，老虎、老虎、老虎、森林。"
)
LONG_FRUIT = (
    "我决定下午去买一些水果，葡萄、芒果、桃子和荔枝，"
    "再带一点奶油挞，最好顺路把书店那本《人类群星闪耀时》也买回家慢慢看。"
)


# ── 1. record_output basic + 滚动 ───────────────────────────


def test_record_output_records_normal_text(tmp_path):
    s = _build_store(tmp_path)
    name = "Neko"
    s.record_output(name, LONG_TIGER, is_proactive=True, now=1000.0)
    # 走一次 score_draft 验证已入库（同一 now，落在 FG TTL 内）
    total, _ = s.score_draft(name, LONG_TIGER, now=1000.0)
    assert total > 0


def test_record_output_skips_short_text(tmp_path):
    """过短的 ai 输出（< MIN_DRAFT_TOKENS ngram）不入库——免得"嗯/好"抹高 DF。"""
    s = _build_store(tmp_path)
    name = "Neko"
    s.record_output(name, "嗯。", is_proactive=False, now=1000.0)
    s.record_output(name, "好的", is_proactive=False, now=1001.0)
    # corpus 仍然为空（落盘前后都一致）
    with s._get_lock(name):
        assert s._load_unlocked(name) == []


def test_record_output_rolling_window(tmp_path):
    s = _build_store(tmp_path)
    name = "Neko"
    for i in range(ANTI_REPEAT_BG_WINDOW + 30):
        # 每次都生成稍微不同的长文本，避免 ngram 完全一致被去重
        text = f"第{i}天的随想，今天发生了一些有趣的事情，让我想了很久，编号{i}"
        # 拼长到过 MIN_DRAFT_TOKENS
        text = text * 3
        s.record_output(name, text, is_proactive=False, now=float(i))
    with s._get_lock(name):
        window = s._load_unlocked(name)
    assert len(window) == ANTI_REPEAT_BG_WINDOW
    # 弹掉了最老的——最早保留的 ts 应该是 30（前 30 条被弹出）
    timestamps = [e["ts"] for e in window]
    assert min(timestamps) >= 30.0


# ── 2. bm25_score ────────────────────────────────────────────


def test_bm25_empty_inputs_return_zero():
    assert bm25_score([], [["a", "b"]]) == (0.0, {})
    assert bm25_score(["a"], []) == (0.0, {})


def test_bm25_high_idf_topic_word_dominates():
    """rare topic word（DF=1/3）的分应严格高于 common word（DF=3/3）。

    BG = FG = 3 docs（同步），所以 IDF 由文档间分布决定；ratio 在 3 doc 量级
    下被 TF saturation 拉低（~2.4x），只校验 strictly greater。
    """
    docs = [
        ["今天", "天气", "好", "老虎"],          # 老虎 unique
        ["今天", "想", "吃", "苹果"],            # 苹果 unique
        ["今天", "学", "了", "数学"],            # 数学 unique
    ]
    draft = ["今天", "老虎"]
    total, per_term = bm25_score(draft, docs)
    assert total > 0
    assert "老虎" in per_term
    if "今天" in per_term:
        assert per_term["老虎"] > per_term["今天"]


def test_bm25_with_large_bg_separates_rare_term():
    """BG 大窗里 unique 的 term，IDF 显著拉开。"""
    # 20 条 BG 都不含 "老虎"；5 条 FG 全含 "老虎"
    bg_filler = [["今天", "天气", "好", f"话题{i}"] for i in range(15)]
    fg_tiger = [["今天", "看见", "老虎", "森林"] for _ in range(5)]
    bg_docs = bg_filler + fg_tiger
    fg_docs = fg_tiger
    draft = ["今天", "老虎"]
    _total, per_term = bm25_score(draft, fg_docs, bg_docs)
    assert "老虎" in per_term
    # 老虎 在 BG 里 DF=5/20，IDF 较高；今天 DF=20/20，IDF=0 → 不打分
    assert "今天" not in per_term or per_term["老虎"] > per_term["今天"] * 5


def test_bm25_unseen_term_zero():
    docs = [["a", "b", "c"]]
    total, per_term = bm25_score(["z"], docs)
    assert total == 0.0
    assert per_term == {}


# ── 3. score_draft end-to-end ─────────────────────────────────


def test_score_draft_empty_corpus_zero(tmp_path):
    s = _build_store(tmp_path)
    total, _ = s.score_draft("Neko", LONG_TIGER)
    assert total == 0.0


def test_score_draft_short_draft_zero(tmp_path):
    s = _build_store(tmp_path)
    s.record_output("Neko", LONG_TIGER, now=1.0)
    total, _ = s.score_draft("Neko", "嗯。")
    assert total == 0.0


def test_score_draft_repeating_topic_scores_high(tmp_path):
    """连续 5 条都聊老虎，新 draft 也聊老虎 → 高 BM25。"""
    s = _build_store(tmp_path)
    name = "Neko"
    for i in range(5):
        s.record_output(name, LONG_TIGER + f"（第{i}次）", now=float(i))
    # 同 topic 的新 draft（now=5 落在 FG TTL 内，5 条都算前景）
    total_same, terms_same = s.score_draft(name, LONG_TIGER + "（新一次）", now=5.0)
    # 完全换 topic 的 draft
    total_diff, _ = s.score_draft(name, LONG_FRUIT, now=5.0)
    assert total_same > total_diff
    assert "老虎" in terms_same or any("虎" in t for t in terms_same)


def test_score_draft_topic_words_ranked_first(tmp_path):
    """BG 大窗里大多与话题无关 + FG 几条全聊 topic → top K per_term 全部来自
    FG (tiger-text)，没有来自 BG filler 的 ngram。

    断言用"top K 全部来自 FG"而不是"top K 含'虎'"——因为 LONG_TIGER 里很多
    高频 2/3-gram 同 DF（5/25）、同 IDF，"虎" 系列与"森林"/"那只"/"难忘"
    并列；ranking 中的 tie-break 由 dict insertion order 决定，这又被 Python
    hash-randomization 跨进程打乱，不能稳定断言"具体含'虎'"。"""
    s = _build_store(tmp_path)
    name = "Neko"
    # 20 条 BG filler：完全不含老虎/森林相关
    bg_marker = "今天去了号地方看到新奇的东西编号事物让印象深刻感觉时间过得很快"
    for i in range(20):
        s.record_output(name, bg_marker + f"片段{i}{i}{i}", now=float(i))
    # 5 条 FG：全部聊老虎话题
    for i in range(20, 25):
        s.record_output(name, LONG_TIGER + f"（第{i}次想到）", now=float(i))
    _total, terms = s.score_draft(
        name, "我又想起了老虎，那只森林里的老虎依然让我难忘", now=25.0
    )
    assert terms
    # top 5 ngram 全部应来自 LONG_TIGER；不该有来自 BG filler 的字符组合
    top5 = list(terms.keys())[:5]
    for t in top5:
        assert t in LONG_TIGER, (
            f"top-ranked ngram {t!r} not from FG text, leaked from BG? top5={top5}"
        )


# ── 3b. FG TTL / 空闲死锁修复 ─────────────────────────────────
#
# 生产事故：主动搭话在用户空闲时才触发，而所有 drop 路径都不写 corpus、只有
# 成功投递 / 真实用户回复才写。空闲期 FG 窗被最近几条同话题（如屏幕解说）冻结，
# 每轮打出同样的超高 BM25 → regen 后仍超 DROP → 永远 drop → 永远无法搭话
# （日志 "proactive BM25 regen still over drop"）。修法：FG 只计入 TTL 内的
# 条目（TF/复读是近因信号），BG 不设 TTL（保住 IDF 语境）。


def test_split_fg_bg_bg_full_fg_ttl_filtered():
    """_split_fg_bg: BG = whole window, unfiltered (full IDF context preserved);
    FG = only the trailing entries that are within the TTL."""
    now = 1_000_000.0
    window = [
        {"ts": now - 5000.0, "ngrams": ["old1", "x"], "is_proactive": False},   # 远超 TTL
        {"ts": now - 4000.0, "ngrams": ["old2", "x"], "is_proactive": False},   # 远超 TTL
        {"ts": now - 100.0, "ngrams": ["fresh1", "x"], "is_proactive": True},   # TTL 内
        {"ts": now - 10.0, "ngrams": ["fresh2", "x"], "is_proactive": True},    # TTL 内
    ]
    fg_docs, bg_docs = AntiRepeatCorpus._split_fg_bg(window, fg_window=5, now=now)
    assert len(bg_docs) == 4                                   # BG 不按时间裁
    assert fg_docs == [["fresh1", "x"], ["fresh2", "x"]]       # FG 只留 TTL 内


def test_split_fg_bg_all_stale_yields_empty_fg():
    """All entries stale -> FG empty (bm25_score hits `not fg_docs` -> 0); BG still present."""
    now = 1_000_000.0
    window = [
        {"ts": now - 5000.0, "ngrams": ["old1"], "is_proactive": False},
        {"ts": now - 4000.0, "ngrams": ["old2"], "is_proactive": False},
    ]
    fg_docs, bg_docs = AntiRepeatCorpus._split_fg_bg(window, fg_window=5, now=now)
    assert bg_docs == [["old1"], ["old2"]]
    assert fg_docs == []


def test_score_draft_idle_frozen_window_ages_out(tmp_path):
    """Five same-topic entries freeze the FG: scores high within the TTL (would
    trigger regen/drop); after idling past the TTL the score drops to 0 and the
    draft passes -- the exact release point of the idle deadlock."""
    s = _build_store(tmp_path)
    name = "Neko"
    base = 1_000_000.0
    for i in range(5):
        s.record_output(name, LONG_TIGER + f"（第{i}次）", now=base + i)
    draft = LONG_TIGER + "（又想到了）"
    total_fresh, _ = s.score_draft(name, draft, now=base + 5)
    assert total_fresh > 0
    total_stale, terms_stale = s.score_draft(
        name, draft, now=base + ANTI_REPEAT_FG_TTL_SECONDS + 60
    )
    assert total_stale == 0.0
    assert terms_stale == {}


def test_score_draft_bg_idf_survives_after_fg_ttl(tmp_path):
    """FG aging past the TTL only affects TF, not BG: one within-TTL entry plus a
    batch of long-expired ones -- a rare topic word still scores via the full-BG
    IDF (if BG were TTL-trimmed too, that context would be lost)."""
    s = _build_store(tmp_path)
    name = "Neko"
    base = 1_000_000.0
    bg_marker = "今天去了号地方看到新奇的东西编号事物让印象深刻感觉时间过得很快"
    for i in range(20):  # 20 条早已过期、且不含老虎 → 只贡献 IDF 背景
        s.record_output(name, bg_marker + f"片段{i}{i}{i}", now=base - 100_000.0 + i)
    s.record_output(name, LONG_TIGER, now=base)  # 唯一 TTL 内条目：聊老虎
    draft = LONG_TIGER + "（再想）"
    total, terms = s.score_draft(name, draft, now=base + 1)
    assert total > 0
    assert "老虎" in terms or any("虎" in t for t in terms)
    # 判别性断言（Greptile P2）：total>0 还不够——BG 只剩 1 条时老虎 IDF≈0.29 仍为正。
    # 真实(全量 21 条 BG)得分必须严格高于「BG 也被 TTL 裁成只剩那条新鲜文档」的假想
    # 得分（后者把稀有 topic 词的 DF 抬到 1/1、IDF 从 ~2.69 塌到 ~0.29）。这才真正
    # 锁住"BG 不按 TTL 裁、IDF 语境完整保留"，防止未来误改成 BG 也 TTL 过滤。
    draft_ngrams = _ngrams(draft)
    fg_only = [_ngrams(LONG_TIGER)]  # 若 BG 也被 TTL 裁，就只剩这一条新鲜文档
    total_if_bg_trimmed, _ = bm25_score(draft_ngrams, fg_only, fg_only)
    assert total > total_if_bg_trimmed


# ── 4. top_recent_topics ──────────────────────────────────────


def test_top_recent_topics_returns_topic_words(tmp_path):
    """Large BG window + a few FG entries focused on one topic → every ngram
    top_recent_topics returns comes from the FG (not the BG filler). Same assertion
    strategy as ``test_score_draft_topic_words_ranked_first`` — avoids the
    hash-randomization tie-break flakiness."""
    s = _build_store(tmp_path)
    name = "Neko"
    bg_marker = "今天去了号地方看到新奇的东西编号事物让印象深刻时间过得很快"
    for i in range(20):
        s.record_output(name, bg_marker + f"片段{i}{i}{i}", now=float(i))
    for i in range(20, 25):
        s.record_output(name, LONG_TIGER + f"（第{i}次）", now=float(i))
    topics = s.top_recent_topics(name, k=6, now=25.0)
    assert topics
    for t in topics:
        assert t in LONG_TIGER, (
            f"top topic {t!r} leaked from BG filler; topics={topics}"
        )


def test_top_recent_topics_stale_fg_returns_empty(tmp_path):
    """FG fully past the TTL -> no "recently discussed" topics to hint -> returns
    empty (dual to score_draft)."""
    s = _build_store(tmp_path)
    name = "Neko"
    base = 1_000_000.0
    for i in range(5):
        s.record_output(name, LONG_TIGER + f"（第{i}次）", now=base + i)
    assert s.top_recent_topics(name, now=base + 5)  # 新鲜时有 topic
    assert s.top_recent_topics(
        name, now=base + ANTI_REPEAT_FG_TTL_SECONDS + 60
    ) == []


def test_top_recent_topics_empty_corpus(tmp_path):
    s = _build_store(tmp_path)
    assert s.top_recent_topics("Neko") == []


def test_top_recent_topics_k_zero(tmp_path):
    s = _build_store(tmp_path)
    s.record_output("Neko", LONG_TIGER, now=1.0)
    assert s.top_recent_topics("Neko", k=0) == []


# ── 5. 持久化 round-trip + clear ──────────────────────────────


def test_round_trip_from_disk(tmp_path):
    name = "Neko"
    s1 = _build_store(tmp_path)
    s1.record_output(name, LONG_TIGER, now=1.0)
    s2 = _build_store(tmp_path)
    total, _ = s2.score_draft(name, LONG_TIGER, now=1.0)
    assert total > 0


def test_corrupt_file_starts_empty(tmp_path):
    name = "Neko"
    char_dir = os.path.join(str(tmp_path), name)
    os.makedirs(char_dir, exist_ok=True)
    with open(os.path.join(char_dir, "anti_repeat_corpus.json"), "w") as f:
        f.write("{not json")
    s = _build_store(tmp_path)
    assert s.score_draft(name, LONG_TIGER) == (0.0, {})


def test_clear_removes_all(tmp_path):
    s = _build_store(tmp_path)
    name = "Neko"
    s.record_output(name, LONG_TIGER, now=1.0)
    s.clear(name)
    assert s.score_draft(name, LONG_TIGER) == (0.0, {})


# ── 6. ngram extraction ───────────────────────────────────────


def test_ngrams_basic_cjk():
    ng = _ngrams("今天天气好")
    # 至少抓到一些 2-gram，包含 "天气" 或 "今天"
    assert any("天" in n for n in ng)


def test_ngrams_empty_returns_empty():
    assert _ngrams("") == []
    # None 走 ``text or ""`` 兜底，等价于空串；显式传 None 验证容错。
    assert _ngrams(None) == []  # type: ignore[arg-type]


# ── 7. prompt 渲染 (recent topics + regen avoid) ─────────────


@pytest.mark.parametrize("lang", list(RECENT_TOPIC_HINT_PROMPT_BLOCK.keys()))
def test_render_recent_topics_block_per_lang(lang):
    out = render_recent_topics_block(["老虎", "葡萄", "数学"], lang)
    assert "老虎" in out
    assert "葡萄" in out
    assert out.startswith("\n")


def test_render_recent_topics_empty():
    assert render_recent_topics_block([], "zh") == ""


@pytest.mark.parametrize("lang", list(PROACTIVE_REGEN_AVOID_INSTRUCTION.keys()))
def test_render_regen_avoid_instruction_per_lang(lang):
    out = render_regen_avoid_instruction(["老虎", "葡萄"], lang)
    assert "老虎" in out and "葡萄" in out


def test_render_regen_avoid_empty():
    assert render_regen_avoid_instruction([], "zh") == ""


def test_recent_topics_block_falls_back_to_en():
    """未支持的 lang 走 en 回退；返回不空字符串即可。"""
    out = render_recent_topics_block(["foo"], "und")
    assert "foo" in out
