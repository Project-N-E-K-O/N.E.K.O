const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'app-buttons.js'), 'utf8');

test('desktop screenshot lifecycle brackets capture and crop with an avatar-tool suspension session', () => {
    const start = source.indexOf('mod.captureScreenshotDataUrl = async function captureScreenshotDataUrl()');
    const end = source.indexOf('window.captureScreenshotDataUrl = mod.captureScreenshotDataUrl', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    const section = source.slice(start, end);

    assert.match(source, /new CustomEvent\('neko:screenshot-capture-session'/);
    assert.match(section, /if \(!U\.isMobile\(\)\) \{[\s\S]*?setScreenshotCaptureSessionActive\(true\);/);
    assert.match(section, /finally \{[\s\S]*?setScreenshotCaptureSessionActive\(false\);[\s\S]*?_captureScreenshotDataUrlBusy = false;/);
});
