const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const standIn = require('./tutorial/avatar/yui-standin.js');
const directorSource = fs.readFileSync(path.join(__dirname, 'tutorial/yui-guide/director.js'), 'utf8');
const avatarStageSource = fs.readFileSync(path.join(__dirname, 'tutorial/avatar/yui-stage.js'), 'utf8');
const controllerSource = fs.readFileSync(path.join(__dirname, 'tutorial/avatar/standin-controller.js'), 'utf8');
const overlaySource = fs.readFileSync(path.join(__dirname, 'tutorial/yui-guide/overlay.js'), 'utf8');
const live2dInteractionSource = fs.readFileSync(path.join(__dirname, 'live2d', 'live2d-interaction.js'), 'utf8');
const live2dInitSource = fs.readFileSync(path.join(__dirname, 'live2d', 'live2d-init.js'), 'utf8');
const live2dButtonsSource = fs.readFileSync(path.join(__dirname, 'live2d', 'live2d-ui-buttons.js'), 'utf8');
const appUiSource = fs.readFileSync(path.join(__dirname, 'app-ui.js'), 'utf8');
const appInterpageSource = fs.readFileSync(path.join(__dirname, 'app-interpage.js'), 'utf8');
const universalManagerSource = fs.readFileSync(path.join(__dirname, 'tutorial/core/universal-manager.js'), 'utf8');

function loadAvatarStageContext(options) {
    const normalizedOptions = options || {};
    const elements = normalizedOptions.elements || {};
    let now = Number.isFinite(Number(normalizedOptions.now)) ? Number(normalizedOptions.now) : 0;
    const window = {
        innerWidth: 1024,
        innerHeight: 768,
        requestAnimationFrame() {
            return 0;
        },
        cancelAnimationFrame() {},
        __setNow(value) {
            now = Number(value) || 0;
        }
    };
    if (typeof normalizedOptions.configureWindow === 'function') {
        normalizedOptions.configureWindow(window);
    }
    const context = vm.createContext({
        window,
        document: {
            getElementById(id) {
                return elements[id] || null;
            },
            querySelector() {
                return null;
            }
        },
        performance: {
            now() {
                return now;
            }
        },
        console
    });
    vm.runInContext(avatarStageSource, context, {
        filename: path.join(__dirname, 'tutorial/avatar/yui-stage.js')
    });
    return {
        api: window.YuiGuideAvatarStage,
        window
    };
}

function loadAvatarStageApi() {
    return loadAvatarStageContext().api;
}

function createCornerPeekSession(position, options) {
    const normalizedOptions = options || {};
    const context = loadAvatarStageContext(normalizedOptions);
    const api = context.api;
    const coreModel = normalizedOptions.coreModel || {};
    const model = {
        x: 512,
        y: 384,
        rotation: 0,
        alpha: 0.8,
        destroyed: false,
        scale: {
            x: 1,
            y: 1,
            set(x, y) {
                this.x = x;
                this.y = y;
            }
        },
        internalModel: {
            coreModel
        },
        getBounds() {
            return {
                x: 362,
                y: 84,
                width: 300,
                height: 600
            };
        }
    };
    const manager = {
        currentModel: model,
        pixi_app: {
            renderer: {
                screen: {
                    width: 1024,
                    height: 768
                }
            }
        }
    };
    const session = new api.Live2DAvatarCornerPeekSession({
        manager,
        model,
        coreModel
    }, {
        position,
        isCancelled: normalizedOptions.isCancelled,
        container: normalizedOptions.container || {
            style: {}
        }
    });
    session.initialModelFrame = {
        x: model.x,
        y: model.y,
        scaleX: 1,
        scaleY: 1,
        rotation: 0
    };
    session.initialAlpha = model.alpha;
    session.initialBounds = model.getBounds();
    session.hiddenFrame = session.resolveHiddenFrame();
    session.peekRegionBounds = session.resolvePeekRegionBounds();
    session.cornerFrame = session.resolveCornerFrame();
    session.cornerHiddenFrame = session.resolveCornerHiddenFrame();
    return {
        session,
        model,
        window: context.window
    };
}

