const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const dragSourcePath = 'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js';
const journeySourcePath = 'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js';
const edgeClasses = [
    'is-cat1-edge-peek-left',
    'is-cat1-edge-peek-right',
    'is-cat1-edge-peek-top',
    'is-cat1-edge-peek-bottom',
    'is-cat1-edge-peek-top-left',
    'is-cat1-edge-peek-top-right',
    'is-cat1-edge-peek-bottom-left',
    'is-cat1-edge-peek-bottom-right'
];

function readFunction(relativePath, name) {
    const source = fs.readFileSync(path.join(projectRoot, relativePath), 'utf8');
    const start = source.indexOf(`function ${name}`);
    assert.notEqual(start, -1, `missing function ${name}`);
    const signatureEnd = source.indexOf(') {', start);
    assert.notEqual(signatureEnd, -1, `missing function body ${name}`);
    const bodyStart = signatureEnd + 2;
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) {
            const extracted = source.slice(start, index + 1);
            assert.doesNotThrow(
                () => new vm.Script(`${extracted}\nvoid ${name};`),
                `invalid extracted function ${name}`
            );
            return extracted;
        }
    }
    throw new Error(`unterminated function ${name}`);
}

class ClassListLike {
    constructor(...names) {
        this.names = new Set(names);
    }

    add(...names) {
        names.forEach((name) => this.names.add(name));
    }

    remove(...names) {
        names.forEach((name) => this.names.delete(name));
    }

    contains(name) {
        return this.names.has(name);
    }
}

function createStyle() {
    const customProperties = new Map();
    return {
        left: '120px',
        top: '90px',
        right: '',
        bottom: '',
        transform: 'none',
        display: '',
        setProperty(name, value) {
            customProperties.set(name, String(value));
        },
        getPropertyValue(name) {
            return customProperties.get(name) || '';
        },
        removeProperty(name) {
            const previous = customProperties.get(name) || '';
            customProperties.delete(name);
            return previous;
        }
    };
}

function createEdgeFixture({ transferredAnchor = '' } = {}) {
    const art = { style: createStyle() };
    const buttonAttributes = new Map([['data-neko-idle-tier', 'cat1']]);
    const containerAttributes = new Map([['data-dragging', 'false']]);
    if (transferredAnchor) {
        containerAttributes.set('data-neko-live2d-peek-anchor', transferredAnchor);
    }
    const button = {
        classList: new ClassListLike('neko-idle-return-btn'),
        getAttribute(name) {
            return buttonAttributes.get(name) || null;
        },
        setAttribute(name, value) {
            buttonAttributes.set(name, String(value));
        },
        querySelector(selector) {
            return selector === '.neko-idle-return-art' ? art : null;
        },
        closest() {
            return container;
        }
    };
    const container = {
        id: 'test-return-button-container',
        classList: new ClassListLike(),
        style: createStyle(),
        offsetWidth: 64,
        offsetHeight: 64,
        getAttribute(name) {
            return containerAttributes.get(name) || null;
        },
        setAttribute(name, value) {
            containerAttributes.set(name, String(value));
        },
        removeAttribute(name) {
            containerAttributes.delete(name);
        },
        querySelector(selector) {
            return selector === '.neko-idle-return-btn' ? button : null;
        }
    };
    return { art, button, container };
}

class CustomEventLike {
    constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
    }
}

class WindowLike {
    constructor() {
        this.listeners = new Map();
    }

    addEventListener(type, listener) {
        const listeners = this.listeners.get(type) || [];
        listeners.push(listener);
        this.listeners.set(type, listeners);
    }

    dispatchEvent(event) {
        (this.listeners.get(event.type) || []).forEach((listener) => listener(event));
        return true;
    }
}

function installEdgeRuntime(context, functionNames) {
    vm.createContext(context);
    vm.runInContext(
        functionNames.map((name) => readFunction(dragSourcePath, name)).join('\n'),
        context
    );
}

