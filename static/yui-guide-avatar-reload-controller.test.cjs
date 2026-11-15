const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const reloadControllerSource = fs.readFileSync(
    path.join(__dirname, 'tutorial/avatar/reload-controller.js'),
    'utf8'
);

function loadReloadControllerWindow() {
    const window = {
        appState: {
            proactiveChatEnabled: true,
            proactiveVisionEnabled: true,
            proactiveVisionChatEnabled: true,
            proactiveNewsChatEnabled: false,
            proactiveVideoChatEnabled: true,
            proactivePersonalChatEnabled: false,
            proactiveMusicEnabled: true,
            proactiveMemeEnabled: false,
            proactiveMiniGameInviteEnabled: true
        },
        proactiveChatEnabled: true,
        proactiveVisionEnabled: true,
        proactiveVisionChatEnabled: true,
        proactiveNewsChatEnabled: false,
        proactiveVideoChatEnabled: true,
        proactivePersonalChatEnabled: false,
        proactiveMusicEnabled: true,
        proactiveMemeEnabled: false,
        proactiveMiniGameInviteEnabled: true,
        stopCalls: [],
        stopProactiveChatSchedule() {
            this.stopCalls.push('chat');
        },
        stopProactiveVisionDuringSpeech() {
            this.stopCalls.push('vision');
        },
        releaseProactiveVisionStream() {
            this.stopCalls.push('stream');
        },
        scheduleCalls: 0,
        scheduleProactiveChat() {
            this.scheduleCalls += 1;
        },
        setTimeout(fn, ms = 0) {
            return setTimeout(fn, ms);
        },
        clearTimeout
    };
    const context = vm.createContext({
        window,
        console,
        setTimeout,
        clearTimeout
    });
    vm.runInContext(reloadControllerSource, context, {
        filename: path.join(__dirname, 'tutorial/avatar/reload-controller.js')
    });
    return window;
}

function attachEventRecorder(window) {
    const events = [];
    window.CustomEvent = class {
        constructor(type, init) {
            this.type = type;
            this.detail = (init && init.detail) || null;
        }
    };
    window.dispatchEvent = function (event) {
        events.push(event);
    };
    return events;
}

function suppressionEvents(events, active) {
    return events.filter(
        (event) => event.type === 'neko:home-tutorial-features-suppressed'
            && event.detail.active === active
    );
}

test('tutorial avatar reload stops proactive runtime without rewriting settings memory', async () => {
    const window = loadReloadControllerWindow();
    const events = attachEventRecorder(window);
    const calls = [];
    const host = {
        constructor: {
            detectModelPrefix() {
                return 'live2d';
            }
        }
    };
    const controller = window.TutorialAvatarReloadController.createController({
        host,
        timeoutMs: 200,
        resolveCurrentName: () => Promise.resolve('LanLan'),
        fetchCharacters: () => Promise.resolve({
            '猫娘': {
                LanLan: {
                    model_type: 'live2d',
                    live2d: 'lanlan'
                }
            }
        }),
        buildSnapshotPayload: () => ({ model_type: 'live2d', live2d: 'lanlan' }),
        reloadModel: (name, payload, options) => {
            calls.push({ type: 'reload', name, payload, options });
            return Promise.resolve();
        },
        setPreparing: (value) => calls.push({ type: 'preparing', value }),
        revealPrepared: () => calls.push({ type: 'reveal' }),
        applyIdentityOverride: (payload) => calls.push({ type: 'identity', payload }),
        clearViewportWatcher: () => calls.push({ type: 'clearViewport' })
    });

    window.releaseProactiveVisionStream = function () {
        assert.equal(controller.isProactiveRuntimeHeld(), true);
        assert.equal(suppressionEvents(events, true).length, 1);
        this.stopCalls.push('stream');
    };
    await controller.beginOverride();

    // 标志与事件先于流释放生效，但设置内存一个字都不许改：
    // 这些值由 saveSettings 持久化并跨窗口同步，改写即污染。
    assert.deepEqual(window.stopCalls, ['chat', 'vision', 'stream']);
    assert.equal(controller.isProactiveRuntimeHeld(), true);
    assert.equal(suppressionEvents(events, true).length, 1);
    assert.equal(window.proactiveChatEnabled, true);
    assert.equal(window.appState.proactiveChatEnabled, true);
    assert.equal(window.proactiveVisionChatEnabled, true);
    assert.equal(window.appState.proactiveVisionChatEnabled, true);
    assert.equal(window.proactiveNewsChatEnabled, false);
    assert.equal(window.appState.proactiveNewsChatEnabled, false);

    await controller.restoreOverride();

    // 释放：标志复位、事件派发；没有设置值需要恢复，控制器也不再自己
    // 排程（重排/补挂由 app-proactive 的释放事件监听负责）。
    assert.equal(controller.isProactiveRuntimeHeld(), false);
    assert.equal(suppressionEvents(events, false).length, 1);
    assert.equal(window.proactiveChatEnabled, true);
    assert.equal(window.appState.proactiveChatEnabled, true);
    assert.equal(window.scheduleCalls, 0);
    assert.equal(calls.filter((call) => call.type === 'reload').length, 2);
});

