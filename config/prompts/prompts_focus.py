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
            "好累", "太累", "累死", "累了", "好疲惫", "疲惫", "撑不住", "扛不住",
            "撑不下去", "撑不住了", "顶不住", "精疲力尽", "身心俱疲", "好疲倦",
            # 低落 / 难受 / 痛苦
            "难受", "好难受", "不开心", "很难过", "好难过", "难过", "想哭",
            "好想哭", "伤心", "好伤心", "痛苦", "好痛苦", "心痛", "心好痛",
            "心碎", "压抑", "好压抑", "憋屈", "心塞", "心累", "好心累", "难熬",
            "煎熬", "提不起劲", "没动力", "心里堵", "心里难受", "情绪低落",
            "好低落", "低落", "丧了", "好丧", "丧丧的", "emo了", "好emo", "emo中",
            # 愤怒 / 烦躁
            "好烦", "烦死", "烦死了", "好烦躁", "烦躁", "心烦", "生气", "好生气",
            "气死", "气死了", "气炸", "火大", "好火大", "暴躁", "抓狂", "受够了",
            "忍不了", "忍无可忍", "恼火",
            # 孤独 / 无依
            "一个人", "好孤独", "孤独", "没人懂", "没人理解", "孤单", "好孤单",
            "没人陪", "没人在乎", "好寂寞", "寂寞",
            # 倦怠 / 空洞
            "没意思", "好无聊", "没劲", "提不起兴趣", "什么都不想", "好空虚",
            "空虚", "好迷茫", "迷茫", "麻木",
            # 压力 / 焦虑
            "压力好大", "压力太大", "好大压力", "好焦虑", "焦虑", "好慌", "心慌",
            "喘不过气", "透不过气", "好委屈", "委屈",
            # 绝望 / 自我怀疑
            "好绝望", "绝望", "坚持不下去", "不想努力了", "想放弃", "快崩溃",
            "要崩溃", "崩溃", "没希望", "好无助", "无助", "活不下去", "撑不住了",
            # 求安慰 / 渴望被安抚
            "安慰",
        ]
    ),
    "en": frozenset(
        [
            # Fatigue / depletion
            "so tired", "so exhausted", "exhausted", "worn out", "worn down",
            "burnt out", "burned out", "burning out", "drained", "so drained",
            "can't keep up", "can't go on", "can't take it", "can't take this",
            "running on empty", "no energy left", "dead tired", "wiped out",
            # Low mood / hurting
            "feel down", "feeling down", "so sad", "really sad", "want to cry",
            "feel like crying", "heavy hearted", "no motivation",
            "can't be bothered", "feeling low", "so miserable", "feeling awful",
            "really hurting",
            # Loneliness
            "so alone", "all alone", "feel alone", "feel so alone", "lonely",
            "so lonely", "no one understands", "no one gets me", "nobody cares",
            "nobody understands", "no one to talk to",
            # Emptiness / aimless
            "pointless", "what's the point", "so bored", "no energy",
            "feel empty", "so empty", "so lost", "feeling lost", "feel numb",
            "nothing matters", "don't care anymore",
            # Pressure / anxiety
            "so stressed", "too much pressure", "under so much pressure",
            "so anxious", "really anxious", "overwhelmed", "so overwhelmed",
            "panicking", "freaking out", "can't breathe",
            # Despair / giving up
            "want to give up", "about to break down", "can't do this anymore",
            "falling apart", "hopeless", "so hopeless", "can't go on anymore",
            "no way out", "want it to stop",
            # Anger / irritation
            "so angry", "pissed off", "so pissed", "fed up", "so fed up",
            "so annoyed", "so irritated", "irritated", "furious", "can't stand it",
            "had enough", "sick of it", "sick of this", "losing my temper",
            # Pain / hurt
            "hurts so much", "heartbroken", "in so much pain", "so upset",
            # Seeking comfort
            "comfort me", "need comfort",
        ]
    ),
    "ja": frozenset(
        [
            # 疲労
            "疲れた", "疲れすぎ", "しんどい", "もう限界", "限界かも", "へとへと",
            "くたくた", "つらい", "つらすぎ", "もう無理", "やってられない",
            "もうへとへと", "気力がない", "ぐったり",
            # 落ち込み
            "悲しい", "悲しすぎ", "泣きたい", "泣きそう", "落ち込", "やる気が出ない",
            "気分が沈", "元気が出ない", "しんどすぎ", "へこんでる", "気分が重い",
            # 孤独
            "一人ぼっち", "ひとりぼっち", "寂しい", "さびしい", "誰もわかって",
            "孤独", "独りぼっち", "誰も分かって", "話す相手がいない",
            # 空虚 / 倦怠
            "つまらない", "むなしい", "虚しい", "退屈", "何もしたくない",
            "迷ってる", "何もする気が", "心が空っぽ", "どうでもいい",
            # 圧力 / 不安
            "プレッシャー", "不安", "焦ってる", "焦って", "息が詰まる",
            "押しつぶされそう", "パニック", "気が休まらない",
            # 絶望
            "もうダメ", "崩れそう", "諦めたい", "頑張れない", "もう頑張れない",
            "立ち直れない", "どうしようもない", "消えてしまいたい",
            # 怒り / イライラ
            "ムカつく", "イライラ", "腹立つ", "腹が立つ", "うざい", "ウザい",
            "もう嫌", "うんざり", "キレそう", "イラつく", "ムカムカ", "頭にくる",
            # 痛み
            "胸が痛い", "心が痛い", "苦しい", "苦しすぎ", "せつない", "胸が苦しい",
            # 慰めてほしい
            "慰めて",
        ]
    ),
    "ko": frozenset(
        [
            # 피로
            "너무 피곤", "너무 지쳐", "지쳤어", "지친다", "힘들어", "너무 힘들어",
            "못 버티", "버티기 힘들", "한계야", "탈진", "기진맥진", "진이 빠",
            "녹초",
            # 우울
            "슬퍼", "너무 슬퍼", "울고 싶", "울고 싶어", "우울", "의욕이 없",
            "기운이 없", "마음이 무거", "기분이 가라앉", "마음이 가라앉",
            # 외로움
            "혼자야", "외로워", "너무 외로워", "외롭", "아무도 몰라", "아무도 없",
            "혼자인 것 같", "아무도 날 몰라", "얘기할 사람이 없",
            # 공허 / 권태
            "재미없", "공허", "지루", "아무것도 하기 싫", "막막", "텅 빈 것 같",
            "다 부질없", "아무 의미 없",
            # 압박 / 불안
            "스트레스", "불안", "초조", "숨이 막혀", "압박감", "숨이 안 쉬",
            "공황",
            # 절망
            "무너질 것 같", "포기하고 싶", "버틸 수 없", "절망", "다 포기하고",
            "희망이 없", "더는 못 하겠",
            # 분노 / 짜증
            "짜증나", "너무 짜증", "짜증", "화나", "화가 치밀", "열받", "빡쳐",
            "지긋지긋", "못 참", "신경질", "화가 나", "분통",
            # 아픔
            "마음이 아파", "가슴이 아파", "괴로워", "괴롭", "마음이 너무 아파",
            "가슴이 미어",
            # 위로받고 싶다
            "위로해", "위로받고 싶",
        ]
    ),
    "ru": frozenset(
        [
            # Усталость
            "так устал", "очень устал", "устала", "вымотан", "вымоталась",
            "нет сил", "больше нет сил", "больше не могу", "выгорел", "выгорела",
            "сил совсем нет", "вымотался", "еле держусь",
            # Подавленность
            "грустно", "так грустно", "хочется плакать", "вот-вот заплачу",
            "тоскливо", "нет настроения", "тяжело на душе", "подавлен",
            "паршиво на душе", "руки опускаются",
            # Одиночество
            "совсем один", "совсем одна", "одиноко", "так одиноко",
            "никто не понимает", "никому не нужен", "никому не нужна",
            "не с кем поговорить", "меня никто не понимает",
            # Пустота / апатия
            "бессмысленно", "какой смысл", "скучно", "пусто внутри",
            "потерян", "потеряна", "всё надоело", "ничего не хочется",
            "всё равно на всё",
            # Давление / тревога
            "столько стресса", "тревожно", "так тревожно", "не справляюсь",
            "давит", "задыхаюсь", "паника", "накрывает",
            # Отчаяние
            "хочу сдаться", "вот-вот сломаюсь", "безнадёжно", "опускаются руки",
            "не вижу выхода", "хочу всё бросить", "больше не выдержу",
            # Гнев / раздражение
            "бесит", "так зол", "так зла", "достало", "всё достало",
            "раздражает", "сыт по горло", "выхожу из себя", "злюсь", "взбешён",
            "сил нет это терпеть",
            # Боль
            "так больно", "сердце болит", "душа болит", "невыносимо больно",
            # Хочется утешения
            "утешь меня", "нужно утешение",
        ]
    ),
    "es": frozenset(
        [
            # Cansancio
            "muy cansado", "muy cansada", "agotado", "agotada", "no puedo más",
            "quemado", "sin energía", "reventado", "reventada", "sin fuerzas",
            "hecho polvo", "hecha polvo", "no doy más",
            # Tristeza
            "triste", "muy triste", "ganas de llorar", "ánimo por los suelos",
            "desanimado", "desanimada", "sin motivación", "hecho una mierda",
            "me siento fatal", "se me cae el mundo",
            # Soledad
            "muy solo", "muy sola", "me siento solo", "me siento sola",
            "nadie me entiende", "a nadie le importa", "tan solo", "tan sola",
            "no tengo a nadie", "nadie me escucha",
            # Vacío / apatía
            "sin sentido", "qué sentido tiene", "aburrido", "vacío por dentro",
            "perdido", "perdida", "todo me da igual", "nada me importa",
            "no tengo ganas de nada",
            # Presión / ansiedad
            "mucho estrés", "ansioso", "ansiosa", "abrumado", "abrumada",
            "no puedo respirar", "me agobio", "ataque de ansiedad", "agobiado",
            "agobiada",
            # Desesperación
            "quiero rendirme", "a punto de derrumbarme", "sin esperanza",
            "no veo salida", "quiero tirar la toalla", "ya no aguanto más",
            "no puedo seguir",
            # Ira / irritación
            "muy enfadado", "muy enfadada", "harto", "harta", "muy harto",
            "muy harta", "me irrita", "furioso", "furiosa", "no aguanto",
            "estoy hasta las narices", "me saca de quicio", "cabreado",
            "cabreada",
            # Dolor
            "me duele mucho", "con el corazón roto", "destrozado", "destrozada",
            "me duele el alma",
            # Busco consuelo
            "consuélame", "necesito consuelo",
        ]
    ),
    "pt": frozenset(
        [
            # Cansaço
            "muito cansado", "muito cansada", "exausto", "exausta",
            "não aguento mais", "esgotado", "sem energia", "acabado", "acabada",
            "sem forças", "no limite", "morto de cansaço",
            # Tristeza
            "triste", "muito triste", "vontade de chorar", "pra baixo",
            "desanimado", "desanimada", "sem motivação", "me sinto péssimo",
            "me sinto horrível", "coração apertado",
            # Solidão
            "muito sozinho", "muito sozinha", "me sinto sozinho",
            "me sinto sozinha", "ninguém me entende", "ninguém se importa",
            "tão sozinho", "tão sozinha", "não tenho ninguém",
            "ninguém me escuta",
            # Vazio / apatia
            "sem sentido", "qual o sentido", "entediado", "vazio por dentro",
            "perdido", "perdida", "tanto faz", "nada me importa",
            "sem vontade de nada",
            # Pressão / ansiedade
            "muito estresse", "ansioso", "ansiosa", "sobrecarregado",
            "sobrecarregada", "não consigo respirar", "sufocado", "sufocada",
            "crise de ansiedade", "em pânico",
            # Desespero
            "quero desistir", "prestes a desabar", "sem esperança",
            "não vejo saída", "quero jogar tudo pro alto", "não consigo seguir",
            "no fundo do poço",
            # Raiva / irritação
            "com muita raiva", "puto", "puta da vida", "saco cheio",
            "de saco cheio", "me irrita", "furioso", "furiosa", "não aguento",
            "puto da vida", "estou de saco cheio", "tô puto",
            # Dor
            "dói muito", "de coração partido", "arrasado", "arrasada",
            "dói demais",
            # Em busca de conforto
            "me consola", "preciso de consolo",
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
