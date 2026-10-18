"""Static gates and fake-SDK behaviour of the visit transport page (design §5 PR-10)."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from tests.visit_js_harness import PROJECT_ROOT, TRANSPORT_DIR, run_visit_js

TEMPLATE = PROJECT_ROOT / "templates" / "visit_transport.html"
LIBS = PROJECT_ROOT / "static" / "libs"
NOTICES = LIBS / "THIRD_PARTY_NOTICES.md"
SDK_FILES = {"trtc": LIBS / "trtc.js", "livekit": LIBS / "livekit-client.umd.js"}


def _src(name: str) -> str:
    return (TRANSPORT_DIR / name).read_text(encoding="utf-8")


def _all_transport_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(TRANSPORT_DIR.glob("*.js"))}


def _code_only(src: str) -> str:
    """Drop // line comments and /* */ blocks (the gates look at code, not prose)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;{}),\s])//[^\n]*", "", src)


def _function_body(src: str, header_regex: str) -> str:
    m = re.search(header_regex, src)
    assert m, header_regex
    depth = 0
    start = src.index("{", m.end() - 1)
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unbalanced braces")


# -- Static gates --


def test_template_only_references_static_and_no_vendor_sdk():
    html = TEMPLATE.read_text(encoding="utf-8")
    refs = re.findall(r"""(?:src|href)=["']([^"']+)["']""", html)
    assert refs, "template must load the transport scripts"
    assert all(r.startswith("/static/visit/transport/") and "?v={{ static_asset_version }}" in r for r in refs)
    assert "trtc.js" not in html and "livekit-client" not in html
    assert "<video" in html and "muted" in html and "playsinline" in html
    video_rule = re.search(r"#visit-video\s*\{([^}]*)\}", html).group(1).replace(" ", "")
    assert "display:none" not in video_rule and "width:2px" in video_rule and "opacity:0.01" in video_rule
    assert re.search(r"html,\s*body\s*\{[^}]*background:\s*transparent", html)


def test_only_backend_ws_opens_a_websocket_and_only_to_location_host():
    sources = _all_transport_sources()
    owners = [n for n, s in sources.items() if "new WebSocket(" in _code_only(s)]
    assert owners == ["backend-ws.js"]
    ws = _code_only(sources["backend-ws.js"])
    assert ws.count("new WebSocket(") == 1
    assert "loc.host" in ws and "'/api/visit/transport/ws?visit_id='" in ws
    assert "fetch(" not in ws
    for name, s in sources.items():
        assert "page_config" not in s, name
    onopen = _function_body(ws, r"ws\.onopen = function \(\)")
    assert "type: 'auth', csrf_token: self.token" in onopen
    assert onopen.index("type: 'auth'") < onopen.index("self.hooks.onOpen()")


def test_backend_close_stops_media_and_leaves_before_any_reconnect():
    ws = _code_only(_src("backend-ws.js"))
    onclose = _function_body(ws, r"ws\.onclose = function \(event\)")
    stop = onclose.index("stopAllMedia(")
    leave = onclose.index("leaveRoom(")
    assert stop < onclose.index("setTimeout(") and leave < onclose.index("setTimeout(")
    # 4409 stops media only, without leaving the room or reconnecting.
    assert "code !== CLOSE_SUPERSEDED ? self.hooks.leaveRoom('backend_socket_closed') : null" in onclose
    # Reconnection waits for the previous room exit to finish.
    assert "Promise.all([delayDone, leftOrGaveUp])" in onclose


def test_frame_sink_object_is_built_once_with_three_entry_points():
    sources = _all_transport_sources()
    assigners = {n: s.count("__nekoVisitFrameSink =") for n, s in sources.items() if "__nekoVisitFrameSink =" in s}
    assert assigners == {"frame-sink.js": 1}
    body = _function_body(sources["frame-sink.js"], r"root\.__nekoVisitFrameSink = ")
    for key in ("onFrame:", "suspend:", "setAuthToken:"):
        assert key in body


def test_vendor_parameters_are_pinned():
    lk = _code_only(_src("livekit-transport.js"))
    for literal in ("scalabilityMode: 'L1T1'", "simulcast: false", "autoSubscribe: false", "'maintain-framerate'",
                    "adaptiveStream: false", "dynacast: false"):
        assert literal in lk, literal
    assert "setMicrophoneEnabled" not in lk and "createLocalAudioTrack" not in lk
    trtc = _code_only(_src("trtc-transport.js"))
    for literal in ("autoReceiveVideo: false", "autoReceiveAudio: false", "view: null", "'SmallStreamAutoSwitcher'"):
        assert literal in trtc, literal
    for name, s in _all_transport_sources().items():
        assert "startLocalAudio" not in s, name
        assert "visibilitychange" not in s, name


def test_sizes_come_only_from_the_framing_tables():
    pack = _code_only(_src("pack.js"))
    assert "alpha: false" in pack and "requestFrame()" in pack and "captureStream(0)" in pack
    assert "contentHint = 'motion'" in pack
    for name in ("trtc-transport.js", "unpack.js", "livekit-transport.js", "loader.js"):
        code = _code_only(_src(name))
        assert not re.search(r"\b(896|448|1120|560|320|256)\b", code.replace("560) * 1000", "")), name
    # pack.js: size numbers appear only in CROP_GEOMETRY and quality-ladder definitions.
    tables = re.search(r"var CROP_GEOMETRY = \{.*?\};\s*.*?var CONGESTION_LADDER = \{.*?\};", pack, re.S)
    assert tables
    rest = pack.replace(tables.group(0), "")
    assert not re.search(r"\b(896|448|1120|560|320|256|352|272|208|160|192)\b", rest)


def test_preflight_does_not_touch_vendor_globals():
    loader = _code_only(_src("loader.js"))
    preflight = _function_body(loader, r"function runPreflight\(win\)")
    assert "TRTC" not in preflight and "LivekitClient" not in preflight
    assert "isSecureContext" in preflight and "RTCPeerConnection" in preflight
    assert "captureStream" not in preflight and "webgl" not in preflight.lower()


def test_vendor_sdks_are_registered_pinned_and_hashed():
    notices = NOTICES.read_text(encoding="utf-8")
    assert "## trtc-sdk-v5" in notices and "## livekit-client" in notices
    assert "`5.20.1`" in notices and "`2.22.3`" in notices
    loader = _src("loader.js")
    for transport, path in SDK_FILES.items():
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() in notices, transport
        sri = "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode()
        assert f"{transport}: '{sri}'" in loader, transport
    assert (LIBS / "licenses" / "TRTC.LICENSE").is_file()
    assert "Apache License" in (LIBS / "licenses" / "LIVEKIT.LICENSE").read_text(encoding="utf-8")
    nuitka = (PROJECT_ROOT / "scripts" / "check_nuitka_dist.py").read_text(encoding="utf-8")
    for line in ('("static/libs", "trtc.js")', '("static/libs", "livekit-client.umd.js")',
                 '("static/libs/licenses", "TRTC.LICENSE")', '("static/libs/licenses", "LIVEKIT.LICENSE")'):
        assert line in nuitka, line


# -- Fake SDK behavior --


def test_fragment_pacing_stays_inside_the_vendor_window():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        const start = clock.now;
        for (let k = 0; k < 3; k++) h.c.core.send(2, { t: 'text', v: 1, seq: k + 1, ln: 'g:' + k, txt: '喵'.repeat(2600) });
        for (let k = 0; k < 12; k++) h.c.core.send(2, { t: 'line_delta', v: 1, ln: 'g:9', i: k, txt: 'abc'.repeat(50) });
        await advance(8000);
        const sends = h.log.filter((e) => e[0] === 'send');
        let worstBytes = 0, worstPieces = 0;
        for (const s of sends) {
          const win = sends.filter((x) => x[1] >= s[1] && x[1] < s[1] + 1000);
          worstBytes = Math.max(worstBytes, win.reduce((a, x) => a + x[3], 0));
          worstPieces = Math.max(worstPieces, win.length);
        }
        const firstSecond = sends.filter((x) => x[1] < start + 1000).reduce((a, x) => a + x[3], 0);
        return { total: sends.length, worstBytes, worstPieces, firstSecond, lastAt: sends[sends.length - 1][1] - start };
        """
    )
    assert out["worstBytes"] <= 6144
    assert out["worstPieces"] <= 20
    assert out["firstSecond"] <= 6144
    assert out["lastAt"] < 6000
    assert out["total"] >= 3 * 7 + 12


def test_backend_socket_drop_stops_media_leaves_and_reconnects_with_same_vid():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const firstTrack = h.c.packer.track;
        h.log.length = 0;
        h.ws.serverClose(1006);
        const syncKinds = kinds(h.log);
        const socketsBefore = h.sockets.length;
        await advance(1100);
        const ws2 = h.sockets[h.sockets.length - 1];
        ws2.serverOpen();
        const firstFrames = ws2.sent.map((m) => m.type + (m.stage ? ':' + m.stage : ''));
        ws2.serverSend(credsFor('guest'));
        await settle();
        ws2.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const enter = h.log.filter((e) => e[0] === 'enterRoom');
        const publish = h.log.filter((e) => e[0] === 'startLocalVideo');
        return {
          syncKinds, reconnected: h.sockets.length === socketsBefore + 1, firstFrames,
          enterUser: enter.map((e) => e[1]), trackReused: publish.length === 1 && publish[0][1] === firstTrack.id,
          trackLive: firstTrack.readyState, states: ws2.sent.filter((m) => m.type === 'state').map((m) => m.state),
        };
        """
    )
    # Execute synchronously: stop publishing, then leave the room, both before reconnection one second later.
    assert out["syncKinds"][:2] == ["stopLocalVideo", "exitRoom"]
    assert out["reconnected"]
    assert out["firstFrames"][:2] == ["auth", "caps:preflight"]
    assert out["enterUser"] == ["g_self"]
    assert out["trackReused"] and out["trackLive"] == "live"
    assert "joined" in out["states"]


def test_ended_track_is_rebuilt_before_republishing():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const old = h.c.packer.track;
        h.ws.serverSend({ type: 'media', publish: false });
        await settle();
        old.stop();
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const pub = h.log.filter((e) => e[0] === 'startLocalVideo').map((e) => e[1]);
        return { pub, oldId: old.id, streams: h.c.packer.pack.streams };
        """
    )
    assert out["pub"][0] == out["oldId"]
    assert out["pub"][1] != out["oldId"]
    assert out["streams"] == 2


def test_superseded_4409_is_terminal_and_does_not_leave():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.log.length = 0;
        const n = h.sockets.length;
        h.ws.serverClose(4409);
        // 被顶号时不立刻离房（接替的页面已经入房）；15 s 后的兜底退房另有测试覆盖
        await advance(14000);
        // 新 iframe 同 vid 入房把这里顶下线：不触发任何上报
        h.trtc.emit('KICKED_OUT', { reason: 'kick' });
        return { kinds: kinds(h.log), newSockets: h.sockets.length - n, closed: h.posted.filter((m) => m.t === 'closed') };
        """
    )
    assert "stopLocalVideo" in out["kinds"]
    assert "exitRoom" not in out["kinds"]
    assert out["newSockets"] == 0
    assert out["closed"] == [{"t": "closed", "code": 4409}]


def test_unknown_visit_4404_leaves_once_and_stops_for_good():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        h.log.length = 0;
        const n = h.sockets.length;
        h.ws.serverClose(4404);
        await advance(30000);
        return { exits: h.log.filter((e) => e[0] === 'exitRoom').length, newSockets: h.sockets.length - n,
                 closed: h.posted.filter((m) => m.t === 'closed').length };
        """
    )
    assert out == {"exits": 1, "newSockets": 0, "closed": 1}


def test_reconnect_gives_up_after_twenty_seconds():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverClose(1006);
        for (let k = 0; k < 40; k++) {
          await advance(1000);
          const last = h.sockets[h.sockets.length - 1];
          if (last.readyState !== 3) last.serverClose(1006);
        }
        return { sockets: h.sockets.length, closed: h.posted.filter((m) => m.t === 'closed').length,
                 exits: h.log.filter((e) => e[0] === 'exitRoom').length };
        """
    )
    assert out["closed"] == 1
    assert out["sockets"] <= 23
    assert out["exits"] >= 1


