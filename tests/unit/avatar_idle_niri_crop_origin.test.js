const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');

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

function readSection(source, startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    assert.notEqual(start, -1, `missing section start: ${startMarker}`);
    const end = source.indexOf(endMarker, start + startMarker.length);
    assert.notEqual(end, -1, `missing section end: ${endMarker}`);
    return source.slice(start, end);
}

test('CAT1 desktop chat rect uses the Niri virtual viewport origin after physical crop', () => {
    const context = {
        Date,
        window: {
            screenX: 1224,
            screenY: 84,
            __nekoNiriPetPhysicalCrop: {
                getState() {
                    return {
                        enabled: true,
                        virtualBounds: { x: 1, y: 1, width: 1706, height: 1066 },
                        cropBounds: { x: 1224, y: 84, width: 492, height: 636 },
                        offsetX: 1223,
                        offsetY: 83
                    };
                }
            }
        },
        _NEKO_IDLE_DESKTOP_COMPACT_SURFACE_RECT_STALE_MS: 10_000,
        _NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS: 2_500,
        _nekoIdleDesktopChatMinimizedState: null,
        _nekoIdleDesktopCompactSurfaceState: {
            visible: true,
            screenRect: { x: 295, y: 200, width: 88, height: 88 },
            updatedAt: Date.now(),
            sourceUpdatedAt: Date.now()
        }
    };
    vm.createContext(context);
    const snippets = [
        readFunction('static/avatar/avatar-ui-buttons/core.js', '_getNekoDesktopVirtualViewportOrigin'),
        readFunction('static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js', '_normalizeNekoIdleScreenRect'),
        readFunction('static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js', '_getNekoIdleDesktopCompactSurfaceRect')
    ];
    vm.runInContext(snippets.join('\n'), context);

    const rect = vm.runInContext('_getNekoIdleDesktopCompactSurfaceRect()', context);

    assert.equal(rect.left, 294);
    assert.equal(rect.top, 199);
    assert.equal(rect.screenLeft, 295);
    assert.notEqual(rect.left, 0, 'the cat target must not be clamped from a false negative local coordinate');
});

test('CAT1 converts a cropped DOM rect back to virtual desktop coordinates before moving', () => {
    const context = {
        window: {
            __nekoNiriPetPhysicalCrop: {
                toVirtualRect(rect) {
                    return {
                        x: rect.x + 1499,
                        y: rect.y + 251,
                        width: rect.width,
                        height: rect.height
                    };
                }
            }
        }
    };
    vm.createContext(context);
    vm.runInContext([
        readFunction('static/avatar/avatar-ui-buttons/core.js', '_getNekoDesktopVirtualRect'),
        readFunction('static/avatar/avatar-ui-buttons/core.js', '_getNekoDesktopVirtualElementRect')
    ].join('\n'), context);

    context.cat = {
        getBoundingClientRect() {
            return { x: 1, y: 1, left: 1, top: 1, width: 122, height: 122 };
        }
    };
    const rect = vm.runInContext('_getNekoDesktopVirtualElementRect(cat)', context);

    assert.deepEqual(
        JSON.parse(JSON.stringify(rect)),
        {
            x: 1500,
            y: 252,
            left: 1500,
            top: 252,
            width: 122,
            height: 122,
            right: 1622,
            bottom: 374
        }
    );
});

test('CAT1 virtual rect fallback keeps the crop offset when conversion is unavailable', () => {
    const context = {
        window: {
            __nekoNiriPetPhysicalCrop: {
                getState() {
                    return {
                        enabled: true,
                        offsetX: 1499,
                        offsetY: 251
                    };
                }
            }
        }
    };
    vm.createContext(context);
    vm.runInContext(
        readFunction('static/avatar/avatar-ui-buttons/core.js', '_getNekoDesktopVirtualRect'),
        context
    );

    context.localRect = { left: 1, top: 1, width: 122, height: 122 };
    const withoutConverter = vm.runInContext('_getNekoDesktopVirtualRect(localRect)', context);
    assert.equal(withoutConverter.left, 1500);
    assert.equal(withoutConverter.top, 252);

    context.window.__nekoNiriPetPhysicalCrop.toVirtualRect = () => {
        throw new Error('converter unavailable');
    };
    const throwingConverter = vm.runInContext('_getNekoDesktopVirtualRect(localRect)', context);
    assert.equal(throwingConverter.left, 1500);
    assert.equal(throwingConverter.top, 252);
});

