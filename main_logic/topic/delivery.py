"""One-shot delivery bridge for prepared topic hooks."""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any


logger = logging.getLogger("N.E.K.O.Main.topic.delivery")

_SessionManagerGetter = Callable[[str], Any]
_session_manager_getter: _SessionManagerGetter | None = None

_DETAIL_TEMPLATES = {
    "en": {
        "intro": "This is a vetted low-frequency deep topic hook.",
        "interest": "Relationship point: {value}",
        "hook": "Entry angle: {value}",
        "opening": "Opening direction: {value}",
        "deepening": "If they respond, expand with: {value}",
        "why_now": "Why now: {value}",
        "hint": "Reusable material: {value}",
        "online": (
            'Online supplement: after searching "{query}", the concrete angle is: {angle}. '
            "Use one concrete detail naturally; if it cannot fit this turn, do not trigger this hook."
        ),
        "final": (
            'Generate only one natural opening sentence, as if it just came to mind. '
            'Do not say "based on your recent interests" and do not make it feel like a survey.'
        ),
    },
    "es": {
        "intro": "Este es un hook de tema profundo y poco frecuente ya filtrado.",
        "interest": "Punto de conexión: {value}",
        "hook": "Ángulo de entrada: {value}",
        "opening": "Dirección de apertura: {value}",
        "deepening": "Si la persona responde, continúa con: {value}",
        "why_now": "Por qué encaja ahora: {value}",
        "hint": "Material aprovechable: {value}",
        "online": (
            'Complemento en línea: tras buscar "{query}", el ángulo concreto es: {angle}. '
            "Usa un detalle concreto de forma natural; si no encaja en este turno, no actives este hook."
        ),
        "final": (
            "Genera solo una frase inicial natural, como si se te acabara de ocurrir. "
            'No digas "según tus intereses recientes" ni lo hagas sonar como una encuesta.'
        ),
    },
    "ja": {
        "intro": "これは、すでに選別済みの低頻度な深掘り話題 hook です。",
        "interest": "関係するポイント：{value}",
        "hook": "切り出す角度：{value}",
        "opening": "最初の出し方：{value}",
        "deepening": "相手が乗った後の広げ方：{value}",
        "why_now": "今この話題が合う理由：{value}",
        "hint": "使える素材：{value}",
        "online": (
            "オンライン補足：「{query}」で調べた具体的な角度：{angle}。"
            "具体情報を一つだけ自然に使ってください。このターンで自然に使えないなら、この hook は発火しないでください。"
        ),
        "final": (
            "自然な一言の切り出しだけを生成してください。ふと思い出したように短く。"
            "「最近の興味によると」のような言い方や、アンケートっぽい聞き方は避けてください。"
        ),
    },
    "ko": {
        "intro": "이미 선별된 낮은 빈도의 깊은 화제 hook입니다.",
        "interest": "연결 지점: {value}",
        "hook": "꺼내는 각도: {value}",
        "opening": "첫마디 방향: {value}",
        "deepening": "상대가 받아 주면 이어 갈 방향: {value}",
        "why_now": "지금 어울리는 이유: {value}",
        "hint": "활용할 수 있는 소재: {value}",
        "online": (
            '온라인 보충: "{query}" 검색 후 얻은 구체적인 각도: {angle}. '
            "구체 정보 하나를 자연스럽게 사용하세요. 이번 턴에 자연스럽지 않다면 이 hook을 발동하지 마세요."
        ),
        "final": (
            "자연스러운 첫 문장 하나만 생성하세요. 문득 떠올린 말처럼 짧게 말하세요. "
            '"최근 관심사에 따르면" 같은 표현이나 설문처럼 느껴지는 질문은 피하세요.'
        ),
    },
    "pt": {
        "intro": "Este é um hook de tópico profundo e pouco frequente já filtrado.",
        "interest": "Ponto de conexão: {value}",
        "hook": "Ângulo de entrada: {value}",
        "opening": "Direção de abertura: {value}",
        "deepening": "Se a pessoa responder, desenvolva com: {value}",
        "why_now": "Por que combina agora: {value}",
        "hint": "Material aproveitável: {value}",
        "online": (
            'Complemento online: após buscar "{query}", o ângulo concreto é: {angle}. '
            "Use um detalhe concreto com naturalidade; se não couber neste turno, não acione este hook."
        ),
        "final": (
            "Gere apenas uma frase de abertura natural, como se tivesse acabado de lembrar. "
            'Não diga "com base nos seus interesses recentes" e não soe como um questionário.'
        ),
    },
    "ru": {
        "intro": "Это уже отобранный редкий hook для более глубокого разговора.",
        "interest": "Точка связи: {value}",
        "hook": "Угол входа: {value}",
        "opening": "Как начать: {value}",
        "deepening": "Если собеседник откликнется, развить так: {value}",
        "why_now": "Почему это уместно сейчас: {value}",
        "hint": "Материал, который можно использовать: {value}",
        "online": (
            'Онлайн-дополнение: после поиска "{query}" конкретный угол такой: {angle}. '
            "Естественно используй одну конкретную деталь; если она не подходит этому ходу, не запускай этот hook."
        ),
        "final": (
            "Сгенерируй только одну естественную вступительную фразу, будто она просто пришла в голову. "
            'Не говори "судя по твоим недавним интересам" и не делай это похожим на анкету.'
        ),
    },
    "zh": {
        "intro": "这是一个已经筛好的低频深话题 hook。",
        "interest": "关系点：{value}",
        "hook": "切入角度：{value}",
        "opening": "开口方向：{value}",
        "deepening": "接话后展开：{value}",
        "why_now": "为什么现在适合：{value}",
        "hint": "可借素材：{value}",
        "online": (
            "联网补充：查询「{query}」后得到的具体角度：{angle}。"
            "必须自然用上其中一个具体信息；如果这轮用不上，就不要触发这个 hook。"
        ),
        "final": "请只生成一句自然开场，像随口想起来，不要说“根据你的近期兴趣”，不要像问卷。",
    },
}