function createBaseEdgeContext(fixture) {
    return {
        console,
        CustomEvent: CustomEventLike,
        window: new WindowLike(),
        _NEKO_IDLE_TIER_CAT1: 'cat1',
        _NEKO_IDLE_CAT1_EDGE_PEEK_CLASSES: edgeClasses,
        _NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO: 0.2,
        _NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO: 0.4,
        _NEKO_CAT_IDLE_OBSERVATION_TYPES: { EDGE_PEEK_AFTER_DRAG: 'edge_peek_after_drag' },
        _getNekoIdleReturnButtonFromContainer(container) {
            return container === fixture.container ? fixture.button : null;
        },
        _getNekoIdleReturnContainerFromButton(button) {
            return button === fixture.button ? fixture.container : null;
        },
        _normalizeNekoIdleReturnTier(value) {
            return value || 'none';
        },
        _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton() {},
        _getNekoDesktopVirtualViewportSize() {
            return { width: 800, height: 600 };
        },
        _getNekoDesktopVirtualElementRect(container) {
            return {
                left: Number.parseFloat(container.style.left) || 0,
                top: Number.parseFloat(container.style.top) || 0,
                width: container.offsetWidth,
                height: container.offsetHeight
            };
        }
    };
}

test('CAT1 edge cleanup owns visualShiftY for direct clear, tier exit, and the next cat cycle', () => {
    const fixture = createEdgeFixture();
    const context = createBaseEdgeContext(fixture);
    context._cancelNekoIdleCat1Journey = () => {};
    installEdgeRuntime(context, [
        '_clampNekoIdleCat1EdgePeekCoordinate',
        '_getNekoIdleCat1EdgePeekButton',
        '_clearNekoIdleCat1EdgePeek',
        '_isNekoIdleCat1EdgePeekActive',
        '_clearNekoIdleCat1EdgePeekForTierExit'
    ]);

    fixture.button.classList.add('is-cat1-edge-peek-bottom');
    fixture.art.style.setProperty('--neko-idle-return-edge-visual-shift-y', '-37px');
    context.targetContainer = fixture.container;
    vm.runInContext('_clearNekoIdleCat1EdgePeek(targetContainer)', context);

    assert.equal(fixture.art.style.getPropertyValue('--neko-idle-return-edge-visual-shift-y'), '');
    assert.equal(fixture.button.classList.contains('is-cat1-edge-peek-bottom'), false);

    fixture.button.classList.add('is-cat1-edge-peek-bottom-right');
    fixture.art.style.setProperty('--neko-idle-return-edge-visual-shift-y', '-37px');
    context.targetContainer = fixture.container;
    vm.runInContext('_clearNekoIdleCat1EdgePeekForTierExit(targetContainer)', context);

    assert.equal(fixture.art.style.getPropertyValue('--neko-idle-return-edge-visual-shift-y'), '');
    assert.equal(fixture.button.classList.contains('is-cat1-edge-peek-bottom-right'), false);

    fixture.button.classList.add('is-cat1-edge-peek-left');
    assert.equal(
        fixture.art.style.getPropertyValue('--neko-idle-return-edge-visual-shift-y'),
        '',
        'starting the next CAT1 edge cycle must not resurrect the previous bottom-edge offset'
    );
});

