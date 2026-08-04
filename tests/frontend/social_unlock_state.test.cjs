const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const METHODS_SOURCE = fs.readFileSync(
    path.join(PROJECT_ROOT, 'static/avatar/avatar-ui-buttons/methods-buttons.js'),
    'utf8'
);

function loadSocialUnlock() {
    const storage = new Map();
    const listeners = new Map();
    const window = {
        localStorage: {
            getItem(key) { return storage.has(key) ? storage.get(key) : null; },
            setItem(key, value) { storage.set(key, String(value)); }
        },
        addEventListener(type, listener) { listeners.set(type, listener); },
        t(key, params = {}) { return `${key}:${params.days ?? ''}`; }
    };
    const context = vm.createContext({
        AvatarButtonMixin: { methods: {} },
        clearTimeout,
        console,
        document: { querySelectorAll() { return []; } },
        setTimeout,
        window
    });
    vm.runInContext(METHODS_SOURCE, context, { filename: 'methods-buttons.js' });
    return { api: window.nekoSocialUnlock, storage, listeners };
}

test('natural-day countdown persists first-seen date and unlocks on day four', () => {
    const { api, storage } = loadSocialUnlock();
    const dayOne = new Date(2026, 0, 1, 12);
    const dayTwo = new Date(2026, 0, 2, 12);
    const dayThree = new Date(2026, 0, 3, 12);
    const dayFour = new Date(2026, 0, 4, 12);

    assert.equal(api.getStatus(dayOne).remainingDays, 3);
    assert.equal(storage.get('neko.social.unlock.v1'), '2026-01-01');
    assert.equal(api.getStatus(dayTwo).remainingDays, 2);
    assert.equal(api.getStatus(dayThree).remainingDays, 1);
    assert.equal(api.getStatus(dayFour).unlocked, true);
    assert.equal(storage.get('neko.social.unlock.v1'), '2026-01-01');
});

test('clock moving backward does not unlock the social entry early', () => {
    const { api } = loadSocialUnlock();
    api.getStatus(new Date(2026, 0, 4, 12));

    const earlier = api.getStatus(new Date(2026, 0, 2, 12));
    assert.equal(earlier.dayDelta, 0);
    assert.equal(earlier.remainingDays, 3);
    assert.equal(earlier.unlocked, false);
});

test('all social opening paths contain the shared unlock guard', () => {
    const controlsSource = fs.readFileSync(
        path.join(PROJECT_ROOT, 'static/app/app-ui/surface-floating-controls.js'),
        'utf8'
    );
    assert.match(controlsSource, /nekoSocialUnlock\.isLocked\(\)/);
    assert.match(METHODS_SOURCE, /stopImmediatePropagation\(\)/);
    for (const renderer of ['live2d', 'vrm', 'mmd']) {
        const source = fs.readFileSync(
            path.join(PROJECT_ROOT, `static/${renderer}/${renderer}-ui-buttons.js`),
            'utf8'
        );
        assert.match(source, /live2d-social-click/);
    }
});

test('locked and unlocked button styles and titles are applied consistently', () => {
    const { api } = loadSocialUnlock();
    const button = {
        dataset: {},
        style: {},
        setAttribute(name, value) { this[name] = value; },
        removeAttribute(name) { delete this[name]; }
    };
    const imgOff = { style: {} };
    const imgOn = { style: {} };

    api.applyButtonState(button, imgOff, imgOn, {
        unlocked: false,
        remainingDays: 3
    });
    assert.equal(button.dataset.socialLocked, 'true');
    assert.equal(button['aria-disabled'], 'true');
    assert.equal(button.title, 'buttons.socialCharging:3');
    assert.equal(button.style.filter, 'grayscale(1)');

    api.applyButtonState(button, imgOff, imgOn, {
        unlocked: true,
        remainingDays: 0
    });
    assert.equal(button.dataset.socialLocked, 'false');
    assert.equal(button['aria-disabled'], 'false');
    assert.equal(button.title, 'buttons.social:');
    assert.equal(button.style.cursor, 'pointer');
});
