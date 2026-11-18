const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const sourcePath = path.join(__dirname, '..', '..', 'static', 'common_ui.js');

function micButton(active) {
    return { dataset: { active: active ? 'true' : 'false' } };
}

function loadToggle(page) {
    const source = fs.readFileSync(sourcePath, 'utf8');
    const start = source.indexOf('function blockNekoShortcutDuringTutorial');
    const end = source.indexOf('window.toggleScreenShare = function');
    assert.ok(start >= 0 && end > start, 'voice hotkey source slice');
    const events = [];
    const window = {
        ...page,
        dispatchEvent(event) {
            events.push({
                type: event.type,
                active: event.detail && event.detail.active,
            });
            return true;
        },
    };
    vm.runInNewContext(source.slice(start, end), { window, console, CustomEvent }, {
        filename: sourcePath,
    });
    return { window, events };
}

function dispatchedActive(events) {
    assert.equal(events.length, 1);
    assert.equal(events[0].type, 'live2d-mic-toggle');
    return events[0].active;
}

test('an active PNGTuber mic button closes the session before recording starts', () => {
    const { window, events } = loadToggle({
        live2dManager: { _floatingButtons: { mic: { button: micButton(false) } } },
        pngtuberManager: { _floatingButtons: { mic: { button: micButton(true) } } },
        isRecording: false,
    });
    window.toggleVoiceSession();
    assert.equal(dispatchedActive(events), false);
});

test('recording without mic buttons still closes the session', () => {
    const { window, events } = loadToggle({ isRecording: true });
    window.toggleVoiceSession();
    assert.equal(dispatchedActive(events), false);
});

test('an idle PNGTuber session opens voice', () => {
    const { window, events } = loadToggle({
        pngtuberManager: { _floatingButtons: { mic: { button: micButton(false) } } },
        isRecording: false,
    });
    window.toggleVoiceSession();
    assert.equal(dispatchedActive(events), true);
});
