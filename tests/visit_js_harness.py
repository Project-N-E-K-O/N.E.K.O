"""Node harness for the visit transport page and parent bridge scripts.

Loads the real ``static/visit/**`` sources into a ``vm`` context that has fake
timers (manual clock), a fake DOM (canvases, video, iframes), a fake
``WebSocket`` and a fake TRTC SDK, runs a test body and returns the JSON the
body resolves to. Nothing here talks to a network or a real browser.
"""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path
from typing import Any, Iterable

from tests.node_harness import run_node_script

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRANSPORT_DIR = PROJECT_ROOT / "static" / "visit" / "transport"
TRANSPORT_FILES = tuple(
    TRANSPORT_DIR / name
    for name in (
        "transport.js",
        "pack.js",
        "unpack.js",
        "voice.js",
        "trtc-transport.js",
        "livekit-transport.js",
        "frame-sink.js",
        "backend-ws.js",
        "loader.js",
    )
)
BRIDGE_FILES = (
    PROJECT_ROOT / "static" / "visit" / "crop-geometry.js",
    PROJECT_ROOT / "static" / "visit" / "voice-relay.js",
    PROJECT_ROOT / "static" / "visit" / "parent-bridge.js",
)
VISIT_ID = "AbCdEfGhIjKlMnOpQrStUv"

_PRELUDE = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const zlib = require('node:zlib');

// ── 手动时钟 ───────────────────────────────────────────────────────────
const clock = { now: 1000000, seq: 0, timers: new Map() };
function setTimeoutF(fn, ms) { const id = ++clock.seq; clock.timers.set(id, { at: clock.now + Math.max(0, ms || 0), fn, every: null }); return id; }
function setIntervalF(fn, ms) { const id = ++clock.seq; clock.timers.set(id, { at: clock.now + ms, fn, every: ms }); return id; }
function clearTimerF(id) { clock.timers.delete(id); }
const flush = () => new Promise((r) => setImmediate(r));
async function settle() { for (let i = 0; i < 6; i++) await flush(); }
async function advance(ms) {
  const end = clock.now + ms;
  for (;;) {
    let next = null;
    for (const [id, t] of clock.timers) if (t.at <= end && (!next || t.at < next[1].at || (t.at === next[1].at && id < next[0]))) next = [id, t];
    if (!next) break;
    const [id, t] = next;
    clock.now = Math.max(clock.now, t.at);
    if (t.every) t.at += t.every; else clock.timers.delete(id);
    try { t.fn(); } catch (e) { errors.push(String(e && e.stack || e)); }
    await settle();
  }
  clock.now = end;
  await settle();
}
const errors = [];

