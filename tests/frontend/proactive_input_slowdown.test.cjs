const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/app/app-proactive.js'), 'utf8');
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

function createWindow(pathname) {
    const timers = new Map();
    let nextTimer = 1;
    const listeners = { document: {}, window: {} };
    const fetches = [];
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
        setInterval() { return 0; },
        clearInterval() {},
        fetch(url) {
            fetches.push(url);
            return Promise.resolve({
                ok: true,
                status: 200,
                json: async () => ({ success: false, action: 'pass' }),
            });
        },
        BroadcastChannel: FakeBroadcastChannel,
    };
    window.window = window;
    vm.createContext(window);
    window.__clock = clock;
    vm.runInContext('Date.now = () => globalThis.__clock.now;', window);
    vm.runInContext(source, window, { filename: 'static/app/app-proactive.js' });
    return {
        window, appState, document, input, timers, fetches, listeners,
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

test('持续输入会刷新判定窗口，到期后才搭话', async () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    const chat = createWindow('/chat');
    flush();
    assert.equal(pet.window.isProactiveLeader(), true);
    assert.equal(chat.window.isProactiveLeader(), false);

    pet.window.scheduleProactiveChat();
    assert.ok(pet.scheduledDelay() < 20000, '未输入时按基础间隔排定');

    chat.document.activeElement = chat.input;
    chat.emit('focusin', chat.input);
    flush();
    const focusedTimer = pet.appState.proactiveChatTimer;
    const initialDelay = pet.scheduledDelay();
    assert.ok(initialDelay < 20000, '聚焦保留已排定的基础计时器');

    clock.now += initialDelay;
    await pet.fire();
    assert.equal(pet.fetches.length, 0, '原计时器到点时仍在输入窗口内，不得发送');
    assert.equal(pet.appState.proactiveChatBackoffLevel, 0);
    assert.ok(Math.abs(pet.scheduledDelay() - (30000 - initialDelay)) < 1,
        '只补足到聚焦后的30秒截止时间');
    assert.notEqual(pet.appState.proactiveChatTimer, focusedTimer);

    clock.now = 1_020_000;
    const pendingTimer = pet.appState.proactiveChatTimer;
    chat.emit('input', chat.input);
    flush();
    assert.equal(pet.appState.proactiveChatTimer, pendingTimer, '继续输入只刷新截止时间，保留计时器');

    clock.now = 1_030_000;
    await pet.fire();
    assert.equal(pet.fetches.length, 0, '继续输入将截止时间延长到50秒，30秒时不得发送');
    assert.equal(pet.scheduledDelay(), 20000, '只等待刷新后窗口的剩余20秒');
    assert.equal(pet.appState.proactiveChatBackoffLevel, 0);

    clock.now += pet.scheduledDelay();
    await pet.fire();
    assert.equal(pet.fetches.length, 1, '续期窗口到期后应正常搭话');
});

test('聚焦后 300ms 内输入只补到新的截止时间', async () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    flush();
    pet.document.activeElement = pet.input;
    pet.emit('focusin', pet.input);
    const focusedTimer = pet.appState.proactiveChatTimer;
    const focusDelay = pet.scheduledDelay();
    assert.ok(focusDelay >= 30000 && focusDelay < 31000);

    clock.now += 300;
    pet.emit('input', pet.input);
    assert.equal(pet.appState.proactiveChatTimer, focusedTimer, '400ms 内的输入合并进当前定时器');

    clock.now += focusDelay - 300;
    await pet.fire();
    assert.equal(pet.fetches.length, 0, '旧定时器早于新截止时间，不得搭话');
    assert.equal(pet.appState.proactiveChatBackoffLevel, 0);
    const retryDelay = pet.scheduledDelay();
    assert.ok(retryDelay > 250 && retryDelay <= 400, '只等待剩余窗口，不重开 30 秒');

    clock.now += retryDelay;
    await pet.fire();
    assert.equal(pet.fetches.length, 1);
});

test('语音模式在输入窗口结束前不发送，且不增加无回复计数', async () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    flush();
    pet.appState.isRecording = true;
    pet.appState._voiceProactiveNoResponseCount = 0;
    pet.document.activeElement = pet.input;
    pet.emit('focusin', pet.input);
    assert.equal(pet.scheduledDelay(), 10000);

    clock.now += pet.scheduledDelay();
    await pet.fire();
    assert.equal(pet.fetches.length, 0, '输入窗口未结束时语音搭话也不发送');
    assert.equal(pet.appState._voiceProactiveNoResponseCount, 0);
    assert.ok(pet.scheduledDelay() > 15000 && pet.scheduledDelay() <= 20000);

    clock.now += pet.scheduledDelay();
    await pet.fire();
    assert.equal(pet.fetches.length, 1);
    assert.equal(pet.appState._voiceProactiveNoResponseCount, 1);
});

