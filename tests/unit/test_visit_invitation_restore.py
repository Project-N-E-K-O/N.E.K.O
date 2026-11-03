"""Retain waiting invitations across reload, restore cancel controls, and release input after cancellation."""

import json
from pathlib import Path

import pytest

from tests.unit.test_visit_chat_assembly_node import CHAT_PATH, HARNESS, PACER_PATH, RELAY_SAMPLES
from tests.unit.test_visit_peer_crop_node import _BRIDGE_JS
from tests.visit_js_harness import BRIDGE_FILES, run_visit_js

ROOT = Path(__file__).resolve().parents[2]


def _prelude():
    chat = (
        HARNESS.split("const report = {};", 1)[0]
        .replace("__CHAT_PATH__", json.dumps(str(CHAT_PATH)))
        .replace("__PACER_PATH__", json.dumps(str(PACER_PATH)))
        .replace("__RELAY_SAMPLES__", json.dumps(RELAY_SAMPLES))
        .replace("return { win, I, chat,", "return { sandbox, win, I, chat,")
    )
    return _BRIDGE_JS + "\nfunction makeChat(opts) {\n" + chat + "\nreturn createHarness(opts);\n}\n" + f"""
const SOCIAL_HUB = {json.dumps(str(ROOT / 'static/social-hub.js'))};
const DISPLAY_RELAY = {json.dumps(str(ROOT / 'static/visit/display-relay.js'))};
const FRIEND = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const VID = 'A'.repeat(22);
const STATE_URL = '/api/visit/state?catgirl=%E5%92%AA%E5%92%AA';
function snapshot(extra) {{
  return Object.assign({{ active: true, enabled: true, side: 'host', phase: 'invite_ready',
    visit_id: VID, target_user_id: FRIEND, invite_expires_at: 9e9 }}, extra || {{}});
}}
function installHub(h) {{
  const storage = new Map();
  h.sandbox.sessionStorage = {{ getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) }};
  h.sandbox.requestAnimationFrame = () => {{}};
  const source = fs.readFileSync(SOCIAL_HUB, 'utf8').replace('_inviteFriend: inviteFriend,',
    '_inviteFriend: inviteFriend, _cancelInvitation: cancelInvitation,');
  vm.runInContext(source, h.sandbox);
  const hub = h.win.nekoSocialHub;
  hub._state.accountId = 'account';
  hub._state.friends = [{{ id: FRIEND, displayName: 'Friend', avatarUrl: '' }}];
  return hub;
}}
function connectMedia(h, multi) {{
  const pet = makeBridge();
  pet.ctx.nekoVisitParentBridge = pet.bridge;
  pet.ctx.lanlan_config = {{ lanlan_name: '咪咪' }};
  if (multi) {{
    const channels = [];
    class Channel {{
      constructor(name) {{ this.name = name; this.closed = false; channels.push(this); }}
      close() {{ this.closed = true; this.onmessage = null; }}
      postMessage(data) {{
        if (this.closed) throw Error('closed channel');
        for (const peer of channels) if (!peer.closed && peer !== this && peer.name === this.name && peer.onmessage) peer.onmessage({{ data }});
      }}
    }}
    pet.channels = channels;
    pet.ctx.__NEKO_MULTI_WINDOW__ = true;
    pet.ctx.BroadcastChannel = Channel;
    load(pet.ctx, [DISPLAY_RELAY]);
    h.win.location.pathname = '/chat';
    h.win.__NEKO_MULTI_WINDOW__ = true;
    h.sandbox.BroadcastChannel = Channel;
  }} else {{ h.win.nekoVisitParentBridge = pet.bridge; }}
  vm.runInContext(fs.readFileSync(DISPLAY_RELAY, 'utf8'), h.sandbox);
  pet.bridge.onStateChange({{ type: 'visit_state_change', action: 'invite_ready', side: 'host', visit_id: VID }});
  return pet;
}}
"""