def test_text_only_snapshots_never_publish():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: false, crop: 'upper', ladder: 0 });
        await settle();
        h.ws.serverClose(1006);
        await advance(1100);
        const ws2 = h.sockets[h.sockets.length - 1];
        ws2.serverOpen();
        ws2.serverSend(credsFor('guest'));
        await settle();
        ws2.serverSend({ type: 'media', publish: false, crop: 'upper', ladder: 0 });
        await settle();
        const host = await joinedController('host');
        host.ws.serverSend({ type: 'media', subscribe: false, peer_vid: 'g_peer' });
        host.trtc.emit('REMOTE_VIDEO_AVAILABLE', { userId: 'g_peer', streamType: 'main' });
        await settle();
        return { guestPublish: h.log.filter((e) => e[0] === 'startLocalVideo').length,
                 hostSubscribe: host.log.filter((e) => e[0] === 'startRemoteVideo').length };
        """
    )
    assert out == {"guestPublish": 0, "hostSubscribe": 0}


def test_refresh_credentials_are_accepted_without_rejoining_and_used_by_a_manual_rejoin():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend(credsFor('guest', { refresh: true, vendor: { trtc: { sdk_app_id: 1, user_id: 'g_self', user_sig: 'sig2', private_map_key: 'pmk2', str_room_id: VISIT_ID, expire: 600 } } }));
        h.ws.serverSend(credsFor('guest', { refresh: true, vendor: { trtc: { sdk_app_id: 1, user_id: 'g_self', user_sig: 'sig3', private_map_key: 'pmk3', str_room_id: VISIT_ID, expire: 600 } } }));
        await settle();
        const entersAfterRefresh = h.log.filter((e) => e[0] === 'enterRoom').length;
        // SDK 自动重连因旧凭证鉴权失败：手动 exitRoom + enterRoom，用最新凭证
        h.trtc.emit('CONNECTION_STATE_CHANGED', { prevState: 'RECONNECTING', state: 'DISCONNECTED' });
        await settle();
        const enters = h.log.filter((e) => e[0] === 'enterRoom');
        const states = h.ws.sent.filter((m) => m.type === 'state').map((m) => m.state);
        return { entersAfterRefresh, lastSig: enters[enters.length - 1][2], enterCount: enters.length, states };
        """
    )
    assert out["entersAfterRefresh"] == 1
    assert out["enterCount"] == 2
    assert out["lastSig"] == "sig3"
    # Full manual rejoin order: report reconnecting, then joining, then joined.
    tail = out["states"][out["states"].index("reconnecting"):]
    assert tail == ["reconnecting", "joining", "joined"]


def test_peer_presence_reports_only_explicit_leaves():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        h.trtc.emit('REMOTE_USER_ENTER', { userId: 'g_peer' });
        h.trtc.emit('REMOTE_USER_EXIT', { userId: 'g_peer', reason: 1 });
        h.trtc.emit('REMOTE_USER_ENTER', { userId: 'g_peer' });
        h.trtc.emit('REMOTE_USER_EXIT', { userId: 'g_peer', reason: 0 });
        const st = h.ws.sent.filter((m) => m.type === 'state');
        return st.map((m) => [m.peer_present, m.vendor_reason || null]);
        """
    )
    assert out[-2:] == [[True, None], [False, "0"]]
    assert [False, "1"] not in out


def test_parent_hidden_goes_up_as_state_hidden():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.c.onParentHidden(true);
        await advance(1500);
        h.c.onParentHidden(false);
        await advance(1500);
        return h.ws.sent.filter((m) => m.type === 'state').map((m) => m.hidden);
        """
    )
    assert out[-2:] == [True, False]


def test_preflight_rejects_a_wrapped_websocket():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const bad = ctx.NekoVisitTransport.loader.runPreflight(ctx);
        const insecure = ctx.NekoVisitTransport.loader.runPreflight(Object.assign({}, ctx, { isSecureContext: false }));
        const noRtc = ctx.NekoVisitTransport.loader.runPreflight(Object.assign({}, ctx, { WebSocket: globalThis.WebSocket || function WebSocket() {}, RTCPeerConnection: undefined }));
        return { bad: bad.reason, insecure: insecure.reason, noRtc: noRtc.reason, clean: ctx.NekoVisitTransport.loader.queryIsClean('?v=1&side=host&visit_id=x'), dirty: ctx.NekoVisitTransport.loader.queryIsClean('?v=1&trtcDebug=1') };
        """
    )
    assert out["bad"] == "foreign_websocket"
    assert out["insecure"] == "insecure_context"
    assert out["noRtc"] in ("no_webrtc", "foreign_websocket")
    assert out["clean"] is True and out["dirty"] is False


def test_first_frame_carries_a_96px_png_data_url():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        const video = h.c.unpacker.video;
        h.ws.serverSend({ type: 'media', subscribe: true, peer_vid: 'g_peer' });
        h.trtc.emit('REMOTE_VIDEO_AVAILABLE', { userId: 'g_peer', streamType: 'main' });
        await settle();
        Object.assign(video, { readyState: 4, videoWidth: 256, videoHeight: 1120 });
        h.c.unpacker.draw();
        await advance(50);
        const ff = h.posted.filter((m) => m.t === 'first_frame');
        return { n: ff.length, url: ff[0] && ff[0].dataURL };
        """
    )
    assert out["n"] == 1
    url = out["url"]
    assert url.startswith("data:image/png;base64,")
    png = base64.b64decode(url.split(",", 1)[1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(png[16:20], "big") == 96 and int.from_bytes(png[20:24], "big") == 96


def test_stop_unpublishes_leaves_reports_left_and_closes():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.log.length = 0;
        h.ws.serverSend({ type: 'stop', reason: 'route_end' });
        await settle();
        await advance(5000);
        return { kinds: kinds(h.log), lastState: h.ws.sent.filter((m) => m.type === 'state').pop().state,
                 closedWith: h.ws.closedWith, sockets: h.sockets.length, trackEnded: h.c.packer.track.readyState };
        """
    )
    assert out["kinds"][:2] == ["stopLocalVideo", "exitRoom"]
    assert out["lastState"] == "left"
    assert out["closedWith"] == 1000
    assert out["sockets"] == 1
    assert out["trackEnded"] == "ended"


def test_unknown_or_foreign_template_free_files():
    # Only the eight registered scripts belong in the directory; additions require matching template/test updates.
    names = sorted(p.name for p in TRANSPORT_DIR.glob("*.js"))
    assert names == sorted([
        "transport.js", "pack.js", "unpack.js", "trtc-transport.js", "livekit-transport.js",
        "frame-sink.js", "backend-ws.js", "loader.js",
    ])
    html = TEMPLATE.read_text(encoding="utf-8")
    assert [Path(r).name.split("?")[0] for r in re.findall(r'src="([^"]+)"', html)] == [
        "transport.js", "pack.js", "unpack.js", "trtc-transport.js", "livekit-transport.js",
        "frame-sink.js", "backend-ws.js", "loader.js",
    ]


_FAKE_LIVEKIT = """
function makeFakeLK(log) {
  const RoomEvent = {};
  ['ParticipantConnected', 'ParticipantDisconnected', 'TrackPublished', 'TrackUnpublished', 'TrackSubscribed',
   'TrackUnsubscribed', 'TrackSubscriptionFailed', 'DataReceived', 'Reconnecting', 'Reconnected', 'Disconnected', 'ConnectionQualityChanged']
    .forEach((n) => { RoomEvent[n] = n; });
  const sender = {
    params: { encodings: [{ maxBitrate: 560000, maxFramerate: 30 }] },
    getParameters() { return JSON.parse(JSON.stringify(this.params)); },
    setParameters(p) { this.params = p; log.push(['setParameters', p.encodings[0].maxBitrate, p.encodings[0].maxFramerate]); return Promise.resolve(); },
  };
  class Room {
    constructor(opts) { this.opts = opts; this.handlers = {}; this.remoteParticipants = new Map();
      this.localParticipant = {
        publishTrack: (t, o) => { log.push(['publishTrack', o.videoCodec]); return Promise.resolve({ track: { sender } }); },
        unpublishTrack: () => Promise.resolve(), publishData: () => Promise.resolve() };
      Room.last = this; }
    on(e, f) { this.handlers[e] = f; }
    emit(e, ...a) { this.handlers[e] && this.handlers[e](...a); }
    connect(url, token, o) { log.push(['connect', url, o.autoSubscribe]); return Promise.resolve(); }
    disconnect() { return Promise.resolve(); }
    removeAllListeners() {}
  }
  return { Room, RoomEvent, Track: { Source: { Camera: 'camera' } }, DisconnectReason: { 1: 'CLIENT_INITIATED' }, sender, isBrowserSupported: () => true };
}
"""


def test_failed_trtc_publish_is_retried_by_the_next_snapshot():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let fail = true;
        const orig = h.trtc.startLocalVideo;
        h.trtc.startLocalVideo = (c) => { if (fail) { fail = false; h.log.push(['startLocalVideo-fail']); return Promise.reject(new Error('transient')); } return orig(c); };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        return kinds(h.log).filter((k) => k.startsWith('startLocalVideo') || k === 'updateLocalVideo');
        """
    )
    assert out == ["startLocalVideo-fail", "startLocalVideo"]


def test_livekit_ladder_codec_stats_and_loopback():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const log = [];
        const LK = makeFakeLK(log);
        const events = [];
        const handlers = { onPiece() {}, onPeer() {}, onState(s) { events.push(s); }, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const creds = { vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp8', bitrate_kbps: 560, fps: 30 } };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join(creds);
        await a.publish({ id: 'pack' });
        const ok = await a.updateProfile({ width: 208, height: 896, frameRate: 15, bitrate: 400 });
        // 接收统计
        let bytes = 0, recv = 0, lost = 0;
        const receiver = { getStats: () => Promise.resolve(new Map([
          ['in', { type: 'inbound-rtp', kind: 'video', bytesReceived: bytes, packetsReceived: recv, packetsLost: lost }],
          ['cp', { type: 'candidate-pair', nominated: true, currentRoundTripTime: 0.12 }]]) ) };
        await a.setSubscribe(true);
        const remotePub = { kind: 'video', setSubscribed() {} };
        LK.Room.last.emit('TrackPublished', remotePub, { identity: 'g_peer' });
        LK.Room.last.emit('TrackSubscribed', { kind: 'video', mediaStreamTrack: {}, receiver }, remotePub, { identity: 'g_peer' });
        const first = await a.receiverStats(0);
        bytes = 350000; recv = 950; lost = 50;
        const second = await a.receiverStats(5000);
        // 编码能力门
        const caps = (enc, dec) => { ctx.RTCRtpSender = { getCapabilities: () => ({ codecs: enc.map((n) => ({ mimeType: 'video/' + n })) }) };
          ctx.RTCRtpReceiver = { getCapabilities: () => ({ codecs: dec.map((n) => ({ mimeType: 'video/' + n })) }) }; };
        caps(['VP8'], ['VP8']);
        const vp8Only = await LKV.capabilities(LK, { publish: { codec: 'vp9' } }, 'guest');
        const hostVp8Only = await LKV.capabilities(LK, { publish: { codec: 'vp9' } }, 'host');
        caps([], ['VP8', 'VP9']);
        const noEnc = await LKV.capabilities(LK, { publish: { codec: 'vp9' } }, 'guest');
        const hostBoth = await LKV.capabilities(LK, {}, 'host');
        return {
          ok, log, first, second, vp8Only, hostVp8Only: hostVp8Only.decode, noEnc: [noEnc.encode, noEnc.codec], hostBoth: hostBoth.decode,
          hosts: [
            LKV.hostAllowed('ws://127.0.0.1:7880', ['127.0.0.1']),
            LKV.hostAllowed('ws://[::1]:7880', ['::1']),
            LKV.hostAllowed('ws://localhost:7880', ['localhost']),
            LKV.hostAllowed('ws://lk.example.com', ['lk.example.com']),
            LKV.hostAllowed('wss://lk.example.com/x', ['lk.example.com']),
            LKV.hostAllowed('wss://evil.example.com', ['lk.example.com']),
          ],
        };
        """
    )
    assert out["ok"] is True
    assert ["setParameters", 400000, 15] in out["log"]
    assert ["connect", "wss://lk.example.com", False] in out["log"]
    assert out["first"] == {"rtt_ms": 120}
    assert out["second"]["rx_kbps"] == 560 and abs(out["second"]["loss_pct"] - 5) < 1e-9 and out["second"]["rtt_ms"] == 120
    assert out["vp8Only"]["encode"] is True and out["vp8Only"]["codec"] == "vp8"
    assert out["hostVp8Only"] is False
    assert out["noEnc"] == [False, None]
    assert out["hostBoth"] is True
    assert out["hosts"] == [True, True, True, False, True, False]


def test_codec_fallback_is_kept_across_credential_refresh():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.c.codecOverride = 'vp8';
        h.c.applyCodecOverride(h.c.creds);
        h.ws.serverSend(credsFor('guest', { refresh: true, publish: { codec: 'vp9', bitrate_kbps: 560, fps: 30 } }));
        await settle();
        return h.c.creds.publish.codec;
        """
    )
    assert out == "vp8"


def test_stalled_stream_reports_zero_rx_fps_in_fallback():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const doc = ctx.document;
        const video = doc.createElement('video');
        let decoded = 300;
        video.getVideoPlaybackQuality = () => ({ totalVideoFrames: decoded, droppedVideoFrames: 0 });
        const u = new ctx.NekoVisitTransport.unpack.Unpacker({ video, canvas: doc.createElement('canvas') });
        u.draws = 0;
        u.stats(0);
        u.draws = 150;             // 回落定时器 5 s 里重画了 150 次，但没有新帧
        const stalled = u.stats(5000).rx_fps;
        decoded = 450;
        const flowing = u.stats(10000).rx_fps;
        return { stalled, flowing };
        """
    )
    assert out == {"stalled": 0, "flowing": 30}


def test_reconnect_waits_for_a_slow_vendor_exit():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let releaseExit;
        h.trtc.exitRoom = () => { h.log.push(['exitRoom-slow']); return new Promise((r) => { releaseExit = r; }); };
        const n = h.sockets.length;
        h.ws.serverClose(1006);
        await advance(3000);
        const beforeExit = h.sockets.length - n;
        releaseExit();
        await settle();
        const afterExit = h.sockets.length - n;
        return { beforeExit, afterExit };
        """
    )
    assert out == {"beforeExit": 0, "afterExit": 1}


def test_publish_finishing_after_stop_is_withdrawn():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let finish;
        h.trtc.startLocalVideo = (c) => { h.log.push(['startLocalVideo-pending']); return new Promise((r) => { finish = r; }); };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.ws.serverSend({ type: 'media', publish: false });
        await settle();
        finish();
        await settle();
        return kinds(h.log).filter((k) => k.indexOf('LocalVideo') !== -1);
        """
    )
    assert out == ["startLocalVideo-pending", "stopLocalVideo"]


def test_quick_republish_after_stop_keeps_the_new_publication():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        const orig = h.trtc.startLocalVideo;
        let finishFirst;
        let first = true;
        h.trtc.startLocalVideo = (c) => {
          if (first) { first = false; h.log.push(['start-1-pending']); return new Promise((r) => { finishFirst = r; }); }
          return orig(c);
        };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.ws.serverSend({ type: 'media', publish: false });
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        finishFirst();
        await settle();
        await settle();
        return kinds(h.log).filter((k) => k.indexOf('LocalVideo') !== -1 || k.startsWith('start-'));
        """
    )
    # Sequential execution: first publication completes -> stop -> second publication; finish published.
    assert out == ["start-1-pending", "stopLocalVideo", "startLocalVideo"]