// ── PNG（首帧缩略图用：toBlob 产出真正的 PNG 字节）────────────────────────
function crc32(buf) { let c, crc = 0xffffffff; for (let n = 0; n < buf.length; n++) { c = (crc ^ buf[n]) & 0xff; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; crc = (crc >>> 8) ^ c; } return (crc ^ 0xffffffff) >>> 0; }
function chunk(type, data) { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]); const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, crc]); }
function makePng(w, h) {
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  const raw = Buffer.alloc((w * 4 + 1) * h);
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

// ── 假 DOM ─────────────────────────────────────────────────────────────
let trackSeq = 0;
class FakeTrack { constructor(canvas) { this.id = 'track-' + (++trackSeq); this.kind = 'video'; this.readyState = 'live'; this.frames = 0; this.canvas = canvas; this.contentHint = ''; } requestFrame() { this.frames++; } stop() { this.readyState = 'ended'; } }
function makeCtx2d(canvas) {
  return new Proxy({ calls: [], globalCompositeOperation: 'source-over', fillStyle: '' }, {
    get(target, prop) {
      if (prop in target) return target[prop];
      return (...args) => { target.calls.push([prop, args.length]); };
    },
    set(target, prop, value) { target[prop] = value; return true; },
  });
}
function makeGl() {
  const consts = { VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4, ARRAY_BUFFER: 5, STATIC_DRAW: 6, FLOAT: 7, TEXTURE_2D: 8, TEXTURE_MIN_FILTER: 9, TEXTURE_MAG_FILTER: 10, LINEAR: 11, CLAMP_TO_EDGE: 12, TEXTURE_WRAP_S: 13, TEXTURE_WRAP_T: 14, BLEND: 15, ONE: 16, ONE_MINUS_SRC_ALPHA: 17, COLOR_BUFFER_BIT: 18, UNPACK_FLIP_Y_WEBGL: 19, UNPACK_PREMULTIPLY_ALPHA_WEBGL: 20, RGBA: 21, UNSIGNED_BYTE: 22, TRIANGLE_STRIP: 23 };
  const gl = { calls: [] };
  return new Proxy(gl, {
    get(target, prop) {
      if (prop in consts) return consts[prop];
      if (prop in target) return target[prop];
      if (prop === 'getShaderParameter' || prop === 'getProgramParameter') return () => true;
      if (prop === 'getExtension') return () => null;
      if (prop === 'getAttribLocation') return () => 0;
      return (...args) => { target.calls.push(prop); return {}; };
    },
  });
}
class FakeElement {
  constructor(tag, doc) { this.tagName = tag.toUpperCase(); this.ownerDocument = doc; this.style = { cssText: '' }; this.children = []; this.attributes = {}; this.classList = new FakeClassList(); this.id = ''; this.className = ''; this.textContent = ''; this.removed = false; }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return this.attributes[k]; }
  appendChild(c) { this.children.push(c); c.parentNode = this; if (this.ownerDocument) this.ownerDocument._attach(c); return c; }
  remove() { this.removed = true; if (this.ownerDocument) this.ownerDocument._detach(this); }
  querySelector() { return null; }
  getBoundingClientRect() { return this._rect || { left: 0, top: 0, width: this.width || 0, height: this.height || 0 }; }
}
class FakeClassList { constructor() { this.set = new Set(); } toggle(c, on) { if (on) this.set.add(c); else this.set.delete(c); } contains(c) { return this.set.has(c); } add(c) { this.set.add(c); } remove(c) { this.set.delete(c); } }
class FakeCanvas extends FakeElement {
  constructor(doc) { super('canvas', doc); this.width = 300; this.height = 150; this.streams = 0; this._ctx = null; this._gl = null; }
  getContext(kind) { if (kind === '2d') return this._ctx || (this._ctx = makeCtx2d(this)); if (kind === 'webgl') return this._gl || (this._gl = makeGl()); return null; }
  captureStream() { this.streams++; const t = new FakeTrack(this); return { getVideoTracks: () => [t] }; }
  toBlob(cb, type) { const bytes = makePng(this.width, this.height); setTimeoutF(() => cb({ type, bytes }), 0); }
}
class FakeDocument {
  constructor() { this.byId = new Map(); this.body = new FakeElement('body', this); this.head = new FakeElement('head', this); this.created = []; }
  createElement(tag) { const el = tag === 'canvas' ? new FakeCanvas(this) : new FakeElement(tag, this); this.created.push(el); return el; }
  getElementById(id) { const el = this.byId.get(id); return el && el.id === id && !el.removed ? el : null; }
  _attach(el) { if (el.id) this.byId.set(el.id, el); }
  _detach(el) { if (el.id && this.byId.get(el.id) === el) this.byId.delete(el.id); }
}
class FakeFileReader { readAsDataURL(blob) { this.result = 'data:' + blob.type + ';base64,' + Buffer.from(blob.bytes).toString('base64'); setTimeoutF(() => this.onload && this.onload(), 0); } }
class FakeMediaStream { constructor(tracks) { this.tracks = tracks; } getVideoTracks() { return this.tracks; } getAudioTracks() { return this.tracks.filter((t) => t.kind === 'audio'); } getTracks() { return this.tracks; } }
// 本页 realm 的语音轨：createMediaStreamDestination 每次给一条新的 audio 轨（被 stop 后 ensureTrack 重建）
class FakeAudioTrack { constructor() { this.id = 'audio-' + (++trackSeq); this.kind = 'audio'; this.readyState = 'live'; } stop() { this.readyState = 'ended'; } }
const audioContexts = [];
class FakeAudioContext {
  constructor() { this.state = 'running'; this.closed = false; this.sources = []; this.dests = []; audioContexts.push(this); }
  createMediaStreamSource(stream) { const node = { stream, connectedTo: null, connect(d) { this.connectedTo = d; }, disconnect() { this.connectedTo = null; } }; this.sources.push(node); return node; }
  createMediaStreamDestination() { const d = { channelCount: 2, stream: new FakeMediaStream([new FakeAudioTrack()]) }; this.dests.push(d); return d; }
  resume() { this.state = 'running'; return Promise.resolve(); }
  close() { this.closed = true; return Promise.resolve(); }
}

