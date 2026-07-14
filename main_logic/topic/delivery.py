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
        'online': 'Online supplement: after searching "{query}", the concrete angle is: {angle}. Use one concrete detail naturally; if it cannot fit this turn, do not raise this topic.',
        'final': 'Generate only one natural opening sentence, as if it just came to mind. Do not say "based on your recent interests" and do not make it feel like a survey.'
    },
    'es': {
        'interest': 'Lo que le importa ahora: {value}',
        'online': 'Complemento en línea: tras buscar "{query}", el ángulo concreto es: {angle}. Usa un detalle concreto de forma natural; si no encaja en este turno, no saques este tema.',
        'final': 'Genera solo una frase inicial natural, como si se te acabara de ocurrir. No digas "según tus intereses recientes" ni lo hagas sonar como una encuesta.'
    },
    'ja': {
        'interest': '最近気にしていること：{value}',
        'online': 'オンライン補足：「{query}」で調べた具体的な角度：{angle}。具体情報を一つだけ自然に使ってください。このターンで自然に使えないなら、この話題は持ち出さないでください。',
        'final': '自然な一言の切り出しだけを生成してください。ふと思い出したように短く。「最近の興味によると」のような言い方や、アンケートっぽい聞き方は避けてください。'
    },
    'ko': {
        'interest': '요즘 신경 쓰는 것: {value}',
        'online': '온라인 보충: "{query}" 검색 후 얻은 구체적인 각도: {angle}. 구체 정보 하나를 자연스럽게 사용하세요. 이번 턴에 자연스럽지 않다면 이 화제를 꺼내지 마세요.',
        'final': '자연스러운 첫 문장 하나만 생성하세요. 문득 떠올린 말처럼 짧게 말하세요. "최근 관심사에 따르면" 같은 표현이나 설문처럼 느껴지는 질문은 피하세요.'
    },
    'pt': {
        'interest': 'O que anda importando: {value}',
        'online': 'Complemento online: após buscar "{query}", o ângulo concreto é: {angle}. Use um detalhe concreto com naturalidade; se não couber neste turno, não puxe este assunto.',
        'final': 'Gere apenas uma frase de abertura natural, como se tivesse acabado de lembrar. Não diga "com base nos seus interesses recentes" e não soe como um questionário.'
    },
    'ru': {
        'interest': 'Что сейчас занимает: {value}',
        'online': 'Онлайн-дополнение: после поиска "{query}" конкретный угол такой: {angle}. Естественно используй одну конкретную деталь; если она не подходит этому ходу, не поднимай эту тему.',
        'final': 'Сгенерируй только одну естественную вступительную фразу, будто она просто пришла в голову. Не говори "судя по твоим недавним интересам" и не делай это похожим на анкету.'
    },
    'zh': {
        'interest': '最近关注：{value}',
        'online': '联网补充：查询「{query}」后得到的具体角度：{angle}。必须自然用上其中一个具体信息；如果这轮用不上，就不要发起这个话题。',
        'final': '请只生成一句自然开场，像随口想起来，不要说“根据你的近期兴趣”，不要像问卷。'
    },
    'zh-TW': {
        'interest': '最近關注：{value}',
        'online': '聯網補充：查詢「{query}」後得到的具體角度：{angle}。必須自然用上其中一個具體資訊；如果這輪用不上，就不要發起這個話題。',
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
    import ``app.main_server`` for state: running ``python -m app.main_server``
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
    callback = {
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
    release_available = material.get("_topic_release_available")
    if callable(release_available):
        callback["_topic_release_available"] = release_available
    return callback


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


def _resolve_topic_manager(lanlan_name: str) -> Any:
    """Resolve the live session manager, logging why when it is unavailable."""
    if _session_manager_getter is None:
        logger.info("[%s] topic hook delivery skipped: no session manager getter", lanlan_name)
        return None
    mgr = _session_manager_getter(lanlan_name)
    if mgr is None:
        logger.info("[%s] topic hook delivery skipped: no session manager", lanlan_name)
    return mgr


def _topic_activity_gate_open(mgr: Any, lanlan_name: str) -> bool:
    """Whether the activity propensity gate allows interrupting right now.

    Deep topic hooks are fresh text openers, so they respect the same gate as
    /api/proactive_chat and stay quiet during closed / gaming / focused-work. A
    closed gate keeps the material pending for TopicHookPool to retry once the
    state opens up, without burning the daily quota. A missing or throwing gate
    fails open, matching the proactive path's default.
    """
    gate = getattr(mgr, "topic_hook_delivery_allowed", None)
    if not callable(gate):
        return True
    try:
        allowed = bool(gate())
    except Exception:
        return True
    if not allowed:
        logger.info(
            "[%s] topic hook delivery skipped: activity propensity restricts proactive interruption",
            lanlan_name,
        )
    return allowed


def _topic_manager_release_gate_open(mgr: Any, lanlan_name: str) -> bool:
    """Whether the proactive manager would release a submitted callback now."""
    gate = getattr(mgr, "_can_release_proactive", None)
    if not callable(gate):
        return True
    try:
        allowed = bool(gate())
    except Exception as exc:
        logger.debug("[%s] topic hook manager release preflight failed: %s", lanlan_name, exc)
        return False
    if not allowed:
        logger.info(
            "[%s] topic hook delivery skipped: proactive manager cannot release yet",
            lanlan_name,
        )
    return allowed


def _topic_proactive_switch_open(lanlan_name: str) -> bool:
    try:
        from utils.preferences import load_global_conversation_settings
        enabled = bool(load_global_conversation_settings().get("proactiveChatEnabled", True))
    except Exception as exc:
        logger.debug("[%s] topic hook proactive switch check failed closed: %s", lanlan_name, exc)
        return False
    if not enabled:
        logger.debug("[%s] topic hook delivery skipped: proactive chat is disabled", lanlan_name)
    return enabled


def topic_hook_delivery_available(
    lanlan_name: str,
    *,
    include_manager_release: bool = True,
) -> bool:
    """Preflight whether a topic hook could be delivered right now."""
    mgr = _resolve_topic_manager(lanlan_name)
    if mgr is None:
        return False
    if not _topic_proactive_switch_open(lanlan_name):
        return False
    is_goodbye_silent = getattr(mgr, "is_goodbye_silent", None)
    has_silent_gate = (
        "is_goodbye_silent" in getattr(mgr, "__dict__", {})
        or hasattr(type(mgr), "is_goodbye_silent")
    )
    if has_silent_gate and callable(is_goodbye_silent):
        try:
            if bool(is_goodbye_silent()):
                logger.info(
                    "[%s] topic hook delivery skipped: goodbye silence is active",
                    lanlan_name,
                )
                return False
        except Exception as exc:
            logger.warning(
                "[%s] topic hook goodbye-silent preflight failed open: %s",
                lanlan_name,
                exc,
            )
    if not _topic_activity_gate_open(mgr, lanlan_name):
        return False
    if include_manager_release and not _topic_manager_release_gate_open(mgr, lanlan_name):
        return False
    if callable(getattr(mgr, "submit_proactive_callback", None)):
        return True
    return callable(getattr(mgr, "enqueue_agent_callback", None)) and callable(
        getattr(mgr, "trigger_agent_callbacks", None)
    )


def _live_topic_lang(mgr: Any, captured_lang: str) -> str:
    """Re-resolve the topic language at firing time.

    The hook captured ``lang`` when scheduled; if the session language changed
    while the material was pending/prepared, the live tracker locale is
    authoritative — otherwise a zh-TW switch would surface the hook in the
    captured locale.
    """
    getter = getattr(mgr, "current_topic_language", None)
    if not callable(getter):
        return captured_lang
    try:
        live_lang = getter()
    except Exception:
        return captured_lang
    return live_lang if isinstance(live_lang, str) and live_lang else captured_lang


async def _deliver_via_proactive_manager(mgr: Any, callback: dict, lanlan_name: str) -> bool:
    """Submit through the proactive manager and wait for the delivery ack."""
    future = asyncio.get_running_loop().create_future()
    callback[DELIVERY_ACK_FUTURE_KEY] = future
    mgr.submit_proactive_callback(
        callback,
        priority=callback.get("priority", 0),
        coalesce_key=callback.get("coalesce_key"),
    )
    try:
        return bool(await asyncio.wait_for(asyncio.shield(future), timeout=_DELIVERY_ACK_TIMEOUT_S))
    except asyncio.CancelledError:
        # If the ack landed True just before cancellation, honour it: the caller
        # must not retract a callback that already went out.
        if future.done() and not future.cancelled() and future.result():
            return True
        raise
    except asyncio.TimeoutError:
        logger.info("[%s] topic hook delivery timed out waiting for proactive ack", lanlan_name)
        return False
    finally:
        callback.pop(DELIVERY_ACK_FUTURE_KEY, None)


async def _deliver_via_agent_callbacks(mgr: Any, callback: dict, lanlan_name: str) -> bool:
    """Fallback delivery for managers without the proactive manager front stage."""
    enqueue = getattr(mgr, "enqueue_agent_callback", None)
    trigger = getattr(mgr, "trigger_agent_callbacks", None)
    if not callable(enqueue) or not callable(trigger):
        logger.info("[%s] topic hook delivery skipped: manager cannot deliver callbacks", lanlan_name)
        return False
    enqueue(callback)
    return bool(await trigger())


async def trigger_topic_hook_once(
    *,
    lanlan_name: str,
    material: Mapping[str, Any],
    lang: str,
) -> bool:
    """Queue one prepared topic hook into the existing character delivery path.

    Returns True only on confirmed delivery. Every other outcome returns False
    (or propagates cancellation) and retracts the queued copy, so the callback
    can never resurface outside TopicHookPool's one-shot bookkeeping — only the
    pool may retry and mark used / burn quota.
    """
    if not topic_hook_delivery_available(
        lanlan_name,
        include_manager_release=False,
    ):
        return False
    mgr = _resolve_topic_manager(lanlan_name)
    if mgr is None:
        return False

    lang = _live_topic_lang(mgr, lang)
    callback = build_topic_hook_callback(material, lang=lang)

    try:
        if callable(getattr(mgr, "submit_proactive_callback", None)):
            delivered = await _deliver_via_proactive_manager(mgr, callback, lanlan_name)
        else:
            delivered = await _deliver_via_agent_callbacks(mgr, callback, lanlan_name)
    except BaseException:
        # Cancellation or a delivery error: drop the queued copy before
        # propagating so it cannot surface outside the pool's bookkeeping.
        _remove_callback_from_manager(mgr, callback)
        raise

    if not delivered:
        _remove_callback_from_manager(mgr, callback)
    return delivered
