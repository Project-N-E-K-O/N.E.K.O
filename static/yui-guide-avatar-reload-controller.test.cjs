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

test('tutorial avatar reload snapshots proactive chat and restores it after model restore', async () => {
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
        reloadModel: (name, payload, options) => {
            calls.push({ type: 'reload', name, payload, options });
            return Promise.resolve();
        },
        setPreparing: (value) => calls.push({ type: 'preparing', value }),
        revealPrepared: () => calls.push({ type: 'reveal' }),
        applyIdentityOverride: (payload) => calls.push({ type: 'identity', payload }),
        clearViewportWatcher: () => calls.push({ type: 'clearViewport' })
    });

    await controller.beginOverride();

    assert.equal(window.proactiveChatEnabled, false);
    assert.equal(window.appState.proactiveChatEnabled, false);
    assert.equal(window.proactiveVisionChatEnabled, false);
    assert.equal(window.appState.proactiveVisionChatEnabled, false);
    assert.deepEqual(window.stopCalls, ['chat', 'vision', 'stream']);

    await controller.restoreOverride();

    assert.equal(window.proactiveChatEnabled, true);
    assert.equal(window.appState.proactiveChatEnabled, true);
    assert.equal(window.proactiveVisionChatEnabled, true);
    assert.equal(window.appState.proactiveVisionChatEnabled, true);
    assert.equal(window.proactiveNewsChatEnabled, false);
    assert.equal(window.appState.proactiveNewsChatEnabled, false);
    assert.equal(window.scheduleCalls, 1);
    assert.equal(calls.filter((call) => call.type === 'reload').length, 2);
});

test('tutorial avatar reload snapshots proactive chat when override starts, not when constructed', async () => {
    const window = loadReloadControllerWindow();
    window.proactiveChatEnabled = false;
    window.appState.proactiveChatEnabled = false;
    window.proactiveVisionChatEnabled = false;
    window.appState.proactiveVisionChatEnabled = false;

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
        resolveCurrentName: () => {
            window.proactiveChatEnabled = true;
            window.appState.proactiveChatEnabled = true;
            window.proactiveVisionChatEnabled = true;
            window.appState.proactiveVisionChatEnabled = true;
            return Promise.resolve('LanLan');
        },
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
    await controller.restoreOverride();

    assert.equal(window.proactiveChatEnabled, true);
    assert.equal(window.appState.proactiveChatEnabled, true);
    assert.equal(window.proactiveVisionChatEnabled, true);
    assert.equal(window.appState.proactiveVisionChatEnabled, true);
    assert.equal(window.scheduleCalls, 1);
});

test('tutorial avatar reload snapshots persisted user truth when memory is already suppressed', async () => {
    const window = loadReloadControllerWindow();
    // 另一层抑制（或历史污染）已把内存关死；持久化设置仍保留用户真值。
    window.proactiveChatEnabled = false;
    window.appState.proactiveChatEnabled = false;
    window.proactiveVisionChatEnabled = false;
    window.appState.proactiveVisionChatEnabled = false;
    window.localStorage = {
        getItem(key) {
            if (key !== 'project_neko_settings') return null;
            return JSON.stringify({
                proactiveChatEnabled: true,
                proactiveVisionEnabled: true,
                proactiveVisionChatEnabled: true,
                proactiveNewsChatEnabled: false,
                proactiveVideoChatEnabled: true,
                proactivePersonalChatEnabled: false,
                proactiveMusicEnabled: true,
                proactiveMemeEnabled: false,
                proactiveMiniGameInviteEnabled: true
            });
        }
    };
    const saveCalls = [];
    window.appSettings = {
        saveSettings(options) {
            saveCalls.push(options || null);
        }
    };

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
        reloadModel: () => Promise.resolve(),
        setPreparing: () => {},
        revealPrepared: () => {},
        applyIdentityOverride: () => {},
        clearViewportWatcher: () => {}
    });

    await controller.beginOverride();

    assert.equal(window.proactiveChatEnabled, false);
    assert.equal(window.appState.proactiveChatEnabled, false);
    assert.deepEqual(saveCalls, []);

    await controller.restoreOverride();

    // 恢复的是持久化用户真值（开），而不是 beginOverride 时的内存抑制态（关）。
    assert.equal(window.proactiveChatEnabled, true);
    assert.equal(window.appState.proactiveChatEnabled, true);
    assert.equal(window.proactiveVisionChatEnabled, true);
    assert.equal(window.appState.proactiveVisionChatEnabled, true);
    assert.equal(window.scheduleCalls, 1);
    // 恢复不再回写落盘：无条件回写会把兄弟窗口在教程期间的并发改动
    // 回滚成 begin 时的旧快照、并被标成显式修改扩散（review P2）；
    // 落盘护栏已保证持久层在抑制期间不会被污染，无需恢复时纠正。
    assert.deepEqual(saveCalls, []);
});

