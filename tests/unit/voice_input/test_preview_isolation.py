from dataclasses import dataclass
import gc

import pytest

from main_logic.voice_input.preview import (
    VoicePreviewIsolationError, VoicePreviewIsolationRegistry,
)


@dataclass(eq=False)
class Manager:
    _asr_route_mode: str = "blocked"
    _voice_lease_synchronized: bool = False
    _voice_lease_owner: str | None = None
    _voice_lease_hard_muted: bool = False
    _voice_lease_focus_suppressed: bool = False


def test_single_use_and_expiry_never_release_successor():
    now = [0.0]
    registry = VoicePreviewIsolationRegistry(now=lambda: now[0])
    ticket = registry.begin_inactive("first", noise_reduction_enabled=False)
    assert ticket.as_dict()["noise_reduction_enabled"] is False
    assert registry.claim(ticket.token) is ticket
    with pytest.raises(VoicePreviewIsolationError, match="preview_consumed"):
        registry.claim(ticket.token)
    with pytest.raises(VoicePreviewIsolationError, match="preview_busy"):
        registry.begin_inactive("overlap")
    now[0] = 30.0
    with pytest.raises(VoicePreviewIsolationError, match="preview_expired"):
        ticket.validate_current()
    successor = registry.begin_inactive("successor")
    assert registry.release(ticket) is False
    assert registry.is_manager_isolated(Manager())
    assert registry.release(successor.token)
    assert not registry.is_manager_isolated(Manager())


@pytest.mark.parametrize("value", [None, 123, "", "invalid", "声纹", "x" * 129])
def test_malformed_capabilities_return_typed_failure(value):
    registry = VoicePreviewIsolationRegistry()
    ticket = registry.begin_inactive("request")
    with pytest.raises(VoicePreviewIsolationError, match="preview_invalid"):
        registry.claim(value)
    assert registry.release(value) is False
    assert registry.claim(ticket.token) is ticket


@pytest.mark.parametrize("request_id,nr", [(None, True), ("", True), ("x" * 129, True), ("request", 1)])
def test_invalid_request_does_not_reserve(request_id, nr):
    registry = VoicePreviewIsolationRegistry()
    with pytest.raises(VoicePreviewIsolationError, match="preview_request_invalid"):
        registry.begin_inactive(request_id, noise_reduction_enabled=nr)
    assert not registry.is_manager_isolated(Manager())


@pytest.mark.parametrize("mode,owner,sync,muted,focused,active", [
    ("native", None, False, False, False, True),
    ("independent", None, False, True, True, True),
    ("blocked", "core", True, False, False, True),
    ("blocked", "game", True, False, False, True),
    ("blocked", "core", True, True, False, False),
    ("blocked", "core", True, False, True, False),
    ("blocked", "none", True, False, False, False),
])
def test_inactive_permission_uses_real_route_and_lease(mode, owner, sync, muted, focused, active):
    registry = VoicePreviewIsolationRegistry()
    manager = Manager(mode, sync, owner, muted, focused)
    registry.register(manager)
    if active:
        with pytest.raises(VoicePreviewIsolationError, match="preview_owner_active"):
            registry.begin_inactive("request")
    else:
        assert registry.begin_inactive("request").ready


def test_actual_owner_must_finish_draining_before_ack():
    registry = VoicePreviewIsolationRegistry()
    manager = Manager("native", True, "core")
    registry.register(manager)
    identity = [1]
    ticket = registry.begin(manager, "request", noise_reduction_enabled=True,
                            current=lambda: identity[0] == 1)
    assert registry.is_manager_isolated(manager)
    with pytest.raises(VoicePreviewIsolationError, match="preview_not_ready"):
        registry.claim(ticket.token)
    with pytest.raises(VoicePreviewIsolationError, match="preview_owner_active"):
        registry.mark_ready(ticket)
    manager._asr_route_mode = "blocked"
    manager._voice_lease_owner = None
    registry.mark_ready(ticket)
    assert registry.claim(ticket.token) is ticket
    identity[0] = 2
    with pytest.raises(VoicePreviewIsolationError, match="preview_owner_changed"):
        ticket.validate_current()
    assert registry.release(ticket)


def test_second_actual_producer_cannot_be_paused_by_other_owner():
    registry = VoicePreviewIsolationRegistry()
    first, other = Manager(), Manager("independent")
    registry.register(first)
    registry.register(other)
    with pytest.raises(VoicePreviewIsolationError, match="preview_owner_active"):
        registry.begin(first, "request", noise_reduction_enabled=True, current=lambda: True)
    assert other._asr_route_mode == "independent"
    del other
    gc.collect()
    ticket = registry.begin(first, "request", noise_reduction_enabled=True, current=lambda: True)
    registry.mark_ready(ticket)
    with pytest.raises(VoicePreviewIsolationError, match="preview_invalid"):
        registry.release_owned(ticket.token, Manager())
    assert registry.release_owned(ticket.token, first)
    assert not registry.release(ticket)


def test_owner_destruction_fences_result_and_keeps_reservation_bounded():
    now = [0.0]
    registry = VoicePreviewIsolationRegistry(now=lambda: now[0])
    manager = Manager()
    registry.register(manager)
    ticket = registry.begin(manager, "request", noise_reduction_enabled=True, current=lambda: True)
    registry.mark_ready(ticket)
    del manager
    gc.collect()
    with pytest.raises(VoicePreviewIsolationError, match="preview_owner_changed"):
        ticket.validate_current()
    now[0] = 31.0
    assert not registry.is_manager_isolated(Manager())
