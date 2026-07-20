'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'js/voice_clone.js'), 'utf8');
const playPreviewSource = source.slice(
    source.indexOf('async function playPreview'),
    source.indexOf('// 加载音色列表'),
);

function createButton() {
    let innerHTML = '<img src="/static/icons/sound.png">Preview';
    let textContent = 'Preview';
    return Object.defineProperties({
        disabled: false,
        dataset: {},
    }, {
        innerHTML: {
            get() { return innerHTML; },
            set(value) {
                innerHTML = String(value);
                textContent = String(value);
            },
        },
        textContent: {
            get() { return textContent; },
            set(value) {
                textContent = String(value);
                innerHTML = String(value);
            },
        },
    });
}

function createHarness({ playError = null } = {}) {
    let audio = null;
    const notices = [];
    const cachedAudio = JSON.stringify({
        version: 2,
        language: 'en',
        audioSrc: 'data:audio/mpeg;base64,dGVzdA==',
    });

    class MockAudio {
        constructor(src) {
            this.src = src;
            this.listeners = new Map();
            audio = this;
        }

        addEventListener(type, listener) {
            this.listeners.set(type, listener);
        }

        play() {
            return playError ? Promise.reject(playError) : Promise.resolve();
        }

        emit(type) {
            this.listeners.get(type)?.();
        }
    }

    const context = {
        Audio: MockAudio,
        console: { error() {}, warn() {} },
        getVoicePreviewLanguage: () => 'en',
        localStorage: {
            getItem: () => cachedAudio,
            setItem() {},
        },
        showVoicePreviewErrorNotice: message => notices.push(message),
        window: {
            t(key, options) {
                if (key === 'voice.loading') return 'Loading...';
                if (key === 'voice.previewing') return 'Previewing';
                if (key === 'voice.playFailed') return `Play failed: ${options.error}`;
                return key;
            },
        },
    };
    vm.runInNewContext(playPreviewSource, context, { filename: 'voice_clone.js' });
    return {
        playPreview: context.playPreview,
        notices,
        get audio() { return audio; },
    };
}

test('preview button shows the playing state until audio ends', async () => {
    const harness = createHarness();
    const button = createButton();
    const originalContent = button.innerHTML;

    await harness.playPreview('test-voice', button);

    assert.equal(button.textContent, 'Previewing');
    assert.equal(button.disabled, true);
    assert.equal(button.dataset.previewState, 'playing');

    harness.audio.emit('ended');

    assert.equal(button.innerHTML, originalContent);
    assert.equal(button.disabled, false);
    assert.equal(button.dataset.previewState, undefined);
});

test('preview button restores when audio playback cannot start', async () => {
    const harness = createHarness({ playError: new Error('blocked') });
    const button = createButton();
    const originalContent = button.innerHTML;

    await harness.playPreview('test-voice', button);

    assert.equal(button.innerHTML, originalContent);
    assert.equal(button.disabled, false);
    assert.deepEqual(harness.notices, ['Play failed: blocked']);
});

test('all supported locales define the previewing label', () => {
    const locales = ['en', 'ja', 'ko', 'zh-CN', 'zh-TW', 'ru', 'pt', 'es'];
    for (const locale of locales) {
        const messages = JSON.parse(fs.readFileSync(path.join(__dirname, `locales/${locale}.json`), 'utf8'));
        assert.equal(typeof messages.voice.previewing, 'string', `${locale} is missing voice.previewing`);
        assert.notEqual(messages.voice.previewing.trim(), '', `${locale} has an empty voice.previewing`);
    }
});