function createDragEndHarness({ transferredAnchor = '' } = {}) {
    const fixture = createEdgeFixture({ transferredAnchor });
    const observations = [];
    const cancellations = [];
    const scheduledButtons = [];
    const context = createBaseEdgeContext(fixture);
    Object.assign(context, {
        document: {
            querySelectorAll() {
                return [];
            }
        },
        _dispatchNekoCatIdleObservationSource(type, detail) {
            observations.push({ type, detail });
        },
        _cancelNekoIdleCat1Journey(button, options) {
            cancellations.push({ button, options });
        },
        _getNekoGoodbyeIdleAppearance() {
            return 'cat';
        },
        _isNekoIdleCat1PlaygroundEntryOrDropActive() {
            return false;
        },
        _finishNekoIdleReturnDragActionForContainer() {},
        _cancelNekoIdleCat1JourneyForContainer(container, options) {
            cancellations.push({ container, options });
        },
        _updateNekoIdleCat1CompactTopEdgeRearmAfterManualMove() {
            return { shouldSync: false };
        },
        _shouldRecheckNekoIdleCat1AfterManualMove() {
            return false;
        },
        _scheduleNekoIdleCat1JourneySync(button) {
            scheduledButtons.push(button);
        },
        _prepareNekoIdleReturnDragActionForContainer() {},
        _startNekoIdleReturnDragActionForContainer() {},
        _handleNekoIdleCat1RapidDragMotionForContainer() {},
        _handleNekoIdleCompactSurfaceMoveState() {},
        _syncAllNekoIdleReturnButtons() {},
        _syncNekoIdleSleepSoundForTier() {},
        _syncNekoIdleCat1AmbientSoundForTier() {},
        _stopNekoGoodbyeIdleBallCatSounds() {},
        _readNekoAutoGoodbyeVisualTier() {
            return 'cat1';
        },
        _NEKO_GOODBYE_IDLE_APPEARANCE_BALL: 'ball'
    });
    installEdgeRuntime(context, [
        '_clampNekoIdleCat1EdgePeekCoordinate',
        '_getNekoIdleCat1EdgePeekButton',
        '_clearNekoIdleCat1EdgePeek',
        '_isNekoIdleCat1EdgePeekActive',
        '_getNekoIdleCat1EdgePeekActiveEdge',
        '_isNekoIdleCat1EdgePeekEligible',
        '_getNekoIdleCat1EdgePeekPlacement',
        '_applyNekoIdleCat1EdgePeek',
        '_applyNekoIdleCat1EdgePeekAfterDrag',
        '_dispatchNekoIdleCat1EdgePeekAfterDragObservation',
        '_scheduleNekoIdleCat1JourneySyncForContainer'
    ]);
    vm.runInContext(readFunction(journeySourcePath, '_ensureNekoIdleReturnPresentationBridge'), context);
    vm.runInContext('_ensureNekoIdleReturnPresentationBridge()', context);
    context.targetContainer = fixture.container;
    return { context, fixture, observations, cancellations, scheduledButtons };
}

function dispatchManualMove(harness, detail) {
    harness.context.window.dispatchEvent(new CustomEventLike('neko:return-ball-manual-move', {
        detail: { container: harness.fixture.container, ...detail }
    }));
}

test('fallback and native drag-end producers each report one shared EDGE observation', async (t) => {
    await t.test('fallback placement defers observation until the shared drag-end boundary', () => {
        const harness = createDragEndHarness();
        const applied = vm.runInContext(
            '_applyNekoIdleCat1EdgePeekAfterDrag(targetContainer, 1, 180, 800, 600)',
            harness.context
        );
        assert.equal(applied, true);
        assert.equal(harness.observations.length, 0, 'fallback placement must not dispatch early');

        dispatchManualMove(harness, {
            reason: 'return-ball-drag-end',
            movedDistancePx: 48,
            producer: 'web-fallback'
        });

        assert.equal(harness.observations.length, 1);
        assert.equal(harness.observations[0].type, 'edge_peek_after_drag');
        assert.equal(harness.observations[0].detail.source, 'return-ball');
        assert.equal(harness.observations[0].detail.tier, 'cat1');
        assert.equal(harness.observations[0].detail.reason, 'drag-edge-peek');
        assert.equal(harness.observations[0].detail.edge, 'left');
    });

    await t.test('native placement enters the same boundary without fallback code', () => {
        const harness = createDragEndHarness();
        harness.fixture.button.classList.add('is-cat1-edge-peek-right');

        dispatchManualMove(harness, {
            reason: 'return-ball-drag-end',
            movedDistancePx: 63,
            producer: 'electron-native'
        });

        assert.equal(harness.observations.length, 1);
        assert.equal(harness.observations[0].type, 'edge_peek_after_drag');
        assert.equal(harness.observations[0].detail.source, 'return-ball');
        assert.equal(harness.observations[0].detail.tier, 'cat1');
        assert.equal(harness.observations[0].detail.reason, 'drag-edge-peek');
        assert.equal(harness.observations[0].detail.edge, 'right');
    });

    await t.test('fallback pre-snap motion survives zero post-snap net distance', () => {
        const harness = createDragEndHarness();
        harness.fixture.button.classList.add('is-cat1-edge-peek-left');

        dispatchManualMove(harness, {
            reason: 'return-ball-drag-end',
            movedDistancePx: 0,
            displacementPx: 12,
            pathDistancePx: 12,
            producer: 'web-fallback'
        });

        assert.equal(harness.observations.length, 1);
        assert.equal(harness.observations[0].detail.edge, 'left');
    });
});

