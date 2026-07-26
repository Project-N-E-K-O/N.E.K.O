const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const JOURNEY_PATH = path.join(
    PROJECT_ROOT,
    'static',
    'avatar',
    'avatar-ui-buttons',
    'idle-journey-and-presentation.js'
);

function sourceBetween(startMarker, endMarker) {
    const source = fs.readFileSync(JOURNEY_PATH, 'utf8');
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start);
    assert.ok(start >= 0 && end > start, 'small-move provider slice not found');
    return source.slice(start, end);
}

test('desktop compact chat surface preserves solo small-move capability', () => {
    let desktopCompactRect = { left: 20, top: 40, width: 408, height: 58 };
    const context = vm.createContext({
        _getNekoIdleReactChatExpandedShell: () => null,
        _getNekoIdleDesktopCompactSurfaceRect: () => desktopCompactRect,
        _isNekoIdleDesktopChatExpandedRecent: () => false,
    });

    vm.runInContext(sourceBetween(
        'function _canNekoIdleCat1MoveSoloWithExpandedChat',
        'function _getNekoIdleChatMinimizedRect'
    ), context, { filename: JOURNEY_PATH });

    assert.equal(context._canNekoIdleCat1MoveSoloWithExpandedChat(), true);
    desktopCompactRect = null;
    assert.equal(context._canNekoIdleCat1MoveSoloWithExpandedChat(), false);
});

test('small_move capability check is pure while actual start owns hover preparation', () => {
    let finishCalls = 0;
    let geometryReads = 0;
    const profile = {
        idleSubstate: 'idle',
        tier: 'cat1',
        target: { exitDistancePx: 14 },
        pairMove: { minUsableDistancePx: 12, maxDistancePx: 120 },
    };
    const state = {
        profile,
        paused: false,
        pairMovePlan: null,
        pairMoveFrame: 0,
        substate: 'idle',
        actionSettled: true,
        targetKind: '',
        pendingWalkTimer: 0,
        pendingWalkReady: false,
        frame: 0,
    };
    const art = {
        __nekoIdleHoverSrc: 'hover.gif',
        __nekoIdleHoverTimer: 0,
    };
    const container = {
        style: { display: '' },
        getAttribute: () => 'false',
        getBoundingClientRect() {
            geometryReads += 1;
            return { left: 100, top: 100, width: 80, height: 80 };
        },
    };
    const button = {
        __nekoIdleCat1Journey: state,
        querySelector(selector) {
            assert.equal(selector, '.neko-idle-return-art');
            return art;
        },
    };
    const context = vm.createContext({
        Math,
        Number,
        _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW: profile,
        _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE: 'compact-top-edge',
        _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX: 12,
        _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DISTANCE_PX: 120,
        _isNekoIdleCat1EdgePeekActive: () => false,
        _isNekoIdleCat1IndependentActionActive: () => false,
        _isNekoIdleReturnDragActionActive: () => false,
        _finishNekoIdleHoverArtAfterPlayback: (receivedArt, tier) => {
            assert.equal(receivedArt, art);
            assert.equal(tier, 'cat1');
            finishCalls += 1;
        },
        _getNekoIdleReturnContainerFromButton: () => container,
        _getNekoIdleCat1PairMoveChatTarget: () => null,
        _canNekoIdleCat1MoveSoloWithExpandedChat: () => true,
        _hasNekoIdleCat1MoveVectorSpace: () => true,
        _getNekoIdleCat1Journey: () => state,
        _cancelNekoIdleCat1Journey: () => {
            throw new Error('edge-peek cancellation should not run');
        },
    });

    vm.runInContext(sourceBetween(
        'function _prepareNekoIdleCat1PairMoveStart',
        'function _finishNekoIdleCat1PairMove'
    ), context, { filename: JOURNEY_PATH });
    vm.runInContext(sourceBetween(
        'function _startNekoIdleCat1PairMove',
        'function _refreshNekoIdleCat1Observer'
    ), context, { filename: JOURNEY_PATH });

    assert.equal(context._canScheduleNekoIdleCat1PairMove(button, state), false);
    assert.equal(finishCalls, 0, 'capability checks must not mutate hover playback');
    assert.equal(geometryReads, 0, 'hover rejection should remain an inexpensive read');

    assert.equal(context._startNekoIdleCat1PairMove(button, { source: 'cat_mind' }), false);
    assert.equal(finishCalls, 1, 'the real start path retains hover playback preparation');
    assert.equal(geometryReads, 0, 'hover preparation should preserve the existing pre-geometry rejection');

    art.__nekoIdleHoverTimer = 73;
    assert.equal(context._startNekoIdleCat1PairMove(button, { source: 'cat_mind' }), false);
    assert.equal(finishCalls, 1, 'an existing hover completion timer must not be duplicated');

    art.__nekoIdleHoverSrc = '';
    art.__nekoIdleHoverTimer = 0;
    assert.equal(context._canScheduleNekoIdleCat1PairMove(button, state), true,
        'expanded and compact chat must retain the existing solo cat move capability');
    assert.ok(geometryReads > 0, 'solo capability must validate the available cat movement space');
});
