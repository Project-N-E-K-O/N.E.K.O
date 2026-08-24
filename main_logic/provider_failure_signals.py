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

"""One reading of "what did the provider just tell us", shared by every caller.

Three call sites classify the same provider vocabulary: the offline client's
safety check, the session manager's ``handle_connection_error`` chain, and the
realtime transport's WebSocket close-frame classifier. They used to carry
private copies of the keyword tables and of the ordering between them, which
is exactly the shape that drifts: adding a safety keyword in one place left
another silently reporting a policy block as an ordinary disconnect.

Only the CRITERIA live here. Each caller still builds its own ``details``
payload, because the payloads mean different things: the manager echoes the
upstream diagnostic text, while the realtime close path deliberately withholds
a peer-controlled reason string and substitutes a stable descriptor.
"""

from __future__ import annotations


_API_KEY_REJECTED_KEYWORDS = (
    "incorrect api key",
    "incorect api key",
    "invalid_api_key",
    "invalid api key",
    "invalid key",
    "api key is invalid",
)

# Mostly Gemini's ``finishReason`` / ``blockReason`` enum, lowercased, plus the
# OpenAI and Azure spellings. The primary caller (_streaming's
# ``_is_safety_violation_signal(finish_reason, block_reason)``) matches against
# those enum VALUES, not against free-form prose — which is why entries like
# "language" and "recitation" are single common words and must stay: they are
# Gemini's LANGUAGE and RECITATION finish reasons verbatim, and dropping one
# sends that block into the unknown-error fallback instead of the policy toast.
# The free-text callers (a provider error message, a WebSocket close reason)
# inherit the same table and therefore the same substring behaviour they have
# always had; tightening THAT is a matching-strategy change for all callers at
# once, not a keyword edit.
_SAFETY_VIOLATION_KEYWORDS = (
    "safety",
    "content_filter",
    "content filter",
    "policy violation",
    "policy_violation",
    "blocklist",
    "prohibited",
    "prohibited_content",
    "recitation",
    "spii",
    "language",
    "image_safety",
    "image_prohibited_content",
    "image_recitation",
    "responsibleaipolicyviolation",
    "responsible ai policy",
)

_ARREARS_KEYWORDS = ("欠费", "standing")

_QUOTA_KEYWORDS = ("quota", "time limit")

_RATE_LIMIT_KEYWORDS = ("429", "too many")

_KEY_REJECTED_KEYWORDS = (
    "401",
    "unauthorized",
    "authentication",
    "incorrect api key",
    "invalid_api_key",
)


def _is_safety_violation_signal(*values: object) -> bool:
    """Return True when provider diagnostics point to safety/policy blocking."""
    text = " ".join(str(value) for value in values if value).lower()
    if not text:
        return False
    return any(keyword in text for keyword in _SAFETY_VIOLATION_KEYWORDS)


def _is_key_rejected_signal(text_lower: str) -> bool:
    """Return True when the text reads as a rejected API key."""
    if any(keyword in text_lower for keyword in _KEY_REJECTED_KEYWORDS):
        return True
    return "invalid" in text_lower and "key" in text_lower


def classify_provider_failure_text(text: object) -> str | None:
    """Map provider diagnostic text to a stable frontend status code.

    Returns ``None`` when nothing in the text identifies a known failure
    class, leaving the caller to pick its own fallback: the manager reports
    ``API_UNKNOWN_ERROR`` because it is holding a real upstream error, while
    the realtime close path reports ``CHARACTER_DISCONNECTED`` because a close
    frame it cannot classify is an ordinary disconnect, not an API error.

    The ordering is load-bearing and predates this module: arrears before
    quota (an unpaid account also reports a quota), rate limit before key
    rejection (a 429 body often echoes auth headers), and safety last of the
    keyword classes because its vocabulary is the broadest.
    """

    raw = str(text or "")
    if not raw:
        return None
    lowered = raw.lower()
    if any(keyword in lowered for keyword in _ARREARS_KEYWORDS):
        return "API_ARREARS"
    if any(keyword in lowered for keyword in _QUOTA_KEYWORDS):
        return "API_QUOTA_TIME"
    if any(keyword in lowered for keyword in _RATE_LIMIT_KEYWORDS):
        return "API_RATE_LIMIT"
    if _is_key_rejected_signal(lowered):
        return "API_KEY_REJECTED"
    if _is_safety_violation_signal(lowered):
        return "API_POLICY_VIOLATION"
    if "1008" in lowered:
        return "API_1008_FALLBACK"
    return None


# Codes whose frontend i18n string interpolates ``{{msg}}``. A caller emitting
# one of these without a ``msg`` detail ships a toast that renders the raw
# placeholder, so every producer must supply one — either the upstream text it
# already holds, or a stable descriptor when the upstream text must not be
# shown.
CODES_REQUIRING_MSG_DETAIL = frozenset(
    {
        "API_POLICY_VIOLATION",
        "API_1008_FALLBACK",
        "API_UNKNOWN_ERROR",
    }
)
