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

"""Mini-game invitation and proactive-source decay settings."""

MINI_GAME_INVITE_ENABLED = True
"""Master switch for the mini-game invitation shortcut (enabled by default).
- Purpose: after proactive_chat passes propensity / skip_probability / restricted_screen_only
  gates, use MINI_GAME_INVITE_TRIGGER_PROBABILITY to shortcut to inviting the player to a
  mini-game, skipping Phase 1/2 LLM. Disabling this switch prevents the branch entirely, returning proactive_chat
  to purely source-driven behavior.
- Upstream: main_routers/system_router._maybe_deliver_mini_game_invite."""

MINI_GAME_INVITE_TRIGGER_PROBABILITY = 0.12
"""Probability that each eligible proactive chat takes the mini-game invitation shortcut.
- Values: [0.0, 1.0]; 0.0 disables it (equivalent to ENABLED=False), 1.0 always invites.
- Upstream: random.random() < this value -> hit -> take the invitation shortcut."""

MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS = 2 * 3600
"""Minimum quiet period in seconds after accept (default 2h).
- Combined with MINI_GAME_INVITE_COOLDOWN_CHATS: both thresholds must pass before rolling again.
- Upstream: time check in _mini_game_invite_in_cooldown (state.last_response_choice='accept').
- History: originally a shared 1h (reduced from 24h in PR follow-up #1), later split into accept/decline
  thresholds: accept feels like just finishing a game, so shorter (2h); decline signals disinterest, extended to 5h
  to avoid repeated short-term interruptions. Both still share the same 10-chat gate."""

MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS = 5 * 3600
"""Minimum quiet period in seconds after decline (default 5h).
- Combined with MINI_GAME_INVITE_COOLDOWN_CHATS: both thresholds must pass before rolling again.
- Upstream: time check in _mini_game_invite_in_cooldown (state.last_response_choice='decline').
- Longer than accept because decline explicitly signals no desire to play, making quick repeat invitations unpleasant; 5h exceeds
  the usual asking-again-minutes-after-rejection window without imposing silence for a whole day."""

MINI_GAME_INVITE_NEW_USER_FORCE_AT = 4
"""Force a mini-game invitation on a new user's Nth successfully delivered proactive chat.
- New user = ``state.delivered_at is None`` (per character, no invitation ever delivered).
- N is an integer >=1; when persisted ``proactive_chat_total >= N - 1``,
  force this delivery (bypassing the 10% roll while still respecting propensity / working status /
  unfinished_thread / cooldown and other gates).
- Default 4: after three ordinary proactive chats are successfully received, the fourth becomes a game invitation,
  ensuring users who have never played one invitation opportunity without relying on a 10% roll.
- Upstream: force-first branch in _maybe_deliver_mini_game_invite."""

MINI_GAME_INVITE_AVAILABLE_GAMES: tuple[str, ...] = (
    "soccer",
    "badminton",
    "drawing_guess",
    "air_basketball",
    "watch-together",
)
"""Available game_type list for mini-game invitations.
- On a hit, select one via random.choice and retrieve its text from
  config.prompts.prompts_proactive.MINI_GAME_INVITE_LINES_BY_GAME[game_type].
- Supports soccer, badminton, draw-and-guess, air basketball, and watch-together; new games require both invitation text and a page URL.
- Order is irrelevant (random.choice); use a tuple to prevent runtime mutation."""

MINI_GAME_INVITE_COOLDOWN_CHATS = 10
"""Number of further successfully delivered proactive chats required after an invitation response.
- Both this and MINI_GAME_INVITE_COOLDOWN_AFTER_{ACCEPT,DECLINE}_SECONDS must pass;
  failure of either keeps suppression active. The chat gate is shared across accept/decline, not split by choice.
- Upstream: count check in _mini_game_invite_in_cooldown."""

MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS = 5 * 60
"""Short-term reroll suppression in seconds after the user chooses later (default 5min).
- D2 semantics: reset state (clear delivered_at/responded_at/chats_since_response,
  restoring force-first and ordinary 10% rolls), but add a ``suppressed_until`` soft gate;
  ``_mini_game_invite_in_cooldown`` still returns True during this period, preventing another immediate proactive
  invitation, effectively ask me later. Only after this window does the next proactive chat roll again.
- Upstream: 'later' action at endpoint /api/mini_game/invite/respond."""

MINI_GAME_LAUNCH_URL_BY_GAME: dict[str, str] = {
    'soccer': '/soccer_demo',
    'badminton': '/badminton_demo',
    'air_basketball': '/air_basketball',
    'watch-together': '/watch_together',
    'drawing_guess': '/drawing_guess_demo',
}
"""game_type -> actual page URL. Frontend `window.open(url)` is intercepted by Electron's main-process
``setWindowOpenHandler`` to create a separate BrowserWindow (a new tab in ordinary browsers); URLs
include ``?lanlan_name=...&session_id=...`` query parameters. Add an entry for each new mini-game."""

MINI_GAME_INVITE_FORCE_GAME_TYPE: str | None = None
"""[Temporary debugging flag] When non-None, every eligible proactive chat takes the mini-game invitation shortcut,
using this value as game_type, bypassing activity_snapshot / propensity / away /
unfinished_thread / cooldown / probability / force-first / user toggle and all other
gates; only the ``MINI_GAME_INVITE_ENABLED`` master switch remains as a final kill switch.
- Values: None disables it (production default); otherwise a valid key such as 'soccer' in ``MINI_GAME_INVITE_LINES_BY_GAME``.
  Invalid keys produce a warning and skip delivery.
- Purpose: manually testing the three-context UI locally without waiting for N-1 proactive chats for force-first or
  repeatedly restarting fixtures to adjust cooldowns. Do not enable in production.
- Upstream: ``main_routers/system_router._maybe_deliver_mini_game_invite``."""

PROACTIVE_SOURCE_HARD_SKIP_SECONDS = 5 * 3600
"""Hard window for proactive-chat source decay history (p_skip=1.0).
- Purpose: always skip the same URL within 5h; afterward decay exponentially using its kind's half-life.
- Upstream: system_router._should_skip_source."""

PROACTIVE_SOURCE_HALF_LIFE_BY_KIND: dict[str, float] = {
    'web': 3 * 86400.0,
    'image': 3 * 86400.0,
    'music': 1 * 86400.0,
}
"""Per-kind p_skip half-life in seconds outside the hard window.
- web/image: 3d (news / memes have relatively low repetition costs and gradually return).
- music: 1d (small library, more frequent rotation).
- Purpose: lookup by system_router._half_life_for."""

PROACTIVE_SOURCE_HALF_LIFE_DEFAULT = 3 * 86400.0
"""Fallback half-life when _BY_KIND has no match."""

PROACTIVE_SOURCE_FORGET_P = 0.05
"""Forget a source from decay history when p_skip falls below this threshold (naturally bounding file size).
- With current parameters: forget music after ≈4.5d, web/image after ≈13d."""

EMOTION_ANALYSIS_MAX_TOKENS = 64
"""Sentiment-analysis LLM max_completion_tokens.
- Purpose: return sentiment labels, confidence, and optional emoji.
- Upstream: LLM output (including possible Gemini markdown wrappers)."""


# Reuse the existing outward emotion decision; no additional inference.
MESSAGE_REACTION_CONFIDENCE_THRESHOLD = 0.72
MESSAGE_REACTION_EMOJIS_BY_EMOTION = {
    "happy": ("😊", "😄", "🥰", "✨", "🎉"),
    "sad": ("😢", "🥺", "🤗", "💧"),
    "surprised": ("😮", "😲", "👀", "❗"),
    "angry": ("😤", "😠", "💢", "😾"),
}
