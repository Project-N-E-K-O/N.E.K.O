"""Handing a plugin's call-out the screen the user is already sharing.

The point of the path is that it costs nothing: the frame exists, the turn
exists, and joining them saves a tool round trip plus a vision-model call. So
the tests here mostly guard the conditions under which it must NOT fire -- a
frame sent to a model that cannot read pixels, or a camera pointed at the room,
buys the cost back and gives nothing -- and the one rule that matters when it
does fail: the character still speaks.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from main_logic.core._shared import _LIVE_VISION_STALE_SECONDS
from main_logic.core.proactive import ProactiveMixin
from main_logic.core.streaming import StreamingMixin
from tests.fake_clock import patch_module_clock

pytestmark = pytest.mark.unit


class _FakeRealtime:
    """Stands in for OmniRealtimeClient's vision surface."""

    def __init__(self, *, native=True, frame="cached-frame", fail=False):
        self._supports_native_image = native
        self._latest_image_b64 = frame
        self._fail = fail
        self.sent = []

    async def stream_image(self, b64, *, bypass_rate_limit=False,
                           cache_latest=True, on_rejected=None):
        if self._fail:
            raise RuntimeError("provider closed the socket")
        self.sent.append(
            {
                "b64": b64,
                "bypass_rate_limit": bypass_rate_limit,
                "cache_latest": cache_latest,
                "on_rejected": on_rejected,
            }
        )
        return None


def _mgr(session, *, snapshot, frame="cached-frame"):
    mgr = SimpleNamespace(
        lanlan_name="lanlan",
        session=session,
        live_vision_snapshot=lambda: snapshot,
        live_vision_frame_b64=lambda: frame if snapshot.get("active") else "",
    )
    mgr._stream_cb_live_frame = (
        lambda cb, sess, si: ProactiveMixin._stream_cb_live_frame(mgr, cb, sess, si)
    )
    return mgr


def _sharing(**over):
    return {
        "active": True,
        "source": "screen",
        "age_seconds": 0.5,
        "native_vision": True,
        **over,
    }


async def _attach(mgr, cb, session):
    return await ProactiveMixin._stream_cb_live_frame(
        mgr, cb, session, session.stream_image)


# ------------------------------------------------------- when it does fire
async def test_the_shared_frame_joins_the_turn_the_plugin_speaks_in():
    session = _FakeRealtime()
    mgr = _mgr(session, snapshot=_sharing())

    assert await _attach(mgr, {"attach_live_frame": True}, session) is True
    sent = session.sent[0]
    assert sent["b64"] == "cached-frame"
    # Bypass is the whole trick: without it the frame is held back until the
    # user happens to be speaking, which during a quiet battle is never.
    assert sent["bypass_rate_limit"] is True
    # Not cached: this frame belongs to the cue, and claiming it as the
    # ambient latest would let the next proactive nudge reuse it.
    assert sent["cache_latest"] is False
    # No rejection handler: an opportunistic frame must not drag the callback
    # into the retry machinery meant for its own pictures.
    assert sent["on_rejected"] is None


# ------------------------------------------------- when it must not fire
@pytest.mark.parametrize(
    ("cb", "snapshot", "session_kwargs", "why"),
    [
        ({}, _sharing(), {}, "plugin did not ask"),
        ({"attach_live_frame": False}, _sharing(), {}, "plugin opted out"),
        ({"attach_live_frame": True}, _sharing(active=False), {}, "not sharing"),
        (
            {"attach_live_frame": True},
            _sharing(source="camera"),
            {},
            "a room, not a screen",
        ),
        (
            {"attach_live_frame": True},
            _sharing(),
            {"native": False},
            "model would need the vision detour anyway",
        ),
    ],
)
async def test_no_frame_is_attached(cb, snapshot, session_kwargs, why):
    session = _FakeRealtime(**session_kwargs)
    mgr = _mgr(session, snapshot=snapshot)

    assert await _attach(mgr, cb, session) is False, why
    assert session.sent == []


async def test_nothing_is_attached_when_the_host_holds_no_frame():
    session = _FakeRealtime()
    mgr = _mgr(session, snapshot=_sharing(), frame="")

    assert await _attach(mgr, {"attach_live_frame": True}, session) is False
    assert session.sent == []


async def test_the_delivered_frame_is_the_share_not_the_ambient_cache():
    """An avatar drop, a pasted image or another plugin's picture all land in
    the session's ``_latest_image_b64``. Delivering that as "your screen" would
    be a different picture entirely, so the share is read from its own slot."""
    session = _FakeRealtime(frame="somebody-dropped-this")
    mgr = _mgr(session, snapshot=_sharing(), frame="the-actual-screen")

    await _attach(mgr, {"attach_live_frame": True}, session)

    assert session.sent[0]["b64"] == "the-actual-screen"


