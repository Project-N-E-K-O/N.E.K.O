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
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import asyncio
import json
import os
import threading
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
    s.record_output(name, "嗯。", is_proactive=True, now=1002.0)
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


# ── 3c. 用户无互动 + 长窗口重复内容 ─────────────────────────────


def test_unanswered_proactive_repeat_detects_non_consecutive_exact_repeat(tmp_path):
    """A third exact draft triggers even when unrelated outputs separate it."""
    s = _build_store(tmp_path)
    name = "Neko"
    base = 1_000_000.0
    draft = "屏幕上这个新的小猫按钮好好看啊，快点点一下看看吧。"
    fillers = [
        "窗外好像开始下雨了，玻璃上的水珠慢慢连成了几条细线。",
        "刚才那段音乐的节奏变轻了，像是从很远的地方飘过来。",
        "桌面右边多了一个新文件，名字看起来像是今天刚保存的。",
        "现在的光线有点暖，整个房间看起来比刚才安静了不少。",
        "任务栏上的时间已经不早了，今天好像一下子就过去了。",
        "这个页面的配色换成了深色，和前一个窗口的感觉完全不同。",
    ]
    timeline = [
        draft,
        *fillers[:3],
        draft,
        *fillers[3:],
    ]
    for index, text in enumerate(timeline):
        s.record_output(
            name,
            text,
            is_proactive=True,
            now=base + index * 900.0,
        )

    now = base + len(timeline) * 900.0
    signal = s.score_unanswered_proactive_draft(
        name,
        draft,
        silence_since=base - 1.0,
        now=now,
    )

    assert signal.triggered is True
    assert signal.match_count == 2
    assert signal.considered_count == len(timeline)
    assert signal.best_similarity >= 0.85
    assert signal.repeated_terms
    # 最早两次模板内容已经远超短 BM25 的 10 分钟 TTL；本测试锁住的是长窗口。
    bm25_total, _ = s.score_draft(name, draft, now=now)
    assert bm25_total == 0.0


def test_unanswered_proactive_repeat_needs_multiple_matches(tmp_path):
    """One similar prior draft is weak evidence and must not trigger intervention."""
    s = _build_store(tmp_path)
    name = "Neko"
    base = 2_000_000.0
    text = "屏幕上这个新的小猫按钮好好看啊，快点点一下看看吧。"
    s.record_output(name, text, is_proactive=True, now=base)

    signal = s.score_unanswered_proactive_draft(
        name,
        text,
        silence_since=base - 1.0,
        now=base + 60.0,
    )

    assert signal.triggered is False
    assert signal.match_count == 1
    assert signal.considered_count == 1


def test_unanswered_proactive_repeat_scores_short_latin_reminders(tmp_path):
    """Concise proactive reminders bypass only the stricter generic BM25 floor."""
    s = _build_store(tmp_path)
    name = "Neko"
    base = 2_500_000.0
    text = "Recuerda levantarte, estirarte y beber agua."
    assert len(_ngrams(text)) < 12

    s.record_output(name, text, is_proactive=True, now=base)
    s.record_output(name, text, is_proactive=True, now=base + 60.0)

    signal = s.score_unanswered_proactive_draft(
        name,
        text,
        silence_since=base - 1.0,
        now=base + 120.0,
    )

    assert signal.triggered is True
    assert signal.match_count == 2
    assert signal.considered_count == 2


def test_unanswered_proactive_repeat_resets_after_real_user_message(tmp_path):
    """A genuine user message invalidates older unanswered-repeat evidence."""
    s = _build_store(tmp_path)
    name = "Neko"
    base = 3_000_000.0
    text = "屏幕上这个新的小猫按钮好好看啊，快点点一下看看吧。"
    s.record_output(name, text, is_proactive=True, now=base)
    s.record_output(name, text + "真的很可爱。", is_proactive=True, now=base + 60.0)

    signal = s.score_unanswered_proactive_draft(
        name,
        text,
        silence_since=base + 120.0,
        now=base + 180.0,
    )

    assert signal.triggered is False
    assert signal.match_count == 0
    assert signal.considered_count == 0