def test_superseded_while_publish_in_flight_still_stops_before_destroy():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let finish;
        h.trtc.startLocalVideo = (c) => { h.log.push(['start-pending']); return new Promise((r) => { finish = r; }); };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.ws.serverClose(4409);
        await settle();
        finish();
        await settle();
        await settle();
        const soon = kinds(h.log).filter((k) => ['start-pending', 'stopLocalVideo', 'exitRoom', 'destroy'].includes(k));
        await advance(15100);
        await settle();
        return { soon, later: kinds(h.log).filter((k) => ['start-pending', 'stopLocalVideo', 'exitRoom', 'destroy'].includes(k)) };
        """
    )
    # Replacement (4409) does not immediately leave; stop in-flight publication when it completes. If the replacement page has not joined, leave after 15s and destroy the instance.
    assert out["soon"] == ["start-pending", "stopLocalVideo"]
    assert out["later"] == ["start-pending", "stopLocalVideo", "exitRoom", "destroy"]


def test_publish_requested_during_join_is_applied_after_join():
    out = run_visit_js(
        """
        const h = makeController('guest');
        h.c.onAuthToken('tok');
        const ws = sockets[sockets.length - 1];
        ws.serverOpen();
        let finishEnter;
        const origCreate = h.TRTC.create;
        h.TRTC.create = () => {
          const inst = origCreate();
          const origEnter = inst.enterRoom;
          inst.enterRoom = (p) => new Promise((r) => { finishEnter = () => r(origEnter(p)); });
          return inst;
        };
        ws.serverSend(credsFor('guest'));
        await settle();
        // 入房还没完成就收到发布快照
        ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const before = h.log.filter((e) => e[0] === 'startLocalVideo').length;
        finishEnter();
        await settle();
        await settle();
        return { before, after: h.log.filter((e) => e[0] === 'startLocalVideo').length };
        """
    )
    assert out == {"before": 0, "after": 1}


def test_stuck_vendor_exit_ends_in_terminal_instead_of_reconnecting():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.trtc.exitRoom = () => new Promise(() => {});      // 永远不结束
        const n = h.sockets.length;
        h.ws.serverClose(1006);
        await advance(25000);
        return { newSockets: h.sockets.length - n, closed: h.posted.filter((m) => m.t === 'closed').length };
        """
    )
    assert out == {"newSockets": 0, "closed": 1}


def test_leave_completion_waits_for_in_flight_publish():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let finish;
        h.trtc.startLocalVideo = (c) => { h.log.push(['start-pending']); return new Promise((r) => { finish = r; }); };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const n = h.sockets.length;
        h.ws.serverClose(1006);
        await advance(3000);
        const reconnectedBeforePublishSettled = h.sockets.length - n;
        finish();
        await settle();
        await settle();
        return { reconnectedBeforePublishSettled, after: h.sockets.length - n,
                 kinds: kinds(h.log).filter((k) => ['start-pending', 'stopLocalVideo', 'exitRoom'].includes(k)) };
        """
    )
    assert out["reconnectedBeforePublishSettled"] == 0
    assert out["after"] == 1
    # exitRoom is issued immediately; stop publication completing after room exit.
    assert out["kinds"] == ["start-pending", "exitRoom", "stopLocalVideo"]


def test_livekit_initial_publication_uses_the_supplied_profile():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const log = [];
        const LK = makeFakeLK(log);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9', bitrate_kbps: 560, fps: 30 } });
        await a.publish({ id: 'pack' }, { width: 160, height: 704, frameRate: 15, bitrate: 300 });
        return log.filter((e) => e[0] === 'setParameters');
        """
    )
    assert out == [["setParameters", 300000, 15]]


def test_leave_completion_also_waits_for_the_late_stop():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let finishStart, finishStop;
        h.trtc.startLocalVideo = () => new Promise((r) => { finishStart = r; });
        h.trtc.stopLocalVideo = () => { h.log.push(['stop-pending']); return new Promise((r) => { finishStop = r; }); };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const n = h.sockets.length;
        h.ws.serverClose(1006);
        await advance(1500);
        finishStart();                      // 离房后发布才完成 → 迟到的停止开始、还没结束
        await settle();
        await advance(1500);
        const beforeStop = h.sockets.length - n;
        finishStop();
        await settle();
        await settle();
        return { beforeStop, after: h.sockets.length - n };
        """
    )
    assert out == {"beforeStop": 0, "after": 1}


def test_failed_publish_tells_the_parent_to_stop_capturing():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let fail = true;
        const orig = h.trtc.startLocalVideo;
        h.trtc.startLocalVideo = (c) => { if (fail) { fail = false; return Promise.reject(new Error('x')); } return orig(c); };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const afterFail = h.posted.filter((m) => m.t === 'publish').map((m) => m.on);
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        return { afterFail, afterRetry: h.posted.filter((m) => m.t === 'publish').map((m) => m.on) };
        """
    )
    # On failure the parent was never told publishing began (or was told it stopped); notify start only after successful retry.
    assert out["afterFail"] in ([], [False])
    assert out["afterRetry"][-1] is True


def test_video_gate_requires_a_manual_frame_track():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const doc = ctx.document;
        const orig = doc.createElement.bind(doc);
        doc.createElement = (tag) => {
          const el = orig(tag);
          if (tag === 'canvas') el.captureStream = () => ({ getVideoTracks: () => [{ kind: 'video', stop() {} }] });
          return el;
        };
        const h = { c: new ctx.NekoVisitTransport.loader.VisitTransportController({ window: ctx, document: doc, side: 'guest',
          visitId: VISIT_ID, post: () => {}, log: () => {}, loadSdk: () => Promise.resolve(makeFakeTRTC([])) }) };
        h.c.preflight = { ok: true };
        const sent = [];
        h.c.sendBackend = (m) => sent.push(m);
        h.c.onCredentials(credsFor('guest'));
        await settle();
        const caps = sent.find((m) => m.type === 'caps' && m.stage === 'sdk');
        return { transport_ok: caps.transport_ok, video_ok: caps.video_ok };
        """
    )
    assert out == {"transport_ok": True, "video_ok": False}


def test_stale_loss_sample_expires():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.c.startStats();
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.trtc.emit('NETWORK_QUALITY', { uplinkLoss: 40, uplinkRTT: 100 });   // 一次很差的样本，之后再无回调
        const peerStats = () => {
          const pieces = h.NVT.fragment(JSON.stringify({ t: 'stats', v: 1, rx_fps: 30 }), { visitId: VISIT_ID, msgId: Math.floor(Math.random() * 1e6) });
          h.trtc.emit('CUSTOM_MESSAGE', { userId: 'h_peer', cmdId: 3, data: new TextEncoder().encode(pieces[0]).buffer });
        };
        for (let k = 0; k < 12; k++) { peerStats(); await advance(5000); }
        return h.c.media.ladder;
        """
    )
    # Old packet-loss samples expire after 12s; then healthy rx_fps can reduce quality at most one step, not repeatedly to the lowest tier.
    assert out <= 1


def test_host_rejoin_waits_for_a_pending_remote_subscription():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        let finish;
        h.trtc.startRemoteVideo = () => { h.log.push(['startRemote-pending']); return new Promise((r) => { finish = r; }); };
        h.ws.serverSend({ type: 'media', subscribe: true, peer_vid: 'g_peer' });
        h.trtc.emit('REMOTE_VIDEO_AVAILABLE', { userId: 'g_peer', streamType: 'main' });
        await settle();
        const n = h.sockets.length;
        h.ws.serverClose(1006);
        await advance(3000);
        const before = h.sockets.length - n;
        finish();
        await settle();
        await settle();
        return { before, after: h.sockets.length - n };
        """
    )
    assert out == {"before": 0, "after": 1}


def test_profile_change_during_pending_publish_is_applied_after_it():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let finish;
        const orig = h.trtc.startLocalVideo;
        h.trtc.startLocalVideo = (c) => new Promise((r) => { finish = () => r(orig(c)); });
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.c.setGeometry('upper', 1);          // 发布挂起期间降了一档
        finish();
        await settle();
        await settle();
        const updates = h.log.filter((e) => e[0] === 'updateLocalVideo').map((e) => [e[1].width, e[1].height, e[1].bitrate]);
        return updates[updates.length - 1];
        """
    )
    assert out == [256, 704, 400]


def test_new_socket_clears_backpressure():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        return h.ws.sent.filter((m) => m.type === 'tx_backpressure').map((m) => m.on);
        """
    )
    assert out == [False]


def test_persistent_livekit_quality_keeps_driving_the_ladder():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        const before = a.currentLossPct() ?? null;
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        const room = LK.Room.last;
        room.emit('ConnectionQualityChanged', 'poor', room.localParticipant);
        const poor = [a.currentLossPct(), a.currentLossPct()];
        room.emit('ConnectionQualityChanged', 'good', room.localParticipant);
        const good = a.currentLossPct();
        await a.leave();
        return { before, poor, good, afterLeave: a.currentLossPct() ?? null };
        """
    )
    assert out == {"before": None, "poor": [100, 100], "good": 0, "afterLeave": None}


def test_livekit_reconnect_drops_stale_quality_and_restarts_ladder_timers():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        const room = LK.Room.last;
        room.emit('ConnectionQualityChanged', 'poor', room.localParticipant);
        const before = a.currentLossPct();
        room.emit('Reconnecting');
        room.emit('Reconnected');
        return { before, after: a.currentLossPct() ?? null };
        """
    )
    assert out == {"before": 100, "after": None}


def test_controller_drops_loss_and_ladder_timers_when_leaving_the_room_state():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.c.loss = { value: 100, at: clock.now };
        h.c.ladder.badSince = 1;
        h.c.ladder.cleanSince = 2;
        const level = h.c.ladder.level;
        h.c.onVendorState('reconnecting', {});
        return { loss: h.c.loss, bad: h.c.ladder.badSince, clean: h.c.ladder.cleanSince, sameLevel: h.c.ladder.level === level };
        """
    )
    assert out == {"loss": None, "bad": None, "clean": None, "sameLevel": True}


def test_livekit_sender_stats_report_tx_kbps():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        let sample = { framesPerSecond: 30, frameWidth: 256, frameHeight: 704, bytesSent: 0, timestamp: 1000 };
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        const room = LK.Room.last;
        room.localParticipant.publishTrack = () => Promise.resolve({ track: { sender: LK.sender, getSenderStats: () => Promise.resolve([sample]) } });
        await a.publish({ id: 'pack' });
        const first = await a.senderStats();
        sample = Object.assign({}, sample, { bytesSent: 350000, timestamp: 6000 });
        const second = await a.senderStats();
        return { first: first.tx_kbps === undefined, second: second.tx_kbps };
        """
    )
    assert out == {"first": True, "second": 560}


def test_queue_reset_clears_the_backpressure_latch():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const reports = [];
        const core = new ctx.NekoVisitTransport.TransportCore({ visitId: VISIT_ID, onRecv() {}, onBackpressure: (b) => reports.push(b.on) });
        core.backpressure = true;
        core.reset();
        return core.backpressure;
        """
    )
    assert out is False


