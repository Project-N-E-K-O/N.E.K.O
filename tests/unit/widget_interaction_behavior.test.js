const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const controllerPath = path.join(projectRoot, 'static', 'app', 'app-widget-interaction.js');

function createHarness(options = {}) {
    let now = 1000;
    let timerSequence = 0;
    const timers = new Map();
    const listeners = new Map();
    const lifecycle = [];
    const broadcastMessages = [];
    const electronMessages = [];
    let broadcastListener = null;

    function setFakeTimeout(callback, delay) {
        const id = ++timerSequence;
        timers.set(id, { callback, at: now + Math.max(0, Number(delay) || 0) });
        return id;
    }

    function clearFakeTimeout(id) {
        timers.delete(id);
    }

    function advance(milliseconds) {
        const target = now + milliseconds;
        while (true) {
            const next = [...timers.entries()]
                .filter(([, timer]) => timer.at <= target)
                .sort((left, right) => left[1].at - right[1].at || left[0] - right[0])[0];
            if (!next) break;
            now = next[1].at;
            timers.delete(next[0]);
            next[1].callback();
        }
        now = target;
    }

    const window = {
        addEventListener(type, handler) {
            const handlers = listeners.get(type) || [];
            handlers.push(handler);
            listeners.set(type, handlers);
        },
        dispatchEvent(event) {
            if (event.type.startsWith('neko:widget-interaction-')) {
                lifecycle.push({ type: event.type, detail: event.detail });
            }
            const handlers = listeners.get(event.type) || [];
            for (const handler of handlers) handler(event);
            return true;
        }
    };
    if (options.electronBridge) {
        window.nekoElectronWidgetInteraction = {
            send(message) {
                electronMessages.push(JSON.parse(JSON.stringify(message)));
            }
        };
    }
    function FakeBroadcastChannel() {
        this.addEventListener = (type, handler) => {
            if (type === 'message') broadcastListener = handler;
        };
        this.postMessage = (message) => {
            broadcastMessages.push(JSON.parse(JSON.stringify(message)));
        };
        this.close = () => {};
    }
    const context = {
        window,
        console,
        setTimeout: setFakeTimeout,
        clearTimeout: clearFakeTimeout,
        Date: { now: () => now },
        Math,
        CustomEvent: function CustomEvent(type, init = {}) {
            this.type = type;
            this.detail = init.detail;
        }
    };
    if (options.broadcastChannel) {
        context.BroadcastChannel = FakeBroadcastChannel;
    }
    context.globalThis = context;
    vm.runInNewContext(fs.readFileSync(controllerPath, 'utf8'), context, {
        filename: controllerPath
    });

    return {
        window,
        lifecycle,
        broadcastMessages,
        electronMessages,
        advance,
        dispatch(type, detail = {}) {
            window.dispatchEvent({ type, detail });
        },
        state() {
            return JSON.parse(JSON.stringify(window.NekoWidgetInteraction.getState()));
        },
        receiveBroadcast(message) {
            assert.equal(typeof broadcastListener, 'function');
            broadcastListener({ data: message });
        },
        receiveElectron(message) {
            window.dispatchEvent({
                type: 'neko:electron-widget-interaction',
                detail: message
            });
        }
    };
}

test('full chat user interaction reaches Pet through the Electron-only transport', () => {
    const chat = createHarness({ electronBridge: true });
    const pet = createHarness();

    chat.dispatch('neko:user-content-sent', {
        requestId: 'request-full-chat',
        source: 'text'
    });

    assert.equal(chat.electronMessages.length, 1);
    pet.receiveElectron(chat.electronMessages[0]);
    assert.equal(pet.window.NekoWidgetInteraction.isActive(), true);
    assert.equal(pet.state().requestId, 'request-full-chat');
});

