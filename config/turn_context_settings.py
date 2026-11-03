# -*- coding: utf-8 -*-
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

"""Passive turn context: retrieval run for the model before a typed reply.

Before a text reply (typed, or transcribed by the independent ASR), Main asks
the Memory Server for knowledge, corpus material and memories related to what
the user just said, and hands the hits to this reply's requests only:

* knowledge stays in the session's requests (never in its history) until the
  session is renewed, so a card is not re-sent while the model still sees it;
* corpus material and memories ride this turn's requests alone and are gone
  from the next turn's.

Nothing here is saved to history, memory, chat records or the plugin bus.
"""

# ---- Main: when and how long ----
TURN_CONTEXT_BUDGET_SECONDS = 1.0
"""Total extra-context wait budget for this turn, seconds.
- Start when user messages arrive at Main; knowledge/corpus and memory providers share it.
  Discard providers not finished by deadline, without delaying the first token.
- Run alongside turn preparation (interrupt, TTS cleanup, focus decisions, agent callbacks),
  which hides most waiting time."""

TURN_CONTEXT_MIN_REMAINING_SECONDS = 0.3
"""Minimum provider time reserved when work starts, seconds.
- Session startup (queued first utterances) or realtime-to-offline handoff can consume over
  1 second after arrival; without a floor, all three retrieval types skip the turn, although
  opening lines especially need memories. This adds at most the floor after startup/handoff."""

TURN_CONTEXT_QUERY_MAX_CHARS = 500
"""Maximum leading characters of original user text used for retrieval, matching knowledge-tool query caps."""

# ---- Memory Server: what one turn may carry ----
TURN_CONTEXT_MAX_ITEMS_PER_KIND = 2
"""Maximum injected entries per category (knowledge /corpus /memory) per turn; no additional total cap."""

TURN_CONTEXT_ITEM_MAX_TOKENS = 400
"""Token cap per injected entry, matching memory recall's RECALL_RENDER_ENTRY_MAX_TOKENS."""

TURN_CONTEXT_RESIDENT_KNOWLEDGE_MAX_TOKENS = 2400
"""Resident in-session knowledge-token budget, independent of hot-switch history thresholds.
- When full, evict the oldest block for new knowledge; subsequent request prefixes change once for the removed block."""

# ---- Memory Server: passive hit rules ----
TURN_CONTEXT_NAME_MIN_CJK_CHARS = 2
"""Named mentions: minimum CJK/kana/Hangul name length matching within sentences; shorter names require whole-sentence equality."""

TURN_CONTEXT_NAME_MIN_LATIN_CHARS = 3
"""Named mentions: minimum Latin-name letters with word boundaries on both sides; shorter names require whole-sentence equality."""

TURN_CONTEXT_RARE_TERM_MIN_CJK_CHARS = 3
TURN_CONTEXT_RARE_TERM_MIN_LATIN_CHARS = 4
TURN_CONTEXT_RARE_TERM_MAX_DOCS = 2
"""Exact rare-proper-term memory route: the user utterance and memory share a term of at least
3 Han characters or 4 letters, present in at most 2 memories across the recall pool."""

TURN_CONTEXT_MEMORY_BM25_MIN_COVERAGE = 0.8
"""Keyword fallback coverage when semantic models are unavailable: at least this proportion
of the user's content words must appear in a memory, which must also include a rare term."""

# Automatic threshold calibration: built-in small-talk probes score the material using the current semantic model to obtain
# an irrelevant-small-talk score distribution. Threshold = upper background quantile + k × background spread (upper quantile − median),
# then take the maximum with the tool threshold. Changing models/dimensions automatically recalibrates.
TURN_CONTEXT_CALIBRATION_HIGH_QUANTILE = 0.99
TURN_CONTEXT_CALIBRATION_BASE_QUANTILE = 0.5
TURN_CONTEXT_CALIBRATION_MIN_SAMPLES = 32
"""Below this background-sample count, distrust calibration and use fallback thresholds."""

TURN_CONTEXT_CALIBRATION_BATCH = 8
TURN_CONTEXT_CALIBRATION_BATCH_PAUSE_SECONDS = 0.05
"""Embed probe sentences in small paused batches, without monopolizing the vector model shared with memory recall."""

TURN_CONTEXT_DUAL_STEPS = 0.5
"""Threshold for matching both BM25 and semantics: dispersion units above the background high quantile."""

TURN_CONTEXT_SEMANTIC_STEPS = 1.0
"""Semantic-only threshold: dispersion units above the background high quantile."""

