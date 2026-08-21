const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// The pytest wrapper feeds this file's CONTENT to node from a temp path, so
// __dirname is not the repo. Same fallback the vrm frontend tests use.
const fileRoot = path.resolve(__dirname, '..', '..');
const PROJECT_ROOT = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const SOURCE_PATH = path.join(PROJECT_ROOT, 'static', 'app', 'app-chat-adapter.js');
const source = fs.readFileSync(SOURCE_PATH, 'utf8');

function sourceBetween(startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    assert.notEqual(start, -1, `missing start marker: ${startMarker}`);
    const end = source.indexOf(endMarker, start + startMarker.length);
    assert.notEqual(end, -1, `missing end marker: ${endMarker}`);
    return source.slice(start, end);
}

// Run the REAL functions rather than asserting on their text: this is an
// ordering bug (turn end lands before the host mounts), and a static check
// would pass against a version that silently drops the update again.
function createAdapter(host) {
    const context = vm.createContext({ host, console: { warn() {}, log() {} } });
    const script = [
        'var _pendingHostMessages = [];',
        'var _pendingFlushTimer = null;',
        'var _PENDING_HOST_MESSAGES_MAX = 50;',
        'function getHost() { return host; }',
        'function clearInterval() {}',
        sourceBetween('function _queuePendingHostMessage(', 'function _tryFlushPendingHostMessages('),
        sourceBetween('function _patchPendingHostMessage(', 'function _resetReactChatSwitchState('),
        sourceBetween('function setReactMessageStatus(', '// ======================== appendReactUserMessage'),
        'function virtualRef(id) { return { dataset: { reactChatMessageId: id } }; }',
    ].join('\n');
    vm.runInContext(script, context);
    return context;
}

test('turn end reaches a message still waiting for the React host', () => {
    const ctx = createAdapter(null); // host not mounted yet
    vm.runInContext(
        "_queuePendingHostMessage({ id: 'plug-1', role: 'assistant', blocks: [{type:'image'}], status: 'streaming' });",
        ctx,
    );

    vm.runInContext("setReactMessageStatus(virtualRef('plug-1'), 'assistant', 'sent');", ctx);

    const queued = vm.runInContext('_pendingHostMessages[0]', ctx);
    assert.equal(
        queued.status,
        'sent',
        'a structured passthrough queued before the host mounts must not flush as a permanently streaming bubble',
    );
});

test('an unrelated queued message is left alone', () => {
    const ctx = createAdapter(null);
    vm.runInContext(
        "_queuePendingHostMessage({ id: 'other', status: 'streaming' });"
        + "_queuePendingHostMessage({ id: 'plug-1', status: 'streaming' });",
        ctx,
    );

    vm.runInContext("setReactMessageStatus(virtualRef('plug-1'), 'assistant', 'sent');", ctx);

    assert.equal(vm.runInContext("_pendingHostMessages[0].status", ctx), 'streaming');
    assert.equal(vm.runInContext("_pendingHostMessages[1].status", ctx), 'sent');
});

test('the mounted-host path still goes through updateMessage', () => {
    const calls = [];
    const host = {
        appendMessage() {},
        updateMessage(id, patch) { calls.push([id, patch]); },
    };
    const ctx = createAdapter(host);
    vm.runInContext("_queuePendingHostMessage({ id: 'plug-1', status: 'streaming' });", ctx);

    vm.runInContext("setReactMessageStatus(virtualRef('plug-1'), 'assistant', 'sent');", ctx);

    // Field-wise: the patch object is created inside the vm realm, so its
    // prototype differs and deepEqual would fail on reference equality alone.
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], 'plug-1');
    assert.equal(calls[0][1].status, 'sent');
    // The host owns the message once mounted; do not also rewrite the queue.
    assert.equal(vm.runInContext("_pendingHostMessages[0].status", ctx), 'streaming');
});

test('a status update for an unknown id is a no-op, not a throw', () => {
    const ctx = createAdapter(null);
    vm.runInContext("setReactMessageStatus(virtualRef('missing'), 'assistant', 'sent');", ctx);
    assert.equal(vm.runInContext('_pendingHostMessages.length', ctx), 0);
});
