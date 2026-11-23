const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// The pytest wrapper feeds this file's CONTENT to node from a temp path, so
// __dirname is not the repo. Same fallback the other wrapped frontend tests use.
const fileRoot = path.resolve(__dirname, '..', '..');
const PROJECT_ROOT = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const stateSource = fs.readFileSync(path.join(PROJECT_ROOT, 'static', 'app', 'app-state.js'), 'utf8');
const websocketSource = fs.readFileSync(path.join(PROJECT_ROOT, 'static', 'app', 'app-websocket.js'), 'utf8');

const DISCARD_LOG = '[App] discard assistant chunk after session ended by server';

// Loads the real app-state.js + app-websocket.js in a vm and drives the socket
// handlers directly. Only the suppression latch is under test: assertions look
// at whether a gemini_response reached the discard guard or got past it to
// appendMessage, not at the rendering further down.
function loadWebsocket() {
    const listeners = new Map();
    const logs = [];
    const appended = [];
    const sockets = [];
    const window = {
        appConst: {}, appUtils: {},
        addEventListener(type, listener) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(listener);
        },
        removeEventListener() {},
        dispatchEvent(event) { for (const listener of listeners.get(event.type) || []) listener(event); return true; },
        location: { protocol: 'http:', host: 'localhost:48911' },
        lanlan_config: { lanlan_name: 'Alpha' },
        t: key => key,
        appendMessage(text, sender) { appended.push({ text, sender }); return true; },
    };
    class FakeWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor(url) {
            this.url = url;
            this.readyState = 0;
            sockets.push(this);
        }
        send() {}
        close() { this.readyState = 3; }
        addEventListener() {}
        removeEventListener() {}
    }
    const quietConsole = {
        log(...args) { logs.push(args.map(String).join(' ')); },
        info() {}, warn() {}, error() {}, debug() {},
    };
    const context = {
        window, console: quietConsole, navigator: {}, WebSocket: FakeWebSocket, Blob,
        CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
        document: {
            getElementById: () => null,
            documentElement: { setAttribute() {} },
            querySelectorAll: () => [],
            querySelector: () => null,
            addEventListener() {},
            hidden: false,
        },
        fetch: () => new Promise(() => {}),
        // Timers never fire: the latch transitions under test are synchronous.
        setTimeout: () => 0,
        clearTimeout() {},
        setInterval: () => 0,
        clearInterval() {},
    };
    vm.runInNewContext(stateSource, context);
    vm.runInNewContext(websocketSource, context);
    const S = window.appState;

    function open(socket) {
        socket.readyState = 1;
        // No try/catch: an exception from onopen must fail the test rather
        // than hide a reset that never ran.
        socket.onopen();
    }
    function deliver(socket, payload) {
        socket.onmessage({ data: JSON.stringify(payload) });
    }
    function connect() {
        window.appWebSocket.connectWebSocket();
        const socket = sockets.at(-1);
        open(socket);
        return socket;
    }
    return {
        window, S, logs, appended, sockets, connect, open, deliver,
        discardCount: () => logs.filter(line => line.includes(DISCARD_LOG)).length,
    };
}

const chunk = text => ({ type: 'gemini_response', text, isNewMessage: true });

test('session_ended_by_server keeps dropping late chunks on the same socket', () => {
    const env = loadWebsocket();
    const socket = env.connect();
    assert.equal(env.S.suppressAssistantStreamUntilNextSession, false);

    env.deliver(socket, { type: 'session_ended_by_server', input_mode: 'text' });
    assert.equal(env.S.suppressAssistantStreamUntilNextSession, true);

    env.deliver(socket, chunk('late tail of the ended session'));
    assert.equal(env.discardCount(), 1);
    assert.deepEqual(env.appended, []);
    assert.equal(env.S.suppressAssistantStreamUntilNextSession, true);
});

test('reconnect clears the latch so a reused session greeting is shown', () => {
    const env = loadWebsocket();
    const oldSocket = env.connect();
    env.deliver(oldSocket, { type: 'session_ended_by_server', input_mode: 'text' });
    assert.equal(env.S.suppressAssistantStreamUntilNextSession, true);

    // Same character, new socket (auto-reconnect after the old one closed).
    oldSocket.readyState = 3;
    oldSocket.onclose();
    const newSocket = env.connect();
    assert.notEqual(newSocket, oldSocket);
    assert.equal(env.S.suppressAssistantStreamUntilNextSession, false);

    // The backend reuses its text session for the greeting: no session_started.
    env.deliver(newSocket, chunk('greeting on reused session'));
    assert.equal(env.discardCount(), 0);
    assert.deepEqual(env.appended.map(m => m.text), ['greeting on reused session']);
});

test('character switch clears the latch on the new socket, old socket tail still dropped', () => {
    const env = loadWebsocket();
    const oldSocket = env.connect();
    env.deliver(oldSocket, { type: 'session_ended_by_server', input_mode: 'text' });

    // Replays handleCatgirlSwitch's socket steps: close, retarget, flag greeting
    // switch, reconnect. test_session_control_contract.py pins that the real switch
    // still takes this path (a new socket, hence a new onopen).
    oldSocket.close();
    env.window.lanlan_config.lanlan_name = 'Beta';
    env.S._pendingGreetingSwitch = true;
    env.window.appWebSocket.connectWebSocket();
    const newSocket = env.sockets.at(-1);
    assert.notEqual(newSocket, oldSocket);

    // Until the new socket opens the latch still holds.
    assert.equal(env.S.suppressAssistantStreamUntilNextSession, true);
    env.open(newSocket);
    assert.equal(env.S.suppressAssistantStreamUntilNextSession, false);

    // A late frame from the old character's socket must not leak in.
    env.deliver(oldSocket, chunk('old character tail'));
    assert.deepEqual(env.appended, []);

    env.deliver(newSocket, chunk('new character greeting'));
    assert.deepEqual(env.appended.map(m => m.text), ['new character greeting']);
});

test('a stale onopen from a replaced socket does not clear the latch', () => {
    const env = loadWebsocket();
    env.connect();

    // A socket that gets replaced before it opens.
    env.window.lanlan_config.lanlan_name = 'Beta';
    env.window.appWebSocket.connectWebSocket();
    const replaced = env.sockets.at(-1);
    env.window.lanlan_config.lanlan_name = 'Gamma';
    env.window.appWebSocket.connectWebSocket();
    const latest = env.sockets.at(-1);
    assert.notEqual(replaced, latest);
    env.open(latest);

    env.deliver(latest, { type: 'session_ended_by_server', input_mode: 'text' });
    assert.equal(env.S.suppressAssistantStreamUntilNextSession, true);
    env.open(replaced);
    assert.equal(env.S.suppressAssistantStreamUntilNextSession, true);
});
