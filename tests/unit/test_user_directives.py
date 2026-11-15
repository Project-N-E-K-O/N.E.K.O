# -*- coding: utf-8 -*-
"""Unit tests for memory.user_directives: banned-topic extraction, persistence, and
prompt injection.

Four contract groups:

1. extract_directives extracts terms from banned-topic templates in seven locales,
   with parallel matching for mixed Chinese/English and no false positives on unrelated text.
2. UserDirectivesManager.record refreshes last_seen + expire_at + hit_count
   on matching (kind,term.casefold()), rather than inserting another entry.
3. TTL: entries disappear from get_active after USER_DIRECTIVE_TTL_SECONDS;
   purge_expired deletes and persists expired entries.
4. render_prompt_block joins active terms into a system-prompt segment with the correct
   language header; empty lists return \"\".
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from config import (
    USER_DIRECTIVE_MAX_STORED,
    USER_DIRECTIVE_TTL_MAX_SECONDS,
    USER_DIRECTIVE_TTL_SECONDS,
)
from config.prompts.prompts_directives import (
    DIRECTIVE_PATTERNS,
    extract_directives,
    render_directives_block,
)
from memory.user_directives import _effective_ttl


# ── helpers ──────────────────────────────────────────────────────────


def _build_manager(tmp_path):
    """Construct a UserDirectivesManager bound to ``tmp_path`` so json
    round-trips through real disk I/O."""
    from memory.user_directives import UserDirectivesManager

    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    mgr = UserDirectivesManager()
    mgr._config_manager = cm
    return mgr


def _read_file(tmp_path, name) -> dict:
    p = os.path.join(str(tmp_path), name, "user_directives.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# -- 1. Extraction: all seven locales and parallel matching. --


@pytest.mark.parametrize("text,expected_term", [
    # Chinese: 别再提 / 不要再说 / 不想聊 / 关于.
    ("别再提小明了", "小明"),
    ("不要再说工作的事了！", "工作"),
    # 的事 is an optional Chinese-pattern trailing suppression suffix; removing it leaves 昨天发生,
    # semantically sufficient for LLM injection, retaining 昨天 and 发生 tokens.
    ("我不想聊昨天发生的事", "昨天发生"),
    ("关于股票就别再讲了", "股票"),
    # en: stop / don't / I don't want
    ("stop talking about work please.", "work"),
    ("don't mention my ex again", "my ex"),
    ("i don't want to talk about politics anymore", "politics"),
    # ja: のこと/についてはもう
    ("仕事のことはもう言わないで", "仕事"),
    ("もう天気の話は嫌だ", "天気"),
    # ko: 에 대해 / 그만
    ("그 일에 대해서는 그만 얘기해줘", "그 일"),
    # ru: не говори про / о ... больше не говори
    ("не говори про работу.", "работу"),
    ("не упоминай моей бывшей больше", "моей бывшей"),
    # es: no hables de
    ("no hables de fútbol, por favor.", "fútbol"),
    ("no menciones mi ex jamás", "mi ex"),
    # pt: não fale de
    ("não fale de trabalho hoje.", "trabalho"),
    ("não mencione minha ex nunca mais", "minha ex"),
])
def test_extract_directives_per_locale(text, expected_term):
    """Core templates in all seven locales extract terms."""
    hits = extract_directives(text)
    assert hits, f"no hit for {text!r}"
    terms = {term for _loc, _kind, term in hits}
    assert any(expected_term in t for t in terms), (
        f"expected {expected_term!r} in {terms!r} for {text!r}"
    )


def test_extract_directives_mixed_language():
    """Mixed Chinese/English also matches: patterns run in parallel without language detection."""
    text = "拜托 stop saying 加班 already"
    hits = extract_directives(text)
    terms = {t for _loc, _kind, t in hits}
    assert "加班" in terms


def test_extract_directives_negative_no_directive():
    """Ordinary statements, greetings, and questions must not trigger."""
    for text in [
        "",
        "今天天气真好。",
        "What time is it?",
        "我喜欢吃西瓜。",  # Preference statement, not an explicit ban.
        "Hello there.",
        "그냥 안녕하세요",
    ]:
        assert extract_directives(text) == [], f"false positive on {text!r}"


def test_extract_directives_term_length_bounds():
    """Discard terms shorter than two or longer than forty characters defensively."""
    # Too short: discard the captured single character a.
    assert extract_directives("don't say a please") == []
    # Too long: construct a 50-character fake term.
    long_term = "x" * 50
    text = f"stop talking about {long_term}."
    hits = extract_directives(text)
    # May discard entirely or capture a <=40-character substring; every term must be <=40.
    for _loc, _kind, term in hits:
        assert len(term) <= 40


def test_extract_directives_dedup_same_term():
    """Deduplicate one sentence's identical (kind,term) matches across locale patterns."""
    # English stop talking about and Japanese patterns cannot capture the same English term simultaneously,
    # but a Chinese/English-overlap setup can test deduplication itself.
    text = "别再提小明了，don't mention 小明 again"
    hits = extract_directives(text)
    # Both Chinese and English capture 小明; deduplication leaves one result.
    term_lower_kind = {(k, t.casefold()) for _l, k, t in hits}
    assert ("ban_topic", "小明") in term_lower_kind
    # Each (kind, term.casefold()) occurs once.
    flat = [(k, t.casefold()) for _l, k, t in hits]
    assert len(flat) == len(set(flat)), f"dups in {hits!r}"


