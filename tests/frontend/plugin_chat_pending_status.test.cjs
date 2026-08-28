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
        'var _PENDING_HOST_ASSISTANT_MAX = 50;',
        'var _PENDING_HOST_PLUGIN_MAX = 20;',
        'var _PENDING_HOST_MESSAGES_MAX = 70;',
        'function getHost() { return host; }',
        'function clearInterval() {}',
        sourceBetween('function _isPluginPendingMessage(', 'function _tryFlushPendingHostMessages('),
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

// --- structured block validation -----------------------------------------

function loadValidator() {
    const context = vm.createContext({});
    vm.runInContext(sourceBetween('var STRUCTURED_BLOCK_TYPES', 'function createVirtualBubbleRef'), context);
    return context;
}

test('unknown and malformed block types never reach the structured branch', () => {
    const ctx = loadValidator();
    const kept = vm.runInContext(`structuredBlocksFrom([
        { type: 'text', text: 'ok' },
        { type: '' },
        { type: 'bogus' },
        { type: 'image' },
        { type: 'image', url: '   ' },
        { type: 'image', url: 'data:image/png;base64,AAA' },
        null,
        'not-an-object'
    ]).map(function (b) { return b.type + ':' + (b.url || b.text || ''); }).join('|')`, ctx);

    // Joined inside the vm: an array built in that realm has a different
    // prototype, so deepEqual compares as non-equal even when the contents match.
    //
    // Only the two well-formed blocks survive. An empty or unknown type used to
    // pass, routing the message down the structured branch to render nothing.
    assert.equal(kept, 'text:ok|image:data:image/png;base64,AAA');
});

test('a text block with a non-string body is rejected', () => {
    const ctx = loadValidator();
    const kept = vm.runInContext(
        "structuredBlocksFrom([{ type: 'text', text: 42 }]).length", ctx);
    assert.equal(kept, 0);
});

test('blocks are copied, not aliased into the caller payload', () => {
    const ctx = loadValidator();
    const same = vm.runInContext(`(function () {
        var src = [{ type: 'text', text: 'x' }];
        var out = structuredBlocksFrom(src);
        return out[0] === src[0];
    })()`, ctx);
    assert.equal(same, false, 'mutating a rendered block must not reach the sender');
});

test('a queued plugin post does not credit the dialogue achievement', () => {
    const credited = [];
    const host = { appendMessage() {}, updateMessage() {} };
    const context = vm.createContext({ host, console: { warn() {}, log() {} } });
    vm.runInContext([
        'var _pendingHostMessages = [];',
        'var _pendingFlushTimer = null;',
        'var _PENDING_HOST_ASSISTANT_MAX = 50;',
        'var _PENDING_HOST_PLUGIN_MAX = 20;',
        'var _PENDING_HOST_MESSAGES_MAX = 70;',
        'var credited = [];',
        'function getHost() { return host; }',
        'function clearInterval() {}',
        'function appendHostMessageSafely(h, m) { return true; }',
        'function markAssistantVisibleResponseForAchievement() { credited.push(1); }',
        sourceBetween('function _isPluginPendingMessage(', 'function _tryFlushPendingHostMessages('),
        sourceBetween('function _tryFlushPendingHostMessages(', '// 供 response_discarded'),
    ].join('\n'), context);

    vm.runInContext(
        "_queuePendingHostMessage({ id: 'a', role: 'system' });"
        + "_queuePendingHostMessage({ id: 'b', role: 'assistant' });"
        + "_tryFlushPendingHostMessages();",
        context,
    );

    // Only the assistant message counts. The direct path already declines to
    // credit plugin posts; the queued path used to credit everything it flushed.
    assert.equal(vm.runInContext('credited.length', context), 1);
});


test('a burst of plugin posts never evicts a waiting assistant message', () => {
    // The React host has not mounted, so everything queues. Under one shared
    // cap, evicting the oldest let plugin pushes delete assistant output the
    // user would then never see.
    const ctx = createAdapter(null);

    ctx.assistant = { id: 'assistant-1', role: 'assistant', text: 'important' };
    vm.runInContext('_queuePendingHostMessage(assistant);', ctx);

    ctx.burst = Array.from({ length: 500 }, (_, i) => ({
        id: 'plugin-' + i, role: 'system', author: 'plugin', text: 'noise',
    }));
    vm.runInContext('burst.forEach(function (m) { _queuePendingHostMessage(m); });', ctx);

    const queued = vm.runInContext('_pendingHostMessages', ctx);
    assert.equal(
        queued.filter((m) => m.id === 'assistant-1').length, 1,
        'the assistant message was evicted by plugin posts',
    );

    // The plugin side stayed inside its own budget and trimmed its OWN oldest.
    const plugins = queued.filter((m) => m.role === 'system');
    assert.equal(plugins.length, 20);
    assert.equal(plugins[plugins.length - 1].id, 'plugin-499');
});

test('a burst of assistant messages never evicts waiting plugin posts', () => {
    // The dual. A quota protecting only one direction is not isolation.
    const ctx = createAdapter(null);

    ctx.pluginPost = { id: 'plugin-keep', role: 'system', author: 'plugin' };
    vm.runInContext('_queuePendingHostMessage(pluginPost);', ctx);

    ctx.burst = Array.from({ length: 500 }, (_, i) => ({
        id: 'assistant-' + i, role: 'assistant',
    }));
    vm.runInContext('burst.forEach(function (m) { _queuePendingHostMessage(m); });', ctx);

    const queued = vm.runInContext('_pendingHostMessages', ctx);
    assert.equal(queued.filter((m) => m.id === 'plugin-keep').length, 1);
    assert.equal(queued.filter((m) => m.role === 'assistant').length, 50);
});

test('arrival order survives the per-source trimming', () => {
    const ctx = createAdapter(null);
    ctx.mixed = [
        { id: 'a1', role: 'assistant' },
        { id: 'p1', role: 'system', author: 'plugin' },
        { id: 'a2', role: 'assistant' },
        { id: 'p2', role: 'system', author: 'plugin' },
    ];
    vm.runInContext('mixed.forEach(function (m) { _queuePendingHostMessage(m); });', ctx);

    // Joined rather than deepEqual: the array comes from the vm realm, so it
    // is structurally identical but not reference-equal to a host-realm Array
    // and assert/strict rejects it.
    assert.equal(
        vm.runInContext('_pendingHostMessages', ctx).map((m) => m.id).join('|'),
        'a1|p1|a2|p2',
    );
});