TURN_CONTEXT_CORPUS_EXTRA_STEPS = 0.25
TURN_CONTEXT_MEMORY_EXTRA_STEPS = 0.5
"""Additional dispersion units for corpus and memory above the two threshold tiers."""

TURN_CONTEXT_LEAD_STEPS = 0.25
"""Semantic-only admission: required dispersion-unit lead of the last accepted candidate over the next."""

TURN_CONTEXT_KNOWLEDGE_FLOOR = 0.5
TURN_CONTEXT_MEMORY_FLOOR = 0.3
"""Fallback: passive thresholds never fall below corresponding tools' existing thresholds (knowledge SEMANTIC_THRESHOLD,
memory HYBRID_RECALL_COSINE_THRESHOLD)."""

TURN_CONTEXT_LEAD_FLOOR = 0.02
"""Fallback lead margin when calibration is unavailable."""

# ---- recall_memory tool ----
RECALL_MEMORY_TOOL_TIMEOUT_SECONDS = 2.5
"""Total recall_memory tool timeout awaiting Memory Server, seconds."""

RECALL_MEMORY_TOOL_BUDGET_MS = 2000
"""Server-side recall_memory budget: if semantic retrieval misses it, use keyword results only,
preventing a timeout from turning the entire response into not found."""

TURN_CONTEXT_CALIBRATION_PROBES = (
    # zh
    "今天好累啊", "你吃饭了吗", "晚上好", "我刚下班回来", "外面在下雨",
    "有点困了", "周末想睡个懒觉", "今天天气还不错", "我去洗个澡", "嗯嗯好的",
    "哈哈哈哈", "最近有点忙", "早上起不来", "等我一下", "我回来啦", "晚安",
    # zh-TW
    "今天好累喔", "你吃飯了嗎", "我剛下班", "外面在下雨耶", "有點想睡了",
    "週末想多睡一點", "等我一下下", "我回來了", "最近好忙", "晚安囉",
    "早安", "今天天氣不錯", "我先去洗澡", "好啊沒問題", "哈哈好好笑", "肚子餓了",
    # en
    "I'm so tired today", "did you eat yet", "good evening", "just got home from work",
    "it's raining outside", "I'm getting sleepy", "want to sleep in this weekend",
    "nice weather today", "going to take a shower", "okay sounds good", "haha",
    "been a bit busy lately", "can't get up in the morning", "give me a sec",
    "I'm back", "good night",
    # ja
    "今日は疲れた", "ご飯食べた？", "こんばんは", "仕事から帰ってきた", "外は雨だよ",
    "ちょっと眠い", "週末は寝坊したい", "今日はいい天気", "お風呂入ってくる", "うんうん",
    "あはは", "最近ちょっと忙しい", "朝起きられない", "ちょっと待って", "ただいま", "おやすみ",
    # ko
    "오늘 너무 피곤해", "밥 먹었어?", "좋은 저녁", "방금 퇴근했어", "밖에 비 와",
    "좀 졸려", "주말엔 늦잠 자고 싶어", "오늘 날씨 좋다", "샤워하고 올게", "응 좋아",
    "하하", "요즘 좀 바빠", "아침에 못 일어나겠어", "잠깐만", "나 왔어", "잘 자",
    # ru
    "я сегодня так устал", "ты уже поел?", "добрый вечер", "только что пришёл с работы",
    "на улице дождь", "что-то спать хочется", "хочу выспаться в выходные",
    "сегодня хорошая погода", "пойду в душ", "ладно, хорошо", "ха-ха",
    "в последнее время занят", "не могу встать утром", "подожди секунду", "я вернулся",
    "спокойной ночи",
    # es
    "estoy muy cansado hoy", "¿ya comiste?", "buenas noches", "acabo de llegar del trabajo",
    "está lloviendo afuera", "tengo un poco de sueño", "quiero dormir hasta tarde el finde",
    "hoy hace buen tiempo", "voy a ducharme", "vale, perfecto", "jaja",
    "últimamente estoy ocupado", "no puedo levantarme por la mañana", "espera un momento",
    "ya volví", "que descanses",
    # pt
    "estou tão cansado hoje", "você já comeu?", "boa noite", "acabei de chegar do trabalho",
    "está chovendo lá fora", "estou com um pouco de sono", "quero dormir até tarde no fim de semana",
    "o tempo está bom hoje", "vou tomar banho", "beleza, tudo bem", "haha",
    "ando meio ocupado", "não consigo acordar de manhã", "espera um pouco", "voltei",
    "durma bem",
)
"""Small-talk probes (16 sentences per 8 languages) measure unrelated-chat scores under the current semantic model;
they never enter any prompts."""
