const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const css = fs.readFileSync(path.join(__dirname, 'css', 'index.css'), 'utf8');

test('compact history remains hit-testable while the compact tool wheel is open', () => {
    const compactToolOpenGuard = ':not([data-compact-export-controls-open="true"]):not([data-compact-export-preview-open="true"])';

    assert.doesNotMatch(
        css,
        new RegExp(`${compactToolOpenGuard.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s+\\.compact-export-history-anchor\\s+\\*\\s*\\{\\s*pointer-events:\\s*none\\s*!important;`),
        'the tool-wheel-open history override must not disable the whole history subtree'
    );

    const scrollSelector = `${compactToolOpenGuard} .compact-export-history-scroll`;
    const bubbleSelector = `${compactToolOpenGuard} .compact-export-history-bubble`;
    const autoPointerBlockPattern = new RegExp(
        `${scrollSelector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[\\s\\S]*${bubbleSelector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[\\s\\S]*\\{\\s*pointer-events:\\s*auto\\s*!important;\\s*\\}`
    );

    assert.match(
        css,
        autoPointerBlockPattern,
        'history scroll and bubbles must stay pointer-events:auto so Electron does not make them click-through'
    );
});