test('CAT1 drag keeps using the virtual desktop size while the Pet window is cropped', () => {
    const context = {
        window: {
            innerWidth: 360,
            innerHeight: 408,
            __nekoNiriPetPhysicalCrop: {
                getState() {
                    return {
                        enabled: true,
                        virtualBounds: { x: 1, y: 1, width: 1706, height: 1066 },
                        cropBounds: { x: 1, y: 228, width: 1706, height: 360 }
                    };
                }
            }
        }
    };
    vm.createContext(context);
    vm.runInContext(
        readFunction('static/avatar/avatar-ui-buttons/core.js', '_getNekoDesktopVirtualViewportSize'),
        context
    );

    const size = vm.runInContext('_getNekoDesktopVirtualViewportSize()', context);

    assert.deepEqual(JSON.parse(JSON.stringify(size)), { width: 1706, height: 1066 });
});

test('CAT1 automatic targets clamp against the virtual desktop instead of the Pet crop', () => {
    const context = {
        window: {
            innerWidth: 360,
            innerHeight: 408,
            __nekoNiriPetPhysicalCrop: {
                getState() {
                    return {
                        enabled: true,
                        virtualBounds: { x: 1, y: 1, width: 1706, height: 1066 }
                    };
                }
            }
        }
    };
    vm.createContext(context);
    vm.runInContext([
        readFunction('static/avatar/avatar-ui-buttons/core.js', '_getNekoDesktopVirtualViewportSize'),
        readFunction(
            'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js',
            '_clampNekoIdleCat1Position'
        )
    ].join('\n'), context);

    const position = vm.runInContext(
        '_clampNekoIdleCat1Position(1500, 900, 122, 122)',
        context
    );
    assert.deepEqual(
        JSON.parse(JSON.stringify(position)),
        { left: 1500, top: 900 }
    );
});

test('CAT1 pair movement clamps vectors against the virtual desktop instead of the Pet crop', () => {
    const context = {
        window: {
            innerWidth: 360,
            innerHeight: 408,
            __nekoNiriPetPhysicalCrop: {
                getState() {
                    return {
                        enabled: true,
                        virtualBounds: { x: 1, y: 1, width: 1706, height: 1066 }
                    };
                }
            }
        }
    };
    vm.createContext(context);
    vm.runInContext([
        readFunction('static/avatar/avatar-ui-buttons/core.js', '_getNekoDesktopVirtualViewportSize'),
        readFunction(
            'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js',
            '_clampNekoIdleCat1MoveVector'
        )
    ].join('\n'), context);

    const vector = vm.runInContext(
        `_clampNekoIdleCat1MoveVector(
            { left: 1200, top: 700, right: 1322, bottom: 822 },
            null,
            200,
            50
        )`,
        context
    );
    assert.deepEqual(
        JSON.parse(JSON.stringify(vector)),
        { dx: 200, dy: 50, distance: Math.hypot(200, 50) }
    );
});

test('model-to-cat anchor converts client managers once but keeps Live2D virtual bounds unchanged', () => {
    const context = {
        I: {
            toNekoVirtualTransitionRect(rect) {
                return {
                    left: Number(rect.left) + 1200,
                    top: Number(rect.top) + 80,
                    width: Number(rect.width),
                    height: Number(rect.height),
                    right: Number(rect.left) + 1200 + Number(rect.width),
                    bottom: Number(rect.top) + 80 + Number(rect.height)
                };
            }
        }
    };
    vm.createContext(context);
    vm.runInContext([
        readFunction('static/app/app-ui/return-transitions.js', 'normalizeNekoScreenRect'),
        readFunction('static/app/app-ui/return-transitions.js', 'getNekoTransitionRect'),
        readFunction('static/app/app-ui/return-transitions.js', 'getModelRectFromManager')
    ].join('\n'), context);

    context.live2dManager = {
        getModelScreenBounds() {
            return { left: 1320, top: 372, width: 387, height: 576 };
        }
    };
    context.mmdManager = {
        getModelScreenBounds() {
            return { left: 120, top: 292, width: 387, height: 576 };
        }
    };

    const live2dRect = vm.runInContext(
        "getModelRectFromManager(live2dManager, { screenBoundsSpace: 'virtual' })",
        context
    );
    const mmdRect = vm.runInContext(
        "getModelRectFromManager(mmdManager, { screenBoundsSpace: 'client' })",
        context
    );

    assert.equal(live2dRect.left, 1320, 'Live2D must not receive the crop offset twice');
    assert.equal(live2dRect.top, 372);
    assert.equal(mmdRect.left, 1320, 'DOM/canvas client bounds must receive the crop offset once');
    assert.equal(mmdRect.top, 372);
});

