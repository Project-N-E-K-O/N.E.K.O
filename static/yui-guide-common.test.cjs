const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const guideHelpers = require('./tutorial/core/guide-helpers.js');
const scopedResources = require('./tutorial/core/scoped-resources.js');
const bridgeCommandBus = require('./tutorial/core/bridge-command-bus.js');
const targetGeometryRegistry = require('./tutorial/core/target-geometry-registry.js');
const chatWindowAdapter = require('./tutorial/core/chat-window-adapter.js');
const commandRegistry = require('./tutorial/core/command-registry.js');
const scriptNormalizer = require('./tutorial/core/script-normalizer.js');
const timelineEngine = require('./tutorial/core/timeline-engine.js');
const visualRuntime = require('./tutorial/core/visual-runtime.js');
const ghostCursorController = require('./tutorial/visual/ghost-cursor-controller.js');
const common = require('./tutorial/yui-guide/common.js');
const repoRoot = path.resolve(__dirname, '..');
const dayGuideFiles = [
    'tutorial/yui-guide/days/day1-home-guide.js',
    'tutorial/yui-guide/days/day2-screen-voice-guide.js',
    'tutorial/yui-guide/days/day3-interaction-guide.js',
    'tutorial/yui-guide/days/day4-companion-guide.js',
    'tutorial/yui-guide/days/day5-personalization-guide.js',
    'tutorial/yui-guide/days/day6-agent-guide.js'
];

test('common guide helpers freeze config, register guides, and create locale audio maps', () => {
    const win = {};
    const nested = { day: 3, child: { label: 'x' } };

    assert.equal(common.deepFreeze(nested), nested);
    assert.equal(Object.isFrozen(nested), true);
    assert.equal(Object.isFrozen(nested.child), true);

    common.registerGuide(nested, { window: win, day1Alias: true });
    assert.equal(win.YuiGuideDailyGuides[3], nested);
    assert.equal(win.YuiGuideDay1HomeGuide, nested);

    assert.deepEqual(common.audioFilesForAllLocales('line.mp3'), {
        zh: 'line.mp3',
        ja: 'line.mp3',
        en: 'line.mp3',
        ko: 'line.mp3',
        ru: 'line.mp3'
    });
});

test('guide helpers are exported from a standalone module and re-exported by common', () => {
    const helpersSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/guide-helpers.js'), 'utf8');
    const commonSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/common.js'), 'utf8');

    assert.equal(typeof guideHelpers.deepFreeze, 'function');
    assert.equal(typeof guideHelpers.registerGuide, 'function');
    assert.equal(typeof guideHelpers.audioFilesForAllLocales, 'function');
    assert.equal(typeof common.deepFreeze, 'function');
    assert.equal(typeof common.registerGuide, 'function');
    assert.equal(typeof common.audioFilesForAllLocales, 'function');
    assert.match(helpersSource, /root\.TutorialGuideHelpers = api/);
    assert.match(helpersSource, /function deepFreeze\(value\)/);
    assert.match(helpersSource, /function registerGuide\(config, options\)/);
    assert.match(helpersSource, /function audioFilesForAllLocales\(fileName\)/);
    assert.match(commonSource, /require\('\.\.\/core\/guide-helpers\.js'\)/);
    assert.match(commonSource, /tutorialGuideHelpersApi\.deepFreeze\(value\)/);
    assert.match(commonSource, /tutorialGuideHelpersApi\.registerGuide\(config, options\)/);
    assert.match(commonSource, /tutorialGuideHelpersApi\.audioFilesForAllLocales\(fileName\)/);
    assert.doesNotMatch(commonSource, /Object\.keys\(value\)\.forEach/);
    assert.doesNotMatch(commonSource, /YuiGuideDailyGuides \|\| \{\}/);
});

test('scoped tutorial resources are exported from a standalone module and re-exported by common', () => {
    const scopedSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/scoped-resources.js'), 'utf8');
    const commonSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/common.js'), 'utf8');

    assert.equal(typeof scopedResources.createScopedTutorialResources, 'function');
    assert.equal(typeof common.createScopedTutorialResources, 'function');
    assert.match(scopedSource, /root\.TutorialScopedResources = api/);
    assert.match(scopedSource, /function createScopedTutorialResources\(options\)/);
    assert.match(commonSource, /require\('\.\.\/core\/scoped-resources\.js'\)/);
    assert.match(commonSource, /tutorialScopedResourcesApi\.createScopedTutorialResources\(options\)/);
    assert.doesNotMatch(commonSource, /const animationFrames = \[\];/);
});