def test_unanswered_proactive_repeat_drops_entries_beyond_max_age(tmp_path):
    """Proactive outputs older than the configured window are not evidence."""
    s = _build_store(tmp_path)
    name = "Neko"
    base = 5_000_000.0
    text = "屏幕上这个新的小猫按钮好好看啊，快点点一下看看吧。"
    s.record_output(name, text, is_proactive=True, now=base)
    s.record_output(name, text + "真的很可爱。", is_proactive=True, now=base + 60.0)

    signal = s.score_unanswered_proactive_draft(
        name,
        text,
        silence_since=base - 1_000_000.0,
        max_age_seconds=86_400.0,
        now=base + 90_000.0,
    )

    assert signal.triggered is False
    assert signal.considered_count == 0


def test_unanswered_proactive_repeat_ignores_regular_ai_outputs(tmp_path):
    """Regular AI replies cannot fabricate an ignored-proactive signal."""
    s = _build_store(tmp_path)
    name = "Neko"
    base = 4_000_000.0
    text = "屏幕上这个新的小猫按钮好好看啊，快点点一下看看吧。"
    s.record_output(name, text, is_proactive=False, now=base)
    s.record_output(name, text, is_proactive=False, now=base + 60.0)

    signal = s.score_unanswered_proactive_draft(
        name,
        text,
        silence_since=base - 1.0,
        now=base + 120.0,
    )

    assert signal.triggered is False
    assert signal.considered_count == 0


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


# ── 8. arecord_output：落盘必须离开事件循环 ──────────────────


@pytest.mark.asyncio
async def test_arecord_output_persists_off_the_event_loop(tmp_path, monkeypatch):
    """The corpus write must not run on the caller's event loop thread.

    record_output ends in atomic_write_json, whose tail is an unbounded
    os.fsync. This corpus is written on EVERY committed assistant reply, so
    on the realtime session's loop that physical flush lands between audio
    chunks. Asserting on the writing thread pins the property itself, not
    the mere presence of an asyncio.to_thread call.
    """
    from memory import anti_repeat as anti_repeat_module

    store = _build_store(tmp_path)
    loop_thread = threading.get_ident()
    write_threads: list[int] = []
    real_write = anti_repeat_module.atomic_write_json

    def _spy(*args, **kwargs):
        write_threads.append(threading.get_ident())
        return real_write(*args, **kwargs)

    monkeypatch.setattr(anti_repeat_module, "atomic_write_json", _spy)

    await store.arecord_output("妮可", LONG_TIGER, is_proactive=False)

    assert write_threads, "arecord_output 必须真的落盘"
    assert loop_thread not in write_threads, (
        "落盘跑在了事件循环线程上——fsync 会掐住音频"
    )
    assert store._load_unlocked("妮可"), "内容必须真的进了 corpus"


@pytest.mark.asyncio
async def test_arecord_output_stamps_time_at_the_call_site(tmp_path, monkeypatch):
    """The timestamp is read when the coroutine is called, not whenever the
    worker thread happens to get scheduled.

    Two back-to-back records can reach the pool in either order, and the
    window is trimmed by ts — so the clock has to be read on the caller's
    side. Asserting on the *thread* that reads it is what distinguishes the
    two designs; asserting on the value alone cannot.
    """
    from memory import anti_repeat as anti_repeat_module

    store = _build_store(tmp_path)
    loop_thread = threading.get_ident()
    clock_threads: list[int] = []

    def _clock() -> float:
        clock_threads.append(threading.get_ident())
        return 1234.5

    monkeypatch.setattr(anti_repeat_module, "_now", _clock)

    await store.arecord_output("妮可", LONG_TIGER, is_proactive=True)

    assert clock_threads, "时间戳必须真的取过"
    assert clock_threads[0] == loop_thread, (
        "时间戳在 worker 线程里取的——两次投递的先后顺序就不再可信"
    )
    window = store._load_unlocked("妮可")
    assert [entry["ts"] for entry in window] == [1234.5]
    assert window[0]["is_proactive"] is True


