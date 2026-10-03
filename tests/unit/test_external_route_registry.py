"""External-route registry: lookup semantics, the game registration, and the
hijack points that now go through it (design doc §5 PR-01)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_input.consumers.game import GameVoiceInputConsumer
from main_logic.voice_turn.contracts import (
    VoiceIngressToken,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)
from main_routers import game_router
from main_routers.game_router import runtime as gr_runtime
from utils import external_route_registry as registry
from utils.external_route_registry import ExternalRouteKind

from .game_route_test_helpers import gr_patch_all, reset_game_route_state


async def _no_routes(_name: str) -> int:
    return 0


async def _unclaimed(_name: str, _message: dict) -> bool:
    return False


def _kind(
    kind: str,
    *,
    active: bool = False,
    locked: bool | None = None,
    background: bool | None = None,
    on_start_session=None,
    finalize=_no_routes,
    route_stream_message=_unclaimed,
    on_page_signal=None,
    route_voice_transcript=None,
    instance=None,
) -> ExternalRouteKind:
    return ExternalRouteKind(
        kind=kind,
        is_active=lambda _name: active,
        route_stream_message=route_stream_message,
        on_start_session=on_start_session,
        finalize_for_character=finalize,
        route_voice_transcript=route_voice_transcript,
        on_page_signal=on_page_signal,
        is_locked=None if locked is None else (lambda _name: locked),
        has_background_tasks=None if background is None else (lambda _name: background),
        current_instance=lambda _name: "instance-1" if instance is None else instance,
        audio_passthrough=on_start_session is None,
    )


@pytest.fixture
def empty_registry():
    # conftest restores the import-time registrations after the test.
    registry._reset_for_tests()
    yield registry


def test_lookup_returns_the_active_kind_and_none_otherwise(empty_registry):
    assert registry.get_active_external_route("Lan") is None
    assert registry.is_external_route_active("Lan") is False

    idle = _kind("idle")
    busy = _kind("busy", active=True)
    registry.register_external_route_kind(idle)
    registry.register_external_route_kind(busy)

    assert registry.get_active_external_route("Lan") is busy
    assert registry.is_external_route_active("Lan") is True


def test_reregistering_a_kind_replaces_it(empty_registry):
    registry.register_external_route_kind(_kind("visit", active=True))
    replacement = _kind("visit", active=False)
    registry.register_external_route_kind(replacement)

    assert registry._snapshot_for_tests() == {"visit": replacement}
    assert registry.get_active_external_route("Lan") is None


@pytest.mark.parametrize("active", [True, False])
def test_kind_without_is_locked_is_locked_exactly_while_active(empty_registry, active):
    registry.register_external_route_kind(_kind("game", active=active))

    assert registry.is_external_route_locked("Lan") is active
    assert registry.is_external_route_locked("Lan") is registry.is_external_route_active("Lan")


def test_locked_but_inactive_kind_holds_the_slot_without_hijacking_input(empty_registry):
    # A kind still running its exit flow: input is back to ordinary chat, the
    # slot is still taken. Mutation: is_external_route_locked only checking
    # is_active turns this red.
    registry.register_external_route_kind(_kind("visit", active=False, locked=True))

    assert registry.get_active_external_route("Lan") is None
    assert registry.is_external_route_active("Lan") is False
    assert registry.is_external_route_locked("Lan") is True


def test_background_tasks_lock_the_character_lifecycle_but_not_the_slot(empty_registry):
    # Mutation: folding has_background_tasks into is_external_route_locked turns
    # this red (a pending summary write would then refuse a new route start).
    registry.register_external_route_kind(
        _kind("visit", active=False, locked=False, background=True)
    )

    assert registry.is_external_route_locked("Lan") is False
    assert registry.is_character_lifecycle_locked("Lan") is True


def test_lifecycle_lock_follows_the_slot_lock(empty_registry):
    registry.register_external_route_kind(_kind("visit", locked=True))
    assert registry.is_character_lifecycle_locked("Lan") is True

    registry.register_external_route_kind(_kind("visit", locked=False))
    assert registry.is_character_lifecycle_locked("Lan") is False


def test_exclude_kind_skips_only_the_callers_own_kind(empty_registry):
    # Mutation: ignoring exclude_kind turns this red (a mini-game could no
    # longer replace another mini-game).
    registry.register_external_route_kind(_kind("game", active=True))
    assert registry.is_external_route_locked("Lan", exclude_kind="game") is False
    assert registry.is_external_route_locked("Lan") is True

    # Another kind still finishing its exit flow blocks both.
    registry.register_external_route_kind(_kind("game", active=False))
    registry.register_external_route_kind(_kind("visit", active=False, locked=True))
    assert registry.is_external_route_locked("Lan", exclude_kind="game") is True
    assert registry.is_external_route_locked("Lan") is True


@pytest.mark.asyncio
async def test_start_session_claims_need_an_active_route_with_a_handler(empty_registry):
    start = registry.route_external_start_session
    Claim = registry.RouteClaim
    assert await start("Lan", {"input_type": "audio"}) == (Claim.UNCLAIMED, None)

    # No handler: default start handling for that route.
    game = _kind("game", active=True)
    registry.register_external_route_kind(game)
    assert await start("Lan", {"input_type": "audio"}) == (Claim.UNCLAIMED, game)

    claim = AsyncMock(return_value=True)
    visit = _kind("visit", active=True, on_start_session=claim)
    registry.register_external_route_kind(visit)
    registry.register_external_route_kind(_kind("game", active=False))
    assert await start("Lan", {"input_type": "audio"}) == (Claim.CLAIMED, visit)
    claim.assert_awaited_once_with("Lan", {"input_type": "audio"})

    # A decline lets the ordinary start run.
    claim.return_value = False
    assert await start("Lan", {"input_type": "audio"}) == (Claim.UNCLAIMED, None)


def test_a_kind_without_start_handler_must_pass_audio_through(empty_registry):
    # Its default audio start runs ordinary realtime as STT, which gets no PCM
    # without passthrough. Mutation: dropping the registration check turns this red.
    with pytest.raises(ValueError, match="audio_passthrough"):
        registry.register_external_route_kind(ExternalRouteKind(
            kind="visit",
            is_active=lambda _name: True,
            route_stream_message=_unclaimed,
            on_start_session=None,
            finalize_for_character=_no_routes,
            current_instance=lambda _name: "visit-1",
        ))


@pytest.mark.asyncio
async def test_stream_message_goes_to_the_active_route_only(empty_registry):
    assert await registry.route_external_stream_message("Lan", {"input_type": "text"}) is registry.RouteClaim.UNCLAIMED

    handler = AsyncMock(return_value=True)
    registry.register_external_route_kind(
        _kind("game", active=True, route_stream_message=handler)
    )
    message = {"input_type": "text", "data": "hi"}

    assert await registry.route_external_stream_message("Lan", message) is registry.RouteClaim.CLAIMED
    handler.assert_awaited_once_with("Lan", message)


@pytest.mark.asyncio
async def test_voice_transcript_passes_route_kwargs_through(empty_registry):
    assert await registry.route_external_voice_transcript("Lan", "hi") is False

    registry.register_external_route_kind(_kind("visit", active=True))
    assert await registry.route_external_voice_transcript("Lan", "hi") is False

    handler = AsyncMock(return_value=True)
    registry.register_external_route_kind(
        _kind("game", active=True, route_voice_transcript=handler)
    )
    registry.register_external_route_kind(_kind("visit", active=False))
    assert await registry.route_external_voice_transcript(
        "Lan", "hi", request_id="r", game_type="soccer", session_id="s",
    ) is True
    handler.assert_awaited_once_with(
        "Lan", "hi", request_id="r", game_type="soccer", session_id="s",
    )


@pytest.mark.asyncio
async def test_page_signal_prefers_the_active_route_then_any_claimant(empty_registry):
    assert await registry.route_external_page_signal("Lan", {"speech_id": "a"}) is False

    # The active kind has no handler (game): an inactive kind that still owns
    # the signal (speech playing after its route ended) gets it.
    claimant = AsyncMock(return_value=True)
    registry.register_external_route_kind(_kind("game", active=True))
    registry.register_external_route_kind(_kind("visit", on_page_signal=claimant))
    assert await registry.route_external_page_signal("Lan", {"speech_id": "a"}) is True
    claimant.assert_awaited_once_with("Lan", {"speech_id": "a"})

    declined = AsyncMock(return_value=False)
    registry.register_external_route_kind(_kind("visit", on_page_signal=declined))
    assert await registry.route_external_page_signal("Lan", {"speech_id": "b"}) is False


@pytest.mark.asyncio
async def test_finalize_sums_every_kind_and_survives_a_failing_one(empty_registry):
    async def _two(_name):
        return 2

    async def _boom(_name):
        raise RuntimeError("finalize failed")

    async def _one(_name):
        return 1

    registry.register_external_route_kind(_kind("a", finalize=_two))
    registry.register_external_route_kind(_kind("b", finalize=_boom))
    registry.register_external_route_kind(_kind("c", finalize=_one))

    assert await registry.finalize_external_routes_for_character("Lan") == 3


def test_register_rejects_a_blank_kind(empty_registry):
    with pytest.raises(ValueError):
        registry.register_external_route_kind(_kind("  "))


def test_game_router_registers_its_original_handlers():
    spec = registry._snapshot_for_tests()["game"]

    assert spec.is_active is game_router.is_game_route_active
    assert spec.route_stream_message is game_router.route_external_stream_message
    assert spec.finalize_for_character is game_router.finalize_game_routes_for_character
    # Mutation: dropping route_voice_transcript from the registration makes
    # independent-ASR game voice fail with GAME_VOICE_TRANSCRIPT_NOT_ROUTED.
    assert spec.route_voice_transcript is game_router.route_external_voice_transcript
    assert spec.on_start_session is None
    assert spec.is_locked is game_router.is_game_route_locked
    assert spec.has_background_tasks is None
    assert spec.on_page_signal is None
    assert spec.current_instance is game_router._game_route_instance


def _voice_token(turn_id: int = 1) -> VoiceTurnToken:
    return VoiceTurnToken(
        ingress=VoiceIngressToken(
            connection_id="connection",
            lease_generation=1,
            route_generation=1,
            audio_generation=1,
            session_epoch=5,
        ),
        turn_id=turn_id,
    )


@pytest.mark.asyncio
async def test_independent_asr_final_reaches_an_active_game_through_the_registry(monkeypatch):
    delivered = AsyncMock(return_value=True)
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    gr_patch_all(monkeypatch, "_route_external_transcript_to_game", delivered)
    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "match-1", "Lan")
        consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")
        token = _voice_token()

        assert consumer.is_available() is True
        assert await consumer.prepare_turn(token) is True
        # Raises GAME_VOICE_TRANSCRIPT_NOT_ROUTED if the registry cannot deliver.
        await consumer.on_final(
            VoiceTranscriptEvent(turn_token=token, provider="qwen", text="shoot")
        )

    delivered.assert_awaited_once()
    args, kwargs = delivered.await_args
    assert args[:3] == ("Lan", state, "shoot")
    assert kwargs["mode"] == "voice"


@pytest.mark.asyncio
async def test_independent_asr_consumer_is_unavailable_without_a_route(empty_registry):
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")

    assert consumer.is_available() is False


@pytest.mark.asyncio
async def test_independent_asr_final_reaches_a_non_game_route_that_accepts_voice(empty_registry):
    # Mutation: prepare_turn requiring a game identity turns this red.
    handler = AsyncMock(return_value=True)
    registry.register_external_route_kind(
        _kind("visit", active=True, route_voice_transcript=handler, instance="visit-1")
    )
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")
    token = _voice_token(turn_id=2)

    assert consumer.is_available() is True
    assert await consumer.prepare_turn(token) is True
    await consumer.on_final(VoiceTranscriptEvent(turn_token=token, provider="qwen", text="hi"))

    handler.assert_awaited_once_with("Lan", "hi", request_id="asr-5-2", route_instance="visit-1")


@pytest.mark.asyncio
async def test_independent_asr_final_is_not_rerouted_after_the_route_changes(empty_registry):
    handler = AsyncMock(return_value=True)
    registry.register_external_route_kind(
        _kind("visit", active=True, route_voice_transcript=handler, instance="visit-1")
    )
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")
    token = _voice_token(turn_id=3)
    assert await consumer.prepare_turn(token) is True

    other = AsyncMock(return_value=True)
    registry.register_external_route_kind(_kind("visit", active=False))
    registry.register_external_route_kind(
        _kind("other", active=True, route_voice_transcript=other, instance="other-1")
    )
    with pytest.raises(RuntimeError, match="GAME_VOICE_TRANSCRIPT_NOT_ROUTED"):
        await consumer.on_final(VoiceTranscriptEvent(turn_token=token, provider="qwen", text="hi"))

    handler.assert_not_awaited()
    other.assert_not_awaited()


@pytest.mark.asyncio
async def test_independent_asr_final_is_not_delivered_to_the_next_instance_of_the_kind(empty_registry):
    # Mutation: pinning only the kind turns this red -- the previous visit's
    # utterance would reach the next visit.
    handler = AsyncMock(return_value=True)
    registry.register_external_route_kind(
        _kind("visit", active=True, route_voice_transcript=handler, instance="visit-1")
    )
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")
    token = _voice_token(turn_id=5)
    assert await consumer.prepare_turn(token) is True

    registry.register_external_route_kind(
        _kind("visit", active=True, route_voice_transcript=handler, instance="visit-2")
    )
    with pytest.raises(RuntimeError, match="GAME_VOICE_TRANSCRIPT_NOT_ROUTED"):
        await consumer.on_final(VoiceTranscriptEvent(turn_token=token, provider="qwen", text="hi"))

    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_independent_asr_final_is_pinned_to_the_instance_not_the_registered_kind(empty_registry):
    # The same registered kind object moves on to its next instance. Mutation:
    # pinning the kind object (ignoring the instance id) turns this red.
    handler = AsyncMock(return_value=True)
    instance = {"id": "visit-1"}
    registry.register_external_route_kind(ExternalRouteKind(
        kind="visit",
        is_active=lambda _name: True,
        route_stream_message=_unclaimed,
        on_start_session=AsyncMock(return_value=False),
        finalize_for_character=_no_routes,
        route_voice_transcript=handler,
        current_instance=lambda _name: instance["id"],
    ))
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")
    token = _voice_token(turn_id=6)
    assert await consumer.prepare_turn(token) is True

    instance["id"] = "visit-2"
    with pytest.raises(RuntimeError, match="GAME_VOICE_TRANSCRIPT_NOT_ROUTED"):
        await consumer.on_final(VoiceTranscriptEvent(turn_token=token, provider="qwen", text="hi"))

    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_independent_asr_does_not_prepare_for_a_route_without_a_voice_handler(empty_registry):
    registry.register_external_route_kind(_kind("visit", active=True, instance="visit-1"))
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")

    assert await consumer.prepare_turn(_voice_token(turn_id=4)) is False


@pytest.mark.asyncio
async def test_independent_asr_does_not_prepare_for_a_route_without_an_instance_id(empty_registry):
    handler = AsyncMock(return_value=True)
    registry.register_external_route_kind(
        _kind("visit", active=True, route_voice_transcript=handler, instance="")
    )
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")

    assert await consumer.prepare_turn(_voice_token(turn_id=6)) is False


@pytest.mark.asyncio
async def test_prepared_game_final_is_not_delivered_to_a_route_that_took_over(
    empty_registry, monkeypatch,
):
    # Mutation: dispatching game finals without checking the active kind turns
    # this red -- the stale game utterance would reach the new route.
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.get_active_game_route_identity",
        lambda _name: ("soccer", "match-1", ""),
    )
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")
    token = _voice_token(turn_id=7)
    assert await consumer.prepare_turn(token) is True

    handler = AsyncMock(return_value=True)
    registry.register_external_route_kind(
        _kind("visit", active=True, route_voice_transcript=handler, instance="visit-1")
    )
    with pytest.raises(RuntimeError, match="GAME_VOICE_TRANSCRIPT_NOT_ROUTED"):
        await consumer.on_final(VoiceTranscriptEvent(turn_token=token, provider="qwen", text="shoot"))

    handler.assert_not_awaited()



@pytest.mark.parametrize(
    ("with_handler", "with_instance", "available"),
    [(True, True, True), (False, True, False), (True, False, False)],
    ids=["takes-voice", "no-voice-handler", "no-instance-id"],
)
def test_independent_asr_availability_matches_what_prepare_accepts(
    empty_registry, with_handler, with_instance, available,
):
    # Mutation: is_available returning True for any active kind turns the
    # last two cases red -- the voice registry would hand the utterance to a
    # consumer that then refuses to prepare it.
    registry.register_external_route_kind(_kind(
        "visit",
        active=True,
        route_voice_transcript=AsyncMock(return_value=True) if with_handler else None,
        instance="visit-1" if with_instance else "",
    ))
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")

    assert consumer.is_available() is available


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("consumed", "passthrough", "drop"),
    [(True, False, True), (True, True, False), (False, False, False)],
)
async def test_microphone_audio_is_dropped_only_when_consumed_without_passthrough(
    empty_registry, consumed, passthrough, drop,
):
    assert await registry.route_external_microphone_audio("Lan") is False

    announce = AsyncMock(return_value=consumed)
    registry.register_external_route_kind(ExternalRouteKind(
        kind="visit",
        is_active=lambda _name: True,
        route_stream_message=announce,
        on_start_session=None if passthrough else AsyncMock(return_value=False),
        finalize_for_character=_no_routes,
        audio_passthrough=passthrough,
        current_instance=lambda _name: "visit-1",
    ))

    assert await registry.route_external_microphone_audio("Lan") is drop
    announce.assert_awaited_once_with("Lan", {"input_type": "audio", "stt_provider": "realtime"})


@pytest.mark.asyncio
async def test_independent_asr_ignores_a_route_reporting_an_empty_instance(empty_registry):
    registry.register_external_route_kind(_kind(
        "visit", active=True, route_voice_transcript=AsyncMock(return_value=True), instance="",
    ))
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")

    assert consumer.is_available() is False
    assert await consumer.prepare_turn(_voice_token(turn_id=9)) is False


@pytest.mark.parametrize("claims_starts", [True, False])
def test_every_kind_must_report_its_instance(empty_registry, claims_starts):
    # Every awaited dispatch re-checks (kind, instance); without an instance id
    # that re-check cannot tell two instances of the kind apart.
    with pytest.raises(ValueError, match="current_instance"):
        registry.register_external_route_kind(ExternalRouteKind(
            kind="visit",
            is_active=lambda _name: True,
            route_stream_message=_unclaimed,
            on_start_session=AsyncMock(return_value=True) if claims_starts else None,
            finalize_for_character=_no_routes,
        ))


@pytest.mark.asyncio
async def test_stream_message_follows_an_owner_change_during_handling(empty_registry):
    """A declining handler whose route was replaced meanwhile must not leak the text.

    Mutation: returning the stale "not consumed" without re-checking the owner
    turns this red -- the text would go to the ordinary chat session.
    """
    new_owner = AsyncMock(return_value=True)

    async def _decline_after_replacement(_name, _message):
        registry.register_external_route_kind(ExternalRouteKind(
            kind="visit",
            is_active=lambda _name: False,
            route_stream_message=_unclaimed,
            on_start_session=None,
            finalize_for_character=_no_routes,
            current_instance=lambda _name: None,
            audio_passthrough=True,
        ))
        registry.register_external_route_kind(_kind(
            "other", active=True, route_stream_message=new_owner, instance="other-1",
        ))
        return False

    registry.register_external_route_kind(_kind(
        "visit", active=True, route_stream_message=_decline_after_replacement, instance="visit-1",
    ))
    message = {"input_type": "text", "data": "hi"}

    assert await registry.route_external_stream_message("Lan", message) is registry.RouteClaim.CLAIMED
    new_owner.assert_awaited_once_with("Lan", message)


@pytest.mark.asyncio
async def test_stream_message_goes_to_ordinary_chat_when_the_owner_left_during_handling(
    empty_registry,
):
    async def _decline_and_end(_name, _message):
        registry._reset_for_tests()
        return False

    registry.register_external_route_kind(_kind(
        "visit", active=True, route_stream_message=_decline_and_end, instance="visit-1",
    ))

    assert await registry.route_external_stream_message("Lan", {"input_type": "text"}) is registry.RouteClaim.UNCLAIMED


@pytest.mark.asyncio
async def test_microphone_audio_follows_an_owner_change_during_the_announcement(empty_registry):
    """A replaced owner's decision and passthrough rule do not apply to the new owner.

    Mutation: returning the stale result without re-checking the owner turns
    this red -- the PCM would flow to the ordinary session.
    """
    new_owner = AsyncMock(return_value=True)

    async def _passthrough_then_replaced(_name, _message):
        registry.register_external_route_kind(_kind("visit", active=False, instance="visit-1"))
        registry.register_external_route_kind(_kind(
            "other", active=True, route_stream_message=new_owner, instance="other-1",
            on_start_session=AsyncMock(return_value=False),  # no passthrough
        ))
        return True

    registry.register_external_route_kind(ExternalRouteKind(
        kind="visit",
        is_active=lambda _name: True,
        route_stream_message=_passthrough_then_replaced,
        on_start_session=None,
        finalize_for_character=_no_routes,
        current_instance=lambda _name: "visit-1",
        audio_passthrough=True,
    ))

    assert await registry.route_external_microphone_audio("Lan") is True
    new_owner.assert_awaited_once()


@pytest.mark.asyncio
async def test_microphone_audio_goes_to_ordinary_session_when_the_owner_left(empty_registry):
    async def _consume_and_end(_name, _message):
        registry._reset_for_tests()
        return True

    registry.register_external_route_kind(_kind(
        "visit", active=True, route_stream_message=_consume_and_end, instance="visit-1",
    ))

    assert await registry.route_external_microphone_audio("Lan") is False


def test_game_route_instance_tells_routes_apart(monkeypatch):
    """Two game sessions of the same kind must not share an instance id."""
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    with reset_game_route_state():
        assert game_router._game_route_instance("Lan") is None
        gr_runtime._activate_game_route("soccer", "match-1", "Lan")
        first = game_router._game_route_instance("Lan")
        gr_runtime._activate_game_route("soccer", "match-2", "Lan")
        second = game_router._game_route_instance("Lan")

    assert first and second and first != second


@pytest.mark.asyncio
async def test_active_game_route_with_blank_session_id_keeps_its_microphone_audio(monkeypatch):
    """An active game always has an instance id, even with a blank session id.

    The voice identity helpers skip blank session ids; deriving the instance
    from them would make every dispatch look like an owner change and drop the
    whole game's PCM. Mutation: building the instance from
    get_active_game_route_generation_identity turns this red.
    """
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    with reset_game_route_state():
        gr_runtime._activate_game_route("soccer", "   ", "Lan")
        assert game_router._game_route_instance("Lan")
        identity = registry.external_route_identity("Lan")
        assert registry.same_external_route_owner(identity, registry.external_route_identity("Lan"))
        # The game passes its PCM through to realtime (its STT provider).
        assert await registry.route_external_microphone_audio("Lan") is False


@pytest.mark.asyncio
async def test_a_claim_from_an_instance_replaced_while_claiming_is_reasked(empty_registry):
    """A claim only stands if the same instance still owns the character.

    Mutation: treating a claim as final (no owner re-check) turns this red --
    the defunct instance's claim would swallow the start.
    """
    instance = {"id": "visit-1"}
    asked = []

    async def _claim_then_replaced(_name, _message):
        asked.append(instance["id"])
        if instance["id"] == "visit-1":
            await asyncio.sleep(0)
            instance["id"] = "visit-2"
            return True
        return False

    registry.register_external_route_kind(ExternalRouteKind(
        kind="visit",
        is_active=lambda _name: True,
        route_stream_message=_unclaimed,
        on_start_session=_claim_then_replaced,
        finalize_for_character=_no_routes,
        current_instance=lambda _name: instance["id"],
    ))

    result = await registry.route_external_start_session("Lan", {"input_type": "audio"})

    assert asked == ["visit-1", "visit-2"]
    assert result == (registry.RouteClaim.UNCLAIMED, None)


@pytest.mark.parametrize("empty_instance", [None, "", 0])
def test_an_unpinnable_instance_never_counts_as_the_same_owner(empty_registry, empty_instance):
    # Mutation: plain tuple equality turns this red -- two instances that both
    # report an empty id would look like one owner.
    spec = _kind("visit", active=True, instance="visit-1")
    assert registry.same_external_route_owner(None, None) is True
    assert registry.same_external_route_owner((spec, "visit-1"), (spec, "visit-1")) is True
    assert registry.same_external_route_owner((spec, "visit-1"), (spec, "visit-2")) is False
    assert registry.same_external_route_owner((spec, "visit-1"), None) is False
    assert registry.same_external_route_owner((spec, empty_instance), (spec, empty_instance)) is False


@pytest.mark.asyncio
async def test_stream_message_is_not_leaked_when_the_owner_reports_no_instance(empty_registry):
    """An owner without a usable instance id cannot vouch for its own decline: fail closed."""
    handler = AsyncMock(return_value=False)
    registry.register_external_route_kind(_kind(
        "visit", active=True, route_stream_message=handler, instance="",
    ))

    assert await registry.route_external_stream_message("Lan", {"input_type": "text"}) is registry.RouteClaim.UNSETTLED


@pytest.mark.asyncio
@pytest.mark.parametrize("instance", [7, ("visit", 1)], ids=["int", "tuple"])
async def test_independent_asr_ignores_a_route_reporting_a_non_string_instance(
    empty_registry, instance,
):
    # Mutation: accepting any truthy id turns this red.
    registry.register_external_route_kind(_kind(
        "visit", active=True, route_voice_transcript=AsyncMock(return_value=True), instance=instance,
    ))
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")

    assert consumer.is_available() is False
    assert await consumer.prepare_turn(_voice_token(turn_id=11)) is False