def test_failed_trtc_remote_subscription_is_retried():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        let fails = 2;
        const orig = h.trtc.startRemoteVideo;
        h.trtc.startRemoteVideo = (c) => { if (fails > 0) { fails -= 1; h.log.push(['startRemote-fail']); return Promise.reject(new Error('transient')); } return orig(c); };
        h.ws.serverSend({ type: 'media', subscribe: true, peer_vid: 'g_peer' });
        h.trtc.emit('REMOTE_VIDEO_AVAILABLE', { userId: 'g_peer', streamType: 'main' });
        await settle();
        await advance(2100);
        await settle();
        await advance(4100);
        await settle();
        return kinds(h.log).filter((k) => k.startsWith('startRemote'));
        """
    )
    assert out == ["startRemote-fail", "startRemote-fail", "startRemoteVideo"]


def test_capability_gate_finishing_after_socket_close_does_not_join():
    out = run_visit_js(
        """
        const h = makeController('guest');
        let release;
        h.c.loadSdk = () => new Promise((r) => { release = () => r(h.TRTC); });
        h.c.onAuthToken('tok');
        const ws = sockets[sockets.length - 1];
        ws.serverOpen();
        ws.serverSend(credsFor('guest'));
        await settle();
        ws.serverClose(1006);
        release();
        await settle();
        return { enter: kinds(h.log).filter((k) => k === 'enterRoom').length, sdkCaps: ws.sent.filter((m) => m.type === 'caps' && m.stage === 'sdk').length };
        """
    )
    assert out == {"enter": 0, "sdkCaps": 0}


def test_trtc_remote_subscription_keeps_retrying_with_backoff():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        let fails = 8;
        const orig = h.trtc.startRemoteVideo;
        h.trtc.startRemoteVideo = (c) => { if (fails > 0) { fails -= 1; h.log.push(['startRemote-fail', clock.now]); return Promise.reject(new Error('transient')); } return orig(c); };
        h.ws.serverSend({ type: 'media', subscribe: true, peer_vid: 'g_peer' });
        h.trtc.emit('REMOTE_VIDEO_AVAILABLE', { userId: 'g_peer', streamType: 'main' });
        await settle();
        for (let i = 0; i < 12; i++) { await advance(31000); await settle(); }
        const fails_at = h.log.filter((e) => e[0] === 'startRemote-fail').map((e) => e[1]);
        const gaps = fails_at.slice(1).map((t, i) => t - fails_at[i]);
        return { started: kinds(h.log).includes('startRemoteVideo'), failures: fails_at.length, maxGap: Math.max(...gaps) };
        """
    )
    assert out["started"] is True
    assert out["failures"] == 8
    assert out["maxGap"] <= 31000


def test_rejected_publish_is_retried_with_backoff():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let fail = 2;
        const orig = h.trtc.startLocalVideo;
        h.trtc.startLocalVideo = (c) => { if (fail > 0) { fail -= 1; h.log.push(['startLocalVideo-fail']); return Promise.reject(new Error('transient')); } return orig(c); };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        await advance(2100); await settle();
        const afterFirst = kinds(h.log).filter((k) => k.startsWith('startLocalVideo'));
        await advance(4100); await settle();
        return { afterFirst, all: kinds(h.log).filter((k) => k.startsWith('startLocalVideo')),
                 announced: h.posted.filter((m) => m.t === 'publish').map((m) => m.on) };
        """
    )
    assert out["afterFirst"] == ["startLocalVideo-fail", "startLocalVideo-fail"]
    assert out["all"] == ["startLocalVideo-fail", "startLocalVideo-fail", "startLocalVideo"]
    assert out["announced"] == [False, True]


def test_publish_retry_stops_when_publication_is_withdrawn():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.trtc.startLocalVideo = () => { h.log.push(['startLocalVideo-fail']); return Promise.reject(new Error('transient')); };
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.ws.serverSend({ type: 'media', publish: false });
        await advance(60000); await settle();
        return kinds(h.log).filter((k) => k.startsWith('startLocalVideo'));
        """
    )
    assert out == ["startLocalVideo-fail"]


def test_trtc_leave_waits_for_a_pending_enter_room():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const log = [];
        const TRTC = makeFakeTRTC(log);
        const states = [];
        const handlers = { onPiece() {}, onPeer() {}, onState(s) { states.push(s); }, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = ctx.NekoVisitTransport.vendors.trtc.createAdapter({ sdk: TRTC, handlers });
        let finishEnter;
        const origCreate = TRTC.create;
        TRTC.create = () => { const inst = origCreate(); inst.enterRoom = () => new Promise((r) => { finishEnter = r; }); return inst; };
        const joining = a.join(credsFor('guest')).then(() => 'joined-resolved');
        await settle();
        let left = false;
        a.leave().then(() => { left = true; });
        await settle();
        const leftBefore = left;
        finishEnter();
        await settle();
        await joining;
        return { leftBefore, leftAfter: left, states, canSend: a.canSend() };
        """
    )
    assert out == {"leftBefore": False, "leftAfter": True, "states": ["joining"], "canSend": False}


def test_failed_manual_rejoin_is_retried_within_the_window():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let fails = 2;
        const orig = h.trtc.enterRoom;
        h.trtc.enterRoom = (p) => { if (fails > 0) { fails -= 1; h.log.push(['enterRoom-fail']); return Promise.reject(new Error('transient')); } return orig(p); };
        h.c.rejoin();
        await settle();
        await advance(1100); await settle();
        await advance(2100); await settle();
        return { calls: kinds(h.log).filter((k) => k.startsWith('enterRoom')),
                 errors: h.ws.sent.filter((m) => m.type === 'state' && m.state === 'error').length };
        """
    )
    assert out == {"calls": ["enterRoom", "enterRoom-fail", "enterRoom-fail", "enterRoom"], "errors": 0}


def test_manual_rejoin_reports_error_once_the_window_is_spent():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.trtc.enterRoom = () => { h.log.push(['enterRoom-fail']); return Promise.reject(new Error('down')); };
        h.c.rejoin();
        await settle();
        for (let i = 0; i < 8; i++) { await advance(5000); await settle(); }
        return { fails: kinds(h.log).filter((k) => k === 'enterRoom-fail').length,
                 errors: h.ws.sent.filter((m) => m.type === 'state' && m.state === 'error').length };
        """
    )
    assert out["errors"] == 1
    assert 3 <= out["fails"] <= 5


def test_leaving_the_room_state_drops_cached_peer_fps():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.c.peerRxFps = { value: 5, at: clock.now };
        h.c.onVendorState('reconnecting', {});
        const afterState = h.c.peerRxFps;
        h.c.peerRxFps = { value: 5, at: clock.now };
        h.c.stopAllMedia();
        return [afterState, h.c.peerRxFps];
        """
    )
    assert out == [None, None]


def test_livekit_late_unsubscribe_of_an_old_track_keeps_the_new_one():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const tracks = [];
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack(t) { tracks.push(t ? t.id : null); }, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        await a.setSubscribe(true);
        const room = LK.Room.last;
        const oldT = { kind: 'video', mediaStreamTrack: { id: 'old' } };
        const newT = { kind: 'video', mediaStreamTrack: { id: 'new' } };
        const remotePub = { kind: 'video', setSubscribed() {} };
        room.emit('TrackPublished', remotePub, { identity: 'g_peer' });
        room.emit('TrackSubscribed', oldT, remotePub, { identity: 'g_peer' });
        room.emit('TrackSubscribed', newT, remotePub, { identity: 'g_peer' });
        room.emit('TrackUnsubscribed', oldT);
        const afterOld = tracks.slice();
        room.emit('TrackUnsubscribed', newT);
        return { afterOld, all: tracks };
        """
    )
    assert out == {"afterOld": ["old", "new"], "all": ["old", "new", None]}


def test_livekit_republish_of_the_same_track_reapplies_the_profile():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const log = [];
        const LK = makeFakeLK(log);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp8', bitrate_kbps: 560, fps: 30 } });
        const track = { id: 'pack' };
        const profile = { width: 256, height: 704, frameRate: 15, bitrate: 400 };
        await a.publish(track, profile);
        // SDK 整体重连按发布选项重发：发送端参数回到默认
        LK.sender.params = { encodings: [{ maxBitrate: 560000, maxFramerate: 30 }] };
        const mark = log.length;
        const ok = await a.publish(track, profile);
        return { ok, again: log.slice(mark).filter((e) => e[0] === 'setParameters'), publishes: log.filter((e) => e[0] === 'publishTrack').length };
        """
    )
    assert out == {"ok": True, "again": [["setParameters", 400000, 15]], "publishes": 1}


def test_rejected_profile_update_is_retried_with_the_current_profile():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        let fail = 1;
        const orig = h.trtc.updateLocalVideo;
        h.trtc.updateLocalVideo = (c) => { if (fail > 0) { fail -= 1; h.log.push(['updateLocalVideo-fail']); return Promise.reject(new Error('busy')); } return orig(c); };
        h.c.setGeometry('upper', 1);
        await settle();
        const before = kinds(h.log).filter((k) => k.startsWith('updateLocalVideo'));
        await advance(2100); await settle();
        const ups = h.log.filter((e) => e[0] === 'updateLocalVideo');
        return { before, after: kinds(h.log).filter((k) => k.startsWith('updateLocalVideo')), bitrate: ups[ups.length - 1][1].bitrate };
        """
    )
    assert out == {"before": ["updateLocalVideo-fail"], "after": ["updateLocalVideo-fail", "updateLocalVideo"], "bitrate": 400}


def test_packer_flips_mirrored_sources():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const pk = new ctx.NekoVisitTransport.pack.Packer({ document: ctx.document, crop: 'upper' });
        pk.active = true;
        const src = ctx.document.createElement('canvas');
        src.width = 1000; src.height = 2000;
        const scaleCalls = () => pk.pctx.calls.filter((c) => c[0] === 'scale').length + pk.sctx.calls.filter((c) => c[0] === 'scale').length;
        pk.onFrame(src, { x: 10, y: 10, w: 300, h: 420 });
        const plain = scaleCalls();
        pk.onFrame(src, { x: 10, y: 10, w: 300, h: 420, flipX: true });
        return { plain, mirrored: scaleCalls() - plain,
                 restored: pk.pctx.calls.filter((c) => c[0] === 'restore').length === pk.pctx.calls.filter((c) => c[0] === 'save').length };
        """
    )
    assert out == {"plain": 0, "mirrored": 2, "restored": True}


def test_livekit_publish_reports_a_profile_that_did_not_apply():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp8' } });
        LK.sender.setParameters = () => Promise.reject(new Error('busy'));
        const profile = { width: 256, height: 704, frameRate: 15, bitrate: 400 };
        const track = { id: 'pack' };
        return [await a.publish(track, profile), await a.publish(track, profile)];
        """
    )
    assert out == ["profile_failed", "profile_failed"]


def test_controller_retries_a_profile_the_publication_could_not_apply():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        const orig = h.c.adapter.publish;
        h.c.adapter.publish = (t, p) => orig(t, p).then((ok) => ok === true ? 'profile_failed' : ok);
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        return { updates: kinds(h.log).filter((k) => k === 'updateLocalVideo').length,
                 announced: h.posted.filter((m) => m.t === 'publish').map((m) => m.on) };
        """
    )
    assert out == {"updates": 1, "announced": [True]}


def test_queue_reset_sends_the_backpressure_falling_edge_once():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const reports = [];
        const core = new ctx.NekoVisitTransport.TransportCore({ visitId: VISIT_ID, onRecv() {}, onBackpressure: (b) => reports.push(b.on) });
        core.reset();
        core.backpressure = true;
        core.reset();
        core.reset();
        return reports;
        """
    )
    assert out == [False]


def test_trtc_leave_waits_for_a_pending_profile_update():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        let finish;
        h.trtc.updateLocalVideo = () => new Promise((r) => { finish = r; });
        h.c.adapter.updateProfile({ width: 256, height: 704, frameRate: 30, bitrate: 400 });
        await settle();
        let left = false;
        h.c.adapter.leave().then(() => { left = true; });
        await settle();
        const before = left;
        finish();
        await settle();
        return { before, after: left };
        """
    )
    assert out == {"before": False, "after": True}


def test_livekit_failed_subscription_is_retried():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        const room = LK.Room.last;
        const calls = [];
        const pub = { kind: 'video', trackSid: 'TR_1', setSubscribed: (on) => calls.push(on) };
        const peer = { identity: 'g_peer' };
        room.emit('TrackPublished', pub, peer);
        await a.setSubscribe(true);
        room.emit('TrackSubscriptionFailed', 'TR_1', peer);
        await advance(1900);
        const early = calls.slice();
        await advance(200);
        room.emit('TrackSubscriptionFailed', 'TR_1', peer);
        await a.setSubscribe(false);
        await advance(10000);
        return { early, all: calls };
        """
    )
    assert out == {"early": [True], "all": [True, False, True, False]}


def test_livekit_publish_treats_an_unready_sender_as_profile_failed():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp8' } });
        LK.sender.params = { encodings: [] };
        const profile = { width: 256, height: 704, frameRate: 15, bitrate: 400 };
        const track = { id: 'pack' };
        const first = await a.publish(track, profile);
        const again = await a.publish(track, profile);
        LK.sender.params = { encodings: [{ maxBitrate: 560000, maxFramerate: 30 }] };
        return [first, again, await a.publish(track, profile)];
        """
    )
    assert out == ["profile_failed", "profile_failed", True]