// ── 假 WebSocket ───────────────────────────────────────────────────────
const sockets = [];
class FakeWebSocket {
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; this.closedWith = null; sockets.push(this); }
  send(text) { this.sent.push(JSON.parse(text)); }
  close(code) { if (this.readyState === 3) return; this.closedWith = code; this.readyState = 3; if (this.onclose) this.onclose({ code: code || 1000 }); }
  serverOpen() { this.readyState = 1; this.onopen && this.onopen(); }
  serverSend(obj) { this.onmessage && this.onmessage({ data: JSON.stringify(obj) }); }
  serverClose(code) { if (this.readyState === 3) return; this.readyState = 3; this.onclose && this.onclose({ code }); }
}
FakeWebSocket.prototype.send.toString = () => 'function send() { [native code] }';

// ── 假 TRTC SDK ────────────────────────────────────────────────────────
function makeFakeTRTC(log) {
  const names = ['REMOTE_USER_ENTER', 'REMOTE_USER_EXIT', 'REMOTE_VIDEO_AVAILABLE', 'REMOTE_VIDEO_UNAVAILABLE', 'REMOTE_AUDIO_AVAILABLE', 'REMOTE_AUDIO_UNAVAILABLE', 'TRACK', 'CUSTOM_MESSAGE', 'CONNECTION_STATE_CHANGED', 'KICKED_OUT', 'NETWORK_QUALITY', 'STATISTICS', 'ERROR'];
  const EVENT = {}; names.forEach((n) => { EVENT[n] = n; });
  const instances = [];
  const remoteTrack = new FakeTrack(null);
  const remoteAudioTrack = new FakeAudioTrack();
  return {
    EVENT, TYPE: { SCENE_RTC: 'rtc', ROLE_ANCHOR: 'anchor', STREAM_TYPE_MAIN: 'main' }, instances, remoteTrack, remoteAudioTrack,
    isSupported: () => Promise.resolve({ result: true, detail: { isWebRTCSupported: true, isH264EncodeSupported: true, isVp8EncodeSupported: true, isH264DecodeSupported: true, isVp8DecodeSupported: true } }),
    create() {
      const handlers = {};
      const inst = {
        on(e, f) { (handlers[e] = handlers[e] || []).push(f); },
        emit(e, a) { (handlers[e] || []).forEach((f) => f(a)); },
        enterRoom(p) { log.push(['enterRoom', p.userId, p.userSig, p.autoReceiveAudio, p.autoReceiveVideo]); return Promise.resolve(); },
        exitRoom() { log.push(['exitRoom']); return Promise.resolve(); },
        startLocalVideo(c) { log.push(['startLocalVideo', c.option.videoTrack.id, c.option.profile]); return Promise.resolve(); },
        updateLocalVideo(c) { log.push(['updateLocalVideo', c.option && c.option.profile, c.option && c.option.videoTrack && c.option.videoTrack.id]); return Promise.resolve(); },
        stopLocalVideo() { log.push(['stopLocalVideo']); return Promise.resolve(); },
        startRemoteVideo(c) { log.push(['startRemoteVideo', c.userId, c.view]); return Promise.resolve(); },
        stopRemoteVideo(c) { log.push(['stopRemoteVideo', c.userId]); return Promise.resolve(); },
        getVideoTrack() { return remoteTrack; },
        startLocalAudio(c) { log.push(['startLocalAudio', c.option.audioTrack.id, c.option.profile]); return Promise.resolve(); },
        updateLocalAudio(c) { log.push(['updateLocalAudio', c.option.audioTrack.id]); return Promise.resolve(); },
        stopLocalAudio() { log.push(['stopLocalAudio']); return Promise.resolve(); },
        muteRemoteAudio(userId, mute) { log.push(['muteRemoteAudio', userId, mute]); return Promise.resolve(); },
        setRemoteAudioVolume(userId, volume) { log.push(['setRemoteAudioVolume', userId, volume]); },
        getAudioTrack(o) { return o && o.userId ? remoteAudioTrack : null; },
        sendCustomMessage(m) { log.push(['send', clock.now, m.cmdId, m.data.byteLength]); },
        stopPlugin(n) { log.push(['stopPlugin', n]); return Promise.resolve(); },
        destroy() { log.push(['destroy']); },
      };
      instances.push(inst);
      return inst;
    },
  };
}