def test_directive_patterns_have_capture_group():
    """Every pattern must have a term capture group."""
    for locale, kind, pat in DIRECTIVE_PATTERNS:
        assert pat.groups >= 1, (
            f"{locale}/{kind} pattern {pat.pattern!r} 缺少 capture group"
        )


# -- 1b. Korean template whitespace cannot be partitioned arbitrarily (ReDoS). --


@pytest.mark.parametrize("text,expected_term", [
    # Template 1: extra whitespace, including tabs, between topic / trigger / particle remains recognized.
    ("그 일   에 대해서는   그만 얘기해줘", "그 일"),
    ("회사  얘기   는   그만해", "회사"),
    ("시험 얘기\t그만", "시험"),
    # Template 3: whitespace on both sides of optional 이/가.
    ("숙제  가   듣기 싫어", "숙제"),
    ("야근   짜증나", "야근"),
])
def test_extract_directives_ko_whitespace_runs(text, expected_term):
    """Extra whitespace around the ko topic still yields the same term.

    Pins the behaviour the atomic ``(?>\\s*)`` runs in the ko templates must keep.
    """
    terms = {t for loc, _kind, t in extract_directives(text) if loc == "ko"}
    assert terms == {expected_term}, terms


# Absolute ceiling is fallback only; growth ratios below are the primary criterion, independent of runner speed.
_WHITESPACE_CEILING_S = 3.0


