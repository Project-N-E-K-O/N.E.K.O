const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// The pytest wrapper copies this file to a temp path, so __dirname may not be the repo.
const fileRoot = path.resolve(__dirname, '..', '..');
const PROJECT_ROOT = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const source = fs.readFileSync(path.join(PROJECT_ROOT, 'static', 'app', 'app-proactive.js'), 'utf8');
const clock = { now: 1_000_000 };
const channels = [];
const queue = [];

class FakeBroadcastChannel {
    constructor(name) {
        this.name = name;
        this.onmessage = null;
        channels.push(this);
    }
    postMessage(data) {
        const copy = JSON.parse(JSON.stringify(data));
        for (const peer of channels) {
            if (peer !== this && peer.name === this.name) {
                queue.push(() => peer.onmessage && peer.onmessage({ data: copy }));
            }
        }
    }
    close() {}
}

function flush() {
    while (queue.length) queue.shift()();
}

function createWindow(pathname, options = {}) {
    const timers = new Map();
    const intervals = [];
    let nextTimer = 1;
    const listeners = { document: {}, window: {} };
    const fetches = [];
    const bodies = [];
    const appState = {
        proactiveChatEnabled: true,
        proactiveNewsChatEnabled: true,
        proactiveChatInterval: 10,
        proactiveChatBackoffLevel: 0,
        proactiveChatTimer: null,
        isRecording: false,
        isProactiveChatRunning: false,
        _proactiveStartupDelayApplied: true,
    };
    const input = {
        nodeType: 1,
        id: 'textInputBox',
        disabled: false,
        readOnly: false,
        matches(selector) {
            return String(selector).split(',').some((part) => part.trim() === '#textInputBox');
        },
    };
    const document = {
        activeElement: null,
        hasFocus: () => true,
        body: { classList: { contains: () => false }, getAttribute: () => null },
        addEventListener(type, fn) {
            (listeners.document[type] || (listeners.document[type] = [])).push(fn);
        },
        getElementById: () => null,
        querySelector: () => null,
    };
    const window = {
        console: { log() {}, info() {}, debug() {}, warn() {}, error() {} },
        location: { pathname, origin: 'https://local.test' },
        appState,
        appConst: {},
        lastUserInputTime: 0,
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        document,
        navigator: { userAgent: 'node' },
        addEventListener(type, fn) {
            (listeners.window[type] || (listeners.window[type] = [])).push(fn);
        },
        setTimeout(fn, delay) {
            const id = nextTimer++;
            timers.set(id, { fn, delay, due: clock.now + delay });
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
        setInterval(fn) {
            // Heartbeat stays disabled unless a test drives it explicitly.
            if (!options.captureIntervals) return 0;
            intervals.push(fn);
            return 1000 + intervals.length;
        },
        clearInterval() {},
        fetch(url, opts) {
            fetches.push(url);
            if (opts && typeof opts.body === 'string') bodies.push(JSON.parse(opts.body));
            const body = window.__proactiveResponse || { success: false, action: 'pass' };
            return Promise.resolve({
                ok: true,
                status: 200,
                json: async () => body,
            });
        },
        BroadcastChannel: options.noBroadcastChannel ? undefined : FakeBroadcastChannel,
    };
    window.window = window;
    vm.createContext(window);
    window.__clock = clock;
    vm.runInContext('Date.now = () => globalThis.__clock.now;', window);
    vm.runInContext(source, window, { filename: 'static/app/app-proactive.js' });
    return {
        window, appState, document, input, timers, fetches, bodies, listeners,
        heartbeat() {
            for (const fn of intervals) fn();
        },
        emit(type, target) {
            for (const fn of listeners.document[type] || []) fn({ target });
        },
        scheduledDelay() {
            const timer = appState.proactiveChatTimer && timers.get(appState.proactiveChatTimer);
            return timer ? timer.delay : null;
        },
        async fire() {
            const timer = timers.get(appState.proactiveChatTimer);
            assert.ok(timer, '应有待触发的主动搭话定时器');
            assert.ok(clock.now >= timer.due, '定时器尚未到期');
            timers.delete(appState.proactiveChatTimer);
            await timer.fn();
        },
    };
}

const INACTIVE = { success: true, action: 'pass', reason_code: 'PASS_SYSTEM_INACTIVE', system_inactive_reason: 'display_off' };
const MODEL_PASS = { success: true, action: 'pass', reason_code: 'PASS_MODEL_PASS' };

function setup() {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    flush();
    return pet;
}

function proactiveFetches(pet) {
    return pet.fetches.filter((url) => url === '/api/proactive_chat').length;
}

test('text tick fired long after its due time (system sleep) is dropped and the schedule restarts quietly', async () => {
    const pet = setup();
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay() + 10 * 60 * 1000;
    await pet.fire();
    assert.equal(proactiveFetches(pet), 0, 'no catch-up proactive right after wake');
    assert.equal(pet.appState.proactiveChatBackoffLevel, 0, 'a dropped tick is not an attempt');
    assert.ok(pet.scheduledDelay() >= 59999, 'stays quiet for at least 60s after wake');

    clock.now += pet.scheduledDelay();
    await pet.fire();
    assert.equal(proactiveFetches(pet), 1, 'resumes normally after the quiet window');
});

test('moderately late text tick (load / throttling) is not mistaken for sleep', async () => {
    const pet = setup();
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay() + 90 * 1000;  // late, but under the 120s floor
    await pet.fire();
    assert.equal(proactiveFetches(pet), 1);
});

test('lateness below one base interval is not sleep even above the floor', async () => {
    const pet = setup();
    pet.appState.proactiveChatInterval = 300;
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay() + 200 * 1000;  // > 120s floor, < 300s base
    await pet.fire();
    assert.equal(proactiveFetches(pet), 1);
});

test('voice tick after system sleep is dropped without counting as no-response', async () => {
    const pet = setup();
    pet.appState.isRecording = true;
    pet.appState._voiceProactiveNoResponseCount = 0;
    pet.window.scheduleProactiveChat();
    assert.equal(pet.scheduledDelay(), 10000);
    clock.now += 10000 + 30 * 60 * 1000;
    await pet.fire();
    assert.equal(proactiveFetches(pet), 0);
    assert.equal(pet.appState._voiceProactiveNoResponseCount, 0);
    assert.ok(pet.scheduledDelay() >= 59999, 'voice also waits out the quiet window');

    clock.now += pet.scheduledDelay();
    await pet.fire();
    assert.equal(proactiveFetches(pet), 1);
});

test('PASS_SYSTEM_INACTIVE does not raise the text backoff level; ordinary pass still does', async () => {
    const pet = setup();
    pet.window.__proactiveResponse = INACTIVE;
    pet.window.scheduleProactiveChat();
    for (let i = 0; i < 5; i += 1) {
        clock.now += pet.scheduledDelay();
        await pet.fire();
        assert.ok(pet.scheduledDelay() >= 59999, 'screen-off polling backs off to the recheck interval');
    }
    assert.equal(proactiveFetches(pet), 5);
    assert.equal(pet.appState.proactiveChatBackoffLevel, 0, 'inactive passes are not attempts');

    pet.window.__proactiveResponse = MODEL_PASS;
    clock.now += pet.scheduledDelay();
    await pet.fire();
    assert.equal(pet.appState.proactiveChatBackoffLevel, 1, 'control: a model pass is an attempt');
});

test('PASS_SYSTEM_INACTIVE does not consume the voice no-response quota; ordinary pass still does', async () => {
    const pet = setup();
    pet.appState.isRecording = true;
    pet.appState._voiceProactiveNoResponseCount = 0;
    pet.window.__proactiveResponse = INACTIVE;
    pet.window.scheduleProactiveChat();
    for (let i = 0; i < 12; i += 1) {
        clock.now += pet.scheduledDelay();
        await pet.fire();
        assert.ok(pet.scheduledDelay() >= 59999, 'voice also rechecks at most once per recheck interval');
    }
    assert.equal(proactiveFetches(pet), 12, 'keeps polling (slowly) through a long screen-off');
    assert.equal(pet.appState._voiceProactiveNoResponseCount, 0);

    pet.window.__proactiveResponse = MODEL_PASS;
    clock.now += pet.scheduledDelay();
    await pet.fire();
    assert.equal(pet.appState._voiceProactiveNoResponseCount, 1, 'control: a delivered voice nudge counts');
});

test('typing that keeps deferring a tick for minutes is not mistaken for sleep', async () => {
    const pet = setup();
    const sleepLogs = [];
    pet.window.console.log = (msg) => { if (/system sleep/.test(String(msg))) sleepLogs.push(msg); };
    pet.window.scheduleProactiveChat();
    pet.document.activeElement = pet.input;
    pet.emit('focusin', pet.input);
    // Keep typing for well over the 120s floor; each due tick re-defers.
    const typingEnd = clock.now + 200 * 1000;
    while (clock.now < typingEnd) {
        clock.now += 20 * 1000;
        pet.emit('input', pet.input);
        const timer = pet.timers.get(pet.appState.proactiveChatTimer);
        if (timer && clock.now >= timer.due) await pet.fire();
    }
    assert.equal(proactiveFetches(pet), 0, 'still typing: no proactive');
    pet.document.activeElement = null;
    for (let i = 0; i < 5 && proactiveFetches(pet) === 0; i += 1) {
        const timer = pet.timers.get(pet.appState.proactiveChatTimer);
        assert.ok(timer, 'a tick stays scheduled');
        clock.now = Math.max(clock.now, timer.due);
        await pet.fire();
    }
    assert.equal(proactiveFetches(pet), 1, 'speaks once typing stops');
    assert.deepEqual(sleepLogs, [], 'input deferrals must not trip the sleep guard');
});


function setupLeaderAndFollower() {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/', { captureIntervals: true });
    const chat = createWindow('/chat', { captureIntervals: true });
    flush();
    pet.heartbeat();
    chat.heartbeat();
    flush();
    assert.equal(pet.window.isProactiveLeader(), true);
    assert.equal(chat.window.isProactiveLeader(), false);
    chat.window.scheduleProactiveChat();  // follower: leader recheck timer
    pet.window.scheduleProactiveChat();   // leader: proactive tick
    return { pet, chat };
}

test('follower taking over through its heartbeat right after wake keeps the quiet window', () => {
    const { chat } = setupLeaderAndFollower();
    clock.now += 10 * 60 * 1000;  // machine slept; the leader's heartbeat expired meanwhile
    chat.heartbeat();
    assert.equal(chat.window.isProactiveLeader(), true, 'follower takes over');
    assert.ok(chat.scheduledDelay() >= 59999, 'new leader still waits out the wake quiet window');
});

test('follower taking over through its recheck timer right after wake keeps the quiet window', async () => {
    const { chat } = setupLeaderAndFollower();
    clock.now += 10 * 60 * 1000;
    await chat.fire();  // recheck timer fires first on wake
    assert.equal(chat.window.isProactiveLeader(), true);
    assert.ok(chat.scheduledDelay() >= 59999);
});

test('regular heartbeats do not trigger the wall-clock sleep guard', () => {
    const { pet, chat } = setupLeaderAndFollower();
    for (let i = 0; i < 100; i += 1) {
        clock.now += 5000;
        pet.heartbeat();
        chat.heartbeat();
        flush();
    }
    assert.equal(pet.window.isProactiveLeader(), true);
    pet.window.scheduleProactiveChat();
    assert.ok(pet.scheduledDelay() < 20000, 'leader keeps the base interval without a clock jump');
});

for (const mode of ['text', 'voice']) {
    test(`${mode} tick that fires on time inside the wake quiet window does not speak`, async () => {
        const { pet, chat } = setupLeaderAndFollower();
        pet.appState.proactiveChatInterval = 600;
        if (mode === 'voice') {
            pet.appState.isRecording = true;
            pet.appState._voiceProactiveNoResponseCount = 0;
        }
        pet.window.scheduleProactiveChat();
        const due = pet.timers.get(pet.appState.proactiveChatTimer).due;
        // Sleep with no heartbeats, waking 10s before the armed tick is due.
        clock.now = due - 10 * 1000;
        pet.heartbeat();   // heartbeat on wake starts the quiet window
        chat.heartbeat();
        flush();
        assert.equal(pet.window.isProactiveLeader(), true);
        clock.now = due;
        await pet.fire();  // on time, so not caught by the late-tick check
        assert.equal(proactiveFetches(pet), 0, 'no request inside the quiet window');
        assert.equal(pet.appState.proactiveChatBackoffLevel, 0);
        assert.equal(pet.appState._voiceProactiveNoResponseCount || 0, 0);
        const next = pet.timers.get(pet.appState.proactiveChatTimer);
        assert.ok(next && next.due >= due + 50 * 1000, 'rescheduled past the quiet deadline');
    });
}

test('8 hours of screen-off polls at most once a minute and leaves the backoff level alone', async () => {
    const pet = setup();
    pet.window.__proactiveResponse = INACTIVE;
    pet.window.scheduleProactiveChat();
    const end = clock.now + 8 * 3600 * 1000;
    while (clock.now < end) {
        clock.now += pet.scheduledDelay();
        await pet.fire();
    }
    assert.ok(proactiveFetches(pet) <= 8 * 60 + 1, `got ${proactiveFetches(pet)} requests`);
    assert.equal(pet.appState.proactiveChatBackoffLevel, 0);
});

test('tick firing before the heartbeat after a medium sleep (late < one long interval) is dropped', async () => {
    const { pet, chat } = setupLeaderAndFollower();
    pet.appState.proactiveChatInterval = 600;
    pet.window.scheduleProactiveChat();
    const due = pet.timers.get(pet.appState.proactiveChatTimer).due;
    while (clock.now + 5000 < due) {   // awake: heartbeats every 5s
        clock.now += 5000;
        pet.heartbeat();
        chat.heartbeat();
        flush();
    }
    clock.now = due + 5 * 60 * 1000;  // slept 5 min: late by less than one 600s interval
    await pet.fire();                 // the tick wins the race against the heartbeat
    assert.equal(proactiveFetches(pet), 0, 'no catch-up line right at wake');
    assert.ok(pet.scheduledDelay() >= 59999);
});

test('a later wake jump never shortens a longer pending recheck wait', async () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/', { captureIntervals: true, noBroadcastChannel: true });
    pet.window.__proactiveResponse = INACTIVE;
    pet.appState.proactiveChatInterval = 300;  // recheck = max(300s, 60s)
    const quietMs = () => pet.window.appProactive._getSystemQuietDelay();
    pet.window.scheduleProactiveChat();
    const due = pet.timers.get(pet.appState.proactiveChatTimer).due;
    // Stay awake: sample well under the 120s sleep floor until the tick is due.
    while (clock.now < due) {
        clock.now = Math.min(due, clock.now + 100 * 1000);
        pet.heartbeat();
    }
    assert.equal(quietMs(), 0, 'no quiet window before the request');
    await pet.fire();
    assert.equal(proactiveFetches(pet), 1, 'the tick really sends and gets PASS_SYSTEM_INACTIVE');
    const passAt = clock.now;
    assert.ok(quietMs() >= 300 * 1000 - 1, `recheck wait after the pass is one base interval (got ${quietMs()})`);
    clock.now += 130 * 1000;  // > 120s without a sample: wake jump, which alone would mean now + 60s
    pet.heartbeat();
    const expected = passAt + 300 * 1000 - clock.now;  // 170s left of the recheck wait
    assert.ok(quietMs() >= expected - 1, `wake jump must not shorten the recheck wait (got ${quietMs()}, want >= ${expected})`);
});