test('cancelled, unmoved, and non-edge drag completions do not report EDGE observations', async (t) => {
    await t.test('drag cancel', () => {
        const harness = createDragEndHarness();
        harness.fixture.button.classList.add('is-cat1-edge-peek-left');
        dispatchManualMove(harness, {
            reason: 'return-ball-drag-cancel',
            movedDistancePx: 0,
            dragCancelled: true
        });
        assert.equal(harness.observations.length, 0);
    });

    await t.test('zero-distance drag-end', () => {
        const harness = createDragEndHarness();
        harness.fixture.button.classList.add('is-cat1-edge-peek-left');
        dispatchManualMove(harness, {
            reason: 'return-ball-drag-end',
            movedDistancePx: 0,
            displacementPx: 0,
            pathDistancePx: 0
        });
        assert.equal(harness.observations.length, 0);
    });

    await t.test('moved but not placed at an edge', () => {
        const harness = createDragEndHarness();
        dispatchManualMove(harness, {
            reason: 'return-ball-drag-end',
            movedDistancePx: 31
        });
        assert.equal(harness.observations.length, 0);
    });
});

test('drag cancel resumes journey after active dragging releases a transferred anchor', () => {
    const harness = createDragEndHarness({ transferredAnchor: 'left' });

    dispatchManualMove(harness, { reason: 'return-ball-drag-active' });
    harness.fixture.container.removeAttribute('data-neko-live2d-peek-anchor');
    dispatchManualMove(harness, {
        reason: 'return-ball-drag-cancel',
        movedDistancePx: 0,
        dragCancelled: true
    });

    assert.deepEqual(harness.scheduledButtons, [harness.fixture.button]);
    assert.equal(harness.observations.length, 0);
});

