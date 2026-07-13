const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const { directorScriptNames } = require('./yui-guide-director-test-parts.cjs');

function createStorage() {
    const values = new Map();
    return {
        getItem(key) {
            return values.has(key) ? values.get(key) : null;
        },
        setItem(key, value) {
            values.set(key, String(value));
        },
        removeItem(key) {
            values.delete(key);
        }
    };
}

function createHarness() {
    const roundCalls = [];
    const sceneCalls = [];
    const handoffCalls = [];
    const document = {
        body: {
            classList: { add() {}, remove() {} }
        },
        documentElement: { lang: 'en' },
        getElementById() {
            return null;
        },
        querySelector() {
            return null;
        },
        querySelectorAll() {
            return [];
        }
    };
    const noOpController = class {
        constructor() {}
    };
    const window = {
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() {},
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        innerWidth: 1280,
        innerHeight: 720,
        localStorage: createStorage(),
        sessionStorage: createStorage(),
        location: {
            href: 'http://localhost/',
            origin: 'http://localhost',
            pathname: '/',
            protocol: 'http:',
            host: 'localhost'
        },
        TutorialVisualControllers: {
            createHighlightController() {
                return {};
            },
            GhostCursorController: noOpController,
            YuiGuideGhostCursor: noOpController,
            SpotlightController: noOpController,
            AvatarStandInController: class {
                getCue() {
                    return null;
                }
                schedule() {}
                clear() {}
            },
            PetalTransitionController: noOpController
        },
        TutorialResistanceControllers: {
            ResistanceController: noOpController,
            SidebarPauseController: class {
                getPauseToken() {
                    return {};
                }
            },
            PauseCoordinator: class {
                registerPauseToken() {}
            },
            TutorialTerminationRouter: noOpController
        },
        TutorialOperationRegistry: {
            OperationRegistry: class {
                async run(...args) {
                    return args;
                }
            }
        },
        TutorialSceneOrchestrator: {
            SceneOrchestrator: class {
                async playRound(...args) {
                    roundCalls.push(args);
                    return 'round-complete';
                }
                async playScene(...args) {
                    sceneCalls.push(args);
                    return 'scene-complete';
                }
            }
        },
        TutorialSettingsTourFlow: {
            SettingsTourFlow: noOpController
        },
        YuiGuideCommon: {
            createTutorialBridgeCommandBus() {
                return { readQueue() { return []; }, enqueue() {}, post() { return true; } };
            },
            createTutorialTargetGeometryRegistry() {
                return {};
            },
            createChatWindowAdapter() {
                return {};
            },
            createScopedTutorialResources() {
                return { clear() {}, destroy() {} };
            }
        },
        YuiGuideOverlay: noOpController
    };
    const context = vm.createContext({
        console,
        document,
        window,
        navigator: { platform: 'Win32', userAgent: 'node-yui-guide-harness' },
        CustomEvent: class {
            constructor(type, options) {
                this.type = type;
                this.detail = options && options.detail;
            }
        },
        AbortController,
        URL,
        fetch: async () => ({ ok: false }),
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval
    });
    window.window = window;
    window.document = document;
    window.navigator = context.navigator;
    window.getYuiGuideHomeInteractionApi = () => ({
        async openPageWithHandoff(...args) {
            handoffCalls.push(args);
            return true;
        }
    });

    for (const relativePath of directorScriptNames) {
        const filename = path.join(__dirname, relativePath);
        vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
    }

    return { context, handoffCalls, roundCalls, sceneCalls, window };
}

test('director parts load in dependency order and preserve class prototype descriptors', () => {
    const { window } = createHarness();
    assert.equal(typeof window.createYuiGuideDirector, 'function');
    assert.equal(typeof window.__YuiGuideDirector.YuiGuideDirector, 'function');

    for (const methodName of ['playAvatarFloatingRound', 'playAvatarFloatingScene', 'callHomeInteractionApi', 'destroy']) {
        const descriptor = Object.getOwnPropertyDescriptor(
            window.__YuiGuideDirector.YuiGuideDirector.prototype,
            methodName
        );
        assert.ok(descriptor, `${methodName} should be installed`);
        assert.equal(descriptor.enumerable, false);
        assert.equal(descriptor.configurable, true);
        assert.equal(descriptor.writable, true);
    }
});

test('director Node harness starts a round and advances a scene without requestAnimationFrame', async () => {
    const { roundCalls, sceneCalls, window } = createHarness();
    const director = window.createYuiGuideDirector({ page: 'settings' });
    const scene = { id: 'node-harness-scene' };

    assert.equal(await director.playAvatarFloatingRound(3, { source: 'node-harness' }), 'round-complete');
    assert.equal(await director.playAvatarFloatingScene(scene, 3, 0, 1, { source: 'node-harness' }), 'scene-complete');
    assert.deepEqual(roundCalls, [[3, { source: 'node-harness' }]]);
    assert.deepEqual(sceneCalls, [[scene, 3, 0, 1, { source: 'node-harness' }]]);
});

test('director Node harness keeps cross-page handoff routed through the existing page API', async () => {
    const { handoffCalls, window } = createHarness();
    const director = window.createYuiGuideDirector({ page: 'settings' });

    assert.equal(
        await director.callHomeInteractionApi('openPageWithHandoff', ['memory_browser', { token: 'node-harness' }]),
        true
    );
    assert.deepEqual(handoffCalls, [['memory_browser', { token: 'node-harness' }]]);
});