for (const mode of ['text', 'voice']) {
    test(`${mode}: sleeping while the request headers are fetched cancels the send`, async () => {
        const { pet } = setupLeaderAndFollower();
        if (mode === 'voice') {
            pet.appState.isRecording = true;
            pet.appState._voiceProactiveNoResponseCount = 0;
        }
        let release;
        pet.window.nekoLocalMutationSecurity = {
            getMutationHeaders: () => new Promise((resolve) => { release = () => resolve({}); }),
        };
        pet.window.scheduleProactiveChat();
        clock.now += pet.scheduledDelay();
        const pending = pet.fire();   // on time: passes the tick checks
        for (let i = 0; i < 20 && typeof release !== 'function'; i += 1) {
            await new Promise((resolve) => setImmediate(resolve));
        }
        assert.equal(typeof release, 'function', 'waiting for headers');
        clock.now += 10 * 60 * 1000;  // machine sleeps during the await
        release();
        await pending;
        assert.equal(proactiveFetches(pet), 0, 'no POST right after wake');
        assert.equal(pet.appState.proactiveChatBackoffLevel, 0);
        assert.equal(pet.appState._voiceProactiveNoResponseCount || 0, 0);
        assert.ok(pet.scheduledDelay() >= 59999, 'rescheduled after the quiet window');
    });
}

