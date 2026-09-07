const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/app/app-chat-adapter.js'), 'utf8');
const fn = source.slice(source.indexOf('    function appendReactHtmlCard('), source.indexOf('    function appendReactChatBlocks('));

function setup(mounted = true) {
    const messages = [];
    const host = {
        getState: () => ({ messages }),
        appendMessage: message => messages.push(message),
        updateMessage: (id, patch) => Object.assign(messages.find(m => m.id === id), patch),
    };
    const ctx = vm.createContext({
        _pendingHostMessages: [], getHost: () => mounted ? host : null,
        getCurrentTimeString: () => '10:00',
        appendHostMessageSafely: (h, message) => { h.appendMessage(message); return true; },
    });
    vm.runInContext(`function _queuePendingHostMessage(m) { _pendingHostMessages.push(m); }
        function _tryFlushPendingHostMessages() {}\n${fn}`, ctx);
    return { ctx, messages, push: block => ctx.appendReactHtmlCard(block) };
}
const initial = { type: 'html_card', cardId: 'one', pluginId: 'demo', targetLanlan: 'Alice',
    operation: 'create', html: '<button>Go</button>', css: 'button{color:red}', summary: 'Go',
    actions: { go: { entry: 'play', args: { id: 1 } } } };

test('partial updates preserve omitted fields and keep one stable message', () => {
    const { messages, push } = setup();
    push(initial);
    const id = messages[0].id;
    push({ type: 'html_card', cardId: 'one', pluginId: 'demo', targetLanlan: 'Alice', operation: 'update', summary: 'Updated' });
    assert.equal(messages.length, 1);
    assert.equal(messages[0].id, id);
    assert.equal(messages[0].blocks[0].css, initial.css);
    assert.equal(messages[0].blocks[0].actions.go.entry, 'play');
    push({ ...initial, operation: 'update', actions: {} });
    assert.equal(Object.keys(messages[0].blocks[0].actions).length, 0);
});

test('update arriving before the React host patches the pending creation', () => {
    const { ctx, push } = setup(false);
    push(initial);
    push({ ...initial, operation: 'update', html: 'Done' });
    assert.equal(ctx._pendingHostMessages.length, 1);
    assert.equal(ctx._pendingHostMessages[0].blocks[0].html, 'Done');
});

test('plugin identity scopes IDs and missing updates do not resurrect old cards', () => {
    const { messages, push } = setup();
    assert.equal(push({ ...initial, operation: 'update' }), false);
    push(initial);
    push({ ...initial, pluginId: 'other' });
    assert.equal(messages.length, 2);
    assert.notEqual(messages[0].id, messages[1].id);
});
