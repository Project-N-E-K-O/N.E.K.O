# -*- coding: utf-8 -*-
"""Unit tests for the habitual-phrase avoid list (memory.habitual_phrases).

Contract groups:

1. Cross-session criterion: a phrase counts only when it recurs across
   distinct sessions; a dense burst inside one session never does.
2. Exclusions: wording from her card or system prompt, short card values,
   quoted card phrases, names and nicknames, and replies carrying code or a
   URL never reach the list.
3. Size: at most HABITUAL_PHRASE_MAX_ITEMS entries, ranked by sessions, one
   entry per catchphrase rather than one per n-gram fragment, and the prompt
   list cut to the token budget.
4. Auto-revocation: a recompute replaces the whole list, and a stale file is
   ignored, so nothing stays blocked forever.
5. Session start only reads the stored file; it never mines.
6. The prompt block renders in all eight locales.
7. The background job: idle-gated, due-gated, leased, spawned in the
   background, and fed from time_indexed.db timestamps.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import string
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import (
    HABITUAL_PHRASE_MAX_AGE_SECONDS,
    HABITUAL_PHRASE_MAX_ITEMS,
    HABITUAL_PHRASE_MIN_SESSIONS,
    HABITUAL_PHRASE_PROMPT_MAX_TOKENS,
    HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS,
    HABITUAL_PHRASE_SESSION_GAP_SECONDS,
)
from config.prompts.prompts_directives import (
    HABITUAL_PHRASE_AVOID_PROMPT_BLOCK,
    RECENT_TOPIC_HINT_PROMPT_BLOCK,
    render_habitual_phrases_block,
)
from memory import habitual_phrases as hp
from memory.habitual_phrases import (
    HABITUAL_PHRASES_FILENAME,
    HabitualPhraseStore,
    TimedReply,
    build_payload,
    card_text_from_character,
    compute_entries,
)
from utils.tokenize import count_tokens

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NAME = "小天"
_T0 = datetime(2026, 11, 1, 9, 0, 0)
_LOCALES = {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
_GAP = timedelta(seconds=HABITUAL_PHRASE_SESSION_GAP_SECONDS + 60)


def _sessions(*sessions: list[str], start: datetime = _T0) -> list[TimedReply]:
    """Replies one minute apart inside a session, sessions a gap apart."""
    replies: list[TimedReply] = []
    moment = start
    for texts in sessions:
        for text in texts:
            replies.append(TimedReply(text, moment))
            moment += timedelta(minutes=1)
        moment += _GAP
    return replies


def _phrases(entries) -> list[str]:
    return [entry["phrase"] for entry in entries]


def _compute(replies, language="zh-CN", **kw):
    entries, _source = compute_entries(replies, language=language, **kw)
    return entries


def _cm(tmp_path):
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    cm.local_state_dir = Path(tmp_path)
    cm.app_docs_dir = Path(tmp_path)
    cm.load_root_state.return_value = {"mode": "normal"}
    return cm


def _store(tmp_path) -> HabitualPhraseStore:
    with patch("memory.habitual_phrases.get_config_manager", return_value=_cm(tmp_path)):
        return HabitualPhraseStore()


def _write(store, phrases, *, language="zh-CN", now=None, name=_NAME):
    entries = [
        {
            "phrase": phrase,
            "normalized_phrase": phrase.casefold(),
            "occurrence_count": 9 - index,
            "message_count": 9 - index,
            "session_count": 9 - index,
            "day_count": 3,
            "first_seen_at": 1.0,
            "last_seen_at": 2.0,
        }
        for index, phrase in enumerate(phrases)
    ]
    ts = datetime.now().timestamp() if now is None else now
    written = store.write(name, build_payload(entries, {}, language=language, now=ts))
    assert written


def _read_file(tmp_path, name=_NAME) -> dict:
    with open(os.path.join(str(tmp_path), name, HABITUAL_PHRASES_FILENAME), encoding="utf-8") as f:
        return json.load(f)


# ── 1. Cross-session criterion ────────────────────────────────────────────


def test_a_dense_burst_inside_one_session_is_not_a_habit():
    burst = _sessions(["今天也要加油哦。"] * 12)
    assert _compute(burst) == []


def test_the_same_phrase_across_sessions_is_a_habit():
    spread = _sessions(*[["今天也要加油哦。"]] * HABITUAL_PHRASE_MIN_SESSIONS)
    entries = _compute(spread)
    assert _phrases(entries) == ["今天也要加油哦"]
    entry = entries[0]
    assert entry["session_count"] == HABITUAL_PHRASE_MIN_SESSIONS
    assert entry["message_count"] == HABITUAL_PHRASE_MIN_SESSIONS
    assert entry["first_seen_at"] == spread[0].at.timestamp()
    assert entry["last_seen_at"] == spread[-1].at.timestamp()


def test_many_messages_in_too_few_sessions_still_do_not_count():
    """Distinct MESSAGES are not distinct sessions: two sittings, five replies."""
    two_sittings = _sessions(["今天也要加油哦。"] * 3, ["今天也要加油哦。"] * 2)
    assert _compute(two_sittings) == []


def test_days_are_counted_from_reply_dates():
    replies = [
        TimedReply("今天也要加油哦。", _T0 + timedelta(days=day, hours=hour))
        for day, hour in ((0, 0), (0, 3), (1, 0), (2, 0))
    ]
    (entry,) = _compute(replies)
    assert entry["session_count"] == 4
    assert entry["day_count"] == 3


def test_replies_without_a_time_are_left_out():
    replies = _sessions(*[["今天也要加油哦。"]] * HABITUAL_PHRASE_MIN_SESSIONS)
    replies.append(TimedReply("今天也要加油哦。", None))
    (entry,) = _compute(replies)
    assert entry["message_count"] == HABITUAL_PHRASE_MIN_SESSIONS


# ── 2. Exclusions ─────────────────────────────────────────────────────────


def _three_sessions(text):
    return _sessions(*[[text]] * HABITUAL_PHRASE_MIN_SESSIONS)


def test_a_phrase_written_into_the_card_is_hers():
    replies = _three_sessions("今天也要加油哦。")
    assert _phrases(_compute(replies)) == ["今天也要加油哦"]
    assert _compute(replies, card_text="一句话台词：今天也要加油哦~") == []


def test_a_phrase_written_into_the_system_prompt_is_hers():
    replies = _three_sessions("今天也要加油哦。")
    card_text, _terms = card_text_from_character({"性别": "女"}, "你常说今天也要加油哦。")
    assert _compute(replies, card_text=card_text) == []


def test_a_quoted_card_tic_excludes_every_phrase_carrying_it():
    replies = _three_sessions("知道了喵。")
    assert _phrases(_compute(replies)) == ["知道了喵"]
    card_text, terms = card_text_from_character({"说话风格": "句尾总要带上「喵~」这个字"})
    assert _compute(replies, card_text=card_text, protected_terms=terms) == []


def test_a_short_card_value_excludes_every_phrase_carrying_it():
    replies = _three_sessions("本喵觉得不错。")
    assert _phrases(_compute(replies)) == ["本喵觉得不错"]
    card_text, terms = card_text_from_character({"自称": "本喵", "性别": "女"})
    assert "本喵" in terms
    assert _compute(replies, card_text=card_text, protected_terms=terms) == []


@pytest.mark.parametrize("name", ["小天", "碳基生物"])
def test_names_and_nicknames_are_never_listed(name):
    replies = _three_sessions(f"{name}最喜欢你了。")
    assert _phrases(_compute(replies)) == [f"{name}最喜欢你了"]
    assert _compute(replies, protected_terms=[name]) == []


def test_latin_names_match_whole_words_only():
    replies = _three_sessions("I will always be here for you.")
    listed = _compute(replies, language="en", protected_terms=["Al"])
    assert listed, "'Al' must not cut into 'always'"
    named = _three_sessions("Thanks Bob for being here.")
    assert _compute(named, language="en", protected_terms=["Bob"]) == []


def test_reserved_card_fields_are_not_her_card():
    card_text, terms = card_text_from_character(
        {
            "voice_id": "今天也要加油哦",
            "_reserved": {"system_prompt": "今天也要加油哦"},
            "__ai_context.profile_rename_events": "今天也要加油哦",
            "性格": ["温柔", "今天也要加油哦"],
        }
    )
    assert card_text.count("今天也要加油哦") == 1, "only the ordinary list field counts"
    assert "温柔" in terms


@pytest.mark.parametrize(
    "suffix",
    [" https://example.com/page", " 看这个 `print(1)`", " www.example.com"],
)
def test_replies_carrying_code_or_a_url_contribute_nothing(suffix):
    replies = _three_sessions(f"今天也要加油哦{suffix}")
    assert _compute(replies) == []


# ── 3. Size, ranking, fragments, token budget ────────────────────────────

_DISTINCT_PHRASES = [
    "早上好呀朋友", "记得多喝热水", "晚安做个好梦", "别太累了休息", "我会一直陪你",
    "这个主意不错", "真拿你没办法", "要不要出去走", "天气变冷添衣", "慢慢来不着急",
]


def test_list_is_capped_and_ranked_by_sessions():
    sessions = []
    total = HABITUAL_PHRASE_MIN_SESSIONS + len(_DISTINCT_PHRASES)
    for index in range(total):
        sessions.append([
            f"{phrase}。"
            for rank, phrase in enumerate(_DISTINCT_PHRASES)
            if index < HABITUAL_PHRASE_MIN_SESSIONS + rank
        ])
    entries = _compute(_sessions(*sessions))
    assert len(entries) == HABITUAL_PHRASE_MAX_ITEMS
    expected = list(reversed(_DISTINCT_PHRASES))[:HABITUAL_PHRASE_MAX_ITEMS]
    assert _phrases(entries) == expected
    counts = [entry["session_count"] for entry in entries]
    assert counts == sorted(counts, reverse=True)


def test_one_catchphrase_is_one_entry_not_its_fragments():
    sentence = "哼才不是因为担心你呢"
    assert len(sentence) > 8, "longer than the miner's largest window"
    follow_ups = ["还有别的事吗？", "快去睡觉吧！", "下次早点说。"]
    entries = _compute(_sessions(*[[f"{sentence}。{tail}"] for tail in follow_ups]))
    assert _phrases(entries) == [sentence], (
        "shifted windows of one catchphrase must come back as that one phrase"
    )
    assert entries[0]["occurrence_count"] == HABITUAL_PHRASE_MIN_SESSIONS


@pytest.mark.parametrize(
    "texts",
    [
        ["好呀今天也要加油哦。", "嗯今天也要加油哦。", "今天也要加油哦！"],
        ["好呀今天也要加油哦。", "嗯嗯今天也要加油哦。", "哈哈今天也要加油哦。"],
        ["今天也要加油哦好吗", "今天也要加油哦对吧", "今天也要加油哦是不"],
    ],
    ids=["boundary", "left-neighbours-differ", "right-neighbours-differ"],
)
def test_a_phrase_is_grown_only_as_far_as_every_occurrence_agrees(texts):
    replies = _sessions(*[[text] for text in texts])
    assert _phrases(_compute(replies)) == ["今天也要加油哦"]


def test_an_excluded_catchphrase_still_claims_its_tail():
    """A line too long to grow in one piece is claimed whole once excluded.

    Growth stops at _MAX_PHRASE_CHARACTERS, so the windows near the end of a
    long templated line grow to a span that no longer reaches the name at its
    start. Were an excluded phrase not to claim them, that tail would be
    listed as if it were a separate habit.
    """
    line = "碳基生物" + "今天我们一起去公园散步看花吃冰淇淋然后回家"
    assert len(line) > 2 * hp._MAX_PHRASE_CHARACTERS - 8
    replies = _three_sessions(line)
    assert _compute(replies, protected_terms=["碳基生物"]) == []


def test_a_name_at_the_edge_of_a_long_phrase_is_still_caught():
    replies = _three_sessions("碳基生物今天最喜欢你了。")
    assert _phrases(_compute(replies)) == ["碳基生物今天最喜欢你了"]
    assert _compute(replies, protected_terms=["碳基生物"]) == []


def test_a_line_longer_than_the_growth_cap_is_still_one_entry():
    """Windows far apart on one long line grow into overlapping spans: one entry."""
    line = "今天我们一起去公园散步看花吃冰淇淋然后回家"
    assert len(line) > hp._MAX_PHRASE_CHARACTERS
    follow_ups = ["还有别的事吗？", "快去睡觉吧！", "下次早点说。"]
    entries = _compute(_sessions(*[[f"{line}。{tail}"] for tail in follow_ups]))
    assert len(entries) == 1
    assert entries[0]["phrase"] in line
    assert len(entries[0]["phrase"]) == hp._MAX_PHRASE_CHARACTERS


def test_a_fragment_used_more_widely_than_its_excluded_parent_is_still_listed():
    """Only fragments that tie or trail the excluded phrase are claimed by it."""
    replies = _sessions(
        *[["本喵觉得不错。"]] * HABITUAL_PHRASE_MIN_SESSIONS,
        *[["觉得不错。"]] * 2,
    )
    card_text, terms = card_text_from_character({"自称": "本喵"})
    assert _phrases(_compute(replies, card_text=card_text, protected_terms=terms)) == ["觉得不错"]


def test_word_phrases_are_deduplicated_by_shared_words():
    entries = _compute(_three_sessions("you know what I mean."), language="en")
    assert len(entries) == 1
    assert entries[0]["normalized_phrase"] == "you know what i mean"


def test_korean_word_and_character_windows_of_one_phrase_are_one_entry():
    """Korean is mined both ways; the character window must not come back on its own."""
    entries = _compute(_three_sessions("정말 고마워요."), language="ko")
    assert _phrases(entries) == ["정말 고마워요"]


def test_prompt_list_is_cut_to_the_token_budget(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _write(store, _DISTINCT_PHRASES[:HABITUAL_PHRASE_MAX_ITEMS])
    full = store.prompt_phrases(_NAME, "zh")
    assert full == _DISTINCT_PHRASES[:HABITUAL_PHRASE_MAX_ITEMS]
    assert count_tokens(render_habitual_phrases_block(full, "zh")) <= HABITUAL_PHRASE_PROMPT_MAX_TOKENS

    budget = count_tokens(render_habitual_phrases_block(_DISTINCT_PHRASES[:3], "zh"))
    monkeypatch.setattr(hp, "HABITUAL_PHRASE_PROMPT_MAX_TOKENS", budget)
    cut = store.prompt_phrases(_NAME, "zh")
    assert cut == _DISTINCT_PHRASES[:3], "lowest-ranked phrases go first"
    assert count_tokens(render_habitual_phrases_block(cut, "zh")) <= budget

    monkeypatch.setattr(hp, "HABITUAL_PHRASE_PROMPT_MAX_TOKENS", 5)
    assert store.prompt_phrases(_NAME, "zh") == [], "a block that cannot fit is not emitted"


def test_stored_entries_are_capped_at_read_time_too(tmp_path):
    store = _store(tmp_path)
    _write(store, [f"说法{i}说法{i}" for i in range(HABITUAL_PHRASE_MAX_ITEMS + 4)])
    assert len(store.prompt_phrases(_NAME, "zh")) <= HABITUAL_PHRASE_MAX_ITEMS


# ── 4. Auto-revocation ────────────────────────────────────────────────────


def test_a_stale_list_is_ignored(tmp_path):
    store = _store(tmp_path)
    now = datetime.now().timestamp()
    _write(store, ["今天也要加油哦"], now=now - HABITUAL_PHRASE_MAX_AGE_SECONDS - 60)
    assert store.prompt_phrases(_NAME, "zh", now=now) == []
    _write(store, ["今天也要加油哦"], now=now)
    assert store.prompt_phrases(_NAME, "zh", now=now) == ["今天也要加油哦"]


def test_a_list_mined_in_another_language_is_not_injected(tmp_path):
    store = _store(tmp_path)
    _write(store, ["今天也要加油哦"], language="zh-CN")
    assert store.prompt_phrases(_NAME, "en") == []
    assert store.prompt_phrases(_NAME, "zh-CN") == ["今天也要加油哦"]


def test_a_damaged_or_foreign_file_reads_as_absent(tmp_path):
    store = _store(tmp_path)
    path = os.path.join(str(tmp_path), _NAME, HABITUAL_PHRASES_FILENAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for content in ("{not json", json.dumps({"schema_version": "other/v9", "entries": []})):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        assert store.read(_NAME) is None
        assert store.prompt_phrases(_NAME, "zh") == []


_SHAPES = [
    {"entries": 1},
    {"entries": "x"},
    {"parameters": 1},
    {"source": "x"},
    {"computed_at": "soon"},
    {"language": 3},
]


@pytest.mark.parametrize("damage", _SHAPES, ids=[next(iter(d)) for d in _SHAPES])
def test_valid_json_of_the_wrong_shape_reads_as_absent(tmp_path, damage):
    store = _store(tmp_path)
    _write(store, ["今天也要加油哦"])
    path = os.path.join(str(tmp_path), _NAME, HABITUAL_PHRASES_FILENAME)
    payload = _read_file(tmp_path)
    payload.update(damage)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    assert store.read(_NAME) is None
    assert store.prompt_phrases(_NAME, "zh") == []


def test_damaged_entries_are_dropped_one_by_one(tmp_path):
    store = _store(tmp_path)
    _write(store, ["今天也要加油哦"])
    path = os.path.join(str(tmp_path), _NAME, HABITUAL_PHRASES_FILENAME)
    payload = _read_file(tmp_path)
    payload["entries"] = [1, "x", {"phrase": 3}, {"phrase": " "}, *payload["entries"]]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    assert store.prompt_phrases(_NAME, "zh") == ["今天也要加油哦"]


@pytest.mark.asyncio
async def test_the_job_rewrites_a_damaged_file_instead_of_failing_on_it(tmp_path, monkeypatch):
    from app.memory_server import habitual_phrase_loop, runtime

    store = _store(tmp_path)
    cm, time_manager = _loop_runtime(tmp_path, [_three_sessions("今天也要加油哦。")])
    monkeypatch.setattr(habitual_phrase_loop, "get_habitual_phrase_store", lambda: store)
    monkeypatch.setattr(habitual_phrase_loop, "get_character_prompt_locale", lambda _n: "zh-CN")
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "time_manager", time_manager)

    now = datetime.now().timestamp()
    _write(store, ["早上好呀朋友"], now=now - 60)
    path = os.path.join(str(tmp_path), _NAME, HABITUAL_PHRASES_FILENAME)
    payload = _read_file(tmp_path)
    payload["entries"] = 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    assert await habitual_phrase_loop._arefresh_habitual_phrases_one(_NAME, now=now)
    assert [e["phrase"] for e in _read_file(tmp_path)["entries"]] == ["今天也要加油哦"]


def test_a_reply_repeated_verbatim_does_not_rescan_per_fragment(monkeypatch):
    """A long reply said word for word in every session yields thousands of fragments.

    Each fragment of an already-claimed phrase must be skipped before the
    growth scan, and each reply split into units once: per-fragment rescans
    took about 20 s of background CPU on 3 x 2000 characters.
    """
    import random

    rng = random.Random(7)
    text = "".join(chr(rng.randint(0x4E00, 0x4E00 + 3000)) for _ in range(1990))
    replies = _three_sessions(text)
    grows, splits = [], []
    real_grow, real_split = hp._maximal_phrase, hp._MessageUnits.of
    monkeypatch.setattr(hp, "_maximal_phrase", lambda *a: grows.append(1) or real_grow(*a))
    monkeypatch.setattr(
        hp._MessageUnits, "of", classmethod(lambda cls, *a: splits.append(1) or real_split(*a)),
    )
    assert _compute(replies, card_text=text) == [], "every phrase is in the card"
    assert len(splits) <= 2 * len(replies)
    assert len(grows) <= len(text) // 4, f"{len(grows)} growth scans"


class _History:
    def __init__(self, replies):
        self.messages = [reply.text for reply in replies]
        self.timestamps = [reply.at for reply in replies]
        self.source_available = True


def _loop_runtime(tmp_path, replies_box, card=None):
    cm = _cm(tmp_path)
    cm.aget_character_data = AsyncMock(return_value=(
        "碳基生物", _NAME, {"档案名": "碳基生物", "昵称": "人类"},
        {_NAME: card or {}}, {"human": "碳基生物"}, {_NAME: ""}, {}, {}, {},
    ))
    cm.aload_characters = AsyncMock(return_value={"猫娘": {_NAME: {}}})
    time_manager = SimpleNamespace(
        aretrieve_latest_assistant_texts=AsyncMock(
            side_effect=lambda name, limit: _History(replies_box[0])
        ),
    )
    return cm, time_manager


@pytest.mark.asyncio
async def test_a_recompute_drops_a_phrase_she_stopped_using(tmp_path, monkeypatch):
    from app.memory_server import habitual_phrase_loop, runtime

    store = _store(tmp_path)
    replies_box = [_three_sessions("今天也要加油哦。")]
    cm, time_manager = _loop_runtime(tmp_path, replies_box)
    monkeypatch.setattr(habitual_phrase_loop, "get_habitual_phrase_store", lambda: store)
    monkeypatch.setattr(habitual_phrase_loop, "get_character_prompt_locale", lambda _n: "zh-CN")
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "time_manager", time_manager)

    now = datetime.now().timestamp()
    assert await habitual_phrase_loop._arefresh_habitual_phrases_one(_NAME, now=now)
    assert [e["phrase"] for e in _read_file(tmp_path)["entries"]] == ["今天也要加油哦"]

    # She moved on: the phrase now appears in one session only.
    replies_box[0] = _sessions(["今天也要加油哦。", "换个话题吧。"])
    later = now + HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS + 1
    assert await habitual_phrase_loop._arefresh_habitual_phrases_one(_NAME, now=later)
    stored = _read_file(tmp_path)
    assert stored["entries"] == [], "an entry that no longer qualifies must disappear"
    assert stored["computed_at"] == later
    assert store.prompt_phrases(_NAME, "zh", now=later) == []


@pytest.mark.asyncio
async def test_a_fresh_list_is_not_recomputed(tmp_path, monkeypatch):
    from app.memory_server import habitual_phrase_loop, runtime

    store = _store(tmp_path)
    cm, time_manager = _loop_runtime(tmp_path, [_three_sessions("今天也要加油哦。")])
    monkeypatch.setattr(habitual_phrase_loop, "get_habitual_phrase_store", lambda: store)
    monkeypatch.setattr(habitual_phrase_loop, "get_character_prompt_locale", lambda _n: "zh-CN")
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "time_manager", time_manager)

    now = datetime.now().timestamp()
    _write(store, ["早上好呀朋友"], now=now - 60)
    assert not await habitual_phrase_loop._arefresh_habitual_phrases_one(_NAME, now=now)
    time_manager.aretrieve_latest_assistant_texts.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_job_excludes_names_and_card_terms_from_characters_json(tmp_path, monkeypatch):
    from app.memory_server import habitual_phrase_loop, runtime

    store = _store(tmp_path)
    replies = _sessions(
        *[["本喵觉得不错。", "碳基生物最喜欢你了。", "记得多喝热水。"]] * HABITUAL_PHRASE_MIN_SESSIONS
    )
    cm, time_manager = _loop_runtime(tmp_path, [replies], card={"自称": "本喵"})
    monkeypatch.setattr(habitual_phrase_loop, "get_habitual_phrase_store", lambda: store)
    monkeypatch.setattr(habitual_phrase_loop, "get_character_prompt_locale", lambda _n: "zh-CN")
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "time_manager", time_manager)

    assert await habitual_phrase_loop._arefresh_habitual_phrases_one(_NAME, now=datetime.now().timestamp())
    assert [e["phrase"] for e in _read_file(tmp_path)["entries"]] == ["记得多喝热水"]


# ── 5. Session start only reads the file ─────────────────────────────────


def _session_manager(lang="zh-CN"):
    return SimpleNamespace(
        user_language=lang,
        lanlan_name=_NAME,
        lanlan_prompt="PROMPT",
        _is_agent_enabled=lambda: False,
    )


def _patch_session_start(monkeypatch, store, *, enabled=True):
    import memory.anti_repeat as anti_repeat_module
    import memory.anti_repeat_effects as effects_module
    import memory.user_directives as user_directives_module
    import utils.natural_expression_candidates as miner_module
    import utils.slop_filter as slop_filter_module

    corpus = SimpleNamespace(apreload=AsyncMock(), top_recent_topics=lambda _key: [])
    monkeypatch.setattr(anti_repeat_module, "get_anti_repeat_corpus", lambda: corpus)
    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager",
        lambda: SimpleNamespace(render_prompt_block=lambda *_a: ""),
    )
    monkeypatch.setattr(hp, "get_habitual_phrase_store", lambda: store)
    monkeypatch.setattr(slop_filter_module, "is_slop_filter_enabled", lambda: enabled)
    mined: list[str] = []

    def _never(*_args, **_kwargs):
        mined.append("mined")
        raise AssertionError("session start must not mine")

    monkeypatch.setattr(hp, "compute_entries", _never)
    monkeypatch.setattr(hp, "build_report", _never)
    monkeypatch.setattr(miner_module, "build_report", _never)
    recorded = MagicMock()
    monkeypatch.setattr(effects_module, "record_anti_repeat_phrase_hint", recorded)
    monkeypatch.setattr(effects_module, "record_anti_repeat_soft_hint", MagicMock())
    return mined, recorded


@pytest.mark.asyncio
async def test_session_start_injects_the_stored_list_without_mining(tmp_path, monkeypatch):
    from main_logic.core.notify import NotifyMixin

    store = _store(tmp_path)
    _write(store, ["今天也要加油哦", "真拿你没办法"])
    mined, recorded = _patch_session_start(monkeypatch, store)

    prompt = await NotifyMixin._build_initial_prompt(_session_manager())

    assert prompt.endswith(render_habitual_phrases_block(["今天也要加油哦", "真拿你没办法"], "zh"))
    assert mined == []
    recorded.assert_called_once_with(_NAME)


@pytest.mark.asyncio
async def test_session_start_follows_the_natural_expression_switch(tmp_path, monkeypatch):
    from main_logic.core.notify import NotifyMixin

    store = _store(tmp_path)
    _write(store, ["今天也要加油哦"])
    _mined, recorded = _patch_session_start(monkeypatch, store, enabled=False)

    prompt = await NotifyMixin._build_initial_prompt(_session_manager())

    assert "今天也要加油哦" not in prompt
    recorded.assert_not_called()


@pytest.mark.asyncio
async def test_session_start_without_a_file_adds_nothing_and_counts_nothing(tmp_path, monkeypatch):
    from main_logic.core.notify import NotifyMixin

    _mined, recorded = _patch_session_start(monkeypatch, _store(tmp_path))
    prompt = await NotifyMixin._build_initial_prompt(_session_manager())
    assert prompt.endswith("PROMPT")
    recorded.assert_not_called()


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_bytes())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node)
    raise AssertionError(f"{name} not found in {path}")


@pytest.mark.parametrize(
    "forbidden",
    ["compute_entries", "build_report", "build_user_review_report",
     "aretrieve_latest_assistant_texts", "natural_expression_candidates"],
)
def test_neither_session_start_nor_the_read_api_reaches_the_miner(forbidden):
    notify_src = _function_source(_REPO_ROOT / "main_logic" / "core" / "notify.py", "_build_initial_prompt")
    assert forbidden not in notify_src
    read_src = inspect.getsource(HabitualPhraseStore.prompt_phrases) + inspect.getsource(
        HabitualPhraseStore.read
    )
    assert forbidden not in read_src


# ── 6. Eight locales ──────────────────────────────────────────────────────


def test_every_locale_has_a_template_and_it_mirrors_the_topic_hint():
    assert set(HABITUAL_PHRASE_AVOID_PROMPT_BLOCK) == _LOCALES
    assert set(HABITUAL_PHRASE_AVOID_PROMPT_BLOCK) == set(RECENT_TOPIC_HINT_PROMPT_BLOCK)
    assert len(set(HABITUAL_PHRASE_AVOID_PROMPT_BLOCK.values())) == len(_LOCALES)
    for lang, template in HABITUAL_PHRASE_AVOID_PROMPT_BLOCK.items():
        fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
        assert fields == {"items", "n"}, lang
        assert template.startswith("\n\n["), lang


@pytest.mark.parametrize("lang", sorted(_LOCALES))
def test_every_locale_renders_the_phrases_verbatim(lang):
    block = HABITUAL_PHRASE_AVOID_PROMPT_BLOCK[lang].format(items="- 今天也要加油哦\n- 真拿你没办法", n=2)
    assert "- 今天也要加油哦\n- 真拿你没办法\n" in block
    assert "2" in block


@pytest.mark.parametrize("lang", ["zh", "en", "ja", "ko", "ru", "es", "pt"])
def test_render_resolves_each_short_locale(lang):
    block = render_habitual_phrases_block(["一", "二"], lang)
    assert block == HABITUAL_PHRASE_AVOID_PROMPT_BLOCK[lang].format(items="- 一\n- 二", n=2)


def test_render_of_nothing_is_empty():
    assert render_habitual_phrases_block([], "zh") == ""


# ── 7. The background job ─────────────────────────────────────────────────


def _loop_modules():
    from app import memory_server
    from app.memory_server import gates, habitual_phrase_loop, runtime

    return memory_server, gates, habitual_phrase_loop, runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{"idle": False}, {"fenced": True}, {"blocked": True}])
async def test_a_busy_fenced_or_blocked_memory_skips_the_round(kw, monkeypatch):
    _, gates, habitual_phrase_loop, runtime = _loop_modules()
    one = AsyncMock(return_value=True)
    cm = MagicMock()
    cm.aload_characters = AsyncMock(return_value={"猫娘": {_NAME: {}}})
    monkeypatch.setattr(habitual_phrase_loop, "_arefresh_habitual_phrases_one", one)
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", kw.get("blocked", False))
    monkeypatch.setattr(gates, "_is_idle", lambda: kw.get("idle", True))
    monkeypatch.setattr(
        "utils.cloudsave_runtime.is_write_fence_active", lambda _cm: kw.get("fenced", False),
    )
    await habitual_phrase_loop._arefresh_habitual_phrases_once()
    one.assert_not_awaited()
    cm.aload_characters.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_round_stops_as_soon_as_memory_turns_busy(monkeypatch):
    _, gates, habitual_phrase_loop, runtime = _loop_modules()
    visited: list[str] = []
    idle = iter([True, True, False, True])

    async def one(name, *, now):
        visited.append(name)
        return True

    cm = MagicMock()
    cm.aload_characters = AsyncMock(return_value={"猫娘": {"悠怡": {}, _NAME: {}, "小八": {}}})
    monkeypatch.setattr(habitual_phrase_loop, "_arefresh_habitual_phrases_one", one)
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", False)
    monkeypatch.setattr(gates, "_is_idle", lambda: next(idle))
    monkeypatch.setattr("utils.cloudsave_runtime.is_write_fence_active", lambda _cm: False)
    await habitual_phrase_loop._arefresh_habitual_phrases_once()
    assert visited == ["悠怡"]


@pytest.mark.asyncio
async def test_an_idle_round_visits_every_character_and_survives_one_failing(monkeypatch):
    _, gates, habitual_phrase_loop, runtime = _loop_modules()
    visited: list[str] = []

    async def one(name, *, now):
        visited.append(name)
        if name == "悠怡":
            raise RuntimeError("disk on fire")
        return True

    cm = MagicMock()
    cm.aload_characters = AsyncMock(return_value={"猫娘": {"悠怡": {}, _NAME: {}}})
    monkeypatch.setattr(habitual_phrase_loop, "_arefresh_habitual_phrases_one", one)
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "_memory_storage_blocked_after_init", False)
    monkeypatch.setattr(gates, "_is_idle", lambda: True)
    monkeypatch.setattr("utils.cloudsave_runtime.is_write_fence_active", lambda _cm: False)
    await habitual_phrase_loop._arefresh_habitual_phrases_once()
    assert visited == ["悠怡", _NAME]


@pytest.mark.asyncio
async def test_the_character_lease_is_always_returned(tmp_path, monkeypatch):
    _, _, habitual_phrase_loop, runtime = _loop_modules()
    store = _store(tmp_path)
    cm, time_manager = _loop_runtime(tmp_path, [[]])
    time_manager.aretrieve_latest_assistant_texts = AsyncMock(side_effect=RuntimeError("db gone"))
    ended: list[str] = []
    monkeypatch.setattr(habitual_phrase_loop, "get_habitual_phrase_store", lambda: store)
    monkeypatch.setattr(habitual_phrase_loop, "get_character_prompt_locale", lambda _n: "zh-CN")
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "time_manager", time_manager)
    monkeypatch.setattr(runtime, "_begin_character_request", lambda name: f"lease-{name}")
    monkeypatch.setattr(runtime, "_end_character_request", lambda name, token: ended.append(token))
    with pytest.raises(RuntimeError):
        await habitual_phrase_loop._arefresh_habitual_phrases_one(_NAME, now=datetime.now().timestamp())
    assert ended == [f"lease-{_NAME}"]
    assert not os.path.exists(os.path.join(str(tmp_path), _NAME, HABITUAL_PHRASES_FILENAME))


@pytest.mark.asyncio
async def test_the_loop_survives_a_failing_round(monkeypatch):
    _, gates, habitual_phrase_loop, _ = _loop_modules()
    sleeps: list[float] = []
    rounds = {"n": 0}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    async def flaky_round():
        rounds["n"] += 1
        raise OSError("transient")

    monkeypatch.setattr(habitual_phrase_loop, "_arefresh_habitual_phrases_once", flaky_round)
    monkeypatch.setattr("app.memory_server.habitual_phrase_loop.asyncio.sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await habitual_phrase_loop._periodic_habitual_phrase_loop()
    assert rounds["n"] == 2
    assert sleeps[0] == gates._INITIAL_DELAY_HABITUAL_PHRASES
    assert sleeps[1:] == [habitual_phrase_loop.HABITUAL_PHRASE_POLL_INTERVAL] * 2


def test_the_loop_is_spawned_in_the_background_not_awaited_on_startup():
    memory_server, gates, habitual_phrase_loop, _ = _loop_modules()
    src = inspect.getsource(memory_server.ensure_memory_server_runtime_initialized)
    assert "_spawn_background_task(habitual_phrase_loop._periodic_habitual_phrase_loop())" in src
    assert "await habitual_phrase_loop" not in src
    assert gates._INITIAL_DELAY_HABITUAL_PHRASES > gates._INITIAL_DELAY_SCOPED_REFINE
    assert memory_server._periodic_habitual_phrase_loop is habitual_phrase_loop._periodic_habitual_phrase_loop
    assert memory_server._INITIAL_DELAY_HABITUAL_PHRASES is gates._INITIAL_DELAY_HABITUAL_PHRASES