for (const mode of ['text', 'voice']) {
    for (const slept of [true, false]) {
        test(`${mode}, no BroadcastChannel: ${slept ? 'sleeping during the header await cancels the send' : 'no sleep still sends'}`, async () => {
            channels.length = 0;
            queue.length = 0;
            clock.now = 1_000_000;
            const pet = createWindow('/', { captureIntervals: true, noBroadcastChannel: true });
            assert.equal(pet.window.isProactiveLeader(), true, 'single window leads');
            if (mode === 'voice') {
                pet.appState.isRecording = true;
                pet.appState._voiceProactiveNoResponseCount = 0;
            }
            let release;
            pet.window.nekoLocalMutationSecurity = {
                getMutationHeaders: () => new Promise((resolve) => { release = () => resolve({}); }),
            };
            pet.window.scheduleProactiveChat();
            clock.now += pet.scheduledDelay();
            pet.heartbeat();  // sampler ticks while awake
            const pending = pet.fire();
            for (let i = 0; i < 20 && typeof release !== 'function'; i += 1) {
                await new Promise((resolve) => setImmediate(resolve));
            }
            assert.equal(typeof release, 'function');
            if (slept) clock.now += 10 * 60 * 1000;
            release();
            await pending;
            assert.equal(proactiveFetches(pet), slept ? 0 : 1);
            assert.equal(pet.appState.proactiveChatBackoffLevel, mode === 'text' && !slept ? 1 : 0);
        });
    }
}

