# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Output-side guard: leaked internal guidance labels never reach spoken text.

The proactive Phase 2 prompt feeds the model tone-angle seeds and memory-cue
labels shaped as ``<label>：<description>``. Weak models sometimes echo the bare
``<label>`` as the first line of the reply, which the client splits into its own
chat bubble — the reported screenshot leak (``屏幕细节轻问`` followed by the real
line) and the older ``回忆 / 线索`` leak. ``_strip_proactive_intent_label_leak``
removes such a leading label before delivery. These tests pin both the derived
registry and the stripping behaviour, including the false-positive guard that
keeps the label words from being scrubbed when they occur in normal speech.
"""  # noqa: DOCSTRING_CJK
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from config.prompts.prompts_activity import get_proactive_intent_leak_labels
from main_routers.system_router import _strip_proactive_intent_label_leak


# ── Registry derivation ─────────────────────────────────────────────


def test_registry_covers_reported_cjk_labels() -> None:
    labels = get_proactive_intent_leak_labels()
    for lab in ('屏幕细节轻问', '回忆线索', '较久前的回忆线索', '质量闸',
                '体察式关心', '纯存在感', '主动关心'):
        assert lab.casefold() in labels, f"registry missing {lab!r}"


def test_registry_covers_latin_labels() -> None:
    labels = get_proactive_intent_leak_labels()
    for lab in ('one screen-detail beat', 'pure presence', 'Memory cues',
                'Older memory cue', 'quality bar', 'between-the-beats soft aside'):
        assert lab.casefold() in labels, f"registry missing {lab!r}"


def test_registry_covers_activity_state_enums() -> None:
    # Multi-token activity enums / labels are leak-prone and safe to deny: an
    # underscore key or multi-word label never occurs as natural speech.
    labels = get_proactive_intent_leak_labels()
    for lab in ('focused_work', 'restricted_screen_only', 'casual_browsing',
                'focused work', 'casual browsing'):
        assert lab.casefold() in labels, f"registry missing activity enum {lab!r}"


def test_registry_skips_single_common_word_enums() -> None:
    # Single common words (idle / gaming / open) are deliberately NOT denied —
    # they can legitimately open an English reply and _strip matches whole
    # first lines, so denying them would scrub real speech (greptile P2 #2173).
    labels = get_proactive_intent_leak_labels()
    for common in ('idle', 'gaming', 'open', 'away', 'chatting'):
        assert common.casefold() not in labels, f"common word wrongly denied: {common!r}"


def test_common_word_exclusions_cover_all_single_token_enums() -> None:
    # Contract: every single-token ActivityState/Propensity enum must be in the
    # exclusion set, else it would be denied and could scrub a legit English
    # reply opening with the word. This pins the exclusion set to stay complete
    # as the enums evolve — a new one-word state fails here instead of silently
    # stripping speech.
    from typing import get_args

    from config.prompts.prompts_activity import _ACTIVITY_ENUM_COMMON_WORDS
    from main_logic.activity.snapshot import ActivityState, Propensity

    single_token = [
        e for e in (*get_args(ActivityState), *get_args(Propensity))
        if '_' not in e and ' ' not in e
    ]
    missing = [e for e in single_token if e not in _ACTIVITY_ENUM_COMMON_WORDS]
    assert not missing, f"single-word enums missing from exclusion set: {missing}"


def test_registry_skips_colonless_sentence_bullets() -> None:
    # The hushed 2nd bullet is a full sentence with no "<label>：" shape; it must
    # NOT become a denylist entry (would be an over-broad, sentence-long label).
    labels = get_proactive_intent_leak_labels()
    assert '几乎不出声，让存在感和氛围本身兜住这一轮'.casefold() not in labels


# ── Stripping behaviour ─────────────────────────────────────────────


def test_strip_reported_screenshot_case() -> None:
    leaked = "屏幕细节轻问\n主人你在合成终端做的这个东西要用来干嘛呀喵"
    assert _strip_proactive_intent_label_leak(leaked) == \
        "主人你在合成终端做的这个东西要用来干嘛呀喵"


def test_strip_memory_cue_label_standalone_line() -> None:
    assert _strip_proactive_intent_label_leak("回忆线索\n今天过得怎么样呀") == "今天过得怎么样呀"


def test_strip_same_line_label_colon_content() -> None:
    assert _strip_proactive_intent_label_leak("屏幕细节轻问：你在写什么代码呀") == "你在写什么代码呀"
    assert _strip_proactive_intent_label_leak("回忆线索：上次说的旅行呢") == "上次说的旅行呢"


def test_strip_stacked_labels() -> None:
    assert _strip_proactive_intent_label_leak("屏幕细节轻问\n回忆线索\n主人在干嘛") == "主人在干嘛"


def test_strip_decorated_label() -> None:
    assert _strip_proactive_intent_label_leak("【屏幕细节轻问】\n你在看啥") == "你在看啥"
    assert _strip_proactive_intent_label_leak("**屏幕细节轻问**\n你在看啥") == "你在看啥"
    assert _strip_proactive_intent_label_leak("- 屏幕细节轻问\n你在看啥") == "你在看啥"


def test_strip_latin_label_case_insensitive() -> None:
    assert _strip_proactive_intent_label_leak(
        "One Screen-Detail Beat\nHey, what are you working on?"
    ) == "Hey, what are you working on?"
    assert _strip_proactive_intent_label_leak(
        "Memory cues: that trip you mentioned?"
    ) == "that trip you mentioned?"


def test_strip_same_line_mixed_colon_takes_earliest() -> None:
    # Half-width separator, full-width colon later in the body: must split on
    # the EARLIEST colon, else the leading label is mis-parsed and survives.
    assert _strip_proactive_intent_label_leak(
        "Memory cues: that trip：mentioned?"
    ) == "that trip：mentioned?"


# ── False-positive guard ────────────────────────────────────────────


def test_no_strip_when_first_line_not_a_label() -> None:
    txt = "你在看这个啊？看起来挺有意思的\n是新项目吗"
    assert _strip_proactive_intent_label_leak(txt) == txt


def test_no_strip_label_only_no_following_content() -> None:
    # Degenerate: only the label, nothing to keep — leave as-is rather than
    # deliver an empty string.
    assert _strip_proactive_intent_label_leak("屏幕细节轻问") == "屏幕细节轻问"


def test_no_strip_label_words_inside_natural_sentence() -> None:
    # The label words appearing mid-sentence (not as a heading) must survive.
    txt = "我刚刚也在想这个屏幕细节，问你一句话哦"
    assert _strip_proactive_intent_label_leak(txt) == txt


def test_empty_input_safe() -> None:
    assert _strip_proactive_intent_label_leak("") == ""
    assert _strip_proactive_intent_label_leak("   ") == "   "
