const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../static/app/app-reply-tail.js'), 'utf8');
const geometry = fs.readFileSync(path.join(__dirname,
    '../../static/app/app-react-chat-window/geometry-and-messages.js'), 'utf8');
const sortStart = geometry.indexOf('    I.sortMessages = function sortMessages(messages)');
const sortEnd = geometry.indexOf('    I.buildRenderProps =', sortStart);
assert.ok(sortStart >= 0 && sortEnd > sortStart);
const hostInternals = {};
vm.runInNewContext(geometry.slice(sortStart, sortEnd), { I: hostInternals });

function harness() {
    const listeners = new Map();
    const frames = [];
    const messages = [];
    const visible = new Set();
    const captionVisible = new Set();
    const attachments = [];
    const window = {
        _realisticGeminiQueue: [],
        requestAnimationFrame(cb) { frames.push(cb); },
        addEventListener(type, cb) { listeners.set(type, cb); },
        reactChatWindowHost: { getState() { return { messages }; } },
        appendReactChatBlocks(payload, placement) {
            attachments.push({ payload, placement });
            messages.push({ role: 'system', ...placement });
            messages.splice(0, messages.length, ...hostInternals.sortMessages(messages));
            return true;
        },
    };
    const document = {
        querySelectorAll(selector) {
            const items = selector.includes('data-reply-tail-caption-turn-id')
                ? Array.from(captionVisible).map(id => ({ id }))
                : messages.filter(m => visible.has(m.id));
            return items.map(m => ({
                getAttribute() { return m.id; },
                getClientRects() { return [1]; },
                getAnimations() { return []; },
            }));
        },
    };
    vm.runInNewContext(source, { window, document, Map, Set, Date, Promise });
    function emit(type, detail) { listeners.get(type)?.({ detail }); }
    function flush() { frames.splice(0).forEach(cb => cb()); }
    function assistant(id, turnId, sortKey) {
        const message = { id, role: 'assistant', turnId, sortKey, createdAt: 123 };
        messages.push(message);
        visible.add(id);
        return message;
    }
    function start(turnId = 'turn-A', requestId = 'request-A') {
        emit('neko-assistant-turn-start', { turnId, requestId });
    }
    function end(turnId = 'turn-A', requestId = 'request-A') {
        emit('neko-assistant-turn-end', { turnId, requestId });
    }
    function enqueue(registration_id = 'image-A', request_id = 'request-A', call_id = 'call-A') {
        return window.nekoReplyTail.enqueue({
            blocks: [{ type: 'image', url: 'data:image/gif;base64,AAAA' }],
            reply_tail: { version: 1, reply_id: 'reply-A', request_id, registration_id, call_id },
        });
    }
    return { window, messages, attachments, visible, captionVisible, emit, flush, assistant, start, end, enqueue };
}

test('generation end waits for the last queued sentence and actual render', () => {
    const h = harness();
    h.start();
    h.assistant('first', 'turn-A', 1);
    h.window._realisticGeminiQueue.push({ turnId: 'turn-A', text: 'last' });
    h.end();
    h.enqueue();
    h.flush();
    assert.equal(h.attachments.length, 0);
    h.window._realisticGeminiQueue.length = 0;
    h.assistant('last', 'turn-A', 2);
    h.visible.delete('last');
    h.window.nekoReplyTail.changed();
    h.flush();
    assert.equal(h.attachments.length, 0);
    h.visible.add('last');
    h.emit('neko-reply-tail-presentation', { compact: false });
    h.flush();
    assert.equal(h.attachments.length, 1);
    assert.ok(h.attachments[0].placement.sortKey > 2);
});

test('compact caption waits for committed final visible character', () => {
    const h = harness();
    h.start();
    h.assistant('first', 'turn-A', 1);
    h.end();
    h.captionVisible.add('turn-A');
    h.emit('neko-reply-tail-presentation', { compact: true, turnId: 'turn-A', complete: false });
    h.enqueue();
    h.flush();
    assert.equal(h.attachments.length, 0);
    h.emit('neko-reply-tail-presentation', { compact: true, turnId: 'turn-A', complete: true });
    h.flush();
    assert.equal(h.attachments.length, 1);
});

test('late attachment stays before the next request and does not wait for its queue', () => {
    const h = harness();
    h.start();
    h.assistant('original', 'turn-A', 1);
    h.end();
    h.start('turn-B', 'request-B');
    h.messages.push({ id: 'user-B', role: 'user', sortKey: 2 });
    h.assistant('new', 'turn-B', 3);
    h.window._realisticGeminiQueue.push({ turnId: 'turn-B', text: 'more' });
    h.enqueue();
    h.flush();
    assert.equal(h.attachments.length, 1);
    assert.ok(h.attachments[0].placement.sortKey > 1);
    assert.ok(h.attachments[0].placement.sortKey < 2);
});

