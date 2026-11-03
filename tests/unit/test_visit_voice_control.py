"""Visit invitations refuse voice before session/UI mutation in either window."""

import json
from pathlib import Path

import pytest

from tests.unit.test_visit_chat_assembly_node import (
    CHAT_PATH,
    HARNESS,
    PACER_PATH,
    RELAY_SAMPLES,
)
from tests.unit.test_visit_peer_crop_node import _BRIDGE_JS
from tests.unit.test_voice_start_failure_static import _js_function_block, _mic_button_start_flow
from tests.visit_js_harness import BRIDGE_FILES, run_visit_js

ROOT = Path(__file__).resolve().parents[2]
DISPLAY_RELAY = ROOT / "static/visit/display-relay.js"
BUTTONS = ROOT / "static/app/app-buttons.js"
CAPTURE = ROOT / "static/app/app-audio-capture.js"


def _prelude():
    chat = (
        HARNESS.split("const report = {};", 1)[0]
        .replace("__CHAT_PATH__", json.dumps(str(CHAT_PATH)))
        .replace("__PACER_PATH__", json.dumps(str(PACER_PATH)))
        .replace("__RELAY_SAMPLES__", json.dumps(RELAY_SAMPLES))
    )
    click = _mic_button_start_flow(BUTTONS.read_text(encoding="utf-8")) + ");"
    capture = "async " + _js_function_block(CAPTURE.read_text(encoding="utf-8"), "startMicCapture")
    return _BRIDGE_JS + "\nfunction makeChatHarness() {\n" + chat + "\nreturn createHarness();\n}\n" + f"""
const MIC_CLICK = {json.dumps(click)};
const MIC_CAPTURE = {json.dumps(capture)};
const DISPLAY_RELAY = {json.dumps(str(DISPLAY_RELAY))};
function installMic(ctx) {{
  const toasts = [];
  ctx.S = {{ isRecording: false, isTextSessionActive: true, voiceStartPending: false }};
  ctx.mod = {{}};
  ctx.micButton = {{
    disabled: false,
    classList: {{ contains: () => false, add() {{ throw Error('ordinary voice start reached'); }} }},
    addEventListener(_type, listener) {{ ctx.clickMic = listener; }},
  }};
  ctx.showStatusToast = text => toasts.push(text);
  ctx.t = key => key;
  vm.runInContext(MIC_CLICK + '\\n' + MIC_CAPTURE, ctx);
  return toasts;
}}
async function clickMic(ctx) {{
  try {{ await ctx.clickMic(); return 'blocked'; }}
  catch (error) {{ if (error.message === 'ordinary voice start reached') return 'started'; throw error; }}
}}
"""


@pytest.mark.parametrize("side", ["host", "guest"])
def test_single_window_invitation_blocks_click_and_capture_until_input_is_returned(side):
    out = run_visit_js(
        f"""
        const chat = makeChatHarness();
        const ctx = makeContext();
        ctx.nekoVisitChat = chat.chat;
        load(ctx, [DISPLAY_RELAY]);
        const toasts = installMic(ctx);
        const before = await clickMic(ctx);
        const phases = [];
        const waitingText = [];
        for (const action of ['pending', 'invite_ready', 'joining', 'awaiting_accept', 'started', 'wrap_up']) {{
          chat.state(action, {{ side: {json.dumps(side)} }});
          phases.push([action, await clickMic(ctx), await ctx.startMicCapture()]);
          if ({json.dumps(side)} === 'host' && phases.length <= 4) {{
            waitingText.push([chat.I.submitVisitChatText({{ text: 'hello', requestId: action }}), chat.toasts.at(-1)]);
          }}
        }}
        const intact = ctx.S.isTextSessionActive && !ctx.S.voiceStartPending && !ctx.isMicStarting
          && !ctx.micButton.disabled && chat.sent.length === 0;
        chat.state('ending');
        const ending = await clickMic(ctx);
        chat.state('ended');
        return {{ before, phases, toasts, waitingText, intact, ending, ended: await clickMic(ctx) }};
        """,
        files=(),
        extra_prelude=_prelude(),
    )
    assert out["before"] == out["ending"] == out["ended"] == "started"
    assert all(click == "blocked" and capture is False for _, click, capture in out["phases"])
    assert out["toasts"] == (
        ["visit.status.VISIT_INPUT_REFUSED_NOT_READY"] * 4
        + ["visit.status.VISIT_VOICE_UNAVAILABLE"] * 2
    )
    if side == "host":
        assert out["waitingText"] == [[False, "visit.status.VISIT_INPUT_REFUSED_NOT_READY"]] * 4
    assert out["intact"]