// ── vm 上下文 ──────────────────────────────────────────────────────────
function makeContext(opts) {
  opts = opts || {};
  const doc = new FakeDocument();
  const posted = [];
  const ctx = {
    console, TextEncoder, TextDecoder, URL, URLSearchParams, Uint8Array, ArrayBuffer, Buffer,
    setTimeout: setTimeoutF, clearTimeout: clearTimerF, setInterval: setIntervalF, clearInterval: clearTimerF,
    WebSocket: FakeWebSocket, MediaStream: FakeMediaStream, FileReader: FakeFileReader, AudioContext: FakeAudioContext,
    RTCPeerConnection: function RTCPeerConnection() {}, EventTarget,
    document: doc, isSecureContext: true, devicePixelRatio: 1,
    navigator: { userAgent: 'harness' },
    location: { protocol: 'http:', host: '127.0.0.1:48911', origin: 'http://127.0.0.1:48911', search: opts.search || '', pathname: opts.pathname || '/' },
    performance: { now: () => clock.now },
    listeners: {},
    addEventListener(type, fn) { (ctx.listeners[type] = ctx.listeners[type] || []).push(fn); },
    removeEventListener() {},
    dispatchEvent(ev) { (ctx.listeners[ev.type] || []).forEach((f) => f(ev)); },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    getComputedStyle: () => ({ visibility: 'visible', display: 'block' }),
    innerWidth: 1920, innerHeight: 1080,
    posted,
  };
  ctx.window = ctx;
  ctx.parent = { postMessage: (msg) => posted.push(JSON.parse(JSON.stringify(msg))) };
  ctx.Date = class extends Date { static now() { return clock.now; } };
  vm.createContext(ctx);
  return ctx;
}
function load(ctx, files) { for (const f of files) vm.runInContext(fs.readFileSync(f, 'utf8'), ctx, { filename: f }); }

// ── 传输页控制器（假 TRTC）────────────────────────────────────────────
function credsFor(side, extra) {
  return Object.assign({
    type: 'credentials', visit_id: VISIT_ID, side, transport: 'trtc',
    vendor: { trtc: { sdk_app_id: 1, user_id: side[0] + '_self', user_sig: 'sig1', private_map_key: 'pmk1', str_room_id: VISIT_ID, expire: 600 } },
    own_vid: side[0] + '_self', peer_vid: side === 'guest' ? 'h_peer' : null, allowed_hosts: [], tier: 'sd600',
    crop: 'upper', publish: { codec: 'h264', bitrate_kbps: 528, fps: 30 }, expires_at: 0,
  }, extra || {});
}
function makeController(side) {
  const ctx = makeContext({ search: '?v=1&side=' + side + '&visit_id=' + VISIT_ID });
  load(ctx, FILES);
  const log = [];
  const TRTC = makeFakeTRTC(log);
  const NVT = ctx.NekoVisitTransport;
  const posted = [];
  const doc = ctx.document;
  const c = new NVT.loader.VisitTransportController({
    window: ctx, document: doc, side, visitId: VISIT_ID, post: (m) => posted.push(m), log: () => {},
    loadSdk: () => Promise.resolve(TRTC), video: doc.createElement('video'), canvas: doc.createElement('canvas'),
  });
  c.preflight = { ok: true };
  return { ctx, c, log, TRTC, NVT, posted, sockets };
}
async function joinedController(side, credsExtra) {
  const h = makeController(side);
  h.c.onAuthToken('tok');
  const ws = sockets[sockets.length - 1];
  ws.serverOpen();
  ws.serverSend(credsFor(side, credsExtra));
  await settle();
  h.ws = ws;
  h.trtc = h.TRTC.instances[h.TRTC.instances.length - 1];
  return h;
}
const kinds = (log) => log.map((e) => e[0]);
// Servers opened the catgirl voice (OD-15 v4 plan A gate)
const VOICE_ON = { publish: { codec: 'h264', bitrate_kbps: 528, audio: true, audio_kbps: 32, fps: 30 } };
"""


def run_visit_js(body: str, *, files: Iterable[Path] = TRANSPORT_FILES, extra_prelude: str = "") -> Any:
    """Run ``body`` (the inside of an async function returning JSON-able data)."""
    node = shutil.which("node")
    if not node:
        raise AssertionError("node is required for the visit transport harness")
    file_list = json.dumps([str(p) for p in files])
    script = (
        _PRELUDE
        + extra_prelude
        + f"\nconst FILES = {file_list};\nconst VISIT_ID = {json.dumps(VISIT_ID)};\n"
        + "(async () => {\n"
        + textwrap.indent(textwrap.dedent(body), "  ")
        + "\n})().then((out) => { process.stdout.write(JSON.stringify({ out, errors })); },"
        + " (e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e), errors })); });\n"
    )
    proc = run_node_script(node, script, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    result = json.loads(proc.stdout)
    assert "fatal" not in result, result["fatal"]
    assert not result["errors"], result["errors"]
    return result["out"]