test('只聚焦不继续输入时，窗口结束后仍会搭话', async () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    flush();
    pet.document.activeElement = pet.input;
    pet.emit('focusin', pet.input);
    assert.ok(pet.scheduledDelay() >= 30000);

    clock.now += 31000;
    await pet.fire();
    assert.equal(pet.fetches.length, 1, '聚焦本身不能一直关掉主动搭话');
});

test('间隔长于 30 秒时，输入不额外拉长排程', () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    flush();
    pet.appState.proactiveChatInterval = 60;
    pet.window.scheduleProactiveChat();
    const timer = pet.appState.proactiveChatTimer;
    const delay = pet.scheduledDelay();
    pet.document.activeElement = pet.input;
    pet.emit('focusin', pet.input);
    pet.emit('input', pet.input);
    assert.equal(pet.appState.proactiveChatTimer, timer);
    assert.equal(pet.scheduledDelay(), delay);
});

function holdMutationHeaders(pet) {
    let release;
    pet.window.nekoLocalMutationSecurity = {
        getMutationHeaders() {
            return new Promise((resolve) => {
                release = () => resolve({});
            });
        },
    };
    return () => release;
}

test('取请求头期间开始输入则取消文本搭话', async () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    flush();
    const releaseHeaders = holdMutationHeaders(pet);
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay();
    const pending = pet.fire();
    assert.equal(typeof releaseHeaders(), 'function');
    pet.document.activeElement = pet.input;
    pet.emit('input', pet.input);
    releaseHeaders()();
    await pending;
    assert.equal(pet.fetches.length, 0, '输入窗口重新打开后不得发送');
    assert.equal(pet.appState.proactiveChatBackoffLevel, 0);
    assert.ok(pet.scheduledDelay() >= 30000, '取消后按新的输入窗口重新排定');
});

test('取请求头期间开始输入则取消语音搭话', async () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    flush();
    const releaseHeaders = holdMutationHeaders(pet);
    pet.appState.isRecording = true;
    pet.appState._voiceProactiveNoResponseCount = 0;
    pet.window.scheduleProactiveChat();
    assert.equal(pet.scheduledDelay(), 10000);
    clock.now += 10000;
    const pending = pet.fire();
    assert.equal(typeof releaseHeaders(), 'function');
    pet.document.activeElement = pet.input;
    pet.emit('input', pet.input);
    releaseHeaders()();
    await pending;
    assert.equal(pet.fetches.length, 0);
    assert.equal(pet.appState._voiceProactiveNoResponseCount, 0);
    assert.ok(pet.scheduledDelay() !== null);
});

test('输入取消屏幕搭话时不消耗视觉冷却', async () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    flush();
    const releaseHeaders = holdMutationHeaders(pet);
    pet.window.proactiveVisionEnabled = true;
    pet.appState.proactiveVisionEnabled = true; // 真实页面由 app-state 桥接，此处补齐模拟状态。
    pet.window.appScreen = {
        acquireOrReuseCachedStream: async () => null,
        fetchBackendScreenshot: async () => ({ dataUrl: 'data:image/jpeg;base64,aa' }),
    };
    pet.appState.proactiveNewsChatEnabled = false;
    pet.appState.proactiveVisionChatEnabled = true;
    pet.appState.proactiveVisionInterval = 120;
    pet.appState._lastProactiveChatScreenTime = 0;
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay();
    const pending = pet.fire();
    for (let i = 0; i < 20 && typeof releaseHeaders() !== 'function'; i += 1) {
        await new Promise((resolve) => setImmediate(resolve));
    }
    assert.equal(typeof releaseHeaders(), 'function');
    assert.notEqual(pet.appState._lastProactiveChatScreenTime, 0, '截图准备期间会先占上冷却');
    pet.document.activeElement = pet.input;
    pet.emit('input', pet.input);
    releaseHeaders()();
    await pending;
    assert.equal(pet.fetches.filter((url) => url === '/api/proactive_chat').length, 0);
    assert.equal(pet.appState._lastProactiveChatScreenTime, 0, '请求没发出去，视觉冷却要还回去');
    assert.equal(pet.appState.proactiveChatBackoffLevel, 0);
});

