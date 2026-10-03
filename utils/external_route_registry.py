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

"""Registry of external routes that can take over a character's input.

An external route is a controller outside the ordinary chat session (today
only the mini-game route) that, while active, owns the character's typed and
spoken input. Before this registry every hijack point imported the game
router directly; a second kind of controller would have had to copy each of
those branches. Hijack points now ask the registry instead:

- input hijack (``websocket_router`` stream_data / start_session, the
  auto-start gate in ``main_logic/core/streaming.py``, the independent ASR
  voice consumer) and the proactive / context-prompt gates look only at
  ``is_active``;
- slot checks (a new route asking whether it may start) look at
  ``is_locked``, which a kind can keep true while its exit flow is still
  running after ``is_active`` has turned false.

``is_character_lifecycle_locked`` is the predicate for a character
rename / delete guard; no endpoint consults it yet.

The registry stores callables only. It lives in ``utils/`` so that
``main_logic/`` can consult it without importing ``main_routers/``; route
owners register themselves when their module is imported.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)


@dataclass(frozen=True)
class ExternalRouteKind:
    """Callables one kind of external route plugs into the shared hijack points.

    ``route_voice_transcript`` receives ``(lanlan_name, transcript, **route_kwargs)``
    exactly as the independent ASR consumer passes them, so a kind can register
    its pre-existing handler object unchanged. ``current_instance`` (required)
    returns an opaque id of the route instance currently active for the
    character (e.g. a session id): every dispatch that awaits a handler
    re-checks ``(kind, instance)`` afterwards, and independent-ASR turns are
    pinned to it, so a decision or utterance of one instance never carries over
    to the next instance of the same kind. ``is_locked`` defaults to
    ``is_active``; ``has_background_tasks`` defaults to "never".

    Microphone PCM on the main socket is announced to the route as
    ``{"input_type": "audio", "stt_provider": "realtime"}``. When the route
    returns True the PCM is dropped, unless the kind sets ``audio_passthrough``,
    in which case it keeps flowing to the ordinary realtime session.

    ``on_start_session`` lets a kind decide a frontend session start itself.
    A kind that leaves it None gets the default start handling while active:
    a text start is only acknowledged (no ordinary text session starts), and an
    audio start announces ``stt_provider="realtime"`` to the route and starts
    the ordinary realtime session as the route's speech-to-text provider. That
    session is useless unless the microphone PCM reaches it, so such a kind
    must set ``audio_passthrough`` (checked at registration).
    """

    kind: str
    is_active: Callable[[str], bool]
    route_stream_message: Callable[[str, dict], Awaitable[bool]]
    on_start_session: Callable[[str, dict], Awaitable[bool]] | None
    finalize_for_character: Callable[[str], Awaitable[int]]
    route_voice_transcript: Callable[..., Awaitable[bool]] | None = None
    on_page_signal: Callable[[str, dict], Awaitable[bool]] | None = None
    is_locked: Callable[[str], bool] | None = None
    has_background_tasks: Callable[[str], bool] | None = None
    current_instance: Callable[[str], str | None] | None = None  # required at registration
    audio_passthrough: bool = False


# Registration order is lookup order. Kinds are expected to be mutually
# exclusive per character (each refuses to start while another is locked), so
# the order only matters as a deterministic tie-break.
_kinds: Dict[str, ExternalRouteKind] = {}


def register_external_route_kind(spec: ExternalRouteKind) -> None:
    """Register (or replace, e.g. on module reload) one route kind."""
    if not isinstance(spec, ExternalRouteKind):
        raise TypeError("spec must be an ExternalRouteKind")
    if not isinstance(spec.kind, str) or not spec.kind.strip():
        raise ValueError("ExternalRouteKind.kind must be a non-empty string")
    if spec.current_instance is None:
        raise ValueError("ExternalRouteKind must provide current_instance")
    if spec.on_start_session is None and not spec.audio_passthrough:
        raise ValueError(
            "ExternalRouteKind without on_start_session must set audio_passthrough: "
            "the default audio start runs ordinary realtime as the route's STT"
        )
    _kinds[spec.kind] = spec


def _registered_kinds() -> tuple[ExternalRouteKind, ...]:
    return tuple(_kinds.values())


def _kind_is_locked(spec: ExternalRouteKind, lanlan_name: str) -> bool:
    predicate = spec.is_locked if spec.is_locked is not None else spec.is_active
    return bool(predicate(lanlan_name))


def get_active_external_route(lanlan_name: str) -> ExternalRouteKind | None:
    """Return the kind whose route currently owns ``lanlan_name``'s input."""
    for spec in _registered_kinds():
        if spec.is_active(lanlan_name):
            return spec
    return None


