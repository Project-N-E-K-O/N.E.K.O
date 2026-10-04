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
"""Ownership of a session takeover for ``LLMSessionManager``.

An external controller (a mini-game route today) can take over the session:
ordinary chat output is muted, voice transcripts go to its dispatcher first
and respond-type plugin callbacks can go to its sink. That state is three
attributes -- ``_takeover_active``, ``_takeover_input_dispatcher`` and
``_takeover_callback_sink`` -- and this mixin is their only writer. Each
acquire hands out a ``TakeoverToken``; only the current token releases the
takeover, so one controller can no longer unmute or overwrite another.

Re-acquiring under the SAME owner replaces the token (a mini-game route
superseding the previous one); the stale token's later release is a no-op.
A DIFFERENT owner raises ``TakeoverOwned``.

Independently of takeover, ``hold_callbacks`` parks respond-type callbacks in
a sink after the takeover itself has been released, so a controller can hand
the conversation back to ordinary chat before re-delivering what it parked.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from ._shared import logger


@dataclass(frozen=True, eq=False)
class TakeoverToken:
    """Proof of holding the takeover; compared by identity, never by value."""

    owner: str
    issued_at: float


@dataclass(frozen=True, eq=False)
class HoldToken:
    """Proof of holding the callback hold; compared by identity."""

    owner: str
    issued_at: float


class TakeoverOwned(RuntimeError):
    """Raised when a different owner already holds the takeover."""

    def __init__(self, requested_owner: str, current_owner: str):
        super().__init__(
            f"takeover requested by {requested_owner!r} is held by {current_owner!r}"
        )
        self.requested_owner = requested_owner
        self.current_owner = current_owner


class TakeoverMixin:
    """Takeover ownership and callback hold (see module docstring)."""

    def acquire_takeover(
        self,
        owner: str,
        dispatcher: Optional[Callable[..., Awaitable[bool]]],
        *,
        callback_sink: Optional[Callable[[dict], bool]] = None,
    ) -> TakeoverToken:
        """Take over the session for ``owner`` and return the releasing token.

        Call ``interrupt_ordinary_speech_for_takeover`` afterwards when the
        controller speaks on its own.
        """
        owner = str(owner or "").strip()
        if not owner:
            raise ValueError("takeover owner must be a non-empty string")
        current = getattr(self, "_takeover_token", None)
        if current is not None and current.owner != owner:
            raise TakeoverOwned(owner, current.owner)
        if current is not None:
            logger.info(
                "[%s] takeover re-acquired by the same owner, previous token retired: owner=%s",
                getattr(self, "lanlan_name", ""),
                owner,
            )
        token = TakeoverToken(owner=owner, issued_at=time.monotonic())
        self._takeover_token = token
        self._takeover_active = True
        self._takeover_input_dispatcher = dispatcher
        self._takeover_callback_sink = callback_sink
        return token

    def set_takeover_callback_sink(
        self,
        token: Optional[TakeoverToken],
        sink: Optional[Callable[[dict], bool]],
    ) -> bool:
        """Install (or clear) the respond-callback sink of the takeover ``token`` holds.

        For controllers that acquire first and build their inbox afterwards.
        Returns False (and changes nothing) when ``token`` is not current.
        """
        if token is None or token is not getattr(self, "_takeover_token", None):
            logger.warning(
                "[%s] takeover callback sink not set: token is not current (owner=%s)",
                getattr(self, "lanlan_name", ""),
                getattr(token, "owner", None),
            )
            return False
        self._takeover_callback_sink = sink
        return True

    def release_takeover(
        self,
        token: Optional[TakeoverToken],
        *,
        force: bool = False,
    ) -> bool:
        """Release the takeover held by ``token``; clears all three attributes together.

        A token that is not current (stale, already released, or None) leaves
        the takeover untouched and returns False. ``force=True`` clears it
        anyway -- only for rollback paths that must not leave the session
        muted -- and still returns False for a non-current token.
        """
        current = getattr(self, "_takeover_token", None)
        is_current = token is not None and token is current
        if not is_current:
            if token is not None:
                logger.warning(
                    "[%s] takeover release ignored: token is not current (owner=%s, current=%s)",
                    getattr(self, "lanlan_name", ""),
                    token.owner,
                    getattr(current, "owner", None),
                )
            if not force:
                return False
            logger.error(
                "[%s] takeover force-released by a non-current token (owner=%s, current=%s)",
                getattr(self, "lanlan_name", ""),
                getattr(token, "owner", None),
                getattr(current, "owner", None),
            )
        self._takeover_token = None
        self._takeover_active = False
        self._takeover_input_dispatcher = None
        self._takeover_callback_sink = None
        return is_current

    def takeover_owner(self) -> Optional[str]:
        """Owner of the current takeover, or None when nobody holds it."""
        token = getattr(self, "_takeover_token", None)
        return token.owner if token is not None else None

    def hold_callbacks(
        self,
        sink: Callable[[dict], bool],
        *,
        owner: str = "callback_hold",
    ) -> HoldToken:
        """Park respond-type callbacks in ``sink`` until ``release_callback_hold``.

        Independent of the takeover: it keeps working after the takeover is
        released. A takeover sink, while installed, still sees callbacks first.
        A new hold replaces the previous one (its token then releases nothing).
        """
        if not callable(sink):
            raise TypeError("callback hold sink must be callable")
        previous = getattr(self, "_callback_hold_token", None)
        if previous is not None:
            logger.warning(
                "[%s] callback hold replaced: previous owner=%s, new owner=%s",
                getattr(self, "lanlan_name", ""),
                previous.owner,
                owner,
            )
        token = HoldToken(owner=str(owner or "callback_hold"), issued_at=time.monotonic())
        self._callback_hold_token = token
        self._callback_hold_sink = sink
        return token

    def release_callback_hold(self, token: Optional[HoldToken]) -> bool:
        """Stop parking callbacks; a non-current ``token`` changes nothing."""
        if token is None or token is not getattr(self, "_callback_hold_token", None):
            if token is not None:
                logger.warning(
                    "[%s] callback hold release ignored: token is not current (owner=%s)",
                    getattr(self, "lanlan_name", ""),
                    token.owner,
                )
            return False
        self._callback_hold_token = None
        self._callback_hold_sink = None
        return True