test('two attachments keep registration order and duplicates are no-ops', () => {
    const h = harness();
    h.start();
    h.assistant('original', 'turn-A', 1);
    h.end();
    h.messages.push({ id: 'next', role: 'user', sortKey: 2 });
    h.enqueue('first');
    h.enqueue('second');
    h.enqueue('first');
    h.flush();
    h.enqueue('first');
    h.flush();
    assert.equal(h.attachments.length, 2);
    assert.ok(h.attachments[0].placement.sortKey < h.attachments[1].placement.sortKey);
    assert.ok(h.attachments[1].placement.sortKey < 2);
});

test('missing original identity never borrows the latest turn', () => {
    const h = harness();
    h.start('turn-B', 'request-B');
    h.assistant('new', 'turn-B', 1);
    h.end('turn-B', 'request-B');
    h.enqueue();
    h.flush();
    assert.equal(h.attachments.length, 0);
});

test('hidden history waits until the original anchor is visible', () => {
    const h = harness();
    h.start();
    h.assistant('original', 'turn-A', 1);
    h.end();
    h.visible.clear();
    h.enqueue();
    h.flush();
    assert.equal(h.attachments.length, 0);
    h.visible.add('original');
    h.emit('neko-reply-tail-presentation', { compact: true, turnId: 'other', complete: false });
    h.flush();
    assert.equal(h.attachments.length, 1);
});

test('reconnect drops old pending attachments without replay', () => {
    const h = harness();
    h.start();
    h.assistant('original', 'turn-A', 1);
    h.enqueue();
    h.window.nekoReplyTail.reset();
    h.end();
    h.flush();
    assert.equal(h.attachments.length, 0);
});

test('visible completed caption releases attachment with history closed', () => {
    const h = harness();
    h.start();
    h.assistant('original', 'turn-A', 1);
    h.visible.clear();
    h.captionVisible.add('turn-A');
    h.end();
    h.emit('neko-reply-tail-presentation', { compact: true, turnId: 'turn-A', complete: false });
    h.enqueue();
    h.flush();
    assert.equal(h.attachments.length, 0);
    h.emit('neko-reply-tail-presentation', { compact: true, turnId: 'turn-A', complete: true });
    h.flush();
    assert.equal(h.attachments.length, 1);
});

test('separate calls may use the same local registration id', () => {
    const h = harness();
    h.start();
    h.assistant('original', 'turn-A', 1);
    h.end();
    h.enqueue('image-A', 'request-A', 'call-A');
    h.enqueue('image-A', 'request-A', 'call-B');
    h.flush();
    assert.equal(h.attachments.length, 2);
    assert.notEqual(h.attachments[0].placement.id, h.attachments[1].placement.id);
});

for (const count of [20, 64]) {
    for (const batchSize of [1, 7, count]) {
        test(`${count} timestamp-sized attachments in batches of ${batchSize} stay before the next turn`, () => {
            const h = harness();
            const base = 1791676800000;
            h.start();
            h.assistant('original', 'turn-A', base);
            h.end();
            h.messages.push({ id: 'user-next', role: 'user', sortKey: base + 1 });
            h.assistant('assistant-next', 'turn-B', base + 2);
            for (let index = 0; index < count; index++) {
                const id = `image-${String(index).padStart(2, '0')}`;
                h.enqueue(id);
                h.enqueue(id);
                if ((index + 1) % batchSize === 0) h.flush();
            }
            h.flush();
            assert.equal(h.attachments.length, count);
            assert.deepEqual(h.messages.map(message => message.id), [
                'original',
                ...h.attachments.map(attachment => attachment.placement.id),
                'user-next',
                'assistant-next',
            ]);
            let previous = base;
            for (const attachment of h.attachments) {
                assert.ok(attachment.placement.sortKey > previous);
                assert.ok(attachment.placement.sortKey < base + 1);
                previous = attachment.placement.sortKey;
            }
        });
    }
}

test('attachments keep ordinary image and card positions unchanged', () => {
    const h = harness();
    const base = 1791676800000;
    h.start();
    h.assistant('original', 'turn-A', base);
    h.end();
    h.messages.push(
        { id: 'ordinary-image', role: 'system', sortKey: base + 1 },
        { id: 'html-card', role: 'system', sortKey: base + 2 },
        { id: 'user-next', role: 'user', sortKey: base + 3 },
    );
    for (let index = 0; index < 64; index++) {
        h.enqueue(`image-${index}`);
        h.flush();
    }
    assert.equal(h.attachments.length, 64);
    assert.deepEqual(h.messages.slice(-3).map(message => [message.id, message.sortKey]), [
        ['ordinary-image', base + 1], ['html-card', base + 2], ['user-next', base + 3],
    ]);
});

test('an unrepresentable insertion never crosses the next message', () => {
    const h = harness();
    const base = 1791676800000;
    h.start();
    h.assistant('original', 'turn-A', base);
    h.end();
    h.messages.push({ id: 'user-next', role: 'user', sortKey: base + 0.000244140625 });
    h.enqueue();
    h.flush();
    assert.equal(h.attachments.length, 0);
    assert.equal(h.messages.length, 2);
});
