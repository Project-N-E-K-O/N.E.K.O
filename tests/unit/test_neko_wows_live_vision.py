"""Reusing the shared screen instead of taking our own screenshot.

Three seams, each with one job: the probe decides whether the host has a frame
for us, the prompt layer tells the character which of those two worlds she is
in, and the screenshot tool picks where its pixels come from. They are tested
apart because a wrong answer in any one of them is a different bug -- a stalled
probe stalls the battle pipeline, a wrong prompt makes her narrate a picture
she never got, and a wrong source silently double-captures.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_wows.presentation.instructions import (
    LIVE_VISION_SPEAK_HINT,
    VISION_LOOK_BEFORE_SPEAK,
    WOWS_CONTEXT_INSTRUCTIONS,
    WOWS_CONTEXT_WITH_LIVE_VISION_INSTRUCTIONS,
    WOWS_CONTEXT_WITH_VISION_INSTRUCTIONS,
    context_instructions,
)
from plugin.plugins.neko_wows.presentation.prompt_router import (
    PromptProfile,
    WowsPromptRouter,
)
from plugin.plugins.neko_wows.domain.contracts import LANE_NORMAL, WowsConfig
from plugin.plugins.neko_wows.policy.tactic_policy import AdviceCandidate
from plugin.plugins.neko_wows.vision.live import LiveVisionProbe
from plugin.plugins.neko_wows.vision.store import ShotStore
from plugin.plugins.neko_wows.vision import tool as tool_module
from plugin.plugins.neko_wows.vision.tool import (
    REASON_CAPTURE_FAILED,
    SOURCE_FULLSCREEN,
    SOURCE_LIVE_SHARE,
    ScreenshotService,
)

pytestmark = [pytest.mark.unit, pytest.mark.plugin_unit]


SHARING = {
    "active": True,
    "source": "screen",
    "age_seconds": 0.4,
    "native_vision": True,
    "role": "lanlan",
}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _inline_spawn(fn):
    """Run the refresh where the caller stands, so tests stay deterministic."""
    fn()


def _probe(replies, *, clock=None, spawn=_inline_spawn, ttl=2.0):
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    probe = LiveVisionProbe(
        fetch, ttl=ttl, clock=clock or _Clock(), spawn=spawn)
    return probe, calls


# --------------------------------------------------------------- the probe
def test_first_read_answers_from_nothing_and_then_learns():
    """The very first call-out of a session must not wait on the host."""
    probe, calls = _probe([SHARING], spawn=lambda fn: None)

    assert probe.snapshot()["active"] is False
    assert calls == []  # spawned, not awaited


def test_state_is_reused_inside_the_ttl():
    clock = _Clock()
    probe, calls = _probe([SHARING], clock=clock)

    probe.snapshot()
    clock.advance(1.0)
    state = probe.snapshot()

    assert len(calls) == 1
    assert state["active"] is True


def test_state_is_refetched_once_the_ttl_lapses():
    clock = _Clock()
    probe, calls = _probe([SHARING], clock=clock)

    probe.snapshot()
    clock.advance(5.0)
    probe.snapshot()

    assert len(calls) == 2


def test_a_stalled_refresh_does_not_pile_up_threads():
    """One refresh per stale window, however many frames ask meanwhile."""
    spawned = []
    probe, calls = _probe([SHARING], spawn=spawned.append)

    for _ in range(5):
        probe.snapshot()

    assert len(spawned) == 1
    assert calls == []


def test_an_unreachable_host_reads_as_not_sharing():
    clock = _Clock()
    probe, calls = _probe([RuntimeError("plugin server down")], clock=clock)

    assert probe.snapshot()["active"] is False
    assert len(calls) == 1
    # Stamped despite the failure: an outage must be polled at the normal rate
    # rather than once per telemetry frame.
    clock.advance(1.0)
    probe.snapshot()
    assert len(calls) == 1


def test_a_nonsense_reply_reads_as_not_sharing():
    probe, _calls = _probe(["not a dict"])

    assert probe.snapshot() == {
        "active": False,
        "source": "",
        "age_seconds": None,
        "native_vision": False,
        "role": "",
    }


@pytest.mark.parametrize(
    ("reply", "usable"),
    [
        (SHARING, True),
        ({**SHARING, "active": False}, False),
        ({**SHARING, "source": "camera"}, False),
        ({**SHARING, "native_vision": False}, False),
    ],
)
def test_relying_on_the_live_frame_needs_all_three_conditions(reply, usable):
    """Same three the host checks. Disagreeing would promise her a picture."""
    probe, _calls = _probe([reply])
    probe.snapshot()

    assert probe.is_active() is usable


def test_the_screenshot_tool_can_use_a_share_the_model_cannot_read():
    """Its pixels go back through the tool channel, which the host transcribes."""
    probe, _calls = _probe([{**SHARING, "native_vision": False}])
    probe.snapshot()

    assert probe.is_active() is False
    assert probe.is_sharing_screen() is True


def test_fetching_a_frame_decodes_it_and_asks_for_pixels():
    jpeg = b"\xff\xd8pretend-jpeg"
    probe, calls = _probe([{**SHARING, "frame_b64": base64.b64encode(jpeg).decode()}])

    assert probe.fetch_frame() == jpeg
    assert calls == [{"include_frame": True}]


@pytest.mark.parametrize(
    "reply",
    [
        {**SHARING},  # sharing, but the host sent no frame
        {**SHARING, "source": "camera", "frame_b64": "Zm9v"},
        {**SHARING, "active": False, "frame_b64": "Zm9v"},
        {**SHARING, "frame_b64": "not base64 at all!!"},
        RuntimeError("timed out"),
    ],
)
def test_fetching_a_frame_yields_nothing_rather_than_raising(reply):
    probe, _calls = _probe([reply])

    assert probe.fetch_frame() is None


# ------------------------------------------------------------ the prompts
def test_live_sharing_wins_over_the_screenshot_switch():
    """Both on would tell her to fetch a picture she was already handed."""
    assert context_instructions(
        screenshot_enabled=False, live_vision_active=False
    ) == WOWS_CONTEXT_INSTRUCTIONS
    assert context_instructions(
        screenshot_enabled=True, live_vision_active=False
    ) == WOWS_CONTEXT_WITH_VISION_INSTRUCTIONS
    assert context_instructions(
        screenshot_enabled=True, live_vision_active=True
    ) == WOWS_CONTEXT_WITH_LIVE_VISION_INSTRUCTIONS


def test_the_live_context_carries_the_reading_guide_it_inherited():
    """Raw pixels have no vision_prompt slot, so the guide must be said aloud."""
    text = WOWS_CONTEXT_WITH_LIVE_VISION_INSTRUCTIONS
    assert "小地图" in text
    assert "鱼雷航迹" in text
    assert "以随附文本中的遥测为准" in text


def _candidate() -> AdviceCandidate:
    return AdviceCandidate(
        event_id="low_health",
        lane=LANE_NORMAL,
        priority=50,
        severity=40,
        at=100.0,
        seq=7,
        battle_id="battle-1",
        summary="血量偏低",
        detail={"own_hp_ratio": 0.3},
        context={"alive_enemies": 4},
    )


def _request(
    *,
    screenshot_enabled: bool,
    live_vision_active: bool,
    live_vision_enabled: bool | None = None,
):
    router = WowsPromptRouter(WowsConfig())
    return router.build(
        _candidate(),
        PromptProfile(
            channel_mode="dual",
            dry_run=True,
            screenshot_enabled=screenshot_enabled,
            live_vision_enabled=(
                live_vision_active
                if live_vision_enabled is None
                else live_vision_enabled
            ),
            live_vision_active=live_vision_active,
        ),
    )


def test_a_live_call_out_asks_the_host_for_the_frame_and_drops_the_tool_nudge():
    request = _request(screenshot_enabled=True, live_vision_active=True)

    assert request.metadata["attach_live_frame"] is True
    assert LIVE_VISION_SPEAK_HINT.strip() in request.text
    assert VISION_LOOK_BEFORE_SPEAK.strip() not in request.text


def test_a_call_out_without_sharing_keeps_the_tool_nudge():
    request = _request(
        screenshot_enabled=True, live_vision_active=False, live_vision_enabled=False)

    assert request.metadata["attach_live_frame"] is False
    assert VISION_LOOK_BEFORE_SPEAK.strip() in request.text
    assert LIVE_VISION_SPEAK_HINT.strip() not in request.text


def test_the_flag_rides_the_metadata_the_host_already_reads():
    """No new push_message field: the host promotes it off metadata."""
    request = _request(screenshot_enabled=False, live_vision_active=True)

    assert request.push_kwargs()["metadata"]["attach_live_frame"] is True


def test_the_ask_survives_a_probe_that_has_not_caught_up_yet():
    """The switch is on but the probe is cold or 2s behind, as it is for every
    call-out in the seconds after sharing starts. Gating the ask on that cache
    would throw away frames the host could have attached; the host re-checks
    liveness itself, so asking anyway costs nothing when there is nothing."""
    request = _request(
        screenshot_enabled=True, live_vision_active=False, live_vision_enabled=True)

    assert request.metadata["attach_live_frame"] is True
    # The wording still follows what she can actually be told right now.
    assert VISION_LOOK_BEFORE_SPEAK.strip() in request.text


def test_the_switch_off_never_asks():
    request = _request(
        screenshot_enabled=False, live_vision_active=True, live_vision_enabled=False)

    assert request.metadata["attach_live_frame"] is False


# -------------------------------------------------------- the vision tool
def _service(tmp_path, *, live_frame_provider, monkeypatch, capture=b"captured"):
    monkeypatch.setattr(tool_module, "find_game_window", lambda _dir: None)
    monkeypatch.setattr(tool_module, "capture_jpeg", lambda _window: capture)
    cfg = WowsConfig(screenshot_enabled=True, screenshot_min_interval_seconds=0.0)
    store = ShotStore(tmp_path / "shots", cfg.screenshot_retain_count)
    service = ScreenshotService(
        cfg,
        store,
        lambda: {"in_battle": True},
        live_frame_provider=live_frame_provider,
    )
    return service, store


def test_the_tool_prefers_the_shared_frame_over_capturing_again(tmp_path, monkeypatch):
    shared = b"\xff\xd8shared-frame"
    service, store = _service(
        tmp_path, live_frame_provider=lambda: shared, monkeypatch=monkeypatch)

    result = service.look()

    assert result["output"]["source"] == SOURCE_LIVE_SHARE
    assert base64.b64decode(result["images"][0]["data_b64"]) == shared
    # Still filed away, so wows_recall_screenshot keeps working regardless of
    # who grabbed the pixels.
    assert store.load(result["output"]["shot_id"]) == shared


@pytest.mark.parametrize(
    "provider",
    [
        lambda: None,
        lambda: (_ for _ in ()).throw(RuntimeError("host went away")),
    ],
    ids=["no-frame", "provider-raised"],
)
def test_the_tool_falls_back_to_its_own_capture(tmp_path, monkeypatch, provider):
    service, store = _service(
        tmp_path, live_frame_provider=provider, monkeypatch=monkeypatch)

    result = service.look()

    assert result["output"]["source"] == SOURCE_FULLSCREEN
    assert store.load(result["output"]["shot_id"]) == b"captured"


def test_a_dead_capture_still_fails_honestly_when_nothing_is_shared(
    tmp_path, monkeypatch
):
    service, _store = _service(
        tmp_path,
        live_frame_provider=lambda: None,
        monkeypatch=monkeypatch,
        capture=None,
    )

    assert service.look()["output"]["reason"] == REASON_CAPTURE_FAILED


def test_no_provider_at_all_behaves_exactly_as_before(tmp_path, monkeypatch):
    service, _store = _service(
        tmp_path, live_frame_provider=None, monkeypatch=monkeypatch)

    assert service.look()["output"]["source"] == SOURCE_FULLSCREEN


# ----------------------------------------------------------- plugin wiring
def _plugin_with(cfg, probe):
    from plugin.plugins.neko_wows import NekoWowsPlugin

    plugin = object.__new__(NekoWowsPlugin)
    plugin.cfg = cfg
    plugin.live_vision = probe
    return plugin


def test_the_panel_switch_short_circuits_the_probe_entirely():
    """Off means off: no probe call, no refresh thread, no behaviour change."""
    probe, calls = _probe([SHARING], spawn=lambda fn: None)
    plugin = _plugin_with(WowsConfig(live_vision_enabled=False), probe)

    assert plugin._live_vision_active() is False
    assert plugin._live_frame() is None
    assert calls == []


def test_the_switch_on_lets_a_live_share_through():
    probe, _calls = _probe([SHARING])
    plugin = _plugin_with(WowsConfig(live_vision_enabled=True), probe)
    probe.snapshot()

    assert plugin._live_vision_active() is True


def test_the_panel_keeps_looking_even_when_no_battle_is_running():
    """Outside a battle the panel is the only caller, so if its read did not
    refresh it would sit on whatever the last battle left and report "not
    sharing" at someone who is."""
    probe, calls = _probe([SHARING])
    plugin = _plugin_with(WowsConfig(live_vision_enabled=True), probe)

    first = plugin._live_vision_payload(plugin.cfg)
    assert first["polled"] is True
    assert len(calls) == 1

    probe._fetched_at = None  # next poll is due
    second = plugin._live_vision_payload(plugin.cfg)

    assert len(calls) == 2
    assert second["active"] is True
    assert second["in_use"] is True


def test_the_panel_does_no_work_while_the_switch_is_off():
    probe, calls = _probe([SHARING], spawn=lambda fn: None)
    plugin = _plugin_with(WowsConfig(live_vision_enabled=False), probe)

    payload = plugin._live_vision_payload(plugin.cfg)

    assert payload["enabled"] is False
    assert payload["in_use"] is False
    assert calls == []
