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

"""Lexical signal tables for Focus mode ("凝神").

Focus is the signal-triggered, user-invisible "thinking-on" turn that
delivers the product thesis's 10% "神明降临" moment (see
``docs/design/focus-truename-mode.md``). One of its cheapest Layer-1
signals is a substring scan of the user's message for **emotional
vulnerability** cues — fatigue, loneliness, feeling overwhelmed, the urge
to give up.

Distinct from ``prompts_directives.NEGATIVE_KEYWORDS_I18N``
--------------------------------------------------------
That table is the *avoidance / annoyance* family ("别说了 / 换话题 /
stop talking about") — it means "the user wants to end THIS topic" and
feeds the ban-list / disputation path. It must NOT pull the companion
into Focus: a user changing the subject does not want gravity, they want
to move on. The vulnerability family below is the opposite — it means
"the user just opened a door"; that is exactly when she should think
harder. The two tables overlap only at the edges (心烦 / 受不了 / 撑不住),
which is acceptable: those genuinely warrant gentler attention either way.

Convention (mirrors prompts_directives)
---------------------------------------
- All locale tables run in parallel of language detection (mixed-language
  speech is common); the scan only normalizes the region suffix and
  falls back to ``zh`` for unknown codes.
- Substring match, case-insensitive. Bias toward false positives is fine
  here — over-triggering Focus just spends some extra thinking tokens on a
  turn that turned out light; the *accumulator knobs* (``FOCUS_CHARGE_*``)
  and the keyword *weight* (``FOCUS_SIGNAL_WEIGHTS``) are where rollout
  tuning lives, not this lexicon.
- Locale keys match the short-code scheme of ``NEGATIVE_KEYWORDS_I18N``
  (zh / en / ja / ko / ru / es / pt); ``zh`` is shared by zh-CN / zh-TW.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations


# Emotional-vulnerability cues. Graded: ``scan_vulnerability_keywords``
# returns the count of distinct matched phrases so the scorer can read a
# rough intensity (one weary "累" vs. a pile-up of "撑不住 / 一个人 / 没意思").
FOCUS_VULNERABILITY_KEYWORDS_I18N: dict[str, frozenset[str]] = {
    "zh": frozenset(
        [
            # 疲惫 / 透支
            "好累", "太累", "累死", "累了", "好疲惫", "撑不住", "扛不住",
            "撑不下去", "顶不住", "精疲力尽", "身心俱疲",
            # 低落 / 难受
            "难受", "好难受", "不开心", "很难过", "好难过", "想哭", "难过",
            "提不起劲", "没动力", "心里堵", "心里难受", "情绪低落", "好低落",
            # 孤独 / 无依
            "一个人", "好孤独", "孤独", "没人懂", "没人理解", "孤单", "好孤单",
            "没人陪", "没人在乎",
            # 倦怠 / 空洞
            "没意思", "好无聊", "没劲", "提不起兴趣", "什么都不想", "好空虚",
            "好迷茫", "迷茫", "麻木",
            # 压力 / 自我怀疑
            "压力好大", "压力太大", "好大压力", "好焦虑", "焦虑", "好慌",
            "撑着", "好委屈", "委屈", "好绝望", "绝望", "坚持不下去",
            "不想努力了", "想放弃", "快崩溃", "要崩溃",
        ]
    ),
    "en": frozenset(
        [
            # Fatigue / depletion
            "so tired", "exhausted", "worn out", "burnt out", "burned out",
            "drained", "can't keep up", "can't go on", "can't take it",
            "running on empty",
            # Low mood / hurting
            "feel down", "feeling down", "so sad", "really sad", "want to cry",
            "feel like crying", "heavy hearted", "no motivation",
            "can't be bothered", "feeling low",
            # Loneliness
            "so alone", "all alone", "feel alone", "lonely", "so lonely",
            "no one understands", "no one gets me", "nobody cares",
            # Emptiness / aimless
            "pointless", "what's the point", "so bored", "no energy",
            "feel empty", "so lost", "feeling lost", "numb",
            # Pressure / despair
            "so stressed", "too much pressure", "so anxious", "anxious",
            "overwhelmed", "want to give up", "about to break down",
            "can't do this anymore", "falling apart", "hopeless",
        ]
    ),
    "ja": frozenset(
        [
            # 疲労
            "疲れた", "しんどい", "もう限界", "へとへと", "つらい", "もう無理",
            "やってられない",
            # 落ち込み
            "悲しい", "泣きたい", "落ち込", "やる気が出ない", "気分が沈",
            "元気が出ない",
            # 孤独
            "一人ぼっち", "ひとりぼっち", "寂しい", "さびしい", "誰もわかって",
            "孤独",
            # 空虚 / 倦怠
            "つまらない", "むなしい", "虚しい", "退屈", "何もしたくない",
            "迷ってる",
            # 圧力 / 絶望
            "プレッシャー", "不安", "焦ってる", "もうダメ", "崩れそう",
            "諦めたい", "頑張れない",
        ]
    ),
    "ko": frozenset(
        [
            # 피로
            "너무 피곤", "지쳤어", "지친다", "힘들어", "못 버티", "한계야",
            "탈진",
            # 우울
            "슬퍼", "울고 싶", "우울", "의욕이 없", "기운이 없", "마음이 무거",
            # 외로움
            "혼자야", "외로워", "외롭", "아무도 몰라", "아무도 없",
            # 공허 / 권태
            "재미없", "공허", "지루", "아무것도 하기 싫", "막막",
            # 압박 / 절망
            "스트레스", "불안", "초조", "무너질 것 같", "포기하고 싶",
            "버틸 수 없", "절망",
        ]
    ),
    "ru": frozenset(
        [
            # Усталость
            "так устал", "устала", "вымотан", "вымоталась", "нет сил",
            "больше не могу", "выгорел", "выгорела",
            # Подавленность
            "грустно", "хочется плакать", "тоскливо", "нет настроения",
            "тяжело на душе", "подавлен",
            # Одиночество
            "совсем один", "совсем одна", "одиноко", "никто не понимает",
            "никому не нужен", "никому не нужна",
            # Пустота / апатия
            "бессмысленно", "какой смысл", "скучно", "пусто внутри",
            "потерян", "потеряна",
            # Давление / отчаяние
            "столько стресса", "тревожно", "не справляюсь", "хочу сдаться",
            "вот-вот сломаюсь", "безнадёжно", "опускаются руки",
        ]
    ),
    "es": frozenset(
        [
            # Cansancio
            "muy cansado", "muy cansada", "agotado", "agotada", "no puedo más",
            "quemado", "sin energía",
            # Tristeza
            "triste", "ganas de llorar", "ánimo por los suelos", "desanimado",
            "desanimada", "sin motivación",
            # Soledad
            "muy solo", "muy sola", "me siento solo", "me siento sola",
            "nadie me entiende", "a nadie le importa",
            # Vacío / apatía
            "sin sentido", "qué sentido tiene", "aburrido", "vacío por dentro",
            "perdido", "perdida",
            # Presión / desesperación
            "mucho estrés", "ansioso", "ansiosa", "abrumado", "abrumada",
            "quiero rendirme", "a punto de derrumbarme", "sin esperanza",
        ]
    ),
    "pt": frozenset(
        [
            # Cansaço
            "muito cansado", "muito cansada", "exausto", "exausta",
            "não aguento mais", "esgotado", "sem energia",
            # Tristeza
            "triste", "vontade de chorar", "pra baixo", "desanimado",
            "desanimada", "sem motivação",
            # Solidão
            "muito sozinho", "muito sozinha", "me sinto sozinho",
            "me sinto sozinha", "ninguém me entende", "ninguém se importa",
            # Vazio / apatia
            "sem sentido", "qual o sentido", "entediado", "vazio por dentro",
            "perdido", "perdida",
            # Pressão / desespero
            "muito estresse", "ansioso", "ansiosa", "sobrecarregado",
            "sobrecarregada", "quero desistir", "prestes a desabar",
            "sem esperança",
        ]
    ),
}


# Explicit topic-switch openers. A clear subject change ("对了… / 话说回来 /
# by the way / ところで") ends the current emotional episode, so Focus
# exits immediately regardless of score (the user has moved on). Matched
# at the START of the message only — a marker buried mid-sentence is far
# more likely to be incidental than a genuine pivot. Conservative by
# design: a missed pivot just lets hysteresis/hard-cap end Focus a turn or
# two later; a false pivot would abort a live emotional moment, which is
# the worse error.
FOCUS_TOPIC_SWITCH_MARKERS_I18N: dict[str, frozenset[str]] = {
    "zh": frozenset(
        ["对了", "话说", "话说回来", "另外", "顺便", "顺便问", "换个话题",
         "说起来", "对了对了", "诶对了", "突然想到", "顺便说"]
    ),
    "en": frozenset(
        ["by the way", "btw", "anyway", "anyways", "on another note",
         "changing the subject", "different topic", "oh right", "speaking of",
         "unrelated", "side note"]
    ),
    "ja": frozenset(
        ["ところで", "そういえば", "話は変わるけど", "話変わるけど", "ちなみに",
         "それはそうと", "余談だけど"]
    ),
    "ko": frozenset(
        ["그건 그렇고", "그나저나", "참", "아 맞다", "다른 얘기지만", "그런데 말이야",
         "근데 있잖아"]
    ),
    "ru": frozenset(
        ["кстати", "между прочим", "к слову", "да, и ещё", "сменим тему",
         "другая тема", "ах да"]
    ),
    "es": frozenset(
        ["por cierto", "a todo esto", "cambiando de tema", "otra cosa",
         "oye, una cosa", "hablando de otra cosa", "ah, por cierto"]
    ),
    "pt": frozenset(
        ["a propósito", "aliás", "mudando de assunto", "outra coisa",
         "falando nisso", "ah, e", "por sinal"]
    ),
}


def scan_vulnerability_keywords(message: str) -> int:
    """Count distinct emotional-vulnerability phrases in *message*, across ALL locales.

    Returns the number of distinct phrases (case-insensitive substring
    match) found in *any* locale table — not just the UI language's. Mixed-
    language speech is common (a Chinese user typing "so tired", or English
    interface with Chinese venting), and the convention documented above is
    to run every locale table in parallel of language detection. 0 means no
    cue. The Focus scorer maps the count to a graded signal (one cue is a
    nudge; several stacked cues are a strong pull) — see
    ``FOCUS_SIGNAL_WEIGHTS`` / ``FOCUS_KEYWORD_SATURATION``.

    Distinct by phrase text, so the same phrase living in two locale tables
    counts once.
    """
    if not message:
        return 0
    lower = message.lower()
    matched: set[str] = set()
    for kws in FOCUS_VULNERABILITY_KEYWORDS_I18N.values():
        for kw in kws:
            kwl = kw.lower()
            if kwl in lower:
                matched.add(kwl)
    # De-nest: a single cue often matches both a base phrase and an
    # intensified form that contains it (e.g. "好难受" matches both "难受"
    # and "好难受"; "so lonely" matches "lonely" and "so lonely"). Counting
    # both lets one cue saturate FOCUS_KEYWORD_SATURATION, so drop any
    # matched phrase that is a substring of another matched phrase — keep
    # only maximal hits.
    maximal = [p for p in matched if not any(p != q and p in q for q in matched)]
    return len(maximal)


def detect_topic_switch(message: str) -> bool:
    """True if *message* opens with an explicit topic-switch marker, ANY locale.

    Match is anchored to the message start (after stripping leading
    whitespace / punctuation) — a marker mid-sentence is usually
    incidental, and the start-anchor keeps cross-locale scanning low-risk
    (markers are distinctive multi-char phrases). Language-agnostic for the
    same mixed-language reason as ``scan_vulnerability_keywords``: a
    bilingual user may pivot in either tongue regardless of the UI language.
    """
    if not message:
        return False
    head = message.strip().lstrip("，,。.！!？?、…—-—— \t").lower()
    if not head:
        return False
    for markers in FOCUS_TOPIC_SWITCH_MARKERS_I18N.values():
        if any(head.startswith(m.lower()) for m in markers):
            return True
    return False


__all__ = [
    "FOCUS_VULNERABILITY_KEYWORDS_I18N",
    "FOCUS_TOPIC_SWITCH_MARKERS_I18N",
    "scan_vulnerability_keywords",
    "detect_topic_switch",
]
