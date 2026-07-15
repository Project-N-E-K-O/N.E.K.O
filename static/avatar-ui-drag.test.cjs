const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'avatar/avatar-ui-drag.js'), 'utf8');

test('chat mode config does not expose Tieba as a standalone proactive mode', () => {
    const configStart = source.indexOf('window.CHAT_MODE_CONFIG = [');
    assert.notEqual(configStart, -1, 'missing chat mode config');
    const configEnd = source.indexOf('];', configStart);
    assert.notEqual(configEnd, -1, 'missing chat mode config terminator');

    const configBlock = source.slice(configStart, configEnd);
    assert.doesNotMatch(configBlock, /mode:\s*'tieba'/);
    assert.doesNotMatch(configBlock, /proactiveTiebaChatEnabled/);
});