test('BroadcastChannel and Electron delivery of the same message are deduplicated', () => {
    const sender = createHarness({ broadcastChannel: true, electronBridge: true });
    const pet = createHarness({ broadcastChannel: true });

    sender.dispatch('neko:user-content-sent', {
        requestId: 'request-dual-transport',
        source: 'text'
    });
    const message = sender.broadcastMessages[0];
    assert.deepEqual(message, sender.electronMessages[0]);

    pet.receiveBroadcast(message);
    pet.receiveElectron(message);

    const starts = pet.lifecycle.filter((entry) =>
        entry.type === 'neko:widget-interaction-start'
    );
    const extensions = pet.lifecycle.filter((entry) =>
        entry.type === 'neko:widget-interaction-extend'
    );
    assert.equal(starts.length, 1);
    assert.equal(extensions.length, 0);
});

test('ordinary browser builds continue to publish only through BroadcastChannel', () => {
    const harness = createHarness({ broadcastChannel: true });

    harness.dispatch('neko:user-content-sent', {
        requestId: 'request-browser',
        source: 'text'
    });

    assert.equal(harness.broadcastMessages.length, 1);
    assert.equal(harness.electronMessages.length, 0);
});

test('proactive assistant output cannot start an interaction lease', () => {
    const harness = createHarness();

    for (const source of ['agent', 'game-event', 'plugin-tool', 'system', 'proactive-message']) {
        harness.dispatch('neko:user-content-sent', {
            requestId: `request-${source}`,
            source
        });
    }
    harness.dispatch('neko-assistant-turn-start', {
        turnId: 'proactive-turn',
        source: 'proactive'
    });
    harness.dispatch('neko-assistant-speech-start', {
        turnId: 'proactive-turn'
    });

    assert.equal(harness.window.NekoWidgetInteraction.isActive(), false);
    assert.equal(harness.state().phase, 'idle');
});

test('matching text reply ends only after the one-second settle delay', () => {
    const harness = createHarness();

    harness.dispatch('neko:user-content-sent', {
        requestId: 'request-1',
        source: 'text'
    });
    assert.deepEqual(harness.state(), {
        active: true,
        phase: 'waiting',
        leaseId: 'request-1',
        requestId: 'request-1',
        turnId: '',
        source: 'text',
        reason: 'snapshot',
        timestamp: 1000
    });

    harness.dispatch('neko-assistant-turn-start', {
        requestId: 'other-request',
        turnId: 'wrong-turn'
    });
    assert.equal(harness.state().phase, 'waiting');

    harness.dispatch('neko-assistant-turn-start', {
        requestId: 'request-1',
        turnId: 'turn-1'
    });
    assert.equal(harness.state().phase, 'reply');

    harness.dispatch('neko-assistant-turn-end', {
        requestId: 'request-1',
        turnId: 'turn-1'
    });
    assert.equal(harness.state().phase, 'settling');
    harness.advance(999);
    assert.equal(harness.window.NekoWidgetInteraction.isActive(), true);
    harness.advance(1);
    assert.equal(harness.window.NekoWidgetInteraction.isActive(), false);
    assert.equal(harness.state().phase, 'idle');
});

test('request-owned lease binds the first requestless assistant turn exactly once', () => {
    const harness = createHarness();

    harness.dispatch('neko:avatar-interaction-sent', {
        requestId: 'avatar-interaction-1',
        interactionId: 'avatar-interaction-1',
        source: 'avatar-tool'
    });
    harness.dispatch('neko-assistant-turn-start', { turnId: 'avatar-turn-1' });

    assert.equal(harness.state().phase, 'reply');
    assert.equal(harness.state().requestId, 'avatar-interaction-1');
    assert.equal(harness.state().turnId, 'avatar-turn-1');

    harness.dispatch('neko-assistant-turn-start', { turnId: 'unrelated-turn' });
    assert.equal(harness.state().turnId, 'avatar-turn-1');

    harness.dispatch('neko-assistant-turn-end', { turnId: 'avatar-turn-1' });
    harness.advance(1000);
    assert.equal(harness.window.NekoWidgetInteraction.isActive(), false);
});