test('CSRF 重试期间开始输入也会退还视觉冷却', async () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    flush();
    let releaseRefresh;
    pet.window.nekoLocalMutationSecurity = {
        getMutationHeaders: async () => ({}),
        refreshToken() {
            return new Promise((resolve) => { releaseRefresh = resolve; });
        },
    };
    pet.window.fetch = async (url) => {
        pet.fetches.push(url);
        if (url === '/api/proactive_chat') {
            return {
                ok: false,
                status: 403,
                clone() { return this; },
                json: async () => ({ error_code: 'csrf_validation_failed' }),
            };
        }
        return { ok: true, status: 200, json: async () => ({ success: false }) };
    };
    pet.window.proactiveVisionEnabled = true;
    pet.appState.proactiveVisionEnabled = true; // 真实页面由 app-state 桥接，此处补齐模拟状态。
    pet.window.appScreen = {
        acquireOrReuseCachedStream: async () => null,
        fetchBackendScreenshot: async () => ({ dataUrl: 'data:image/jpeg;base64,aa' }),
    };
    pet.appState.proactiveNewsChatEnabled = false;
    pet.appState.proactiveVisionChatEnabled = true;
    pet.appState.proactiveVisionInterval = 120;
    pet.appState._lastProactiveChatScreenTime = 0;
    pet.window.scheduleProactiveChat();
    clock.now += pet.scheduledDelay();
    const pending = pet.fire();
    for (let i = 0; i < 20 && typeof releaseRefresh !== 'function'; i += 1) {
        await new Promise((resolve) => setImmediate(resolve));
    }
    assert.equal(typeof releaseRefresh, 'function');
    assert.equal(pet.fetches.filter((url) => url === '/api/proactive_chat').length, 1);
    assert.notEqual(pet.appState._lastProactiveChatScreenTime, 0);
    pet.document.activeElement = pet.input;
    pet.emit('input', pet.input);
    releaseRefresh();
    await pending;
    assert.equal(pet.fetches.filter((url) => url === '/api/proactive_chat').length, 1, '重试不应再发送');
    assert.equal(pet.appState._lastProactiveChatScreenTime, 0, '重试被输入取消后也要退还视觉冷却');
    assert.equal(pet.appState.proactiveChatBackoffLevel, 0);
});


test('reviewed idle observes typing even with proactive chat disabled, without scheduling chat', () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 1_000_000;
    const pet = createWindow('/');
    pet.appState.proactiveChatEnabled = false;
    const manager = { hasReviewedActions: () => true, _reviewedIdleSince: 1 };
    pet.window.pngtuberManager = manager;
    pet.emit('input', pet.input);
    assert.equal(manager._reviewedIdleSince, null);
    assert.equal(pet.window.getReviewedPNGTuberActivity().inputAt, clock.now);
    assert.equal(pet.appState.proactiveChatTimer, null);
    clock.now += 30001;
    assert.equal(pet.window.getReviewedPNGTuberActivity().busy, false);
});


test('chat-follower input reaches the reviewed owner while proactive chat is off', () => {
    channels.length = 0;
    queue.length = 0;
    clock.now = 2_000_000;
    const pet = createWindow('/'), chat = createWindow('/chat');
    pet.appState.proactiveChatEnabled = chat.appState.proactiveChatEnabled = false;
    flush();
    chat.emit('input', chat.input);
    flush();
    assert.equal(pet.window.getReviewedPNGTuberActivity().inputAt, clock.now);
    assert.equal(pet.appState.proactiveChatTimer, null);
});


test('reviewed typing broadcasts at most once a second with a trailing peer notification', () => {
    channels.length=0;queue.length=0;clock.now=3_000_000;
    const pet=createWindow('/'),chat=createWindow('/chat');flush();
    pet.appState.proactiveChatEnabled=chat.appState.proactiveChatEnabled=false;
    let broadcasts=0;
    const original=FakeBroadcastChannel.prototype.postMessage;
    FakeBroadcastChannel.prototype.postMessage=function(data){if(data.type==='reviewed_input_activity') broadcasts++;original.call(this,data)};
    try {
        for(let i=0;i<10;i++) {clock.now=3_000_000+i*90;chat.emit('input',chat.input);}
        flush();assert.equal(broadcasts,1);
        assert.equal(chat.window.getReviewedPNGTuberActivity().inputAt,3_000_810,'local activity updates on every key');
        clock.now=3_001_000;
        for(const [id,timer] of [...chat.timers]) if(timer.due<=clock.now){chat.timers.delete(id);timer.fn();}
        flush();assert.equal(broadcasts,2);
        assert.equal(pet.window.getReviewedPNGTuberActivity().inputAt,3_001_000,'owner receives the trailing activity');
        clock.now=3_001_050;chat.emit('input',chat.input);flush();assert.equal(broadcasts,2);
        assert.equal(chat.window.getReviewedPNGTuberActivity().inputAt,clock.now);
    } finally {FakeBroadcastChannel.prototype.postMessage=original;}
});
