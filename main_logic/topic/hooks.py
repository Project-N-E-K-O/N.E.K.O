"""Topic-hook prompt helpers for proactive chat.

This module intentionally does not schedule, persist, or deliver anything.
It only turns already-approved proactive candidates into a compact prompt
section that the existing /api/proactive_chat Phase 2 path can consume.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from main_logic.topic.common import clean_text


_HEADER_ZH = """【低频深话题候选】
下面这些不是必须聊的话题，只是更适合聊深一点的切入点。目标是关系深度，不是触发频率；宁可不用，也不要硬聊；这轮最多认真挑 1-2 个最强相关的。
候选里可能夹着寒暄、语气词或还不值得展开的短句；你先判断，没价值就忽略。
开口要求：具体、短、像随口一提，可以轻微调侃；最终只选一个，只抛一个自然钩子，后面交给多轮展开；不要暴露素材来源，也不要像问卷。"""

_HEADER_ZH_TW = """【低頻深話題候選】
下面這些不是必須聊的話題，只是更適合聊深一點的切入點。目標是關係深度，不是觸發頻率；寧可不用，也不要硬聊；這輪最多認真挑 1-2 個最強相關的。
候選裡可能夾著寒暄、語氣詞或還不值得展開的短句；你先判斷，沒價值就忽略。
開口要求：具體、短、像隨口一提，可以輕微調侃；最終只選一個，只拋一個自然鉤子，後面交給多輪展開；不要暴露素材來源，也不要像問卷。"""

_HEADER_EN = """[Low-frequency deeper topic candidates]
These are optional hooks for a slightly deeper proactive chat. Use at most 1-2 only if they are clearly the strongest matches; it is better to use none than force it.
Some candidates may be greetings, filler, or too thin to continue; judge first and ignore them if they are not useful.
Opening style: specific, short, casual, lightly teasing if appropriate. Open with one natural hook and leave the rest to multi-turn expansion. Do not say "based on your recent interests" or sound like a survey."""

_HEADER_JA = """【低頻度の深め話題候補】
これは少し深めの自然な会話に使える任意のきっかけです。明らかに強く合うものだけ最大1-2個使い、無理に拾うくらいなら使わないでください。
候補には挨拶、つなぎ言葉、広げるには薄い短文が混じることがあります。まず判断し、役に立たなければ無視してください。
切り出し方：具体的、短く、自然に、合うなら軽くからかう程度。自然な hook を1つだけ投げ、続きは複数ターンに任せてください。素材元を明かしたり、アンケートのように聞いたりしないでください。"""

_HEADER_KO = """[저빈도 깊은 화제 후보]
이 항목들은 조금 더 깊은 능동 대화를 위한 선택적 hook입니다. 가장 잘 맞는 경우에만 최대 1-2개를 쓰고, 억지로 쓰기보다 안 쓰는 편이 낫습니다.
후보에는 인사, 말버릇, 이어가기 어려운 짧은 문장이 섞일 수 있습니다. 먼저 판단하고 쓸모없으면 무시하세요.
시작 방식: 구체적이고 짧고 자연스럽게, 어울리면 살짝 장난스럽게. 자연스러운 hook 하나만 던지고 나머지는 여러 턴에 맡기세요. 소재 출처를 드러내거나 설문처럼 묻지 마세요."""

_HEADER_ES = """[Candidatos de temas profundos de baja frecuencia]
Son hooks opcionales para una charla proactiva un poco más profunda. Usa como máximo 1-2 solo si encajan claramente; es mejor no usar ninguno que forzarlo.
Algunos candidatos pueden ser saludos, relleno o ideas demasiado débiles; juzga primero e ignóralos si no sirven.
Estilo de apertura: concreto, breve, casual y con una broma suave si encaja. Abre con un solo hook natural y deja el resto para varios turnos. No reveles el origen del material ni suenes como una encuesta."""

_HEADER_PT = """[Candidatos de temas profundos de baixa frequência]
Estes são hooks opcionais para uma conversa proativa um pouco mais profunda. Use no máximo 1-2 apenas quando forem claramente os melhores encaixes; é melhor não usar nenhum do que forçar.
Alguns candidatos podem ser cumprimentos, enchimento ou frases fracas demais para continuar; avalie primeiro e ignore se não forem úteis.
Estilo de abertura: concreto, curto, casual e com uma provocação leve se couber. Abra com um único hook natural e deixe o resto para vários turnos. Não revele a origem do material nem soe como questionário."""

_HEADER_RU = """[Низкочастотные кандидаты для более глубоких тем]
Это необязательные hooks для чуть более глубокой проактивной беседы. Используй максимум 1-2 только если они явно подходят лучше всего; лучше не использовать ничего, чем форсировать тему.
Среди кандидатов могут быть приветствия, пустые фразы или слишком слабые зацепки; сначала оцени и игнорируй, если пользы нет.
Стиль начала: конкретно, коротко, непринужденно, с легкой поддевкой если уместно. Начни с одного естественного hook и оставь развитие на несколько ходов. Не раскрывай источник материала и не звучит как анкета."""

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
