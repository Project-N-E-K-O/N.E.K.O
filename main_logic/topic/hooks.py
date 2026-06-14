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
    "zh-TW": _HEADER_ZH,
    "en": _HEADER_EN,
    "ja": _HEADER_JA,
    "ko": _HEADER_KO,
    "es": _HEADER_ES,
    "pt": _HEADER_PT,
    "ru": _HEADER_RU,
}

_LABELS = {
    "zh": {"material": "深话题 hook", "recent": "刚聊到的点", "memory": "可以顺手接的话题", "thread": "刚才没聊完的点"},
    "zh-CN": {"material": "深话题 hook", "recent": "刚聊到的点", "memory": "可以顺手接的话题", "thread": "刚才没聊完的点"},
    "zh-TW": {"material": "深話題 hook", "recent": "剛聊到的點", "memory": "可以順手接的話題", "thread": "剛才沒聊完的點"},
    "en": {"material": "Deep topic hook", "recent": "Recent topic", "memory": "Optional memory hook", "thread": "Open thread"},
    "ja": {"material": "深め話題 hook", "recent": "さっき触れた点", "memory": "自然に拾える話題", "thread": "未完了の話題"},
    "ko": {"material": "깊은 화제 hook", "recent": "방금 나온 점", "memory": "가볍게 이어갈 화제", "thread": "아직 끝나지 않은 점"},
    "es": {"material": "Hook de tema profundo", "recent": "Tema reciente", "memory": "Tema opcional de memoria", "thread": "Hilo abierto"},
    "pt": {"material": "Hook de tema profundo", "recent": "Tema recente", "memory": "Gancho opcional de memória", "thread": "Ponto ainda em aberto"},
    "ru": {"material": "Hook для глубокой темы", "recent": "Недавняя тема", "memory": "Тема из памяти", "thread": "Незавершенная мысль"},
}