def _best_time(text: str, runs: int = 3) -> float:
    """Fastest of ``runs`` calls, so one noisy-neighbour spike doesn't count."""
    import time

    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        extract_directives(text)
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.parametrize("make", [
    # Template 3: ``(.{1,30}?)\s*(?:이|가)?\s*`` is cubic on whitespace; without atomic grouping, 480 takes 5-7s and 800 takes 19s.
    lambda n: " " * n,
    # Template 1 has the same ``\s*(?:는|은)?\s*`` after triggers. Prefix whitespace allows many topic
    # splits reaching 말; without atomic grouping template 1 alone takes 1.6s, template 3 eight seconds on the same input.
    lambda n: " " * 39 + "말" + " " * n,
], ids=["spaces", "padded_keyword"])
def test_extract_directives_whitespace_does_not_blow_up(make):
    """``extract_directives`` runs synchronously on every user message with no
    length cap, so a whitespace-heavy message must not stall it.

    The main assertion is the growth ratio from 200 to 800 characters, which
    does not depend on how fast the runner is: cubic is 64x (the ko templates
    before their whitespace runs were made atomic), quadratic 16x (about
    12-13x measured, the shape right after that, before topic captures were
    barred from starting on whitespace), linear 4x (about 3.5x now; see
    test_directive_regex_whitespace.py). The line sits at 7x, between linear
    and quadratic. The absolute ceiling is only a loose backstop for shared CI
    runners.
    """
    # Warm up to exclude initial regex compilation.
    extract_directives(" ")
    short = _best_time(make(200))
    long = _best_time(make(800))
    assert long < _WHITESPACE_CEILING_S, f"{long:.3f}s"
    # Use a 7x growth threshold between linear (~3.5x) and quadratic (~12-13x); the previous 35x caught only cubic behavior,
    # letting quadratic regression (topics starting in whitespace again) pass. Increase sizes from 120/480 to 200/800 because
    # smaller quadratic terms do not dominate constants and measure only 10-12x, too near the threshold; larger sizes make
    # cubic regressions take minutes. Measurements: corrected 800-character inputs take 0.2-0.6ms; quadratic regressions 109/174ms,
    # against 59/101ms thresholds. The absolute 2ms allowance covers corrected timing noise only, far below quadratic costs.
    assert long < short * 7 + 0.002, f"200: {short:.4f}s, 800: {long:.4f}s"


# ── 2. record dedup + refresh ────────────────────────────────────


def test_record_dedup_refreshes_existing(tmp_path):
    """Second match of (kind,term.casefold()) increments hit_count and refreshes last_seen."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    first = mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    assert first["hit_count"] == 1
    assert first["last_seen_at"] == 1000.0
    assert first["expire_at"] == 1000.0 + USER_DIRECTIVE_TTL_SECONDS

    second = mgr.record(name, locale="en", kind="ban_topic", term="加班", now=2000.0)
    assert second["hit_count"] == 2
    assert second["last_seen_at"] == 2000.0
    # Renewal uses the incremented count: this repetition itself proves the user repeated
    # the instruction, so count it immediately rather than waiting for another hit.
    assert second["expire_at"] == 2000.0 + _effective_ttl(2)
    # Preserve the initial locale as a diagnostic signal.
    assert second["locale"] == "zh"

    # Only one disk entry.
    data = _read_file(tmp_path, name)
    assert len(data["directives"]) == 1


# -- 2b. TTL increases with repetition and is capped. --


def test_effective_ttl_grows_with_hits_and_caps():
    """Repeats buy a longer window, and the growth must be capped."""
    # Do not copy implementation formulas into assertions, making constant changes automatically pass mutation tests.
    # Pin three properties: initial TTL equals base, increases strictly to the cap, and stops there.
    assert _effective_ttl(1) == USER_DIRECTIVE_TTL_SECONDS
    # Strictly increasing until the cap.
    growing = [_effective_ttl(h) for h in range(1, 12)]
    assert growing == sorted(growing)
    assert growing[1] > growing[0], "重复一次必须比只说一次记得久"
    # Arbitrarily large counts never exceed the cap and can actually reach it.
    assert _effective_ttl(10_000) == USER_DIRECTIVE_TTL_MAX_SECONDS
    assert max(growing) == USER_DIRECTIVE_TTL_MAX_SECONDS, (
        "递增必须能在合理次数内达到封顶，否则封顶常量形同虚设"
    )
    # Constant relationship guards MAX below the base value.
    assert USER_DIRECTIVE_TTL_MAX_SECONDS > USER_DIRECTIVE_TTL_SECONDS
    # Dirty input does not crash; fall back to the base window.
    assert _effective_ttl(None) == USER_DIRECTIVE_TTL_SECONDS
    assert _effective_ttl("abc") == USER_DIRECTIVE_TTL_SECONDS
    assert _effective_ttl(0) == USER_DIRECTIVE_TTL_SECONDS
    assert _effective_ttl(-5) == USER_DIRECTIVE_TTL_SECONDS


def test_repeated_directive_outlives_a_one_off(tmp_path):
    """Behavioural: a twice-said directive outlives a once-said one."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=0.0)
    mgr.record(name, locale="zh", kind="ban_topic", term="股票", now=0.0)
    # Repeat the overtime topic.
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1.0)

    # Just beyond base TTL, once-mentioned stocks expire while twice-mentioned overtime remains.
    later = USER_DIRECTIVE_TTL_SECONDS + 100.0
    terms = {e["term"] for e in mgr.get_active(name, now=later)}
    assert terms == {"加班"}