test('voice request carries voice_session_active only while actually recording', async () => {
    const pet = setup();
    pet.appState.isRecording = true;
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay();
    await pet.fire();
    assert.equal(pet.bodies.length, 1);
    assert.equal(pet.bodies[0].voice_session_active, true);
});

test('recording stopped while headers are fetched: flag is false at send time', async () => {
    const pet = setup();
    pet.appState.isRecording = true;
    let release;
    pet.window.nekoLocalMutationSecurity = {
        getMutationHeaders: () => new Promise((resolve) => { release = () => resolve({}); }),
    };
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay();
    const pending = pet.fire();
    for (let i = 0; i < 20 && typeof release !== 'function'; i += 1) {
        await new Promise((resolve) => setImmediate(resolve));
    }
    pet.appState.isRecording = false;
    release();
    await pending;
    assert.equal(pet.bodies.length, 1);
    assert.equal(pet.bodies[0].voice_session_active, false);
});

test('text request never carries voice_session_active', async () => {
    const pet = setup();
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay();
    await pet.fire();
    assert.equal(pet.bodies.length, 1);
    assert.equal(pet.bodies[0].voice_session_active, undefined);
});

test('clock set back after wake does not stretch the quiet window', async () => {
    const pet = setup();
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay() + 10 * 60 * 1000;
    await pet.fire();                     // dropped: quiet for 60s
    clock.now -= 10 * 60 * 1000;          // time sync moves the clock back 10 min
    pet.window.scheduleProactiveChat();
    assert.ok(pet.scheduledDelay() <= 60000 + 1, `delay ${pet.scheduledDelay()} capped to the quiet floor`);
    // The stored deadline must be rebased as well: the next tick really speaks.
    const setBackAt = clock.now;
    for (let i = 0; i < 5 && proactiveFetches(pet) === 0; i += 1) {
        clock.now += pet.scheduledDelay();
        await pet.fire();
    }
    assert.equal(proactiveFetches(pet), 1, 'speaks again after one capped quiet window');
    assert.ok(clock.now - setBackAt <= 60000 + 1, `resumed after ${(clock.now - setBackAt) / 1000}s, not ~11 min`);
});

test('a tick firing a few ms before the quiet deadline still speaks', async () => {
    const pet = setup();
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay() + 10 * 60 * 1000;
    await pet.fire();                     // dropped: quiet for 60s, next tick at the deadline
    const id = pet.appState.proactiveChatTimer;
    const timer = pet.timers.get(id);
    clock.now = timer.due - 1;            // browser timer fires 1ms early
    pet.timers.delete(id);
    await timer.fn();
    assert.equal(proactiveFetches(pet), 1, 'no extra full interval for a 1ms early timer');
});