def test_electron_pet_uses_relayed_invitation_state_and_releases_guard_after_cancel():
    out = run_visit_js(
        """
        const channels = [];
        class Channel {
          constructor(name) { this.name = name; channels.push(this); }
          postMessage(data) {
            for (const peer of channels) {
              if (peer !== this && peer.name === this.name && peer.onmessage) peer.onmessage({ data });
            }
          }
        }
        const pet = makeBridge();
        pet.ctx.nekoVisitParentBridge = pet.bridge;
        // Pet's ordinary chat state has missed the invitation held by /chat.
        const staleChat = makeChatHarness();
        pet.ctx.nekoVisitChat = staleChat.chat;
        const chat = makeContext({ pathname: '/chat' });
        for (const ctx of [pet.ctx, chat]) {
          ctx.__NEKO_MULTI_WINDOW__ = true;
          ctx.lanlan_config = { lanlan_name: 'Lanlan' };
          ctx.BroadcastChannel = Channel;
          load(ctx, [DISPLAY_RELAY]);
        }
        const toasts = installMic(pet.ctx);
        function relay(action, visitId = VISIT_ID) {
          chat.nekoRelayVisitStateToDisplay({ type: 'visit_state_change', action, side: 'host', visit_id: visitId });
        }
        relay('pending'); relay('invite_ready');
        const invitation = await clickMic(pet.ctx);
        const capture = await pet.ctx.startMicCapture();
        // A local state frame received before /chat took over can also stay active forever.
        staleChat.state('pending');
        // A different character's visit must not clear the local guard.
        chat.lanlan_config.lanlan_name = 'Other';
        relay('ended');
        const otherCharacter = pet.ctx.isNekoVisitVoiceBlocked();
        chat.lanlan_config.lanlan_name = 'Lanlan';
        relay('ended', 'B'.repeat(22));
        const otherVisit = pet.ctx.isNekoVisitVoiceBlocked();
        relay('ended');
        const cancelled = await clickMic(pet.ctx);
        relay('pending', 'C'.repeat(22));
        const nextInvite = await clickMic(pet.ctx);
        relay('ending', 'C'.repeat(22));
        return { invitation, capture, otherCharacter, otherVisit, cancelled, nextInvite,
          ending: await clickMic(pet.ctx), toasts };
        """,
        files=BRIDGE_FILES,
        extra_prelude=_prelude(),
    )
    assert out["invitation"] == out["nextInvite"] == "blocked"
    assert out["capture"] is False
    assert out["otherCharacter"] and out["otherVisit"]
    assert out["cancelled"] == out["ending"] == "started"
    assert out["toasts"] == ["visit.status.VISIT_INPUT_REFUSED_NOT_READY"] * 2


def test_pet_transport_close_keeps_voice_blocked_until_visit_ends():
    out = run_visit_js(
        """
        const pet = makeBridge();
        await open(pet, 'host');
        change(pet, 'invite_ready');
        pet.bridge._onFrameMessage({ source: pet.child, origin: pet.ctx.location.origin,
          data: { t: 'closed', code: 4409 } });
        const blocked = pet.bridge.blocksOrdinaryVoice();
        change(pet, 'ended');
        return { blocked, ended: pet.bridge.blocksOrdinaryVoice() };
        """,
        files=BRIDGE_FILES,
        extra_prelude=_BRIDGE_JS,
    )
    assert out == {"blocked": True, "ended": False}