test('tutorial bridge command bus is exported from a standalone module and re-exported by common', () => {
    const bridgeSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/bridge-command-bus.js'), 'utf8');
    const commonSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/common.js'), 'utf8');

    assert.equal(typeof bridgeCommandBus.createTutorialBridgeCommandBus, 'function');
    assert.equal(typeof common.createTutorialBridgeCommandBus, 'function');
    assert.match(bridgeSource, /root\.TutorialBridgeCommandBus = api/);
    assert.match(bridgeSource, /function createTutorialBridgeCommandBus\(options\)/);
    assert.match(bridgeSource, /DEFAULT_BRIDGE_QUEUE_KEY/);
    assert.match(commonSource, /require\('\.\.\/core\/bridge-command-bus\.js'\)/);
    assert.match(commonSource, /tutorialBridgeCommandBusApi\.createTutorialBridgeCommandBus\(options\)/);
    assert.doesNotMatch(commonSource, /DEFAULT_BRIDGE_QUEUE_KEY/);
    assert.doesNotMatch(commonSource, /function normalizeBridgeMessage\(message/);
});

test('tutorial bridge command bus restores onmessage fallback on unsubscribe', () => {
    const calls = [];
    const previousHandler = (event) => calls.push(['previous', event.data.action]);
    const channel = { onmessage: previousHandler };
    const bus = bridgeCommandBus.createTutorialBridgeCommandBus({
        channelProvider: () => channel
    });

    const unsubscribe = bus.on('guide-action', (message) => {
        calls.push(['listener', message.action]);
    });
    assert.notEqual(channel.onmessage, previousHandler);

    channel.onmessage({ data: { action: 'guide-action' } });
    unsubscribe();

    assert.equal(channel.onmessage, previousHandler);
    channel.onmessage({ data: { action: 'guide-action' } });
    assert.deepEqual(calls, [
        ['previous', 'guide-action'],
        ['listener', 'guide-action'],
        ['previous', 'guide-action']
    ]);
});

test('target geometry registry is exported from a standalone module and re-exported by common', () => {
    const registrySource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/target-geometry-registry.js'), 'utf8');
    const commonSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/common.js'), 'utf8');
    const chatAvatarToolsRegistryBlock = registrySource.split("'chat-avatar-tools': Object.freeze({")[1].split(
        "'chat-avatar-tool-items': Object.freeze({",
        1
    )[0];
    const chatAvatarToolItemsRegistryBlock = registrySource.split("'chat-avatar-tool-items': Object.freeze({")[1].split(
        "'chat-galgame': Object.freeze({",
        1
    )[0];

    assert.equal(typeof targetGeometryRegistry.createTutorialTargetGeometryRegistry, 'function');
    assert.equal(typeof common.createTutorialTargetGeometryRegistry, 'function');
    assert.match(registrySource, /root\.TutorialTargetGeometryRegistry = api/);
    assert.match(registrySource, /function createTutorialTargetGeometryRegistry\(options\)/);
    assert.match(registrySource, /DEFAULT_TARGET_GEOMETRY_ENTRIES/);
    assert.match(registrySource, /'chat-capsule-input'/);
    assert.match(chatAvatarToolsRegistryBlock, /\.compact-input-tool-item-avatar > \.composer-emoji-btn/);
    assert.doesNotMatch(chatAvatarToolsRegistryBlock, /\.composer-icon-button\[data-avatar-tool-id\]/);
    assert.match(chatAvatarToolItemsRegistryBlock, /#composer-tool-popover-compact \.composer-icon-button\[data-avatar-tool-id\]/);
    assert.match(chatAvatarToolItemsRegistryBlock, /#composer-avatar-tool-quickbar \.composer-icon-button\[data-avatar-tool-id\]/);
    assert.match(commonSource, /require\('\.\.\/core\/target-geometry-registry\.js'\)/);
    assert.match(commonSource, /tutorialTargetGeometryRegistryApi\.createTutorialTargetGeometryRegistry\(options\)/);
    assert.doesNotMatch(commonSource, /DEFAULT_TARGET_GEOMETRY_ENTRIES/);
    assert.doesNotMatch(commonSource, /function cloneTargetGeometryEntry\(entry\)/);
});

test('chat window adapter is exported from a standalone module and re-exported by common', () => {
    const adapterSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/chat-window-adapter.js'), 'utf8');
    const commonSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/common.js'), 'utf8');

    assert.equal(typeof chatWindowAdapter.createReactChatTutorialHostAdapter, 'function');
    assert.equal(typeof chatWindowAdapter.createChatWindowAdapter, 'function');
    assert.equal(typeof common.createReactChatTutorialHostAdapter, 'function');
    assert.equal(typeof common.createChatWindowAdapter, 'function');
    assert.match(adapterSource, /root\.TutorialChatWindowAdapter = api/);
    assert.match(adapterSource, /function createReactChatTutorialHostAdapter\(options\)/);
    assert.match(adapterSource, /function createChatWindowAdapter\(options\)/);
    assert.match(adapterSource, /rotateExternalizedChatCompactToolWheel/);
    assert.match(commonSource, /require\('\.\.\/core\/chat-window-adapter\.js'\)/);
    assert.match(commonSource, /tutorialChatWindowAdapterApi\.createReactChatTutorialHostAdapter\(options\)/);
    assert.match(commonSource, /tutorialChatWindowAdapterApi\.createChatWindowAdapter\(options\)/);
    assert.doesNotMatch(commonSource, /function callHost\(methodName, args\)/);
    assert.doesNotMatch(commonSource, /rotateExternalizedChatCompactToolWheel/);
});

test('timeline command modules are exported from standalone modules and re-exported by common', () => {
    const commandSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/command-registry.js'), 'utf8');
    const normalizerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/script-normalizer.js'), 'utf8');
    const engineSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/timeline-engine.js'), 'utf8');
    const visualRuntimeSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/visual-runtime.js'), 'utf8');
    const commonSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/common.js'), 'utf8');

    assert.equal(typeof commandRegistry.createTutorialCommandRegistry, 'function');
    assert.equal(typeof scriptNormalizer.normalizeTutorialScene, 'function');
    assert.equal(typeof timelineEngine.createTutorialTimelineEngine, 'function');
    assert.equal(typeof visualRuntime.createTutorialVisualRuntime, 'function');
    assert.equal(typeof common.createTutorialCommandRegistry, 'function');
    assert.equal(typeof common.normalizeTutorialScene, 'function');
    assert.equal(typeof common.createTutorialTimelineEngine, 'function');
    assert.equal(typeof common.createTutorialVisualRuntime, 'function');
    assert.match(commandSource, /root\.TutorialCommandRegistry = api/);
    assert.match(normalizerSource, /root\.TutorialScriptNormalizer = api/);
    assert.match(engineSource, /root\.TutorialTimelineEngine = api/);
    assert.match(visualRuntimeSource, /root\.TutorialVisualRuntime = api/);
    assert.match(commonSource, /require\('\.\.\/core\/command-registry\.js'\)/);
    assert.match(commonSource, /require\('\.\.\/core\/script-normalizer\.js'\)/);
    assert.match(commonSource, /require\('\.\.\/core\/timeline-engine\.js'\)/);
    assert.match(commonSource, /require\('\.\.\/core\/visual-runtime\.js'\)/);
});

test('ghost cursor can use exact timeline durations without display slowdown', async () => {
    const moves = [];
    const cursor = new ghostCursorController.GhostCursorController({
        overlay: {
            moveCursorTo(x, y, options) {
                moves.push({ x, y, durationMs: options.durationMs });
                return Promise.resolve(true);
            }
        }
    });

    await cursor.moveToPoint(10, 20, { durationMs: 760 });
    await cursor.moveToPoint(30, 40, { durationMs: 760, exactDuration: true });

    assert.ok(moves[0].durationMs > 760);
    assert.deepEqual(moves[1], { x: 30, y: 40, durationMs: 760 });
});

test('scoped tutorial resources remove listeners and clear timers on destroy', () => {
    const calls = [];
    const timerCalls = [];
    const fakeTarget = {
        addEventListener(type, handler, options) {
            calls.push(['add', type, handler, options]);
        },
        removeEventListener(type, handler, options) {
            calls.push(['remove', type, handler, options]);
        }
    };
    const fakeWindow = {
        setTimeout(callback, delayMs) {
            timerCalls.push(['set', callback, delayMs]);
            return 42;
        },
        clearTimeout(timerId) {
            timerCalls.push(['clear', timerId]);
        },
        setInterval(callback, delayMs) {
            timerCalls.push(['setInterval', callback, delayMs]);
            return 77;
        },
        clearInterval(timerId) {
            timerCalls.push(['clearInterval', timerId]);
        },
        requestAnimationFrame(callback) {
            timerCalls.push(['requestAnimationFrame', callback]);
            return 88;
        },
        cancelAnimationFrame(frameId) {
            timerCalls.push(['cancelAnimationFrame', frameId]);
        }
    };
    const resources = common.createScopedTutorialResources({ window: fakeWindow });
    const handler = () => {};
    const options = { capture: true };

    resources.addEventListener(fakeTarget, 'click', handler, options);
    resources.setTimeout(() => {}, 120);
    resources.setInterval(() => {}, 240);
    resources.requestAnimationFrame(() => {});
    resources.destroy();
    resources.destroy();

    assert.deepEqual(calls, [
        ['add', 'click', handler, options],
        ['remove', 'click', handler, options]
    ]);
    assert.deepEqual(timerCalls, [
        ['set', timerCalls[0][1], 120],
        ['setInterval', timerCalls[1][1], 240],
        ['requestAnimationFrame', timerCalls[2][1]],
        ['cancelAnimationFrame', 88],
        ['clearInterval', 77],
        ['clear', 42]
    ]);
});

test('tutorial bridge command bus queues and posts valid command messages', () => {
    const storage = new Map();
    const postedMessages = [];
    const relayedMessages = [];
    const petRelays = [];
    const warnings = [];
    const listeners = [];
    const fakeWindow = {
        Date: {
            now() {
                return 123456;
            }
        },
        localStorage: {
            getItem(key) {
                return storage.has(key) ? storage.get(key) : null;
            },
            setItem(key, value) {
                storage.set(key, value);
            },
            removeItem(key) {
                storage.delete(key);
            }
        },
        console: {
            warn(...args) {
                warnings.push(args);
            }
        }
    };
    fakeWindow.localStorage.setItem('yuiGuidePcOverlayRunId', 'run-7');
    const bus = common.createTutorialBridgeCommandBus({
        window: fakeWindow,
        storageKey: 'queue-key',
        queueLimit: 3,
        nativeRelayProvider: () => ({
            relayToChat(message) {
                relayedMessages.push(message);
            },
            relayToPet(message) {
                petRelays.push(message);
            }
        }),
        channelProvider: () => ({
            postMessage(message) {
                postedMessages.push(message);
            },
            addEventListener(type, handler) {
                listeners.push(['add', type, handler]);
            },
            removeEventListener(type, handler) {
                listeners.push(['remove', type, handler]);
            }
        })
    });

    assert.equal(bus.post({ action: 'yui_guide_append_chat_message', id: 'a' }), true);
    assert.equal(bus.post({
        action: 'yui_guide_update_chat_message',
        id: 'b',
        tutorialRunId: 'custom'
    }, { bypassDedup: true }), true);
    assert.equal(bus.post({ action: 'yui_guide_clear_chat_messages', id: 'c' }), true);
    assert.equal(bus.post({ action: 'tutorial_chat_identity_override', id: 'identity', active: true }), true);
    assert.equal(bus.post({ id: 'invalid' }), false);

    const queue = JSON.parse(storage.get('queue-key'));
    assert.deepEqual(queue.map((message) => message.id), ['b', 'c', 'identity']);
    assert.equal(queue[0].tutorialRunId, 'custom');
    assert.equal(queue[0].timestamp, 123456);
    assert.equal(queue[0].bypassDedup, true);
    assert.equal(queue[1].tutorialRunId, 'run-7');
    assert.equal(queue[1].timestamp, 123456);
    assert.equal(queue[2].tutorialRunId, 'run-7');
    assert.equal(queue[2].timestamp, 123456);
    assert.equal(postedMessages.length, 4);
    assert.equal(relayedMessages.length, 4);
    assert.equal(warnings.length, 0);

    assert.equal(bus.postToPet('yui_guide_pet_ping', { id: 'pet-a' }), true);
    assert.equal(postedMessages.length, 5);
    assert.equal(postedMessages[4].action, 'yui_guide_pet_ping');
    assert.equal(petRelays.length, 1);
    assert.equal(petRelays[0].action, 'yui_guide_pet_ping');
    assert.equal(petRelays[0].id, 'pet-a');
    assert.equal(petRelays[0].tutorialRunId, 'run-7');

    assert.equal(typeof bus.clearQueue, 'function');
    bus.clearQueue();
    assert.equal(storage.has('queue-key'), false);
    assert.deepEqual(bus.readQueue(), []);

    const off = bus.on('yui_guide_append_chat_message', () => {});
    assert.equal(typeof off, 'function');
    assert.equal(listeners[0][0], 'add');
    off();
    assert.equal(listeners[1][0], 'remove');
});

test('target geometry registry and chat window adapter expose phase two boundaries', () => {
    assert.equal(typeof common.createTutorialTargetGeometryRegistry, 'function');
    assert.equal(typeof common.createReactChatTutorialHostAdapter, 'function');
    assert.equal(typeof common.createChatWindowAdapter, 'function');

    const registry = common.createTutorialTargetGeometryRegistry();
    const capsule = registry.resolve('chat-capsule-input');
    assert.equal(capsule.externalKind, 'capsule-input');
    assert.equal(capsule.fallbackGroup, 'chat-input');
    assert.ok(capsule.localSelectors.some((selector) => selector.includes('capsuleBody')));
    assert.equal(registry.getExternalKind('chat-galgame'), 'galgame');
    assert.equal(typeof registry.getByExternalKind, 'function');
    assert.equal(registry.getByExternalKind('capsule-input').key, 'chat-capsule-input');
    assert.equal(registry.getByExternalKind('galgame').shape, 'rounded-rect');

    const localTarget = { id: 'local-capsule' };
    const hostCalls = [];
    const reactHostAdapter = common.createReactChatTutorialHostAdapter({
        host: {
            setHomeTutorialInputLocked(locked, reason) {
                hostCalls.push(['lockInput', locked, reason]);
            },
            setHomeTutorialInteractionLocked(locked, reason) {
                hostCalls.push(['buttons', locked, reason]);
            },
            setAvatarToolMenuOpen(open, reason) {
                hostCalls.push(['avatarMenu', open, reason]);
            },
            setCompactToolFanOpen(open, reason) {
                hostCalls.push(['toolFan', open, reason]);
            },
            setCompactHistoryOpen(open, reason) {
                hostCalls.push(['history', open, reason]);
            },
            rotateCompactToolWheel(direction, stepCount, reason) {
                hostCalls.push(['rotate', direction, stepCount, reason]);
            },
            setCompactToolWheelIndex(index, reason) {
                hostCalls.push(['wheelIndex', index, reason]);
            }
        }
    });
    const localAdapter = common.createChatWindowAdapter({
        mode: 'local',
        registry,
        reactHostAdapter,
        resolveLocalTarget(key) {
            return key === 'chat-capsule-input' ? localTarget : null;
        }
    });
    assert.equal(localAdapter.resolveTarget('chat-capsule-input'), localTarget);
    assert.equal(localAdapter.getExternalKind('chat-capsule-input'), 'capsule-input');
    assert.equal(localAdapter.lockInput(true, 'phase2-local'), true);
    assert.equal(localAdapter.setButtonsDisabled(true, 'phase2-local'), true);
    assert.equal(localAdapter.setAvatarToolMenuOpen(true, 'phase2-local'), true);
    assert.equal(localAdapter.setCompactToolFanOpen(true, 'phase2-local'), true);
    assert.equal(localAdapter.setCompactHistoryOpen(true, 'phase2-local'), true);
    assert.equal(localAdapter.rotateCompactToolWheel(-1, 2, 'phase2-local'), true);
    assert.equal(localAdapter.setCompactToolWheelIndex(3, 'phase2-local'), true);
    assert.deepEqual(hostCalls, [
        ['lockInput', true, 'phase2-local'],
        ['buttons', true, 'phase2-local'],
        ['avatarMenu', true, 'phase2-local'],
        ['toolFan', true, 'phase2-local'],
        ['history', true, 'phase2-local'],
        ['rotate', -1, 2, 'phase2-local'],
        ['wheelIndex', 3, 'phase2-local']
    ]);

    const externalCalls = [];
    const externalAdapter = common.createChatWindowAdapter({
        mode: 'externalized',
        registry,
        interactionTakeover: {
            setExternalizedChatSpotlight(kind) {
                externalCalls.push(['spotlight', kind]);
            },
            setExternalizedChatCursor(kind, options) {
                externalCalls.push(['cursor', kind, options.effect]);
            },
            setExternalizedChatInputLocked(locked, reason) {
                externalCalls.push(['lock', locked, reason]);
            },
            setExternalizedChatButtonsDisabled(disabled) {
                externalCalls.push(['buttons', disabled]);
            },
            setExternalizedChatAvatarToolMenuOpen(open, reason) {
                externalCalls.push(['avatarMenu', open, reason]);
            },
            setExternalizedChatCompactToolFanOpen(open, reason) {
                externalCalls.push(['toolFan', open, reason]);
            },
            setExternalizedChatCompactHistoryOpen(open, reason) {
                externalCalls.push(['history', open, reason]);
            },
            rotateExternalizedChatCompactToolWheel(direction, stepCount, reason) {
                externalCalls.push(['rotate', direction, stepCount, reason]);
            },
            setExternalizedChatCompactToolWheelIndex(index, reason) {
                externalCalls.push(['wheelIndex', index, reason]);
            }
        },
        beforeExternalizedSpotlight(kind) {
            externalCalls.push(['beforeSpotlight', kind]);
        }
    });
    assert.equal(externalAdapter.resolveTarget('chat-capsule-input'), null);
    assert.equal(externalAdapter.setSpotlight('chat-capsule-input'), true);
    assert.equal(externalAdapter.setCursor('chat-capsule-input', { effect: 'wobble' }), true);
    assert.equal(externalAdapter.lockInput(true, 'phase2-test'), true);
    assert.equal(externalAdapter.setButtonsDisabled(true, 'phase2-test'), true);
    assert.equal(externalAdapter.setAvatarToolMenuOpen(true, 'phase2-test'), true);
    assert.equal(externalAdapter.setCompactToolFanOpen(true, 'phase2-test'), true);
    assert.equal(externalAdapter.setCompactHistoryOpen(true, 'phase2-test'), true);
    assert.equal(externalAdapter.rotateCompactToolWheel(1, 2, 'phase2-test'), true);
    assert.equal(externalAdapter.setCompactToolWheelIndex(4, 'phase2-test'), true);
    assert.deepEqual(externalCalls, [
        ['beforeSpotlight', 'capsule-input'],
        ['spotlight', 'capsule-input'],
        ['cursor', 'capsule-input', 'wobble'],
        ['lock', true, 'phase2-test'],
        ['buttons', true],
        ['avatarMenu', true, 'phase2-test'],
        ['toolFan', true, 'phase2-test'],
        ['history', true, 'phase2-test'],
        ['rotate', 1, 2, 'phase2-test'],
        ['wheelIndex', 4, 'phase2-test']
    ]);

    const hookErrorCalls = [];
    const hookErrorAdapter = common.createChatWindowAdapter({
        mode: 'externalized',
        interactionTakeover: {
            setExternalizedChatSpotlight(kind) {
                hookErrorCalls.push(['spotlight', kind]);
            }
        },
        beforeExternalizedSpotlight() {
            throw new Error('expected-hook-failure');
        }
    });
    const originalWarn = console.warn;
    const hookWarnings = [];
    console.warn = (...args) => hookWarnings.push(args);
    try {
        assert.equal(hookErrorAdapter.setSpotlight('chat-capsule-input'), true);
    } finally {
        console.warn = originalWarn;
    }
    assert.deepEqual(hookErrorCalls, [['spotlight', 'capsule-input']]);
    assert.equal(hookWarnings.length, 1);
});

test('app interpage recognizes explicit Yui guide dedup bypass messages', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');

    assert.match(source, /function shouldBypassYuiGuideMessageDedup\(action,\s*message\)/);
    assert.match(source, /message\s*&&\s*message\.bypassDedup === true/);
    assert.match(source, /\|\| action === 'yui_guide_set_chat_cursor'/);
    assert.doesNotMatch(source, /\|\| action === 'yui_guide_drag_chat_cursor'/);
    assert.doesNotMatch(source, /\|\| action === 'yui_guide_arc_chat_cursor'/);
    assert.doesNotMatch(source, /action === 'yui_guide_set_chat_cursor' && !\(message && message\.freezePoint === true\)/);
    assert.match(source, /shouldBypassYuiGuideMessageDedup\(event\.data\.action,\s*event\.data\)/);
});

test('app interpage sends external chat pet reports through the command bus', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');
    const bridgeDataBlock = source.split('    function handleYuiGuideChatBridgeData(data) {')[1].split(
        '    function drainPendingYuiGuideChatBridgeQueue',
        1
    )[0];
    const requestIdentityBlock = source.split("                    case 'request_tutorial_chat_identity': {")[1].split(
        "                    case 'request_avatar':",
        1
    )[0];
    const requestAvatarBlock = source.split("                    case 'request_avatar': {")[1].split(
        "                    case 'handoff_consumed':",
        1
    )[0];
    const avatarPreviewBlock = source.split("    yuiGuideInterpageResources.addEventListener(window, 'chat-avatar-preview-updated'")[1].split(
        "    yuiGuideInterpageResources.addEventListener(window, 'neko:idle-chat-minimized-state'",
        1
    )[0];
    const cursorAnchorBlock = source.split('    function reportYuiGuideChatCursorAnchor')[1].split(
        '    function applyYuiGuideChatCursor',
        1
    )[0];
    const standaloneChatBlock = source.split('    if (isStandaloneChatPage()) {')[1].split(
        '    // =====================================================================\n    // postMessage listeners',
        1
    )[0];

    assert.match(source, /function getYuiGuideBridgeCommandBus\(\) \{/);
    assert.match(source, /window\.YuiGuideCommon[\s\S]*createTutorialBridgeCommandBus/);
    assert.match(source, /function postYuiGuideMessageToChat\(action,\s*payload,\s*options\) \{/);
    assert.match(source, /bus\.post\(message,\s*options \|\| \{\}\)/);
    assert.match(source, /function postYuiGuideMessageToPet\(action,\s*payload,\s*options\) \{/);
    assert.match(source, /bus\.postToPet\(action,\s*payload,\s*options \|\| \{\}\)/);
    assert.match(bridgeDataBlock, /case 'tutorial_chat_identity_override':/);
    assert.match(bridgeDataBlock, /applyTutorialChatIdentityOverride\(data\)/);
    assert.match(requestIdentityBlock, /postYuiGuideMessageToChat\([\s\S]*'tutorial_chat_identity_override'/);
    assert.doesNotMatch(requestIdentityBlock, /nekoBroadcastChannel\.postMessage\(Object\.assign\(\{\s*action: 'tutorial_chat_identity_override'/);
    assert.match(requestAvatarBlock, /postYuiGuideMessageToChat\('avatar_updated'/);
    assert.doesNotMatch(requestAvatarBlock, /nekoBroadcastChannel\.postMessage\(\{\s*action: 'avatar_updated'/);
    assert.match(source, /postYuiGuideMessageToChat\('avatar_capture_result'/);
    assert.doesNotMatch(source, /nekoBroadcastChannel\.postMessage\(\{\s*action: 'avatar_capture_result'/);
    assert.match(avatarPreviewBlock, /postYuiGuideMessageToChat\('avatar_updated'/);
    assert.doesNotMatch(avatarPreviewBlock, /nekoBroadcastChannel\.postMessage\(\{\s*action: 'avatar_updated'/);
    assert.match(cursorAnchorBlock, /postYuiGuideMessageToPet\('yui_guide_chat_cursor_anchor',\s*message/);
    assert.doesNotMatch(cursorAnchorBlock, /relayYuiGuideMessageToNative\('pet',\s*message\)/);
    assert.doesNotMatch(cursorAnchorBlock, /nekoBroadcastChannel\.postMessage\(message\)/);
    assert.match(standaloneChatBlock, /postYuiGuideMessageToPet\('request_avatar'/);
    assert.match(standaloneChatBlock, /postYuiGuideMessageToPet\('request_tutorial_chat_identity'/);
    assert.match(standaloneChatBlock, /postYuiGuideMessageToPet\('yui_guide_chat_ready'/);
    assert.match(source, /return kind === 'input' \|\| kind === 'capsule-input';/);
    assert.match(source, /function preserveYuiGuideChatSpotlightDuringResistance\(kind, pcOverlayRunId\) \{/);
    assert.match(source, /preserveDuringResistance === true[\s\S]*preserveYuiGuideChatSpotlightDuringResistance\(normalizedKind, pcOverlayRunId\)/);
    assert.match(source, /applyYuiGuideChatSpotlight\(message\.kind \|\| '', \{[\s\S]*preserveDuringResistance: preserveSpotlightDuringResistance[\s\S]*\}\);\s*scheduleYuiGuideChatInputSpotlightRetry\(message\.kind \|\| '', getYuiGuidePcOverlayRunIdFromMessage\(message\)\);/);
    assert.match(source, /applyYuiGuideChatSpotlight\(event\.data\.kind \|\| '', \{[\s\S]*preserveDuringResistance: preserveSpotlightDuringResistance[\s\S]*\}\);\s*scheduleYuiGuideChatInputSpotlightRetry\(event\.data\.kind \|\| '', spotlightRunId\);/);
    assert.match(standaloneChatBlock, /yuiGuideInterpageResources\.setTimeout\(drainPendingYuiGuideChatBridgeQueue,\s*0\)/);
    assert.match(standaloneChatBlock, /yuiGuideInterpageResources\.addEventListener\(window,\s*'neko:config-injected'/);
    assert.doesNotMatch(standaloneChatBlock, /window\.setTimeout\(drainPendingYuiGuideChatBridgeQueue/);
    assert.doesNotMatch(standaloneChatBlock, /window\.addEventListener\('neko:config-injected'/);
    assert.doesNotMatch(standaloneChatBlock, /relayYuiGuideMessageToNative\('pet'/);
});

test('app interpage routes non-guide broadcasts through a shared interpage sender', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');
    const senderBlock = source.split('    function postInterpageMessage(message, options) {')[1].split(
        '    function stopIdleChatCompactSurfaceHeartbeat',
        1
    )[0];
    const idleActivityBlock = source.split('    function broadcastCrossWindowIdleActivity(source, kind) {')[1].split(
        "    document.addEventListener('pointerdown'",
        1
    )[0];
    const voiceChatBlock = source.split('    function syncVoiceChatComposerHidden(hidden) {')[1].split(
        '    // =====================================================================',
        1
    )[0];
    const handoffForwardBlock = source.split("    yuiGuideInterpageResources.addEventListener(window, 'neko:yui-guide:handoff-sent', function (evt) {")[1].split(
        "    // 监听角色信息变化",
        1
    )[0];
    const icebreakerBlock = source.split('    function postIcebreakerBridgeEvent(action, payload) {')[1].split(
        '    function relayYuiGuideMessageToNative',
        1
    )[0];
    const compactStateForwardBlock = source.split("    yuiGuideInterpageResources.addEventListener(window, 'neko:idle-chat-minimized-state', function (evt) {")[1].split(
        "    if (isStandaloneChatPage()) {",
        1
    )[0];

    assert.match(senderBlock, /nekoBroadcastChannel\.postMessage\(message\)/);
    assert.match(senderBlock, /window\.opener\.postMessage\(message,\s*window\.location\.origin\)/);
    assert.match(idleActivityBlock, /postInterpageMessage\(payload,\s*\{ openerFallback: true \}\);/);
    assert.match(voiceChatBlock, /postInterpageMessage\(\{/);
    assert.doesNotMatch(voiceChatBlock, /nekoBroadcastChannel\.postMessage/);
    assert.match(handoffForwardBlock, /postInterpageMessage\(\{/);
    assert.doesNotMatch(handoffForwardBlock, /window\.addEventListener\('neko:yui-guide:handoff-sent'/);
    assert.match(icebreakerBlock, /postInterpageMessage\(message\);/);
    assert.doesNotMatch(icebreakerBlock, /nekoBroadcastChannel\.postMessage/);
    assert.match(compactStateForwardBlock, /postInterpageMessage\(Object\.assign\(\{/);
});

test('app interpage routes Yui guide timers and local listeners through scoped resources', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');
    const helperBlock = source.split('    function createAppInterpageScopedResources() {')[1].split(
        '    /**\n     * Returns true if this action+timestamp was already processed',
        1
    )[0];
    const heartbeatBlock = source.split('    function stopIdleChatCompactSurfaceHeartbeat() {')[1].split(
        '    function syncIdleChatCompactSurfaceHeartbeat(payload) {',
        1
    )[0];
    const chatFlushBlock = source.split('    function scheduleYuiGuideChatMessageFlush(delay) {')[1].split(
        '    function flushPendingYuiGuideChatMessages() {',
        1
    )[0];
    const icebreakerFlushBlock = source.split('    function scheduleIcebreakerBridgeFlush(delay) {')[1].split(
        '    function queueIcebreakerBridgeAction(action) {',
        1
    )[0];
    const relayStorageBlock = source.split(
        "    yuiGuideInterpageResources.addEventListener(window, 'neko:tutorial-overlay-relay'"
    )[1].split(
        '    try {\n        if (typeof BroadcastChannel',
        1
    )[0];
    const clearSpotlightBlock = source.split('    function clearYuiGuideChatSpotlightTracking() {')[1].split(
        '    function scheduleYuiGuideChatInputSpotlightRetry(kind, pcOverlayRunId) {',
        1
    )[0];
    const spotlightRetryBlock = source.split('    function scheduleYuiGuideChatInputSpotlightRetry(kind, pcOverlayRunId) {')[1].split(
        '    function ensureYuiGuideChatSpotlightTracking(pcOverlayRunId) {',
        1
    )[0];
    const spotlightTrackingBlock = source.split('    function ensureYuiGuideChatSpotlightTracking(pcOverlayRunId) {')[1].split(
        '    function updateYuiGuideChatSpotlight(kind, pcOverlayRunId) {',
        1
    )[0];
    const spotlightApplyBlock = source.split('    function applyYuiGuideChatSpotlight(kind, options) {')[1].split(
        '    // =====================================================================',
        1
    )[0];
    const preserveSpotlightBlock = spotlightApplyBlock.split(
        '        if (normalizedKind && options && options.preserveDuringResistance === true) {'
    )[1].split(
        '        if (\n            !normalizedKind',
        1
    )[0];
    const pagehideCleanupBlock = source.split('    function cleanupAppInterpageTransientResources() {')[1].split(
        '    // =====================================================================',
        1
    )[0];
    const scopedListenerBlock = source.split(
        "    yuiGuideInterpageResources.addEventListener(window, 'neko:yui-guide:handoff-sent'"
    )[1].split(
        '    // =====================================================================\n    // postMessage listeners',
        1
    )[0];

    assert.match(source, /var yuiGuideInterpageResources = createAppInterpageScopedResources\(\);/);
    assert.match(source, /var yuiGuideChatSpotlightResources = createAppInterpageScopedResources\(\);/);
    assert.match(helperBlock, /window\.YuiGuideCommon[\s\S]*createScopedTutorialResources/);
    assert.match(helperBlock, /setTimeout: setScopedTimeout/);
    assert.match(helperBlock, /setInterval: setScopedInterval/);
    assert.match(helperBlock, /destroy: destroy/);

    assert.match(heartbeatBlock, /yuiGuideInterpageResources\.clearInterval\(idleChatCompactSurfaceHeartbeatTimer\)/);
    assert.match(heartbeatBlock, /idleChatCompactSurfaceHeartbeatTimer = yuiGuideInterpageResources\.setInterval\(/);
    assert.doesNotMatch(heartbeatBlock, /window\.setInterval\(/);
    assert.match(chatFlushBlock, /_yuiGuideChatFlushTimer = yuiGuideInterpageResources\.setTimeout\(/);
    assert.doesNotMatch(chatFlushBlock, /_yuiGuideChatFlushTimer = setTimeout\(/);
    assert.match(icebreakerFlushBlock, /_icebreakerBridgeFlushTimer = yuiGuideInterpageResources\.setTimeout\(/);
    assert.match(icebreakerFlushBlock, /yuiGuideInterpageResources\.clearTimeout\(_icebreakerBridgeFlushTimer\)/);
    assert.doesNotMatch(icebreakerFlushBlock, /_icebreakerBridgeFlushTimer = setTimeout\(/);
    assert.match(relayStorageBlock, /yuiGuideInterpageResources\.addEventListener\(window,\s*'message'/);
    assert.match(relayStorageBlock, /yuiGuideInterpageResources\.addEventListener\(window,\s*'storage',\s*handleYuiGuideChatBridgeStorageEvent\)/);
    assert.doesNotMatch(relayStorageBlock, /window\.addEventListener\('storage',\s*handleYuiGuideChatBridgeStorageEvent\)/);
    assert.match(clearSpotlightBlock, /yuiGuideChatSpotlightResources\.clearInterval\(yuiGuideChatSpotlightTimer\)/);
    assert.match(clearSpotlightBlock, /yuiGuideChatSpotlightResources\.destroy\(\)/);
    assert.match(clearSpotlightBlock, /yuiGuideChatSpotlightResources = createAppInterpageScopedResources\(\);/);
    assert.match(spotlightRetryBlock, /yuiGuideChatSpotlightResources\.setTimeout\(/);
    assert.doesNotMatch(spotlightRetryBlock, /window\.setTimeout\(/);
    assert.match(spotlightTrackingBlock, /yuiGuideChatSpotlightTimer = yuiGuideChatSpotlightResources\.setInterval\(/);
    assert.match(spotlightTrackingBlock, /updateYuiGuideChatSpotlight\(yuiGuideChatSpotlightKind,\s*yuiGuideChatSpotlightPcOverlayRunId\)/);
    assert.doesNotMatch(spotlightTrackingBlock, /yuiGuideChatSpotlightTimer = window\.setInterval\(/);
    assert.match(spotlightApplyBlock, /ensureYuiGuideChatSpotlightTracking\(pcOverlayRunId\)/);
    assert.doesNotMatch(preserveSpotlightBlock, /yuiGuideChatSpotlightKind = normalizedKind;\s*clearYuiGuideChatSpotlightTracking\(\);/);
    assert.match(preserveSpotlightBlock, /preserveYuiGuideChatSpotlightDuringResistance\(normalizedKind, pcOverlayRunId\)/);
    assert.match(preserveSpotlightBlock, /scheduleYuiGuideChatInputSpotlightRetry\(normalizedKind, pcOverlayRunId\)/);
    assert.match(preserveSpotlightBlock, /ensureYuiGuideChatSpotlightTracking\(pcOverlayRunId\)/);
    assert.match(pagehideCleanupBlock, /clearYuiGuideChatFlushTimer\(\)/);
    assert.match(pagehideCleanupBlock, /clearIcebreakerBridgeFlushTimer\(\)/);
    assert.match(pagehideCleanupBlock, /stopIdleChatCompactSurfaceHeartbeat\(\)/);
    assert.match(pagehideCleanupBlock, /clearYuiGuideChatSpotlightTracking\(\)/);
    assert.match(pagehideCleanupBlock, /yuiGuideInterpageResources\.addEventListener\(window,\s*'pagehide',\s*cleanupAppInterpageTransientResources\)/);
    assert.match(scopedListenerBlock, /yuiGuideInterpageResources\.addEventListener\(window,\s*'chat-avatar-preview-updated'/);
    assert.match(scopedListenerBlock, /yuiGuideInterpageResources\.addEventListener\(window,\s*'neko:idle-chat-minimized-state'/);
    assert.match(scopedListenerBlock, /yuiGuideInterpageResources\.addEventListener\(window,\s*'neko:compact-surface-layout-change'/);
    assert.match(scopedListenerBlock, /yuiGuideInterpageResources\.addEventListener\(window,\s*'neko:icebreaker-choice-selected'/);
    assert.match(scopedListenerBlock, /yuiGuideInterpageResources\.addEventListener\(window,\s*'neko:icebreaker-free-text-submitted'/);
    assert.doesNotMatch(scopedListenerBlock, /window\.addEventListener\('chat-avatar-preview-updated'/);
    assert.doesNotMatch(scopedListenerBlock, /window\.addEventListener\('neko:icebreaker-choice-selected'/);
});

test('full tutorial pages load common helpers before the director', () => {
    for (const templatePath of [
        'templates/index.html',
        'templates/api_key_settings.html',
        'templates/memory_browser.html'
    ]) {
        const source = fs.readFileSync(path.join(repoRoot, templatePath), 'utf8');
        const guideHelpersIndex = source.indexOf('/static/tutorial/core/guide-helpers.js');
        const scopedResourcesIndex = source.indexOf('/static/tutorial/core/scoped-resources.js');
        const bridgeCommandBusIndex = source.indexOf('/static/tutorial/core/bridge-command-bus.js');
        const targetRegistryIndex = source.indexOf('/static/tutorial/core/target-geometry-registry.js');
        const chatAdapterIndex = source.indexOf('/static/tutorial/core/chat-window-adapter.js');
        const commonIndex = source.indexOf('/static/tutorial/yui-guide/common.js');
        const directorIndex = source.indexOf('/static/tutorial/yui-guide/director.js');

        assert.notEqual(guideHelpersIndex, -1, templatePath + ' should load tutorial/core/guide-helpers.js');
        assert.notEqual(scopedResourcesIndex, -1, templatePath + ' should load tutorial/core/scoped-resources.js');
        assert.notEqual(bridgeCommandBusIndex, -1, templatePath + ' should load tutorial/core/bridge-command-bus.js');
        assert.notEqual(targetRegistryIndex, -1, templatePath + ' should load tutorial/core/target-geometry-registry.js');
        assert.notEqual(chatAdapterIndex, -1, templatePath + ' should load tutorial/core/chat-window-adapter.js');
        assert.notEqual(commonIndex, -1, templatePath + ' should load tutorial/yui-guide/common.js');
        assert.notEqual(directorIndex, -1, templatePath + ' should load tutorial/yui-guide/director.js');
        assert.ok(guideHelpersIndex < commonIndex, templatePath + ' should load guide helpers before common helpers');
        assert.ok(scopedResourcesIndex < commonIndex, templatePath + ' should load scoped resources before common helpers');
        assert.ok(bridgeCommandBusIndex < commonIndex, templatePath + ' should load bridge command bus before common helpers');
        assert.ok(targetRegistryIndex < commonIndex, templatePath + ' should load target registry before common helpers');
        assert.ok(chatAdapterIndex < commonIndex, templatePath + ' should load chat adapter before common helpers');
        assert.ok(commonIndex < directorIndex, templatePath + ' should load common helpers before director');
    }
});

test('lifecycle state store module is loaded before prompt and manager scripts', () => {
    const lifecyclePath = path.join(__dirname, 'tutorial/core/lifecycle-state-store.js');
    assert.ok(fs.existsSync(lifecyclePath), 'tutorial/core/lifecycle-state-store.js should exist');
    const source = fs.readFileSync(lifecyclePath, 'utf8');
    const stores = require('./tutorial/core/lifecycle-state-store.js');

    for (const exportName of [
        'TutorialLifecycleStateStore',
        'HomeTutorialPromptLifecycleStateStore'
    ]) {
        assert.equal(typeof stores[exportName], 'function', exportName + ' should be exported');
        assert.match(source, new RegExp('class ' + exportName));
    }
    assert.match(source, /root\.TutorialLifecycleStores = api/);

    const orderedScripts = [
        ['templates/index.html', '/static/tutorial/core/app-prompt.js'],
        ['templates/index.html', '/static/tutorial/core/universal-manager.js'],
        ['templates/api_key_settings.html', '/static/tutorial/core/universal-manager.js'],
        ['templates/memory_browser.html', '/static/tutorial/core/universal-manager.js']
    ];

    for (const [templatePath, consumerScript] of orderedScripts) {
        const templateSource = fs.readFileSync(path.join(repoRoot, templatePath), 'utf8');
        const lifecycleIndex = templateSource.indexOf('/static/tutorial/core/lifecycle-state-store.js');
        const consumerIndex = templateSource.indexOf(consumerScript);

        assert.notEqual(lifecycleIndex, -1, templatePath + ' should load tutorial/core/lifecycle-state-store.js');
        assert.notEqual(consumerIndex, -1, templatePath + ' should load ' + consumerScript);
        assert.ok(lifecycleIndex < consumerIndex, templatePath + ' should load lifecycle stores before ' + consumerScript);
    }
});

test('resistance controller support module is loaded before the director', () => {
    const controllerPath = path.join(__dirname, 'tutorial/visual/resistance-controllers.js');
    assert.ok(fs.existsSync(controllerPath), 'tutorial/visual/resistance-controllers.js should exist');
    const source = fs.readFileSync(controllerPath, 'utf8');
    const controllers = require('./tutorial/visual/resistance-controllers.js');

    for (const exportName of [
        'ResetInterruptController',
        'SidebarPauseController',
        'PauseCoordinator',
        'TutorialTerminationRouter'
    ]) {
        assert.equal(typeof controllers[exportName], 'function', exportName + ' should be exported');
        assert.match(source, new RegExp('class ' + exportName));
    }
    assert.match(source, /root\.TutorialResistanceControllers = api/);

    for (const templatePath of [
        'templates/index.html',
        'templates/api_key_settings.html',
        'templates/memory_browser.html'
    ]) {
        const templateSource = fs.readFileSync(path.join(repoRoot, templatePath), 'utf8');
        const controllerIndex = templateSource.indexOf('/static/tutorial/visual/resistance-controllers.js');
        const directorIndex = templateSource.indexOf('/static/tutorial/yui-guide/director.js');

        assert.notEqual(controllerIndex, -1, templatePath + ' should load tutorial/visual/resistance-controllers.js');
        assert.notEqual(directorIndex, -1, templatePath + ' should load tutorial/yui-guide/director.js');
        assert.ok(controllerIndex < directorIndex, templatePath + ' should load resistance controllers before director');
    }
});

test('interpage consumes common tutorial geometry before chat bridge scripts run', () => {
    const indexTemplate = fs.readFileSync(path.join(repoRoot, 'templates', 'index.html'), 'utf8');
    const chatTemplate = fs.readFileSync(path.join(repoRoot, 'templates', 'chat.html'), 'utf8');
    const appInterpageSource = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');

    assert.notEqual(indexTemplate.indexOf('/static/tutorial/core/bridge-command-bus.js'), -1);
    assert.notEqual(chatTemplate.indexOf('/static/tutorial/core/bridge-command-bus.js'), -1);
    assert.notEqual(indexTemplate.indexOf('/static/tutorial/core/target-geometry-registry.js'), -1);
    assert.notEqual(chatTemplate.indexOf('/static/tutorial/core/target-geometry-registry.js'), -1);
    assert.notEqual(indexTemplate.indexOf('/static/tutorial/core/chat-window-adapter.js'), -1);
    assert.notEqual(chatTemplate.indexOf('/static/tutorial/core/chat-window-adapter.js'), -1);
    assert.notEqual(indexTemplate.indexOf('/static/tutorial/core/command-registry.js'), -1);
    assert.notEqual(chatTemplate.indexOf('/static/tutorial/core/command-registry.js'), -1);
    assert.notEqual(indexTemplate.indexOf('/static/tutorial/core/script-normalizer.js'), -1);
    assert.notEqual(chatTemplate.indexOf('/static/tutorial/core/script-normalizer.js'), -1);
    assert.notEqual(indexTemplate.indexOf('/static/tutorial/core/timeline-engine.js'), -1);
    assert.notEqual(chatTemplate.indexOf('/static/tutorial/core/timeline-engine.js'), -1);
    assert.notEqual(indexTemplate.indexOf('/static/tutorial/core/visual-runtime.js'), -1);
    assert.notEqual(chatTemplate.indexOf('/static/tutorial/core/visual-runtime.js'), -1);
    assert.match(indexTemplate, /\/static\/app-interpage\.js\?v=\{\{\s*static_asset_version\s*\}\}/);
    assert.match(chatTemplate, /\/static\/app-interpage\.js\?v=\{\{\s*static_asset_version\s*\}\}/);
    assert.ok(
        indexTemplate.indexOf('/static/tutorial/core/guide-helpers.js') >= 0
            && indexTemplate.indexOf('/static/tutorial/core/guide-helpers.js') < indexTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && indexTemplate.indexOf('/static/tutorial/core/scoped-resources.js') < indexTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && indexTemplate.indexOf('/static/tutorial/core/bridge-command-bus.js') < indexTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && indexTemplate.indexOf('/static/tutorial/core/target-geometry-registry.js') < indexTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && indexTemplate.indexOf('/static/tutorial/core/chat-window-adapter.js') < indexTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && indexTemplate.indexOf('/static/tutorial/core/command-registry.js') < indexTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && indexTemplate.indexOf('/static/tutorial/core/script-normalizer.js') < indexTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && indexTemplate.indexOf('/static/tutorial/core/timeline-engine.js') < indexTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && indexTemplate.indexOf('/static/tutorial/core/visual-runtime.js') < indexTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && indexTemplate.indexOf('/static/tutorial/yui-guide/common.js') < indexTemplate.indexOf('/static/app-interpage.js'),
        'index.html should load scoped resources and common helpers before app-interpage.js'
    );
    assert.ok(
        chatTemplate.indexOf('/static/tutorial/core/guide-helpers.js') >= 0
            && chatTemplate.indexOf('/static/tutorial/core/guide-helpers.js') < chatTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && chatTemplate.indexOf('/static/tutorial/core/scoped-resources.js') < chatTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && chatTemplate.indexOf('/static/tutorial/core/bridge-command-bus.js') < chatTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && chatTemplate.indexOf('/static/tutorial/core/target-geometry-registry.js') < chatTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && chatTemplate.indexOf('/static/tutorial/core/chat-window-adapter.js') < chatTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && chatTemplate.indexOf('/static/tutorial/core/command-registry.js') < chatTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && chatTemplate.indexOf('/static/tutorial/core/script-normalizer.js') < chatTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && chatTemplate.indexOf('/static/tutorial/core/timeline-engine.js') < chatTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && chatTemplate.indexOf('/static/tutorial/core/visual-runtime.js') < chatTemplate.indexOf('/static/tutorial/yui-guide/common.js')
            && chatTemplate.indexOf('/static/tutorial/yui-guide/common.js') < chatTemplate.indexOf('/static/app-interpage.js'),
        'chat.html should load scoped resources and common helpers before app-interpage.js'
    );
    assert.ok(
        indexTemplate.indexOf('/static/tutorial/yui-guide/common.js') >= 0
            && indexTemplate.indexOf('/static/tutorial/yui-guide/common.js') < indexTemplate.indexOf('/static/app-interpage.js'),
        'index.html should load tutorial/yui-guide/common.js before app-interpage.js'
    );
    assert.ok(
        chatTemplate.indexOf('/static/tutorial/yui-guide/common.js') >= 0
            && chatTemplate.indexOf('/static/tutorial/yui-guide/common.js') < chatTemplate.indexOf('/static/app-interpage.js'),
        'chat.html should load tutorial/yui-guide/common.js before app-interpage.js'
    );
    assert.match(appInterpageSource, /createYuiGuideTargetGeometryRegistry\(\)/);
    assert.match(appInterpageSource, /function getYuiGuideChatSpotlightElement\(createIfMissing\) \{[\s\S]*document\.createElement\('div'\)[\s\S]*spotlight\.id = 'yui-guide-chat-spotlight'/);
    assert.match(appInterpageSource, /getYuiGuideChatTargetRegistryEntryByExternalKind\(kind\)/);
    assert.match(appInterpageSource, /entry\.localSelectors\.some\(function \(selector\)/);
    assert.match(appInterpageSource, /getYuiGuideChatTargetShape\(kind\)/);
    assert.match(appInterpageSource, /getYuiGuideChatTargetShape\(kind\) === 'circle'/);
});

test('daily guide files consume common helpers instead of redeclaring shared helpers', () => {
    for (const fileName of dayGuideFiles) {
        const source = fs.readFileSync(path.join(repoRoot, 'static', fileName), 'utf8');

        assert.match(source, /window\.YuiGuideCommon/);
        assert.doesNotMatch(source, /function deepFreeze\(value\)/);
        assert.doesNotMatch(source, /function registerGuide\(config\)/);
        assert.doesNotMatch(source, /function zhAudio\(fileName\)/);
        assert.doesNotMatch(source, /function audioFilesForAllLocales\(fileName\)/);
    }
});

test('Day3 guide ships every referenced audio file', () => {
    const audioRoot = path.join(repoRoot, 'static', 'assets/tutorial/guide-audio');
    const day3GuideSource = fs.readFileSync(
        path.join(repoRoot, 'static', 'tutorial/yui-guide/days/day3-interaction-guide.js'),
        'utf8'
    );
    const expectedAudioFiles = Array.from(day3GuideSource.matchAll(/zhAudio\('([^']+\.mp3)'\)/g))
        .map((match) => match[1]);

    assert.ok(expectedAudioFiles.length > 0, 'Day3 guide should reference audio files');

    for (const locale of ['zh', 'ja', 'en', 'ko', 'ru']) {
        for (const audioFile of expectedAudioFiles) {
            assert.ok(
                fs.existsSync(path.join(audioRoot, locale, audioFile)),
                locale + ' should ship ' + audioFile
            );
        }
    }
});

test('director delegates external chat bridge messages to the command bus', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const constructorBlock = source.split('    class YuiGuideDirector {')[1].split(
        '            const capabilityApi = window.homeTutorialPlatformCapabilities;',
        1
    )[0];
    const postBlock = source.split('        postExternalChatGuideMessage(message) {')[1].split(
        '        getSceneSpotlightTarget',
        1
    )[0];

    assert.match(constructorBlock, /this\.chatBridgeCommandBus = createYuiGuideChatBridgeCommandBus/);
    assert.match(constructorBlock, /nativeRelayProvider:\s*\(\) => window\.nekoTutorialOverlay \|\| null/);
    assert.match(postBlock, /this\.chatBridgeCommandBus\.post\(outgoingMessage\)/);
    assert.doesNotMatch(postBlock, /nekoBroadcastChannel/);
    assert.doesNotMatch(postBlock, /relayToChat/);
});

test('director streams guide chat text over voice duration without an empty placeholder', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const appendBlock = source.split('        appendGuideChatMessage(text, options) {')[1].split(
        '        focusAndHighlightChatInput',
        1
    )[0];
    const streamBlock = source.split('        streamGuideChatMessage(message, content, options) {')[1].split(
        '        appendGuideChatMessage(text, options) {',
        1
    )[0];
    const resolveDurationBlock = source.split('        resolveGuideChatStreamDurationMs(content, options) {')[1].split(
        '        streamGuideChatMessage(message, content, options) {',
        1
    )[0];

    assert.match(appendBlock, /const initialVisibleText = Array\.from\(content\)\.slice\(0,\s*1\)\.join\(''\)/);
    assert.match(appendBlock, /initialVisibleTextLength:\s*Array\.from\(initialVisibleText\)\.length/);
    assert.match(appendBlock, /cloneGuideChatMessageWithText\(message,\s*initialVisibleText,\s*'streaming'\)/);
    assert.doesNotMatch(appendBlock, /cloneGuideChatMessageWithText\(message,\s*content,\s*'streaming'\)/);
    assert.doesNotMatch(appendBlock, /cloneGuideChatMessageWithText\(message,\s*'',\s*'streaming'\)/);
    assert.match(streamBlock, /initialVisibleTextLength/);
    assert.match(streamBlock, /elapsedActiveMs \/ durationMs/);
    assert.doesNotMatch(streamBlock, /revealTextImmediately/);
    assert.match(resolveDurationBlock, /if \(voiceDurationMs > 0\) \{\s*return voiceDurationMs;\s*\}/);
    assert.doesNotMatch(resolveDurationBlock, /resolveGuideChatStreamSyncDurationMs/);
});

test('interaction takeover delegates external chat commands to the command bus boundary', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/interaction-takeover.js'), 'utf8');
    const constructorBlock = source.split('        constructor(options) {')[1].split(
        '            this.interactionGuardHandler =',
        1
    )[0];
    const commandsBlock = source.split('        setExternalizedChatButtonsDisabled(disabled) {')[1].split(
        '        clearExternalizedChatFx() {',
        1
    )[0];

    assert.match(constructorBlock, /this\.externalChatCommandBus = this\.createExternalChatCommandBus\(\);/);
    assert.match(source, /createExternalChatCommandBus\(\) \{[\s\S]*this\.window\.YuiGuideCommon[\s\S]*createTutorialBridgeCommandBus/);
    assert.match(source, /resolveLanlanName\(\) \{/);
    assert.doesNotMatch(source, /message\.lanlan_name = this\.resolveLanlanName\(\);/);
    assert.match(source, /postExternalChatCommand\(action,\s*payload,\s*options\) \{[\s\S]*this\.externalChatCommandBus\.post\(message,\s*normalizedOptions\)/);
    assert.match(source, /resolveLanlanName\(\) \{[\s\S]*this\.window\.appState[\s\S]*this\.window\.lanlan_config/);
    assert.match(source, /if \(!message\.lanlan_name\) \{[\s\S]*const lanlanName = this\.resolveLanlanName\(\);[\s\S]*message\.lanlan_name = lanlanName;/);
    assert.match(source, /getExternalizedChatTutorialRunId\(\) \{/);
    assert.match(source, /getItem\('yuiGuidePcOverlayRunId'\)/);
    assert.match(source, /const tutorialRunId = this\.getExternalizedChatTutorialRunId\(\);/);
    assert.match(source, /if \(tutorialRunId && !message\.tutorialRunId\) \{[\s\S]*message\.tutorialRunId = tutorialRunId;/);
    assert.match(source, /if \(tutorialRunId && !message\.pcOverlayRunId\) \{[\s\S]*message\.pcOverlayRunId = tutorialRunId;/);
    assert.match(commandsBlock, /this\.postExternalChatCommand\('yui_guide_set_chat_buttons_disabled'/);
    assert.match(commandsBlock, /this\.postExternalChatCommand\('yui_guide_set_chat_cursor'/);
    assert.match(commandsBlock, /this\.postExternalChatCommand\('yui_guide_drag_chat_cursor'/);
    assert.match(source, /clearExternalizedChatGuideMessages\(\) \{[\s\S]*this\.postExternalChatCommand\('yui_guide_clear_chat_messages'/);
    const clearFxBlock = source.split('        clearExternalizedChatFx() {')[1].split(
        '        onExternalChatReady() {',
        1
    )[0];
    assert.doesNotMatch(clearFxBlock, /clearExternalizedChatGuideMessages/);
    assert.doesNotMatch(clearFxBlock, /yui_guide_clear_chat_messages/);
    assert.doesNotMatch(commandsBlock, /getExternalChatChannel\(\)/);
    assert.doesNotMatch(commandsBlock, /channel\.postMessage/);
});

test('interaction takeover preserves external chat spotlight clears during resistance pause', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/interaction-takeover.js'), 'utf8');
    const spotlightBlock = source.split('        setExternalizedChatSpotlight(kind) {')[1].split(
        '        setExternalizedChatCursor(kind, options) {',
        1
    )[0];

    assert.match(spotlightBlock, /const previousKind = this\.externalizedChatSpotlightKind;/);
    assert.match(spotlightBlock, /this\.externalizedChatSpotlightKind = typeof kind === 'string' \? kind : '';/);
    assert.match(spotlightBlock, /\(this\.externalizedChatSpotlightKind \|\| previousKind\)/);
    assert.match(spotlightBlock, /safeInvoke\(this\.isResistancePaused,\s*\[\],\s*false\) === true/);
    assert.match(spotlightBlock, /message\.preserveDuringResistance = true;/);
    assert.match(spotlightBlock, /this\.postExternalChatCommand\('yui_guide_set_chat_spotlight', message\);/);
    assert.match(source, /preserveExternalizedChatSpotlightDuringResistance\(\) \{/);
    assert.match(source, /preserveDuringResistance:\s*true/);
});

test('externalized chat spotlight ownership stops home overlay spotlight tracking', () => {
    const directorSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const overlaySource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/overlay.js'), 'utf8');
    const sceneOrchestratorSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/scene-orchestrator.js'), 'utf8');
    const visualRuntimeSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/visual-runtime.js'), 'utf8');
    const settingsTourSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/settings-tour-flow.js'), 'utf8');
    const operationRegistrySource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/operation-registry.js'), 'utf8');
    const chatAdapterSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/chat-window-adapter.js'), 'utf8');
    const clearSpotlightBlock = overlaySource.split('        clearSpotlight(options) {')[1].split(
        '        hasCursorPosition() {',
        1
    )[0];
    const helperBlock = directorSource.split('        clearHomeSpotlightsForExternalizedChat() {')[1].split(
        '        hideHomeCursorForExternalizedChat() {',
        1
    )[0];
    const setTargetBlock = directorSource.split('        setExternalizedChatGuideTarget(kind, options) {')[1].split(
        '        isDay3AvatarToolsSceneId(sceneId) {',
        1
    )[0];
    const adapterSetSpotlightBlock = chatAdapterSource.split('            setSpotlight(targetKey) {')[1].split(
        '            setCursor(targetKey, cursorOptions) {',
        1
    )[0];

    assert.match(clearSpotlightBlock, /const preservePcOverlaySpotlights = !!\(/);
    assert.match(clearSpotlightBlock, /options\.preservePcOverlaySpotlights === true/);
    assert.match(clearSpotlightBlock, /this\.stopSpotlightTracking\(\);/);
    assert.match(clearSpotlightBlock, /this\.spotlightState\.clearAll\(\);/);
    assert.match(clearSpotlightBlock, /if \(this\.isPcOverlayActive\(\) && !preservePcOverlaySpotlights\) \{/);
    assert.match(helperBlock, /this\.overlay\.clearSpotlight\(\{\s*preservePcOverlaySpotlights: true\s*\}\);/);
    assert.match(setTargetBlock, /this\.clearHomeSpotlightsForExternalizedChat\(\);\s*this\.interactionTakeover\.setExternalizedChatSpotlight\(normalizedKind\);/);
    assert.match(sceneOrchestratorSource, /clearHomeSpotlightsForExternalizedChat\(\);[\s\S]*setExternalizedChatSpotlight\(introExternalizedChatSpotlightKind\)/);
    assert.match(sceneOrchestratorSource, /clearHomeSpotlightsForExternalizedChat\(\);[\s\S]*setExternalizedChatSpotlight\(externalizedSpotlightKind\)/);
    assert.match(visualRuntimeSource, /clearHomeSpotlightsForExternalizedChat\(\);[\s\S]*setExternalizedChatSpotlight\(normalizedIntroKind\)/);
    assert.match(visualRuntimeSource, /clearHomeSpotlightsForExternalizedChat\(\);[\s\S]*setExternalizedChatSpotlight\(externalizedSpotlightKind\)/);
    assert.match(settingsTourSource, /clearHomeSpotlightsForExternalizedChat\(\);[\s\S]*setExternalizedChatSpotlight\(introExternalizedChatSpotlightKind\)/);
    assert.match(operationRegistrySource, /clearHomeSpotlightsForExternalizedChat\(\);[\s\S]*setExternalizedChatSpotlight\(/);
    assert.match(chatAdapterSource, /const beforeExternalizedSpotlight = typeof normalizedOptions\.beforeExternalizedSpotlight === 'function'/);
    assert.match(chatAdapterSource, /function getExternalizedRunMeta\(\) \{/);
    assert.match(chatAdapterSource, /getExternalizedChatTutorialRunId\(\)/);
    assert.match(chatAdapterSource, /pcOverlayRunId: tutorialRunId/);
    assert.match(chatAdapterSource, /function notifyBeforeExternalizedSpotlight\(kind\) \{/);
    assert.match(chatAdapterSource, /try \{[\s\S]*beforeExternalizedSpotlight\(kind, getExternalizedRunMeta\(\)\);[\s\S]*catch \(error\) \{/);
    assert.match(adapterSetSpotlightBlock, /const externalKind = getExternalKind\(targetKey\);/);
    assert.match(adapterSetSpotlightBlock, /notifyBeforeExternalizedSpotlight\(externalKind\);/);
    assert.match(adapterSetSpotlightBlock, /setExternalizedChatSpotlight\(externalKind\)/);
    assert.match(directorSource, /beforeExternalizedSpotlight: \(\) => this\.clearHomeSpotlightsForExternalizedChat\(\)/);
});

test('new user icebreaker clears and locks choice prompt while advancing branches', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/icebreaker/new-user-icebreaker.js'), 'utf8');
    const handleChoiceBlock = source.split('    function handleChoice(detail) {')[1].split(
        '\n    function handleFreeText',
        1
    )[0];

    assert.match(handleChoiceBlock, /if \(session\.choiceInFlight\) return;/);
    assert.match(handleChoiceBlock, /session\.choiceInFlight = true;\s*clearChoicePrompt\(\);/);
    assert.match(handleChoiceBlock, /appendChatMessage\('user'[\s\S]*\)\.then\(function \(\) \{[\s\S]*return deliverNode\(option\.next\);/);
    assert.match(handleChoiceBlock, /\}\)\.then\(function \(\) \{\s*session\.choiceInFlight = false;/);
    assert.match(handleChoiceBlock, /\.catch\(function \(error\) \{[\s\S]*session\.choiceInFlight = false;[\s\S]*setChoicePrompt\(node,\s*session\.localeData\);/);
});

test('new user icebreaker exports state used by greeting gating', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/icebreaker/new-user-icebreaker.js'), 'utf8');

    assert.match(source, /window\.NekoNewUserIcebreakerState\s*=\s*\{/);
    assert.match(source, /readStore:\s*readStore/);
    assert.match(source, /hasCompletedDay:\s*isDayCompleted/);
    assert.match(source, /isPeriodActive:\s*isPeriodActive/);
});

test('director exposes phase one guard and timing helpers for complex sequences', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const directorSource = source.split('    class YuiGuideDirector {')[1];
    const helperBlock = directorSource.split('        isStopping() {')[1].split(
        '        setTutorialTakingOver(active) {',
        1
    )[0];
    const keyboardBlock = source.split('        async runTakeoverKeyboardControlSequence')[1].split(
        '        async runPluginDashboardLaunchSequence',
        1
    )[0];
    const launchBlock = source.split('        async runPluginDashboardLaunchSequence')[1].split(
        '        async runPluginPreviewHomeExitSequence',
        1
    )[0];
    const captureBlock = source.split('        async runTakeoverCaptureActionSequence')[1].split(
        '        async runTakeoverSettingsPeekSequence',
        1
    )[0];

    assert.match(helperBlock, /isGuardFailed\(runId\)/);
    assert.match(helperBlock, /createSceneScaler\(voiceKey\)/);
    assert.match(helperBlock, /createNarrationPromise\(scene,\s*text,\s*voiceKey,\s*options\)/);
    assert.match(helperBlock, /finalizeScene\(runId,\s*options\)/);
    assert.match(keyboardBlock, /const scaleSceneMs = this\.createSceneScaler\(performance && performance\.voiceKey\);/);
    assert.match(keyboardBlock, /const guardFailed = \(\) => this\.isGuardFailed\(runId\);/);
    assert.match(launchBlock, /const scaleSceneMs = this\.createSceneScaler\(performance && performance\.voiceKey\);/);
    assert.match(launchBlock, /const guardFailed = \(\) => this\.isGuardFailed\(runId\);/);
    assert.match(captureBlock, /const scaleSceneMs = this\.createSceneScaler\(performance && performance\.voiceKey\);/);
    assert.match(captureBlock, /const guardFailed = \(\) => this\.isGuardFailed\(runId\);/);
    assert.match(source, /this\.takeoverOriginalAgentSwitches = null;/);
    assert.match(source, /async captureDay1TakeoverAgentSwitches\(\) \{/);
    assert.match(source, /async restoreDay1TakeoverAgentSwitches\(reason\) \{/);
    assert.match(source, /setAgentFlagEnabled\('computer_use_enabled', originalKeyboardControl\)/);
    assert.match(source, /setAgentMasterEnabled\(false\)/);
    assert.match(source, /restoreDay1TakeoverAgentSwitches\('termination_cleanup'\)/);
    assert.match(source, /restoreDay1TakeoverAgentSwitches\('destroy'\)/);
});

test('director routes resistance interrupts through ResistanceController boundary', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const resistanceSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/resistance-controllers.js'), 'utf8');
    const resetSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/avatar/floating-guide-reset.js'), 'utf8');
    const directorSource = source.split('    class YuiGuideDirector {')[1];
    const constructorBlock = directorSource.split(
        '            this.keydownHandler = this.onKeyDown.bind(this);',
        1
    )[0];
    const resistanceControllerSource = resistanceSource.split('    class ResistanceController {')[1] || '';
    const resistanceControllerBlock = resistanceControllerSource.split(
        '    class PauseCoordinator {',
        1
    )[0];
    const playResistanceBlock = directorSource.split('        playLightResistance(x, y, options) {')[1].split(
        '        async abortAsAngryExit(source) {',
        1
    )[0];
    const angryExitBlock = directorSource.split('        async abortAsAngryExit(source) {')[1].split(
        '        requestTermination(reason, tutorialReason) {',
        1
    )[0];
    const pointerDownBlock = directorSource.split('        onPointerDown(event) {')[1].split(
        '        handleInterrupt(event) {',
        1
    )[0];
    const handleInterruptBlock = directorSource.split('        handleInterrupt(event) {')[1].split(
        '        noteUserCursorRevealAttempt(distance, now) {',
        1
    )[0];
    const destroyBlock = directorSource.split('        destroy() {\n            if (this.destroyed) {')[1].split(
        '        onKeyDown(event) {',
        1
    )[0];

    assert.match(resistanceSource, /class ResistanceController/);
    assert.match(resistanceSource, /const DEFAULT_RESISTANCE_VOICE_KEYS = Object\.freeze\(\[/);
    assert.match(source, /const ResistanceController = TutorialResistanceControllers\.ResistanceController;/);
    assert.doesNotMatch(source, /    class ResistanceController \{/);
    assert.match(constructorBlock, /this\.resistanceController = new ResistanceController\(this\);/);
    assert.doesNotMatch(constructorBlock, /this\.interruptController/);
    assert.doesNotMatch(directorSource, /createInterruptController\(\) \{/);
    assert.doesNotMatch(resistanceControllerBlock, /this\.interruptController/);
    assert.doesNotMatch(resistanceControllerBlock, /createInterruptController\(\) \{/);
    assert.doesNotMatch(resistanceControllerBlock, /window\.TutorialInterruptController/);
    assert.match(resistanceControllerBlock, /getResistanceMessage\(performance\) \{/);
    assert.match(resistanceControllerBlock, /recordPointerDown\(event\) \{/);
    assert.match(resistanceControllerBlock, /handleInterrupt\(event\) \{/);
    assert.match(resistanceControllerBlock, /playLightResistance\(x,\s*y,\s*options\) \{/);
    assert.match(resistanceControllerBlock, /abortAsAngryExit\(source\) \{/);
    assert.match(resistanceControllerBlock, /destroy\(\) \{/);
    assert.match(resistanceControllerBlock, /director\.interruptQualifyingMoveStreak \+= 1;/);
    assert.match(resistanceControllerBlock, /director\.interruptCount \+= 1;/);
    assert.match(resistanceControllerBlock, /director\.abortAsAngryExit\('pointer_interrupt'\);/);
    assert.match(resistanceControllerBlock, /director\.playLightResistance\(x,\s*y,\s*\{/);
    assert.match(resistanceControllerBlock, /this\.lightResistanceActive = true;/);
    assert.match(resistanceControllerBlock, /director\.pauseCurrentSceneForResistance\(\);/);
    assert.match(resistanceControllerBlock, /director\.interruptNarrationForResistance\(\);/);
    assert.match(resistanceControllerBlock, /director\.runInterruptResistPerformance\(\{/);
    assert.match(resistanceControllerBlock, /director\.runAngryExitPerformance\(\{/);
    assert.match(resistanceControllerBlock, /const angryExitNarrationDurationMs = director\.getGuideVoiceDurationMs\(/);
    assert.match(resistanceControllerBlock, /minDurationMs:\s*Number\.isFinite\(angryExitNarrationDurationMs\)/);
    assert.match(resistanceControllerBlock, /director\.requestTermination\(source \|\| 'angry_exit', 'angry_exit'\);/);
    assert.match(pointerDownBlock, /this\.resistanceController\.recordPointerDown\(event\);/);
    assert.match(handleInterruptBlock, /return this\.resistanceController\.handleInterrupt\(event\);/);
    assert.doesNotMatch(handleInterruptBlock, /DEFAULT_INTERRUPT_DISTANCE/);
    assert.doesNotMatch(handleInterruptBlock, /this\.interruptQualifyingMoveStreak \+= 1/);
    assert.doesNotMatch(handleInterruptBlock, /this\.interruptCount \+= 1/);
    assert.match(playResistanceBlock, /return this\.resistanceController\.playLightResistance\(x,\s*y,\s*options\);/);
    assert.match(angryExitBlock, /return this\.resistanceController\.abortAsAngryExit\(source\);/);
    assert.match(destroyBlock, /this\.resistanceController\.destroy\(\)/);
    assert.doesNotMatch(resistanceControllerBlock, /return this\.interruptController\.playLightResistance/);
    assert.doesNotMatch(resistanceControllerBlock, /return this\.interruptController\.abortAsAngryExit/);
    assert.doesNotMatch(playResistanceBlock, /this\.interruptController\.playLightResistance/);
    assert.doesNotMatch(angryExitBlock, /this\.interruptController\.abortAsAngryExit/);
    assert.doesNotMatch(destroyBlock, /this\.interruptController\.destroy\(\)/);
    assert.doesNotMatch(resetSource, /window\.TutorialResistanceControllers\.createResetInterruptController/);
    assert.doesNotMatch(resetSource, /window\.TutorialInterruptController/);
    assert.doesNotMatch(resetSource, /interruptController\.playLightResistance/);
    assert.doesNotMatch(resetSource, /interruptController\.abortAsAngryExit/);
    assert.doesNotMatch(resetSource, /createResetInterruptController/);
});

test('director wraps round-level look-at lifecycle with withLookAt helper', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const orchestratorSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/scene-orchestrator.js'), 'utf8');
    const directorSource = source.split('    class YuiGuideDirector {')[1];
    const helperBlock = directorSource.split('        isStopping() {')[1].split(
        '        setTutorialTakingOver(active) {',
        1
    )[0];
    const roundBlock = source.split('        async playAvatarFloatingRound(round, options) {')[1].split(
        '        disableInterrupts() {',
        1
    )[0];

    assert.match(helperBlock, /async withLookAt\(options,\s*run\) \{/);
    assert.match(helperBlock, /this\.ensurePersistentGhostCursorLookAtPerformance\(\{/);
    assert.match(helperBlock, /this\.stopIntroVoiceCursorLookAtPerformance\(lookAtHandle,\s*completeReason\)/);
    assert.match(roundBlock, /return this\.sceneOrchestrator\.playRound\(round,\s*options\);/);
    assert.match(orchestratorSource, /return await director\.withLookAt\(\{/);
    assert.match(orchestratorSource, /completeReason: roundId \+ '_complete'/);
    assert.doesNotMatch(orchestratorSource, /let lookAtHandle = null;/);
});

test('settings tour flow owns migrated settings tour concrete scene bodies', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const settingsTourFlowSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/settings-tour-flow.js'), 'utf8');
    const day4GuideSource = fs.readFileSync(
        path.join(repoRoot, 'static', 'tutorial/yui-guide/days/day4-companion-guide.js'),
        'utf8'
    );
    const day2Block = source.split('        async playDay2PersonalizationDetailScene')[1].split(
        '        async playDay5CharacterPanicScene',
        1
    )[0];
    const prepareSceneBlock = source.split('        async prepareAvatarFloatingScene(scene, options) {')[1].split(
        '        async runDay6PluginOpenAgentPanelFlow',
        1
    )[0];
    const resolveTargetBlock = source.split('        async resolveAvatarFloatingTarget(scene, role) {')[1].split(
        '        async resolveAvatarFloatingPersistent',
        1
    )[0];
    const inputIntroSceneBlock = source.split('        isAvatarFloatingInputIntroScene(scene) {')[1].split(
        '        getAvatarFloatingIntroSpotlightTarget(scene) {',
        1
    )[0];
    const introExternalizedKindBlock = source.split('        getAvatarFloatingIntroExternalizedSpotlightKind(scene) {')[1].split(
        '        getAvatarFloatingIntroExternalizedCursorOptions(scene) {',
        1
    )[0];
    const chatBlock = source.split('        async playDay4ChatSettingsScene')[1].split(
        '        async playDay4ModelBehaviorScene',
        1
    )[0];
    const modelBlock = source.split('        async playDay4ModelBehaviorScene')[1].split(
        '        async playDay4GazeFollowScene',
        1
    )[0];
    const gazeBlock = source.split('        async playDay4GazeFollowScene')[1].split(
        '        async playDay4PrivacyModeScene',
        1
    )[0];
    const privacyBlock = source.split('        async playDay4PrivacyModeScene')[1].split(
        '        async playDay5CharacterSettingsScene',
        1
    )[0];
    const characterSettingsBlock = source.split('        async playDay5CharacterSettingsScene')[1].split(
        '        async playDay2PersonalizationDetailScene',
        1
    )[0];
    const panicBlock = source.split('        async playDay5CharacterPanicScene')[1].split(
        '        async runAvatarFloatingSceneOperation',
        1
    )[0];
    const flowDay2Block = settingsTourFlowSource.split('        async playDay2PersonalizationDetailScene')[1].split(
        '        async playDay4ChatSettingsScene',
        1
    )[0];
    const flowChatBlock = settingsTourFlowSource.split('        async playDay4ChatSettingsScene')[1].split(
        '        async playDay4ModelBehaviorScene',
        1
    )[0];
    const flowModelBlock = settingsTourFlowSource.split('        async playDay4ModelBehaviorScene')[1].split(
        '        async playPanelTourScene',
        1
    )[0];
    const flowPanelTourBlock = settingsTourFlowSource.split('        async playPanelTourScene')[1].split(
        '        async playDay4GazeFollowScene',
        1
    )[0];
    const flowGazeBlock = settingsTourFlowSource.split('        async playDay4GazeFollowScene')[1].split(
        '        async playDay4PrivacyModeScene',
        1
    )[0];
    const flowPrivacyBlock = settingsTourFlowSource.split('        async playDay4PrivacyModeScene')[1].split(
        '        async playDay5CharacterSettingsScene',
        1
    )[0];
    const flowCharacterSettingsBlock = settingsTourFlowSource.split('        async playDay5CharacterSettingsScene')[1].split(
        '        async playDay5CharacterPanicScene',
        1
    )[0];
    const flowPanicBlock = settingsTourFlowSource.split('        async playDay5CharacterPanicScene')[1].split(
        '        prepareNarration(scene) {',
        1
    )[0];
    const day4ChatSettingsSceneBlock = day4GuideSource.split("id: 'day4_chat_settings'")[1].split(
        "id: 'day4_model_behavior'",
        1
    )[0];
    const day4IntroSceneBlock = day4GuideSource.split("id: 'day4_intro_companion'")[1].split(
        "id: 'day4_chat_settings'",
        1
    )[0];

    assert.match(source, /this\.settingsTourFlow = new TutorialSettingsTourFlow\.SettingsTourFlow\(this\);/);
    assert.match(day2Block, /return this\.settingsTourFlow\.playDay2PersonalizationDetailScene\(scene,\s*\{/);
    assert.match(chatBlock, /return this\.settingsTourFlow\.playDay4ChatSettingsScene\(scene,\s*\{/);
    assert.match(modelBlock, /return this\.settingsTourFlow\.playDay4ModelBehaviorScene\(scene,\s*\{/);
    assert.match(gazeBlock, /return this\.settingsTourFlow\.playDay4GazeFollowScene\(scene,\s*\{/);
    assert.match(privacyBlock, /return this\.settingsTourFlow\.playDay4PrivacyModeScene\(scene,\s*\{/);
    assert.match(characterSettingsBlock, /return this\.settingsTourFlow\.playDay5CharacterSettingsScene\(scene,\s*\{/);
    assert.match(panicBlock, /return this\.settingsTourFlow\.playDay5CharacterPanicScene\(scene,\s*\{/);
    assert.match(settingsTourFlowSource, /isSceneStale\(sceneRunId\) \{/);
    assert.match(settingsTourFlowSource, /async finalizeNarration\(sceneRunId,\s*narration,\s*context\) \{/);
    assert.match(settingsTourFlowSource, /async tourPanel\(scene,\s*sceneRunId,\s*panel,\s*narrationPromise,\s*options\) \{/);
    assert.match(settingsTourFlowSource, /getPanelTourSchema\(scene\) \{/);
    assert.match(settingsTourFlowSource, /async playPanelTourScene\(scene,\s*context,\s*schema\) \{/);
    assert.match(inputIntroSceneBlock, /sceneId === 'day4_intro_companion'/);
    assert.match(introExternalizedKindBlock, /return 'capsule-input';/);
    assert.match(day4IntroSceneBlock, /target:\s*'chat-capsule-input'/);
    assert.match(
        settingsTourFlowSource,
        /runPanelNarrationEllipse[\s\S]*director\.setHomePcCursorOutputSuppressedForExternalizedChat\(false\);[\s\S]*director\.cursor\.runPauseAwareEllipse/
    );
    assert.match(
        settingsTourFlowSource,
        /const waitForEllipseYield = async \(\) => \{[\s\S]*Promise\.race\(\[narrationSettledPromise,\s*delayPromise\]\)/
    );
    assert.match(
        settingsTourFlowSource,
        /director\.cursor\.runPauseAwareEllipse[\s\S]*await waitForEllipseYield\(\);/
    );
    assert.match(
        source,
        /async moveCursorToElement\(element,\s*durationMs,\s*options\) \{[\s\S]*this\.setHomePcCursorOutputSuppressedForExternalizedChat\(false\);[\s\S]*this\.cursor\.moveToRect/
    );
    assert.match(
        source,
        /async moveCursorToTrackedElement\(element,\s*durationMs,\s*options\) \{[\s\S]*this\.setHomePcCursorOutputSuppressedForExternalizedChat\(false\);[\s\S]*this\.cursor\.moveToPoint/
    );
    assert.match(flowChatBlock, /return this\.playPanelTourScene\(scene,\s*context,\s*this\.getPanelTourSchema\(scene\)\);/);
    assert.match(flowModelBlock, /return this\.playPanelTourScene\(scene,\s*context,\s*this\.getPanelTourSchema\(scene\)\);/);
    assert.match(flowPanelTourBlock, /const narration = this\.prepareNarration\(scene\);/);
    assert.match(flowPanelTourBlock, /this\.createNarrationPromise\(scene,\s*narration\)/);
    assert.match(day4ChatSettingsSceneBlock, /deferSettingsSidePanelUntilCursorClick:\s*true/);
    assert.match(prepareSceneBlock, /const deferSettingsSidePanelUntilCursorClick = !!\(/);
    assert.match(
        prepareSceneBlock,
        /operation\.indexOf\('show-settings-sidepanel:'\) === 0[\s\S]*&& !deferSettingsSidePanelUntilCursorClick[\s\S]*this\.ensureAvatarFloatingSettingsSidePanel/
    );
    assert.match(
        resolveTargetBlock,
        /targetKey\.indexOf\('settings-sidepanel:'\) === 0[\s\S]*scene\.deferSettingsSidePanelUntilCursorClick === true[\s\S]*return this\.getAvatarFloatingSidePanel\(type\);[\s\S]*this\.ensureAvatarFloatingSettingsSidePanel\(type\)/
    );
    assert.match(flowPanelTourBlock, /onClickStart: \(\) => director\.openSettingsPanel\(\)/);
    assert.match(flowPanelTourBlock, /this\.tourPanel\(scene,\s*sceneRunId,\s*touredPanel,\s*narrationPromise/);
    assert.match(flowPanelTourBlock, /return this\.finalizeNarration\(sceneRunId,\s*narration,\s*normalizedContext\);/);
    for (const block of [
        flowDay2Block,
        flowGazeBlock,
        flowPrivacyBlock,
        flowCharacterSettingsBlock,
        flowPanicBlock
    ]) {
        assert.match(block, /const narration = this\.prepareNarration\(scene\);/);
        assert.match(block, /this\.createNarrationPromise\(scene,\s*narration/);
        assert.match(block, /return this\.finalizeNarration\(sceneRunId,\s*narration,\s*normalizedContext\);/);
        assert.doesNotMatch(block, /sceneRunId !== director\.sceneRunId \|\| director\.isStopping\(\)/);
        assert.doesNotMatch(block, /return this\.finalize\(sceneRunId,\s*\{/);
        assert.doesNotMatch(block, /this\.getAvatarFloatingSceneButtons\(scene\)/);
        assert.doesNotMatch(block, /this\.speakGuideLine\(text,\s*\{/);
        assert.doesNotMatch(block, /this\.armPendingGuideMessageActionTimeout\(12000\);/);
    }
});

test('director routes cursor anchor persistence through CursorAnchorStore', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const orchestratorSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/scene-orchestrator.js'), 'utf8');
    const constructorBlock = source.split('    class YuiGuideDirector {')[1].split(
        '            this.keydownHandler = this.onKeyDown.bind(this);',
        1
    )[0];
    const externalAnchorBlock = source.split('        getExternalizedChatCursorAnchorPoint(maxAgeMs) {')[1].split(
        '        rememberAvatarFloatingSceneCursorAnchorFromExternalizedChat',
        1
    )[0];
    const remoteAnchorBlock = source.split('        onExternalChatCursorAnchor(event) {')[1].split(
        '        onExternalChatReady()',
        1
    )[0];
    const sceneAnchorBlock = source.split('        rememberAvatarFloatingSceneCursorAnchorPoint(sceneId, point) {')[1].split(
        '        getAvatarFloatingSceneCursorAnchor',
        1
    )[0];
    const sceneAnchorGetterBlock = source.split('        getAvatarFloatingSceneCursorAnchor(sceneIds) {')[1].split(
        '        getPreviousAvatarFloatingSceneCursorAnchor',
        1
    )[0];
    const playScenePreamble = orchestratorSource.split('        async playScene(scene, day, index, total) {')[1].split(
        '            director.clearSceneTimers();',
        1
    )[0];

    assert.match(source, /class CursorAnchorStore/);
    assert.match(constructorBlock, /this\.cursorAnchorStore = new CursorAnchorStore\(\);/);
    assert.match(externalAnchorBlock, /this\.cursorAnchorStore\.getLatestExternalizedPoint\(maxAgeMs\)/);
    assert.match(remoteAnchorBlock, /this\.cursorAnchorStore\.rememberLatestExternalizedPoint\(\{/);
    assert.match(sceneAnchorBlock, /this\.cursorAnchorStore\.rememberScenePoint\(sceneId,\s*point\)/);
    assert.match(sceneAnchorGetterBlock, /this\.cursorAnchorStore\.getScenePoint\(sceneIds\)/);
    assert.match(playScenePreamble, /director\.cursorAnchorStore\.clear\(\);/);
    assert.doesNotMatch(playScenePreamble, /avatarFloatingSceneCursorAnchorPoints = Object\.create/);
});

test('director delegates avatar floating scene operations through OperationRegistry facade', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const operationRegistrySource = fs.readFileSync(
        path.join(repoRoot, 'static', 'tutorial/core/operation-registry.js'),
        'utf8'
    );
    const constructorBlock = source.split('    class YuiGuideDirector {')[1].split(
        '            this.keydownHandler = this.onKeyDown.bind(this);',
        1
    )[0];
    const operationEntryBlock = source.split(
        '        async runAvatarFloatingSceneOperation(scene, primaryTarget, narrationStartedAt, narrationPromise) {'
    )[1].split(
        '        closeChatToolPopover() {',
        1
    )[0];
    const operationRegistryBlock = operationRegistrySource;

    const operationRegistryApi = require('./tutorial/core/operation-registry.js');
    assert.strictEqual(typeof operationRegistryApi.OperationRegistry, 'function');
    assert.match(source, /const TutorialOperationRegistry = window\.TutorialOperationRegistry \|\| \{\};/);
    assert.match(source, /const OperationRegistry = TutorialOperationRegistry\.OperationRegistry;/);
    assert.doesNotMatch(source, /    class OperationRegistry \{/);
    assert.doesNotMatch(source, /runAvatarFloatingSceneOperationLegacy/);
    assert.match(constructorBlock, /this\.operationRegistry = new OperationRegistry\(this,\s*\{\s*registry: this\.targetGeometryRegistry,\s*pluginDashboardWindowName: PLUGIN_DASHBOARD_WINDOW_NAME,\s*resolveGuideLocale: resolveGuideLocale\s*\}\);/);
    assert.match(operationEntryBlock, /return this\.operationRegistry\.run\(scene,\s*primaryTarget,\s*narrationStartedAt,\s*narrationPromise\);/);
    assert.match(operationRegistryBlock, /class OperationRegistry/);
    assert.match(operationRegistryBlock, /resolveTargetEntry\(targetKey\) \{/);
    assert.match(operationRegistryBlock, /resolveTarget\(targetKey,\s*fallbackTarget\) \{/);
    assert.match(operationRegistryBlock, /this\.operationHandlers = \[\];/);
    assert.match(operationRegistryBlock, /registerOperation\(matcher,\s*handler\) \{/);
    assert.match(operationRegistryBlock, /registerBuiltInOperations\(\) \{/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('day2-open-settings-personalization',\s*\(\) => this\.runDay2OpenSettingsPersonalization\(\)\);/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('day2-settings-detail',\s*\(\) => this\.runDay2SettingsDetail\(\)\);/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('day4-animation-distance-showcase'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('day1-managed-scene:takeover_capture_cursor'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\(\{ prefix: 'day1-managed-scene-settled:' \},\s*\(\) => true\);/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('day6-plugin-open-agent-panel-flow'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('day6-plugin-open-management-panel-flow'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('day6-plugin-dashboard-handoff-flow'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('day6-plugin-sidepanel-flow'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('rotate-galgame-tool-into-center'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\(\(context\) => \([\s\S]*context\.operation\.indexOf\('show-agent-sidepanel:'\) === 0[\s\S]*context\.scene\.activateSecondaryAction === true/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('cleanup',\s*\(context\) => this\.runCleanup\(context\.scene\)\);/);
    assert.match(operationRegistryBlock, /this\.registerOperation\(\(context\) => \([\s\S]*!context\.operation[\s\S]*context\.operation === 'show-task-hud'[\s\S]*context\.operation\.indexOf\('show-settings-sidepanel:'\) === 0/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('settings-peek-panic'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\(\{ prefix: 'show-settings-menu:' \}/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('show-settings-management'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('click'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('open-agent'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('open-screen-popup'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('open-mic-popup'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('open-compact-history-during-narration'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('open-compact-tool-fan'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('open-avatar-tool-menu'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('show-avatar-tools-then-hide-after-narration'/);
    assert.match(operationRegistryBlock, /this\.registerOperation\('toggle-avatar-tool-after-narration'/);
    assert.match(operationRegistryBlock, /async runDay2OpenSettingsPersonalization\(\) \{/);
    assert.match(operationRegistryBlock, /async runDay2SettingsDetail\(\) \{/);
    assert.match(operationRegistryBlock, /async runDay4AnimationDistanceShowcase\(scene,\s*narrationStartedAt\) \{/);
    assert.match(operationRegistryBlock, /async runDay1TakeoverCaptureCursor\(scene\) \{/);
    assert.match(operationRegistryBlock, /captureDay1TakeoverAgentSwitches/);
    assert.match(operationRegistryBlock, /async runCleanup\(scene\) \{/);
    assert.match(operationRegistryBlock, /sceneId === 'day1_takeover_return_control'/);
    assert.match(operationRegistryBlock, /return await this\.director\.restoreDay1TakeoverAgentSwitches\('day1-return-control'\);/);
    assert.match(operationRegistryBlock, /async runDay6PluginOpenAgentPanelFlow\(scene\) \{/);
    assert.match(operationRegistryBlock, /async runDay6PluginOpenManagementPanelFlow\(scene\) \{/);
    assert.match(operationRegistryBlock, /async runDay6PluginDashboardHandoffFlow\(scene,\s*narrationStartedAt\) \{/);
    assert.match(operationRegistryBlock, /async runDay6PluginSidePanelFlow\(scene,\s*narrationStartedAt\) \{/);
    assert.match(operationRegistryBlock, /async runDay3GalgameWheelDragScene\(scene,\s*primaryTarget\) \{/);
    assert.match(operationRegistryBlock, /async runShowAgentSidePanelAction\(scene,\s*operation\) \{/);
    assert.match(operationRegistryBlock, /async runPreparedNoopOperation\(scene,\s*operation\) \{/);
    assert.match(operationRegistryBlock, /async runSettingsPeekPanic\(scene,\s*primaryTarget,\s*narrationStartedAt\) \{/);
    assert.match(operationRegistryBlock, /async runShowSettingsMenu\(operation\) \{/);
    assert.match(operationRegistryBlock, /async runShowSettingsManagement\(scene\) \{/);
    assert.match(operationRegistryBlock, /async runClick\(primaryTarget\) \{/);
    assert.match(operationRegistryBlock, /async runOpenAgent\(scene\) \{/);
    assert.match(operationRegistryBlock, /async runOpenScreenPopup\(primaryTarget\) \{/);
    assert.match(operationRegistryBlock, /async runOpenMicPopup\(primaryTarget\) \{/);
    assert.match(operationRegistryBlock, /async runOpenCompactHistoryDuringNarration\(scene,\s*narrationStartedAt\) \{/);
    assert.match(operationRegistryBlock, /async runOpenCompactToolFan\(primaryTarget\) \{/);
    assert.match(operationRegistryBlock, /async runOpenAvatarToolMenu\(scene,\s*primaryTarget\) \{/);
    assert.match(operationRegistryBlock, /async runShowAvatarToolsThenHideAfterNarration\(scene,\s*primaryTarget,\s*narrationStartedAt,\s*narrationPromise\) \{/);
    assert.match(operationRegistryBlock, /async runToggleAvatarToolAfterNarration\(scene,\s*narrationStartedAt\) \{/);
    assert.match(operationRegistryBlock, /this\.resolveTarget\('chat-tool-toggle',\s*primaryTarget\)/);
    assert.match(operationRegistryBlock, /this\.resolveTarget\('chat-avatar-tools',\s*primaryTarget\)/);
    assert.match(operationRegistryBlock, /this\.resolveTarget\('chat-avatar-tools'\)/);
    assert.doesNotMatch(operationRegistryBlock, /runAvatarFloatingSceneOperationLegacy/);
    assert.match(operationRegistryBlock, /for \(let index = 0; index < this\.operationHandlers\.length; index \+= 1\) \{/);
    assert.match(operationRegistryBlock, /return true;\s*\}\s*\}\s*return \{/);
});

test('day3 Galgame guide drag follows the compact tool wheel arc and holds the target', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const overlaySource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/overlay.js'), 'utf8');
    const day3GuideSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/days/day3-interaction-guide.js'), 'utf8');
    const appInterpageSource = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');
    const sceneOrchestratorSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/scene-orchestrator.js'), 'utf8');
    const operationRegistrySource = fs.readFileSync(
        path.join(repoRoot, 'static', 'tutorial/core/operation-registry.js'),
        'utf8'
    );
    const block = source.split('        async runDay3GalgameWheelDragScene(scene, primaryTarget) {')[1].split(
        '        async resolveDay3GalgameWheelSlotTarget',
        1
    )[0];
    const localDragBlock = block.split('            const movePromise = this.cursor.moveCursorAlongPoints(arcPoints, {')[1].split(
        '            }).then((moved) => {',
        1
    )[0];
    const pcOverlayMoveBlock = overlaySource.split('        moveCursorTo(x, y, options) {')[1].split(
        '        clickCursor(durationMs) {',
        1
    )[0];
    const externalizedCursorEffectBlock = source.split('        setExternalizedChatCursorEffect(kind, effect, options) {')[1].split(
        '        clearExternalizedChatSpotlightOnly() {',
        1
    )[0];
    const clearExternalizedGuideTargetBlock = source.split('        clearExternalizedChatGuideTarget(options) {')[1].split(
        '        createAvatarFloatingUnionTarget',
        1
    )[0];
    const externalizedArcBlock = appInterpageSource.split('    function applyYuiGuideChatCursorArc(kind, options) {')[1].split(
        '    function clearYuiGuideChatSpotlightTracking()',
        1
    )[0];
    const externalizedSetCursorBlock = appInterpageSource.split("            case 'yui_guide_set_chat_cursor': {")[1].split(
        "            case 'yui_guide_chat_cursor_anchor':",
        1
    )[0];
    const externalizedApplyCursorBlock = appInterpageSource.split('    function applyYuiGuideChatCursor(kind, options) {')[1].split(
        '    function applyYuiGuideChatCursorDrag(kind, options) {',
        1
    )[0];
    const externalizedBranchBlock = sceneOrchestratorSource.split('            } else if (externalizedSceneTargetKind) {')[1].split(
        '            } else {',
        1
    )[0];
    const day3ChoicesBlock = day3GuideSource.split("id: 'day3_galgame_choices'")[1].split(
        "id: 'day3_wrap'",
        1
    )[0];

    assert.doesNotMatch(block, /const dragDeltaY = -100;/);
    assert.doesNotMatch(block, /deltaY: dragDeltaY/);
    assert.match(block, /const dragArcFraction = 1 \/ 5;/);
    assert.match(block, /buildDay3GalgameWheelArcPoints/);
    assert.match(block, /const dragRotateDirection = -1;/);
    assert.match(block, /arcExternalizedChatCursor\('galgame'/);
    assert.match(block, /effect:\s*'click'/);
    assert.match(block, /effectDurationMs: dragSettleWaitMs/);
    assert.match(block, /setExternalizedChatCursorEffect\('galgame', 'click'/);
    assert.match(block, /durationMs:\s*0/);
    assert.doesNotMatch(externalizedSetCursorBlock, /isStaleYuiGuideChatCursorCommandForActiveArc/);
    assert.doesNotMatch(externalizedSetCursorBlock, /cancelYuiGuideChatCursorArcForCommand/);
    assert.doesNotMatch(appInterpageSource, /function isStaleYuiGuideChatCursorCommandForActiveArc/);
    assert.doesNotMatch(appInterpageSource, /function cancelYuiGuideChatCursorArcForCommand/);
    assert.match(appInterpageSource, /function getYuiGuideChatSpotlightItemTargets\(kind\) \{[\s\S]*document\.getElementById\('composer-tool-popover-compact'\)/);
    assert.match(appInterpageSource, /function getYuiGuideChatSpotlightItemTargets\(kind\) \{[\s\S]*document\.getElementById\('composer-avatar-tool-quickbar'\)/);
    assert.match(appInterpageSource, /function getYuiGuideChatCursorTarget\(kind, options\) \{[\s\S]*normalizedOptions\.targetIndex/);
    assert.match(appInterpageSource, /targets\[Math\.min\(targetIndex, targets\.length - 1\)\]/);
    assert.match(appInterpageSource, /function ensureYuiGuideChatCursorElement\(\) \{[\s\S]*yui-guide-chat-cursor/);
    assert.match(externalizedApplyCursorBlock, /return moveYuiGuideChatCursor\(kind, getYuiGuideChatCursorTargetPoint\(kind, normalizedOptions\), normalizedOptions\)/);
    assert.doesNotMatch(appInterpageSource, /kind === 'avatar-tools'\) \{[\s\S]*getYuiGuideChatVisibleElement\('#react-chat-window-root \.composer-emoji-btn'\)/);
    assert.match(appInterpageSource, /function getYuiGuideCompactToolWheelCenterPoint\(\) \{[\s\S]*--compact-tool-wheel-center-x[\s\S]*--compact-tool-wheel-center-y/);
    assert.match(appInterpageSource, /function buildYuiGuideChatCursorArcMotion\(kind, options\) \{[\s\S]*kind === 'galgame'[\s\S]*getYuiGuideCompactToolWheelCenterPoint\(\)[\s\S]*Math\.hypot/);
    assert.match(appInterpageSource, /var totalAngle = direction \* Math\.PI \* 2 \* fraction;/);
    assert.match(externalizedArcBlock, /yuiGuideChatCursorRequestToken = yuiGuideChatCursorRequestToken \+ 1;/);
    assert.match(externalizedArcBlock, /var cursorRequestToken = yuiGuideChatCursorRequestToken;/);
    assert.match(externalizedArcBlock, /var arcRequestToken = \+\+yuiGuideChatCursorArcRequestToken;/);
    assert.match(externalizedArcBlock, /var motion = buildYuiGuideChatCursorArcMotion\(kind, options \|\| \{\}\);/);
    assert.match(externalizedArcBlock, /moveYuiGuideChatCursor\(kind, motion\.start/);
    assert.match(externalizedArcBlock, /durationMs:\s*0/);
    assert.match(externalizedArcBlock, /var segmentDuration = Math\.max\(0, Math\.round\(duration \/ motion\.points\.length\)\);/);
    assert.match(externalizedArcBlock, /motion\.points\.forEach\(function \(point, index\) \{/);
    assert.match(externalizedArcBlock, /moveYuiGuideChatCursor\(kind, point/);
    assert.doesNotMatch(externalizedArcBlock, /yuiGuideChatCursorActiveArcTimestamp/);
    assert.match(externalizedArcBlock, /arcRequestToken !== yuiGuideChatCursorArcRequestToken[\s\S]*cursorRequestToken !== yuiGuideChatCursorRequestToken/);
    assert.match(externalizedArcBlock, /window\.setTimeout\(function \(\) \{[\s\S]*rememberYuiGuideChatCursorScreenPoint\(finalScreenPoint/);
    assert.doesNotMatch(appInterpageSource, /yuiGuideChatCursorActiveArcTimestamp/);
    assert.match(block, /moveCursorAlongPoints\(arcPoints/);
    assert.match(
        pcOverlayMoveBlock,
        /const forcePcOverlayCursorOnly = normalizedOptions\.forcePcOverlay === true[\s\S]*typeof this\.overlayRenderer\.pcOverlayBridge\.moveCursorOnlyTo === 'function';/
    );
    assert.match(
        pcOverlayMoveBlock,
        /if \(this\.shouldForwardCursorToPcOverlay\(\) \|\| forcePcOverlayCursorOnly\) \{/
    );
    assert.match(
        pcOverlayMoveBlock,
        /this\.overlayRenderer\.moveCursorTo\(\s*x,\s*y,\s*durationMs,\s*cursorEffect,\s*cursorEffectDurationMs\s*\);/
    );
    assert.match(
        pcOverlayMoveBlock,
        /this\.overlayRenderer\.pcOverlayBridge\.moveCursorOnlyTo\(\s*x,\s*y,\s*durationMs,\s*cursorEffect,\s*cursorEffectDurationMs\s*\);/
    );
    assert.match(
        externalizedCursorEffectBlock,
        /this\.setHomePcCursorOutputSuppressedForExternalizedChat\(true\);/
    );
    assert.match(
        clearExternalizedGuideTargetBlock,
        /this\.setHomePcCursorOutputSuppressedForExternalizedChat\(false\);/
    );
    assert.equal(
        [...block.matchAll(/rotateCompactToolWheelForGuide\(dragRotateDirection,\s*1,\s*rotateReason\)/g)].length,
        2
    );
    assert.match(day3ChoicesBlock, /target:\s*'chat-galgame'/);
    assert.match(day3ChoicesBlock, /cursorTarget:\s*'chat-galgame'/);
    assert.match(day3ChoicesBlock, /cursorAction:\s*'hold'/);
    assert.match(day3ChoicesBlock, /cursorHoldFreezePoint:\s*true/);
    assert.match(day3ChoicesBlock, /cursorHoldSettleMs:\s*260/);
    assert.doesNotMatch(day3ChoicesBlock, /operation:/);
    assert.match(appInterpageSource, /freezePoint:\s*message\.freezePoint === true/);
    assert.match(appInterpageSource, /freezePoint:\s*event\.data\.freezePoint === true/);
    assert.match(appInterpageSource, /var freezePoint = normalizedOptions\.freezePoint === true;/);
    assert.match(externalizedApplyCursorBlock, /if \(freezePoint\) \{[\s\S]*yuiGuideChatCursorRequestToken \+= 1;[\s\S]*if \(kind === 'galgame'\) \{[\s\S]*yuiGuideChatCursorArcRequestToken \+= 1;/);
    assert.match(appInterpageSource, /yuiGuideChatCursorFrozenScreenPoints\[freezeKey\]/);
    assert.match(appInterpageSource, /if \(expandedForCursor && cursorOptions\.freezePoint !== true\)/);
    assert.match(sceneOrchestratorSource, /externalizedSceneCursorKind[\s\S]*scene\.cursorAction !== 'hold'[\s\S]*setExternalizedChatCursorEffect/);
    assert.doesNotMatch(operationRegistrySource, /tour-mini-game-choice-buttons/);
});

test('day3 avatar tool props cleanup waits for the real narration promise', () => {
    const operationRegistrySource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/operation-registry.js'), 'utf8');
    const sceneOrchestratorSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/scene-orchestrator.js'), 'utf8');
    const day3GuideSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/days/day3-interaction-guide.js'), 'utf8');
    const avatarToolsMatch = operationRegistrySource.match(
        /        async runShowAvatarToolsThenHideAfterNarration\(scene, primaryTarget, narrationStartedAt, narrationPromise\) \{([\s\S]*?)\n        async runToggleAvatarToolAfterNarration/
    );
    assert.ok(avatarToolsMatch, 'expected Avatar tools operation to accept the narration promise');
    const avatarToolsBlock = avatarToolsMatch[1];
    const avatarToolsPropsBlock = day3GuideSource.split("id: 'day3_avatar_tools_props'")[1].split(
        "id: 'day3_galgame_entry'",
        1
    )[0];
    const sceneOperationBlock = sceneOrchestratorSource.split(
        'director.runAvatarFloatingSceneOperation('
    )[0];

    assert.match(operationRegistrySource, /runShowAvatarToolsThenHideAfterNarration\([\s\S]*context\.narrationPromise/);
    assert.match(sceneOrchestratorSource, /director\.runAvatarFloatingSceneOperation\(\s*scene,\s*primaryTarget,\s*narrationStartedAt,\s*narrationPromise\s*\)/);
    assert.match(sceneOrchestratorSource, /playback\.narrationStartedAt,\s*playback\.narrationPromise/);
    assert.match(avatarToolsBlock, /await waitForNarrationPromise\(\);/);
    assert.doesNotMatch(avatarToolsBlock, /getAvatarFloatingNarrationDurationMs/);
    assert.doesNotMatch(avatarToolsBlock, /durationMs - elapsedMs/);
    assert.match(avatarToolsPropsBlock, /afterSceneDelayMs:\s*0/);
    assert.match(sceneOrchestratorSource, /scene\.afterSceneDelayMs/);
    assert.doesNotMatch(sceneOperationBlock, /director\.runAvatarFloatingSceneOperation\(scene,\s*primaryTarget,\s*narrationStartedAt\)/);
});

test('templates and frontend harness load OperationRegistry before Director', () => {
    const templatePaths = [
        path.join(repoRoot, 'templates', 'index.html'),
        path.join(repoRoot, 'templates', 'api_key_settings.html'),
        path.join(repoRoot, 'templates', 'memory_browser.html')
    ];
    for (const templatePath of templatePaths) {
        const templateSource = fs.readFileSync(templatePath, 'utf8');
        const registryIndex = templateSource.indexOf('/static/tutorial/core/operation-registry.js');
        const directorIndex = templateSource.indexOf('/static/tutorial/yui-guide/director.js');
        assert.notStrictEqual(registryIndex, -1, `${templatePath} should load tutorial/core/operation-registry.js`);
        assert.notStrictEqual(directorIndex, -1, `${templatePath} should load tutorial/yui-guide/director.js`);
        assert.ok(registryIndex < directorIndex, `${templatePath} should load OperationRegistry before Director`);
    }

    const harnessSource = fs.readFileSync(
        path.join(repoRoot, 'tests', 'frontend', 'test_home_prompt_flow.py'),
        'utf8'
    );
    const dependencyBlock = harnessSource.split('_YUI_DIRECTOR_DEPENDENCIES = (')[1].split(')', 1)[0];
    assert.match(dependencyBlock, /"tutorial\/visual\/resistance-controllers\.js",\s*"tutorial\/core\/operation-registry\.js",/);
});

test('director routes final teardown through performFullCleanup helper', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const directorSource = source.split('    class YuiGuideDirector {')[1];
    const destroyBlock = directorSource.split('        destroy() {')[1].split(
        '        onKeyDown(event) {',
        1
    )[0];

    assert.match(source, /performFullCleanup\(options\) \{/);
    assert.match(destroyBlock, /this\.performFullCleanup\(\{\s*destroyInteractionTakeover: true,\s*destroyOverlay: true\s*\}\);/);
    assert.doesNotMatch(destroyBlock, /this\.overlay\.hidePluginPreview\(\);\s*this\.overlay\.hideBubble\(\);\s*this\.overlay\.setAngry\(false\);\s*this\.setTutorialTakingOver\(false\);/);
});

test('director routes scene and chat stream timers through scoped resources', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const constructorBlock = source.split('    class YuiGuideDirector {')[1].split(
        '            this.keydownHandler = this.onKeyDown.bind(this);',
        1
    )[0];
    const clearSceneTimersBlock = source.split('        clearSceneTimers() {')[1].split(
        '        clearGuideChatStreamTimers() {',
        1
    )[0];
    const clearGuideChatStreamTimersBlock = source.split('        clearGuideChatStreamTimers() {')[1].split(
        '        scheduleGuideChatStream(callback, delayMs) {',
        1
    )[0];
    const scheduleGuideChatStreamBlock = source.split('        scheduleGuideChatStream(callback, delayMs) {')[1].split(
        '        schedule(callback, delayMs) {',
        1
    )[0];
    const scheduleBlock = source.split('        schedule(callback, delayMs) {')[1].split(
        '        clearNarrationResumeTimer() {',
        1
    )[0];

    assert.match(source, /function createYuiGuideScopedTutorialResources\(\) \{/);
    assert.match(constructorBlock, /this\.sceneResources = createYuiGuideScopedTutorialResources\(\);/);
    assert.match(constructorBlock, /this\.guideChatStreamResources = createYuiGuideScopedTutorialResources\(\);/);
    assert.match(clearSceneTimersBlock, /this\.sceneResources\.destroy\(\)/);
    assert.match(clearSceneTimersBlock, /this\.sceneResources = createYuiGuideScopedTutorialResources\(\);/);
    assert.match(clearGuideChatStreamTimersBlock, /this\.guideChatStreamResources\.destroy\(\)/);
    assert.match(clearGuideChatStreamTimersBlock, /this\.guideChatStreamResources = createYuiGuideScopedTutorialResources\(\);/);
    assert.match(scheduleGuideChatStreamBlock, /this\.guideChatStreamResources\.setTimeout\(/);
    assert.match(scheduleBlock, /this\.sceneResources\.setTimeout\(/);
    assert.doesNotMatch(scheduleGuideChatStreamBlock, /window\.setTimeout/);
    assert.doesNotMatch(scheduleBlock, /window\.setTimeout/);
});

test('manager keeps Yui-only lifecycle resources and excludes legacy driver tutorial code', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/universal-manager.js'), 'utf8');
    const constructorBlock = source.split('class UniversalTutorialManager {')[1].split('    logPromptFlow(', 1)[0];
    const destroyBlock = source.split("    async destroy(reason = 'destroy') {")[1].split(
        '    broadcastYuiGuideTerminationRequest',
        1
    )[0];
    const viewportWatcherBlock = source.split('    ensureTutorialLive2dViewportPlacementWatcher() {')[1].split(
        '    clearTutorialLive2dViewportPlacementWatcher() {',
        1
    )[0];
    const clearViewportWatcherBlock = source.split('    clearTutorialLive2dViewportPlacementWatcher() {')[1].split(
        '    beginTutorialAvatarOverride() {',
        1
    )[0];
    const scrollBlock = source.split('    blockTutorialScroll() {')[1].split(
        '    unblockTutorialScroll() {',
        1
    )[0];
    const unblockScrollBlock = source.split('    unblockTutorialScroll() {')[1].split(
        '    resetTutorialStartState() {',
        1
    )[0];
    const startBlock = source.split('    startTutorial() {')[1].split(
        '    resetTutorialStartState() {',
        1
    )[0];

    assert.match(source, /function createUniversalTutorialScopedResources\(\) \{/);
    assert.match(source, /window\.YuiGuideCommon\.createScopedTutorialResources/);
    assert.match(source, /setInterval\(callback, delayMs\) \{/);
    assert.match(source, /clearInterval\(intervalId\) \{/);
    assert.match(constructorBlock, /this\.managerResources = createUniversalTutorialScopedResources\(\);/);
    assert.match(constructorBlock, /this\.managerResources\.addEventListener\(\s*window,\s*'neko:yui-guide:desktop-skip-request'/);
    assert.doesNotMatch(constructorBlock, /window\.addEventListener\('neko:yui-guide:desktop-skip-request'/);
    assert.match(destroyBlock, /this\.managerResources\.destroy\(\)/);
    assert.doesNotMatch(destroyBlock, /window\.removeEventListener\('neko:yui-guide:desktop-skip-request'/);

    assert.match(viewportWatcherBlock, /this\._tutorialViewportPlacementResources = createUniversalTutorialScopedResources\(\);/);
    assert.match(viewportWatcherBlock, /this\._tutorialViewportPlacementResources\.setTimeout\(/);
    assert.match(viewportWatcherBlock, /this\._tutorialViewportPlacementResources\.addEventListener\(window,\s*'resize'/);
    assert.match(viewportWatcherBlock, /this\._tutorialViewportPlacementResources\.addEventListener\(window,\s*'electron-display-changed'/);
    assert.doesNotMatch(viewportWatcherBlock, /window\.addEventListener\('resize'/);
    assert.doesNotMatch(viewportWatcherBlock, /this\._tutorialViewportPlacementResizeTimer = setTimeout\(/);
    assert.match(clearViewportWatcherBlock, /this\._tutorialViewportPlacementResources\.destroy\(\)/);
    assert.doesNotMatch(clearViewportWatcherBlock, /window\.removeEventListener\('resize'/);

    assert.match(scrollBlock, /this\._tutorialScrollBlockResources = createUniversalTutorialScopedResources\(\);/);
    assert.match(scrollBlock, /this\._tutorialScrollBlockResources\.addEventListener\(window,\s*'wheel'/);
    assert.match(unblockScrollBlock, /this\._tutorialScrollBlockResources\.destroy\(\)/);
    assert.doesNotMatch(scrollBlock, /window\.addEventListener\('wheel'/);
    assert.doesNotMatch(unblockScrollBlock, /window\.removeEventListener\('wheel'/);

    assert.match(source, /getHomeAvatarFloatingGuideStartRound\(options = \{\}\) \{/);
    assert.match(source, /candidates\.push\(state\.pendingRound, state\.manualResetRound, 1\);/);
    assert.match(startBlock, /const round = this\.getHomeAvatarFloatingGuideStartRound\(\);/);
    assert.match(startBlock, /this\.startAvatarFloatingGuideRound\(round, \{/);
    assert.doesNotMatch(source, /startYuiGuideSceneSequence|getDirectYuiGuideSceneIdsForCurrentPage|getPendingYuiGuideResumeScene/);
    assert.doesNotMatch(source, /callYuiGuideDirector|notifyYuiGuideStepEnter|notifyYuiGuideStepLeave/);
    assert.doesNotMatch(source, /waitForDriver|initDriver|getDriverConfig|recreateDriverWithI18n|startTutorialSteps|onStepChange/);
    assert.doesNotMatch(source, /blockNekoTutorialClickEvent|blockTutorialPointerEvent|getStepsForPage|getModelManagerSteps|getCharaManagerSteps/);
    assert.doesNotMatch(source, /driver-popover|driver-overlay|driver-highlight|neko-tutorial-driver/);
});

test('director uses SpotlightController facade for guide highlight operations', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const visualControllerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/controllers.js'), 'utf8');
    const spotlightControllerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/spotlight-controller.js'), 'utf8');
    const resistanceControllerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/resistance-controllers.js'), 'utf8');
    const directorSource = source.split('    class YuiGuideDirector {')[1];
    const pauseCoordinatorBlock = resistanceControllerSource.split('    class PauseCoordinator {')[1].split(
        '    class TutorialTerminationRouter {',
        1
    )[0];
    const constructorBlock = directorSource.split(
        '            this.cursorAnchorStore = new CursorAnchorStore();',
        1
    )[0];
    const highlightWrapperBlock = directorSource.split('        getElementRect(element) {')[1].split(
        '        clearIntroFlow() {',
        1
    )[0];
    const resistancePauseBlock = directorSource.split('        pauseCurrentSceneForResistance() {')[1].split(
        '        resumeCurrentSceneAfterResistance() {',
        1
    )[0];
    const resistanceResumeBlock = directorSource.split('        resumeCurrentSceneAfterResistance() {')[1].split(
        '        waitUntilSceneResumed() {',
        1
    )[0];
    const destroyBlock = directorSource.split('        destroy() {')[1].split(
        '        onKeyDown(event) {',
        1
    )[0];

    assert.match(spotlightControllerSource, /class SpotlightController/);
    assert.match(visualControllerSource, /spotlightControllerApi\.SpotlightController/);
    assert.match(resistanceControllerSource, /class PauseCoordinator/);
    assert.match(source, /const PauseCoordinator = TutorialResistanceControllers\.PauseCoordinator;/);
    assert.doesNotMatch(source, /    class PauseCoordinator \{/);
    assert.doesNotMatch(constructorBlock, /this\.highlightController\s*=/);
    assert.match(constructorBlock, /this\.spotlightController = new TutorialVisualControllers\.SpotlightController\(TutorialVisualControllers\.createHighlightController\(\{/);
    assert.match(resistancePauseBlock, /this\.pauseCoordinator\.pauseForResistance\(\);/);
    assert.match(resistancePauseBlock, /preserveExternalizedChatSpotlightDuringResistance\(\);/);
    assert.match(constructorBlock, /registry: this\.targetGeometryRegistry/);
    assert.match(constructorBlock, /this\.pauseCoordinator = new PauseCoordinator\(\{/);
    assert.match(highlightWrapperBlock, /this\.spotlightController\.applyGuideHighlights\(config\)/);
    assert.match(highlightWrapperBlock, /this\.spotlightController\.clearAllExtraSpotlights\(\)/);
    assert.match(resistancePauseBlock, /this\.pauseCoordinator\.pauseForResistance\(\)/);
    assert.match(resistanceResumeBlock, /this\.pauseCoordinator\.resumeAfterResistance\(\)/);
    assert.doesNotMatch(resistancePauseBlock, /this\.cursor\.pause\(\)/);
    assert.doesNotMatch(resistancePauseBlock, /this\.cursor\.cancel\(\)/);
    assert.doesNotMatch(resistancePauseBlock, /this\.spotlightController\.pause\(\)/);
    assert.doesNotMatch(resistanceResumeBlock, /this\.cursor\.resume\(\)/);
    assert.doesNotMatch(resistanceResumeBlock, /this\.spotlightController\.resume\(\)/);
    assert.match(pauseCoordinatorBlock, /this\.pauseTokens = new Map\(\);/);
    assert.match(pauseCoordinatorBlock, /registerPauseToken\(name,\s*token\) \{/);
    assert.match(pauseCoordinatorBlock, /this\.registerPauseToken\('cursor',\s*normalizedOptions\.cursor\);/);
    assert.match(pauseCoordinatorBlock, /this\.registerPauseToken\('spotlight',\s*normalizedOptions\.spotlightController\);/);
    assert.match(pauseCoordinatorBlock, /pauseForResistance\(\) \{/);
    assert.match(pauseCoordinatorBlock, /this\.pauseTokens\.forEach\(\(token\) => \{/);
    assert.match(pauseCoordinatorBlock, /token\.pause\(\);/);
    assert.match(pauseCoordinatorBlock, /resumeAfterResistance\(\) \{/);
    assert.match(pauseCoordinatorBlock, /token\.resume\(\);/);
    assert.doesNotMatch(pauseCoordinatorBlock, /this\.cursor\.pause\(\)/);
    assert.doesNotMatch(pauseCoordinatorBlock, /this\.spotlightController\.pause\(\)/);
    assert.doesNotMatch(pauseCoordinatorBlock, /this\.cursor\.resume\(\)/);
    assert.doesNotMatch(pauseCoordinatorBlock, /this\.spotlightController\.resume\(\)/);
    assert.match(destroyBlock, /this\.spotlightController\.destroy\(\)/);
    assert.doesNotMatch(highlightWrapperBlock, /this\.highlightController\.applyGuideHighlights\(config\)/);
});

test('director registers settings side panels as pause tokens', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const resistanceControllerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/resistance-controllers.js'), 'utf8');
    const directorSource = source.split('    class YuiGuideDirector {')[1];
    const sidebarControllerBlock = resistanceControllerSource.split('    class SidebarPauseController {')[1].split(
        '    class PauseCoordinator {',
        1
    )[0];
    const constructorBlock = directorSource.split(
        '            this.cursorAnchorStore = new CursorAnchorStore();',
        1
    )[0];
    const ensureSettingsBlock = directorSource.split('        async ensureAvatarFloatingSettingsSidePanel(type) {')[1].split(
        '        async ensureAvatarFloatingAgentSidePanel(toggleId) {',
        1
    )[0];
    const ensureCharacterBlock = directorSource.split('        async ensureCharacterSettingsSidePanelVisible() {')[1].split(
        '        collapseCharacterSettingsSidePanel() {',
        1
    )[0];
    const collapseCharacterBlock = directorSource.split('        collapseCharacterSettingsSidePanel() {')[1].split(
        '        normalizeHighlightTarget(target, fallbackKey) {',
        1
    )[0];

    assert.match(resistanceControllerSource, /class SidebarPauseController/);
    assert.match(source, /const SidebarPauseController = TutorialResistanceControllers\.SidebarPauseController;/);
    assert.doesNotMatch(source, /    class SidebarPauseController \{/);
    assert.match(sidebarControllerBlock, /trackPanel\(panel\) \{/);
    assert.match(sidebarControllerBlock, /getPauseToken\(\) \{/);
    assert.match(sidebarControllerBlock, /pause\(\) \{/);
    assert.match(sidebarControllerBlock, /resume\(\) \{/);
    assert.match(sidebarControllerBlock, /data-yui-guide-sidebar-paused/);
    assert.match(constructorBlock, /this\.sidebarPauseController = new SidebarPauseController\(\{/);
    assert.match(constructorBlock, /this\.pauseCoordinator\.registerPauseToken\('sidebar',\s*this\.sidebarPauseController\.getPauseToken\(\)\);/);
    assert.match(ensureSettingsBlock, /this\.sidebarPauseController\.trackPanel\(panel\);/);
    assert.match(ensureCharacterBlock, /this\.sidebarPauseController\.trackPanel\(sidePanel\);/);
    assert.match(collapseCharacterBlock, /this\.sidebarPauseController\.trackPanel\(sidePanel\);/);
});

test('director routes termination requests through TutorialTerminationRouter', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const resistanceControllerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/resistance-controllers.js'), 'utf8');
    const directorSource = source.split('    class YuiGuideDirector {')[1];
    const routerBlock = resistanceControllerSource.split('    class TutorialTerminationRouter {')[1];
    const constructorBlock = directorSource.split(
        '            this.keydownHandler = this.onKeyDown.bind(this);',
        1
    )[0];
    const requestTerminationBlock = directorSource.split('        requestTermination(reason, tutorialReason) {')[1].split(
        '        skip(reason, tutorialReason) {',
        1
    )[0];
    const skipBlock = directorSource.split('        skip(reason, tutorialReason) {')[1].split(
        '        destroy() {',
        1
    )[0];
    const pageHideBlock = directorSource.split('        onPageHide() {')[1].split(
        '        get mobileTouchInteractionPassthrough() {',
        1
    )[0];
    const pluginSkipBlock = directorSource.split('        async handlePluginDashboardSkipRequest(data) {')[1].split(
        '        onWindowMessage(event) {',
        1
    )[0];

    assert.match(resistanceControllerSource, /class TutorialTerminationRouter/);
    assert.match(source, /const TutorialTerminationRouter = TutorialResistanceControllers\.TutorialTerminationRouter;/);
    assert.doesNotMatch(source, /    class TutorialTerminationRouter \{/);
    assert.match(constructorBlock, /this\.terminationRouter = new TutorialTerminationRouter\(this\);/);
    assert.match(routerBlock, /requestTermination\(reason,\s*tutorialReason\) \{/);
    assert.match(routerBlock, /skip\(reason,\s*tutorialReason\) \{/);
    assert.match(routerBlock, /async handlePluginDashboardSkipRequest\(data\) \{/);
    assert.doesNotMatch(routerBlock, /director\.beginTerminationVisualCleanup\(\);/);
    assert.doesNotMatch(routerBlock, /requestAvatarFloatingGuideCooperativeEnd\(finalReason\)/);
    assert.match(routerBlock, /typeof director\.tutorialManager\.requestTutorialEnd === 'function'/);
    assert.match(routerBlock, /return director\.tutorialManager\.requestTutorialEnd\(finalReason\);/);
    assert.match(routerBlock, /return director\.tutorialManager\.requestTutorialDestroy\(finalReason\);/);
    assert.match(routerBlock, /director\.tutorialManager\.handleTutorialSkipRequest\(\)/);
    assert.match(routerBlock, /await director\.skip\('skip', 'skip'\);/);
    assert.match(routerBlock, /director\.forwardPluginDashboardSkipRequestToButton\(detail\)/);
    assert.match(requestTerminationBlock, /return this\.terminationRouter\.requestTermination\(reason,\s*tutorialReason\);/);
    assert.match(skipBlock, /return this\.terminationRouter\.skip\(reason,\s*tutorialReason\);/);
    assert.match(pageHideBlock, /this\.tutorialManager\.requestTutorialEnd\('pagehide'\)/);
    assert.match(pageHideBlock, /try \{/);
    assert.match(pageHideBlock, /pagehide tutorial end threw/);
    assert.match(pageHideBlock, /this\.destroy\(\);/);
    assert.match(pluginSkipBlock, /return this\.terminationRouter\.handlePluginDashboardSkipRequest\(data\);/);
    assert.doesNotMatch(requestTerminationBlock, /beginTerminationVisualCleanup/);
    assert.doesNotMatch(skipBlock, /recordExperienceMetric\('skip'/);
    assert.doesNotMatch(pluginSkipBlock, /forwardPluginDashboardSkipRequestToButton/);
});

test('tutorial skip button reuses the manager tutorial end lifecycle', () => {
    const managerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/universal-manager.js'), 'utf8');
    const resistanceControllerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/resistance-controllers.js'), 'utf8');
    const skipBlock = managerSource.split('    handleTutorialSkipRequest() {')[1].split(
        '    /**\n     * 移除「跳过」按钮',
        1
    )[0];
    const cooperativeEndBlock = managerSource.split('    requestAvatarFloatingGuideCooperativeEnd(reason = ')[1].split(
        '    handleDesktopYuiGuideSkipRequest(event) {',
        1
    )[0];
    const ensureDirectorBlock = managerSource.split('    ensureYuiGuideDirector() {')[1].split(
        '        if (!this.isYuiGuideEnabledForPage()) {',
        1
    )[0];
    const avatarInteractionRestoreBlock = managerSource.split(
        "    restoreAvatarFloatingModelInteractionState(reason = 'tutorial-ended') {"
    )[1].split(
        '    applyTutorialChatIdentityOverride(detail) {',
        1
    )[0];
    const teardownBlock = managerSource.split('    _teardownTutorialUI() {')[1].split(
        '    /**\n     * 恢复所有在引导中修改过的元素',
        1
    )[0];
    const routerBlock = resistanceControllerSource.split('    class TutorialTerminationRouter {')[1].split(
        '    class TutorialResetInterruptController {',
        1
    )[0];

    assert.doesNotMatch(skipBlock, /director\.skip\('skip', 'skip'\)/);
    assert.doesNotMatch(skipBlock, /const director = this\.yuiGuideDirector;/);
    assert.doesNotMatch(skipBlock, /\.then\(\(\) => \{\s*this\.requestTutorialDestroy\('skip'\);/);
    assert.doesNotMatch(skipBlock, /this\.clearAllTutorialLifecycles\('skip'\);/);
    assert.match(skipBlock, /return Promise\.resolve\(this\.requestTutorialEnd\('skip'\)\);/);
    assert.match(ensureDirectorBlock, /this\.yuiGuideDirector\.destroyed \|\| this\.yuiGuideDirector\.terminationRequested/);
    assert.match(ensureDirectorBlock, /this\.yuiGuideDirector\.destroy\(\);/);
    assert.match(ensureDirectorBlock, /this\.yuiGuideDirector = null;/);
    assert.doesNotMatch(cooperativeEndBlock, /this\.setTutorialEndReason\(reason\);/);
    assert.doesNotMatch(cooperativeEndBlock, /this\.clearPcTutorialGlobalOverlay\(reason\);/);
    assert.doesNotMatch(cooperativeEndBlock, /this\.invalidateTutorialInteractionApply\(reason\);/);
    assert.match(cooperativeEndBlock, /return this\.requestTutorialEnd\(reason\);/);
    assert.doesNotMatch(cooperativeEndBlock, /return this\.onTutorialEnd\(\);/);
    assert.match(managerSource, /snapshotAvatarFloatingModelInteractionState\(reason = 'tutorial-started'\)/);
    assert.match(managerSource, /this\.snapshotAvatarFloatingModelInteractionState\('avatar-floating-guide-start'\);/);
    assert.match(avatarInteractionRestoreBlock, /const snapshot = this\._avatarFloatingModelLockSnapshot/);
    assert.match(avatarInteractionRestoreBlock, /window\.live2dManager\.setLocked\(!!snapshot\.live2d,\s*\{\s*updateFloatingButtons:\s*false\s*\}\);/);
    assert.match(avatarInteractionRestoreBlock, /window\.vrmManager\.core\.setLocked\(!!snapshot\.vrm\);/);
    assert.match(avatarInteractionRestoreBlock, /window\.mmdManager\.core\.setLocked\(!!snapshot\.mmd\);/);
    assert.match(avatarInteractionRestoreBlock, /window\.pngtuberManager\.setLocked\(!!snapshot\.pngtuber,\s*\{\s*updateFloatingButtons:\s*false\s*\}\);/);
    assert.match(managerSource, /pointerEvents:\s*\{/);
    assert.match(managerSource, /vrmCanvas: readPointerEvents\('vrm-canvas'\)/);
    assert.match(managerSource, /mmdCanvas: readPointerEvents\('mmd-canvas'\)/);
    assert.match(avatarInteractionRestoreBlock, /const hasSnapshotPointerEvents = snapshot\.pointerEvents/);
    assert.match(avatarInteractionRestoreBlock, /element\.style\.pointerEvents = snapshot\.pointerEvents\[pointerKey\] \|\| '';/);
    assert.match(avatarInteractionRestoreBlock, /activePrefix === 'live2d' \|\| activePrefix === 'pngtuber'/);
    assert.match(managerSource, /modelType === 'live3d'/);
    assert.match(managerSource, /live3d_sub_type/);
    assert.match(teardownBlock, /this\.restoreAvatarFloatingModelInteractionState\('teardown-early'\);/);
    assert.match(teardownBlock, /\.then\(\(\) => this\.restoreAvatarFloatingModelInteractionState\('tutorial-avatar-restored'\)\)/);
    assert.doesNotMatch(routerBlock, /requestAvatarFloatingGuideCooperativeEnd\(finalReason\)/);
    assert.match(routerBlock, /return director\.tutorialManager\.requestTutorialEnd\(finalReason\);/);
    assert.match(routerBlock, /return director\.tutorialManager\.requestTutorialDestroy\(finalReason\);/);
    assert.match(routerBlock, /await director\.skip\('skip', 'skip'\);/);
    assert.doesNotMatch(routerBlock, /director\.beginTerminationVisualCleanup\(\);/);
    assert.match(resistanceControllerSource, /director\.requestTermination\(source \|\| 'angry_exit', 'angry_exit'\);/);
    assert.match(resistanceControllerSource, /minDurationMs:\s*Number\.isFinite\(angryExitNarrationDurationMs\)/);
});

test('return petal transition cancels immediately when tutorial is skipped', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/petal-transition-controller.js'), 'utf8');
    const cancelBlock = source.split('        isCancelled() {')[1].split(
        '        waitForNarrationEnd(durationMs) {',
        1
    )[0];
    const waitBlock = source.split('        waitForNarrationEnd(durationMs) {')[1].split(
        '        async finishTransition(transition) {',
        1
    )[0];
    const finishBlock = source.split('        async finishTransition(transition) {')[1].split(
        '        async executeReturnTransition(options) {',
        1
    )[0];
    const cancelledFinishBlock = finishBlock.split('            if (this.isCancelled()) {')[1].split(
        '            if (typeof transition.done',
        1
    )[0];
    const executeBlock = source.split('        async executeReturnTransition(options) {')[1].split(
        '        fadeReturnModelOut(durationMs) {',
        1
    )[0];

    assert.match(cancelBlock, /typeof director\.isStopping === 'function'/);
    assert.match(cancelBlock, /director\.isStopping\(\)/);
    assert.match(waitBlock, /if \(this\.isCancelled\(\)\) \{[\s\S]*?resolve\(false\);/);
    assert.match(source, /cancelWhenStopped\(promise\) \{/);
    assert.match(source, /fadePromise = this\.cancelWhenStopped\(fadeModelOut\(baseTransitionDurationMs\)\);/);
    assert.match(finishBlock, /const completed = await this\.cancelWhenStopped\(transition\.done\(\)\);/);
    assert.match(finishBlock, /if \(completed === false\) \{[\s\S]*transition\.finish\(\);[\s\S]*return;/);
    assert.match(executeBlock, /const loadedPetalSequence = await this\.cancelWhenStopped\(petalSequencePromise\);/);
    assert.match(executeBlock, /if \(this\.isCancelled\(\)\) \{[\s\S]*await this\.finishTransition\(transition\);[\s\S]*return;/);
    assert.match(cancelledFinishBlock, /transition\.finish\(\);/);
    assert.match(cancelledFinishBlock, /return;/);
    assert.doesNotMatch(cancelledFinishBlock, /transition\.done\(\)/);
    assert.match(executeBlock, /if \(this\.isCancelled\(\)\) \{[\s\S]*?return;/);
    assert.match(executeBlock, /await this\.finishTransition\(transition\);/);
    assert.doesNotMatch(executeBlock, /await finishTransition\(transition\);/);
});

test('avatar floating auto-start rechecks pending state before delayed launch', () => {
    const managerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/universal-manager.js'), 'utf8');
    const maybeAutoBlock = managerSource.split('    async maybeStartAvatarFloatingGuideAutoRound(delayMs = 1200) {')[1].split(
        '    ensureTutorialSkipController() {',
        1
    )[0];
    const pendingCheckMatch = managerSource.match(
        /    isAvatarFloatingGuideRoundPendingAutoStart\(day\) \{([\s\S]*?)\n    \}\n\n    async maybeStartAvatarFloatingGuideAutoRound/
    );
    assert.ok(pendingCheckMatch, 'expected manager to expose stale auto-start pending guard');
    const pendingCheckBlock = pendingCheckMatch[1];

    assert.match(maybeAutoBlock, /if \(!this\.isAvatarFloatingGuideRoundPendingAutoStart\(round\)\) \{\s*return;\s*\}/);
    assert.match(pendingCheckBlock, /const state = loadAvatarFloatingGuideState\(\);/);
    assert.match(pendingCheckBlock, /state\.pendingRound !== round && state\.manualResetRound !== round/);
    assert.match(pendingCheckBlock, /state\.completedRounds\.includes\(round\)/);
    assert.match(pendingCheckBlock, /state\.skippedRounds\.includes\(round\)/);
});

test('tutorial destroy requests share the PC global overlay cleanup path', () => {
    const managerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/universal-manager.js'), 'utf8');
    const clearOverlayBlock = managerSource.split('    clearPcTutorialGlobalOverlay(reason = ')[1].split(
        '    requestTutorialEnd(reason = ',
        1
    )[0];
    const endRequestBlock = managerSource.split('    requestTutorialEnd(reason = ')[1].split(
        '    requestTutorialDestroy(reason = ',
        1
    )[0];
    const destroyRequestBlock = managerSource.split('    requestTutorialDestroy(reason = ')[1].split(
        '    requestAvatarFloatingGuideCooperativeEnd(reason = ',
        1
    )[0];
    const lifecycleCleanupBlock = managerSource.split('    clearAllTutorialLifecycles(reason = ')[1].split(
        '    normalizeTutorialEndRawReason(reason) {',
        1
    )[0];

    assert.match(clearOverlayBlock, /window\.nekoTutorialOverlay/);
    assert.match(clearOverlayBlock, /window\.nekoTutorialOverlay\.clear\(\{/);
    assert.match(clearOverlayBlock, /reason:\s*rawReason/);
    assert.match(clearOverlayBlock, /tutorialRunId:\s*tutorialRunId/);
    assert.match(clearOverlayBlock, /yuiGuidePcOverlayRunId/);
    assert.match(endRequestBlock, /this\.setTutorialEndReason\(reason\);[\s\S]*this\.clearAllTutorialLifecycles\(reason\);/);
    assert.doesNotMatch(endRequestBlock, /this\.clearPcTutorialGlobalOverlay\(reason\);/);
    assert.match(endRequestBlock, /return this\.onTutorialEnd\(\);/);
    assert.doesNotMatch(endRequestBlock, /this\.driver|waitForTeardown/);
    assert.match(destroyRequestBlock, /return this\.requestTutorialEnd\(reason\);/);
    assert.match(lifecycleCleanupBlock, /this\.clearPcTutorialGlobalOverlay\(rawReason\);/);
});

test('PC global overlay cleanup clears the stored run id before the next tutorial run', () => {
    const managerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/universal-manager.js'), 'utf8');
    const clearOverlayBlock = managerSource.split('    clearPcTutorialGlobalOverlay(reason = ')[1].split(
        '    requestTutorialEnd(reason = ',
        1
    )[0];

    assert.match(clearOverlayBlock, /window\.localStorage\.removeItem\('yuiGuidePcOverlayRunId'\)/);
});

test('PC global overlay cleanup notifies external chat windows to stop overlay relays', () => {
    const managerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/universal-manager.js'), 'utf8');
    const appInterpageSource = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');
    const clearOverlayBlock = managerSource.split('    clearPcTutorialGlobalOverlay(reason = ')[1].split(
        '    requestTutorialEnd(reason = ',
        1
    )[0];
    const externalCleanupBlock = appInterpageSource.split('    function clearYuiGuidePcOverlayBridgeState(reason, tutorialRunId) {')[1].split(
        '    function createYuiGuideTargetGeometryRegistry() {',
        1
    )[0];

    assert.match(clearOverlayBlock, /const lifecycleEndedMessage = \{/);
    assert.match(clearOverlayBlock, /window\.nekoTutorialOverlay\.relayToChat\(lifecycleEndedMessage\)/);
    assert.match(clearOverlayBlock, /window\.nekoTutorialOverlay\.relayToPet\(lifecycleEndedMessage\)/);
    assert.match(clearOverlayBlock, /window\.appInterpage\.nekoBroadcastChannel\.postMessage\(lifecycleEndedMessage\)/);
    assert.match(clearOverlayBlock, /action:\s*'yui_guide_tutorial_lifecycle_ended'/);
    const lifecycleMessageBlock = clearOverlayBlock.split('const lifecycleEndedMessage = {')[1].split('};', 1)[0];
    assert.match(lifecycleMessageBlock, /tutorialRunId:\s*tutorialRunId/);
    assert.match(appInterpageSource, /if \(message\.tutorialRunId && message\.action !== 'yui_guide_tutorial_lifecycle_ended'\) \{/);
    assert.match(appInterpageSource, /var bounds = metrics\.contentBounds \|\| metrics\.bounds \|\| \{ x: 0, y: 0 \};/);
    assert.match(externalCleanupBlock, /yuiGuidePcOverlayActive = false;/);
    assert.match(externalCleanupBlock, /yuiGuidePcOverlayReady = false;/);
    assert.match(externalCleanupBlock, /var endedRunId = typeof tutorialRunId === 'string' && tutorialRunId/);
    assert.match(externalCleanupBlock, /getExistingYuiGuidePcOverlayRunId\(\)/);
    assert.match(externalCleanupBlock, /yuiGuidePcOverlayRunIdOverride = '';/);
    assert.match(externalCleanupBlock, /yuiGuidePcOverlaySpotlights = \[\];/);
    assert.match(externalCleanupBlock, /yuiGuidePcOverlayCursor = null;/);
    assert.match(externalCleanupBlock, /clearYuiGuideChatPcSpotlightRects\(\);/);
    assert.match(externalCleanupBlock, /window\.localStorage\.removeItem\('yuiGuidePcOverlayRunId'\)/);
    assert.ok(
        externalCleanupBlock.indexOf("window.localStorage.removeItem('yuiGuidePcOverlayRunId');")
            < externalCleanupBlock.indexOf("applyYuiGuideChatSpotlight('',"),
        'external chat cleanup should forget the ended run before clearing through the bridge'
    );
    assert.match(externalCleanupBlock, /yuiGuideChatCursorRequestToken \+= 1;/);
    assert.match(externalCleanupBlock, /yuiGuideCompactToolWheelRotateRetryToken \+= 1;/);
    assert.match(externalCleanupBlock, /applyYuiGuideChatSpotlight\('', \{[\s\S]*pcOverlayRunId: endedRunId/);
    assert.match(externalCleanupBlock, /applyYuiGuideChatCursor\('', \{[\s\S]*pcOverlayRunId: endedRunId/);
    assert.match(externalCleanupBlock, /allowCreatePcOverlayRun: false/);
    assert.match(externalCleanupBlock, /skipPcOverlayBegin: true/);
    assert.doesNotMatch(externalCleanupBlock, /relayYuiGuideChatCommand\(\{\s*action: 'yui_guide_set_chat_cursor'/);
    assert.match(clearOverlayBlock, /Promise\.resolve\(clearResult\)\.then\(result => \{[\s\S]*window\.nekoTutorialOverlay\.clear\(\{ reason: rawReason \}\)/);
    assert.match(externalCleanupBlock, /window\.nekoTutorialOverlay\.clear\(\{/);
    assert.match(externalCleanupBlock, /tutorialRunId: endedRunId/);
    assert.match(externalCleanupBlock, /Promise\.resolve\(clearResult\)\.then\(function \(result\) \{[\s\S]*window\.nekoTutorialOverlay\.clear\(\{ reason: rawReason \}\)/);
    assert.match(appInterpageSource, /case 'yui_guide_tutorial_lifecycle_ended':/);
});

test('external chat ignores stale guide commands after lifecycle ended', () => {
    const appInterpageSource = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');
    const relayHandlerBlock = appInterpageSource.split('    function handleYuiGuideRelayedMessage(message) {')[1].split(
        '    function postInterpageMessage',
        1
    )[0];
    const clearStateBlock = appInterpageSource.split('    function clearYuiGuidePcOverlayBridgeState(reason, tutorialRunId) {')[1].split(
        '    // =====================================================================',
        1
    )[0];
    const sendPatchBlock = appInterpageSource.split('    function sendYuiGuidePcOverlayPatch(patch, retried, options) {')[1].split(
        '    function isYuiGuidePcCursorOnlyMode()',
        1
    )[0];
    const getRunIdBlock = appInterpageSource.split('    function getYuiGuidePcOverlayRunId() {')[1].split(
        '    function getExistingYuiGuidePcOverlayRunId() {',
        1
    )[0];
    const getExistingRunIdBlock = appInterpageSource.split('    function getExistingYuiGuidePcOverlayRunId() {')[1].split(
        '    function isYuiGuidePcOverlayRunEnded(runId) {',
        1
    )[0];
    const readStoredRunIdBlock = appInterpageSource.split('    function readStoredYuiGuidePcOverlayRunId() {')[1].split(
        '    function syncYuiGuidePcOverlayRunIdFromStorage() {',
        1
    )[0];
    const rememberRunIdBlock = appInterpageSource.split('    function rememberYuiGuidePcOverlayRunId(runId) {')[1].split(
        '    function getYuiGuidePcOverlayRunIdFromMessage(message) {',
        1
    )[0];
    const cursorRelayBlock = appInterpageSource.split('    function applyYuiGuideChatCursorRelay(message) {')[1].split(
        '    yuiGuideInterpageResources.addEventListener(window, ',
        1
    )[0];
    const broadcastStaleGuardBlock = appInterpageSource.split('nekoBroadcastChannel.onmessage = async function (event) {')[1].split(
        '                console.log(\'[BroadcastChannel] 收到消息:\', event.data.action);',
        1
    )[0];

    assert.match(appInterpageSource, /var yuiGuidePcOverlayEndedRunId = '';/);
    assert.match(appInterpageSource, /function isYuiGuidePcOverlayRunEnded\(runId\) \{/);
    assert.match(appInterpageSource, /function isYuiGuideLifecycleScopedAction\(action\) \{/);
    assert.match(appInterpageSource, /case 'yui_guide_set_chat_input_locked':/);
    assert.match(appInterpageSource, /case 'yui_guide_set_chat_spotlight':/);
    assert.match(appInterpageSource, /case 'yui_guide_set_chat_cursor':/);
    assert.match(clearStateBlock, /yuiGuidePcOverlayEndedRunId = endedRunId;/);
    assert.match(getRunIdBlock, /isYuiGuidePcOverlayRunEnded\(yuiGuidePcOverlayRunIdOverride\)/);
    assert.match(appInterpageSource, /function readStoredYuiGuidePcOverlayRunId\(\) \{/);
    assert.match(appInterpageSource, /function syncYuiGuidePcOverlayRunIdFromStorage\(\) \{/);
    assert.match(readStoredRunIdBlock, /window\.localStorage\.getItem\('yuiGuidePcOverlayRunId'\)/);
    assert.match(readStoredRunIdBlock, /isYuiGuidePcOverlayRunEnded\(storedRunId\)[\s\S]*window\.localStorage\.removeItem\('yuiGuidePcOverlayRunId'\)/);
    assert.match(getRunIdBlock, /var storedRunId = readStoredYuiGuidePcOverlayRunId\(\);/);
    assert.match(getRunIdBlock, /storedRunId !== yuiGuidePcOverlayRunIdOverride[\s\S]*yuiGuidePcOverlayActive = false/);
    assert.match(getExistingRunIdBlock, /var storedRunId = readStoredYuiGuidePcOverlayRunId\(\);/);
    assert.match(rememberRunIdBlock, /isYuiGuidePcOverlayRunEnded\(normalizedRunId\)[\s\S]*window\.localStorage\.removeItem\('yuiGuidePcOverlayRunId'\)/);
    assert.match(rememberRunIdBlock, /var storedRunId = readStoredYuiGuidePcOverlayRunId\(\);/);
    assert.match(rememberRunIdBlock, /storedRunId !== normalizedRunId[\s\S]*return storedRunId/);
    assert.match(relayHandlerBlock, /isYuiGuideLifecycleScopedAction\(message\.action\)/);
    assert.match(relayHandlerBlock, /isYuiGuidePcOverlayRunEnded\(message\.tutorialRunId\)/);
    assert.match(relayHandlerBlock, /clearYuiGuidePcOverlayBridgeState\('stale-after-lifecycle-ended', message\.tutorialRunId \|\| ''\);/);
    assert.match(relayHandlerBlock, /return true;\s*\}\s*if \(message\.tutorialRunId && message\.action !== 'yui_guide_tutorial_lifecycle_ended'\) \{/);
    assert.match(sendPatchBlock, /if \(isYuiGuidePcOverlayRunEnded\(sendOptions\.tutorialRunId\)\) \{/);
    assert.match(sendPatchBlock, /resolveYuiGuidePcOverlayRunIdForSend\(/);
    assert.match(sendPatchBlock, /if \(!runId \|\| isYuiGuidePcOverlayRunEnded\(runId\)\) \{/);
    assert.match(cursorRelayBlock, /if \(isYuiGuidePcOverlayRunEnded\(message\.tutorialRunId\)\) \{/);
    assert.match(broadcastStaleGuardBlock, /isYuiGuideLifecycleScopedAction\(message\.action\)/);
    assert.match(broadcastStaleGuardBlock, /isYuiGuidePcOverlayRunEnded\(message\.tutorialRunId\)/);
    assert.match(broadcastStaleGuardBlock, /clearYuiGuidePcOverlayBridgeState\('stale-after-lifecycle-ended', message\.tutorialRunId \|\| ''\);/);
    assert.match(broadcastStaleGuardBlock, /return;/);
});

test('external chat reuses tutorial PC overlay run id for capsule spotlight and cursor patches', () => {
    const appInterpageSource = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');
    const normalizeBridgeBlock = appInterpageSource.split('    function normalizeYuiGuideBridgeMessage(action, payload) {')[1].split(
        '    function postYuiGuideMessageToChat',
        1
    )[0];
    const relayHandlerBlock = appInterpageSource.split('    function handleYuiGuideRelayedMessage(message) {')[1].split(
        '    function postInterpageMessage',
        1
    )[0];
    const broadcastHandlerBlock = appInterpageSource.split("case 'handoff_sent':")[1].split(
        "case 'yui_guide_set_avatar_tool_menu_open':",
        1
    )[0];
    const cursorRelayBlock = appInterpageSource.split('    function applyYuiGuideChatCursorRelay(message) {')[1].split(
        '    yuiGuideInterpageResources.addEventListener(window, ',
        1
    )[0];
    const cursorPatchBlock = appInterpageSource.split('    function moveYuiGuideChatCursor(kind, point, options) {')[1].split(
        '    function applyYuiGuideChatCursor(kind, options) {',
        1
    )[0] + appInterpageSource.split('    function applyYuiGuideChatCursor(kind, options) {')[1].split(
        '    function applyYuiGuideChatCursorDrag(kind, options) {',
        1
    )[0];
    const spotlightBlock = appInterpageSource.split('    function applyYuiGuideChatSpotlight(kind, options) {')[1].split(
        '    function applyYuiGuideChatCursorRelay(message) {',
        1
    )[0];
    const spotlightUpdateBlock = appInterpageSource.split('    function updateYuiGuideChatSpotlight(kind, pcOverlayRunId) {')[1].split(
        '    function applyYuiGuideChatSpotlight(kind, options) {',
        1
    )[0];

    assert.match(normalizeBridgeBlock, /resolveCanonicalYuiGuideBridgeRunId\(message\)/);
    assert.match(normalizeBridgeBlock, /message\.tutorialRunId = canonicalRunId;/);
    assert.match(normalizeBridgeBlock, /message\.pcOverlayRunId = canonicalRunId;/);
    assert.doesNotMatch(normalizeBridgeBlock, /getYuiGuidePcOverlayRunId\(\)/);
    assert.match(appInterpageSource, /function rememberYuiGuidePcOverlayRunId\(runId\) \{/);
    assert.match(appInterpageSource, /function resolveCanonicalYuiGuideBridgeRunId\(message\) \{/);
    assert.match(appInterpageSource, /var tutorialRunId = message && typeof message\.tutorialRunId === 'string'/);
    assert.match(appInterpageSource, /return rememberYuiGuidePcOverlayRunId\(tutorialRunId\);/);
    assert.match(appInterpageSource, /var existingRunId = getExistingYuiGuidePcOverlayRunId\(\);/);
    assert.match(appInterpageSource, /return rememberYuiGuidePcOverlayRunId\(existingRunId\);/);
    assert.match(appInterpageSource, /var pcOverlayRunId = message && typeof message\.pcOverlayRunId === 'string'/);
    assert.match(appInterpageSource, /return rememberYuiGuidePcOverlayRunId\(pcOverlayRunId\);/);
    assert.match(appInterpageSource, /function resolveYuiGuidePcOverlayRunIdForSend\(requestedRunId, allowCreateRun\) \{/);
    assert.match(appInterpageSource, /storedRunId && storedRunId !== normalizedRequestedRunId/);
    assert.match(appInterpageSource, /function getYuiGuidePcOverlayRunIdFromMessage\(message\) \{/);
    assert.match(relayHandlerBlock, /rememberYuiGuidePcOverlayRunId\(message\.tutorialRunId\)/);
    assert.match(relayHandlerBlock, /pcOverlayRunId: getYuiGuidePcOverlayRunIdFromMessage\(message\)/);
    assert.match(relayHandlerBlock, /scheduleYuiGuideChatInputSpotlightRetry\(message\.kind \|\| '', getYuiGuidePcOverlayRunIdFromMessage\(message\)\)/);
    assert.match(broadcastHandlerBlock, /var cursorRunId = getYuiGuidePcOverlayRunIdFromMessage\(event\.data\);/);
    assert.match(broadcastHandlerBlock, /pcOverlayRunId: cursorRunId/);
    assert.match(broadcastHandlerBlock, /var spotlightRunId = getYuiGuidePcOverlayRunIdFromMessage\(event\.data\);/);
    assert.match(broadcastHandlerBlock, /pcOverlayRunId: spotlightRunId/);
    assert.match(broadcastHandlerBlock, /scheduleYuiGuideChatInputSpotlightRetry\(event\.data\.kind \|\| '', spotlightRunId\)/);
    assert.match(cursorRelayBlock, /pcOverlayRunId: message\.pcOverlayRunId \|\| getYuiGuidePcOverlayRunIdFromMessage\(message\)/);
    assert.match(cursorPatchBlock, /tutorialRunId: normalizedOptions\.pcOverlayRunId/);
    assert.match(spotlightBlock, /yuiGuideChatSpotlightPcOverlayRunId = pcOverlayRunId;/);
    assert.match(spotlightBlock, /ensureYuiGuideChatSpotlightTracking\(pcOverlayRunId\)/);
    assert.match(spotlightBlock, /updateYuiGuideChatSpotlight\(yuiGuideChatSpotlightKind, pcOverlayRunId\)/);
    assert.match(spotlightUpdateBlock, /tutorialRunId: pcOverlayRunId \|\| yuiGuideChatSpotlightPcOverlayRunId/);
    assert.match(spotlightUpdateBlock, /sendYuiGuidePcOverlayPatch\(\{ spotlights: pcRects \}, false, patchOptions\)/);
});

test('PC overlay bridges rotate stale run ids and replay current state', () => {
    const appInterpageSource = fs.readFileSync(path.join(repoRoot, 'static', 'app-interpage.js'), 'utf8');
    const overlaySource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/overlay.js'), 'utf8');
    const externalBridgeBlock = appInterpageSource.split('    function resetYuiGuidePcOverlayRunForRetry() {')[1].split(
        '    function createYuiGuideTargetGeometryRegistry() {',
        1
    )[0];
    const mainBridgeBlock = overlaySource.split('    function createPcOverlayBridge(doc) {')[1].split(
        '    const OverlayRendererClass = window.TutorialOverlayRenderer;',
        1
    )[0];

    assert.match(externalBridgeBlock, /window\.localStorage\.removeItem\('yuiGuidePcOverlayRunId'\)/);
    assert.match(appInterpageSource, /function getExistingYuiGuidePcOverlayRunId\(\) \{/);
    assert.match(externalBridgeBlock, /function handleYuiGuidePcOverlayStaleResult\(result, patch, attemptedRunId, retried\)/);
    assert.match(externalBridgeBlock, /var isStaleResponse = !!\(result && result\.stale === true\);/);
    assert.match(externalBridgeBlock, /var attemptedCurrentRun = !!\(attemptedRunId && attemptedRunId === yuiGuidePcOverlayRunIdOverride\);/);
    assert.match(externalBridgeBlock, /var attemptedChatOwnedRun = isYuiGuideChatOwnedPcOverlayRunId\(attemptedRunId\);/);
    assert.match(externalBridgeBlock, /var storedCanonicalRunId = readStoredYuiGuidePcOverlayRunId\(\);/);
    assert.match(externalBridgeBlock, /var attemptedCanonicalRun = !!\(/);
    assert.match(externalBridgeBlock, /if \(attemptedCanonicalRun\) \{[\s\S]*if \(syncYuiGuidePcOverlayRunIdFromStorage\(\)\) \{[\s\S]*sendYuiGuidePcOverlayPatch\(patch \|\| \{\}, true\);[\s\S]*return;[\s\S]*\}\s*\} else if \(!attemptedCurrentRun \|\| !attemptedChatOwnedRun\) \{/);
    assert.match(appInterpageSource, /function isYuiGuideChatOwnedPcOverlayRunId\(runId\) \{/);
    assert.doesNotMatch(externalBridgeBlock, /if \(attemptedCanonicalRun\) \{[\s\S]*syncYuiGuidePcOverlayRunIdFromStorage\(\);[\s\S]*return;[\s\S]*\}\s*if \(!attemptedCurrentRun \|\| !attemptedChatOwnedRun\) \{/);
    assert.match(externalBridgeBlock, /syncYuiGuidePcOverlayRunIdFromStorage\(\)[\s\S]*sendYuiGuidePcOverlayPatch\(patch \|\| \{\}, true\)/);
    assert.match(externalBridgeBlock, /sendYuiGuidePcOverlayPatch\(patch \|\| \{\}, true\)/);
    assert.match(externalBridgeBlock, /function resolveYuiGuidePcOverlayRunIdForSend\(requestedRunId, allowCreateRun\)/);
    assert.match(externalBridgeBlock, /function sendYuiGuidePcOverlayPatch\(patch, retried, options\)/);
    assert.match(externalBridgeBlock, /resolveYuiGuidePcOverlayRunIdForSend\(/);
    assert.match(externalBridgeBlock, /sendOptions\.skipBegin !== true/);
    assert.match(externalBridgeBlock, /result && result\.stale === true/);

    assert.match(mainBridgeBlock, /const readStoredRunId = \(\) => \{/);
    assert.match(mainBridgeBlock, /const rotateRunId = \(\) => \{/);
    assert.match(mainBridgeBlock, /const adoptRunId = \(nextRunId\) => \{/);
    assert.match(mainBridgeBlock, /const syncRunIdFromStorage = \(\) => adoptRunId\(readStoredRunId\(\)\);/);
    assert.match(mainBridgeBlock, /const handleStaleResult = \(result, patch, force, retried, attemptedRunId\) => \{/);
    assert.match(mainBridgeBlock, /result\.stale !== true/);
    assert.match(mainBridgeBlock, /if \(syncRunIdFromStorage\(\)\) \{[\s\S]*send\(patch, force, true\)/);
    assert.match(mainBridgeBlock, /send\(patch, force, true\)/);
    assert.match(mainBridgeBlock, /const send = \(patch, force, retried\) => \{/);
    assert.match(mainBridgeBlock, /const send = \(patch, force, retried\) => \{[\s\S]*syncRunIdFromStorage\(\);[\s\S]*completeStateStore\.applyPatch/);
    assert.match(mainBridgeBlock, /result && result\.stale === true/);
    assert.match(mainBridgeBlock, /const handleCursorOnlyStaleResult = \(result, cursor, retried, attemptedRunId\) => \{/);
    assert.match(mainBridgeBlock, /if \(syncRunIdFromStorage\(\)\) \{[\s\S]*sendCursorOnly\(cursor, true\)/);
    assert.match(mainBridgeBlock, /sendCursorOnly\(cursor, true\)/);
    assert.match(mainBridgeBlock, /const sendCursorOnly = \(cursor, retried\) => \{/);
    assert.match(mainBridgeBlock, /const sendCursorOnly = \(cursor, retried\) => \{[\s\S]*syncRunIdFromStorage\(\);[\s\S]*completeStateStore\.applyPatch\(\{ cursor: cursor \}\)/);
    assert.match(mainBridgeBlock, /const payload = completeStateStore\.applyPatch\(\{ cursor: cursor \}\);/);
    assert.doesNotMatch(mainBridgeBlock, /const payload = \{ cursor: cursor \};/);
    assert.match(mainBridgeBlock, /handleCursorOnlyStaleResult\(result, cursor, retried === true, updateRunId\)/);
});

test('director wraps the legacy ghost cursor with GhostCursorController facade', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const visualControllerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/controllers.js'), 'utf8');
    const ghostCursorControllerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/ghost-cursor-controller.js'), 'utf8');
    const constructorBlock = source.split('    class YuiGuideDirector {')[1].split(
        '            this.latestExternalizedChatCursorMoveSceneId =',
        1
    )[0];

    assert.match(ghostCursorControllerSource, /class GhostCursorController/);
    assert.match(ghostCursorControllerSource, /class YuiGuideGhostCursor/);
    assert.match(visualControllerSource, /ghostCursorControllerApi\.GhostCursorController/);
    assert.match(visualControllerSource, /ghostCursorControllerApi\.YuiGuideGhostCursor/);
    assert.doesNotMatch(source, /    class YuiGuideGhostCursor \{/);
    assert.match(constructorBlock, /this\.targetGeometryRegistry = createYuiGuideTargetGeometryRegistry\(\);/);
    assert.match(constructorBlock, /this\.cursor = new TutorialVisualControllers\.GhostCursorController\(new TutorialVisualControllers\.YuiGuideGhostCursor\(this\.overlay\),\s*\{\s*registry: this\.targetGeometryRegistry\s*\}\);/);
    for (const methodName of [
        'showAt',
        'moveToPoint',
        'moveToRect',
        'click',
        'wobble',
        'hide',
        'runPauseAwareEllipse'
    ]) {
        assert.doesNotMatch(ghostCursorControllerSource, new RegExp('this\\.legacyCursor\\.' + methodName + '\\s*\\('));
    }
    assert.match(ghostCursorControllerSource, /cancel\(.*\) \{[\s\S]*typeof this\.legacyCursor\.cancel !== 'function'[\s\S]*this\.legacyCursor\.cancel/);
    assert.match(ghostCursorControllerSource, /pause\(.*\) \{[\s\S]*typeof this\.legacyCursor\.pause !== 'function'[\s\S]*this\.legacyCursor\.pause/);
    assert.doesNotMatch(ghostCursorControllerSource, /pause\(.*\) \{[\s\S]*this\.legacyCursor\.cancel/);
    assert.match(ghostCursorControllerSource, /resume\(.*\) \{[\s\S]*typeof this\.legacyCursor\.resume !== 'function'[\s\S]*this\.legacyCursor\.resume/);
});

test('spotlight facade exposes pause and resume hooks for pause coordination', () => {
    const visualControllerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/controllers.js'), 'utf8');
    const spotlightControllerSource = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/visual/spotlight-controller.js'), 'utf8');

    assert.match(spotlightControllerSource, /class SpotlightController/);
    assert.match(visualControllerSource, /spotlightControllerApi\.SpotlightController/);
    assert.match(spotlightControllerSource, /pause\(\) \{[\s\S]*typeof this\.highlightController\.pause !== 'function'[\s\S]*this\.highlightController\.pause\(\)/);
    assert.match(spotlightControllerSource, /resume\(\) \{[\s\S]*typeof this\.highlightController\.resume !== 'function'[\s\S]*this\.highlightController\.resume\(\)/);
    assert.doesNotMatch(spotlightControllerSource, /call\(methodName/);
});

test('director consumes phase two target registry and chat adapter boundaries', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/yui-guide/director.js'), 'utf8');
    const constructorBlock = source.split('    class YuiGuideDirector {')[1].split(
        '            this.latestExternalizedChatCursorMoveSceneId =',
        1
    )[0];
    const postTakeoverConstructorBlock = source.split('            this.interactionTakeover = window.TutorialInteractionTakeover')[1].split(
        '            if (this.interactionTakeover && typeof this.interactionTakeover.enableFaceForwardLock',
        1
    )[0];
    const externalKindBlock = source.split('        getExternalizedChatTargetKind(targetKey, scene) {')[1].split(
        '        getExternalizedChatSpotlightKind',
        1
    )[0];
    const externalCursorKindBlock = source.split('        getExternalizedChatCursorTargetKind(scene) {')[1].split(
        '        getExternalizedChatCursorEffect',
        1
    )[0];
    const avatarSelectorBlock = source.split('        resolveAvatarFloatingSelector(selector) {')[1].split(
        '        getMiniGameChoiceTargets',
        1
    )[0];
    const inputLockBlock = source.split('        setGuideChatInputLocked(locked, reason) {')[1].split(
        '        setAvatarFloatingGuideTutorialMode',
        1
    )[0];
    const avatarMenuBlock = source.split('        setChatAvatarToolMenuOpen(open, reason) {')[1].split(
        '        clickChatAvatarToolButton',
        1
    )[0];
    const toolFanBlock = source.split('        setCompactToolFanOpen(open, reason) {')[1].split(
        '        rotateCompactToolWheelForGuide',
        1
    )[0];
    const rotateBlock = source.split('        rotateCompactToolWheelForGuide(direction, stepCount, reason) {')[1].split(
        '        setCompactToolWheelIndexForGuide',
        1
    )[0];
    const wheelIndexBlock = source.split('        setCompactToolWheelIndexForGuide(index, reason) {')[1].split(
        '        setCompactHistoryOpen',
        1
    )[0];
    const historyBlock = source.split('        setCompactHistoryOpen(open, reason) {')[1].split(
        '        getExternalizedChatTargetKind',
        1
    )[0];

    assert.match(source, /createYuiGuideTargetGeometryRegistry\(\)/);
    assert.match(source, /createYuiGuideChatWindowAdapter\(options\)/);
    assert.match(constructorBlock, /this\.targetGeometryRegistry = createYuiGuideTargetGeometryRegistry\(\);/);
    assert.match(postTakeoverConstructorBlock, /this\.chatWindowAdapter = createYuiGuideChatWindowAdapter\(\{/);
    assert.match(externalKindBlock, /const registeredKind = this\.spotlightController\.getExternalKind\(targetKey\);/);
    assert.match(externalKindBlock, /if \(registeredKind\) \{\s*return registeredKind;\s*\}/);
    assert.match(externalCursorKindBlock, /this\.cursor\.getExternalKind\(this\.getAvatarFloatingCursorTargetKey\(scene\)\)/);
    assert.doesNotMatch(externalKindBlock, /if \(targetKey === 'chat-capsule-input'\) \{\s*return 'capsule-input';\s*\}/);
    assert.match(source, /resolveRegisteredAvatarFloatingSelector\(selector\) \{/);
    assert.match(avatarSelectorBlock, /const registeredTarget = this\.resolveRegisteredAvatarFloatingSelector\(selector\);/);
    assert.match(source, /this\.spotlightController\.getLocalSelectors\(selector\)/);
    assert.match(inputLockBlock, /this\.chatWindowAdapter\.lockInput\(isLocked,\s*lockReason\)/);
    assert.doesNotMatch(inputLockBlock, /window\.reactChatWindowHost/);
    assert.match(avatarMenuBlock, /const desiredOpen = open === true;/);
    assert.match(avatarMenuBlock, /this\.chatWindowAdapter\.setAvatarToolMenuOpen\(desiredOpen,\s*actionReason\)/);
    assert.match(avatarMenuBlock, /this\.interactionTakeover\.setExternalizedChatAvatarToolMenuOpen\(desiredOpen,\s*actionReason\)/);
    assert.match(avatarMenuBlock, /reactHost\.setAvatarToolMenuOpen\(desiredOpen,\s*actionReason\)/);
    assert.match(toolFanBlock, /const desiredOpen = open === true;/);
    assert.match(toolFanBlock, /this\.chatWindowAdapter\.setCompactToolFanOpen\(desiredOpen,\s*actionReason\)/);
    assert.match(toolFanBlock, /this\.interactionTakeover\.setExternalizedChatCompactToolFanOpen\(desiredOpen,\s*actionReason\)/);
    assert.match(toolFanBlock, /this\.resolveAvatarFloatingSelector\('chat-tool-toggle'\)/);
    assert.match(rotateBlock, /this\.chatWindowAdapter\.rotateCompactToolWheel\(\s*normalizedDirection,\s*normalizedStepCount,\s*reason \|\| 'avatar-floating-guide'\s*\)/);
    assert.doesNotMatch(rotateBlock, /window\.reactChatWindowHost/);
    assert.match(wheelIndexBlock, /this\.chatWindowAdapter\.setCompactToolWheelIndex\(\s*normalizedIndex,\s*reason \|\| 'avatar-floating-guide'\s*\)/);
    assert.doesNotMatch(wheelIndexBlock, /window\.reactChatWindowHost/);
    assert.match(historyBlock, /this\.chatWindowAdapter\.setCompactHistoryOpen\(desiredOpen,\s*actionReason\)/);
    assert.match(historyBlock, /this\.interactionTakeover\.setExternalizedChatCompactHistoryOpen\(desiredOpen,\s*actionReason\)/);
    assert.match(historyBlock, /this\.resolveAvatarFloatingSelector\('chat-history-handle'\)/);
});

test('skip controller uses scoped resources with a fallback cleanup path', () => {
    const source = fs.readFileSync(path.join(repoRoot, 'static', 'tutorial/core/skip-controller.js'), 'utf8');

    assert.match(source, /createScopedTutorialResources/);
    assert.match(source, /this\.currentResources\.destroy\(\)/);
    assert.match(source, /button\.removeEventListener\('pointerdown', handleSkipRequest\)/);
});