function createHeadAnchoredCornerPeekSession(position) {
    const api = loadAvatarStageApi();
    const coreModel = {};
    const model = {
        x: 512,
        y: 384,
        rotation: 0,
        alpha: 0.8,
        destroyed: false,
        scale: {
            x: 1,
            y: 1,
            set(x, y) {
                this.x = x;
                this.y = y;
            }
        },
        internalModel: {
            coreModel
        },
        getBounds() {
            return {
                x: 362,
                y: 84,
                width: 300,
                height: 600
            };
        }
    };
    const headRect = {
        left: 438,
        top: 104,
        right: 586,
        bottom: 252,
        width: 148,
        height: 148,
        centerX: 512,
        centerY: 178
    };
    const bodyRect = {
        left: 414,
        top: 250,
        right: 610,
        bottom: 560,
        width: 196,
        height: 310,
        centerX: 512,
        centerY: 405
    };
    const manager = {
        currentModel: model,
        getHeadScreenRectInfo() {
            return { rect: headRect };
        },
        getBodyScreenRectInfo() {
            return { rect: bodyRect };
        },
        pixi_app: {
            renderer: {
                screen: {
                    width: 1024,
                    height: 768
                }
            }
        }
    };
    const session = new api.Live2DAvatarCornerPeekSession({
        manager,
        model,
        coreModel
    }, {
        position,
        container: {
            style: {}
        }
    });
    session.initialModelFrame = {
        x: model.x,
        y: model.y,
        scaleX: 1,
        scaleY: 1,
        rotation: 0
    };
    session.initialAlpha = model.alpha;
    session.initialBounds = model.getBounds();
    session.hiddenFrame = session.resolveHiddenFrame();
    session.peekRegionBounds = session.resolvePeekRegionBounds();
    session.cornerFrame = session.resolveCornerFrame();
    session.cornerHiddenFrame = session.resolveCornerHiddenFrame();
    return {
        session,
        model,
        headRect,
        bodyRect
    };
}

test('returns fixed Live2D corner peek cues without image resources', () => {
    assert.equal(standIn.getCue(2, 'day2_tool_toggle_intro'), null);
    assert.equal(
        standIn.getCue(2, 'day2_avatar_tools'),
        null,
        'day2_avatar_tools stays disabled because it sits too close to the opening motion'
    );
    assert.deepEqual(standIn.getCue(2, 'day2_galgame_entry'), {
        delay: 900,
        duration: 5000,
        position: 'top-right'
    });
    assert.deepEqual(standIn.getCue(3, 'day3_proactive_chat'), {
        delay: 900,
        duration: 5000,
        position: 'top-left'
    });
    assert.deepEqual(standIn.getCue(4, 'day4_privacy_mode'), {
        delay: 900,
        duration: 5000,
        position: 'bottom-right'
    });
    assert.equal(standIn.getCue(5, 'day5_character_settings'), null);
    assert.equal(standIn.getCue(7, 'day7_memory_review'), null);
    assert.equal(standIn.getCue(7, 'day7_wrap'), null);
    assert.equal(typeof standIn.getResourcePath, 'undefined');
});

test('exports all fixed day two through seven cue positions', () => {
    const cues = standIn.getAllCues();
    const allowedPositions = new Set(['bottom-right', 'bottom-left', 'top-right', 'top-left']);
    const expectedCueCounts = {
        2: 1,
        3: 1,
        4: 2,
        5: 0,
        6: 2,
        7: 0
    };

    assert.equal(Object.keys(cues).length, 6);
    for (const day of [2, 3, 4, 5, 6, 7]) {
        assert.equal(Object.keys(cues[day]).length, expectedCueCounts[day]);
        Object.values(cues[day]).forEach((cue) => {
            assert.equal(cue.duration, 5000);
            assert.equal(Object.prototype.hasOwnProperty.call(cue, 'durationMs'), false);
            assert.equal(Object.prototype.hasOwnProperty.call(cue, 'delayMs'), false);
            assert.equal(allowedPositions.has(cue.position), true);
            assert.equal(Object.prototype.hasOwnProperty.call(cue, 'resource'), false);
        });
    }
});