test('tutorial avatar reload restores newer accepted values over the begin snapshot', async () => {
    const window = loadReloadControllerWindow();
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
        reloadModel: () => Promise.resolve(),
        setPreparing: () => {},
        revealPrepared: () => {},
        applyIdentityOverride: () => {},
        clearViewportWatcher: () => {}
    });

    await controller.beginOverride();
    // begin 快照里 proactiveNewsChatEnabled=false。教程期间兄弟窗口把它打开、
    // 经本窗口出处校验接受（进入接受真值登记表与持久层）——用 appSettings
    // 的更新后真值链模拟；恢复必须用新值，而不是旧快照。
    window.appSettings = {
        getProactiveUserTruth() {
            return { proactiveNewsChatEnabled: true };
        }
    };
    assert.equal(window.proactiveNewsChatEnabled, false);

    await controller.restoreOverride();

    assert.equal(window.proactiveNewsChatEnabled, true);
    assert.equal(window.appState.proactiveNewsChatEnabled, true);
    // 真值链没有新值的键仍按 begin 快照恢复。
    assert.equal(window.proactiveChatEnabled, true);
    assert.equal(window.appState.proactiveChatEnabled, true);
    assert.equal(window.scheduleCalls, 1);
});

test('tutorial avatar reload reports proactive suppression only after memory is disabled', async () => {
    const window = loadReloadControllerWindow();
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
    let releaseSetup;
    const setupGate = new Promise((resolve) => { releaseSetup = resolve; });
    const host = {
        constructor: {
            detectModelPrefix() {
                return 'live2d';
            }
        }
    };
    const controller = window.TutorialAvatarReloadController.createController({
        host,
        timeoutMs: 5000,
        resolveCurrentName: () => setupGate.then(() => 'LanLan'),
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

    const beginPromise = controller.beginOverride();

    // override 的异步 setup 窗口期：override 已建、内存尚未关闭。
    // 此窗口内 app-settings 的持久化护栏绝不能生效（否则会用旧本地值
    // 压掉 boot merge 刚合入的服务器新值并上行）。
    assert.equal(controller.hasActiveOverride(), true);
    assert.equal(controller.isProactiveSuppressed(), false);
    assert.equal(controller.getProactiveUserValues(), null);
    assert.equal(window.proactiveChatEnabled, true);

    releaseSetup();
    await beginPromise;

    assert.equal(controller.isProactiveSuppressed(), true);
    assert.equal(window.proactiveChatEnabled, false);
    // 抑制开始必须派发事件：情境弹窗据此撤回展示中的弹窗，app-proactive
    // 的事件标志同步（与 feature controller 共用事件面）。
    const suppressEvents = events.filter((event) => event.type === 'neko:home-tutorial-features-suppressed');
    assert.equal(suppressEvents.length, 1);
    assert.equal(suppressEvents[0].detail.active, true);
    // 抑制期间暴露 begin 时的用户真值，供持久化护栏在持久层缺键时回退。
    const userValues = controller.getProactiveUserValues();
    assert.equal(userValues && userValues.proactiveChatEnabled, true);

    await controller.restoreOverride();

    assert.equal(controller.isProactiveSuppressed(), false);
    assert.equal(controller.getProactiveUserValues(), null);
    assert.equal(window.proactiveChatEnabled, true);
    // 释放事件在模型恢复完成后派发：被 feature controller 收口提前重放、
    // 又因本层仍抑制而重新入队的情境弹窗，靠这条补上重放。
    const releaseEvents = events.filter(
        (event) => event.type === 'neko:home-tutorial-features-suppressed'
            && event.detail.active === false
    );
    assert.equal(releaseEvents.length, 1);
});

test('tutorial avatar reload does not overwrite proactive state restored by the tutorial lifecycle', async () => {
    const window = loadReloadControllerWindow();
    window.proactiveChatEnabled = false;
    window.appState.proactiveChatEnabled = false;
    window.proactiveVisionChatEnabled = false;
    window.appState.proactiveVisionChatEnabled = false;
    window.NekoHomeTutorialFeatureController = {
        isActive() {
            return true;
        }
    };

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
        reloadModel: () => Promise.resolve(),
        setPreparing: () => {},
        revealPrepared: () => {},
        applyIdentityOverride: () => {},
        clearViewportWatcher: () => {}
    });

    await controller.beginOverride();

    // Skip/angry-exit tears down the feature controller before the model restore.
    window.proactiveChatEnabled = true;
    window.appState.proactiveChatEnabled = true;
    window.proactiveVisionChatEnabled = true;
    window.appState.proactiveVisionChatEnabled = true;
    window.NekoHomeTutorialFeatureController = null;

    await controller.restoreOverride();

    assert.equal(window.proactiveChatEnabled, true);
    assert.equal(window.appState.proactiveChatEnabled, true);
    assert.equal(window.proactiveVisionChatEnabled, true);
    assert.equal(window.appState.proactiveVisionChatEnabled, true);
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
