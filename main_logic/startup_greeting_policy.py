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
"""Selection and suppression policy for ordinary startup greetings."""

from __future__ import annotations

from memory.startup_greeting_history import StartupGreetingRecord


# 开屏问候的去重分两级：
#   · 召回窗（3 天）——所有已提交的开场都会作为「不要雷同」的参考进入 prompt，
#     记忆话题也在这个窗口内不复用。
#   · 强约束窗（1 天）——窗内的开场额外要求「不得复述或近义改写」，开场角度
#     也在这一层轮换，保证同一天内说法明显不同。
# 两级都只统计真正送达用户的文本（见 memory.startup_greeting_history）。
_STARTUP_GREETING_RECALL_SECONDS = 3 * 24 * 60 * 60
_STARTUP_GREETING_STRICT_SECONDS = 24 * 60 * 60
_STARTUP_GREETING_BURST_SECONDS = 30 * 60
# prompt 里每层最多列多少条。召回窗满载时可达上百条（30 分钟 burst 闸下每天
# 最多 48 条），全塞会把开屏 prompt 撑爆，所以两层都按条数封顶。
_STARTUP_GREETING_STRICT_SAMPLES = 6
_STARTUP_GREETING_EARLIER_SAMPLES = 6
_STARTUP_GREETING_VARIANT_MEMORY = "memory_followup"
_STARTUP_GREETING_GENERIC_VARIANTS = (
    "recent_continuity",
    "personal_share",
    "light_question",
    "simple_presence",
)


def split_startup_history_windows(
    recall_records: list[StartupGreetingRecord],
    *,
    observed_at: float,
    strict_seconds: float = _STARTUP_GREETING_STRICT_SECONDS,
) -> tuple[list[StartupGreetingRecord], list[StartupGreetingRecord]]:
    """Split newest-first recall records into the strict and earlier layers.

    ``recall_records`` comes from a single 3-day read.  Records at or inside
    ``strict_seconds`` form the strict layer; everything older stays in the
    earlier layer.  A record timestamped in the future (wall-clock rollback)
    counts as strict, which is the conservative side: it tightens avoidance
    rather than letting a greeting repeat itself.
    """

    strict_cutoff = float(observed_at) - max(0.0, float(strict_seconds))
    strict: list[StartupGreetingRecord] = []
    earlier: list[StartupGreetingRecord] = []
    for record in recall_records:
        (strict if record.ts > strict_cutoff else earlier).append(record)
    return strict, earlier


def _startup_greeting_burst_age(
    recent_records: list[StartupGreetingRecord],
    *,
    observed_at: float,
    last_user_engagement_at: float | None = None,
) -> float | None:
    if not recent_records:
        return None
    if (
        last_user_engagement_at is not None
        and float(last_user_engagement_at) > float(recent_records[0].ts)
    ):
        return None
    age = float(observed_at) - float(recent_records[0].ts)
    if 0.0 <= age <= _STARTUP_GREETING_BURST_SECONDS:
        return age
    return None


def _select_startup_greeting_variant(
    recent_records: list[StartupGreetingRecord],
    *,
    has_followup: bool,
) -> str:
    """Choose a different opening angle before the one existing LLM call.

    ``recent_records`` is the strict (1-day) layer, not the full 3-day recall.
    Rotating against three days would exhaust every angle after the first day
    and collapse this back into the plain round-robin fallback below.
    """

    recent_variants = [record.variant_key for record in recent_records]
    if has_followup and _STARTUP_GREETING_VARIANT_MEMORY not in recent_variants:
        return _STARTUP_GREETING_VARIANT_MEMORY

    for variant in _STARTUP_GREETING_GENERIC_VARIANTS:
        if variant not in recent_variants:
            return variant

    most_recent_generic = next(
        (
            variant
            for variant in recent_variants
            if variant in _STARTUP_GREETING_GENERIC_VARIANTS
        ),
        None,
    )
    if most_recent_generic is None:
        return _STARTUP_GREETING_GENERIC_VARIANTS[0]
    current_index = _STARTUP_GREETING_GENERIC_VARIANTS.index(most_recent_generic)
    return _STARTUP_GREETING_GENERIC_VARIANTS[
        (current_index + 1) % len(_STARTUP_GREETING_GENERIC_VARIANTS)
    ]


def _select_startup_followup(
    raw_topics,
    *,
    recently_used_topic_keys: set[str],
) -> tuple[str, str] | None:
    """Select one bounded reflection cue unused across the 3-day recall window.

    Topic reuse is judged on the wider window than opening angles: hearing the
    same remembered topic raised again reads as far more repetitive than
    reusing a generic opening shape.
    """

    if not isinstance(raw_topics, list):
        return None
    from main_logic.topic.common import clean_text

    for topic in raw_topics[:10]:
        if not isinstance(topic, dict):
            continue
        if any(bool(topic.get(flag)) for flag in ("sensitive", "private", "rejected")):
            continue
        topic_key = str(topic.get("id") or "").strip()[:160]
        if not topic_key or topic_key in recently_used_topic_keys:
            continue
        # This runs on the event loop, so use the deterministic character bound
        # instead of synchronously cold-starting the tokenizer here.
        text = clean_text(topic.get("text"), limit=120)
        if text:
            return topic_key, text
    return None
