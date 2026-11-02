'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');

test('information opens once and closes on pagehide without requests or persistent state', () => {
    const listeners = [];
    let opens = 0, closes = 0;
    const dialog = { showModal() { opens++; }, close() { closes++; } };
    vm.runInNewContext(fs.readFileSync(path.join(root, 'static/feedback-information.js'), 'utf8'), {
        document: { getElementById(id) { assert.equal(id, 'feedback-information'); return dialog; } },
        window: { addEventListener(...args) { listeners.push(args); } },
        fetch() { assert.fail('reading information cannot send any request'); },
    }, { filename: path.join(root, 'static/feedback-information.js') });
    assert.equal(opens, 1);
    assert.equal(closes, 0);
    assert.equal(listeners.length, 1);
    assert.equal(listeners[0][0], 'pagehide');
    assert.equal(listeners[0][2].once, true);
    listeners[0][1]();
    assert.equal(closes, 1);
    assert.equal(opens, 1, 'returning from BFCache does not reopen the information');
});

test('native dialog has a descriptive accessible name and a non-network close form', () => {
    const html = fs.readFileSync(path.join(root, 'templates/feedback_devices.html'), 'utf8');
    assert.match(html, /<dialog\b[^>]*id="feedback-information"[^>]*aria-labelledby="feedback-information-title"/);
    assert.match(html, /aria-describedby="feedback-information-data feedback-information-excluded feedback-information-note"/);
    assert.match(html, /<form method="dialog"><button type="submit"[^>]*id="feedback-information-close"[^>]*autofocus/);
    assert.match(html, /\/static\/feedback-information\.js\?v=/);
    assert.doesNotMatch(html, /disclosure-open|feedback-disclosure/);
});