def test_normalize_entry_backfills_ttl_from_hit_count(tmp_path):
    """Backfilled expire_at must use the row's own hit_count, not the base TTL."""
    # Legacy files lack expire_at; backfilling N-hit instructions as single-hit ones
    # would immediately reduce renewal duration after restart.
    from memory.user_directives import _normalize_entry

    entry = _normalize_entry({
        "term": "加班", "kind": "ban_topic",
        "created_at": 0.0, "last_seen_at": 100.0, "hit_count": 4,
    })
    assert entry is not None
    assert entry["expire_at"] == 100.0 + _effective_ttl(4)
    assert entry["expire_at"] > 100.0 + USER_DIRECTIVE_TTL_SECONDS


# -- 2c. Persisted-entry cap with oldest-first rotation. --


def test_stored_cap_rotates_oldest_first(tmp_path):
    """Past MAX_STORED, evict least-recently-seen; the newest write must survive."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    total = USER_DIRECTIVE_MAX_STORED + 10
    for i in range(total):
        # Increasing last_seen: t0 is oldest.
        mgr.record(name, locale="zh", kind="ban_topic", term=f"t{i:03d}", now=float(i))
    data = _read_file(tmp_path, name)
    stored = [e["term"] for e in data["directives"]]
    assert len(stored) == USER_DIRECTIVE_MAX_STORED
    # Keep newest, evict oldest.
    assert f"t{total - 1:03d}" in stored
    assert "t000" not in stored
    # Retain exactly the newest last_seen entries, matching get_active sorting.
    assert set(stored) == {
        f"t{i:03d}" for i in range(total - USER_DIRECTIVE_MAX_STORED, total)
    }


def test_refresh_branch_also_rotates(tmp_path):
    """An over-cap store must come back under cap even with no new terms."""
    # Existing files may have hundreds of entries from before caps. Users repeating only existing
    # instructions never enter insertion, so rotation on refresh is needed to shrink these files
    # to cap. Every record synchronously reads/writes the whole file on each user-message path.
    mgr = _build_manager(tmp_path)
    name = "Neko"
    over = USER_DIRECTIVE_MAX_STORED + 20
    for i in range(over):
        mgr.record(name, locale="zh", kind="ban_topic", term=f"t{i:03d}", now=float(i))
    # Inject over-cap cache state to simulate files already oversized before upgrade.
    mgr._cache[name] = [
        {"term": f"old{i:03d}", "kind": "ban_topic", "locale": "zh",
         "created_at": 0.0, "last_seen_at": float(i),
         "expire_at": 1e12, "hit_count": 1, "source": "regex"}
        for i in range(over)
    ]
    # Refresh one existing instruction without insertion.
    mgr.record(name, locale="zh", kind="ban_topic", term="old000", now=1e6)
    stored = _read_file(tmp_path, name)["directives"]
    assert len(stored) == USER_DIRECTIVE_MAX_STORED


def test_new_entry_rotated_out_immediately_is_not_reported_as_recorded(tmp_path):
    """record() must not claim success for a row rotate just evicted."""
    # Trigger: cap active entries all have last_seen >= this timestamp. Clock rollback
    # (NTP correction / resume / dual-boot timezone on this Windows desktop app) or same-millisecond
    # ties suffice. Stable sorting preserves ties, leaving newly appended entries at the tail where slicing
    # removes them. Unconditional success would make callers mark a nonexistent
    # term for prompt injection.
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr._cache[name] = [
        {"term": f"full{i:03d}", "kind": "ban_topic", "locale": "zh",
         "created_at": 0.0, "last_seen_at": 9000.0,
         "expire_at": 1e12, "hit_count": 1, "source": "regex"}
        for i in range(USER_DIRECTIVE_MAX_STORED)
    ]
    # Clock rollback makes the new timestamp older than every stored entry.
    result = mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    stored = {e["term"] for e in _read_file(tmp_path, name)["directives"]}
    assert "加班" not in stored, "前提：这条确实被 rotate 挤掉了"
    assert result == {}, "被挤掉就不能报告成功，否则上游会去渲染一个不存在的 term"


def test_refreshed_entry_rotated_out_is_not_reported_as_recorded(tmp_path):
    """The refresh branch needs the same eviction guard as the insert branch."""
    # Symmetric bug: refresh rotation was added to fix preexisting oversized files,
    # without revisiting unconditional return dict(e); greptile P1 caught it.
    # Rollback makes refreshed last_seen oldest, so rotation evicts it,
    # but the caller marks a term absent from disk for injection.
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr._cache[name] = [
        {"term": "加班", "kind": "ban_topic", "locale": "zh", "created_at": 0.0,
         "last_seen_at": 9000.0, "expire_at": 1e12, "hit_count": 1, "source": "regex"}
    ] + [
        {"term": f"x{i:03d}", "kind": "ban_topic", "locale": "zh", "created_at": 0.0,
         "last_seen_at": 9000.0 + i + 1, "expire_at": 1e12, "hit_count": 1,
         "source": "regex"}
        for i in range(USER_DIRECTIVE_MAX_STORED)
    ]
    # Clock rollback: overtime refresh timestamp predates every stored entry.
    result = mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)

    stored = {e["term"] for e in mgr._cache[name]}
    assert "加班" not in stored, "前提：这条确实被 rotate 挤掉了"
    assert result == {}, "被挤掉就不能报告成功，否则上游会去渲染一个不存在的 term"


def test_rotate_drops_expired_before_live_entries(tmp_path):
    """Expired rows go first — dead data must not evict a live directive."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    # Fill cap slots with already expired entries.
    for i in range(USER_DIRECTIVE_MAX_STORED):
        mgr.record(name, locale="zh", kind="ban_topic", term=f"old{i:03d}", now=float(i))
    # Advance beyond all expiries, then insert a new entry.
    fresh_at = USER_DIRECTIVE_TTL_SECONDS + 10_000.0
    mgr.record(name, locale="zh", kind="ban_topic", term="新话题", now=fresh_at)

    stored = {e["term"] for e in _read_file(tmp_path, name)["directives"]}
    assert "新话题" in stored
    # Remove the entire expired group, not just one entry to make room.
    assert not any(t.startswith("old") for t in stored)