@pytest.mark.asyncio
async def test_apreload_reads_without_holding_the_data_lock(tmp_path, monkeypatch):
    """A slow first read must not make loop-side scorers wait on a worker lock."""
    store = _build_store(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def _slow_read(_name):
        started.set()
        assert release.wait(timeout=5)
        return []

    monkeypatch.setattr(store, "_read_window_from_disk", _slow_read)
    preload = asyncio.create_task(store.apreload("妮可"))
    assert await asyncio.to_thread(started.wait, 5)

    lock = store._get_lock("妮可")
    acquired = lock.acquire(blocking=False)
    if acquired:
        lock.release()
    release.set()
    assert await preload is None

    assert acquired, "the worker held the data lock while performing disk I/O"
    assert store._cache["妮可"] == []


@pytest.mark.asyncio
async def test_apreload_caches_empty_window_after_disk_lookup_failure(
    tmp_path, monkeypatch
):
    """A failed warmup must not make the event loop retry the same disk read."""
    store = _build_store(tmp_path)
    calls = 0

    def _failed_read(_name):
        nonlocal calls
        calls += 1
        raise OSError("memory root unavailable")

    monkeypatch.setattr(store, "_read_window_from_disk", _failed_read)

    await store.apreload("妮可")
    staged = store.stage_output("妮可", LONG_TIGER, now=1.0)

    assert store._cache["妮可"]
    assert staged is not None
    assert calls == 1, "stage_output retried disk I/O synchronously after warmup failed"


def test_the_per_turn_callers_use_the_async_twin():
    """The two per-turn call sites must not fall back to the sync writer.

    Both run inside a coroutine on the realtime session's loop; a plain
    record_output there puts an unbounded fsync on that loop. The guard in
    scripts/check_async_blocking.py cannot see this pair (its documented
    depth-1 limit stops one hop short), so it is pinned here instead.
    """
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    for rel in _PER_TURN_CALL_SITES:
        source = (repo_root / rel).read_text(encoding="utf-8")
        assert "stage_output(" in source and "flush_staged_detached(" in source, (
            f"{rel} 必须走两段式：内存更新在收尾信号之前，落盘摘出去"
        )
        assert ".record_output(" not in source, (
            f"{rel} 回退到了同步 record_output —— 那会把 fsync 压在会话循环上"
        )


_PER_TURN_CALL_SITES = (
    "main_logic/omni_offline_client/_lifecycle.py",
    "main_logic/core/proactive.py",
)


def test_the_per_turn_callers_never_await_the_flush():
    """Nothing cancellable may sit between "delivered" and "report delivered".

    Both call sites flush after the turn's terminal signals are already out,
    and both then return the value their caller uses as the record that the
    turn committed. An ``await`` in that stretch reintroduces a cancellation
    point past the point of no return: ``CancelledError`` is a
    ``BaseException``, so it skips the surrounding ``except Exception`` and the
    ``return``, and the caller books an already-visible turn as undelivered.
    """
    import ast
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for rel in _PER_TURN_CALL_SITES:
        tree = ast.parse((repo_root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Await):
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"aflush_staged", "arecord_output"}:
                offenders.append(f"{rel}:{node.lineno} await {name}(...)")

    assert not offenders, (
        "这些 per-turn 调用点又把落盘 await 回了提交路径上：\n  "
        + "\n  ".join(offenders)
        + "\n用 flush_staged_detached() —— 到这一步这轮对用户已经发生完了，"
        "取消不该把「已投递」倒回成「没投递」"
    )


def test_the_data_lock_is_never_held_across_the_disk_write(tmp_path):
    """Scoring runs on the event loop and takes the same per-name lock.

    ``arecord_output`` hands the whole record to a worker; if that worker held
    the data lock across ``atomic_write_json`` (tail: an unbounded fsync), a
    concurrent ``score_draft`` on the loop would block on the worker — exactly
    the stall the off-loading exists to remove. So the write must happen with
    the data lock released.
    """
    from memory import anti_repeat as anti_repeat_module

    store = _build_store(tmp_path)
    store.record_output("妮可", LONG_TIGER, now=1.0)  # 建好锁与缓存
    held_during_write: list[bool] = []
    real_write = anti_repeat_module.atomic_write_json

    def _spy(*args, **kwargs):
        lock = store._get_lock("妮可")
        acquired = lock.acquire(blocking=False)
        held_during_write.append(not acquired)
        if acquired:
            lock.release()
        return real_write(*args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(anti_repeat_module, "atomic_write_json", _spy)
        store.record_output("妮可", LONG_FRUIT, now=2.0)
    finally:
        monkeypatch.undo()

    assert held_during_write == [False], (
        "落盘时数据锁还握着——事件循环上的 score_draft 会卡在这次 fsync 上"
    )


def test_a_stale_snapshot_never_overwrites_a_newer_one(tmp_path):
    """Workers can reach the disk out of order; the older window must lose.

    Writes no longer happen under the data lock, so two records can be staged
    in caller order yet reach ``atomic_write_json`` in the opposite order. The
    staged sequence number, not the winner of the write lock, decides.
    """
    store = _build_store(tmp_path)
    store.record_output("妮可", LONG_TIGER, now=1.0)
    store.record_output("妮可", LONG_FRUIT, now=2.0)

    fresh = json.loads(
        (tmp_path / "妮可" / "anti_repeat_corpus.json").read_text(encoding="utf-8")
    )
    newest_seq = store._staged_seq["妮可"]

    # 一次「迟到的」旧快照：seq 比已落盘的小，必须被丢掉。
    store._flush_snapshot("妮可", {"version": 1, "window": []}, newest_seq - 1)

    after = json.loads(
        (tmp_path / "妮可" / "anti_repeat_corpus.json").read_text(encoding="utf-8")
    )
    assert after == fresh, "陈旧快照把新窗口盖回去了"
    assert len(after["window"]) == 2


def test_entries_stay_ordered_by_timestamp(tmp_path):
    """Out-of-order arrivals must not make an older reply look like the newest.

    Scoring takes the trailing slice of the window as "the most recent
    entries", so ordering has to hold even when workers land out of order.
    """
    store = _build_store(tmp_path)
    store.record_output("妮可", LONG_FRUIT, now=200.0)
    store.record_output("妮可", LONG_TIGER, now=100.0)  # 迟到的更早那条

    window = store._load_unlocked("妮可")
    assert [entry["ts"] for entry in window] == [100.0, 200.0]


class _GatedAsyncio:
    """Stands in for ``anti_repeat.asyncio``: parks at the to_thread boundary."""

    def __init__(self, reached: "asyncio.Event", release: "asyncio.Event") -> None:
        self._reached = reached
        self._release = release

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def to_thread(self, fn, *args, **kwargs):
        self._reached.set()
        await self._release.wait()
        return await asyncio.to_thread(fn, *args, **kwargs)


@pytest.mark.asyncio
async def test_the_corpus_is_updated_before_arecord_output_yields(tmp_path, monkeypatch):
    """Scoring must never miss the reply that was just committed.

    Only the disk write is off-loaded; the in-memory update runs on the
    caller's thread, before the coroutine yields at all. Deferring the whole
    record to a worker leaves a window in which the loop runs the next turn's
    score_draft / top_recent_topics against a corpus that is still missing
    this reply — and repeats it.

    Observed exactly at the to_thread boundary: that is the first instant the
    loop can run anything else, and it is where the two designs differ.
    """
    from memory import anti_repeat as anti_repeat_module

    store = _build_store(tmp_path)
    reached = asyncio.Event()
    release = asyncio.Event()
    monkeypatch.setattr(
        anti_repeat_module, "asyncio", _GatedAsyncio(reached, release),
    )

    task = asyncio.create_task(store.arecord_output("妮可", LONG_TIGER, now=1.0))
    await asyncio.wait_for(reached.wait(), timeout=5)

    # 协程刚让出，落盘还没开始 —— 但 corpus 必须已经含有这条。
    total, _terms = store.score_draft("妮可", LONG_TIGER, now=1.0)
    assert total > 0, "协程让出时 corpus 还没更新，下一轮打分会漏掉刚说过的这句"

    release.set()
    await asyncio.wait_for(task, timeout=5)


# ── 9. flush_staged_detached：提交路径上不许有取消点 ──────────


@pytest.mark.asyncio
async def test_detached_flush_still_reaches_disk(tmp_path):
    """Detaching must not turn the write into a no-op."""
    store = _build_store(tmp_path)
    staged = store.stage_output("妮可", LONG_TIGER, now=1.0)
    assert staged is not None

    store.flush_staged_detached(staged)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if not store._detached_flushes:
            break
    assert not store._detached_flushes, "摘下来的落盘 task 没跑完"

    reloaded = _build_store(tmp_path)
    total, _terms = reloaded.score_draft("妮可", LONG_TIGER, now=1.0)
    assert total > 0, "落盘没到盘上，重启后这条就丢了"


@pytest.mark.asyncio
async def test_cancelling_the_caller_cannot_rewind_a_delivered_turn(tmp_path):
    """The caller must reach its `return` even when cancelled at the flush.

    This is the whole reason the flush is detached. The turn's terminal
    signals are already out by this point, so the delivery has happened; if
    cancellation could still unwind past the return, the caller would book it
    as undelivered and the same proactive line could be sent again.
    """
    from memory import anti_repeat as anti_repeat_module

    store = _build_store(tmp_path)
    terminal_signals = asyncio.Event()
    reached_return = asyncio.Event()

    # 把落盘卡死在 to_thread 上。这样「落盘是不是取消点」就是确定性的差异，不用赌
    # 一次 to_thread 往返能不能在某个 sleep(0) 里跑完。
    reached_disk = asyncio.Event()
    release_disk = asyncio.Event()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        anti_repeat_module, "asyncio", _GatedAsyncio(reached_disk, release_disk),
    )
    try:
        async def _deliver() -> bool:
            staged = store.stage_output("妮可", LONG_TIGER, now=1.0)
            await terminal_signals.wait()   # 模拟 TTS 收尾 + 两处 turn end
            store.flush_staged_detached(staged)
            reached_return.set()
            return True

        task = asyncio.create_task(_deliver())
        await asyncio.sleep(0)
        terminal_signals.set()
        await asyncio.sleep(0)

        # 收尾信号一发完，协程就该已经跑到 return 了：落盘摘出去之后，这段里再没有
        # 挂起点。谁要是把 aflush_staged await 回来，协程此刻还挂在落盘上。
        assert reached_return.is_set(), (
            "收尾信号之后仍有挂起点 —— 落盘被 await 回了提交路径上"
        )

        # 取消请求这时才到。已经没有挂起点可以让它落进去，凭据必须照常返回。
        task.cancel()
        assert await task is True
    finally:
        release_disk.set()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if not store._detached_flushes:
                break
        monkeypatch.undo()

    assert not store._detached_flushes, "摘出去的落盘 task 没跑完就泄漏了"


def test_detached_flush_without_a_running_loop_is_a_no_op(tmp_path):
    """No loop to attach to must degrade quietly, never fsync inline."""
    # 同步调用方 / 循环已关停时，回退成「就地同步落盘」会把这轮改动移走的 fsync
    # 又搬回来。best-effort 的东西宁可丢。
    store = _build_store(tmp_path)
    staged = store.stage_output("妮可", LONG_TIGER, now=1.0)

    store.flush_staged_detached(staged)

    assert not store._detached_flushes
    assert not os.path.exists(store._file_path("妮可")), (
        "没有事件循环时不该就地落盘"
    )


def test_detached_flush_ignores_a_none_handle(tmp_path):
    """stage_output returns None for skipped text; that must stay harmless."""
    store = _build_store(tmp_path)
    store.flush_staged_detached(None)
    assert not store._detached_flushes
