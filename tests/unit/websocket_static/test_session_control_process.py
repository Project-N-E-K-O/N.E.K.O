from pathlib import Path
import pytest

from tests.unit.websocket_static._scenarios import (
    _block_after,
)

APP_WEBSOCKET_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-websocket.js"

APP_STATE_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-state.js"

APP_BUTTONS_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-buttons.js"

APP_GAME_VOICE_CONTROL_PATH = (
    Path(__file__).resolve().parents[3] / "static" / "app" / "app-game-voice-control.js"
)

pytestmark = pytest.mark.integration_serial


def test_every_start_session_send_carries_a_request_id():
    # #2539 / Codex P2. The ack names the start it answers, and the receiver
    # ignores acks that name a different one. A send site that forgets the id
    # gets an anonymous ack back, which every window treats as "mine" -- the
    # exact failure the id exists to prevent. Discovered rather than listed, so
    # a NEW send site cannot slip past this.
    sources = {
        "app-buttons.js": APP_BUTTONS_PATH.read_text(encoding="utf-8"),
        "app-websocket.js": APP_WEBSOCKET_PATH.read_text(encoding="utf-8"),
    }
    found = 0
    for name, source in sources.items():
        cursor = 0
        while True:
            at = source.find("action: 'start_session'", cursor)
            if at == -1:
                break
            cursor = at + 1
            found += 1
            payload_end = source.index("}", at)
            payload = source[at:payload_end]
            assert "request_id: window.sessionStartRequestId(" in payload, (
                f"{name}: a start_session send at offset {at} carries no request id"
            )
    assert found >= 4, "the send sites were not discovered; the search anchor moved"

    # Read off the flow's OWN owner token, never the shared slot: a start
    # displaced during its reconnect await would otherwise stamp the newer
    # start's id onto its own stale request.
    state_source = APP_STATE_PATH.read_text(encoding="utf-8")
    assert "window.sessionStartRequestId = function (owner) {" in state_source
    assert "startRequestIdByOwner.get(owner)" in state_source


def test_pending_request_id_is_claimed_and_released_with_the_slot():
    # The id lives and dies with the shared start slot. Left behind after a
    # release, it would make the NEXT anonymous-or-foreign ack look mismatched
    # and strand a start that nothing else settles.
    state_source = APP_STATE_PATH.read_text(encoding="utf-8")
    websocket_source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert "_pendingSessionStartRequestId: null," in state_source
    claim = state_source.split("window.claimSessionStart = function (", 1)[1].split(
        "\n    };", 1
    )[0]
    assert "S._pendingSessionStartRequestId = requestId;" in claim

    # Every slot teardown clears it. Paired with the mode, which is the field
    # that already had to be cleared everywhere -- so count against that rather
    # than list the sites.
    for source in (state_source, websocket_source):
        assert source.count("S._pendingSessionStartRequestId = null;") == source.count(
            "S._pendingSessionStartMode = null;"
        )


def test_startup_failure_runs_the_same_teardown_as_a_runtime_failure():
    # Codex P2. A startup failure (provider connect, credentials, config)
    # leaves the route blocked but can NEVER emit a BLOCKED lifecycle event --
    # IndependentAsrRuntime.start cannot reach _handle_independent_asr_error,
    # the only emitter. So the terminal ASR_INDEPENDENT_* codes used to show a
    # toast and nothing else, while the browser kept the hardware microphone
    # open for the rest of the session.
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    status_block = source.split(
        "if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0)", 1
    )[1].split("if (statusCode === 'TTS_CONNECTION_FAILED')", 1)[0]
    terminal = status_block.split(
        "if (statusCode === 'ASR_INDEPENDENT_INJECTION_FAILED')", 1
    )[1]

    # Both failure kinds go through one teardown, so they cannot drift.
    assert "tearDownBlockedVoiceRoute();" in terminal
    lifecycle_block = source.split("if (statusCode === 'ASR_LIFECYCLE_STATE')", 1)[
        1
    ].split("if (statusCode === 'VOICE_INPUT_LEASE_RESYNC_REQUIRED')", 1)[0]
    assert "tearDownBlockedVoiceRoute();" in lifecycle_block
    assert source.count("function tearDownBlockedVoiceRoute()") == 1


def test_game_voice_command_commits_its_teardown_before_it_can_yield():
    """The microphone teardown must be issued while the admitting check still holds.

    ``stopMicCapture()`` is process-global. If a command could issue it after
    awaiting, a stop belonging to a route that has since been superseded would
    land on whatever owns the microphone by then -- the replacement route, or
    the ordinary chat capture the host resumes on route exit -- and kill it
    mid-utterance with no transcript and nothing logged.

    Two properties keep that unreachable, and both are easy to lose in an edit:
      1. ``routeMatches()`` admits the command and the single ``stopMicCapture()``
         call sits in the same synchronous segment -- no ``await`` between them,
         so no route change can interleave.
      2. Nothing after the awaited command tears anything down. The
         route-superseded branch reports and returns; it never issues a
         teardown, and never re-starts. (The runtime harness in
         tests/frontend/test_game_voice_control_runtime.js asserts the
         behaviour; this pins the ordering the behaviour depends on.)
    """
    source = APP_GAME_VOICE_CONTROL_PATH.read_text(encoding="utf-8")

    stop_body = _block_after(source, "async function stopOfficialVoiceSession() {")
    assert stop_body.count("stopMicCapture(") == 1, (
        "the stop helper issues more than one microphone teardown"
    )
    assert stop_body.index("stopMicCapture(") < stop_body.index("waitFor("), (
        "the microphone teardown is issued after this helper has already yielded, "
        "so it can land on a capture the command never opened"
    )

    handler = _block_after(source, "async function handleRequest(request) {")
    admit_at = handler.index("if (!routeMatches(request))")
    dispatch_at = handler.index("await startOfficialVoiceSession()")
    admitted_segment = handler[admit_at:dispatch_at]
    assert "await" not in admitted_segment, (
        "an await was introduced between the route check that admits a voice "
        "command and the command dispatch, so the route can change underneath it"
    )

    superseded_at = handler.index("if (!routeSnapshotIsCurrent(acceptedRoute))")
    superseded_branch = handler[superseded_at:handler.index("broadcastState({", superseded_at)]
    # Code only: this branch carries a long comment explaining which mechanisms
    # it deliberately does NOT reach for, and those names must not trip the check.
    superseded_code = chr(10).join(
        line for line in superseded_branch.splitlines()
        if not line.strip().startswith("//")
    )
    for teardown in ("stopMicCapture(", "startMicCapture(", ".click("):
        assert teardown not in superseded_code, (
            f"the superseded branch reaches for {teardown}; a command whose route "
            "is gone must not touch the process-global microphone"
        )