def external_route_identity(lanlan_name: str) -> tuple[ExternalRouteKind, str | None] | None:
    """The active route and its instance id (if the kind reports one), or None.

    Lets a caller that awaited on the route check afterwards that the same
    route instance -- not just the same kind -- still owns the character.
    """
    spec = get_active_external_route(lanlan_name)
    if spec is None:
        return None
    instance = spec.current_instance(lanlan_name) if spec.current_instance is not None else None
    return spec, instance


def same_external_route_owner(
    before: tuple[ExternalRouteKind, str | None] | None,
    after: tuple[ExternalRouteKind, str | None] | None,
) -> bool:
    """True when two ``external_route_identity`` reads name the same owner.

    No route on both sides counts as the same owner. An active route whose
    instance id is empty or not a string cannot be pinned, so it never counts
    as unchanged: callers fail closed instead of trusting a stale decision.
    """
    if before is None or after is None:
        return before is None and after is None
    instance = before[1]
    if not isinstance(instance, str) or not instance:
        return False
    return before == after


def is_external_route_active(lanlan_name: str) -> bool:
    """True iff some registered kind currently owns ``lanlan_name``'s input."""
    return get_active_external_route(lanlan_name) is not None


def is_external_route_locked(
    lanlan_name: str,
    *,
    exclude_kind: str | None = None,
) -> bool:
    """True iff any kind other than ``exclude_kind`` occupies the character slot.

    A kind without ``is_locked`` is locked exactly while it is active. Callers
    starting a route pass their own kind: a same-kind predecessor is replaced
    by that kind's own supersede logic (e.g. one mini-game opening over
    another), so it must not block the start.
    """
    for spec in _registered_kinds():
        if exclude_kind is not None and spec.kind == exclude_kind:
            continue
        if _kind_is_locked(spec, lanlan_name):
            return True
    return False


def is_route_slot_taken(
    lanlan_name: str,
    *,
    kind: str,
    takeover_owner: str | None = None,
) -> bool:
    """True when something other than ``kind`` occupies ``lanlan_name``'s slot.

    Shared by every place that decides whether a route of ``kind`` may start:
    another registered kind still locks the slot, or the session takeover is
    held by a different owner (which would make ``kind``'s own acquire fail).
    Same-kind predecessors are left to that kind's own supersede logic.
    """
    if is_external_route_locked(lanlan_name, exclude_kind=kind):
        return True
    return takeover_owner not in (None, kind)


def is_character_lifecycle_locked(lanlan_name: str) -> bool:
    """Predicate for a character rename / delete guard (no endpoint uses it yet).

    Besides an occupied slot, a kind may still be writing data keyed to this
    character in the background after its route ended. Those tasks would
    block rename / delete but never block starting a new route.
    """
    if is_external_route_locked(lanlan_name):
        return True
    for spec in _registered_kinds():
        if spec.has_background_tasks is not None and spec.has_background_tasks(lanlan_name):
            return True
    return False


# How many times an offer follows an owner change before it is given up.
_STREAM_MESSAGE_MAX_OWNER_CHANGES = 2

# Result of ``_offer_to_current_owner`` when the owner kept changing.
_UNSETTLED = object()


async def _offer_to_current_owner(
    lanlan_name: str,
    offer: Callable[[ExternalRouteKind], Awaitable[tuple[Any, bool]]],
    *,
    what: str,
) -> tuple[ExternalRouteKind | None, Any]:
    """Run ``offer`` against the route that owns ``lanlan_name`` until it sticks.

    ``offer(spec)`` returns ``(result, final)``. A final result stands as is.
    Otherwise the handler may have suspended, and an answer from a route
    instance that has since been replaced does not speak for the current
    owner: the offer is repeated against whoever owns the character now.
    Returns ``(spec, result)`` of the owner whose answer stands, ``(None, None)``
    when no route owns the character, or ``(None, _UNSETTLED)`` when the owner
    kept changing. Each owner read is reused as the next attempt's starting
    point, so a steady owner costs one read before and one after.
    """
    identity = external_route_identity(lanlan_name)
    for _ in range(_STREAM_MESSAGE_MAX_OWNER_CHANGES + 1):
        if identity is None:
            return None, None
        spec = identity[0]
        result, final = await offer(spec)
        if final:
            return spec, result
        current = external_route_identity(lanlan_name)
        if same_external_route_owner(identity, current):
            return spec, result
        logger.info(
            "external route changed while handling %s: lanlan=%s kind=%s",
            what,
            lanlan_name,
            spec.kind,
        )
        identity = current
    return None, _UNSETTLED


