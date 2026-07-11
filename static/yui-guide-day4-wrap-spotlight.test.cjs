const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

function readStatic(relativePath) {
    return fs.readFileSync(path.join(__dirname, relativePath), 'utf8');
}

test('day4 wrap spotlight and cursor target the same chat capsule', () => {
    const day4Source = readStatic('tutorial/yui-guide/days/day4-companion-guide.js');
    const wrapScene = day4Source.match(/id:\s*'day4_wrap'[\s\S]*?petalTransition:\s*true/);

    assert.ok(wrapScene, 'day4_wrap scene should exist');
    assert.match(wrapScene[0], /command:\s*'spotlight\.show'[\s\S]*target:\s*'chat-capsule-input'/);
    assert.match(wrapScene[0], /target:\s*'chat-capsule-input'/);
    assert.match(wrapScene[0], /cursorTarget:\s*'chat-capsule-input'/);
    assert.doesNotMatch(wrapScene[0], /petalCueMode/);
    assert.doesNotMatch(wrapScene[0], /command:\s*'petal\.play'/);
    assert.doesNotMatch(wrapScene[0], /spotlightVariant:\s*'plain-capsule'/);
});
