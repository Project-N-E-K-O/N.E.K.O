"""Topic-hook prompt helpers for proactive chat.

This module intentionally does not schedule, persist, or deliver anything.
It only turns already-approved proactive candidates into a compact prompt
section that the existing /api/proactive_chat Phase 2 path can consume.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from main_logic.topic.common import clean_text


# Deliberately encouraging, not discouraging: paired with Phase 2's repeated
# anti-repeat warnings, weak/negative wording made the model treat callbacks as
# "high repeat risk" and skip them. Frame these as welcome, natural cues. No
# backend scheduling jargon (frequency/quota): the model only decides whether to
# open this turn. One natural pick, the rest left to later turns.
_HEADER_ZH = """======可以自然回忆或接续的话题======
下面是一些适合自然带出来的旧话题和还没聊完的点。挑一个最顺的，像随口想起一样轻轻提起就好；开口具体、简短、别像问卷，剩下的留给后面慢慢聊。"""

_HEADER_ZH_TW = """======可以自然回憶或接續的話題======
下面是一些適合自然帶出來的舊話題和還沒聊完的點。挑一個最順的，像隨口想起一樣輕輕提起就好；開口具體、簡短、別像問卷，剩下的留給後面慢慢聊。"""

_HEADER_EN = """======Topics worth recalling or picking back up======
Below are older topics and unfinished points that fit a natural callback. Pick whichever flows best and bring it up lightly, as if it just came to mind; keep the opener specific and short, not survey-like, and leave the rest for later turns."""

_HEADER_JA = """======自然に思い出して続けられる話題======
以下は自然に持ち出せる昔の話題やまだ終わっていない点です。一番しっくりくるものを一つ、ふと思い出したように軽く切り出してください。具体的で短く、アンケートっぽくならないように、残りは後のターンに回します。"""

_HEADER_KO = """======자연스럽게 떠올려 이어갈 화제======
아래는 자연스럽게 꺼낼 수 있는 예전 화제와 아직 끝나지 않은 점들입니다. 가장 잘 맞는 하나를 문득 떠오른 듯 가볍게 꺼내세요. 구체적이고 짧게, 설문처럼 굴지 말고, 나머지는 다음 턴에 맡기세요."""

_HEADER_ES = """======Temas para recordar o retomar con naturalidad======
Abajo hay temas antiguos y puntos sin terminar que encajan en un retorno natural. Elige el que mejor fluya y sácalo con ligereza, como si acabara de ocurrírsete; que la apertura sea concreta, breve y no parezca una encuesta, y deja el resto para turnos posteriores."""

_HEADER_PT = """======Temas para relembrar ou retomar com naturalidade======
Abaixo há temas antigos e pontos não concluídos que cabem em um retorno natural. Escolha o que fluir melhor e traga-o de leve, como se tivesse acabado de lembrar; mantenha a abertura concreta, curta e sem parecer um questionário, e deixe o resto para turnos seguintes."""

_HEADER_RU = """======Темы, к которым приятно вернуться======
Ниже — старые темы и незавершённые моменты, подходящие для естественного возврата. Выбери ту, что заходит лучше всего, и заведи её легко, будто только что вспомнил; начни конкретно и коротко, без анкетного тона, остальное оставь на следующие ходы."""

_HEADERS = {
    "zh": _HEADER_ZH,
    "zh-CN": _HEADER_ZH,
    "zh-TW": _HEADER_ZH_TW,
    "en": _HEADER_EN,
    "ja": _HEADER_JA,
    "ko": _HEADER_KO,
    "es": _HEADER_ES,
    "pt": _HEADER_PT,
    "ru": _HEADER_RU,
}

_LABELS = {
    "zh": {"memory": "可以顺手接的话题", "thread": "刚才没聊完的点"},
    "zh-CN": {"memory": "可以顺手接的话题", "thread": "刚才没聊完的点"},
    "zh-TW": {"memory": "可以順手接的話題", "thread": "剛才沒聊完的點"},
    "en": {"memory": "Optional memory hook", "thread": "Open thread"},
    "ja": {"memory": "自然に拾える話題", "thread": "未完了の話題"},
    "ko": {"memory": "가볍게 이어갈 화제", "thread": "아직 끝나지 않은 점"},
    "es": {"memory": "Tema opcional de memoria", "thread": "Hilo abierto"},
    "pt": {"memory": "Gancho opcional de memória", "thread": "Ponto ainda em aberto"},
    "ru": {"memory": "Тема из памяти", "thread": "Незавершенная мысль"},
}

def _lang_key(lang: str) -> str:
    raw = (lang or "").strip()
    if raw in _LABELS:
        return raw
    if raw.lower().startswith("zh"):
        return "zh"
    short = raw.split("-", 1)[0].lower()
    if short in _LABELS:
        return short
    return "en"


def _iter_followup_texts(followup_topics: Iterable[Mapping[str, Any]] | None) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    for topic in followup_topics or []:
        if not isinstance(topic, Mapping):
            continue
        text = clean_text(topic.get("text"))
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text)
    return texts


def _iter_open_threads(open_threads: Iterable[Any] | None) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    for item in open_threads or []:
        text = clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text)
    return texts


def build_topic_hook_prompt(
    lang: str,
    *,
    followup_topics: Iterable[Mapping[str, Any]] | None = None,
    open_threads: Iterable[Any] | None = None,
    max_items: int = 3,
) -> str:
    """Render optional topic hooks for the existing proactive prompt.

    The output is deliberately a prompt section, not final copy. Phase 2 still
    owns character voice, timing, and whether to pass. Only the followup
    (reflection) and open-thread surfaces are rendered here; the background
    topic pool delivers its own materials through build_topic_hook_callback,
    not this prompt section.
    """
    key = _lang_key(lang)
    labels = _LABELS.get(key, _LABELS["en"])
    header = _HEADERS.get(key, _HEADER_EN)

    memory_texts = _iter_followup_texts(followup_topics)[:max_items]
    thread_texts = _iter_open_threads(open_threads)[:max_items]
    if not memory_texts and not thread_texts:
        return ""

    lines = [header]
    for text in memory_texts:
        lines.append(f"- {labels['memory']}: {text}")
    for text in thread_texts:
        lines.append(f"- {labels['thread']}: {text}")
    return "\n".join(lines) + "\n"