def test_profile_update_resolving_false_is_retried():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const results = [false, false, true];
        let calls = 0;
        h.c.adapter.updateProfile = () => Promise.resolve(results[Math.min(calls++, results.length - 1)]);
        h.c.pushProfile();
        await settle();
        await advance(2100); await settle();
        await advance(4100); await settle();
        const afterSuccess = calls;
        await advance(60000); await settle();
        return { calls: afterSuccess, later: calls, retries: h.c.profileRetries };
        """
    )
    assert out == {"calls": 3, "later": 3, "retries": 0}


def test_unpacker_rebuilds_gl_after_context_restore():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const doc = ctx.document;
        const canvas = doc.createElement('canvas');
        const listeners = {};
        canvas.addEventListener = (type, fn) => { listeners[type] = fn; };
        const u = new ctx.NekoVisitTransport.unpack.Unpacker({ video: doc.createElement('video'), canvas });
        u.track = { id: 'remote' };
        u._ensureGl();
        let prevented = false;
        listeners.webglcontextlost({ preventDefault: () => { prevented = true; } });
        const lost = { gl: u.gl, state: u.glState, draw: u.draw() };
        listeners.webglcontextrestored({});
        const restored = { gl: !!u.gl, state: !!u.glState };
        u.track = null;
        listeners.webglcontextlost({ preventDefault() {} });
        listeners.webglcontextrestored({});
        return { prevented, lost, restored, idle: u.gl };
        """
    )
    assert out == {"prevented": True, "lost": {"gl": None, "state": None, "draw": False},
                   "restored": {"gl": True, "state": True}, "idle": None}


def test_hidden_avatar_does_not_count_as_vp9_overload():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.c.creds.publish.codec = 'vp9';
        h.c.media.publish = true;
        const run = (hidden) => {
          h.c.hidden = hidden;
          h.c.softencSince = null;
          const flags = [];
          for (const t of [0, 5000, 10000, 15000]) {
            const out = { enc_fps: 0 };
            h.c.checkSoftenc(out, t);
            flags.push(!!out.softenc_overloaded);
          }
          return flags;
        };
        return { hidden: run(true), shown: run(false) };
        """
    )
    assert out == {"hidden": [False, False, False, False], "shown": [False, False, True, True]}


def test_trtc_stale_enter_room_is_exited_again():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const log = [];
        const TRTC = makeFakeTRTC(log);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = ctx.NekoVisitTransport.vendors.trtc.createAdapter({ sdk: TRTC, handlers });
        let finishEnter;
        const origCreate = TRTC.create;
        TRTC.create = () => { const inst = origCreate(); inst.enterRoom = () => new Promise((r) => { finishEnter = r; }); return inst; };
        a.join(credsFor('guest'));
        await settle();
        let left = false;
        a.leave().then(() => { left = true; });
        await settle();
        const exitsBefore = kinds(log).filter((k) => k === 'exitRoom').length;
        finishEnter();
        await settle();
        return { exitsBefore, exitsAfter: kinds(log).filter((k) => k === 'exitRoom').length, left };
        """
    )
    assert out == {"exitsBefore": 1, "exitsAfter": 2, "left": True}


def test_livekit_stale_connect_is_disconnected_again():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        let finish;
        let disconnects = 0;
        const origConnect = LK.Room.prototype.connect;
        LK.Room.prototype.connect = function () { return new Promise((r) => { finish = r; }); };
        LK.Room.prototype.disconnect = function () { disconnects += 1; return Promise.resolve(); };
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        await settle();
        let left = false;
        a.leave().then(() => { left = true; });
        await settle();
        const before = { disconnects, left };
        finish();
        await settle();
        return { before, after: { disconnects, left } };
        """
    )
    assert out == {"before": {"disconnects": 1, "left": False}, "after": {"disconnects": 2, "left": True}}


def test_trtc_capabilities_require_the_overall_verdict():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const caps = ctx.NekoVisitTransport.vendors.trtc.capabilities;
        const detail = { isWebRTCSupported: true, isH264EncodeSupported: true, isVp8EncodeSupported: true, isH264DecodeSupported: true, isVp8DecodeSupported: true };
        const ok = await caps({ isSupported: () => Promise.resolve({ result: true, detail }) });
        const no = await caps({ isSupported: () => Promise.resolve({ result: false, detail }) });
        return [ok.transportOk, no.transportOk];
        """
    )
    assert out == [True, False]


def test_livekit_ignores_late_subscription_of_a_superseded_publication():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const tracks = [];
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack(t) { tracks.push(t ? t.id : null); }, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        await a.setSubscribe(true);
        const room = LK.Room.last;
        const peer = { identity: 'g_peer' };
        const oldPub = { kind: 'video', trackSid: 'TR_old', setSubscribed() {} };
        const newPub = { kind: 'video', trackSid: 'TR_new', setSubscribed() {} };
        room.emit('TrackPublished', oldPub, peer);
        room.emit('TrackPublished', newPub, peer);
        room.emit('TrackSubscribed', { kind: 'video', mediaStreamTrack: { id: 'new' } }, newPub, peer);
        room.emit('TrackSubscribed', { kind: 'video', mediaStreamTrack: { id: 'old' } }, oldPub, peer);
        return tracks;
        """
    )
    assert out == ["new"]


def test_livekit_derives_encoder_fps_from_frames_sent():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        let sample = { frameWidth: 256, frameHeight: 704, bytesSent: 0, framesSent: 0, timestamp: 1000 };
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        LK.Room.last.localParticipant.publishTrack = () => Promise.resolve({ track: { sender: LK.sender, getSenderStats: () => Promise.resolve([sample]) } });
        await a.publish({ id: 'pack' });
        const first = await a.senderStats();
        sample = Object.assign({}, sample, { framesSent: 100, bytesSent: 350000, timestamp: 6000 });
        const second = await a.senderStats();
        sample = Object.assign({}, sample, { framesPerSecond: 29, framesSent: 150, timestamp: 11000 });
        const reported = await a.senderStats();
        return { first: first.enc_fps ?? null, second: second.enc_fps, reported: reported.enc_fps };
        """
    )
    assert out == {"first": None, "second": 20, "reported": 29}


def test_softenc_window_restarts_after_leaving_the_room_state():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.c.creds.publish.codec = 'vp9';
        h.c.media.publish = true;
        const first = { enc_fps: 5 };
        h.c.checkSoftenc(first, 0);
        h.c.onVendorState('reconnecting', {});
        const afterState = h.c.softencSince;
        h.c.onVendorState('joined', {});
        h.c.media.publish = true;
        const late = { enc_fps: 5 };
        h.c.checkSoftenc(late, 60000);
        h.c.softencSince = 1;
        h.c.stopAllMedia();
        return { afterState, flagged: !!late.softenc_overloaded, afterStop: h.c.softencSince };
        """
    )
    assert out == {"afterState": None, "flagged": False, "afterStop": None}


def test_first_join_failure_is_retried_within_the_window():
    out = run_visit_js(
        """
        const h = makeController('guest');
        let fails = 1;
        const origCreate = h.TRTC.create;
        h.TRTC.create = () => {
          const inst = origCreate();
          const orig = inst.enterRoom;
          inst.enterRoom = (p) => { if (fails > 0) { fails -= 1; h.log.push(['enterRoom-fail']); return Promise.reject(new Error('transient')); } return orig(p); };
          return inst;
        };
        h.c.onAuthToken('tok');
        const ws = sockets[sockets.length - 1];
        ws.serverOpen();
        ws.serverSend(credsFor('guest'));
        await settle();
        await advance(1100); await settle();
        return { calls: kinds(h.log).filter((k) => k.startsWith('enterRoom')),
                 errors: ws.sent.filter((m) => m.type === 'state' && m.state === 'error').length };
        """
    )
    assert out == {"calls": ["enterRoom-fail", "enterRoom"], "errors": 0}


def test_flapping_socket_cannot_extend_the_reconnect_window():
    out = run_visit_js(
        """
        const runs = {};
        for (const [name, openMs] of [['flapping', 300], ['stable', 6000]]) {
          const h = makeController('guest');
          h.c.onAuthToken('tok');
          let closed = null;
          const origTerminal = h.c.onTerminal.bind(h.c);
          h.c.onTerminal = (code) => { closed = clock.now; origTerminal(code); };
          const start = clock.now;
          let cycles = 0;
          while (closed === null && cycles < 40) {
            const ws = sockets[sockets.length - 1];
            ws.serverOpen();
            await advance(openMs);
            ws.serverClose(1006);
            await settle();
            await advance(1100);
            cycles += 1;
          }
          runs[name] = { terminal: closed !== null, elapsed: closed === null ? null : closed - start, cycles };
        }
        return runs;
        """
    )
    assert out["flapping"]["terminal"] is True and out["flapping"]["elapsed"] <= 25000
    assert out["stable"]["terminal"] is False


def test_livekit_ignores_subscription_after_the_publication_was_withdrawn():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const tracks = [];
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack(t) { tracks.push(t ? t.id : null); }, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        await a.setSubscribe(true);
        const room = LK.Room.last;
        const peer = { identity: 'g_peer' };
        const pub = { kind: 'video', trackSid: 'TR_1', setSubscribed() {} };
        room.emit('TrackPublished', pub, peer);
        room.emit('TrackUnpublished', pub, peer);
        room.emit('TrackSubscribed', { kind: 'video', mediaStreamTrack: { id: 'late' } }, pub, peer);
        return tracks;
        """
    )
    assert out == [None]


def test_leaving_the_room_resets_stat_baselines_and_drops_the_pending_sample():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        h.c.unpacker.statsMark = { at: 0, kind: 'presented', count: 10 };
        h.c.statsMark = { at: 0, frames: 10 };
        let resets = 0;
        h.c.adapter.resetStats = () => { resets += 1; };
        h.c.media.subscribe = true;
        let release;
        h.c.adapter.receiverStats = () => new Promise((r) => { release = r; });
        h.c.collectStats();
        // collectStats 同步取样会改写基准：取样之后再放一个旧基准，确认离房时被清掉
        h.c.unpacker.statsMark = { at: 0, kind: 'presented', count: 10 };
        h.c.onVendorState('reconnecting', {});
        release({ rx_kbps: 10 });
        await settle();
        return { unpack: h.c.unpacker.statsMark, own: h.c.statsMark, resets,
                 sent: h.ws.sent.filter((m) => m.type === 'stats').length };
        """
    )
    assert out == {"unpack": None, "own": None, "resets": 1, "sent": 0}


def test_livekit_reset_stats_drops_rx_and_tx_baselines():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        await a.setSubscribe(true);
        let bytes = 0;
        const receiver = { getStats: () => Promise.resolve(new Map([['in', { type: 'inbound-rtp', kind: 'video', bytesReceived: bytes, packetsReceived: 0, packetsLost: 0 }]])) };
        const pub = { kind: 'video', setSubscribed() {} };
        LK.Room.last.emit('TrackPublished', pub, { identity: 'g_peer' });
        LK.Room.last.emit('TrackSubscribed', { kind: 'video', mediaStreamTrack: {}, receiver }, pub, { identity: 'g_peer' });
        await a.receiverStats(0);
        bytes = 350000;
        a.resetStats();
        const afterReset = await a.receiverStats(5000);
        bytes = 700000;
        const next = await a.receiverStats(10000);
        return { afterReset: afterReset.rx_kbps ?? null, next: next.rx_kbps };
        """
    )
    assert out == {"afterReset": None, "next": 560}


def test_trtc_unpublish_waits_for_a_pending_profile_update():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        let finish;
        h.trtc.updateLocalVideo = () => { h.log.push(['updateLocalVideo-pending']); return new Promise((r) => { finish = r; }); };
        h.c.adapter.updateProfile({ width: 256, height: 704, frameRate: 30, bitrate: 400 });
        await settle();
        h.c.adapter.unpublish();
        await settle();
        const before = kinds(h.log).filter((k) => k === 'stopLocalVideo').length;
        h.log.push(['update-finished']);
        finish();
        await settle();
        const tail = kinds(h.log).filter((k) => k === 'update-finished' || k === 'stopLocalVideo');
        return { before, tail };
        """
    )
    assert out == {"before": 0, "tail": ["update-finished", "stopLocalVideo"]}


def test_unpublish_without_pending_operations_still_stops_synchronously():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        h.c.adapter.unpublish();
        return kinds(h.log).filter((k) => k === 'stopLocalVideo').length;
        """
    )
    assert out == 1


