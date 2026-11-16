'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const path = require('node:path');

const sourceRoot = process.env.NEKO_DESKTOP_SOURCE;
if (!sourceRoot) throw new Error('Set NEKO_DESKTOP_SOURCE to the companion Electron shell checkout');

function harness(t) {
  function load(name) {
    const module = { exports: {} };
    vm.runInNewContext(fs.readFileSync(path.join(sourceRoot, name), 'utf8'), {
      module, exports: module.exports, Event, MessageEvent,
      CloseEvent: class extends Event {
        constructor(type, data) { super(type); Object.assign(this, data); }
      },
      console: { log() {}, warn() {}, error() {} },
    });
    return module.exports;
  }
  const channels = load('src/ipc-channels.js');
  const lifecycle = load('src/preload/shared/lifecycle.js');
  const petBridge = load('src/preload/bridges/pet-websocket-bridge.js');
  const chatBridge = load('src/preload/bridges/chat-websocket-bridge.js');
  const wire = [], mirrored = [], chats = [];
  class NativeSocket extends EventTarget {
    constructor() { super(); this.readyState = NativeSocket.CONNECTING; }
    send(raw) { wire.push(JSON.parse(raw)); }
    receive(message) { this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(message) })); }
    open() { this.readyState = NativeSocket.OPEN; this.dispatchEvent(new Event('open')); }
  }
  Object.assign(NativeSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  function makeWindow() {
    const window = new EventTarget();
    Object.assign(window, { WebSocket: NativeSocket, setTimeout, clearTimeout, setInterval, clearInterval,
      appState: { lanlan_name: 'Fixture' }, fetch: async () => ({ ok: true, json: async () => ({ active: false }) }) });
    t.after(() => window.dispatchEvent(new Event('pagehide')));
    return window;
  }
  const petWindow = makeWindow(), petIpc = new EventEmitter();
  petIpc.sendSync = () => 1;
  petIpc.send = (channel, data) => {
    if (channel === channels.WS_PROXY_CHANNELS.RAW_MESSAGE) mirrored.push(JSON.parse(data));
    for (const chat of chats) chat.ipc.emit(channel, {}, data);
  };
  petBridge.installPetWebSocketBridge({ lifecycle: lifecycle.getPreloadLifecycle(petWindow),
    window: petWindow, ipcRenderer: petIpc, channels });
  const pet = new petWindow.WebSocket('ws://fixture/ws/Fixture');
  for (const role of ['compact-chat', 'full-chat']) {
    const window = makeWindow(), ipc = new EventEmitter();
    ipc.send = (channel, data) => petIpc.emit(channel, {}, data);
    chatBridge.installChatWebSocketBridge({ lifecycle: lifecycle.getPreloadLifecycle(window),
      window, ipcRenderer: ipc, WS_PROXY_CHANNELS: channels.WS_PROXY_CHANNELS });
    const socket = new window.WebSocket('ws://fixture/ws/Fixture');
    chats.push({ role, window, ipc, socket });
  }
  pet.open();
  return { pet, petWindow, petIpc, chats, wire, mirrored, channels };
}

for (const operation of ['overview', 'window', 'validate']) {
  test(`${operation} reaches only the pet and cannot lose to a follower rejection`, async t => {
    const h = harness(t);
    for (const { socket } of h.chats) {
      socket.onmessage = ({ data }) => {
        const request = JSON.parse(data);
        if (request.type === 'request_screen_inspection') {
          socket.send(JSON.stringify({ action: 'screen_inspection_response', request_id: request.request_id,
            success: false, error: 'unsupported_capture' }));
        }
      };
    }
    let finishCapture;
    const pendingCapture = new Promise(resolve => { finishCapture = resolve; });
    let ownerRequest;
    h.pet.onmessage = ({ data }) => {
      ownerRequest = JSON.parse(data);
      pendingCapture.then(() => h.pet.send(JSON.stringify({ action: 'screen_inspection_response',
        request_id: ownerRequest.request_id, scope: ownerRequest.scope, success: true })));
    };
    const request = { type: 'request_screen_inspection', request_id: operation, operation,
      scope: 'window:allowed', window_id: 'window:allowed' };
    h.pet.receive(request);
    assert.deepEqual(ownerRequest, request);
    assert.deepEqual(h.mirrored, [], 'capture control frames must not reach chat renderers');
    assert.deepEqual(h.wire, [], 'no follower may settle a pending pet capture');
    finishCapture();
    await pendingCapture;
    assert.deepEqual(h.wire, [{ action: 'screen_inspection_response', request_id: operation,
      scope: 'window:allowed', success: true }]);
  });
}

for (const role of ['compact-chat', 'full-chat']) {
  test(`${role} cannot inject either success or failure inspection receipts`, t => {
    const h = harness(t), chat = h.chats.find(c => c.role === role);
    for (const success of [true, false]) chat.socket.send(JSON.stringify({
      action: 'screen_inspection_response', request_id: 'forged', success,
    }));
    assert.deepEqual(h.wire, []);
    chat.socket.send(JSON.stringify({ action: 'text', text: 'ordinary chat' }));
    assert.deepEqual(h.wire, [{ action: 'text', text: 'ordinary chat' }]);
  });
}

test('owner privacy declines and ordinary chat results still flow normally', t => {
  const h = harness(t), received = [];
  for (const { socket } of h.chats) socket.onmessage = e => received.push(JSON.parse(e.data));
  h.pet.onmessage = ({ data }) => {
    const request = JSON.parse(data);
    if (request.type === 'request_screen_inspection') h.pet.send(JSON.stringify({
      action: 'screen_inspection_response', request_id: request.request_id, success: false, error: 'disabled',
    }));
  };
  h.pet.receive({ type: 'request_screen_inspection', request_id: 'privacy', operation: 'overview' });
  assert.deepEqual(h.wire, [{ action: 'screen_inspection_response', request_id: 'privacy',
    success: false, error: 'disabled' }]);
  const response = { type: 'gemini_response', text: 'Hello' };
  h.pet.receive(response);
  assert.deepEqual(received, [response, response]);
});

test('replaced socket control frames never reappear in chat mirrors', t => {
  const h = harness(t);
  const current = new h.petWindow.WebSocket('ws://fixture/ws/Fixture');
  current.open();
  h.pet.receive({ type: 'request_screen_inspection', request_id: 'old', operation: 'overview' });
  current.receive({ type: 'request_screen_inspection', request_id: 'new', operation: 'overview' });
  assert.deepEqual(h.mirrored, []);
});
