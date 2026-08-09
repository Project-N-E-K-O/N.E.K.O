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

function loadSocialUnlock(options = {}) {
    const storage = new Map();
    const listeners = new Map();
    const registeredButtons = [];
    const dispatchedEvents = [];
    let tutorialState = options.tutorialState || null;
    class CustomEvent {
        constructor(type, init = {}) {
            this.type = type;
            this.detail = init.detail;
        }
    }
    const window = {
        CustomEvent,
        localStorage: {
            getItem(key) { return storage.has(key) ? storage.get(key) : null; },
            setItem(key, value) { storage.set(key, String(value)); }
        },
        addEventListener(type, listener) { listeners.set(type, listener); },
        dispatchEvent(event) {
            dispatchedEvents.push(event);
            const listener = listeners.get(event && event.type);
            if (listener) listener(event);
            return true;
        },
        t(key, params = {}) { return `${key}:${params.days ?? ''}`; }
    };
    if (options.withTutorialState) {
        window.NekoSevenDayTutorialState = {
            ROUND_COUNT: 7,
            loadState() { return tutorialState; },
            ready() {
                if (options.authoritativeTutorialState) {
                    tutorialState = options.authoritativeTutorialState;
                }
                return Promise.resolve(tutorialState);
            }
        };
    }
    const document = {
        querySelectorAll(selector) {
            assert.equal(selector, '[data-social-button="true"]');
            return registeredButtons;
        }
    };
    const context = vm.createContext({
        AvatarButtonMixin: { methods: {} },
        clearTimeout,
        console,
        document,
        setTimeout,
        window
    });
    vm.runInContext(METHODS_SOURCE, context, { filename: 'methods-buttons.js' });
    return {
        api: window.nekoSocialUnlock,
        storage,
        listeners,
        registeredButtons,
        dispatchedEvents,
        setTutorialState(nextState) { tutorialState = nextState; }
    };
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

test('existing users migrated by the seven-day tutorial skip charging immediately', () => {
    const { api, storage, dispatchedEvents, setTutorialState } = loadSocialUnlock({
        withTutorialState: true,
        tutorialState: {
            firstSeenDate: '2026-01-01',
            completedRounds: [],
            skippedRounds: [1, 2, 3, 4, 5, 6, 7]
        }
    });

    const status = api.getStatus(new Date(2026, 0, 1, 12));
    assert.equal(status.unlocked, true);
    assert.equal(status.remainingDays, 0);
    assert.equal(storage.get('neko.social.unlock.v1'), '2025-12-29');
    const event = dispatchedEvents.find(item => item.type === 'neko-social-unlock-status');
    assert.ok(event);
    assert.equal(event.detail.firstSeenDate, '2025-12-29');
    assert.equal(event.detail.unlocked, true);
    assert.equal(event.detail.existingUser, true);

    api.getStatus(new Date(2026, 0, 1, 12));
    assert.equal(
        dispatchedEvents.filter(item => item.type === 'neko-social-unlock-status').length,
        1
    );

    setTutorialState({
        firstSeenDate: '2026-01-01',
        completedRounds: [],
        skippedRounds: []
    });
    const afterReset = api.getStatus(new Date(2026, 0, 1, 12));
    assert.equal(afterReset.unlocked, true);
    const unlockEvents = dispatchedEvents.filter(item => item.type === 'neko-social-unlock-status');
    assert.equal(unlockEvents.length, 2);
    assert.equal(unlockEvents[1].detail.firstSeenDate, '2025-12-29');
    assert.equal(unlockEvents[1].detail.unlocked, true);
    assert.equal(unlockEvents[1].detail.existingUser, false);
});

test('users whose tutorial first-seen date is three days old skip charging', () => {
    const { api, storage } = loadSocialUnlock({
        withTutorialState: true,
        tutorialState: {
            firstSeenDate: '2026-01-01',
            completedRounds: [1],
            skippedRounds: []
        }
    });

    const status = api.getStatus(new Date(2026, 0, 4, 12));
    assert.equal(status.unlocked, true);
    assert.equal(status.remainingDays, 0);
    assert.equal(storage.get('neko.social.unlock.v1'), '2026-01-01');
});

test('genuinely new users still charge from the shared first-seen date', () => {
    const { api } = loadSocialUnlock({
        withTutorialState: true,
        tutorialState: {
            firstSeenDate: '2026-01-01',
            completedRounds: [],
            skippedRounds: []
        }
    });

    const status = api.getStatus(new Date(2026, 0, 1, 12));
    assert.equal(status.unlocked, false);
    assert.equal(status.remainingDays, 3);
});

test('authoritative tutorial migration refreshes an already-rendered social button', async () => {
    const now = new Date();
    const today = [
        now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, '0'),
        String(now.getDate()).padStart(2, '0')
    ].join('-');
    const { api, registeredButtons } = loadSocialUnlock({
        withTutorialState: true,
        tutorialState: {
            firstSeenDate: today,
            completedRounds: [],
            skippedRounds: []
        },
        authoritativeTutorialState: {
            firstSeenDate: today,
            completedRounds: [],
            skippedRounds: [1, 2, 3, 4, 5, 6, 7]
        }
    });
    const button = {
        dataset: {},
        style: {},
        setAttribute(name, value) { this[name] = value; },
        removeAttribute(name) { delete this[name]; },
        querySelectorAll() { return []; }
    };

    api.registerButton(button);
    registeredButtons.push(button);
    assert.equal(button.dataset.socialLocked, 'true');

    await new Promise(resolve => setImmediate(resolve));
    assert.equal(button.dataset.socialLocked, 'false');
    assert.equal(button['aria-disabled'], 'false');
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

test('refreshButtons updates registered buttons after the unlock date', () => {
    const { api, storage, registeredButtons } = loadSocialUnlock();
    const button = {
        dataset: {},
        style: {},
        setAttribute(name, value) { this[name] = value; },
        removeAttribute(name) { delete this[name]; },
        querySelectorAll() { return []; }
    };

    api.registerButton(button);
    registeredButtons.push(button);
    assert.equal(button.dataset.socialLocked, 'true');

    const firstSeen = new Date();
    firstSeen.setDate(firstSeen.getDate() - 3);
    const firstSeenDate = [
        firstSeen.getFullYear(),
        String(firstSeen.getMonth() + 1).padStart(2, '0'),
        String(firstSeen.getDate()).padStart(2, '0')
    ].join('-');
    storage.set('neko.social.unlock.v1', firstSeenDate);

    api.refreshButtons();
    assert.equal(button.dataset.socialLocked, 'false');
    assert.equal(button['aria-disabled'], 'false');
    assert.equal(button.title, 'buttons.social:');
});