def test_livekit_late_stats_do_not_restore_reset_baselines():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        await a.setSubscribe(true);
        let bytes = 0;
        let hold = null;
        const report = () => new Map([['in', { type: 'inbound-rtp', kind: 'video', bytesReceived: bytes, packetsReceived: 0, packetsLost: 0 }]]);
        const receiver = { getStats: () => hold ? hold : Promise.resolve(report()) };
        const pub = { kind: 'video', setSubscribed() {} };
        LK.Room.last.emit('TrackPublished', pub, { identity: 'g_peer' });
        LK.Room.last.emit('TrackSubscribed', { kind: 'video', mediaStreamTrack: {}, receiver }, pub, { identity: 'g_peer' });
        let release;
        hold = new Promise((r) => { release = r; });
        const pending = a.receiverStats(0);        // 断线前发出、还没回来的取样
        a.resetStats();
        hold = null;
        bytes = 350000;
        release(report());
        const stale = await pending;
        bytes = 350000;
        const first = await a.receiverStats(5000);  // 重置后的第一拍：没有基准
        bytes = 700000;
        const second = await a.receiverStats(10000);
        return { stale, first: first.rx_kbps ?? null, second: second.rx_kbps };
        """
    )
    assert out == {"stale": None, "first": None, "second": 560}


def test_superseded_trtc_subscription_failure_does_not_reset_the_new_attempt():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        const settles = [];
        h.trtc.startRemoteVideo = () => { h.log.push(['startRemote']); return new Promise((res, rej) => settles.push({ res, rej })); };
        h.ws.serverSend({ type: 'media', subscribe: true, peer_vid: 'g_peer' });
        h.trtc.emit('REMOTE_VIDEO_AVAILABLE', { userId: 'g_peer', streamType: 'main' });
        await settle();
        h.trtc.emit('REMOTE_VIDEO_UNAVAILABLE', { userId: 'g_peer', streamType: 'main' });
        h.trtc.emit('REMOTE_VIDEO_AVAILABLE', { userId: 'g_peer', streamType: 'main' });
        settles[0].rej(new Error('old attempt'));   // 旧尝试迟到失败
        await settle();
        settles[1].res();                           // 新尝试成功
        await settle();
        await advance(10000);
        await settle();
        const starts = kinds(h.log).filter((k) => k === 'startRemote').length;
        h.c.adapter.setSubscribe(false);
        await settle();
        return { starts, stops: kinds(h.log).filter((k) => k === 'stopRemoteVideo').length };
        """
    )
    assert out == {"starts": 2, "stops": 1}


def test_stalled_connect_attempt_is_bounded():
    out = run_visit_js(
        """
        const runs = {};
        // 首连卡在 CONNECTING：10 s 后按断开处理并开始重连
        {
          const h = makeController('guest');
          h.c.onAuthToken('tok');
          const n = sockets.length;
          // 黑洞网络：close() 之后浏览器的 close 事件迟迟不来
          sockets[n - 1].close = function () { this.readyState = 3; };
          await advance(11500);
          runs.first = { closed: sockets[n - 1].readyState === 3, retried: sockets.length > n };
        }
        // 重连一直卡住：重连窗口用完就终止，不会无限挂着
        {
          const h = makeController('guest');
          let terminal = null;
          const orig = h.c.onTerminal.bind(h.c);
          h.c.onTerminal = (code) => { terminal = clock.now; orig(code); };
          h.c.onAuthToken('tok');
          const ws = sockets[sockets.length - 1];
          ws.serverOpen();
          await advance(6000);
          const start = clock.now;
          ws.serverClose(1006);
          for (let i = 0; i < 30 && terminal === null; i++) await advance(1000);
          runs.reconnect = { terminal: terminal !== null, within: terminal !== null && terminal - start <= 21000 };
        }
        return runs;
        """
    )
    assert out == {"first": {"closed": True, "retried": True}, "reconnect": {"terminal": True, "within": True}}


def test_slow_stats_sample_overtaken_by_a_newer_one_is_dropped():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        h.c.media.subscribe = true;
        const releases = [];
        h.c.adapter.receiverStats = () => new Promise((r) => releases.push(r));
        h.c.collectStats();          // 旧拍：慢
        h.c.collectStats();          // 新拍：先回来
        releases[1]({ rx_kbps: 200 });
        await settle();
        releases[0]({ rx_kbps: 999 });
        await settle();
        return h.ws.sent.filter((m) => m.type === 'stats').map((m) => m.rx_kbps);
        """
    )
    assert out == [200]


def test_livekit_older_receiver_sample_does_not_rewind_the_baseline():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        await a.setSubscribe(true);
        const holds = [];
        const report = (bytes) => new Map([['in', { type: 'inbound-rtp', kind: 'video', bytesReceived: bytes, packetsReceived: 0, packetsLost: 0 }]]);
        const receiver = { getStats: () => new Promise((r) => holds.push(r)) };
        const pub = { kind: 'video', setSubscribed() {} };
        LK.Room.last.emit('TrackPublished', pub, { identity: 'g_peer' });
        LK.Room.last.emit('TrackSubscribed', { kind: 'video', mediaStreamTrack: {}, receiver }, pub, { identity: 'g_peer' });
        const older = a.receiverStats(5000);
        const newer = a.receiverStats(10000);
        holds[1](report(700000));
        await newer;
        holds[0](report(350000));
        const stale = await older;
        const p = a.receiverStats(15000);
        holds[2](report(1050000));
        const next = await p;
        return { stale, next: next.rx_kbps };
        """
    )
    assert out == {"stale": None, "next": 560}


def test_hidden_avatar_pauses_the_congestion_ladder():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const run = (hidden) => {
          h.c.ladder.reset('upper', 0);
          h.c.hidden = hidden;
          let level = 0;
          for (const t of [0, 5000, 10000, 15000, 20000]) {
            h.c.peerRxFps = { value: 0, at: t };
            h.c.stepLadder(t);
            level = h.c.ladder.level;
          }
          h.c.setGeometry('upper', 0);
          return level;
        };
        const hidden = run(true);
        const shown = run(false);
        h.c.peerRxFps = { value: 0, at: 0 };
        h.c.hidden = true;
        h.c.onParentHidden(false);
        return { hidden, shown, clearedOnShow: h.c.peerRxFps };
        """
    )
    assert out["hidden"] == 0 and out["shown"] >= 1 and out["clearedOnShow"] is None


def test_consistently_slow_stats_are_still_emitted_in_order():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        h.c.media.subscribe = true;
        const releases = [];
        h.c.adapter.receiverStats = () => new Promise((r) => releases.push(r));
        h.c.collectStats();          // 第 1 拍
        h.c.collectStats();          // 第 2 拍在第 1 拍回来之前发起
        h.c.collectStats();          // 第 3 拍同样
        releases[0]({ rx_kbps: 100 });
        await settle();
        releases[1]({ rx_kbps: 200 });
        await settle();
        releases[2]({ rx_kbps: 300 });
        await settle();
        return h.ws.sent.filter((m) => m.type === 'stats').map((m) => m.rx_kbps);
        """
    )
    assert out == [100, 200, 300]


def test_trtc_drops_an_unverified_remote_video_candidate():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        h.c.adapter.setPeerVid(null);
        h.trtc.emit('REMOTE_VIDEO_AVAILABLE', { userId: 'g_intruder', streamType: 'main' });
        await h.c.adapter.setSubscribe(true);
        await settle();
        h.c.adapter.setPeerVid('g_peer');
        await settle();
        const afterVerify = kinds(h.log).filter((k) => k === 'startRemoteVideo' || k === 'stopRemoteVideo');
        await h.c.adapter.setSubscribe(true);
        await settle();
        return { afterVerify, starts: h.log.filter((e) => e[0] === 'startRemoteVideo').map((e) => e[1]) };
        """
    )
    assert out == {"afterVerify": ["startRemoteVideo", "stopRemoteVideo"], "starts": ["g_intruder"]}


def test_livekit_drops_an_unverified_publication_candidate():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const tracks = [];
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack(t) { tracks.push(t ? t.id : null); }, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        const calls = [];
        const pub = { kind: 'video', setSubscribed: (on) => calls.push(on) };
        LK.Room.last.emit('TrackPublished', pub, { identity: 'g_intruder' });
        a.setPeerVid('g_peer');
        await a.setSubscribe(true);
        return { calls, tracks };
        """
    )
    assert out == {"calls": [False], "tracks": [None]}


def test_flooding_fragments_are_dropped_before_reassembly():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const NVT = ctx.NekoVisitTransport;
        const core = new NVT.TransportCore({ visitId: VISIT_ID, now: () => clock.now });
        let fed = 0;
        const orig = core.reassembler.feed.bind(core.reassembler);
        core.reassembler.feed = (...a) => { fed += 1; return orig(...a); };
        const piece = new Uint8Array(100);
        for (let i = 0; i < 100; i++) core.onPiece('g_peer', 2, piece);
        const firstSecond = { fed, dropped: core.droppedFlood };
        for (let i = 0; i < 5; i++) core.onPiece('g_other', 2, piece);   // 另一个发送者有自己的预算
        clock.now += 1000;
        core.onPiece('g_peer', 2, piece);                                // 窗口滑过后恢复
        return { firstSecond, total: fed };
        """
    )
    assert out["firstSecond"] == {"fed": 40, "dropped": 60}
    assert out["total"] == 46


def test_reconnect_opening_after_the_window_is_rejected():
    out = run_visit_js(
        """
        const h = makeController('guest');
        let terminal = null;
        const orig = h.c.onTerminal.bind(h.c);
        h.c.onTerminal = (code) => { terminal = code; orig(code); };
        h.c.onAuthToken('tok');
        const first = sockets[sockets.length - 1];
        first.serverOpen();
        await advance(6000);
        first.serverClose(1006);
        await advance(1100);
        const retry = sockets[sockets.length - 1];
        h.c.link.clearConnectTimer();     // 模拟主线程卡住：看门狗没来得及跑
        clock.now += 25000;
        retry.serverOpen();
        await settle();
        return { terminal, retryClosed: retry.readyState === 3, auth: retry.sent.filter((m) => m.type === 'auth').length };
        """
    )
    assert out == {"terminal": 1006, "retryClosed": True, "auth": 0}


def test_terminal_close_waits_for_the_vendor_leave():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        let releaseExit;
        h.trtc.exitRoom = () => { h.log.push(['exitRoom-slow']); return new Promise((r) => { releaseExit = r; }); };
        let terminalAt = null;
        const orig = h.c.onTerminal.bind(h.c);
        h.c.onTerminal = (code) => { terminalAt = clock.now; orig(code); };
        const start = clock.now;
        h.ws.serverClose(4403);
        await settle();
        const before = terminalAt;
        await advance(1000);
        releaseExit();
        await settle();
        const after = terminalAt === null ? null : terminalAt - start;
        // 离房一直不完成时最多等 5 s
        const h2 = await joinedController('guest');
        h2.trtc.exitRoom = () => new Promise(() => {});
        let t2 = null;
        const orig2 = h2.c.onTerminal.bind(h2.c);
        h2.c.onTerminal = (code) => { t2 = clock.now; orig2(code); };
        const s2 = clock.now;
        h2.ws.serverClose(4404);
        await advance(6000);
        return { before, after, capped: t2 === null ? null : t2 - s2 };
        """
    )
    assert out["before"] is None
    assert out["after"] is not None and out["after"] >= 1000
    assert out["capped"] is not None and 4900 <= out["capped"] <= 5100


def test_slow_stats_judge_freshness_at_completion_time():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        const seen = [];
        h.c.stepLadder = (t) => seen.push(t);
        h.c.checkSoftenc = () => {};
        let release;
        h.c.adapter.senderStats = () => new Promise((r) => { release = r; });
        const startedAt = clock.now;
        h.c.collectStats();
        clock.now += 9000;              // getStats 拖了 9 s
        release(null);
        await settle();
        return seen[0] - startedAt;
        """
    )
    assert out == 9000


def test_livekit_destroy_disconnects_a_lingering_room_later():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        let disconnects = 0;
        LK.Room.prototype.disconnect = function () { disconnects += 1; return Promise.resolve(); };
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        a.destroy();                    // 4409：不离房直接拆
        await settle();
        const immediately = disconnects;
        await advance(15100);
        return { immediately, later: disconnects };
        """
    )
    assert out == {"immediately": 0, "later": 1}