function createMovementHarness({ transferredAnchor = 'left' } = {}) {
    const fixture = createEdgeFixture({ transferredAnchor });
    const state = {
        paused: false,
        pairMovePlan: null,
        pairMoveFrame: 0,
        pendingWalkTimer: 0,
        pendingWalkReady: true,
        frame: 41,
        syncFrame: 0,
        actionSettled: true,
        targetKind: '',
        substate: 'idle',
        profile: {
            tier: 'cat1',
            idleSubstate: 'idle',
            walkingSubstate: 'walking',
            target: { exitDistancePx: 12 },
            pairMove: {}
        }
    };
    fixture.button.__nekoIdleReturnSubactionState = state;
    const rafCallbacks = [];
    const cancellations = [];
    const containerObservers = [];
    let reclampCount = 0;
    const context = createBaseEdgeContext(fixture);
    Object.assign(context, {
        window: {
            requestAnimationFrame(callback) {
                rafCallbacks.push(callback);
                return rafCallbacks.length;
            }
        },
        document: {
            getElementById() {
                return null;
            }
        },
        MutationObserver: class {
            constructor(callback) {
                this.callback = callback;
            }

            observe(target, options) {
                this.target = target;
                this.options = options;
                containerObservers.push(this);
            }
        },
        performance: { now: () => 10 },
        _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW: state.profile,
        _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE: 'compact-top-edge',
        _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX: 8,
        _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DISTANCE_PX: 40,
        _NEKO_IDLE_TIER_CAT1: 'cat1',
        _nekoIdleCompactSurfaceSettleTimer: 0,
        _getNekoIdleCat1Journey() {
            return state;
        },
        _cancelNekoIdleCat1Journey(button, options) {
            cancellations.push({ button, options });
        },
        _isNekoIdleReturnDragActionActive() {
            return false;
        },
        _isNekoIdleCat1IndependentActionActive() {
            return false;
        },
        _isNekoIdleCat1PlaygroundEntryOrDropActive() {
            return false;
        },
        _isNekoIdleCompactSurfaceDragging() {
            return false;
        },
        _reclampNekoIdleCat1EdgePeekToViewport() {
            reclampCount += 1;
        }
    });
    vm.createContext(context);
    vm.runInContext([
        readFunction(dragSourcePath, '_getNekoIdleCat1EdgePeekButton'),
        readFunction(dragSourcePath, '_isNekoIdleCat1EdgePeekActive'),
        readFunction(dragSourcePath, '_isNekoIdleCat1TransferredPeekAnchorActive'),
        readFunction(dragSourcePath, '_isNekoIdleCat1MovementAnchored'),
        readFunction(journeySourcePath, '_startNekoIdleCat1Walk'),
        readFunction(journeySourcePath, '_scheduleNekoIdleCat1WalkStart'),
        readFunction(journeySourcePath, '_prepareNekoIdleCat1PairMoveStart'),
        readFunction(journeySourcePath, '_canScheduleNekoIdleCat1PairMove'),
        readFunction(journeySourcePath, '_startNekoIdleCat1PairMove'),
        readFunction(journeySourcePath, '_refreshNekoIdleCat1Observer'),
        readFunction(journeySourcePath, '_syncNekoIdleCat1Journey'),
        readFunction(journeySourcePath, '_scheduleNekoIdleCat1JourneySync')
    ].join('\n'), context);
    context.targetButton = fixture.button;
    context.targetState = state;
    context.target = { left: 400, top: 250, distance: 200, kind: 'chat' };
    return {
        context,
        fixture,
        state,
        rafCallbacks,
        cancellations,
        containerObservers,
        getReclampCount: () => reclampCount
    };
}

test('transferred anchor mutation cancels an in-flight walk through the existing sync gate', () => {
    const harness = createMovementHarness({ transferredAnchor: '' });
    harness.state.substate = harness.state.profile.walkingSubstate;

    vm.runInContext('_refreshNekoIdleCat1Observer(targetButton)', harness.context);
    assert.equal(harness.containerObservers.length, 1);
    const observer = harness.containerObservers[0];
    assert.ok(observer.options.attributeFilter.includes('data-neko-live2d-peek-anchor'));

    observer.callback([{ type: 'attributes', attributeName: 'style' }]);
    assert.equal(harness.cancellations.length, 0, 'walking style updates must remain ignored');

    harness.fixture.container.setAttribute('data-neko-live2d-peek-anchor', 'left');
    observer.callback([{
        type: 'attributes',
        attributeName: 'data-neko-live2d-peek-anchor'
    }]);

    assert.equal(harness.cancellations.length, 1);
    assert.equal(harness.cancellations[0].options.resetArt, false);
    assert.equal(harness.cancellations[0].options.preserveObservers, true);
    assert.equal(harness.getReclampCount(), 0, 'transferred anchor must not use CAT reclamp');
});

