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

"""Whether the agent model can be reached: one small request, classified.

Task agents, and with them the keyboard-and-mouse and browser tools, are of
no use while the model in the ``agent`` slot cannot be reached. The switches
for those tools are therefore only kept on while a probe of that model
passes. The probe used to live in the keyboard-and-mouse adapter; it never
had anything to do with the keyboard or the mouse.

``AgentModelProbe.check_connectivity`` sends one request of a few tokens and
returns ``(ok, reason_code)``. The reason codes are stable identifiers the
status display and the restore path match against:

    ``AGENT_ENDPOINT_NOT_CONFIGURED``  - address or model missing
    ``AGENT_API_KEY_INVALID``          - HTTP 401/403, the key was refused
    ``AGENT_QUOTA_EXCEEDED``           - HTTP 429, quota used up
    ``AGENT_DNS_NXDOMAIN``             - the host does not resolve
    ``AGENT_LLM_UNREACHABLE``          - anything passing (timeout, 5xx, refused)

The first four are permanent (``PERMANENT_CONNECTIVITY_REASONS``): trying
again within the same minute cannot help.

It blocks, so it is called in a worker thread.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from ._shared import logger

_PERMANENT_AUTH_TOKENS = (
    "authentication",
    "invalid_api_key",
    "invalid api key",
    "unauthorized",
    "forbidden",
    " 401",
    " 403",
)
_QUOTA_TOKENS = (
    "quota",
    "rate_limit",
    "rate limit",
    " 429",
    "insufficient_quota",
)
_DNS_NXDOMAIN_TOKENS = (
    "nxdomain",
    "name or service not known",
    "getaddrinfo failed",
    "no address associated",
)

# No point in trying again right away; see the restore path in ``api_routes``.
PERMANENT_CONNECTIVITY_REASONS = frozenset({
    "AGENT_ENDPOINT_NOT_CONFIGURED",
    "AGENT_API_KEY_INVALID",
    "AGENT_QUOTA_EXCEEDED",
    "AGENT_DNS_NXDOMAIN",
})


def classify_connectivity_exception(exc: Optional[Exception]) -> str:
    """Return the reason code for a failed probe.

    The match is on the text of the exception: the SDKs fold the HTTP status
    into the message instead of exposing it as a typed field.
    """
    if exc is None:
        return "AGENT_LLM_UNREACHABLE"
    msg = str(exc).lower()
    if any(tok in msg for tok in _PERMANENT_AUTH_TOKENS):
        return "AGENT_API_KEY_INVALID"
    if any(tok in msg for tok in _QUOTA_TOKENS):
        return "AGENT_QUOTA_EXCEEDED"
    if any(tok in msg for tok in _DNS_NXDOMAIN_TOKENS):
        return "AGENT_DNS_NXDOMAIN"
    return "AGENT_LLM_UNREACHABLE"


class AgentModelProbe:
    """Probes the model in the ``agent`` slot and remembers how the last probe went."""

    def __init__(self) -> None:
        from utils.config_manager import get_config_manager

        # True once a probe has passed; False until then and after a failed one.
        self.init_ok = False
        self.last_error: Optional[str] = None
        self._config_manager = get_config_manager()
        self._client: Any = None
        self._client_sig: Optional[tuple] = None

    def model_config(self) -> Dict[str, Any]:
        return self._config_manager.get_model_api_config("agent")

    def check_connectivity(self, *, timeout_s: float = 6.0, _retries: int = 0) -> Tuple[bool, str]:
        """Send one small request to the agent model. Returns ``(ok, reason_code)``; blocking.

        One probe stays well under the gap the restore loop leaves between
        its tries, so tries never overlap.
        """
        from config import LLM_PING_MAX_TOKENS, get_agent_extra_body
        from utils.llm_client import create_chat_llm
        from utils.token_tracker import set_call_type

        cfg = self.model_config()
        api_key = cfg.get("api_key") or "EMPTY"
        base_url = cfg.get("base_url", "")
        model = cfg.get("model", "")
        provider_type = cfg.get("provider_type")
        if not base_url or not model:
            self.init_ok = False
            self.last_error = "Agent model not configured"
            return False, "AGENT_ENDPOINT_NOT_CONFIGURED"

        last_exc: Optional[Exception] = None
        for attempt in range(_retries + 1):
            try:
                current_sig = (base_url.rstrip("/"), api_key, model, provider_type)
                if self._client is None or self._client_sig != current_sig:
                    self._client = create_chat_llm(  # noqa: LLM_OUTPUT_BUDGET  # the output budget is set per call below
                        model=model,
                        base_url=base_url,
                        api_key=api_key,
                        timeout=timeout_s,
                        max_retries=0,
                        provider_type=provider_type,
                    )
                    self._client_sig = current_sig
                extra = get_agent_extra_body(model, base_url) or {}
                set_call_type("agent")
                # A reachable endpoint that answers with a well-formed response
                # passes, whatever it says; some answer with an empty message.
                self._client.invoke_raw(
                    [{"role": "user", "content": "ok"}],
                    max_completion_tokens=LLM_PING_MAX_TOKENS,
                    extra_body=extra or None,
                    timeout=timeout_s,
                )
                self.init_ok = True
                self.last_error = None
                logger.info("[Agent] agent model reachable (%s @ %s)", model, base_url)
                return True, ""
            except Exception as e:
                last_exc = e
                if attempt < _retries:
                    delay = 3 * (attempt + 1)
                    logger.info(
                        "[Agent] agent model probe %d/%d failed (%s), retry in %ds",
                        attempt + 1, _retries + 1, type(e).__name__, delay,
                    )
                    time.sleep(delay)

        self.init_ok = False
        self.last_error = str(last_exc) if last_exc else "unknown"
        reason_code = classify_connectivity_exception(last_exc)
        logger.warning(
            "[Agent] agent model probe failed after %d attempts (reason=%s): %s",
            _retries + 1, reason_code, last_exc,
        )
        return False, reason_code

    def is_available(self) -> Dict[str, Any]:
        """Return whether the agent model is configured and the last probe passed."""
        model_cfg = self.model_config()
        ok = True
        reasons: List[str] = []
        if not model_cfg.get("base_url") or not model_cfg.get("model"):
            ok = False
            reasons.append("AGENT_ENDPOINT_NOT_CONFIGURED")
        if not self.init_ok:
            ok = False
            reasons.append("AGENT_NOT_INITIALIZED")
        return {"ready": ok, "reasons": reasons, "model": model_cfg.get("model", "")}