test('does not schedule Live2D corner peek on final wrap-adjacent scenes', () => {
    assert.equal(standIn.getCue(2, 'day2_tool_toggle_intro'), null);
    assert.equal(
        standIn.getCue(2, 'day2_avatar_tools'),
        null,
        'day2_avatar_tools intentionally remains outside the legacy stand-in cue table'
    );
    assert.equal(standIn.getCue(2, 'day2_galgame_choices'), null);
    assert.equal(standIn.getCue(4, 'day4_return_home'), null);
    assert.equal(standIn.getCue(5, 'day5_character_settings'), null);
    assert.equal(standIn.getCue(5, 'day5_memory_entry'), null);
    assert.equal(standIn.getCue(6, 'day6_wrap_cleanup'), null);
    assert.equal(standIn.getCue(7, 'day7_memory_review'), null);
    assert.equal(standIn.getCue(7, 'day7_memory_control'), null);
    assert.equal(standIn.getCue(7, 'day7_graduation_wrap'), null);
});

test('director routes avatar stand-ins through Live2D corner peek, not overlay images', () => {
    assert.match(controllerSource, /class AvatarStandInController/);
    assert.match(directorSource, /this\.avatarStandInController = new TutorialVisualControllers\.AvatarStandInController\(this\);/);
    assert.match(directorSource, /this\.startAvatarCornerPeekPerformance\({[\s\S]*position: cue\.position/);
    assert.match(controllerSource, /Number\.isFinite\(Number\(cue\.delay\)\)/);
    assert.match(directorSource, /Number\.isFinite\(Number\(cue\.duration\)\)/);
    assert.match(directorSource, /await this\.stopAvatarCornerPeekPerformance\(handle,\s*reason \|\| 'avatar_standin_clear'\);/);
    assert.doesNotMatch(directorSource, /overlay\.showAvatarStandIn/);
    assert.doesNotMatch(overlaySource, /showAvatarStandIn/);
    assert.doesNotMatch(overlaySource, /avatarStandIn:\s*\{/);
});

test('avatar stage exposes generic Live2D corner peek while keeping plugin-dashboard entry', () => {
    assert.match(avatarStageSource, /class Live2DAvatarCornerPeekSession/);
    assert.match(avatarStageSource, /async function startAvatarCornerPeek\(options\)/);
    assert.match(avatarStageSource, /startAvatarCornerPeek: startAvatarCornerPeek/);
    assert.match(avatarStageSource, /startPluginDashboardCornerPeek: startPluginDashboardCornerPeek/);
    assert.match(avatarStageSource, /Live2DPluginDashboardCornerSession: Live2DAvatarCornerPeekSession/);
});

test('avatar stage exposes reusable motion core and preset playback entry', () => {
    assert.match(avatarStageSource, /class Live2DMotionBaseSession/);
    assert.match(avatarStageSource, /class Live2DFrameMotionSession extends Live2DMotionBaseSession/);
    assert.match(avatarStageSource, /async function playAvatarMotion\(options\)/);
    assert.match(avatarStageSource, /playAvatarMotion: playAvatarMotion/);
    assert.match(avatarStageSource, /Live2DMotionBaseSession: Live2DMotionBaseSession/);
    assert.match(avatarStageSource, /Live2DFrameMotionSession: Live2DFrameMotionSession/);
});

test('bottom-rise intro avatar motion approaches and holds the first-day half-body frame', () => {
    assert.match(avatarStageSource, /to:\s*isBottomRise\s*\?\s*'close-up'\s*:/);
    assert.match(avatarStageSource, /frameScale[\s\S]*:\s*INTRO_GREETING_HUG_CLOSE_SCALE/);
    assert.match(avatarStageSource, /frameY[\s\S]*resolveIntroGreetingHugFrameShift\(this\.container\)/);
    assert.match(avatarStageSource, /frameY[\s\S]*:\s*undefined/);
    assert.match(avatarStageSource, /restoreMode[\s\S]*normalizedOptions\.restore[\s\S]*\|\|\s*'half-body'/);
    assert.match(avatarStageSource, /this\.restoreMode === 'half-body'[\s\S]*this\.applyFrame\(this\.toFrame, this\.initialAlpha\)/);
});

test('corner and top peek intro avatar motions settle on the first-day half-body frame', () => {
    assert.match(avatarStageSource, /function applyAvatarMotionHalfBodyPlacement\(options\)/);
    assert.match(avatarStageSource, /INTRO_GREETING_HUG_CLOSE_SCALE/);
    assert.match(avatarStageSource, /const container = getLive2DContainer/);
    assert.match(avatarStageSource, /resolveIntroGreetingHugFrameShift\(container\)/);
    assert.match(avatarStageSource, /await handle\.stop\('avatar_motion_complete', \{ animateReturn: false \}\);[\s\S]*if \(restoreMode === 'half-body'\)/);
    assert.match(avatarStageSource, /applyAvatarMotionHalfBodyPlacement\(normalizedOptions\)/);
});

test('corner peek keeps floating buttons frozen by default but intro motion can opt out', () => {
    assert.match(avatarStageSource, /this\.freezeFloatingButtons = normalizedOptions\.freezeFloatingButtons !== false;/);
    assert.match(avatarStageSource, /if \(this\.freezeFloatingButtons\) \{[\s\S]*this\.freezeFloatingButtonsPosition\(\);[\s\S]*\}/);
    assert.match(avatarStageSource, /freezeFloatingButtons: normalizedOptions\.freezeFloatingButtons/);
    assert.match(avatarStageSource, /function showAvatarMotionFloatingButtons\(options\)/);
    assert.match(avatarStageSource, /showAvatarMotionFloatingButtons\(normalizedOptions\)/);
    assert.match(directorSource, /freezeFloatingButtons: performance\.freezeFloatingButtons === false \? false : undefined/);
});

test('corner peek can rotate floating buttons only when intro motion opts in', () => {
    assert.match(avatarStageSource, /this\.rotateFloatingButtons = normalizedOptions\.rotateFloatingButtons === true;/);
    assert.match(avatarStageSource, /this\.syncFloatingButtonsRotation\(frame\)/);
    assert.match(avatarStageSource, /manager\._floatingButtonsRotationRadians = rotation;/);
    assert.match(live2dButtonsSource, /const rotation = Number\(this\._floatingButtonsRotationRadians\) \|\| 0;/);
    assert.match(live2dButtonsSource, /buttonsContainer\.style\.transform = `scale\(\$\{scale\}\)\$\{rotateTransform\}`;/);
    assert.match(directorSource, /rotateFloatingButtons: performance\.rotateFloatingButtons === true/);
});

test('corner and top peek intro avatar motions fade in the half-body handoff', () => {
    assert.match(avatarStageSource, /const AVATAR_MOTION_HALF_BODY_FADE_IN_MS = 900;/);
    assert.match(avatarStageSource, /const AVATAR_MOTION_HALF_BODY_FADE_OUT_MS = 420;/);
    assert.match(avatarStageSource, /function collectAvatarMotionVisibleOpacityTargets\(context, options\)/);
    assert.match(avatarStageSource, /function writeAvatarMotionVisibleOpacity\(context, targets, modelAlpha, displayAlpha\)/);
    assert.match(avatarStageSource, /async function fadeOutAvatarMotionVisibleLayer\(options\)/);
    assert.match(avatarStageSource, /async function fadeInAvatarMotionHalfBodyPlacement\(options\)/);
    assert.match(avatarStageSource, /const targetAlpha = 1;/);
    assert.match(avatarStageSource, /const targetDisplayAlpha = 1;/);
    assert.match(avatarStageSource, /await fadeOutAvatarMotionVisibleLayer\(normalizedOptions\)/);
    assert.match(avatarStageSource, /writeAvatarMotionVisibleOpacity\(context, targets, 0, 0\)/);
    assert.match(avatarStageSource, /writeAvatarMotionVisibleOpacity\(context, targets, lerp\(0, targetAlpha, eased\), displayAlpha\)/);
    assert.match(avatarStageSource, /await fadeInAvatarMotionHalfBodyPlacement\(normalizedOptions\)/);
});

test('soft approach intro avatar motion uses the first-day half-body scale', () => {
    assert.doesNotMatch(avatarStageSource, /preset === 'soft-approach'\s*\?\s*1\.08/);
    assert.match(avatarStageSource, /const frameScale = INTRO_GREETING_HUG_CLOSE_SCALE;/);
});

test('Live2D corner peek keeps center model still while fading and uses one second phases', () => {
    const { session, model } = createCornerPeekSession('bottom-right');

    assert.equal(session.hideMs, 1000);
    assert.equal(session.appearMs, 1000);
    assert.equal(session.totalDurationMs, 2000);

    session.tickEnter(500);
    assert.equal(model.x, 512);
    assert.equal(model.y, 384);
    assert.equal(model.alpha, 0.4);

    session.tickEnter(1500);
    assert.notEqual(model.x, 512);
    assert.notEqual(model.y, 384);
    assert.ok(model.alpha > 0);
    assert.ok(model.alpha < 1);

    session.tickExit(500);
    assert.equal(model.x, session.cornerFrame.x);
    assert.equal(model.y, session.cornerFrame.y);
    assert.equal(model.alpha, 0.5);

    session.tickExit(1500);
    assert.equal(model.x, 512);
    assert.equal(model.y, 384);
    assert.ok(model.alpha > 0);
    assert.ok(model.alpha < 0.8);
});

test('Live2D corner peek fades the visible layer and centers look-at during playback', () => {
    const acquiredLocks = [];
    const releasedLocks = [];
    const paramWrites = [];
    const canvas = { style: {} };
    const container = { style: {} };
    const coreModel = {
        setParameterValueById(id, value) {
            paramWrites.push({ id, value });
        }
    };
    const { session, window } = createCornerPeekSession('bottom-right', {
        container,
        coreModel,
        elements: {
            'live2d-canvas': canvas
        },
        configureWindow(testWindow) {
            testWindow.AvatarPerformance = {
                getDefaultCoordinator() {
                    return {
                        acquire(request) {
                            acquiredLocks.push(request);
                            return { id: 'corner-peek-lock' };
                        },
                        release(sessionRecord, reason) {
                            releasedLocks.push({ sessionRecord, reason });
                            return true;
                        }
                    };
                }
            };
        }
    });

    assert.equal(session.start(), true);
    assert.equal(window.nekoYuiGuideAvatarCornerPeekActive, true);
    assert.equal(window.nekoYuiGuideFaceForwardLock, true);
    assert.deepEqual(Array.from(acquiredLocks[0].capabilities), ['frame', 'lookAt']);
    assert.deepEqual(paramWrites.slice(-4), [
        { id: 'ParamAngleX', value: 0 },
        { id: 'ParamAngleY', value: 0 },
        { id: 'ParamEyeBallX', value: 0 },
        { id: 'ParamEyeBallY', value: 0 }
    ]);

    session.tickEnter(500);
    assert.equal(container.style.opacity, '0.4');
    assert.equal(canvas.style.opacity, '0.4');

    session.finish('test_complete');
    assert.equal(window.nekoYuiGuideAvatarCornerPeekActive, false);
    assert.equal(window.nekoYuiGuideFaceForwardLock, false);
    assert.equal(container.style.opacity, '');
    assert.equal(canvas.style.opacity, '');
    assert.equal(releasedLocks.length, 1);
});

test('Live2D corner peek disables opacity transitions while it owns the visible layer', () => {
    const canvas = { style: { opacity: '1', transition: 'opacity 0.28s ease' } };
    const container = { style: { opacity: '1', transition: 'opacity 0.28s ease' } };
    const { session } = createCornerPeekSession('bottom-right', {
        container,
        elements: {
            'live2d-canvas': canvas
        }
    });

    assert.equal(session.start(), true);
    session.tickEnter(500);

    assert.equal(container.style.opacity, '0.4');
    assert.equal(canvas.style.opacity, '0.4');
    assert.equal(container.style.transition, 'none');
    assert.equal(canvas.style.transition, 'none');

    session.finish('test_complete');
    assert.equal(container.style.opacity, '1');
    assert.equal(canvas.style.opacity, '1');
    assert.equal(container.style.transition, 'opacity 0.28s ease');
    assert.equal(canvas.style.transition, 'opacity 0.28s ease');
});

test('Live2D corner peek does not self-cancel its return fade after stand-in token changes', () => {
    let cancelled = false;
    const { session, model, window } = createCornerPeekSession('bottom-right', {
        isCancelled: () => cancelled
    });

    assert.equal(session.start(), true);
    window.__setNow(2500);
    session.tick();
    assert.equal(session.phase, 'hold');

    session.stop('avatar_standin_clear');
    cancelled = true;
    window.__setNow(3000);
    session.tick();

    assert.equal(session.phase, 'exit');
    assert.equal(model.x, session.cornerFrame.x);
    assert.equal(model.y, session.cornerFrame.y);
    assert.equal(model.alpha, 0.5);
});

test('Live2D visibility recovery preserves opacity while avatar corner peek is active', () => {
    assert.match(live2dInitSource, /nekoYuiGuideAvatarCornerPeekActive[\s\S]{0,120}return;/);
    assert.match(appUiSource, /preserveAvatarCornerPeekOpacity[\s\S]{0,240}model\.alpha = 1;/);
    assert.match(appInterpageSource, /preserveAvatarCornerPeekOpacity[\s\S]{0,240}currentModel\.alpha = 1;/);
    assert.match(universalManagerSource, /preserveAvatarCornerPeekOpacity[\s\S]{0,360}restoreTutorialLive2dDisplayState/);
    assert.match(universalManagerSource, /preserveOpacity[\s\S]{0,360}live2dCanvas\.style\.setProperty\('opacity', '1', 'important'\)/);
});

test('Live2D corner peek can continue across scene boundaries until its cue duration ends', () => {
    const showBlock = directorSource
        .split('        showAvatarStandIn(cue, token) {')[1]
        .split('        clearAvatarStandIn(options) {')[0];
    assert.doesNotMatch(showBlock, /sceneRunId !== this\.sceneRunId/);
    assert.match(showBlock, /isCancelled: \(\) => token !== this\.avatarStandInToken[\s\S]*this\.isStopping\(\)[\s\S]*this\.destroyed/);
});

test('Live2D interaction skips cursor focus while YUI face-forward lock is active', () => {
    assert.match(live2dInteractionSource, /nekoYuiGuideFaceForwardLock/);
    assert.match(live2dInteractionSource, /ParamAngleX/);
    assert.match(live2dInteractionSource, /ParamEyeBallY/);
    assert.match(live2dInteractionSource, /isYuiGuideFaceForwardLocked[\s\S]*model\.focus\(pointer\.x,\s*pointer\.y\)/);
});

test('top-left Live2D corner peek keeps enough of the model visible', () => {
    const { session } = createCornerPeekSession('top-left');
    const visibleWidth = Math.min(1024, session.cornerFrame.x + 150) - Math.max(0, session.cornerFrame.x - 150);
    const visibleHeight = Math.min(768, session.cornerFrame.y + 300) - Math.max(0, session.cornerFrame.y - 300);

    assert.ok(visibleWidth >= 120);
    assert.ok(visibleHeight >= 240);
});

test('Live2D corner peek uses corner-specific head-first rotation angles', () => {
    const expectedDegrees = {
        'bottom-right': -45,
        'bottom-left': 45,
        'top-right': -135,
        'top-left': 135
    };

    Object.keys(expectedDegrees).forEach((position) => {
        const { session } = createHeadAnchoredCornerPeekSession(position);
        const degrees = Math.round(session.cornerFrame.rotation * 180 / Math.PI);
        assert.equal(degrees, expectedDegrees[position], position);
    });
});

function transformRectFromFrame(rect, fromFrame, toFrame) {
    const rotation = (toFrame.rotation || 0) - (fromFrame.rotation || 0);
    const cos = Math.cos(rotation);
    const sin = Math.sin(rotation);
    const points = [
        { x: rect.x, y: rect.y },
        { x: rect.x + rect.width, y: rect.y },
        { x: rect.x, y: rect.y + rect.height },
        { x: rect.x + rect.width, y: rect.y + rect.height }
    ].map((point) => {
        const dx = point.x - fromFrame.x;
        const dy = point.y - fromFrame.y;
        return {
            x: toFrame.x + dx * cos - dy * sin,
            y: toFrame.y + dx * sin + dy * cos
        };
    });
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    const left = Math.min(...xs);
    const top = Math.min(...ys);
    const right = Math.max(...xs);
    const bottom = Math.max(...ys);
    return {
        left,
        top,
        right,
        bottom,
        width: right - left,
        height: bottom - top
    };
}

function intersectionArea(rect, viewport) {
    const left = Math.max(rect.left, viewport.left);
    const top = Math.max(rect.top, viewport.top);
    const right = Math.min(rect.right, viewport.right);
    const bottom = Math.min(rect.bottom, viewport.bottom);
    return Math.max(0, right - left) * Math.max(0, bottom - top);
}

test('Live2D corner peek anchors the head and upper chest from every corner', () => {
    const viewport = { left: 0, top: 0, right: 1024, bottom: 768 };
    for (const position of ['bottom-right', 'bottom-left', 'top-right', 'top-left']) {
        const { session } = createHeadAnchoredCornerPeekSession(position);
        const visiblePeekRect = transformRectFromFrame(
            session.peekRegionBounds,
            session.initialModelFrame,
            session.cornerFrame
        );
        const visibleModelRect = transformRectFromFrame(
            session.initialBounds,
            session.initialModelFrame,
            session.cornerFrame
        );
        const peekArea = visiblePeekRect.width * visiblePeekRect.height;
        const modelArea = visibleModelRect.width * visibleModelRect.height;
        const peekVisibleRatio = intersectionArea(visiblePeekRect, viewport) / peekArea;
        const modelVisibleRatio = intersectionArea(visibleModelRect, viewport) / modelArea;

        assert.ok(peekVisibleRatio >= 0.8, position);
        assert.ok(modelVisibleRatio <= 0.62, position);
        if (position === 'top-left' || position === 'top-right') {
            assert.ok(visiblePeekRect.top >= 40, position);
            assert.ok(visiblePeekRect.top <= 80, position);
            assert.ok(visiblePeekRect.bottom < 360, position);
        } else {
            assert.ok(visiblePeekRect.bottom >= 688, position);
            assert.ok(visiblePeekRect.bottom <= 728, position);
            assert.ok(visiblePeekRect.top > 400, position);
        }
        if (position === 'top-left' || position === 'bottom-left') {
            assert.ok(visiblePeekRect.left >= 40, position);
            assert.ok(visiblePeekRect.left <= 80, position);
        } else {
            assert.ok(visiblePeekRect.right >= 944, position);
            assert.ok(visiblePeekRect.right <= 984, position);
        }
    }
});