test('transferred Live2D anchor blocks every CAT1 movement entry without becoming CAT edge peek', () => {
    const harness = createMovementHarness();
    const initialPosition = {
        left: harness.fixture.container.style.left,
        top: harness.fixture.container.style.top
    };

    assert.equal(vm.runInContext('_isNekoIdleCat1TransferredPeekAnchorActive(targetButton)', harness.context), true);
    assert.equal(vm.runInContext('_isNekoIdleCat1MovementAnchored(targetButton)', harness.context), true);
    assert.equal(vm.runInContext('_isNekoIdleCat1EdgePeekActive(targetButton)', harness.context), false);

    vm.runInContext('_startNekoIdleCat1Walk(targetButton, target)', harness.context);
    vm.runInContext('_scheduleNekoIdleCat1WalkStart(targetButton, target)', harness.context);
    vm.runInContext('_prepareNekoIdleCat1PairMoveStart(targetButton, targetState)', harness.context);
    assert.equal(vm.runInContext('_canScheduleNekoIdleCat1PairMove(targetButton, targetState)', harness.context), false);
    assert.equal(
        vm.runInContext("_startNekoIdleCat1PairMove(targetButton, { source: 'cat_mind' })", harness.context),
        false
    );
    vm.runInContext('_syncNekoIdleCat1Journey(targetButton)', harness.context);
    vm.runInContext('_scheduleNekoIdleCat1JourneySync(targetButton)', harness.context);
    vm.runInContext('_scheduleNekoIdleCat1JourneySync(targetButton)', harness.context);

    assert.deepEqual(
        {
            left: harness.fixture.container.style.left,
            top: harness.fixture.container.style.top
        },
        initialPosition
    );
    assert.equal(harness.rafCallbacks.length, 0, 'anchored sync must not enqueue a movement frame');
    assert.ok(harness.cancellations.length >= 5, 'active or queued journey work must be cancelled');
    assert.equal(harness.getReclampCount(), 0, 'transferred anchor positioning belongs to app-ui');
    assert.equal(edgeClasses.some((name) => harness.fixture.button.classList.contains(name)), false);
});

test('clearing transferred anchor resumes scheduling and a later drag uses CAT edge semantics', () => {
    const harness = createMovementHarness();
    harness.fixture.container.removeAttribute('data-neko-live2d-peek-anchor');

    assert.equal(vm.runInContext('_isNekoIdleCat1MovementAnchored(targetButton)', harness.context), false);
    vm.runInContext('_scheduleNekoIdleCat1JourneySync(targetButton)', harness.context);
    assert.equal(harness.rafCallbacks.length, 1, 'movement scheduling must resume after anchor removal');

    const dragContext = createBaseEdgeContext(harness.fixture);
    dragContext._cancelNekoIdleCat1Journey = () => {};
    installEdgeRuntime(dragContext, [
        '_clampNekoIdleCat1EdgePeekCoordinate',
        '_getNekoIdleCat1EdgePeekButton',
        '_clearNekoIdleCat1EdgePeek',
        '_isNekoIdleCat1EdgePeekActive',
        '_isNekoIdleCat1TransferredPeekAnchorActive',
        '_isNekoIdleCat1MovementAnchored',
        '_isNekoIdleCat1EdgePeekEligible',
        '_getNekoIdleCat1EdgePeekPlacement',
        '_applyNekoIdleCat1EdgePeek',
        '_applyNekoIdleCat1EdgePeekAfterDrag'
    ]);
    dragContext.targetContainer = harness.fixture.container;
    const applied = vm.runInContext(
        '_applyNekoIdleCat1EdgePeekAfterDrag(targetContainer, 1, 160, 800, 600)',
        dragContext
    );

    assert.equal(applied, true);
    assert.equal(harness.fixture.button.classList.contains('is-cat1-edge-peek-left'), true);
    assert.equal(vm.runInContext('_isNekoIdleCat1EdgePeekActive(targetContainer)', dragContext), true);
    assert.equal(vm.runInContext('_isNekoIdleCat1TransferredPeekAnchorActive(targetContainer)', dragContext), false);
    assert.equal(vm.runInContext('_isNekoIdleCat1MovementAnchored(targetContainer)', dragContext), true);
});