def test_track_attached_during_context_loss_is_attached_after_restore():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const doc = ctx.document;
        const canvas = doc.createElement('canvas');
        const listeners = {};
        canvas.addEventListener = (type, fn) => { listeners[type] = fn; };
        let lost = false;
        const realGet = canvas.getContext.bind(canvas);
        canvas.getContext = (kind) => (kind === 'webgl' && lost ? null : realGet(kind));
        const video = doc.createElement('video');
        video.play = () => Promise.resolve();
        const u = new ctx.NekoVisitTransport.unpack.Unpacker({ video, canvas });
        lost = true;
        listeners.webglcontextlost({ preventDefault() {} });
        const track = { id: 'remote' };
        u.attach(track);
        const during = { track: u.track, pending: u.pendingTrack && u.pendingTrack.id };
        lost = false;
        listeners.webglcontextrestored({});
        return { during, after: u.track && u.track.id, pending: u.pendingTrack };
        """
    )
    assert out == {"during": {"track": None, "pending": "remote"}, "after": "remote", "pending": None}


def test_track_is_kept_when_the_lost_context_object_is_still_returned():
    out = run_visit_js(
        """
        const ctx = makeContext();
        load(ctx, FILES);
        const doc = ctx.document;
        const canvas = doc.createElement('canvas');
        const listeners = {};
        canvas.addEventListener = (type, fn) => { listeners[type] = fn; };
        let lost = false;
        const realGet = canvas.getContext.bind(canvas);
        // 第一种：初始化不抛错，但对象自己报「已丢失」——只能靠 isContextLost 判
        const quietLost = new Proxy(realGet('webgl'), { get: (t, p) => (p === 'isContextLost' ? () => true : t[p]) });
        // 第二种：不报丢失，但初始化抛错
        const throwing = { isContextLost: () => false, createProgram() { throw new Error('context lost'); } };
        let lostGl = quietLost;
        canvas.getContext = (kind) => (kind === 'webgl' && lost ? lostGl : realGet(kind));
        const video = doc.createElement('video');
        video.play = () => Promise.resolve();
        const u = new ctx.NekoVisitTransport.unpack.Unpacker({ video, canvas });
        lost = true;
        listeners.webglcontextlost({ preventDefault() {} });
        u.attach({ id: 'remote' });
        const during = u.pendingTrack && u.pendingTrack.id;
        // 只抛异常、不报 isContextLost 的实现同样走待接
        u.detach();
        lostGl = throwing;
        u.attach({ id: 'remote2' });
        const thrown = u.pendingTrack && u.pendingTrack.id;
        lost = false;
        listeners.webglcontextrestored({});
        return { during, thrown, after: u.track && u.track.id };
        """
    )
    assert out == {"during": "remote", "thrown": "remote2", "after": "remote2"}


def test_livekit_receive_bitrate_uses_report_timestamps():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        await a.setSubscribe(true);
        let report = null;
        const receiver = { getStats: () => Promise.resolve(new Map([['in', report]])) };
        const pub = { kind: 'video', setSubscribed() {} };
        LK.Room.last.emit('TrackPublished', pub, { identity: 'g_peer' });
        LK.Room.last.emit('TrackSubscribed', { kind: 'video', mediaStreamTrack: {}, receiver }, pub, { identity: 'g_peer' });
        report = { type: 'inbound-rtp', kind: 'video', bytesReceived: 0, packetsReceived: 0, packetsLost: 0, timestamp: 1000 };
        await a.receiverStats(0);
        // 第二次发起时间只隔 5 s，但报告本身隔了 10 s：按报告时间算是 280 kbps，不是 560
        report = { type: 'inbound-rtp', kind: 'video', bytesReceived: 350000, packetsReceived: 0, packetsLost: 0, timestamp: 11000 };
        const second = await a.receiverStats(5000);
        return second.rx_kbps;
        """
    )
    assert out == 280


def test_trtc_receiver_stats_use_downlink_loss():
    out = run_visit_js(
        """
        const host = await joinedController('host');
        await host.c.adapter.setSubscribe(true);
        host.trtc.emit('NETWORK_QUALITY', { uplinkLoss: 1, downlinkLoss: 30, uplinkRTT: 50 });
        host.trtc.emit('STATISTICS', { rtt: 50, upLoss: 2, downLoss: 25, localStatistics: {}, remoteStatistics: [] });
        const hostLoss = host.c.adapter.stats().loss_pct;
        const guest = await joinedController('guest');
        guest.trtc.emit('NETWORK_QUALITY', { uplinkLoss: 7, downlinkLoss: 40, uplinkRTT: 50 });
        guest.trtc.emit('STATISTICS', { rtt: 50, upLoss: 6, downLoss: 35, localStatistics: {}, remoteStatistics: [] });
        // 只有 STATISTICS、还没有 NETWORK_QUALITY 时同样按侧位取
        const host2 = await joinedController('host');
        await host2.c.adapter.setSubscribe(true);
        host2.trtc.emit('STATISTICS', { rtt: 50, upLoss: 2, downLoss: 25, localStatistics: {}, remoteStatistics: [] });
        return { hostLoss, guestLoss: guest.c.adapter.stats().loss_pct, statsOnly: host2.c.adapter.stats().loss_pct };
        """
    )
    assert out == {"hostLoss": 30, "guestLoss": 7, "statsOnly": 25}


def test_trtc_destroy_exits_a_superseded_room_later():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        const before = kinds(h.log).filter((k) => k === 'exitRoom' || k === 'destroy');
        h.c.adapter.destroy();                 // 4409：不离房直接拆
        await settle();
        const immediately = kinds(h.log).filter((k) => k === 'exitRoom' || k === 'destroy');
        await advance(15100);
        await settle();
        const later = kinds(h.log).filter((k) => k === 'exitRoom' || k === 'destroy');
        // 正常离房后再拆：立刻 destroy，不再补退
        const h2 = await joinedController('guest');
        await h2.c.adapter.leave();
        h2.c.adapter.destroy();
        await settle();
        const normal = kinds(h2.log).filter((k) => k === 'exitRoom' || k === 'destroy');
        await advance(15100);
        return { before, immediately, later, normal, normalLater: kinds(h2.log).filter((k) => k === 'exitRoom' || k === 'destroy') };
        """
    )
    assert out["before"] == [] and out["immediately"] == []
    assert out["later"] == ["exitRoom", "destroy"]
    assert out["normal"] == ["exitRoom", "destroy"] and out["normalLater"] == ["exitRoom", "destroy"]


def test_backend_socket_drop_invalidates_pending_stats():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        h.c.media.subscribe = true;
        let release;
        h.c.adapter.receiverStats = () => new Promise((r) => { release = r; });
        let resets = 0;
        h.c.adapter.resetStats = () => { resets += 1; };
        h.c.collectStats();
        const old = h.ws;
        h.ws.serverClose(1006);          // 传输 WS 直接断（vendor 没先报 reconnecting）
        release({ rx_kbps: 10 });
        await settle();
        return { sent: old.sent.filter((m) => m.type === 'stats').length, resets, mark: h.c.statsMark };
        """
    )
    assert out == {"sent": 0, "resets": 1, "mark": None}


def test_receive_fps_is_sampled_when_async_stats_complete():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        h.c.media.subscribe = true;
        let release;
        h.c.adapter.receiverStats = () => new Promise((r) => { release = r; });
        const sampledAt = [];
        h.c.unpacker.stats = (t) => { sampledAt.push(t); return { rx_fps: 25 }; };
        const start = clock.now;
        h.c.collectStats();
        clock.now += 7000;
        release({ rx_kbps: 10 });
        await settle();
        return sampledAt.map((t) => t - start);
        """
    )
    assert out == [7000]


def test_superseded_teardown_reports_cleanup_done_after_the_fallback_exit():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverClose(4409);
        await settle();
        const early = h.posted.filter((m) => m.t === 'cleanup_done').length;
        await advance(15100);
        await settle();
        return { early, done: h.posted.filter((m) => m.t === 'cleanup_done').length,
                 order: kinds(h.log).filter((k) => k === 'exitRoom' || k === 'destroy') };
        """
    )
    assert out == {"early": 0, "done": 1, "order": ["exitRoom", "destroy"]}


def test_fallback_exit_is_bounded_when_the_sdk_hangs():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.trtc.exitRoom = () => { h.log.push(['exitRoom-hang']); return new Promise(() => {}); };
        h.ws.serverClose(4409);
        await settle();
        await advance(15100);
        const during = h.posted.filter((m) => m.t === 'cleanup_done').length;
        await advance(8100);
        await settle();
        return { during, after: h.posted.filter((m) => m.t === 'cleanup_done').length, destroyed: kinds(h.log).includes('destroy') };
        """
    )
    assert out == {"during": 0, "after": 1, "destroyed": True}


def test_livekit_destroy_after_leave_needs_no_fallback():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        let disconnects = 0;
        LK.Room.prototype.disconnect = function () { disconnects += 1; return Promise.resolve(); };
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join({ vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp9' } });
        await a.leave();
        let done = false;
        a.destroy().then(() => { done = true; });
        await settle();
        const immediate = done;
        await advance(16000);
        return { immediate, disconnects };
        """
    )
    assert out == {"immediate": True, "disconnects": 1}


def test_stop_finishes_even_when_the_vendor_leave_never_settles():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.trtc.exitRoom = () => new Promise(() => {});      // 网络坏着：离房一直不落定
        h.ws.serverSend({ type: 'stop', reason: 'route_end' });
        await settle();
        await advance(4900);
        const early = { closedWith: h.ws.closedWith || null };
        await advance(200);
        return { early, closedWith: h.ws.closedWith, lastState: h.ws.sent.filter((m) => m.type === 'state').pop().state };
        """
    )
    assert out == {"early": {"closedWith": None}, "closedWith": 1000, "lastState": "left"}


def test_stuck_sdk_chain_still_destroys_and_reports_cleanup_done():
    out = run_visit_js(
        """
        const runs = {};
        // stop：发布一直不落定（离房排在它后面同样卡住）→ 5 s 收线后不再等操作链；
        // 已发出的退房还在路上，再等它最多 8 s 才销毁、报 cleanup_done（父页据此才移除 iframe）
        {
          const h = await joinedController('guest');
          h.trtc.startLocalVideo = () => new Promise(() => {});
          h.trtc.exitRoom = () => new Promise(() => {});
          h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
          await settle();
          h.ws.serverSend({ type: 'stop', reason: 'route_end' });
          await settle();
          await advance(5100);
          await settle();
          const at5 = { destroyed: kinds(h.log).includes('destroy'), done: h.posted.some((m) => m.t === 'cleanup_done') };
          await advance(8000);
          await settle();
          runs.stop = { at5, destroyed: kinds(h.log).includes('destroy'), done: h.posted.some((m) => m.t === 'cleanup_done') };
        }
        // 4409 被顶号：在途发布一直不落定 → 最多等 2 s 就交给适配器（15 s 兜底退房、退房本身最多 8 s）
        {
          const h = await joinedController('guest');
          h.trtc.startLocalVideo = () => new Promise(() => {});
          h.trtc.exitRoom = () => new Promise(() => {});
          h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
          await settle();
          h.ws.serverClose(4409);
          await settle();
          await advance(2100 + 15000 + 8000);
          await settle();
          runs.superseded = { destroyed: kinds(h.log).includes('destroy'), done: h.posted.some((m) => m.t === 'cleanup_done') };
        }
        return runs;
        """
    )
    assert out == {"stop": {"at5": {"destroyed": False, "done": False}, "destroyed": True, "done": True},
                   "superseded": {"destroyed": True, "done": True}}


def test_livekit_destroy_waits_for_a_disconnect_still_in_flight():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const creds = { vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp8', bitrate_kbps: 560, fps: 30 } };
        // leave 发出的 disconnect 还没落定时拆适配器：等它做完才算清理完成
        const a = LKV.createAdapter({ sdk: LK, handlers });
        await a.join(creds);
        let finishDisconnect;
        LK.Room.last.disconnect = () => new Promise((res) => { finishDisconnect = res; });
        a.leave();
        let cleaned = false;
        a.destroy().then(() => { cleaned = true; });
        await advance(3000);
        const before = cleaned;
        finishDisconnect();
        await settle();
        const after = cleaned;
        // 一直不落定：最多 8 s
        const b = LKV.createAdapter({ sdk: LK, handlers });
        await b.join(creds);
        LK.Room.last.disconnect = () => new Promise(() => {});
        b.leave();
        let cleanedB = false;
        b.destroy().then(() => { cleanedB = true; });
        await advance(7900);
        const early = cleanedB;
        await advance(200);
        return { before, after, early, late: cleanedB };
        """
    )
    assert out == {"before": False, "after": True, "early": False, "late": True}


def test_stop_timeout_with_async_socket_close_reports_cleanup_once():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.trtc.exitRoom = () => new Promise(() => {});        // 网络坏着：退房一直不落定
        // 真浏览器里 ws.close() 的 onclose 是异步派发的：在拆适配器之后才走离房 → onTerminal → teardown
        const ws = h.ws;
        const syncClose = ws.close.bind(ws);
        ws.close = (code) => { setTimeoutF(() => syncClose(code), 0); };
        h.ws.serverSend({ type: 'stop', reason: 'route_end' });
        await settle();
        const done = () => h.posted.filter((m) => m.t === 'cleanup_done').length;
        await advance(10500);
        await settle();
        const at10 = done();
        await advance(3000);
        await settle();
        return { at10, at13: done(), destroys: kinds(h.log).filter((k) => k === 'destroy').length };
        """
    )
    # First teardown awaits room exit (up to 8s); a second teardown must not report cleanup_done early during it.
    assert out == {"at10": 0, "at13": 1, "destroys": 1}


def test_stale_manual_rejoin_does_not_join_after_a_socket_replacement():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        const exits = [];
        h.trtc.exitRoom = () => new Promise((r) => { exits.push(r); });
        const enters = () => kinds(h.log).filter((k) => k === 'enterRoom').length;
        h.c.rejoin();                                   // 手动重新入房：离房还在路上
        await settle();
        h.ws.serverClose(1006);                         // 这时传输 WS 断了 → 换连接代，断线这次也发出离房
        await settle();
        exits[1]();                                      // 断线那次离房先落定 → 重连
        await settle();
        await advance(1100);
        const ws2 = sockets[sockets.length - 1];
        ws2.serverOpen();
        ws2.serverSend(credsFor('guest'));               // 新连接拿到凭证后自己入房
        await settle();
        const afterReconnect = enters();
        exits[0]();                                      // 手动重新入房那次离房迟到落定
        await settle();
        await settle();
        return { exits: exits.length, replaced: ws2 !== h.ws, reconnectJoined: afterReconnect > 1, extra: enters() - afterReconnect };
        """
    )
    assert out == {"exits": 2, "replaced": True, "reconnectJoined": True, "extra": 0}


