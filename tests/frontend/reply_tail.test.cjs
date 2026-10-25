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
    const timers = new Map();
    const warnings = [];
    let now = 1791676800000;
    let timerId = 0;
    const window = {
        _realisticGeminiQueue: [],
        requestAnimationFrame(cb) { frames.push(cb); },
        setTimeout(cb, delay) {
            const id = ++timerId;
            timers.set(id, { cb, due: now + delay });
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
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
    vm.runInNewContext(source, {
        window, document, Map, Set, Date: { now: () => now }, Promise,
        console: { warn(...args) { warnings.push(args); } },
    });
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
    function enqueue(registration_id = 'image-A', request_id = 'request-A', call_id = 'call-A',
            invocation_id = undefined) {
        return window.nekoReplyTail.enqueue({
            blocks: [{ type: 'image', url: 'data:image/gif;base64,AAAA' }],
            reply_tail: {
                version: 1, reply_id: 'reply-A', request_id, registration_id, call_id,
                ...(invocation_id ? { invocation_id } : {}),
            },
        });
    }
    function advance(ms) {
        now += ms;
        for (const [id, timer] of Array.from(timers)) {
            if (timer.due <= now && timers.delete(id)) timer.cb();
        }
    }
    return { window, messages, attachments, visible, captionVisible, emit, flush, assistant,
        start, end, enqueue, advance, timers, warnings };
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

test('invocations repeating a provider call id stay distinct, each still deduplicated', () => {
    const h = harness();
    h.start();
    h.assistant('original', 'turn-A', 1);
    h.end();
    h.enqueue('image-A', 'request-A', 'call_0', 'invocation-1');
    h.enqueue('image-A', 'request-A', 'call_0', 'invocation-2');
    h.enqueue('image-A', 'request-A', 'call_0', 'invocation-1');
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

test('unplaceable images expire without chat events or animation frames, freeing capacity', () => {
    const h = harness();
    const base = 1791676800000;
    h.start();
    h.assistant('original', 'turn-A', base);
    h.end();
    h.messages.push({ id: 'next', role: 'user', sortKey: base + 0.000244140625 });
    for (let index = 0; index < 128; index++) assert.equal(h.enqueue(`image-${index}`), true);
    assert.equal(h.enqueue('overflow'), false);
    h.flush();
    h.advance(119999);
    assert.equal(h.warnings.length, 0);
    assert.equal(h.enqueue('overflow'), false);
    h.advance(2);
    assert.equal(h.warnings.length, 128);
    assert.equal(h.timers.size, 0);
    assert.equal(h.attachments.length, 0);
    assert.equal(h.enqueue('overflow'), true);
});

for (const reason of ['hidden-history', 'missing-host']) {
    test(`${reason} pending images expire without later rendering`, () => {
        const h = harness();
        h.start();
        h.assistant('original', 'turn-A', 1);
        h.end();
        h.visible.clear();
        if (reason === 'missing-host') h.window.reactChatWindowHost = null;
        h.enqueue();
        h.flush();
        h.advance(120001);
        assert.equal(h.warnings.length, 1);
        assert.equal(h.timers.size, 0);
        h.visible.add('original');
        h.window.reactChatWindowHost = { getState: () => ({ messages: h.messages }) };
        h.window.nekoReplyTail.changed();
        h.flush();
        assert.equal(h.attachments.length, 0);
    });
}

test('successful display and reconnect clear timers; duplicate packets do not add timers', () => {
    const h = harness();
    h.start();
    h.assistant('original', 'turn-A', 1);
    h.enqueue('first');
    h.enqueue('first');
    assert.equal(h.timers.size, 1);
    h.end();
    h.flush();
    assert.equal(h.attachments.length, 1);
    assert.equal(h.timers.size, 0);
    h.visible.clear();
    h.enqueue('second');
    assert.equal(h.timers.size, 1);
    h.window.nekoReplyTail.reset();
    assert.equal(h.timers.size, 0);
    h.advance(120001);
    assert.equal(h.warnings.length, 0);
});

test('removing a blocking next message still allows a pending image before expiry', () => {
    const h = harness();
    h.start();
    h.assistant('original', 'turn-A', 1);
    h.end();
    h.enqueue('first');
    h.flush();
    const firstKey = h.attachments[0].placement.sortKey;
    h.messages.push({ id: 'blocker', role: 'system', sortKey: firstKey + 0.0001 });
    h.enqueue('second');
    h.flush();
    assert.equal(h.attachments.length, 1);
    assert.equal(h.timers.size, 1);
    h.advance(30000);
    h.messages.splice(h.messages.findIndex(message => message.id === 'blocker'), 1);
    h.window.nekoReplyTail.changed();
    h.flush();
    assert.equal(h.attachments.length, 2);
    assert.equal(h.timers.size, 0);
    assert.ok(h.attachments[1].placement.sortKey > firstKey);
});

test('a stale timer after reset cannot expire a new registration with the same identity', () => {
    const h = harness();
    h.enqueue();
    const oldCallback = Array.from(h.timers.values())[0].cb;
    h.window.nekoReplyTail.reset();
    h.advance(60000);
    h.enqueue();
    h.advance(60001);
    oldCallback();
    assert.equal(h.warnings.length, 0);
    assert.equal(h.timers.size, 1);
    h.start();
    h.assistant('original', 'turn-A', 1);
    h.end();
    h.flush();
    assert.equal(h.attachments.length, 1);
});