test('speech playback keeps the matching interaction active until actual playback ends', () => {
    const harness = createHarness();

    harness.dispatch('neko:user-content-sent', { requestId: 'request-2' });
    harness.dispatch('neko-assistant-turn-start', {
        requestId: 'request-2',
        turnId: 'turn-2'
    });
    harness.dispatch('neko-assistant-speech-start', { turnId: 'turn-2' });
    harness.dispatch('neko-assistant-turn-end', {
        requestId: 'request-2',
        turnId: 'turn-2'
    });

    harness.advance(5000);
    assert.equal(harness.window.NekoWidgetInteraction.isActive(), true);
    assert.equal(harness.state().phase, 'speaking');

    harness.dispatch('neko-assistant-speech-end', { turnId: 'turn-2' });
    assert.equal(harness.state().phase, 'settling');
    harness.advance(1000);
    assert.equal(harness.window.NekoWidgetInteraction.isActive(), false);
});

test('continuous messages replace the lease and ignore completion from the older request', () => {
    const harness = createHarness();

    harness.dispatch('neko:user-content-sent', { requestId: 'request-old' });
    harness.dispatch('neko:user-content-sent', { requestId: 'request-new' });
    assert.equal(harness.state().requestId, 'request-new');

    harness.dispatch('neko-assistant-turn-start', {
        requestId: 'request-old',
        turnId: 'turn-old'
    });
    harness.dispatch('neko-assistant-turn-end', {
        requestId: 'request-old',
        turnId: 'turn-old'
    });
    harness.advance(2000);

    assert.equal(harness.window.NekoWidgetInteraction.isActive(), true);
    assert.equal(harness.state().phase, 'waiting');
});

test('request-scoped cancellation cannot clear a newer interaction lease', () => {
    const harness = createHarness();

    harness.dispatch('neko:user-content-sent', { requestId: 'request-old' });
    harness.dispatch('neko:user-content-sent', { requestId: 'request-new' });
    harness.dispatch('neko:assistant-response-cancelled', {
        reason: 'response-discarded',
        requestId: 'request-old'
    });

    assert.equal(harness.window.NekoWidgetInteraction.isActive(), true);
    assert.equal(harness.state().requestId, 'request-new');

    harness.dispatch('neko:assistant-response-cancelled', {
        reason: 'response-discarded',
        requestId: 'request-new'
    });
    assert.equal(harness.window.NekoWidgetInteraction.isActive(), false);
});

test('non-empty voice transcript accepts the next requestless assistant turn', () => {
    const harness = createHarness();

    harness.dispatch('neko:user-voice-content-received', { source: 'voice' });
    harness.dispatch('neko-assistant-turn-start', { turnId: 'voice-turn' });
    assert.equal(harness.state().phase, 'reply');
    assert.equal(harness.state().turnId, 'voice-turn');

    harness.dispatch('neko-assistant-turn-end', { turnId: 'voice-turn' });
    harness.advance(1000);
    assert.equal(harness.window.NekoWidgetInteraction.isActive(), false);
});

test('disconnect and feature disable cancel immediately', () => {
    const harness = createHarness();

    harness.dispatch('neko:user-content-sent', { requestId: 'request-3' });
    harness.dispatch('neko:websocket-disconnected');
    assert.equal(harness.window.NekoWidgetInteraction.isActive(), false);

    harness.dispatch('neko:user-content-sent', { requestId: 'request-4' });
    harness.dispatch('neko:assistant-response-cancelled', { reason: 'user-cancel' });
    assert.equal(harness.window.NekoWidgetInteraction.isActive(), false);

    harness.dispatch('neko:user-content-sent', { requestId: 'request-5' });
    harness.dispatch('neko:widget-mode-state-changed', { enabled: false });
    assert.equal(harness.window.NekoWidgetInteraction.isActive(), false);
    assert.ok(harness.lifecycle.some((entry) =>
        entry.type === 'neko:widget-interaction-cancel'
    ));
});
