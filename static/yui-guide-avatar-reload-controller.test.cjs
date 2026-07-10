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