def test_record_case_insensitive_dedup(tmp_path):
    """Case does not affect deduplication: Work and work merge."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="en", kind="ban_topic", term="Work", now=1000.0)
    mgr.record(name, locale="en", kind="ban_topic", term="work", now=2000.0)
    active = mgr.get_active(name, now=2000.0)
    assert len(active) == 1
    assert active[0]["hit_count"] == 2


def test_record_different_terms_keep_separate(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    mgr.record(name, locale="zh", kind="ban_topic", term="股票", now=1001.0)
    active = mgr.get_active(name, now=1001.0)
    assert len(active) == 2
    assert {e["term"] for e in active} == {"加班", "股票"}


def test_record_from_text_end_to_end(tmp_path):
    """record_from_text exercises the complete extraction-to-persistence pipeline."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    written = mgr.record_from_text(name, "别再提小明了，stop saying work please")
    assert written
    terms = {e["term"] for e in written}
    assert "小明" in terms
    assert "work" in terms


# ── 3. TTL ────────────────────────────────────────────────────────


def test_ttl_expires_after_window(tmp_path):
    """After three days, get_active excludes the entry."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=0.0)
    # Inside the boundary.
    assert mgr.get_active(name, now=USER_DIRECTIVE_TTL_SECONDS - 1)
    # Outside the boundary.
    assert mgr.get_active(name, now=USER_DIRECTIVE_TTL_SECONDS + 1) == []


def test_purge_expired_drops_entries(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=0.0)
    mgr.record(name, locale="zh", kind="ban_topic", term="股票", now=1000.0)
    # Advance to overtime expiry while stocks remains active.
    # Overtime expires at USER_DIRECTIVE_TTL_SECONDS; stocks at 1000 + USER_DIRECTIVE_TTL_SECONDS.
    purge_at = USER_DIRECTIVE_TTL_SECONDS + 500.0
    removed = mgr.purge_expired(name, now=purge_at)
    assert removed == 1
    data = _read_file(tmp_path, name)
    assert {e["term"] for e in data["directives"]} == {"股票"}


def test_get_active_sorted_by_last_seen(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    # Terms must have at least two characters, validated at record boundary (CodeRabbit Minor).
    mgr.record(name, locale="zh", kind="ban_topic", term="aa", now=100.0)
    mgr.record(name, locale="zh", kind="ban_topic", term="bbb", now=200.0)
    mgr.record(name, locale="zh", kind="ban_topic", term="cccc", now=150.0)
    active = mgr.get_active(name, now=200.0)
    assert [e["term"] for e in active] == ["bbb", "cccc", "aa"]


def test_record_rejects_invalid_term(tmp_path):
    """record() rejects whitespace / oversized / non-string terms at its boundary."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    # Whitespace.
    assert mgr.record(name, locale="zh", kind="ban_topic", term="") == {}
    assert mgr.record(name, locale="zh", kind="ban_topic", term="   ") == {}
    # Single character.
    assert mgr.record(name, locale="zh", kind="ban_topic", term="X") == {}
    # Too long (>40).
    assert mgr.record(name, locale="zh", kind="ban_topic", term="x" * 41) == {}
    # Non-string.
    assert mgr.record(name, locale="zh", kind="ban_topic", term=None) == {}  # type: ignore[arg-type]
    # Valid
    assert mgr.record(name, locale="zh", kind="ban_topic", term="OK")
    # Trim both ends before persistence.
    e = mgr.record(name, locale="zh", kind="ban_topic", term="  ban-me  ")
    assert e["term"] == "ban-me"


