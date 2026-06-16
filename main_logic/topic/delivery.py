"""One-shot delivery bridge for prepared topic hooks."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any

from main_logic.proactive_delivery import DELIVERY_ACK_FUTURE_KEY, DELIVERY_RETRACTED_KEY


logger = logging.getLogger("N.E.K.O.Main.topic.delivery")
_DELIVERY_ACK_TIMEOUT_S = 120.0

_SessionManagerGetter = Callable[[str], Any]
_session_manager_getter: _SessionManagerGetter | None = None

_DETAIL_TEMPLATES = {
    'en': {
        'interest': 'Recent focus: {value}',
        'online': 'Online supplement: after searching "{query}", the concrete angle is: {angle}. Use one concrete detail naturally; if it cannot fit this turn, do not trigger this hook.',
        'final': 'Generate only one natural opening sentence, as if it just came to mind. Do not say "based on your recent interests" and do not make it feel like a survey.'
    },
    'es': {
        'interest': 'Lo que le importa ahora: {value}',
        'online': 'Complemento en línea: tras buscar "{query}", el ángulo concreto es: {angle}. Usa un detalle concreto de forma natural; si no encaja en este turno, no actives este hook.',
        'final': 'Genera solo una frase inicial natural, como si se te acabara de ocurrir. No digas "según tus intereses recientes" ni lo hagas sonar como una encuesta.'
    },
    'ja': {
        'interest': '最近気にしていること：{value}',
        'online': 'オンライン補足：「{query}」で調べた具体的な角度：{angle}。具体情報を一つだけ自然に使ってください。このターンで自然に使えないなら、この hook は発火しないでください。',
        'final': '自然な一言の切り出しだけを生成してください。ふと思い出したように短く。「最近の興味によると」のような言い方や、アンケートっぽい聞き方は避けてください。'
    },
    'ko': {
        'interest': '요즘 신경 쓰는 것: {value}',
        'online': '온라인 보충: "{query}" 검색 후 얻은 구체적인 각도: {angle}. 구체 정보 하나를 자연스럽게 사용하세요. 이번 턴에 자연스럽지 않다면 이 hook을 발동하지 마세요.',
        'final': '자연스러운 첫 문장 하나만 생성하세요. 문득 떠올린 말처럼 짧게 말하세요. "최근 관심사에 따르면" 같은 표현이나 설문처럼 느껴지는 질문은 피하세요.'
    },
    'pt': {
        'interest': 'O que anda importando: {value}',
        'online': 'Complemento online: após buscar "{query}", o ângulo concreto é: {angle}. Use um detalhe concreto com naturalidade; se não couber neste turno, não acione este hook.',
        'final': 'Gere apenas uma frase de abertura natural, como se tivesse acabado de lembrar. Não diga "com base nos seus interesses recentes" e não soe como um questionário.'
    },
    'ru': {
        'interest': 'Что сейчас занимает: {value}',
        'online': 'Онлайн-дополнение: после поиска "{query}" конкретный угол такой: {angle}. Естественно используй одну конкретную деталь; если она не подходит этому ходу, не запускай этот hook.',
        'final': 'Сгенерируй только одну естественную вступительную фразу, будто она просто пришла в голову. Не говори "судя по твоим недавним интересам" и не делай это похожим на анкету.'
    },
    'zh': {
        'interest': '最近关注：{value}',
        'online': '联网补充：查询「{query}」后得到的具体角度：{angle}。必须自然用上其中一个具体信息；如果这轮用不上，就不要触发这个 hook。',
        'final': '请只生成一句自然开场，像随口想起来，不要说“根据你的近期兴趣”，不要像问卷。'
    },
    'zh-TW': {
        'interest': '最近關注：{value}',
        'online': '聯網補充：查詢「{query}」後得到的具體角度：{angle}。必須自然用上其中一個具體資訊；如果這輪用不上，就不要觸發這個 hook。',
        'final': '請只生成一句自然開場，像隨口想起來，不要說「根據你的近期興趣」，不要像問卷。'
    },
}


def _detail_template_for_lang(lang: str) -> dict[str, str]:
    raw = (lang or "").strip().lower().replace("_", "-")
    if raw.startswith(("zh-tw", "zh-hant", "zh-hk")):
        return _DETAIL_TEMPLATES["zh-TW"]
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
    online_angle = str(material.get("online_angle") or "").strip()
    online_query = str(material.get("online_query") or "").strip()

    # The hook is a SIGNAL, not an instruction: hand the LLM the topic (and a
    # concrete online fact when we have one), then let it decide how to open.
    # We deliberately do not ship small-model-authored angle/opening/deepening
    # text — that is the Phase-2 model's job.
    template = _detail_template_for_lang(lang)
    detail_parts = [
        template["interest"].format(value=interest) if interest else "",
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
        "summary": interest,
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
    if isinstance(callback, dict):
        callback[DELIVERY_RETRACTED_KEY] = True

    proactive_manager = getattr(mgr, "proactive_manager", None)
    retract = getattr(proactive_manager, "retract", None)
    if callable(retract) and isinstance(callback, dict):
        retract(callback)

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

    # Activity gate: deep topic hooks are fresh text openers, so they must
    # respect the same propensity gate as /api/proactive_chat and stay quiet
    # while the user is in a privacy / gaming / focused-work state. Returning
    # False keeps the material pending so TopicHookPool retries once the state
    # opens up, without burning the daily quota.
    gate = getattr(mgr, "topic_hook_delivery_allowed", None)
    if callable(gate):
        try:
            allowed = bool(gate())
        except Exception:
            allowed = True
        if not allowed:
            logger.info(
                "[%s] topic hook delivery skipped: activity propensity restricts proactive interruption",
                lanlan_name,
            )
            return False

    # Re-resolve the topic language at firing time. The hook captured `lang`
    # when it was scheduled; if the session language changed during the quiet
    # window (e.g. set_user_language with no new chat turn to reschedule the
    # trigger), the live tracker locale is authoritative — otherwise a zh-TW
    # switch would surface the hook in the previously captured locale.
    live_lang_getter = getattr(mgr, "current_topic_language", None)
    if callable(live_lang_getter):
        try:
            live_lang = live_lang_getter()
        except Exception:
            live_lang = None
        if isinstance(live_lang, str) and live_lang:
            lang = live_lang

    callback = build_topic_hook_callback(material, lang=lang)
    submit = getattr(mgr, "submit_proactive_callback", None)
    if callable(submit):
        future = asyncio.get_running_loop().create_future()
        callback[DELIVERY_ACK_FUTURE_KEY] = future
        submit(
            callback,
            priority=callback.get("priority", 0),
            coalesce_key=callback.get("coalesce_key"),
        )
        try:
            delivered = bool(await asyncio.wait_for(asyncio.shield(future), timeout=_DELIVERY_ACK_TIMEOUT_S))
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                delivered = bool(future.result())
                if delivered:
                    return True
            _remove_callback_from_manager(mgr, callback)
            raise
        except asyncio.TimeoutError:
            logger.info("[%s] topic hook delivery timed out waiting for proactive ack", lanlan_name)
            delivered = False
        finally:
            callback.pop(DELIVERY_ACK_FUTURE_KEY, None)
        if not delivered:
            _remove_callback_from_manager(mgr, callback)
        return delivered

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