test('CAT1 drag writes virtual coordinates without subtracting the crop offset twice', () => {
    const source = fs.readFileSync(
        path.join(projectRoot, 'static/avatar/avatar-ui-buttons/methods-return.js'),
        'utf8'
    );
    const handleMoveSource = readSection(
        source,
        "const handleMove = (clientX, clientY, sourceEvent = null, movePoint = null) => {",
        "const handleStart = (clientX, clientY, pointerType = 'mouse', sourceEvent = null, startPoint = null) => {"
    );
    const handleStartSource = readSection(
        source,
        "const handleStart = (clientX, clientY, pointerType = 'mouse', sourceEvent = null, startPoint = null) => {",
        "const handleEnd = () => {"
    );
    const handleEndSource = readSection(
        source,
        "const handleEnd = () => {",
        "container.addEventListener('mousedown'"
    );
    const mouseMoveSource = readSection(source, "mouseMove: (e) => {", "mouseUp: handleEnd,");
    const cropStateAppliedSource = readSection(
        source,
        "cropStateApplied: (event) => {",
        "document.addEventListener('mousemove'"
    );
    const cropReadySource = readSection(
        source,
        "const isNiriReturnBallFullCropReady = (",
        "const clearDragCropHoldPending = () => {"
    );
    const finishStateSource = readSection(
        source,
        "const finishDragState = (moved, safetyToken, suppressClick = moved) => {",
        "const resetDragStateAfterMissingEnd = (safetyToken) => {"
    );

    assert.match(handleMoveSource, /const virtualViewport = _getNekoDesktopVirtualViewportSize\(\);/);
    assert.match(handleMoveSource, /container\.style\.left = `\$\{nextVirtualLeft\}px`;/);
    assert.match(handleMoveSource, /container\.style\.top = `\$\{nextVirtualTop\}px`;/);
    assert.doesNotMatch(handleMoveSource, /nextVirtualLeft - offset\.x|nextVirtualTop - offset\.y/);
    assert.match(handleMoveSource, /return-ball-drag-active', \{[\s\S]*?dragSessionId: dragSafetyToken/);
    assert.match(handleMoveSource, /return-ball-drag-motion', \{[\s\S]*?dragSessionId: dragSafetyToken/);
    assert.match(finishStateSource, /return-ball-drag-end', \{[\s\S]*?dragSessionId: safetyToken/);
    assert.match(handleMoveSource, /const w = dragVisualWidth;[\s\S]*?const h = dragVisualHeight;/);
    assert.match(
        handleStartSource,
        /_getNekoIdleReturnDragGrabOffset\([\s\S]*?useLocalGrabAnchor \? localRect : rect,[\s\S]*?useLocalGrabAnchor \? 'local' : 'virtual'[\s\S]*?dragGrabOffsetX = grabOffset\.x;[\s\S]*?dragGrabOffsetY = grabOffset\.y;/
    );
    assert.match(
        mouseMoveSource,
        /mouseMove: \(e\) => \{[\s\S]*?if \(shouldUseGlobalCursorForMouseDrag\(\)\) return;[\s\S]*?!shouldIgnoreMissingMouseButtons\(\)[\s\S]*?handleMove\(point\.x, point\.y, e, point\);/
    );
    assert.match(
        handleEndSource,
        /if \(movedPastThreshold && dragCropHoldPending\)[\s\S]*?dragReleasePending = true[\s\S]*?finishDragState\(false, safetyToken, true\)/
    );
    assert.match(
        cropStateAppliedSource,
        /cropStateApplied:[\s\S]*?isNiriReturnBallFullCropReady\(detail, true, dragSafetyToken\)[\s\S]*?handleMove\([\s\S]*?if \(dragReleasePending && !dragCropHoldPending\)[\s\S]*?finishDragState\(true, safetyToken\)/
    );
    assert.match(
        cropReadySource,
        /const stateSession = Math\.max\(0, Math\.round\(Number\(state\.dragSessionId\) \|\| 0\)\);[\s\S]*?if \(expectedSession && stateSession !== expectedSession\) return false;/
    );
});

test('CAT1 Niri drag preserves the exact pointer grab point across crop changes', () => {
    const context = {};
    vm.createContext(context);
    vm.runInContext(
        readFunction(
            'static/avatar/avatar-ui-buttons/methods-return.js',
            '_getNekoIdleReturnDragGrabOffset'
        ),
        context
    );

    context.point = {
        localX: 72,
        localY: 96,
        virtualX: 620,
        virtualY: 430
    };
    context.localRect = { left: 20, top: 30, width: 122, height: 122 };

    const anchor = vm.runInContext(
        "_getNekoIdleReturnDragGrabOffset(point, localRect, 'local')",
        context
    );

    assert.deepEqual(
        JSON.parse(JSON.stringify(anchor)),
        { x: 52, y: 66 },
        'the anchor must stay in the raw local coordinate frame while the crop origin changes'
    );
});

test('CAT1 Niri mouse drag has one authoritative movement source', () => {
    const source = fs.readFileSync(
        path.join(projectRoot, 'static/avatar/avatar-ui-buttons/methods-return.js'),
        'utf8'
    );

    assert.match(source, /dragUsesGlobalCursor = pointerType === 'mouse' && canPollNiriDragCursor\(\);/);
    assert.match(
        source,
        /mouseMove: \(e\) => \{[\s\S]*?if \(shouldUseGlobalCursorForMouseDrag\(\)\) return;[\s\S]*?const rawPoint = getDragPoint\(e, e\.clientX, e\.clientY\);[\s\S]*?getContinuousDomMouseDragPoint\(rawPoint, e\)/
    );
    assert.match(
        source,
        /cropStateApplied:[\s\S]*?window\.electronScreen\.getCursorPoint\(\)[\s\S]*?getDragPointFromScreenPoint\(screenPoint\)[\s\S]*?flushPoint\(isUsableDragPoint\(point\) \? point : fallbackPoint\)/
    );
});

test('CAT1 only selects global cursor polling when the desktop runtime explicitly supports it', () => {
    const context = {};
    vm.createContext(context);
    vm.runInContext(
        readFunction(
            'static/avatar/avatar-ui-buttons/methods-return.js',
            '_canNekoIdleReturnDragUseGlobalCursor'
        ),
        context
    );

    context.cursorApi = { getCursorPoint() {} };
    context.nativeWayland = {
        isWayland: true,
        canReadGlobalCursorScreenPoint: false
    };
    context.supportedDesktop = {
        isWayland: false,
        canReadGlobalCursorScreenPoint: true
    };

    assert.equal(
        vm.runInContext(
            '_canNekoIdleReturnDragUseGlobalCursor(nativeWayland, cursorApi, true)',
            context
        ),
        false,
        'an exposed API must not become authoritative when native Wayland returns null'
    );
    assert.equal(
        vm.runInContext(
            '_canNekoIdleReturnDragUseGlobalCursor({}, cursorApi, true)',
            context
        ),
        false,
        'older runtimes without an explicit capability must safely keep DOM movement'
    );
    assert.equal(
        vm.runInContext(
            '_canNekoIdleReturnDragUseGlobalCursor(supportedDesktop, cursorApi, true)',
            context
        ),
        true
    );
});

test('CAT1 keeps native Wayland DOM drag alive when Chromium temporarily clears buttons', () => {
    const context = {};
    vm.createContext(context);
    vm.runInContext(
        readFunction(
            'static/avatar/avatar-ui-buttons/methods-return.js',
            '_shouldNekoIdleReturnDragIgnoreMissingMouseButtons'
        ),
        context
    );

    context.nativeWayland = { isWayland: true };
    context.x11 = { isWayland: false };

    assert.equal(
        vm.runInContext(
            "_shouldNekoIdleReturnDragIgnoreMissingMouseButtons(nativeWayland, 'mouse', false)",
            context
        ),
        true
    );
    assert.equal(
        vm.runInContext(
            "_shouldNekoIdleReturnDragIgnoreMissingMouseButtons(x11, 'mouse', false)",
            context
        ),
        false
    );
    assert.equal(
        vm.runInContext(
            "_shouldNekoIdleReturnDragIgnoreMissingMouseButtons(nativeWayland, 'touch', false)",
            context
        ),
        false
    );
});

test('CAT1 keeps one continuous virtual pointer when Niri switches the Pet crop origin', () => {
    const context = {};
    vm.createContext(context);
    vm.runInContext(
        readFunction(
            'static/avatar/avatar-ui-buttons/methods-return.js',
            '_getNekoIdleReturnDragContinuousVirtualPoint'
        ),
        context
    );

    context.previous = { virtualX: 1110, virtualY: 500 };
    context.beforeExpansion = {
        localX: 77,
        localY: 81,
        virtualX: 1120,
        virtualY: 500
    };
    const stableBeforeExpansion = vm.runInContext(
        '_getNekoIdleReturnDragContinuousVirtualPoint(previous, beforeExpansion, 10, 0)',
        context
    );
    assert.equal(stableBeforeExpansion.virtualX, 1120);
    assert.equal(stableBeforeExpansion.virtualY, 500);
    assert.equal(stableBeforeExpansion.continuityBasis, 'absolute-reconciled');

    // Niri has moved the physical carrier from x=1044 to x=1, but Chromium can
    // keep reporting this mouse sequence in the compact carrier's old frame.
    // Treating that raw client point as full-window coordinates would jump the
    // kitten left by roughly the old crop origin.
    context.previous = stableBeforeExpansion;
    context.originSwitch = {
        localX: 77,
        localY: 81,
        virtualX: 77,
        virtualY: 81
    };
    const heldAcrossOriginSwitch = vm.runInContext(
        '_getNekoIdleReturnDragContinuousVirtualPoint(previous, originSwitch, -1043, -419)',
        context
    );
    assert.equal(heldAcrossOriginSwitch.virtualX, 1120);
    assert.equal(heldAcrossOriginSwitch.virtualY, 500);
    assert.equal(heldAcrossOriginSwitch.continuityBasis, 'hold');

    context.previous = heldAcrossOriginSwitch;
    context.afterExpansion = {
        localX: 87,
        localY: 86,
        virtualX: 87,
        virtualY: 86
    };
    const continuedAfterExpansion = vm.runInContext(
        '_getNekoIdleReturnDragContinuousVirtualPoint(previous, afterExpansion, 10, 5)',
        context
    );
    assert.equal(continuedAfterExpansion.virtualX, 1130);
    assert.equal(continuedAfterExpansion.virtualY, 505);
    assert.equal(
        continuedAfterExpansion.continuityBasis,
        'movement',
        'the old-frame absolute coordinate must not replace the continuous drag point'
    );
});

test('CAT1 cursor polling keeps global coordinates while the Niri Pet crop expands', () => {
    const context = {};
    vm.createContext(context);
    vm.runInContext(
        readFunction(
            'static/avatar/avatar-ui-buttons/methods-return.js',
            '_getNekoIdleReturnDragGlobalScreenPoint'
        ),
        context
    );

    context.compactCrop = {
        cropBounds: { x: 924, y: 516, width: 252, height: 252 }
    };
    context.cursor = {
        x: 120,
        y: 134,
        screenX: 1044,
        screenY: 650
    };
    const direct = vm.runInContext(
        '_getNekoIdleReturnDragGlobalScreenPoint(cursor, compactCrop)',
        context
    );

    assert.deepEqual(
        JSON.parse(JSON.stringify(direct)),
        { x: 1044, y: 650 },
        'window-local x/y must never replace the explicit global screen coordinates'
    );

    context.legacyCursor = { x: 120, y: 134 };
    const recovered = vm.runInContext(
        '_getNekoIdleReturnDragGlobalScreenPoint(legacyCursor, compactCrop)',
        context
    );
    assert.deepEqual(
        JSON.parse(JSON.stringify(recovered)),
        { x: 1044, y: 650 },
        'legacy local-only cursor payloads should be rebased through the compact crop bounds'
    );

    context.fullCrop = {
        cropBounds: { x: 1, y: 1, width: 1706, height: 1066 }
    };
    const afterExpansion = vm.runInContext(
        '_getNekoIdleReturnDragGlobalScreenPoint(cursor, fullCrop)',
        context
    );
    assert.deepEqual(
        JSON.parse(JSON.stringify(afterExpansion)),
        { x: 1044, y: 650 },
        'the global pointer must stay stable when the physical carrier expands'
    );
});

test('CAT1 movement paths never consume cropped container rects directly', () => {
    const journey = fs.readFileSync(
        path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js'),
        'utf8'
    );
    const drag = fs.readFileSync(
        path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js'),
        'utf8'
    );

    assert.doesNotMatch(journey, /(?:catRect|currentRect|containerRect|const rect) = container\.getBoundingClientRect\(\)/);
    assert.match(journey, /const catRect = _getNekoDesktopVirtualElementRect\(container\);/);
    assert.match(drag, /const catRect = _getNekoDesktopVirtualElementRect\(container\);/);
});

test('CAT1 return click publishes the virtual rect used by model restore', () => {
    const source = fs.readFileSync(
        path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-playground.js'),
        'utf8'
    );

    assert.match(
        source,
        /const rect = _getNekoDesktopVirtualElementRect\(container\)[\s\S]*?\|\| container\.getBoundingClientRect\(\)/
    );
    assert.match(source, /returnButtonRect: \{[\s\S]*?left: rect\.left[\s\S]*?top: rect\.top/);
    assert.match(source, /anchorRect: rect/);
});
