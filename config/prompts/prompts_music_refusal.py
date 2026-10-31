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

"""
Phrases where the user verbally turns down proactive music recommendations.

A hit puts the character's music channel into the same cooldown that two
consecutive card skips trigger (``main_logic.proactive_chat.music_refusal``).
Unlike ``prompts_directives`` this is a channel switch, not a topic ban: it
captures no term and never reaches the system prompt.

Every locale runs on every message (mixed-language speech is common). Patterns
require an explicit music object ("song" / "music" / ...) next to the refusal
verb, because an object-less "stop it" may be about anything. A false positive
costs twenty minutes without music suggestions; a miss means she keeps playing
songs after being told not to, so the lists lean towards recall.

All patterns are linear: no nested unbounded quantifiers, and the scanned text
is capped at ``MUSIC_REFUSAL_SCAN_MAX_CHARS``.
"""

import re
from typing import Pattern

MUSIC_REFUSAL_SCAN_MAX_CHARS = 300

# zh / zh-TW share one table shape; traditional forms differ only where the
# glyph does. 别/別 skips compounds (个别推荐 / 区别), and the negation skips
# the question form 要不要放歌 (she is asking, not being told).
_ZH_BIE = r"(?<![特性区级个分识告辨差类])别"
_ZH_TW_BIE = r"(?<![特性區級個分識告辨差類])別"
_NOT_ASKED = r"(?<!要)"

MUSIC_REFUSAL_PATTERNS_I18N: dict[str, tuple[str, ...]] = {
    "zh": (
        # 别再给我放歌 / 不要推荐音乐了 / 甭推歌
        _NOT_ASKED + rf"(?:{_ZH_BIE}|不要|不用|甭)(?:再|老|总|一直)?(?:给我|帮我|为我)?(?:放|播|推荐|推|点)(?:歌|音乐|曲子|bgm)",
        # 不想听歌 / 没心情听音乐
        _NOT_ASKED + r"(?:不想|不要|没心情)听(?:歌|音乐)",
        # 我听自己的就行 / 我在听自己的歌
        r"我(?:在|正在)?听(?:我)?自己的",
        # 歌就别放了 / 音乐不要再推了
        rf"(?:歌|音乐)(?:就|也)?(?:{_ZH_BIE}|不要|不用)(?:再)?(?:放|播|推荐|推)",
    ),
    "zh-TW": (
        _NOT_ASKED + rf"(?:{_ZH_TW_BIE}|不要|不用|甭)(?:再|老|總|一直)?(?:給我|幫我|為我)?(?:放|播|推薦|推|點)(?:歌|音樂|曲子|bgm)",
        _NOT_ASKED + r"(?:不想|不要|沒心情)聽(?:歌|音樂)",
        r"我(?:在|正在)?聽(?:我)?自己的",
        rf"(?:歌|音樂)(?:就|也)?(?:{_ZH_TW_BIE}|不要|不用)(?:再)?(?:放|播|推薦|推)",
    ),
    "en": (
        # stop playing music / don't recommend me any more songs.
        # "don't (ever) stop playing music" is blanked out first, see _NEGATED_STOP.
        r"\b(?:stop|quit|don'?t|do not|no more)\s+(?!stop\b|quit\b)(?:\w+\s+){0,2}?"
        r"(?:play(?:ing)?|recommend(?:ing)?|suggest(?:ing)?|send(?:ing)?|put(?:ting)? on)\s+"
        r"(?:\w+\s+){0,3}?(?:songs?|music|tracks?)\b",
        r"\bno more (?:songs?|music)\b",
        r"\bi'?m listening to my own\b",
    ),
    "ja": (
        # 曲はもういい / 音楽を流さないで / 歌をおすすめしないで
        r"(?:曲|音楽|歌)(?:は|を)?(?:もう)?(?:いらない|要らない|流さないで|かけないで|勧めないで|すすめないで|おすすめしないで)",
        r"(?:曲|音楽|歌)(?:は|を)?もういい",
        r"自分の(?:曲|音楽|歌)を?聴",
    ),
    "ko": (
        # 노래 그만 틀어 / 음악 추천하지 마
        r"(?:노래|음악)(?:은|는|을|를)?\s*(?:좀\s*)?(?:그만|틀지\s*마|추천하지\s*마|안\s*들을래|필요\s*없어)",
        r"내\s*(?:노래|음악)\s*들을게",
    ),
    "ru": (
        # не включай музыку / хватит ставить песни
        r"(?:не|хватит|перестань|прекрати)\s+(?:\w+\s+){0,2}?"
        r"(?:включа\w*|став\w*|предлага\w*|совет\w*|рекоменд\w*)\s+(?:\w+\s+){0,1}?"
        r"(?:музык\w*|песн\w*|трек\w*)",
        r"хватит\s+(?:музык\w*|песен)",
    ),
    "es": (
        # no me pongas más música / deja de recomendar canciones
        r"(?:\bno|deja de|para de)\s+(?:me\s+)?(?:pongas|poner|recomiendes|recomendar|sugieras|sugerir)\s+"
        r"(?:\w+\s+){0,2}?(?:música|musica|canci[oó]n(?:es)?)",
        r"\bbasta de (?:música|musica|canciones)\b",
    ),
    "pt": (
        # não coloque música / pare de recomendar músicas
        r"(?:n[aã]o|para de|pare de)\s+(?:me\s+)?(?:coloque|coloca|colocar|toque|toca|tocar|recomende|recomenda|recomendar|sugira|sugerir)\s+"
        r"(?:\w+\s+){0,2}?(?:m[uú]sicas?|can[cç](?:[aã]o|[oõ]es))",
        r"\bchega de (?:m[uú]sicas?|can[cç][oõ]es)\b",
    ),
}

# "Don't (ever) stop playing music" asks for more, not less. The negated
# stop is blanked before matching so "playing music" alone cannot hit. Only
# known fillers may sit in between: "I don't care, just stop playing music"
# is still a refusal.
_NEGATED_STOP = re.compile(
    r"\b(?:don'?t|do not|never|won'?t|can'?t|cannot|shouldn'?t)\s+"
    r"(?:(?:ever|even|really|please|you|want to|wanna|have to|need to)\s+){0,2}(?:stop|quit)\b",
    re.IGNORECASE,
)
# Curly and modifier apostrophes from mobile keyboards and IMEs.
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})

_COMPILED: tuple[Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for patterns in MUSIC_REFUSAL_PATTERNS_I18N.values()
    for pattern in patterns
)


def detect_music_refusal(text: str) -> bool:
    """Return whether ``text`` turns down music recommendations in any locale."""
    if not isinstance(text, str) or not text.strip():
        return False
    sample = _NEGATED_STOP.sub(" ", text[:MUSIC_REFUSAL_SCAN_MAX_CHARS].translate(_APOSTROPHES))
    return any(pattern.search(sample) for pattern in _COMPILED)
