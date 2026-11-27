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

"""Which way a small closed question goes, by what the user's vendor can do.

Call sites that once asked the recognition chat model a closed question
(which mood, which option, is it a side question...) now pick one of three
routes, decided per vendor (owner sign-off 2026-11-27, D26):

``ROUTE_NATIVE``
    The decision slot points at a native zero-output Decisions API
    (Bailian, OpenAI, SiliconFlow, Gemini, OpenRouter, the System One routers).
    The site asks its decision question there.
``ROUTE_REHOST``
    No usable native decision, but the site's chat model returns token
    probabilities (Doubao, DeepSeek, Kimi, StepFun). The same decision
    question goes to that chat model as a lettered single choice and the
    answer is read from the first token's probabilities
    (:mod:`utils.decision_client.rehost`).
``ROUTE_CHAT``
    Everyone else: the site's own chat prompt and parser, unchanged.

Every failure of the first two routes (error, timeout, cooldown, no
probabilities) falls back to the site's own chat prompt, never to
:class:`~utils.decision_client.adapters.ChatFallbackDecisionAdapter`: that
rewrite regressed several sites and the free tier refuses its prompt.
Each site decides which routes it uses; a site with no rehost reading simply
treats ``ROUTE_REHOST`` as ``ROUTE_CHAT``.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Mapping

from .schema import infer_decision_protocol, is_native_decision_protocol

ROUTE_NATIVE = "native"
ROUTE_REHOST = "rehost"
ROUTE_CHAT = "chat"

# Call sites, as named in the owner's decisions.
SITE_MASTER_EMOTION = "master_emotion"
SITE_SOCCER_GUARD = "soccer_guard"
SITE_NEGATIVE_KEYWORDS = "negative_keywords"
SITE_COARSE_SCREEN = "coarse_screen"
SITE_MEMORY_RECALL = "memory_recall"
SITE_SIDE_QUESTION = "side_question"
SITE_BELONGS = "belongs"
SITE_EMOTION_DISPLAY = "emotion_display"
SITE_TURN_CHECK = "turn_check"

# Chat endpoints whose answers carry the top-20 candidates of each token
# (probed 2026-11-27). GLM, Grok and MiniMax return none; Bailian chat caps
# top_logprobs at 5 and has a native decision model anyway.
LOGPROB_CHAT_HOSTS = frozenset({
    "ark.cn-beijing.volces.com",  # Doubao
    "api.deepseek.com",
    "api.moonshot.cn",  # Kimi
    "api.stepfun.com",
})

# The built-in free tier. Its gateways take a request only by fixed opening
# words of the prompt, so a question written for this rework is turned away
# ("you are not using Lanlan") until the gateway registers it. Until then the
# free tier keeps every site's own chat request unchanged (owner, 2026-11-28);
# the domestic gateway, which is Doubao behind it, joins the rehost route when
# FREE_TIER_NEW_PROMPTS_ENABLED is switched on.
FREE_TIER_HOSTS = frozenset({
    "www.lanlan.tech",
    "lanlan.tech",
    "www.lanlan.app",
    "lanlan.app",
})
FREE_TIER_LOGPROB_HOSTS = frozenset({"www.lanlan.tech", "lanlan.tech"})
FREE_TIER_NEW_PROMPTS_ENABLED = False

# Native decision endpoints that only some sites use. Doubao's decision model
# costs about 300 input tokens a request however short the question, so only
# memory recall (one request for all candidates) uses it; its other sites
# rehost the question on Doubao chat instead (D22-D24).
NATIVE_DECISION_SITES_BY_HOST: dict[str, frozenset[str]] = {
    "ark.cn-beijing.volces.com": frozenset({SITE_MEMORY_RECALL}),
}


def _host(url: Any) -> str:
    return (urllib.parse.urlsplit(str(url or "").strip()).hostname or "").lower()


def native_decision_usable(decision_config: Mapping[str, Any] | None, site: str) -> bool:
    """Whether *site* asks its question on the native decision endpoint of *decision_config*.

    *decision_config* is the resolved ``decision`` slot
    (``ConfigManager.aget_model_api_config("decision")``).
    """
    if not isinstance(decision_config, Mapping) or not str(decision_config.get("model") or "").strip():
        return False
    endpoint = str(decision_config.get("decision_url") or decision_config.get("base_url") or "")
    protocol = str(decision_config.get("decision_protocol") or "").strip().lower()
    if not protocol:
        protocol = infer_decision_protocol(decision_config.get("model"), endpoint)
    if not is_native_decision_protocol(protocol):
        return False
    host = _host(endpoint)
    if host in FREE_TIER_HOSTS and not FREE_TIER_NEW_PROMPTS_ENABLED:
        return False
    only_for = NATIVE_DECISION_SITES_BY_HOST.get(host)
    return only_for is None or site in only_for


def chat_logprobs_usable(chat_config: Mapping[str, Any] | None) -> bool:
    """Whether the chat model of *chat_config* answers with first-token probabilities."""
    if not isinstance(chat_config, Mapping) or not str(chat_config.get("model") or "").strip():
        return False
    provider_type = str(chat_config.get("provider_type") or "").strip().lower()
    if provider_type in ("anthropic", "chatgpt", "websocket"):
        return False
    host = _host(chat_config.get("base_url"))
    if host in LOGPROB_CHAT_HOSTS:
        return True
    if host in FREE_TIER_LOGPROB_HOSTS:
        return FREE_TIER_NEW_PROMPTS_ENABLED
    return False


def decide_route(
    site: str,
    *,
    decision_config: Mapping[str, Any] | None,
    chat_config: Mapping[str, Any] | None,
    rehost: bool = True,
) -> str:
    """Route of *site*: native decision first, then rehost, else the site's chat.

    *rehost* is False for a site whose question has no rehost reading (the
    owner kept those on the site's chat for vendors without native decisions).
    """
    if native_decision_usable(decision_config, site):
        return ROUTE_NATIVE
    if rehost and chat_logprobs_usable(chat_config):
        return ROUTE_REHOST
    return ROUTE_CHAT


async def ask_site_questions(
    site: str,
    *,
    state: Any,
    questions: Mapping[str, Any],
    chat_config: Mapping[str, Any] | None,
    decision_config: Mapping[str, Any] | None = None,
    config_manager: Any | None = None,
    rehost: bool = True,
    timeout: float = 8.0,
    call_type: str | None = None,
) -> dict[str, Any] | None:
    """Ask *site*'s decision *questions* on the route its vendor allows.

    Returns the answers keyed by question ID, or ``None`` when the site should
    use its own chat prompt instead: the route is ``ROUTE_CHAT``, or the native
    call / rehost missed (error, timeout, cooldown, empty answers). Native
    answers must cover every question. The native call never uses the decision
    client's own chat fallback.

    *decision_config* is read from *config_manager* (or the global one) when
    not given. A rehosted site's questions must all be ``ChoiceQuestion``.
    """
    from utils.logger_config import get_module_logger

    log = get_module_logger(__name__)
    if decision_config is None:
        try:
            cm = config_manager
            if cm is None:
                from utils.config_manager import get_config_manager

                cm = get_config_manager()
            decision_config = await cm.aget_model_api_config("decision")
        except Exception as exc:
            log.debug("[routing] %s: decision config unavailable (%s)", site, type(exc).__name__)
            decision_config = None
    route = decide_route(site, decision_config=decision_config, chat_config=chat_config, rehost=rehost)
    if route == ROUTE_CHAT:
        return None
    if route == ROUTE_REHOST:
        from .rehost import rehost_choices

        return await rehost_choices(
            state=state, questions=questions, chat_config=chat_config or {},
            timeout=timeout, call_type=call_type,
        )
    from .client import DecisionClient

    try:
        resp = await DecisionClient(config_manager=config_manager).decide(
            state=state,
            questions=questions,
            config=decision_config,
            timeout=timeout,
            allow_fallback=False,
            respect_cooldown=True,
            call_type=call_type,
        )
    except Exception as exc:
        log.info("[routing] %s: native decision failed (%s); using the site's chat prompt",
                 site, type(exc).__name__)
        return None
    answers = dict(resp.answers or {})
    if any(answers.get(qid) is None for qid in questions):
        log.info("[routing] %s: native decision left questions unanswered; using the site's chat prompt", site)
        return None
    return answers