_MATERIAL_FIELD_LABELS = {
    "zh": {
        "interest": "关系点",
        "hook": "切入",
        "opening": "开口方向",
        "deepening": "接话后",
        "hint_summary": "联网素材",
        "online_angle": "联网角度",
        "online_angle_suffix": "如果用这个 hook，必须自然借一个具体点",
        "hint_links": "素材标题",
    },
    "zh-CN": {
        "interest": "关系点",
        "hook": "切入",
        "opening": "开口方向",
        "deepening": "接话后",
        "hint_summary": "联网素材",
        "online_angle": "联网角度",
        "online_angle_suffix": "如果用这个 hook，必须自然借一个具体点",
        "hint_links": "素材标题",
    },
    "zh-TW": {
        "interest": "關係點",
        "hook": "切入",
        "opening": "開口方向",
        "deepening": "接話後",
        "hint_summary": "聯網素材",
        "online_angle": "聯網角度",
        "online_angle_suffix": "如果用這個 hook，必須自然借一個具體點",
        "hint_links": "素材標題",
    },
    "en": {
        "interest": "Relationship point",
        "hook": "Entry hook",
        "opening": "Opening direction",
        "deepening": "If they respond",
        "hint_summary": "Online material",
        "online_angle": "Online angle",
        "online_angle_suffix": "if you use this hook, borrow one concrete detail naturally",
        "hint_links": "Source titles",
    },
    "ja": {
        "interest": "関係点",
        "hook": "切り口",
        "opening": "切り出し方",
        "deepening": "相手が返した後",
        "hint_summary": "オンライン素材",
        "online_angle": "オンライン角度",
        "online_angle_suffix": "この hook を使うなら具体点を1つ自然に借りる",
        "hint_links": "素材タイトル",
    },
    "ko": {
        "interest": "관계 포인트",
        "hook": "진입 hook",
        "opening": "시작 방향",
        "deepening": "상대가 답한 뒤",
        "hint_summary": "온라인 소재",
        "online_angle": "온라인 각도",
        "online_angle_suffix": "이 hook을 쓰면 구체적인 디테일 하나를 자연스럽게 빌릴 것",
        "hint_links": "소재 제목",
    },
    "es": {
        "interest": "Punto de conexión",
        "hook": "Entrada",
        "opening": "Intención de apertura",
        "deepening": "Si responde",
        "hint_summary": "Material online",
        "online_angle": "Ángulo online",
        "online_angle_suffix": "si usas este hook, apóyate naturalmente en un detalle concreto",
        "hint_links": "Títulos de material",
    },
    "pt": {
        "interest": "Ponto de conexão",
        "hook": "Entrada",
        "opening": "Intenção de abertura",
        "deepening": "Se a pessoa responder",
        "hint_summary": "Material online",
        "online_angle": "Ângulo online",
        "online_angle_suffix": "se usar este hook, apoie-se naturalmente em um detalhe concreto",
        "hint_links": "Títulos do material",
    },
    "ru": {
        "interest": "Точка связи",
        "hook": "Вход в тему",
        "opening": "Намерение начала",
        "deepening": "Если пользователь ответит",
        "hint_summary": "Онлайн-материал",
        "online_angle": "Онлайн-ракурс",
        "online_angle_suffix": "если используешь этот hook, естественно привяжи один конкретный факт",
        "hint_links": "Заголовки материалов",
    },
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


def _iter_topic_materials(topic_materials: Iterable[Mapping[str, Any]] | None, *, lang: str) -> list[str]:
    field_labels = _MATERIAL_FIELD_LABELS.get(lang, _MATERIAL_FIELD_LABELS["en"])
    texts: list[str] = []
    seen: set[str] = set()
    for material in topic_materials or []:
        if not isinstance(material, Mapping):
            continue
        interest = clean_text(material.get("interest"), limit=90)
        hook = clean_text(material.get("hook"), limit=120)
        opening = clean_text(material.get("opening_intent"), limit=90)
        deepening = clean_text(material.get("deepening_hint"), limit=90)
        hint = material.get("material_hint")
        online_angle = clean_text(material.get("online_angle"), limit=100)
        hint_summary = ""
        hint_links: list[str] = []
        if isinstance(hint, Mapping):
            hint_summary = clean_text(hint.get("summary"), limit=100)
            for link in hint.get("links") or []:
                if isinstance(link, Mapping):
                    title = clean_text(link.get("title"), limit=60)
                    link_type = clean_text(link.get("type"), limit=20)
                    if title:
                        hint_links.append(f"{link_type}:{title}" if link_type else title)

        parts = []
        if interest:
            parts.append(f"{field_labels['interest']}={interest}")
        if hook:
            parts.append(f"{field_labels['hook']}={hook}")
        if opening:
            parts.append(f"{field_labels['opening']}={opening}")
        if deepening:
            parts.append(f"{field_labels['deepening']}={deepening}")
        if hint_summary:
            parts.append(f"{field_labels['hint_summary']}={hint_summary}")
        if online_angle:
            parts.append(
                f"{field_labels['online_angle']}={online_angle}；{field_labels['online_angle_suffix']}"
            )
        if hint_links:
            parts.append(f"{field_labels['hint_links']}={'; '.join(hint_links[:2])}")
        text = "；".join(parts)
        if text and text not in seen:
            seen.add(text)
            texts.append(text)
    return texts


def build_topic_hook_prompt(
    lang: str,
    *,
    topic_materials: Iterable[Mapping[str, Any]] | None = None,
    recent_topics: Iterable[Any] | None = None,
    followup_topics: Iterable[Mapping[str, Any]] | None = None,
    open_threads: Iterable[Any] | None = None,
    max_items: int = 3,
) -> str:
    """Render optional topic hooks for the existing proactive prompt.

    The output is deliberately a prompt section, not final copy. Phase 2 still
    owns character voice, timing, and whether to pass.
    """
    key = _lang_key(lang)
    labels = _LABELS.get(key, _LABELS["en"])
    header = _HEADERS.get(key, _HEADER_EN)

    material_texts = _iter_topic_materials(topic_materials, lang=key)[:max_items]
    recent_texts = _iter_open_threads(recent_topics)[:max_items]
    memory_texts = _iter_followup_texts(followup_topics)[:max_items]
    thread_texts = _iter_open_threads(open_threads)[:max_items]
    if not material_texts and not recent_texts and not memory_texts and not thread_texts:
        return ""

    lines = [header]
    for text in material_texts:
        lines.append(f"- {labels['material']}: {text}")
    for text in recent_texts:
        lines.append(f"- {labels['recent']}: {text}")
    for text in memory_texts:
        lines.append(f"- {labels['memory']}: {text}")
    for text in thread_texts:
        lines.append(f"- {labels['thread']}: {text}")
    return "\n".join(lines) + "\n"
