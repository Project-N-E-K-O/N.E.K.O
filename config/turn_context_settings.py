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
"""本轮附加上下文的总等待预算（秒）。
- 从用户消息到达 Main 时开始计时，知识/语料与记忆两个提供者共用；到点还没
  回来的提供者结果直接丢弃，不拖首字。
- 与回合准备（打断、清 TTS、凝神判断、取 agent 回调）并行，大部分时间被
  准备工作盖住。"""

TURN_CONTEXT_MIN_REMAINING_SECONDS = 0.3
"""开工时至少留给提供者的时间（秒）。
- 会话还在启动（新对话第一句排在启动后补发）或 realtime 切离线交接时，消息到达
  后可能已过去 1 秒以上；不设下限的话这一轮三类检索一个都不跑，而开场白恰恰
  最需要记忆召回。下限让这种回合最多在启动/交接之后再多等这么久。"""

TURN_CONTEXT_QUERY_MAX_CHARS = 500
"""拿去检索的用户原话最多取前多少字（与知识工具的 query 上限一致）。"""

# ---- Memory Server: what one turn may carry ----
TURN_CONTEXT_MAX_ITEMS_PER_KIND = 2
"""每类（知识 / 语料 / 记忆）每轮最多注入几条。不另设总上限。"""

TURN_CONTEXT_ITEM_MAX_TOKENS = 400
"""单条注入内容的 token 上限（与记忆召回单条上限 RECALL_RENDER_ENTRY_MAX_TOKENS 对齐）。"""

TURN_CONTEXT_RESIDENT_KNOWLEDGE_MAX_TOKENS = 2400
"""会话内常驻知识的 token 预算（独立于会话热切换的历史门槛）。
- 满了就删最老的一块，给新命中的知识腾位置；被删那块之后的请求前缀变化一次。"""

# ---- Memory Server: passive hit rules ----
TURN_CONTEXT_NAME_MIN_CJK_CHARS = 2
"""「点名」：汉字/假名/韩文名称至少几个字，出现在句中就算点名；更短的须整句等于它。"""

TURN_CONTEXT_NAME_MIN_LATIN_CHARS = 3
"""「点名」：拉丁字母名称至少几个字母（且前后是词边界）；更短的须整句等于它。"""

TURN_CONTEXT_RARE_TERM_MIN_CJK_CHARS = 3
TURN_CONTEXT_RARE_TERM_MIN_LATIN_CHARS = 4
TURN_CONTEXT_RARE_TERM_MAX_DOCS = 2
"""记忆的「罕见专有词」精确路：用户这句话与某条记忆共有一个词，该词至少
3 个汉字或 4 个字母，并且整个召回池里最多 2 条记忆含有它。"""

TURN_CONTEXT_MEMORY_BM25_MIN_COVERAGE = 0.8
"""语义模型不可用时，记忆退到关键词召回的覆盖率门槛：用户这句话里的实词
至少有这么大比例出现在该条记忆里（并且还要含一个罕见词）。"""

# 门槛自动校准：内置闲聊探针句在当前语义模型下对资料打分，得到「无关闲聊的
# 分数分布」。门槛 = 背景高分位 + k × 背景离散度（高分位 − 中位数），再与工具
# 门槛取大。换模型 / 换维度会自动重测。
TURN_CONTEXT_CALIBRATION_HIGH_QUANTILE = 0.99
TURN_CONTEXT_CALIBRATION_BASE_QUANTILE = 0.5
TURN_CONTEXT_CALIBRATION_MIN_SAMPLES = 32
"""背景样本少于这个数时不信校准结果，退回保底门槛。"""

TURN_CONTEXT_CALIBRATION_BATCH = 8
TURN_CONTEXT_CALIBRATION_BATCH_PAUSE_SECONDS = 0.05
"""探针句分小批嵌入、批间稍停，不一次占满和记忆召回共用的向量模型。"""

TURN_CONTEXT_DUAL_STEPS = 0.5
"""两路都中（BM25 + 语义）的门槛：背景高分位之上几个离散度。"""

TURN_CONTEXT_SEMANTIC_STEPS = 1.0
"""只中语义的门槛：背景高分位之上几个离散度。"""

TURN_CONTEXT_CORPUS_EXTRA_STEPS = 0.25
TURN_CONTEXT_MEMORY_EXTRA_STEPS = 0.5
"""语料、记忆在上面两档的基础上再各高多少个离散度。"""

TURN_CONTEXT_LEAD_STEPS = 0.25
"""只中语义时，收下的最后一名须领先下一名多少个离散度。"""

TURN_CONTEXT_KNOWLEDGE_FLOOR = 0.5
TURN_CONTEXT_MEMORY_FLOOR = 0.3
"""保底：被动门槛永远不低于对应工具的现有门槛（知识 SEMANTIC_THRESHOLD、
记忆 HYBRID_RECALL_COSINE_THRESHOLD）。"""

TURN_CONTEXT_LEAD_FLOOR = 0.02
"""领先幅度的保底值（校准不可用时使用）。"""

# ---- recall_memory tool ----
RECALL_MEMORY_TOOL_TIMEOUT_SECONDS = 2.5
"""recall_memory 工具等 Memory Server 的总超时（秒）。"""

RECALL_MEMORY_TOOL_BUDGET_MS = 2000
"""recall_memory 交给服务端的预算：语义检索在这之前没算完就只用关键词结果，
免得一超时就整个回"没找到"。"""

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
"""闲聊探针句（8 种语言各 16 句）：只用来测「无关闲聊在当前语义模型下能拿多少分」，
不会出现在任何提示里。"""