test('tutorial avatar reload keeps the runtime hold until the model restore settles', async () => {
    const window = loadReloadControllerWindow();
    const events = attachEventRecorder(window);
    let releaseModel;
    const modelGate = new Promise((resolve) => { releaseModel = resolve; });
    let modelCalls = 0;
    const controller = window.TutorialAvatarReloadController.createController({
        host: {
            constructor: {
                detectModelPrefix() {
                    return 'live2d';
                }
            }
        },
        timeoutMs: 5000,
        resolveCurrentName: () => Promise.resolve('LanLan'),
        fetchCharacters: () => Promise.resolve({
            '猫娘': {
                LanLan: {
                    model_type: 'live2d',
                    live2d: 'lanlan'
                }
            }
        }),
        buildSnapshotPayload: () => ({ model_type: 'live2d', live2d: 'lanlan' }),
        // 第一次调用是换入教程模型：立即完成；第二次是恢复用户模型：受控。
        reloadModel: () => {
            modelCalls += 1;
            return modelCalls === 1 ? Promise.resolve() : modelGate;
        },
        setPreparing: () => {},
        revealPrepared: () => {},
        applyIdentityOverride: () => {},
        clearViewportWatcher: () => {}
    });

    await controller.beginOverride();
    assert.equal(controller.isProactiveRuntimeHeld(), true);

    const restorePromise = controller.restoreOverride();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    // 模型恢复中的窗口：feature controller 的 end 可能已先行放行，本层
    // hold 必须撑到恢复完成——触发闸门靠它拦住对临时模型开口的定时器。
    assert.equal(controller.isProactiveRuntimeHeld(), true);
    assert.equal(suppressionEvents(events, false).length, 0);

    releaseModel();
    await restorePromise;

    assert.equal(controller.isProactiveRuntimeHeld(), false);
    assert.equal(suppressionEvents(events, false).length, 1);
    // 全程设置内存零改写。
    assert.equal(window.proactiveChatEnabled, true);
    assert.equal(window.appState.proactiveChatEnabled, true);
});

test('tutorial avatar reload registers its own hold while the feature controller is active', async () => {
    const window = loadReloadControllerWindow();
    const events = attachEventRecorder(window);
    window.NekoHomeTutorialFeatureController = {
        isActive() {
            return true;
        }
    };

    const controller = window.TutorialAvatarReloadController.createController({
        host: {
            constructor: {
                detectModelPrefix() {
                    return 'live2d';
                }
            }
        },
        timeoutMs: 200,
        resolveCurrentName: () => Promise.resolve('LanLan'),
        fetchCharacters: () => Promise.resolve({
            '猫娘': {
                LanLan: {
                    model_type: 'live2d',
                    live2d: 'lanlan'
                }
            }
        }),
        buildSnapshotPayload: () => ({ model_type: 'live2d', live2d: 'lanlan' }),
        reloadModel: () => Promise.resolve(),
        setPreparing: () => {},
        revealPrepared: () => {},
        applyIdentityOverride: () => {},
        clearViewportWatcher: () => {}
    });

    await controller.beginOverride();

    // feature controller 已激活时本层也登记自己的 hold：controller 的 end
    // 先于模型恢复完成，那段窗口由本层的标志继续守住触发闸门。
    assert.equal(controller.isProactiveRuntimeHeld(), true);
    assert.equal(window.proactiveChatEnabled, true);
    assert.equal(window.appState.proactiveChatEnabled, true);

    await controller.restoreOverride();

    assert.equal(controller.isProactiveRuntimeHeld(), false);
    assert.equal(suppressionEvents(events, false).length, 1);
    assert.equal(window.proactiveChatEnabled, true);
    assert.equal(window.appState.proactiveChatEnabled, true);
});

test('tutorial avatar reload can keep prepared model hidden for intro performance reveal', async () => {
    const window = loadReloadControllerWindow();
    const reloadCalls = [];
    const revealCalls = [];
    const host = {
        constructor: {
            detectModelPrefix() {
                return 'live2d';
            }
        }
    };
    const controller = window.TutorialAvatarReloadController.createController({
        host,
        timeoutMs: 200,
        resolveCurrentName: () => Promise.resolve('LanLan'),
        fetchCharacters: () => Promise.resolve({
            '猫娘': {
                LanLan: {
                    model_type: 'live2d',
                    live2d: 'lanlan'
                }
            }
        }),
        buildSnapshotPayload: () => ({ model_type: 'live2d', live2d: 'lanlan' }),
        reloadModel: (name, payload, options) => {
            reloadCalls.push({ name, payload, options });
            return Promise.resolve();
        },
        setPreparing: () => {},
        revealPrepared: () => revealCalls.push('reveal'),
        applyIdentityOverride: () => {},
        clearViewportWatcher: () => {}
    });

    await controller.beginOverride({ deferRevealPrepared: true });

    assert.equal(reloadCalls.length, 1);
    assert.equal(reloadCalls[0].options.deferRevealPrepared, true);
    assert.deepEqual(revealCalls, []);
});

test('tutorial avatar reload fades out current model before preparing hide', async () => {
    const window = loadReloadControllerWindow();
    const calls = [];
    const host = {
        constructor: {
            detectModelPrefix() {
                return 'live2d';
            }
        }
    };
    const controller = window.TutorialAvatarReloadController.createController({
        host,
        timeoutMs: 200,
        resolveCurrentName: () => Promise.resolve('LanLan'),
        fetchCharacters: () => Promise.resolve({
            '猫娘': {
                LanLan: {
                    model_type: 'live2d',
                    live2d: 'lanlan'
                }
            }
        }),
        buildSnapshotPayload: () => ({ model_type: 'live2d', live2d: 'lanlan' }),
        fadeOutCurrentModel: () => {
            calls.push({ type: 'fadeOut' });
            return Promise.resolve();
        },
        reloadModel: () => {
            calls.push({ type: 'reload' });
            return Promise.resolve();
        },
        setPreparing: (value) => calls.push({ type: 'preparing', value }),
        revealPrepared: () => {},
        applyIdentityOverride: () => {},
        clearViewportWatcher: () => {}
    });

    await controller.beginOverride({ deferRevealPrepared: true });

    assert.deepEqual(calls.slice(0, 3), [
        { type: 'fadeOut' },
        { type: 'preparing', value: true },
        { type: 'reload' }
    ]);
});