def test_stalled_sdk_script_fails_the_capability_gate_in_time():
    out = run_visit_js(
        """
        const ctx = makeContext({ search: '?v=1&side=guest&visit_id=' + VISIT_ID });
        load(ctx, FILES);
        const NVT = ctx.NekoVisitTransport;
        const doc = ctx.document;
        // 不注入 loadSdk：走真实的脚本插入，fake DOM 里脚本既不报 load 也不报 error（请求卡住）
        const c = new NVT.loader.VisitTransportController({
          window: ctx, document: doc, side: 'guest', visitId: VISIT_ID, post: () => {}, log: () => {},
          video: doc.createElement('video'), canvas: doc.createElement('canvas'),
        });
        c.preflight = { ok: true };
        c.onAuthToken('tok');
        const ws = sockets[sockets.length - 1];
        ws.serverOpen();
        ws.serverSend(credsFor('guest'));
        await settle();
        const caps = () => ws.sent.filter((m) => m.type === 'caps' && m.stage === 'sdk');
        await advance(9900);
        const early = caps().length;
        await advance(200);
        const script = doc.created.find((el) => el.tagName === 'SCRIPT');
        return { early, caps: caps().map((m) => ({ ok: m.transport_ok, reason: m.reason })), removed: !!(script && script.removed) };
        """
    )
    assert out == {"early": 0, "caps": [{"ok": False, "reason": "sdk_load_failed"}], "removed": True}


def test_manual_rejoin_clears_stale_peer_presence():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.c.peerPresent = true;                         // 断线前对端在场；断线期间它走了、退出事件没送到
        h.c.rejoin();
        await settle();
        await settle();
        const joined = h.ws.sent.filter((m) => m.type === 'state' && m.state === 'joined').pop();
        return { flag: h.c.peerPresent, reported: joined ? joined.peer_present : null };
        """
    )
    assert out == {"flag": False, "reported": False}


def test_stalled_async_stats_still_emit_each_tick():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        h.c.media.subscribe = true;
        h.c.adapter.receiverStats = () => new Promise(() => {});     // getStats 一直不回
        const stats = () => h.ws.sent.filter((m) => m.type === 'stats').length;
        const before = stats();
        h.c.collectStats();
        await advance(3900);
        const early = stats() - before;
        await advance(200);
        return { early, late: stats() - before };
        """
    )
    # At 4s, send synchronous stats only (queue length, received frame rate, etc.) so backend statistics cannot stall the ladder.
    assert out == {"early": 0, "late": 1}


def test_stalled_receiver_stats_still_carry_the_fields_the_backend_forwards():
    out = run_visit_js(
        """
        const runs = {};
        for (const label of ['afterSample', 'neverSampled']) {
          const h = await joinedController('host');
          h.c.media.subscribe = true;
          h.c.unpacker.stats = () => ({ rx_fps: 25, rx_w: 320, rx_h: 896 });
          if (label === 'afterSample') {
            h.c.adapter.receiverStats = () => Promise.resolve({ rx_kbps: 300, rtt_ms: 40, loss_pct: 1 });
            h.c.collectStats();
            await settle();
          }
          h.c.adapter.receiverStats = () => new Promise(() => {});     // getStats 卡住
          h.c.collectStats();
          await advance(4100);
          const last = h.ws.sent.filter((m) => m.type === 'stats').pop();
          runs[label] = { rx_fps: last.rx_fps, rx_kbps: last.rx_kbps, rtt_ms: last.rtt_ms, loss_pct: last.loss_pct };
        }
        return runs;
        """
    )
    # Backend forwards only when all three fields exist; the peer reads fresh rx_fps.
    assert out == {"afterSample": {"rx_fps": 25, "rx_kbps": 300, "rtt_ms": 40, "loss_pct": 1},
                   "neverSampled": {"rx_fps": 25, "rx_kbps": 0, "rtt_ms": 0, "loss_pct": 0}}


def test_empty_or_partial_receiver_samples_are_filled_too():
    out = run_visit_js(
        """
        const h = await joinedController('host');
        h.c.media.subscribe = true;
        h.c.unpacker.stats = () => ({ rx_fps: 25, rx_w: 320, rx_h: 896 });
        const pick = () => { const m = h.ws.sent.filter((x) => x.type === 'stats').pop(); return { rx_kbps: m.rx_kbps, rtt_ms: m.rtt_ms, loss_pct: m.loss_pct }; };
        h.c.adapter.receiverStats = () => Promise.resolve(null);            // getStats 失败，适配器给了空
        h.c.collectStats(); await settle();
        const empty = pick();
        h.c.adapter.receiverStats = () => Promise.resolve({ rx_kbps: 300, rtt_ms: 40, loss_pct: 1 });
        h.c.collectStats(); await settle();
        h.c.adapter.receiverStats = () => Promise.resolve({ rx_kbps: 200 }); // 只给了一部分
        h.c.collectStats(); await settle();
        const partial = pick();
        return { empty, partial };
        """
    )
    assert out == {"empty": {"rx_kbps": 0, "rtt_ms": 0, "loss_pct": 0},
                   "partial": {"rx_kbps": 200, "rtt_ms": 40, "loss_pct": 1}}


def test_initial_join_that_never_settles_is_left_to_the_backend_deadline():
    out = run_visit_js(
        """
        const h = makeController('guest');
        const origCreate = h.TRTC.create;
        h.TRTC.create = () => {
          const inst = origCreate();
          inst.enterRoom = () => { h.log.push(['enterRoom-hang']); return new Promise(() => {}); };   // 一开始就卡住
          return inst;
        };
        h.c.onAuthToken('tok');
        const ws = sockets[sockets.length - 1];
        ws.serverOpen();
        ws.serverSend(credsFor('guest'));
        await settle();
        const errors = () => ws.sent.filter((m) => m.type === 'state' && m.state === 'error').map((m) => m.error_code);
        await advance(30000);
        return { errors: errors(), instances: h.TRTC.instances.length, enters: kinds(h.log).filter((k) => k.startsWith('enterRoom')).length };
        """
    )
    # Initial room entry failure: no premature client failure or adapter retry; let the backend's 25s first-entry deadline end the session.
    # SDK TURN fallback can take over ten seconds on slow networks; a 10s client timeout would reject valid attempts.
    assert out == {"errors": [], "instances": 1, "enters": 1}


def test_stuck_rejoin_leave_replaces_the_adapter():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        const old = h.trtc;
        old.exitRoom = () => { h.log.push(['exitRoom-hang']); return new Promise(() => {}); };   // 离房一直不结束
        h.c.rejoin();
        await settle();
        await advance(4900);
        const early = h.TRTC.instances.length;
        await advance(200);
        await settle();
        const fresh = h.TRTC.instances[h.TRTC.instances.length - 1];
        const states = () => h.ws.sent.filter((m) => m.type === 'state').map((m) => m.state);
        const joinedAfter = states().lastIndexOf('joined');
        // 旧实例迟到的事件（被同身份顶下线）不再影响控制器
        old.emit('KICKED_OUT', { reason: 'kick' });
        await settle();
        return { early, instances: h.TRTC.instances.length, freshIsNew: fresh !== old, joined: joinedAfter >= 0,
                 kickedReported: states().includes('kicked') };
        """
    )
    assert out == {"early": 1, "instances": 2, "freshIsNew": True, "joined": True, "kickedReported": False}


def test_stuck_mid_session_join_is_retried_with_a_new_adapter():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        const origCreate = h.TRTC.create;
        let made = 0;
        h.TRTC.create = () => {
          const inst = origCreate();
          made += 1;
          if (made === 1) inst.enterRoom = () => { h.log.push(['enterRoom-hang']); return new Promise(() => {}); };
          return inst;
        };
        // 中途断线后重进：现有适配器的入房卡住
        h.trtc.enterRoom = () => { h.log.push(['enterRoom-hang']); return new Promise(() => {}); };
        h.c.rejoin();
        await settle();
        await advance(9900);
        const early = h.TRTC.instances.length;
        await advance(200);                     // 10 s：丢掉卡住的适配器，按退避换新的再试
        await advance(1100);
        await settle();
        const errors = h.ws.sent.filter((m) => m.type === 'state' && m.state === 'error').length;
        return { early, instances: h.TRTC.instances.length, errors };
        """
    )
    assert out["early"] == 1 and out["instances"] >= 2 and out["errors"] == 0


def test_immediate_destroy_exits_without_the_superseded_wait():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const runs = {};
        // TRTC：在房时 immediate 销毁马上退房，不等 15 s 顶号窗口
        {
          const h = await joinedController('guest');
          h.log.length = 0;
          h.c.retireAdapter();
          await settle();                      // 销毁排在下一个微任务里
          await advance(100);
          runs.trtc = kinds(h.log).filter((k) => k === 'exitRoom' || k === 'destroy');
        }
        // LiveKit：同样马上断开
        {
          const ctx = makeContext();
          load(ctx, FILES);
          const LKV = ctx.NekoVisitTransport.vendors.livekit;
          const LK = makeFakeLK([]);
          const handlers = { onPiece() {}, onPeer() {}, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
          const creds = { vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp8', bitrate_kbps: 560, fps: 30 } };
          const a = LKV.createAdapter({ sdk: LK, handlers });
          await a.join(creds);
          let disconnects = 0;
          LK.Room.last.disconnect = () => { disconnects += 1; return Promise.resolve(); };
          let done = false;
          a.destroy({ immediate: true }).then(() => { done = true; });
          await advance(100);
          runs.livekit = { disconnects, done };
        }
        return runs;
        """
    )
    assert out == {"trtc": ["exitRoom", "destroy"], "livekit": {"disconnects": 1, "done": True}}


def test_late_publish_result_of_a_retired_adapter_is_ignored():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        const old = h.trtc;
        let failOld;
        old.startLocalVideo = () => new Promise((_, reject) => { failOld = reject; });   // 旧适配器上的发布挂着
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });
        await settle();
        old.exitRoom = () => new Promise(() => {});                                      // 离房也卡住
        h.c.rejoin();
        await settle();
        await advance(5100);                                                              // 换新适配器、入房
        await settle();
        h.ws.serverSend({ type: 'media', publish: true, crop: 'upper', ladder: 0 });   // 重进后后端重发媒体快照
        await settle();
        const posts = () => h.posted.filter((m) => m.t === 'publish').map((m) => m.on);
        const before = posts();
        failOld(new Error('stale'));                                                      // 旧发布迟到失败
        await settle();
        await advance(40000);
        // 新适配器已经在发布：旧发布的迟到失败不能叫停取帧（哪怕之后的重试又把它拉回来，中间也会冻住）
        return { last: before[before.length - 1], afterStale: posts().slice(before.length),
                 freshPublishes: kinds(h.log).filter((k) => k === 'startLocalVideo').length };
        """
    )
    assert out == {"last": True, "afterStale": [], "freshPublishes": 1}


def test_peer_enter_queued_while_leaving_does_not_restore_presence():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.trtc.exitRoom = () => new Promise(() => {});        // 离房还没结束
        h.c.peerPresent = true;
        h.ws.serverClose(1006);                                 // 传输 WS 断了：清在场、离房
        await settle();
        h.trtc.emit('REMOTE_USER_ENTER', { userId: 'h_peer' });  // 离房期间排队到的旧进房事件
        await settle();
        return { present: h.c.peerPresent };
        """
    )
    assert out == {"present": False}


def test_livekit_ignores_peer_enter_while_leaving():
    out = run_visit_js(
        _FAKE_LIVEKIT + """
        const ctx = makeContext();
        load(ctx, FILES);
        const LKV = ctx.NekoVisitTransport.vendors.livekit;
        const LK = makeFakeLK([]);
        const peers = [];
        const handlers = { onPiece() {}, onPeer(kind) { peers.push(kind); }, onState() {}, onRemoteTrack() {}, onNetwork() {}, onNeedRejoin() {}, onLog() {} };
        const creds = { vendor: { livekit: { url: 'wss://lk.example.com', token: 't' } }, allowed_hosts: ['lk.example.com'], publish: { codec: 'vp8', bitrate_kbps: 560, fps: 30 } };
        const a = LKV.createAdapter({ sdk: LK, handlers, peerVid: 'h_peer' });
        await a.join(creds);
        LK.Room.last.disconnect = () => new Promise(() => {});    // 离房还没结束
        a.leave();
        LK.Room.last.emit('ParticipantConnected', { identity: 'h_peer', trackPublications: new Map() });
        return peers;
        """
    )
    assert out == []


def test_every_join_starts_without_stale_peer_presence():
    out = run_visit_js(
        """
        const h = await joinedController('guest');
        h.ws.serverClose(1006);                     // 传输 WS 断了：离房、重连
        await settle();
        await advance(1100);
        const ws2 = sockets[sockets.length - 1];
        ws2.serverOpen();
        h.c.peerPresent = true;                     // 残留的在场标记（离房期间排队到的旧事件之类）
        ws2.serverSend(credsFor('guest'));          // 新连接拿到凭证后入房
        await settle();
        const joined = ws2.sent.filter((m) => m.type === 'state' && m.state === 'joined').pop();
        return joined ? joined.peer_present : 'none';
        """
    )
    assert out is False