@pytest.mark.parametrize("phase", ["pending", "invite_ready"])
@pytest.mark.parametrize("reconnecting", [False, True])
def test_reload_keeps_the_invitation_and_restores_friend_and_waiting_input(phase, reconnecting):
    out = run_visit_js(
        f"""
        const h = makeChat({{ routes: {{ [STATE_URL]: async () => ({{ status: 200,
          body: snapshot({{ phase: {json.dumps(phase)}, reconnecting: {json.dumps(reconnecting)} }}) }}) }} }});
        const hub = installHub(h);
        hub._state.invitations.set(FRIEND, {{ visitId: VID, phase: 'invite_ready', expiresAt: 9e9 }});
        h.advance(5000); // 社区先恢复，串门状态仍未读回：不能把缓存当成过期邀请删掉。
        const retainedBeforeBind = hub._state.invitations.has(FRIEND);
        await h.chat.afterBind(h.socket, '咪咪');
        const view = h.I.getVisitChatView();
        return {{ retainedBeforeBind, state: h.chat.getState(), invitation: hub._state.invitations.get(FRIEND),
          disabled: view.composerDisabled, placeholder: view.inputPlaceholder,
          submitted: h.I.submitVisitChatText({{ text: 'hello', requestId: 'waiting' }}),
          toast: h.toasts.at(-1), mutations: h.fetches.filter(item => item.method === 'POST') }};
        """,
        files=(), extra_prelude=_prelude(),
    )
    assert out["retainedBeforeBind"] and out["state"]["ready"]
    assert out["state"]["phase"] == out["invitation"]["phase"] == phase
    assert out["state"]["targetUserId"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert out["state"]["inviteExpiresAt"] == 9e9
    assert not out["disabled"] and not out["submitted"]
    assert out["placeholder"] == "客人还没到等等再聊吧"
    assert out["toast"] == "visit.status.VISIT_INPUT_REFUSED_NOT_READY"
    assert out["mutations"] == []


def test_snapshot_rebuilds_the_cancel_control_even_without_a_cached_invitation():
    out = run_visit_js(
        """
        const h = makeChat({ routes: { [STATE_URL]: async () => ({ status: 200, body: snapshot() }) } });
        const hub = installHub(h);
        await h.chat.afterBind(h.socket, '咪咪');
        return { invitation: hub._state.invitations.get(FRIEND) };
        """,
        files=(), extra_prelude=_prelude(),
    )
    assert out["invitation"]["phase"] == "invite_ready"
    assert out["invitation"]["expiresAt"] == 9e9


@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("status", [200, 404, 503])
def test_cancel_after_reload_releases_both_text_and_voice_only_when_backend_releases(multi, status):
    out = run_visit_js(
        f"""
        let reads = 0;
        const h = makeChat({{ routes: {{
          [STATE_URL]: async () => ({{ status: 200, body: ++reads <= 2 ? snapshot() : {{ active: false, enabled: true }} }}),
          '/api/visit/route/end': async () => ({{ status: {status}, body: {{}} }}),
        }} }});
        const hub = installHub(h);
        const pet = connectMedia(h, {json.dumps(multi)});
        await h.chat.afterBind(h.socket, '咪咪');
        const before = {{ text: h.chat.isActive(), voice: pet.bridge.blocksOrdinaryVoice() }};
        const result = await h.chat.cancelDirectInvitation(VID);
        const afterRequest = {{ text: h.chat.isActive(), voice: pet.bridge.blocksOrdinaryVoice(), phase: h.chat.getState().phase }};
        h.advance(5000); await h.settle();
        const duringCleanup = h.chat.isActive();
        h.advance(5000); await h.settle();
        return {{ before, result, afterRequest, duringCleanup, reads,
          after: {{ text: h.chat.isActive(), voice: pet.bridge.blocksOrdinaryVoice(), invitations: hub._state.invitations.size }} }};
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out["before"] == {"text": True, "voice": True}
    if status == 200:
        assert out["result"] and out["afterRequest"]["phase"] == "cancelling"
        assert out["afterRequest"]["text"] and out["afterRequest"]["voice"] and out["duringCleanup"]
        assert out["reads"] == 3
    elif status == 404:
        assert out["result"] and not out["afterRequest"]["text"] and not out["afterRequest"]["voice"]
    else:
        assert not out["result"] and out["afterRequest"]["phase"] == "invite_ready"
        assert out["after"] == {"text": True, "voice": True, "invitations": 1}
        return
    assert out["after"] == {"text": False, "voice": False, "invitations": 0}


def test_cancel_poll_does_not_restore_a_waiting_phase_over_a_newer_live_frame():
    out = run_visit_js(
        """
        let release;
        const gate = new Promise(r => { release = r; });
        const h = makeChat({ routes: {
          '/api/visit/route/end': async () => ({ status: 200, body: {} }),
          [STATE_URL]: async () => { await gate; return { status: 200, body: snapshot() }; },
        } });
        h.state('invite_ready', { target_user_id: FRIEND });
        await h.chat.cancelDirectInvitation(VID);
        h.advance(5000); await h.settle();
        h.state('started');
        release(); await h.settle();
        return { phase: h.chat._test.state().visit.phase };
        """,
        files=(), extra_prelude=_prelude(),
    )
    assert out["phase"] == "active"


@pytest.mark.parametrize("status", [200, 404, 503])
def test_hub_cancel_control_waits_for_authoritative_completion(status):
    out = run_visit_js(
        f"""
        let reads = 0;
        const h = makeChat({{ routes: {{
          [STATE_URL]: async () => ({{ status: 200, body: ++reads === 1 ? snapshot() : {{ active: false, enabled: true }} }}),
          '/api/visit/route/end': async () => ({{ status: {status}, body: {{}} }}),
        }} }});
        const hub = installHub(h);
        await h.chat.afterBind(h.socket, '咪咪');
        await hub._cancelInvitation(FRIEND, hub._state.invitations.get(FRIEND));
        const afterResponse = hub._state.invitations.get(FRIEND);
        const phase = afterResponse ? afterResponse.phase : '';
        h.advance(5000); await h.settle();
        return {{ phase, remaining: hub._state.invitations.size }};
        """,
        files=(), extra_prelude=_prelude(),
    )
    assert out["phase"] == {200: "cancelling", 404: "", 503: "invite_ready"}[status]
    assert out["remaining"] == (1 if status == 503 else 0)


@pytest.mark.parametrize("action", ["ending", "ended"])
def test_late_cancel_response_never_reblocks_input_after_ending(action):
    out = run_visit_js(
        f"""
        const h = makeChat({{ routes: {{
          '/api/visit/route/end': async () => {{ h.state({json.dumps(action)}); return {{ status: 200, body: {{}} }}; }},
        }} }});
        h.state('invite_ready', {{ target_user_id: FRIEND }});
        await h.chat.cancelDirectInvitation(VID);
        return {{ blocked: h.chat.isActive() }};
        """,
        files=(), extra_prelude=_prelude(),
    )
    assert not out["blocked"]


@pytest.mark.parametrize("outcome", ["ended", "still_waiting", "new_visit", "network_error"])
def test_refresh_after_cancel_reconciles_pet_without_clearing_a_valid_invitation(outcome):
    out = run_visit_js(
        f"""
        const h = makeChat({{ routes: {{ [STATE_URL]: async () => ({{ status: 200,
          body: {{ active: false, enabled: true, visit_id: null }} }}) }} }});
        const pet = connectMedia(h, true);
        let respond;
        pet.ctx.fetch = () => new Promise(r => {{ respond = r; }});
        await h.chat.afterBind(h.socket, '咪咪');
        await settle();
        const beforeConfirmation = pet.bridge.blocksOrdinaryVoice();
        if ({json.dumps(outcome)} === 'new_visit') pet.bridge.onStateChange({{
          type: 'visit_state_change', action: 'pending', side: 'host', visit_id: 'B'.repeat(22)
        }});
        respond({{ ok: {json.dumps(outcome != 'network_error')}, json: async () =>
          ({json.dumps(outcome)} === 'still_waiting' ? snapshot() : {{ active: false, visit_id: null }}) }});
        await settle();
        return {{ beforeConfirmation, blocked: pet.bridge.blocksOrdinaryVoice(), visitId: pet.bridge._state.visitId }};
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out["beforeConfirmation"]
    assert out["blocked"] is (outcome != "ended")
    assert out["visitId"] == (None if outcome == "ended" else "B" * 22 if outcome == "new_visit" else "A" * 22)


def test_late_idle_recovery_cannot_clear_an_invitation_restored_by_a_newer_snapshot():
    out = run_visit_js(
        """
        const pet = makeBridge();
        const replies = [];
        pet.ctx.fetch = () => new Promise(resolve => replies.push(resolve));
        const oldRequest = pet.bridge.recoverAfterReload('咪咪');
        await settle();
        const newRequest = pet.bridge.recoverAfterReload('咪咪');
        await settle();
        replies[1]({ ok: true, json: async () => snapshot() });
        await newRequest;
        replies[0]({ ok: true, json: async () => ({ active: false, visit_id: null }) });
        await oldRequest;
        return { blocked: pet.bridge.blocksOrdinaryVoice(), visitId: pet.bridge._state.visitId };
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out == {"blocked": True, "visitId": "A" * 22}


@pytest.mark.parametrize("first_response", [0, 1])
@pytest.mark.parametrize("new_active", [False, True])
@pytest.mark.parametrize("has_frame", [False, True])
def test_latest_recovery_request_wins_without_changing_the_live_frame_generation(first_response, new_active, has_frame):
    out = run_visit_js(
        f"""
        const pet = makeBridge();
        if ({json.dumps(has_frame)}) pet.bridge.onStateChange({{
          type: 'visit_state_change', action: 'invite_ready', visit_id: VID, side: 'host' }});
        const liveGen = pet.bridge._state.liveGen;
        const replies = [];
        pet.ctx.fetch = () => new Promise(resolve => replies.push(resolve));
        const oldRequest = pet.bridge.recoverAfterReload('咪咪');
        await settle();
        const newRequest = pet.bridge.recoverAfterReload('咪咪');
        await settle();
        const snapshots = {json.dumps(new_active)} ? [{{ active: false }}, snapshot()] : [snapshot(), {{ active: false }}];
        for (const i of [{first_response}, {1 - first_response}]) {{
          replies[i]({{ ok: true, json: async () => snapshots[i] }});
          await settle();
        }}
        return {{ oldApplied: await oldRequest, newApplied: await newRequest,
          blocked: pet.bridge.blocksOrdinaryVoice(), unchangedLiveGen: pet.bridge._state.liveGen === liveGen }};
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out == {"oldApplied": False, "newApplied": True, "blocked": new_active, "unchangedLiveGen": True}


@pytest.mark.parametrize("change", ["none", "new_visit", "new_phase", "new_character", "pagehide"])
def test_idle_reconciliation_retries_after_failure_but_stops_for_a_changed_target(change):
    out = run_visit_js(
        f"""
        const h = makeChat({{ routes: {{ [STATE_URL]: async () => ({{ status: 200,
          body: {{ active: false, enabled: true, visit_id: null }} }}) }} }});
        const pet = connectMedia(h, true);
        let calls = 0;
        pet.ctx.fetch = async () => {{
          if (++calls === 1) return {{ ok: false, status: 503 }};
          return {{ ok: true, json: async () => ({{ active: false, visit_id: null }}) }};
        }};
        await h.chat.afterBind(h.socket, '咪咪');
        await settle();
        const blockedAfterFailure = pet.bridge.blocksOrdinaryVoice();
        if ({json.dumps(change)} === 'new_visit') pet.bridge.onStateChange({{
          type: 'visit_state_change', action: 'pending', side: 'host', visit_id: 'B'.repeat(22)
        }});
        if ({json.dumps(change)} === 'new_phase') pet.bridge.onStateChange({{
          type: 'visit_state_change', action: 'started', side: 'host', visit_id: VID
        }});
        if ({json.dumps(change)} === 'new_character') pet.ctx.lanlan_config.lanlan_name = 'Other';
        if ({json.dumps(change)} === 'pagehide') pet.ctx.dispatchEvent(new pet.ctx.CustomEvent('pagehide'));
        await advance(1000);
        await settle();
        return {{ blockedAfterFailure, calls, blocked: pet.bridge.blocksOrdinaryVoice() }};
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out["blockedAfterFailure"]
    assert out["calls"] == (2 if change == "none" else 1)
    assert out["blocked"] is (change != "none")


def test_idle_reconciliation_recovers_after_the_parent_fetch_retry_window_is_exhausted():
    out = run_visit_js(
        """
        const h = makeChat({ routes: { [STATE_URL]: async () => ({ status: 200,
          body: { active: false, enabled: true, visit_id: null } }) } });
        const pet = connectMedia(h, true);
        let calls = 0;
        pet.ctx.fetch = async () => {
          if (++calls <= 4) throw Error('offline');
          return { ok: true, json: async () => ({ active: false, visit_id: null }) };
        };
        await h.chat.afterBind(h.socket, '咪咪');
        await settle();
        await advance(7000);
        const afterFailures = pet.bridge.blocksOrdinaryVoice();
        await advance(1000);
        return { afterFailures, calls, blocked: pet.bridge.blocksOrdinaryVoice() };
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out == {"afterFailures": True, "calls": 5, "blocked": False}


def test_repeated_idle_notifications_share_one_pending_reconciliation():
    out = run_visit_js(
        """
        const h = makeChat({ routes: { [STATE_URL]: async () => ({ status: 200,
          body: { active: false, enabled: true, visit_id: null } }) } });
        const pet = connectMedia(h, true);
        let calls = 0;
        let respond;
        pet.ctx.fetch = () => { calls++; return new Promise(resolve => { respond = resolve; }); };
        await h.chat.afterBind(h.socket, '咪咪');
        await settle();
        h.win.nekoRelayVisitIdleToDisplay();
        h.win.nekoRelayVisitIdleToDisplay();
        await settle();
        const pendingCalls = calls;
        respond({ ok: true, json: async () => ({ active: false, visit_id: null }) });
        await settle();
        await advance(31000);
        return { pendingCalls, calls, blocked: pet.bridge.blocksOrdinaryVoice() };
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out == {"pendingCalls": 1, "calls": 1, "blocked": False}


@pytest.mark.parametrize("still_waiting", [False, True])
def test_bfcache_return_reopens_channels_and_reconciles_while_preserving_valid_invites(still_waiting):
    out = run_visit_js(
        f"""
        const h = makeChat({{ routes: {{ [STATE_URL]: async () => ({{ status: 200,
          body: {{ active: false, enabled: true, visit_id: null }} }}) }} }});
        const pet = connectMedia(h, true);
        let calls = 0;
        pet.ctx.fetch = async () => {{
          if (++calls === 1) return {{ ok: false, status: 503 }};
          return {{ ok: true, json: async () => ({json.dumps(still_waiting)} ? snapshot() : {{ active: false, visit_id: null }}) }};
        }};
        await h.chat.afterBind(h.socket, '咪咪');
        await settle();
        function lifecycle(ctx, type) {{ ctx.dispatchEvent({{ type, persisted: true }}); }}
        lifecycle(pet.ctx, 'pagehide');
        lifecycle(h.win, 'pagehide');
        const closed = pet.channels.every(channel => channel.closed);
        await advance(1000);
        const hiddenCalls = calls;
        lifecycle(h.win, 'pageshow');
        lifecycle(pet.ctx, 'pageshow');
        await settle();
        const blockedAfterReturn = pet.bridge.blocksOrdinaryVoice();
        const reopened = pet.channels.filter(channel => !channel.closed).length;
        function relay(action, visitId) {{ h.win.nekoRelayVisitStateToDisplay({{
          type: 'visit_state_change', action, side: 'host', visit_id: visitId
        }}); }}
        relay('pending', 'B'.repeat(22));
        const receivesNewInvite = pet.bridge.getVisitId() === 'B'.repeat(22);
        // 再次缓存/返回仍要关闭旧通道并恢复接收，不能只处理第一次 pagehide。
        lifecycle(pet.ctx, 'pagehide');
        lifecycle(h.win, 'pagehide');
        lifecycle(h.win, 'pageshow');
        lifecycle(pet.ctx, 'pageshow');
        relay('ended', 'B'.repeat(22));
        await settle();
        return {{ closed, hiddenCalls, reopened, blockedAfterReturn, receivesNewInvite,
          receivesEnd: !pet.bridge.blocksOrdinaryVoice() }};
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out["closed"] and out["hiddenCalls"] == 1 and out["reopened"] == 2
    assert out["blockedAfterReturn"] is still_waiting
    assert out["receivesNewInvite"] and out["receivesEnd"]


def test_ordinary_pageshow_does_not_start_idle_reconciliation():
    out = run_visit_js(
        """
        const h = makeChat();
        const pet = connectMedia(h, true);
        let calls = 0;
        pet.ctx.fetch = async () => { calls++; return { ok: false, status: 503 }; };
        pet.ctx.dispatchEvent({ type: 'pageshow', persisted: false });
        h.win.dispatchEvent({ type: 'pageshow', persisted: false });
        await settle();
        return { calls, channels: pet.channels.length };
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out == {"calls": 0, "channels": 2}


@pytest.mark.parametrize("phase", ["active", "wrap_up"])
def test_cached_guest_applies_missed_phase_without_replacing_transport_and_resumes_capture(phase):
    out = run_visit_js(
        f"""
        const h = makeChat();
        const pet = connectMedia(h, true);
        const frame = await open(pet, 'guest');
        change(pet, 'joining');
        pet.ctx.dispatchEvent({{ type: 'pagehide', persisted: true }});
        h.win.nekoRelayVisitStateToDisplay({{ type: 'visit_state_change', action: 'started',
          side: 'guest', visit_id: VISIT_ID }});
        const before = pet.bridge.getPhase();
        pet.ctx.fetch = async () => ({{ ok: true, json: async () => snapshot({{
          visit_id: VISIT_ID, side: 'guest', phase: {json.dumps(phase)} }}) }});
        pet.ctx.dispatchEvent({{ type: 'pageshow', persisted: true }});
        await settle();
        return {{ before, phase: pet.bridge.getPhase(), sameFrame: pet.bridge._state.frame === frame,
          capturing: pet.ctx.live2dManager._visitCaptureActive,
          postedPhase: pet.child.posted.filter(m => m.t === 'visit_state').at(-1).phase }};
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out == {"before": "joining", "phase": phase, "sameFrame": True, "capturing": True, "postedPhase": phase}


@pytest.mark.parametrize("outcome", ["waiting", "idle", "retry", "newer_live"])
def test_cached_idle_pet_checks_for_missed_invitations_before_allowing_voice(outcome):
    out = run_visit_js(
        f"""
        const h = makeChat();
        const pet = connectMedia(h, true);
        pet.bridge.onStateChange({{ type: 'visit_state_change', action: 'ended', side: 'host', visit_id: VID }});
        pet.ctx.dispatchEvent({{ type: 'pagehide', persisted: true }});
        h.win.nekoRelayVisitStateToDisplay({{ type: 'visit_state_change', action: 'pending',
          side: 'host', visit_id: 'B'.repeat(22) }});
        let calls = 0;
        let respond;
        pet.ctx.fetch = () => {{ calls++; return new Promise(resolve => {{ respond = resolve; }}); }};
        const idleBeforeReturn = !pet.bridge.blocksOrdinaryVoice();
        pet.ctx.dispatchEvent({{ type: 'pageshow', persisted: true }});
        const checking = pet.ctx.isNekoVisitVoiceBlocked();
        await settle();
        if ({json.dumps(outcome)} === 'newer_live') h.win.nekoRelayVisitStateToDisplay({{
          type: 'visit_state_change', action: 'pending', side: 'host', visit_id: 'C'.repeat(22) }});
        respond({{ ok: {json.dumps(outcome != 'retry')}, json: async () =>
          ({json.dumps(outcome)} === 'idle' ? {{ active: false }} : snapshot({{ visit_id: 'B'.repeat(22) }})) }});
        await settle();
        const afterFirst = pet.ctx.isNekoVisitVoiceBlocked();
        if ({json.dumps(outcome)} === 'retry') {{
          await advance(1000);
          respond({{ ok: true, json: async () => snapshot({{ visit_id: 'B'.repeat(22) }}) }});
          await settle();
        }}
        const beforeDuplicateIdle = calls;
        h.win.nekoRelayVisitIdleToDisplay();
        await settle();
        return {{ idleBeforeReturn, checking, afterFirst, calls: beforeDuplicateIdle,
          blocked: pet.ctx.isNekoVisitVoiceBlocked(), visitId: pet.bridge.getVisitId() }};
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out["idleBeforeReturn"] and out["checking"]
    assert out["afterFirst"] is (outcome != "idle")
    assert out["calls"] == (2 if outcome == "retry" else 1)
    assert out["blocked"] is (outcome != "idle")
    assert out["visitId"] == ("" if outcome == "idle" else ("C" if outcome == "newer_live" else "B") * 22)


@pytest.mark.parametrize("change", ["newer_phase", "different_visit"])
def test_existing_transport_recovery_preserves_newer_lifecycle_and_other_visit(change):
    out = run_visit_js(
        f"""
        const pet = makeBridge();
        const frame = await open(pet, 'guest');
        change(pet, 'joining');
        let respond;
        pet.ctx.fetch = () => new Promise(resolve => {{ respond = resolve; }});
        const request = pet.bridge.recoverAfterReload('咪咪');
        await settle();
        if ({json.dumps(change)} === 'newer_phase') change(pet, 'wrap_up');
        respond({{ ok: true, json: async () => snapshot({{ side: 'guest', phase: 'active',
          visit_id: {json.dumps(change)} === 'different_visit' ? 'B'.repeat(22) : VISIT_ID }}) }});
        const applied = await request;
        return {{ applied, sameFrame: pet.bridge._state.frame === frame, phase: pet.bridge.getPhase(),
          visitId: pet.bridge.getVisitId() === VISIT_ID }};
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    assert out == {"applied": False, "sameFrame": True, "phase": "wrap_up" if change == "newer_phase" else "joining", "visitId": True}


@pytest.mark.parametrize("failure", ["nonlocal", "missing_endpoint", "csrf", "server"])
@pytest.mark.parametrize("has_invite", [False, True])
def test_return_releases_temporary_voice_gate_when_visits_are_unavailable_but_keeps_known_invites(failure, has_invite):
    out = run_visit_js(
        f"""
        const h = makeChat();
        const pet = connectMedia(h, true);
        if (!{json.dumps(has_invite)}) pet.bridge.onStateChange({{
          type: 'visit_state_change', action: 'ended', side: 'host', visit_id: VID }});
        pet.ctx.dispatchEvent({{ type: 'pagehide', persisted: true }});
        const failure = {json.dumps(failure)};
        let calls = 0;
        pet.ctx.fetch = async () => {{ calls++; return {{ ok: false,
          status: failure === 'server' ? 503 : failure === 'missing_endpoint' ? 404 : 403,
          json: async () => ({{ code: failure === 'nonlocal' ? 'VISIT_E_UNAUTHORIZED' : 'invalid_csrf_token' }})
        }}; }};
        pet.ctx.dispatchEvent({{ type: 'pageshow', persisted: true }});
        const duringCheck = pet.ctx.isNekoVisitVoiceBlocked();
        await settle();
        const afterCheck = pet.ctx.isNekoVisitVoiceBlocked();
        await advance(1000);
        return {{ duringCheck, afterCheck, calls, knownInvite: pet.bridge.blocksOrdinaryVoice() }};
        """,
        files=BRIDGE_FILES, extra_prelude=_prelude(),
    )
    unavailable = failure in {"nonlocal", "missing_endpoint"}
    assert out == {"duringCheck": True, "afterCheck": has_invite or not unavailable,
                   "calls": 1 if unavailable else 2, "knownInvite": has_invite}