async def route_external_stream_message(lanlan_name: str, message: dict) -> bool:
    """Offer a main-socket ``stream_data`` message to the active route.

    Returns True when the route consumed it (the caller must then skip the
    ordinary chat path). A "not consumed" from a route instance replaced while
    it decided is re-offered to the current owner (or, with no owner left, the
    message goes to the ordinary path). An owner that keeps changing gets the
    message dropped rather than leaked into ordinary chat.
    """
    async def offer(spec: ExternalRouteKind) -> tuple[bool, bool]:
        consumed = bool(await spec.route_stream_message(lanlan_name, message))
        return consumed, consumed

    _spec, consumed = await _offer_to_current_owner(lanlan_name, offer, what="stream_data")
    if consumed is _UNSETTLED:
        return True
    return bool(consumed)


async def route_external_microphone_audio(lanlan_name: str) -> bool:
    """Announce microphone PCM to the active route.

    Returns True when the PCM must not reach the ordinary session: the route
    consumed the announcement and does not declare ``audio_passthrough``. The
    decision (and passthrough rule) of a route replaced while it decided does
    not apply to the current owner, which is asked again.
    """
    async def offer(spec: ExternalRouteKind) -> tuple[bool, bool]:
        consumed = await spec.route_stream_message(
            lanlan_name, {"input_type": "audio", "stt_provider": "realtime"},
        )
        return bool(consumed), False

    spec, consumed = await _offer_to_current_owner(lanlan_name, offer, what="microphone audio")
    if consumed is _UNSETTLED:
        return True
    return spec is not None and bool(consumed) and not spec.audio_passthrough


class StartSessionClaim(Enum):
    """How the external routes answered a session start."""

    CLAIMED = "claimed"  # a route took the start; the caller does nothing more
    UNCLAIMED = "unclaimed"  # see route_external_start_session for the route
    UNSETTLED = "unsettled"  # the owner kept changing; the caller gives up


async def route_external_start_session(
    lanlan_name: str,
    message: dict,
) -> tuple[StartSessionClaim, ExternalRouteKind | None]:
    """Let the route owning ``lanlan_name`` decide a session start.

    ``(UNCLAIMED, None)``: no route owns the character, or the owner declined;
    the ordinary start runs. ``(UNCLAIMED, spec)``: the owner has no
    ``on_start_session``, so the default start handling for ``spec`` applies
    (see ``ExternalRouteKind``). A decline from a route replaced while it
    decided is re-asked of the current owner, like stream messages.
    """
    async def offer(spec: ExternalRouteKind) -> tuple[bool, bool]:
        if spec.on_start_session is None:
            return False, True
        claimed = bool(await spec.on_start_session(lanlan_name, message))
        return claimed, claimed

    spec, claimed = await _offer_to_current_owner(lanlan_name, offer, what="start_session")
    if claimed is _UNSETTLED:
        return StartSessionClaim.UNSETTLED, None
    if claimed:
        return StartSessionClaim.CLAIMED, spec
    if spec is not None and spec.on_start_session is None:
        return StartSessionClaim.UNCLAIMED, spec
    return StartSessionClaim.UNCLAIMED, None


async def route_external_voice_transcript(
    lanlan_name: str,
    transcript: str,
    **route_kwargs: Any,
) -> bool:
    """Deliver an independent-ASR final transcript to the active route."""
    spec = get_active_external_route(lanlan_name)
    if spec is None or spec.route_voice_transcript is None:
        return False
    return bool(await spec.route_voice_transcript(lanlan_name, transcript, **route_kwargs))


async def route_external_page_signal(lanlan_name: str, message: dict) -> bool:
    """Deliver a page signal (e.g. speech progress) to whichever kind claims it.

    The active route is asked first. Signals can also outlive a route (speech
    that keeps playing after the route ended), so every other kind with a
    handler is asked next; the first one that returns True wins.
    """
    active = get_active_external_route(lanlan_name)
    candidates = []
    if active is not None:
        candidates.append(active)
    candidates.extend(spec for spec in _registered_kinds() if spec is not active)
    for spec in candidates:
        if spec.on_page_signal is None:
            continue
        if await spec.on_page_signal(lanlan_name, message):
            return True
    return False


async def finalize_external_routes_for_character(lanlan_name: str) -> int:
    """Finalize every kind's routes for ``lanlan_name`` (character switch).

    Each kind only waits for its own state flip, not for its whole exit flow.
    A failing kind is logged and skipped so the remaining kinds still run.
    Returns the total number of routes finalized.
    """
    total = 0
    for spec in _registered_kinds():
        try:
            total += int(await spec.finalize_for_character(lanlan_name) or 0)
        except Exception as exc:
            logger.warning(
                "external route finalize failed: kind=%s lanlan=%s err=%s",
                spec.kind,
                lanlan_name,
                exc,
                exc_info=True,
            )
    return total


def _snapshot_for_tests() -> Dict[str, ExternalRouteKind]:
    return dict(_kinds)


def _restore_for_tests(snapshot: Dict[str, ExternalRouteKind]) -> None:
    _kinds.clear()
    _kinds.update(snapshot)


def _reset_for_tests() -> None:
    _kinds.clear()