async def test_a_failed_send_is_swallowed_rather_than_raised():
    session = _FakeRealtime(fail=True)
    mgr = _mgr(session, snapshot=_sharing())

    assert await _attach(mgr, {"attach_live_frame": True}, session) is False


# ------------------------------------------------- inside _stream_cb_media
async def test_a_failed_live_frame_still_lets_the_call_out_go_out():
    """Unlike media_images, whose failure defers the whole delivery."""
    session = _FakeRealtime(fail=True)
    mgr = _mgr(session, snapshot=_sharing())
    cb = {"attach_live_frame": True}

    ok = await ProactiveMixin._stream_cb_media(mgr, [cb], session)

    assert ok is True


async def test_a_batch_shares_one_frame_rather_than_one_each():
    """Cues released together land in one turn; a second copy buys nothing."""
    session = _FakeRealtime()
    mgr = _mgr(session, snapshot=_sharing())
    cbs = [{"attach_live_frame": True} for _ in range(3)]

    await ProactiveMixin._stream_cb_media(mgr, cbs, session)

    assert len(session.sent) == 1


# ------------------------------------------------------------- the liveness
def _liveness(*, last_at, source="screen", native=True, frame="shared-frame"):
    mgr = SimpleNamespace(
        session=SimpleNamespace(_supports_native_image=native),
        _live_vision_source=source,
        _live_vision_last_frame_at=last_at,
        _live_vision_frame_b64=frame,
    )
    mgr.live_vision_snapshot = lambda: StreamingMixin.live_vision_snapshot(mgr)
    return mgr


def test_a_session_that_never_saw_a_frame_is_not_sharing():
    state = StreamingMixin.live_vision_snapshot(_liveness(last_at=0.0))

    assert state == {
        "active": False,
        "source": "",
        "age_seconds": None,
        "native_vision": True,
    }


def test_a_recent_frame_means_sharing(monkeypatch):
    import main_logic.core.streaming as streaming

    patch_module_clock(monkeypatch, streaming, monotonic=lambda: 500.0)
    state = StreamingMixin.live_vision_snapshot(_liveness(last_at=499.0))

    assert state["active"] is True
    assert state["source"] == "screen"
    assert state["age_seconds"] == pytest.approx(1.0)


def test_frames_that_stopped_arriving_stop_counting(monkeypatch):
    """Minimized window, closed tab, idle release -- all look the same here."""
    import main_logic.core.streaming as streaming

    patch_module_clock(
        monkeypatch,
        streaming,
        monotonic=lambda: 500.0 + _LIVE_VISION_STALE_SECONDS + 1.0,
    )
    state = StreamingMixin.live_vision_snapshot(_liveness(last_at=500.0))

    assert state["active"] is False
    # Blanked with the liveness: a stale source would read as "still on screen".
    assert state["source"] == ""


def test_only_an_accepted_share_frame_reaches_the_slot(monkeypatch):
    """One writer, so nothing else can pass itself off as the shared screen."""
    import main_logic.core.streaming as streaming

    patch_module_clock(monkeypatch, streaming, monotonic=lambda: 500.0)
    mgr = _liveness(last_at=0.0, source="", frame="")

    StreamingMixin._note_live_vision_frame(mgr, "screen", "frame-one")

    assert mgr._live_vision_frame_b64 == "frame-one"
    assert StreamingMixin.live_vision_frame_b64(mgr) == "frame-one"


def test_a_frame_from_a_finished_share_is_never_handed_out(monkeypatch):
    import main_logic.core.streaming as streaming

    patch_module_clock(
        monkeypatch,
        streaming,
        monotonic=lambda: 500.0 + _LIVE_VISION_STALE_SECONDS + 1.0,
    )
    mgr = _liveness(last_at=500.0)

    assert StreamingMixin.live_vision_frame_b64(mgr) == ""


def test_an_expired_frame_is_dropped_rather_than_kept_in_memory(monkeypatch):
    """It is a picture of somebody's desktop; once unanswerable, let it go."""
    import main_logic.core.streaming as streaming

    patch_module_clock(
        monkeypatch,
        streaming,
        monotonic=lambda: 500.0 + _LIVE_VISION_STALE_SECONDS + 1.0,
    )
    mgr = _liveness(last_at=500.0)

    StreamingMixin.live_vision_snapshot(mgr)

    assert mgr._live_vision_frame_b64 == ""