def _detail_template_for_lang(lang: str) -> dict[str, str]:
    raw = (lang or "").strip().lower().replace("_", "-")
    if raw.startswith("zh"):
        return _DETAIL_TEMPLATES["zh"]
    key = raw.split("-", 1)[0] if raw else "en"
    return _DETAIL_TEMPLATES.get(key, _DETAIL_TEMPLATES["en"])


def register_topic_session_manager_getter(getter: _SessionManagerGetter | None) -> None:
    """Install the runtime session-manager lookup used by topic delivery.

    ``main_logic.topic.delivery`` lives below the app entrypoint layer, so it must not
    import ``app.main_server`` for state: running ``python app/main_server.py``
    stores the real state under ``__main__`` and importing ``app.main_server``
    creates a second, empty module copy.
    """
    global _session_manager_getter
    _session_manager_getter = getter


def clear_topic_session_manager_getter() -> None:
    """Test helper: remove the runtime session-manager lookup."""
    register_topic_session_manager_getter(None)


def build_topic_hook_callback(material: Mapping[str, Any], *, lang: str) -> dict[str, Any]:
    hook_id = str(material.get("hook_id") or "")
    interest = str(material.get("interest") or "").strip()
    hook = str(material.get("hook") or "").strip()
    opening = str(material.get("opening_intent") or "").strip()
    deepening = str(material.get("deepening_hint") or "").strip()
    why_now = str(material.get("why_now") or "").strip()
    online_angle = str(material.get("online_angle") or "").strip()
    online_query = str(material.get("online_query") or material.get("search_query") or "").strip()
    hint = material.get("material_hint")
    hint_summary = ""
    if isinstance(hint, Mapping):
        hint_summary = str(hint.get("summary") or "").strip()

    template = _detail_template_for_lang(lang)
    detail_parts = [
        template["intro"],
        template["interest"].format(value=interest) if interest else "",
        template["hook"].format(value=hook) if hook else "",
        template["opening"].format(value=opening) if opening else "",
        template["deepening"].format(value=deepening) if deepening else "",
        template["why_now"].format(value=why_now) if why_now else "",
        template["hint"].format(value=hint_summary) if hint_summary else "",
        (
            template["online"].format(query=online_query, angle=online_angle)
        ) if online_angle else "",
        template["final"],
    ]
    detail = "\n".join(part for part in detail_parts if part)
    return {
        "event": "agent_task_callback",
        "origin": "event",
        "task_id": hook_id or "topic_hook",
        "channel": "topic_hook",
        "status": "completed",
        "success": True,
        "summary": hook or interest,
        "detail": detail,
        "source_kind": "topic",
        "source_name": "deep_topic_hook",
        "delivery_mode": "proactive",
        "priority": -20,
        "coalesce_key": hook_id or interest,
        "timestamp": "",
        "metadata": {
            "context_type": "topic_hook",
            "hook_id": hook_id,
            "lang": lang,
        },
        "context_type": "topic_hook",
    }


def _remove_callback_from_manager(mgr: Any, callback: Mapping[str, Any]) -> None:
    delivery_id = callback.get("_callback_delivery_id")
    callback_obj_id = id(callback)

    pending = getattr(mgr, "pending_agent_callbacks", None)
    if isinstance(pending, list):
        mgr.pending_agent_callbacks = [
            item for item in pending
            if (
                id(item) != callback_obj_id
                and (
                    not delivery_id
                    or not isinstance(item, Mapping)
                    or item.get("_callback_delivery_id") != delivery_id
                )
            )
        ]

    extras = getattr(mgr, "pending_extra_replies", None)
    if delivery_id and isinstance(extras, list):
        mgr.pending_extra_replies = [
            item for item in extras
            if not isinstance(item, Mapping) or item.get("_callback_delivery_id") != delivery_id
        ]


async def trigger_topic_hook_once(
    *,
    lanlan_name: str,
    material: Mapping[str, Any],
    lang: str,
) -> bool:
    """Queue one prepared topic hook into the existing character delivery path."""
    if _session_manager_getter is None:
        logger.info("[%s] topic hook delivery skipped: no session manager getter", lanlan_name)
        return False

    mgr = _session_manager_getter(lanlan_name)
    if mgr is None:
        logger.info("[%s] topic hook delivery skipped: no session manager", lanlan_name)
        return False

    callback = build_topic_hook_callback(material, lang=lang)
    enqueue = getattr(mgr, "enqueue_agent_callback", None)
    trigger = getattr(mgr, "trigger_agent_callbacks", None)
    if not callable(enqueue) or not callable(trigger):
        logger.info("[%s] topic hook delivery skipped: manager cannot deliver callbacks", lanlan_name)
        return False

    enqueue(callback)
    try:
        delivered = bool(await trigger())
    except Exception:
        _remove_callback_from_manager(mgr, callback)
        raise
    if not delivered:
        # ``trigger_agent_callbacks`` keeps callbacks queued on many retriable
        # False paths. Topic hooks need stricter accounting: only
        # TopicHookPool may retry and mark used/quota. Remove this queued copy so
        # it cannot surface later outside the topic pool's one-shot bookkeeping.
        _remove_callback_from_manager(mgr, callback)
    return delivered