def test_get_active_respects_limit(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    for i in range(30):
        mgr.record(
            name, locale="zh", kind="ban_topic",
            term=f"term-{i:02d}", now=float(i),
        )
    active = mgr.get_active(name, now=100.0, limit=5)
    assert len(active) == 5
    # Take the five greatest last_seen values.
    assert [e["term"] for e in active] == [
        "term-29", "term-28", "term-27", "term-26", "term-25"
    ]


# -- 3b. Pending injection flags during sessions. --


def test_record_from_text_marks_pending_injection(tmp_path):
    """A extracted directive raises the flag main_logic consumes to inject."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    assert mgr.take_pending_injection(name) is False  # Initially clean.
    written = mgr.record_from_text(name, "别再提小明了")
    assert written and any(written)
    assert mgr.take_pending_injection(name) is True


def test_take_pending_injection_is_take_once(tmp_path):
    """The flag is consumed once; otherwise every later turn re-injects it."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record_from_text(name, "别再提小明了")
    assert mgr.take_pending_injection(name) is True
    assert mgr.take_pending_injection(name) is False


def test_no_directive_hit_leaves_no_pending_flag(tmp_path):
    """Ordinary chat must not arm an injection (it would pad the prompt)."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    assert mgr.record_from_text(name, "今天天气不错啊") == []
    assert mgr.take_pending_injection(name) is False


def test_out_of_range_term_leaves_no_pending_flag(tmp_path):
    """A term rejected by the length check must not leave an empty-injection flag."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    # record rejects at boundary validation and returns {}.
    assert mgr.record(name, locale="zh", kind="ban_topic", term="x") == {}
    assert mgr.take_pending_injection(name) is False


def test_pending_flag_is_per_character(tmp_path):
    mgr = _build_manager(tmp_path)
    mgr.record_from_text("Neko", "别再提小明了")
    assert mgr.take_pending_injection("Other") is False
    assert mgr.take_pending_injection("Neko") is True


def test_clear_drops_pending_flag(tmp_path):
    """After clear there is nothing to inject, so the flag must go too."""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record_from_text(name, "别再提小明了")
    mgr.clear(name)
    assert mgr.take_pending_injection(name) is False


# ── 4. prompt render ─────────────────────────────────────────────


@pytest.mark.parametrize("lang,header_substring", [
    ("zh", "用户最近"),
    ("en", "user recently"),
    ("ja", "ユーザー"),
    ("ko", "사용자"),
    ("ru", "пользователь"),
    ("es", "usuario"),
    ("pt", "usuário"),
])
def test_render_prompt_block_per_lang(lang, header_substring):
    text = render_directives_block(["加班", "股票"], lang)
    assert "加班" in text and "股票" in text
    assert header_substring.lower() in text.lower()


def test_render_prompt_block_empty_returns_empty_string():
    assert render_directives_block([], "zh") == ""


def test_manager_render_prompt_block_integration(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    # Return "" when empty.
    assert mgr.render_prompt_block(name, "zh") == ""
    # Active entries form a complete block.
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    block = mgr.render_prompt_block(name, "zh", now=1000.0)
    assert "加班" in block
    assert block.startswith("\n")  # Leading newline facilitates concatenation.


def test_manager_render_skips_expired(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=0.0)
    block = mgr.render_prompt_block(
        name, "zh", now=USER_DIRECTIVE_TTL_SECONDS + 1
    )
    assert block == ""


# -- 5. Persistence roundtrip. --


def test_load_round_trip_from_disk(tmp_path):
    """Record, rebuild the manager, and load: fields remain identical."""
    name = "Neko"
    mgr1 = _build_manager(tmp_path)
    mgr1.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    mgr1.record(name, locale="en", kind="ban_topic", term="work", now=1500.0)

    mgr2 = _build_manager(tmp_path)
    active = mgr2.get_active(name, now=2000.0)
    terms = sorted(e["term"] for e in active)
    assert terms == ["work", "加班"]


def test_corrupt_file_starts_empty(tmp_path):
    """Externally corrupt files restart empty after reconstruction, nonfatally."""
    name = "Neko"
    char_dir = os.path.join(str(tmp_path), name)
    os.makedirs(char_dir, exist_ok=True)
    with open(os.path.join(char_dir, "user_directives.json"), "w", encoding="utf-8") as f:
        f.write("this is not json {")
    mgr = _build_manager(tmp_path)
    assert mgr.get_active(name, now=1000.0) == []


# ── 6. clear ─────────────────────────────────────────────────────


def test_clear_removes_all(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    mgr.clear(name)
    assert mgr.get_active(name, now=1000.0) == []
    data = _read_file(tmp_path, name)
    assert data["directives"] == []


# ── 7. sink integration (end-to-end via dispatch_user_utterance) ──


def test_user_utterance_sink_records(tmp_path, monkeypatch):
    """Simulate dispatch_user_utterance through storage; verify sink registration and event processing."""
    from memory import user_directives as ud_module

    # Temporarily point singleton _config_manager to tmp_path.
    mgr = ud_module.get_user_directives_manager()
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    monkeypatch.setattr(mgr, "_config_manager", cm)
    # Clear cache to prevent contamination from other tests.
    monkeypatch.setattr(mgr, "_cache", {})

    name = "Neko"
    ud_module._on_user_utterance(name, {
        "type": "user_message",
        "content": "别再提小明了",
        "lanlan": name,
        "is_voice": False,
    })
    active = mgr.get_active(name)
    assert {e["term"] for e in active} == {"小明"}


def test_user_utterance_sink_skips_default_bucket(tmp_path, monkeypatch):
    """The same dispatch also sends a default bucket. When event[\"lanlan\"]
    identifies a real character, default is duplicate and skipped. Otherwise empty/missing/
    literal default lanlan must persist, avoiding complete extraction loss (Codex P1)."""
    from memory import user_directives as ud_module
    mgr = ud_module.get_user_directives_manager()
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    monkeypatch.setattr(mgr, "_config_manager", cm)
    monkeypatch.setattr(mgr, "_cache", {})

    # Real-character event: event["lanlan"]="Neko", so skip the default bucket.
    ud_module._on_user_utterance("default", {
        "type": "user_message",
        "content": "别再提小明了",
        "lanlan": "Neko",
    })
    # Create no directories; another dispatch handles Neko, not triggered by this test.
    assert not os.listdir(str(tmp_path))


def test_user_utterance_sink_records_default_when_no_character(tmp_path, monkeypatch):
    """With unconfigured character / lanlan_name default / missing lanlan,
    dispatch sends only default; the sink must handle it or lose all user
    banned-topic instructions (Codex P1)."""
    from memory import user_directives as ud_module
    mgr = ud_module.get_user_directives_manager()
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    monkeypatch.setattr(mgr, "_config_manager", cm)
    monkeypatch.setattr(mgr, "_cache", {})

    # Scenario 1: missing lanlan field.
    ud_module._on_user_utterance("default", {
        "type": "user_message",
        "content": "别再提小明了",
    })
    active = mgr.get_active("default")
    assert {e["term"] for e in active} == {"小明"}

    monkeypatch.setattr(mgr, "_cache", {})

    # Scenario 2: literal lanlan == "default".
    ud_module._on_user_utterance("default", {
        "type": "user_message",
        "content": "别再提股票了",
        "lanlan": "default",
    })
    active = mgr.get_active("default")
    assert any("股票" in e["term"] for e in active)


def test_user_utterance_sink_handles_multimodal_content(tmp_path, monkeypatch):
    """For multimodal list content, join text before extraction."""
    from memory import user_directives as ud_module
    mgr = ud_module.get_user_directives_manager()
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    monkeypatch.setattr(mgr, "_config_manager", cm)
    monkeypatch.setattr(mgr, "_cache", {})

    name = "Neko"
    ud_module._on_user_utterance(name, {
        "content": [
            {"type": "text", "text": "别再提"},
            {"type": "text", "text": "工作"},
            {"type": "image", "url": "..."},
            "了",
        ],
    })
    active = mgr.get_active(name)
    assert any("工作" in e["term"] for e in active)


def test_sink_registered_after_install_runtime_bindings():
    """After app.runtime_bindings.install_runtime_bindings, the sink must already
    appear in main_logic.agent_event_bus._user_utterance_sinks.

    memory.user_directives does not self-register: it lives below main_logic,
    so upward imports cause LAYER_CYCLE. L6 app-layer runtime_bindings
    wires registration; identity-deduplicated register functions make repeated
    installation safe.
    """
    from app.runtime_bindings import install_runtime_bindings
    install_runtime_bindings()

    from memory import user_directives as ud_module
    from main_logic.agent_event_bus import _user_utterance_sinks

    assert ud_module._on_user_utterance in _user_utterance_sinks
