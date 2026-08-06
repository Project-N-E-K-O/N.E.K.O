const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const fileRoot = path.resolve(__dirname, '..', '..');
const root = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const source = fs.readFileSync(path.join(root, 'static/vrm/motion/bridge.js'), 'utf8');
const listeners = new Map();
const localMessages = [];
const broadcastMessages = [];

class CustomEventLike {
    constructor(type, options) {
        this.type = type;
        this.detail = options && options.detail;
    }
}

const windowLike = {
    lanlan_config: { lanlan_name: 'Yui' },
    appInterpage: {
        nekoBroadcastChannel: {
            postMessage(message) {
                broadcastMessages.push(message);
            }
        }
    },
    addEventListener(type, listener) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(listener);
    },
    dispatchEvent(event) {
        (listeners.get(event.type) || []).slice().forEach(function (listener) {
            listener(event);
        });
        return true;
    }
};

windowLike.addEventListener('neko:motion-lifecycle-relay', function (event) {
    localMessages.push(event.detail);
});

vm.runInNewContext(source, {
    window: windowLike,
    CustomEvent: CustomEventLike,
    Map,
    Object,
    String,
    Date,
    Math
}, { filename: 'bridge.js' });

function emit(type, detail) {
    windowLike.dispatchEvent(new CustomEventLike(type, { detail: detail || {} }));
}

function latest(eventName) {
    return localMessages.filter(function (message) {
        return message.eventName === eventName;
    }).at(-1);
}

emit('neko:user-content-sent', {
    requestId: 'request-1',
    text: '请鼓掌',
    source: 'text'
});
windowLike._lastSubmittedText = '';
emit('neko-assistant-turn-start', {
    turnId: 'turn-1',
    requestId: 'request-1',
    source: 'visible_gemini_bubble'
});
assert.equal(latest('neko-assistant-turn-start').detail.userText, '请鼓掌');
assert.equal(latest('neko-assistant-turn-start').detail.lanlan_name, 'Yui');

emit('neko:user-content-sent', {
    requestId: 'request-2',
    text: 'wave',
    source: 'text'
});
emit('neko-assistant-turn-start', {
    turnId: 'turn-other',
    requestId: 'request-other'
});
assert.equal(latest('neko-assistant-turn-start').detail.userText, undefined);
emit('neko-assistant-turn-start', {
    turnId: 'turn-2',
    requestId: 'request-2'
});
assert.equal(latest('neko-assistant-turn-start').detail.userText, 'wave');

emit('neko:user-voice-content-received', {
    text: '点头',
    source: 'voice'
});
emit('neko-assistant-turn-start', { turnId: 'voice-turn' });
assert.equal(latest('neko-assistant-turn-start').detail.userText, '点头');

emit('neko:user-content-sent', {
    requestId: 'cancelled-request',
    text: 'jump',
    source: 'text'
});
emit('neko:assistant-response-cancelled', { requestId: 'cancelled-request' });
emit('neko-assistant-turn-start', {
    turnId: 'cancelled-turn',
    requestId: 'cancelled-request'
});
assert.equal(latest('neko-assistant-turn-start').detail.userText, undefined);

windowLike._geminiTurnFullText = '(old wave)';
emit('neko-compact-caption-update', { text: '（鼓掌）' });
assert.equal(latest('neko-assistant-text-update').detail.text, '（鼓掌）');
const updateCount = localMessages.filter(function (message) {
    return message.eventName === 'neko-assistant-text-update';
}).length;
emit('neko-compact-caption-update', { text: '' });
assert.equal(localMessages.filter(function (message) {
    return message.eventName === 'neko-assistant-text-update';
}).length, updateCount);

windowLike._geminiTurnFullText = 'Okay（挥手）';
emit('neko-compact-caption-update');
assert.equal(latest('neko-assistant-text-update').detail.text, 'Okay（挥手）');

windowLike._turnIsStructured = true;
windowLike._geminiTurnFullText = 'structured response';
emit('neko-assistant-turn-end', {
    turnId: 'turn-2',
    requestId: 'request-2'
});
assert.equal(latest('neko-assistant-turn-end').detail.text, 'structured response');
assert.equal(latest('neko-assistant-turn-end').detail.structured, true);

emit('neko:user-content-sent', {
    requestId: 'stale-request',
    text: 'clap',
    source: 'text'
});
emit('neko:session-ended-by-server');
emit('neko-assistant-turn-start', {
    turnId: 'after-session',
    requestId: 'stale-request'
});
assert.equal(latest('neko-assistant-turn-start').detail.userText, undefined);

emit('neko-assistant-emotion-ready', { turnId: 'turn-3', emotion: 'happy' });
assert.equal(latest('neko-assistant-emotion-ready').detail.emotion, 'happy');
emit('neko-assistant-speech-cancel', { turnId: 'turn-3', source: 'user_activity' });
assert.equal(latest('neko-assistant-speech-cancel').detail.source, 'user_activity');

assert.equal(localMessages.length, broadcastMessages.length);
assert.equal(broadcastMessages.every(function (message) {
    return message.action === 'motion_lifecycle';
}), true);
assert.equal(source.includes('_lastSubmittedText'), false);
assert.equal(source.includes('_nekoMotionPendingUserText'), false);

console.log('VRM motion lifecycle bridge: OK');
