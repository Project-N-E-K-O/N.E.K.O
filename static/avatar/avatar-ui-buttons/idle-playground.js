function _getNekoIdleCat1PlaygroundDropState(button) {
    if (!button) return null;
    if (!button.__nekoIdleCat1PlaygroundDropState) {
        button.__nekoIdleCat1PlaygroundDropState = {
            active: false,
            released: true,
            phase: 'inactive',
            token: 0,
            frame: 0,
            button: button,
            container: null,
            targetMode: 'none',
            targetElement: null,
            targetScreenRect: null,
            start: null,
            end: null,
            lastTickAt: 0,
            bodies: new Map(),
            draggingBodyId: '',
            lastPointerSamples: [],
            gravityPxPerSecond2: _NEKO_IDLE_CAT1_PLAYGROUND_GRAVITY_PX_PER_SECOND2,
            floorY: 0,
            wallLeft: 0,
            wallRight: 0,
            maxDeltaMs: _NEKO_IDLE_CAT1_PLAYGROUND_MAX_DELTA_MS,
            disabledCapabilities: new Set(),
            restoreSnapshot: {},
            releaseReason: '',
            previousArt: '',
            entryQuestionBlockElement: null,
            cleanups: [],
            pointerHandlers: [],
            pointerBodyId: '',
            pointerId: null,
            pointerOffsetX: 0,
            pointerOffsetY: 0,
            pointerStartX: 0,
            pointerStartY: 0,
            pointerMoved: false,
            suppressClickBodyId: '',
            suppressClickTimer: 0
        };
    }
    return button.__nekoIdleCat1PlaygroundDropState;
}

function _dispatchNekoIdleCat1PlaygroundState(button, active, reason) {
    const detail = {
        active: !!active,
        reason: reason || (active ? 'active' : 'inactive'),
        source: 'cat1-playground',
        timestamp: Date.now()
    };
    try {
        window.dispatchEvent(new CustomEvent('neko:idle-cat1-playground-state', {
            detail
        }));
    } catch (_) {}
}

function _acquireNekoIdleCat1PlaygroundDropLifecycle(button, entryDetail) {
    const state = _getNekoIdleCat1PlaygroundDropState(button);
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!state || !button || !container) return null;

    if (state.active) {
        _clearNekoIdleCat1QuestionMark(button);
        return state;
    }
    state.active = true;
    state.released = false;
    state.phase = 'dropping';
    state.token += 1;
    state.button = button;
    state.container = container;
    state.releaseReason = '';
    state.lastTickAt = 0;
    state.start = null;
    state.end = null;
    state.bodies = new Map();
    state.draggingBodyId = '';
    state.lastPointerSamples = [];
    state.entryQuestionBlockElement = _consumeNekoIdleCat1PlaygroundQuestionBlockClone(button);
    state.cleanups = [];
    state.pointerHandlers = [];
    state.disabledCapabilities = new Set([
        'cat1-state-switch',
        'return-art-source',
        'return-container-position',
        'question-mark-keyboard',
        'question-mark-entry',
        'ordinary-drag',
        'return-ball-drag',
        'random-actions',
        'journey',
        'pair-move',
        'eat',
        'play',
        'hover',
        'edge-peek',
        'compact-follow',
        'dock-return-goodbye'
    ]);
    state.restoreSnapshot = {
        entryDetail: entryDetail || null
    };

    _clearNekoIdleCat1QuestionMark(button);
    _setNekoIdleCat1QuestionMarkKeyboardTarget(null);
    _cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });
    _cancelNekoIdleCat1EatAction(button, { restoreArt: false });
    _cancelNekoIdleCat1PlayAction(button, { restoreArt: false });
    _finishNekoIdleReturnDragAction(button, { restoreArt: false });
    _stopNekoIdleCat1AmbientSound();
    _fadeOutNekoIdleCat1DragSound();
    _clearNekoIdleHoverPlayback(button.querySelector('.neko-idle-return-art'));
    _clearNekoIdleThoughtBubble(button);

    container.classList.add('is-cat1-playground-drop');
    _dispatchNekoIdleCat1PlaygroundState(button, true, 'acquire');
    return state;
}

function _releaseNekoIdleCat1PlaygroundDropLifecycle(button, reason) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state) return false;
    if (state.released) return false;
    state.released = true;
    state.releaseReason = reason || 'unknown';
    _stopNekoIdleCat1PlaygroundPhysics(button);
    _clearNekoIdleCat1PlaygroundPointerListeners(button);
    if (state.container) {
        state.container.classList.remove('is-cat1-playground-drop');
        state.container.removeAttribute('data-neko-cat1-playground-dragging');
    }
    state.active = false;
    state.phase = 'inactive';
    state.token += 1;
    state.draggingBodyId = '';
    state.pointerBodyId = '';
    state.pointerId = null;
    state.lastPointerSamples = [];
    state.start = null;
    state.end = null;
    state.entryQuestionBlockElement = null;
    state.suppressClickBodyId = '';
    if (state.suppressClickTimer) {
        clearTimeout(state.suppressClickTimer);
        state.suppressClickTimer = 0;
    }
    state.bodies.clear();
    state.disabledCapabilities.clear();
    const cleanups = state.cleanups.splice(0);
    cleanups.forEach((cleanup) => {
        try { cleanup(); } catch (_) {}
    });
    _dispatchNekoIdleCat1PlaygroundState(button, false, reason || 'release');
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton(button);
    return true;
}

function _releaseAllNekoIdleCat1PlaygroundDropLifecycles(reason) {
    _forEachNekoIdleReturnButton((button) => {
        _cancelNekoIdleCat1PlaygroundPendingEntry(button);
        _clearNekoIdleCat1PlaygroundQuestionBlockClone(button);
        _releaseNekoIdleCat1PlaygroundDropLifecycle(button, reason || 'page-destroy');
    });
}

function _isNekoIdleCat1PlaygroundDropActive(button) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    return !!(state && state.active && !state.released);
}

function _isNekoIdleCat1PlaygroundEntryPending(button) {
    return !!(button && button.__nekoIdleCat1PlaygroundPendingEntry);
}

function _isNekoIdleCat1PlaygroundEntryOrDropActive(button, capability) {
    return _isNekoIdleCat1PlaygroundEntryPending(button) ||
        _isNekoIdleCat1PlaygroundCapabilityBlocked(button, capability);
}

function _isAnyNekoIdleCat1PlaygroundDropActive() {
    let active = false;
    _forEachNekoIdleReturnButton((button) => {
        if (active) return;
        active = _isNekoIdleCat1PlaygroundEntryOrDropActive(button);
    });
    return active;
}

function _isAnyNekoIdleCat1PlaygroundDropLifecycleActive() {
    let active = false;
    _forEachNekoIdleReturnButton((button) => {
        if (active) return;
        active = _isNekoIdleCat1PlaygroundDropActive(button);
    });
    return active;
}

function _isNekoIdleCat1PlaygroundPairMoveFeedback(detail) {
    if (!detail || typeof detail !== 'object') return false;
    return detail.reason === _NEKO_IDLE_CAT1_PLAYGROUND_PAIR_MOVE_SOURCE ||
        detail.source === _NEKO_IDLE_CAT1_PLAYGROUND_PAIR_MOVE_SOURCE;
}

function _isNekoIdleCat1PlaygroundCapabilityBlocked(button, capability) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || !state.active || state.released) return false;
    if (!capability) return true;
    return state.disabledCapabilities.has(capability) || ![
        'playground-physics',
        'playground-drag',
        'cat-click-exit',
        'release-cleanup'
    ].includes(capability);
}

function _registerNekoIdleCat1PlaygroundCleanup(button, cleanup) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || typeof cleanup !== 'function') return;
    state.cleanups.push(cleanup);
}

function _getNekoIdleCat1PlaygroundEntryButton() {
    let selected = null;
    _forEachNekoIdleReturnButton((button) => {
        if (selected) return;
        if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) === _NEKO_IDLE_TIER_CAT1) {
            selected = button;
        }
    });
    return selected;
}

function _requestNekoIdleCat1PlaygroundYarnTarget(detail) {
    if (_getNekoIdleCat1PairMoveChatTarget()) return false;
    const timestamp = Date.now();
    const requestDetail = {
        reason: 'cat1-playground-entry',
        source: detail && detail.source ? detail.source : 'cat1-playground',
        trigger: detail && detail.trigger ? detail.trigger : 'cat1-question-mark',
        timestamp
    };
    let dispatched = false;
    try {
        window.dispatchEvent(new CustomEvent('neko:idle-cat1-playground-yarn-request', {
            detail: requestDetail
        }));
        dispatched = true;
    } catch (_) {
        // Keep the desktop broadcast path available even if a local listener fails.
    }
    const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
    if (channel && typeof channel.postMessage === 'function') {
        try {
            channel.postMessage(Object.assign({
                action: 'idle_cat1_playground_yarn_request',
                lanlan_name: _getNekoIdleCurrentLanlanName()
            }, requestDetail));
            dispatched = true;
        } catch (error) {
            try {
                console.warn('[NekoIdleCat1] playground yarn request postMessage failed:', error && error.message ? error.message : error);
            } catch (_) {}
        }
    }
    return dispatched;
}

function _cancelNekoIdleCat1PlaygroundPendingEntry(button) {
    const pending = button && button.__nekoIdleCat1PlaygroundPendingEntry;
    if (!pending) return false;
    if (pending.frame) {
        try { window.cancelAnimationFrame(pending.frame); } catch (_) {}
    }
    if (pending.timer) {
        clearTimeout(pending.timer);
    }
    button.__nekoIdleCat1PlaygroundPendingEntry = null;
    return true;
}

function _getNekoIdleCat1PlaygroundBodyRectFromElement(element) {
    if (!element || typeof element.getBoundingClientRect !== 'function') return null;
    const rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    return rect;
}

function _normalizeNekoIdleCat1PlaygroundBodyMass(mass) {
    const normalized = Number(mass);
    if (!Number.isFinite(normalized) || normalized <= 0) {
        return _NEKO_IDLE_CAT1_PLAYGROUND_DEFAULT_BODY_MASS;
    }
    return Math.max(
        _NEKO_IDLE_CAT1_PLAYGROUND_MIN_BODY_MASS,
        Math.min(normalized, _NEKO_IDLE_CAT1_PLAYGROUND_MAX_BODY_MASS)
    );
}

function _normalizeNekoIdleCat1PlaygroundInsetRatio(ratio) {
    const normalized = Number(ratio);
    if (!Number.isFinite(normalized) || normalized <= 0) return 0;
    return Math.min(normalized, 0.45);
}

function _normalizeNekoIdleCat1PlaygroundVisibleInsetRatios(ratios) {
    return {
        left: _normalizeNekoIdleCat1PlaygroundInsetRatio(ratios && ratios.left),
        top: _normalizeNekoIdleCat1PlaygroundInsetRatio(ratios && ratios.top),
        right: _normalizeNekoIdleCat1PlaygroundInsetRatio(ratios && ratios.right),
        bottom: _normalizeNekoIdleCat1PlaygroundInsetRatio(ratios && ratios.bottom)
    };
}

function _getNekoIdleCat1PlaygroundBodyVisibleInsetsPx(body) {
    const ratios = _normalizeNekoIdleCat1PlaygroundVisibleInsetRatios(body && body.visibleInsetRatios);
    const width = Number(body && body.width);
    const height = Number(body && body.height);
    return {
        left: Number.isFinite(width) && width > 0 ? width * ratios.left : 0,
        top: Number.isFinite(height) && height > 0 ? height * ratios.top : 0,
        right: Number.isFinite(width) && width > 0 ? width * ratios.right : 0,
        bottom: Number.isFinite(height) && height > 0 ? height * ratios.bottom : 0
    };
}

function _getNekoIdleCat1PlaygroundBodyCollisionRect(body) {
    if (!body) return null;
    const insets = _getNekoIdleCat1PlaygroundBodyVisibleInsetsPx(body);
    const left = body.x + insets.left;
    const top = body.y + insets.top;
    const right = body.x + body.width - insets.right;
    const bottom = body.y + body.height - insets.bottom;
    if (right <= left || bottom <= top) {
        return {
            left: body.x,
            top: body.y,
            right: body.x + body.width,
            bottom: body.y + body.height,
            width: body.width,
            height: body.height
        };
    }
    return {
        left: left,
        top: top,
        right: right,
        bottom: bottom,
        width: right - left,
        height: bottom - top
    };
}

function _getNekoIdleCat1PlaygroundWindowBottomPx() {
    const windowBottom = Number(window.innerHeight);
    return Number.isFinite(windowBottom) && windowBottom > 0 ? windowBottom : 0;
}

function _getNekoIdleCat1PlaygroundViewportBottomPx() {
    const windowBottom = _getNekoIdleCat1PlaygroundWindowBottomPx();
    const cachedBottom = Number(_nekoIdleCat1PlaygroundViewportBottomPx);
    if (Number.isFinite(cachedBottom) && cachedBottom > 0) {
        return Math.min(windowBottom, cachedBottom);
    }
    return windowBottom;
}

function _applyNekoIdleCat1PlaygroundCurrentDisplayBottom(currentDisplay) {
    const windowBottom = _getNekoIdleCat1PlaygroundWindowBottomPx();
    const workAreaHeight = Number(currentDisplay && currentDisplay.workArea && currentDisplay.workArea.height);
    if (Number.isFinite(workAreaHeight) && workAreaHeight > 0) {
        _nekoIdleCat1PlaygroundViewportBottomPx = Math.min(windowBottom, workAreaHeight);
        return _nekoIdleCat1PlaygroundViewportBottomPx;
    }
    const displayHeight = Number(
        (currentDisplay && currentDisplay.height) ||
        (currentDisplay && currentDisplay.bounds && currentDisplay.bounds.height)
    );
    _nekoIdleCat1PlaygroundViewportBottomPx = Number.isFinite(displayHeight) && displayHeight > 0
        ? Math.min(windowBottom, displayHeight)
        : windowBottom;
    return _nekoIdleCat1PlaygroundViewportBottomPx;
}

function _refreshNekoIdleCat1PlaygroundViewportBottom(button) {
    const electronScreen = window.electronScreen;
    if (!electronScreen || typeof electronScreen.getCurrentDisplay !== 'function') {
        _nekoIdleCat1PlaygroundViewportBottomPx = null;
        return Promise.resolve(_getNekoIdleCat1PlaygroundViewportBottomPx());
    }
    const seq = ++_nekoIdleCat1PlaygroundViewportBottomRefreshSeq;
    return Promise.resolve()
        .then(() => electronScreen.getCurrentDisplay())
        .then((currentDisplay) => {
            if (seq !== _nekoIdleCat1PlaygroundViewportBottomRefreshSeq) {
                return _getNekoIdleCat1PlaygroundViewportBottomPx();
            }
            const viewportBottom = _applyNekoIdleCat1PlaygroundCurrentDisplayBottom(currentDisplay);
            if (_isNekoIdleCat1PlaygroundDropActive(button)) {
                const state = button && button.__nekoIdleCat1PlaygroundDropState;
                if (state && state.bodies) {
                    state.bodies.forEach(_updateNekoIdleCat1PlaygroundBodyBounds);
                }
                _startNekoIdleCat1PlaygroundPhysics(button);
            }
            return viewportBottom;
        })
        .catch(() => {
            if (seq === _nekoIdleCat1PlaygroundViewportBottomRefreshSeq) {
                _nekoIdleCat1PlaygroundViewportBottomPx = null;
            }
            return _getNekoIdleCat1PlaygroundViewportBottomPx();
        });
}

function _createNekoIdleCat1PlaygroundPhysicsBody(id, element, options = {}) {
    const rect = options.rect || _getNekoIdleCat1PlaygroundBodyRectFromElement(element);
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    const width = Number(rect.width);
    const height = Number(rect.height);
    const mass = _normalizeNekoIdleCat1PlaygroundBodyMass(options.mass);
    const body = {
        id: id,
        element: element || null,
        desktop: !!options.desktop,
        mass: mass,
        inverseMass: 1 / mass,
        visibleInsetRatios: _normalizeNekoIdleCat1PlaygroundVisibleInsetRatios(options.visibleInsetRatios),
        x: Number(rect.left) || 0,
        y: Number(rect.top) || 0,
        width: width,
        height: height,
        vx: Number(options.vx) || 0,
        vy: Number(options.vy) || 0,
        rotationEnabled: !!options.rotationEnabled,
        rotation: Number(options.rotation) || 0,
        angularVelocity: Number(options.angularVelocity) || 0,
        angularDamping: Number(options.angularDamping) || _NEKO_IDLE_CAT1_PLAYGROUND_DEFAULT_ANGULAR_DAMPING,
        angularGroundDamping: Number(options.angularGroundDamping) || _NEKO_IDLE_CAT1_PLAYGROUND_DEFAULT_GROUND_ANGULAR_DAMPING,
        settleRotationWhenGrounded: !!options.settleRotationWhenGrounded,
        restRotationStepRad: Math.max(0, Number(options.restRotationStepRad) || 0),
        restRotationOffsetRad: Number(options.restRotationOffsetRad) || 0,
        rotationSettling: false,
        rotationSettleTarget: Number(options.rotationSettleTarget) || 0,
        rotationSettleSpeed: Number(options.rotationSettleSpeed) || _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_SETTLE_SPEED_RAD_PER_SEC,
        dragging: false,
        grounded: false,
        floorY: Math.max(0, _getNekoIdleCat1PlaygroundViewportBottomPx() - height),
        wallLeft: 0,
        wallRight: Math.max(0, window.innerWidth - width),
        screenOffsetX: Number.isFinite(Number(window.screenX)) ? Number(window.screenX) : 0,
        screenOffsetY: Number.isFinite(Number(window.screenY)) ? Number(window.screenY) : 0
    };
    _updateNekoIdleCat1PlaygroundBodyBounds(body);
    return body;
}

function _createNekoIdleCat1PlaygroundDesktopYarnMirror(rect) {
    if (!rect || typeof document === 'undefined' || !document.body) return null;
    const width = Math.max(1, Math.round(Number(rect.width) || 0));
    const height = Math.max(1, Math.round(Number(rect.height) || 0));
    if (!width || !height) return null;
    const mirror = document.createElement('button');
    mirror.type = 'button';
    mirror.className = 'neko-idle-cat1-playground-desktop-yarn-mirror';
    mirror.setAttribute('aria-label', '最小化聊天框');
    Object.assign(mirror.style, {
        position: 'fixed',
        left: `${Math.round(Number(rect.left) || 0)}px`,
        top: `${Math.round(Number(rect.top) || 0)}px`,
        width: `${width}px`,
        height: `${height}px`,
        padding: '0',
        border: '0',
        borderRadius: '0',
        backgroundColor: 'transparent',
        backgroundImage: `url("${_NEKO_IDLE_CAT1_PLAYGROUND_YARN_ASSET_URL}${_getNekoIdleReturnAssetVersionSuffix()}")`,
        backgroundPosition: 'center center',
        backgroundRepeat: 'no-repeat',
        backgroundSize: `${width}px ${height}px`,
        boxShadow: 'none',
        cursor: 'grab',
        pointerEvents: 'auto',
        touchAction: 'none',
        userSelect: 'none',
        webkitUserSelect: 'none',
        zIndex: _NEKO_IDLE_RETURN_COMPACT_SURFACE_Z_INDEX
    });
    document.body.appendChild(mirror);
    return mirror;
}

function _createNekoIdleCat1PlaygroundQuestionBlockClone(rect, button) {
    if (!rect || typeof document === 'undefined' || !document.body) return null;
    const width = Math.max(1, Math.round(Number(rect.width) || 0));
    const height = Math.max(1, Math.round(Number(rect.height) || 0));
    if (!width || !height) return null;
    const clone = document.createElement('button');
    clone.type = 'button';
    clone.className = 'neko-idle-cat1-playground-question-block';
    clone.setAttribute('aria-label', '问号方块');
    Object.assign(clone.style, {
        position: 'fixed',
        left: `${Math.round(Number(rect.left) || 0)}px`,
        top: `${Math.round(Number(rect.top) || 0)}px`,
        width: `${width}px`,
        height: `${height}px`,
        padding: '0',
        border: '0',
        borderRadius: '0',
        backgroundColor: 'transparent',
        backgroundImage: `url("${_getNekoIdleCat1QuestionMarkAssetUrl()}")`,
        backgroundPosition: 'center center',
        backgroundRepeat: 'no-repeat',
        backgroundSize: 'contain',
        boxShadow: 'none',
        cursor: 'grab',
        pointerEvents: 'auto',
        touchAction: 'none',
        userSelect: 'none',
        webkitUserSelect: 'none',
        zIndex: _NEKO_IDLE_RETURN_COMPACT_SURFACE_Z_INDEX
    });
    clone.addEventListener('click', (event) => {
        _handleNekoIdleCat1PlaygroundQuestionBlockCloneClick(button, clone, event);
    }, { capture: true });
    document.body.appendChild(clone);
    return clone;
}

function _captureNekoIdleCat1PlaygroundStartPositions(state) {
    const snapshot = { bodies: {} };
    if (!state || !state.bodies || typeof state.bodies.forEach !== 'function') return snapshot;
    state.bodies.forEach((body) => {
        if (!body || !['cat', 'yarn', 'desktop-yarn'].includes(body.id)) return;
        snapshot.bodies[body.id] = {
            x: Number(body.x) || 0,
            y: Number(body.y) || 0
        };
    });
    return snapshot;
}

function _registerNekoIdleCat1PlaygroundPhysicsBodies(button) {
    const state = _getNekoIdleCat1PlaygroundDropState(button);
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!state || !container) return false;
    state.bodies.clear();
    state.container = container;
    state.targetMode = 'none';
    state.targetElement = null;
    state.targetScreenRect = null;

    const body = _createNekoIdleCat1PlaygroundPhysicsBody('cat', container, {
        id: 'cat',
        mass: _NEKO_IDLE_CAT1_PLAYGROUND_CAT_BODY_MASS,
        visibleInsetRatios: _NEKO_IDLE_CAT1_PLAYGROUND_CAT_VISIBLE_INSET_RATIOS
    });
    if (body) state.bodies.set(body.id, body);

    const target = _getNekoIdleCat1PairMoveChatTarget();
    if (target && target.mode === 'dom' && target.shell) {
        const body = _createNekoIdleCat1PlaygroundPhysicsBody('yarn', target.shell, {
            id: 'yarn',
            rect: target.rect,
            mass: _NEKO_IDLE_CAT1_PLAYGROUND_YARN_BODY_MASS,
            visibleInsetRatios: _NEKO_IDLE_CAT1_PLAYGROUND_YARN_VISIBLE_INSET_RATIOS
        });
        if (body) {
            state.targetMode = 'dom';
            state.targetElement = target.shell;
            state.bodies.set(body.id, body);
        }
    } else if (target && target.mode === 'desktop') {
        const mirror = _createNekoIdleCat1PlaygroundDesktopYarnMirror(target.rect);
        const body = _createNekoIdleCat1PlaygroundPhysicsBody('desktop-yarn', mirror, {
            id: 'desktop-yarn',
            rect: target.rect,
            desktop: true,
            mass: _NEKO_IDLE_CAT1_PLAYGROUND_YARN_BODY_MASS,
            visibleInsetRatios: _NEKO_IDLE_CAT1_PLAYGROUND_YARN_VISIBLE_INSET_RATIOS
        });
        if (body) {
            state.targetMode = 'desktop';
            state.targetElement = mirror;
            state.targetScreenRect = target.screenRect;
            state.bodies.set(body.id, body);
            _registerNekoIdleCat1PlaygroundCleanup(button, () => {
                if (mirror && mirror.parentNode) {
                    try { mirror.parentNode.removeChild(mirror); } catch (_) {}
                }
            });
        } else if (mirror && mirror.parentNode) {
            try { mirror.parentNode.removeChild(mirror); } catch (_) {}
        }
    }

    const questionBlockElement = state.entryQuestionBlockElement && state.entryQuestionBlockElement.isConnected
        ? state.entryQuestionBlockElement
        : null;
    if (questionBlockElement) {
        const body = _createNekoIdleCat1PlaygroundPhysicsBody('question-block', questionBlockElement, {
            id: 'question-block',
            mass: _NEKO_IDLE_CAT1_PLAYGROUND_QUESTION_BLOCK_BODY_MASS,
            rotationEnabled: true,
            angularDamping: _NEKO_IDLE_CAT1_PLAYGROUND_DEFAULT_ANGULAR_DAMPING,
            angularGroundDamping: _NEKO_IDLE_CAT1_PLAYGROUND_DEFAULT_GROUND_ANGULAR_DAMPING,
            settleRotationWhenGrounded: true,
            restRotationStepRad: Math.PI / 2,
            restRotationOffsetRad: 0,
            visibleInsetRatios: _NEKO_IDLE_CAT1_PLAYGROUND_QUESTION_BLOCK_VISIBLE_INSET_RATIOS
        });
        if (body) {
            state.bodies.set(body.id, body);
            _registerNekoIdleCat1PlaygroundCleanup(button, () => {
                if (questionBlockElement && questionBlockElement.parentNode) {
                    try { questionBlockElement.parentNode.removeChild(questionBlockElement); } catch (_) {}
                }
            });
        } else if (questionBlockElement.parentNode) {
            try { questionBlockElement.parentNode.removeChild(questionBlockElement); } catch (_) {}
        }
    }
    state.start = _captureNekoIdleCat1PlaygroundStartPositions(state);
    return state.bodies.size > 0;
}

function _setNekoIdleCat1PlaygroundBodyPosition(body, left, top, options = {}) {
    if (!body) return;
    body.x = Number(left) || 0;
    body.y = Number(top) || 0;
    if (body.id === 'cat') {
        _setNekoIdleCat1ContainerPosition(body.element, body.x, body.y);
    } else if (body.desktop) {
        if (body.element) {
            _setNekoIdleCat1PairMoveChatPosition(body.element, body.x, body.y);
        }
        _dispatchNekoIdleDesktopChatPairMoveBounds({
            left: body.screenOffsetX + body.x,
            top: body.screenOffsetY + body.y,
            width: body.width,
            height: body.height
        }, {
            force: !!options.force,
            reason: _NEKO_IDLE_CAT1_PLAYGROUND_PAIR_MOVE_SOURCE,
            source: _NEKO_IDLE_CAT1_PLAYGROUND_PAIR_MOVE_SOURCE
        });
    } else if (body.id === 'yarn') {
        _setNekoIdleCat1PairMoveChatPosition(body.element, body.x, body.y);
    } else {
        _setNekoIdleCat1PlaygroundFixedBodyPosition(body.element, body.x, body.y);
    }
    _applyNekoIdleCat1PlaygroundBodyRotation(body);
}

function _setNekoIdleCat1PlaygroundFixedBodyPosition(element, left, top) {
    if (!element) return;
    element.style.left = `${Math.round(left)}px`;
    element.style.top = `${Math.round(top)}px`;
    element.style.right = '';
    element.style.bottom = '';
}

function _applyNekoIdleCat1PlaygroundBodyRotation(body) {
    if (!body || !body.rotationEnabled || !body.element || body.desktop) return;
    body.element.style.transformOrigin = 'center center';
    body.element.style.transform = `rotate(${Number(body.rotation) || 0}rad)`;
}

function _updateNekoIdleCat1PlaygroundBodyBounds(body) {
    if (!body) return;
    const insets = _getNekoIdleCat1PlaygroundBodyVisibleInsetsPx(body);
    body.floorY = Math.max(0, _getNekoIdleCat1PlaygroundViewportBottomPx() - body.height + insets.bottom);
    body.wallLeft = -insets.left;
    body.wallRight = Math.max(body.wallLeft, window.innerWidth - body.width + insets.right);
}

function _clampNekoIdleCat1PlaygroundBodyToBounds(body) {
    if (!body) return;
    _updateNekoIdleCat1PlaygroundBodyBounds(body);
    body.x = Math.max(body.wallLeft, Math.min(body.x, body.wallRight));
    body.y = Math.min(body.y, body.floorY);
    body.grounded = body.y >= body.floorY - 0.5;
}

function _reclampNekoIdleCat1PlaygroundBodyAfterBoundsChange(body) {
    if (!body) return;
    const wasGrounded = !!body.grounded;
    _updateNekoIdleCat1PlaygroundBodyBounds(body);
    body.x = Math.max(body.wallLeft, Math.min(body.x, body.wallRight));
    if (wasGrounded) {
        body.y = body.floorY;
        body.vy = 0;
        body.grounded = true;
    } else {
        body.y = Math.min(body.y, body.floorY);
        body.grounded = body.y >= body.floorY - 0.5;
    }
    _setNekoIdleCat1PlaygroundBodyPosition(body, body.x, body.y, { force: body.desktop });
}

function _resolveNekoIdleCat1PlaygroundBodyCollisionPair(state, first, second) {
    if (!state || !first || !second || first === second) return false;
    const firstRect = _getNekoIdleCat1PlaygroundBodyCollisionRect(first);
    const secondRect = _getNekoIdleCat1PlaygroundBodyCollisionRect(second);
    if (!firstRect || !secondRect) return false;
    const overlapX = Math.min(firstRect.right, secondRect.right) - Math.max(firstRect.left, secondRect.left);
    const overlapY = Math.min(firstRect.bottom, secondRect.bottom) - Math.max(firstRect.top, secondRect.top);
    if (overlapX <= 0 || overlapY <= 0) return false;

    const firstCanMove = !first.dragging;
    const secondCanMove = !second.dragging;
    if (!firstCanMove && !secondCanMove) return false;

    const firstInverseMass = firstCanMove ? (Number(first.inverseMass) || 1) : 0;
    const secondInverseMass = secondCanMove ? (Number(second.inverseMass) || 1) : 0;
    const totalInverseMass = firstInverseMass + secondInverseMass;
    if (totalInverseMass <= 0) return false;
    const firstMass = _normalizeNekoIdleCat1PlaygroundBodyMass(first.mass);
    const secondMass = _normalizeNekoIdleCat1PlaygroundBodyMass(second.mass);
    const totalMass = firstMass + secondMass;
    const getDraggedPushRatio = (pusherMass, pushedMass) => {
        const draggedPushRatio = Math.min(1, pusherMass / pushedMass);
        return Number.isFinite(draggedPushRatio) && draggedPushRatio > 0 ? draggedPushRatio : 1;
    };
    const firstShare = firstCanMove && secondCanMove
        ? firstInverseMass / totalInverseMass
        : (firstCanMove ? getDraggedPushRatio(secondMass, firstMass) : 0);
    const secondShare = firstCanMove && secondCanMove
        ? secondInverseMass / totalInverseMass
        : (secondCanMove ? getDraggedPushRatio(firstMass, secondMass) : 0);
    const firstCenterX = firstRect.left + firstRect.width / 2;
    const secondCenterX = secondRect.left + secondRect.width / 2;
    const firstCenterY = firstRect.top + firstRect.height / 2;
    const secondCenterY = secondRect.top + secondRect.height / 2;

    if (overlapX <= overlapY) {
        const direction = firstCenterX <= secondCenterX ? -1 : 1;
        if (firstCanMove) first.x += direction * overlapX * firstShare;
        if (secondCanMove) second.x -= direction * overlapX * secondShare;
        const firstVx = first.vx;
        const secondVx = second.vx;
        if (firstCanMove && secondCanMove) {
            first.vx = ((firstMass * firstVx + secondMass * secondVx) -
                secondMass * _NEKO_IDLE_CAT1_PLAYGROUND_BODY_RESTITUTION * (firstVx - secondVx)) / totalMass;
            second.vx = ((firstMass * firstVx + secondMass * secondVx) +
                firstMass * _NEKO_IDLE_CAT1_PLAYGROUND_BODY_RESTITUTION * (firstVx - secondVx)) / totalMass;
        } else if (firstCanMove) {
            first.vx = direction *
                _NEKO_IDLE_CAT1_PLAYGROUND_BODY_PUSH_VELOCITY_PX_PER_SEC *
                first.inverseMass *
                getDraggedPushRatio(secondMass, firstMass);
        } else if (secondCanMove) {
            second.vx = -direction *
                _NEKO_IDLE_CAT1_PLAYGROUND_BODY_PUSH_VELOCITY_PX_PER_SEC *
                second.inverseMass *
                getDraggedPushRatio(firstMass, secondMass);
        }
    } else {
        const direction = firstCenterY <= secondCenterY ? -1 : 1;
        if (firstCanMove) first.y += direction * overlapY * firstShare;
        if (secondCanMove) second.y -= direction * overlapY * secondShare;
        const firstVy = first.vy;
        const secondVy = second.vy;
        if (firstCanMove && secondCanMove) {
            first.vy = ((firstMass * firstVy + secondMass * secondVy) -
                secondMass * _NEKO_IDLE_CAT1_PLAYGROUND_BODY_RESTITUTION * (firstVy - secondVy)) / totalMass;
            second.vy = ((firstMass * firstVy + secondMass * secondVy) +
                firstMass * _NEKO_IDLE_CAT1_PLAYGROUND_BODY_RESTITUTION * (firstVy - secondVy)) / totalMass;
        } else if (firstCanMove) {
            first.vy = direction *
                _NEKO_IDLE_CAT1_PLAYGROUND_BODY_PUSH_VELOCITY_PX_PER_SEC *
                first.inverseMass *
                getDraggedPushRatio(secondMass, firstMass);
        } else if (secondCanMove) {
            second.vy = -direction *
                _NEKO_IDLE_CAT1_PLAYGROUND_BODY_PUSH_VELOCITY_PX_PER_SEC *
                second.inverseMass *
                getDraggedPushRatio(firstMass, secondMass);
        }
    }

    [first, second].forEach((body) => {
        _clampNekoIdleCat1PlaygroundBodyToBounds(body);
        _setNekoIdleCat1PlaygroundBodyPosition(body, body.x, body.y, { force: body.desktop });
    });
    return true;
}

function _resolveNekoIdleCat1PlaygroundBodyCollisions(state) {
    if (!state || !state.bodies || state.bodies.size < 2) return false;
    const bodies = Array.from(state.bodies.values()).filter((body) => !!body);
    let resolved = false;
    for (let i = 0; i < bodies.length; i += 1) {
        for (let j = i + 1; j < bodies.length; j += 1) {
            resolved = _resolveNekoIdleCat1PlaygroundBodyCollisionPair(state, bodies[i], bodies[j]) || resolved;
        }
    }
    return resolved;
}

function _isNekoIdleCat1PlaygroundBodyRotating(body) {
    return !!(body && body.rotationEnabled &&
        Math.abs(Number(body.angularVelocity) || 0) > _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_STOP_RAD_PER_SEC);
}

function _isNekoIdleCat1PlaygroundBodySettlingRotation(body) {
    return !!(body && body.rotationEnabled && body.rotationSettling);
}

function _isNekoIdleCat1PlaygroundBodyRestRotationPending(body) {
    if (!_shouldNekoIdleCat1PlaygroundBodySettleRotation(body)) return false;
    if (body.rotationSettling) return true;
    const target = _getNekoIdleCat1PlaygroundNearestRestRotation(body);
    return Math.abs((Number(body.rotation) || 0) - target) > _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_SETTLE_EPSILON_RAD ||
        Math.abs(Number(body.angularVelocity) || 0) > _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_STOP_RAD_PER_SEC;
}

function _clampNekoIdleCat1PlaygroundAngularVelocity(value) {
    const velocity = Number(value) || 0;
    return Math.max(
        -_NEKO_IDLE_CAT1_PLAYGROUND_MAX_ANGULAR_VELOCITY_RAD_PER_SEC,
        Math.min(velocity, _NEKO_IDLE_CAT1_PLAYGROUND_MAX_ANGULAR_VELOCITY_RAD_PER_SEC)
    );
}

function _getNekoIdleCat1PlaygroundThrowAngularVelocity(body, velocity, state) {
    if (!body || !body.rotationEnabled || !velocity || !state) return 0;
    const width = Math.max(1, Number(body.width) || 1);
    const height = Math.max(1, Number(body.height) || 1);
    const pointerOffsetX = Number(state.pointerOffsetX);
    const pointerOffsetY = Number(state.pointerOffsetY);
    const rx = (Number.isFinite(pointerOffsetX) ? pointerOffsetX : width / 2) - width / 2;
    const ry = (Number.isFinite(pointerOffsetY) ? pointerOffsetY : height / 2) - height / 2;
    const radiusSquared = (rx * rx) + (ry * ry);
    const safeRadiusSquared = Math.max(radiusSquared, Math.pow(Math.max(width, height), 2) * 0.16);
    let angularVelocity = ((rx * velocity.vy) - (ry * velocity.vx)) / safeRadiusSquared;
    if (Math.abs(angularVelocity) < 0.25) {
        angularVelocity = velocity.vx / Math.max(width, height) * 0.65;
    }
    return _clampNekoIdleCat1PlaygroundAngularVelocity(angularVelocity);
}

function _getNekoIdleCat1PlaygroundNearestRestRotation(body) {
    const rotation = Number(body && body.rotation) || 0;
    const step = Math.abs(Number(body && body.restRotationStepRad) || 0);
    if (step <= 0) return rotation;
    const offset = Number(body && body.restRotationOffsetRad) || 0;
    return Math.round((rotation - offset) / step) * step + offset;
}

function _shouldNekoIdleCat1PlaygroundBodySettleRotation(body) {
    return !!(body && body.rotationEnabled &&
        body.settleRotationWhenGrounded &&
        Math.abs(Number(body.restRotationStepRad) || 0) > 0 &&
        body.grounded &&
        !body.dragging);
}

function _stepNekoIdleCat1PlaygroundBodyRestRotation(body, dt) {
    if (!body || !body.rotationEnabled) return false;
    const shouldSettle = _shouldNekoIdleCat1PlaygroundBodySettleRotation(body);
    if (!shouldSettle) {
        if (body.rotationSettling && (!body.grounded || body.dragging)) {
            body.rotationSettling = false;
        }
        return false;
    }
    if (!body.rotationSettling) {
        body.rotationSettleTarget = _getNekoIdleCat1PlaygroundNearestRestRotation(body);
        if (Math.abs((Number(body.rotation) || 0) - body.rotationSettleTarget) <= _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_SETTLE_EPSILON_RAD &&
            Math.abs(Number(body.angularVelocity) || 0) <= _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_STOP_RAD_PER_SEC) {
            body.rotation = body.rotationSettleTarget;
            body.angularVelocity = 0;
            _applyNekoIdleCat1PlaygroundBodyRotation(body);
            return false;
        }
        body.rotationSettling = true;
    }

    const current = Number(body.rotation) || 0;
    const target = Number(body.rotationSettleTarget) || 0;
    const delta = target - current;
    const settleSpeed = Math.max(0.001, Number(body.rotationSettleSpeed) ||
        _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_SETTLE_SPEED_RAD_PER_SEC);
    const damping = Math.exp(-settleSpeed * 0.9 * Math.max(0, dt));
    body.angularVelocity = _clampNekoIdleCat1PlaygroundAngularVelocity(
        ((Number(body.angularVelocity) || 0) + (delta * settleSpeed * settleSpeed * dt)) * damping
    );
    body.rotation = current + ((Number(body.angularVelocity) || 0) * dt);

    if (Math.abs(delta) <= _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_SETTLE_EPSILON_RAD &&
        Math.abs(Number(body.angularVelocity) || 0) <= _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_STOP_RAD_PER_SEC) {
        body.rotation = target;
        body.angularVelocity = 0;
        body.rotationSettling = false;
        _applyNekoIdleCat1PlaygroundBodyRotation(body);
        return false;
    }
    _applyNekoIdleCat1PlaygroundBodyRotation(body);
    return true;
}

function _stepNekoIdleCat1PlaygroundBodyRotation(body, dt) {
    if (!body || !body.rotationEnabled) return false;
    if (body.rotationSettling) return false;
    if (!_isNekoIdleCat1PlaygroundBodyRotating(body)) {
        body.angularVelocity = 0;
        return false;
    }
    body.rotation = (Number(body.rotation) || 0) + ((Number(body.angularVelocity) || 0) * dt);
    const baseDamping = Number(body.angularDamping) || _NEKO_IDLE_CAT1_PLAYGROUND_DEFAULT_ANGULAR_DAMPING;
    const groundDamping = Number(body.angularGroundDamping) || _NEKO_IDLE_CAT1_PLAYGROUND_DEFAULT_GROUND_ANGULAR_DAMPING;
    const damping = body.grounded
        ? Math.min(baseDamping, groundDamping)
        : baseDamping;
    body.angularVelocity *= Math.pow(damping, Math.max(1, dt * 60));
    if (!_isNekoIdleCat1PlaygroundBodyRotating(body)) {
        body.angularVelocity = 0;
    }
    _applyNekoIdleCat1PlaygroundBodyRotation(body);
    return true;
}

function _setNekoIdleCat1PlaygroundCatAirArt(button) {
    const art = button && button.querySelector('.neko-idle-return-art');
    if (!art) return;
    _setNekoIdleReturnArtSource(art, _NEKO_IDLE_CAT1_PLAYGROUND_AIR_ASSET_URL, _NEKO_IDLE_TIER_CAT1, { animate: false });
}

function _setNekoIdleCat1PlaygroundCatGroundedArt(button) {
    const art = button && button.querySelector('.neko-idle-return-art');
    if (!art) return;
    _setNekoIdleReturnArtSource(art, _getNekoIdleReturnAssetUrl(_NEKO_IDLE_TIER_CAT1), _NEKO_IDLE_TIER_CAT1, { animate: false });
}

function _stepNekoIdleCat1PlaygroundPhysics(button, now) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || !state.active || state.released) return;
    const token = state.token;
    if (!state.lastTickAt) state.lastTickAt = now;
    const dt = Math.min(now - state.lastTickAt, state.maxDeltaMs) / 1000;
    state.lastTickAt = now;
    let needsNextFrame = false;
    let allGrounded = true;

    state.bodies.forEach((body) => {
        if (!body || body.dragging) {
            needsNextFrame = true;
            allGrounded = false;
            return;
        }
        _updateNekoIdleCat1PlaygroundBodyBounds(body);
        if (!_shouldNekoIdleCat1PlaygroundBodySettleRotation(body)) {
            _stepNekoIdleCat1PlaygroundBodyRotation(body, dt);
        }
        const linearActive = !body.grounded || Math.abs(body.vx) > 0.05 || Math.abs(body.vy) > 0.05;
        if (linearActive) {
            if (body.grounded &&
                Math.abs(body.vx) <= _NEKO_IDLE_CAT1_PLAYGROUND_GROUND_STOP_VELOCITY_PX_PER_SEC) {
                body.vx = 0;
                body.vy = 0;
                body.y = body.floorY;
            } else {
                body.vy += state.gravityPxPerSecond2 * dt;
                body.x += body.vx * dt;
                body.y += body.vy * dt;
                body.vx *= body.grounded
                    ? _NEKO_IDLE_CAT1_PLAYGROUND_GROUND_DAMPING
                    : _NEKO_IDLE_CAT1_PLAYGROUND_HORIZONTAL_DAMPING;

                if (body.x <= body.wallLeft) {
                    body.x = body.wallLeft;
                    body.vx = Math.abs(body.vx) * _NEKO_IDLE_CAT1_PLAYGROUND_WALL_RESTITUTION;
                }

                if (body.x >= body.wallRight) {
                    body.x = body.wallRight;
                    body.vx = -Math.abs(body.vx) * _NEKO_IDLE_CAT1_PLAYGROUND_WALL_RESTITUTION;
                }

                if (body.y >= body.floorY) {
                    body.y = body.floorY;
                    body.vy = 0;
                    body.grounded = true;
                    if (Math.abs(body.vx) <= _NEKO_IDLE_CAT1_PLAYGROUND_GROUND_STOP_VELOCITY_PX_PER_SEC) {
                        body.vx = 0;
                    }
                } else {
                    body.grounded = false;
                }
            }
            _setNekoIdleCat1PlaygroundBodyPosition(body, body.x, body.y, { force: body.grounded || body.desktop });
        }
        _stepNekoIdleCat1PlaygroundBodyRestRotation(body, dt);
        if (!body.grounded) allGrounded = false;
        const angularActive = _isNekoIdleCat1PlaygroundBodyRotating(body) ||
            _isNekoIdleCat1PlaygroundBodySettlingRotation(body) ||
            _isNekoIdleCat1PlaygroundBodyRestRotationPending(body);
        if (!body.grounded || Math.abs(body.vx) > 0.5 || Math.abs(body.vy) > 0.5 ||
            angularActive) {
            needsNextFrame = true;
        }
    });

    if (_resolveNekoIdleCat1PlaygroundBodyCollisions(state)) {
        allGrounded = true;
        state.bodies.forEach((body) => {
            if (!body) return;
            if (body.dragging) needsNextFrame = true;
            if (!body.grounded) allGrounded = false;
            if (!body.grounded || Math.abs(body.vx) > 0.5 || Math.abs(body.vy) > 0.5 ||
                _isNekoIdleCat1PlaygroundBodyRotating(body) ||
                _isNekoIdleCat1PlaygroundBodySettlingRotation(body) ||
                _isNekoIdleCat1PlaygroundBodyRestRotationPending(body)) {
                needsNextFrame = true;
            }
        });
    }

    const catBody = state.bodies.get('cat');
    if (catBody && catBody.grounded && state.phase !== 'dragging') {
        const art = button && button.querySelector('.neko-idle-return-art');
        if (art) {
            _setNekoIdleReturnArtSource(art, _getNekoIdleReturnAssetUrl(_NEKO_IDLE_TIER_CAT1), _NEKO_IDLE_TIER_CAT1, { animate: false });
        }
    }
    if (allGrounded && (state.phase === 'dropping' || state.phase === 'ballistic')) {
        state.phase = 'settled';
        if (!state.pointerHandlers || !state.pointerHandlers.length) {
            _installNekoIdleCat1PlaygroundPointerListeners(button);
        }
    }

    if (needsNextFrame) {
        state.frame = window.requestAnimationFrame((timestamp) => {
            if (token !== state.token) return;
            _stepNekoIdleCat1PlaygroundPhysics(button, timestamp);
        });
    } else {
        state.frame = 0;
    }
}

function _startNekoIdleCat1PlaygroundPhysics(button) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || !state.active || state.released || state.frame) return;
    state.lastTickAt = 0;
    const token = state.token;
    state.frame = window.requestAnimationFrame((timestamp) => {
        if (token !== state.token) return;
        _stepNekoIdleCat1PlaygroundPhysics(button, timestamp);
    });
}

function _stopNekoIdleCat1PlaygroundPhysics(button) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || !state.frame) return;
    window.cancelAnimationFrame(state.frame);
    state.frame = 0;
}

function _getNekoIdleCat1PlaygroundPointerVelocity(samples) {
    if (!samples || samples.length < 2) return { vx: 0, vy: 0 };
    const first = samples[0];
    const last = samples[samples.length - 1];
    const elapsed = Math.max(16, (last.timestamp || 0) - (first.timestamp || 0)) / 1000;
    return {
        vx: ((last.x || 0) - (first.x || 0)) / elapsed,
        vy: ((last.y || 0) - (first.y || 0)) / elapsed
    };
}

function _resolveNekoIdleCat1PlaygroundPointerClient(state, body, event) {
    const rawClientX = Number(event && event.clientX);
    const rawClientY = Number(event && event.clientY);
    const fallback = {
        x: Number.isFinite(rawClientX) ? rawClientX : 0,
        y: Number.isFinite(rawClientY) ? rawClientY : 0
    };
    if (!state || !body || !event) return fallback;

    const screenX = Number(event.screenX);
    const screenY = Number(event.screenY);
    const windowScreenX = Number(window.screenX);
    const windowScreenY = Number(window.screenY);
    if (![screenX, screenY, windowScreenX, windowScreenY].every(Number.isFinite)) {
        return fallback;
    }

    const screenClient = {
        x: screenX - windowScreenX,
        y: screenY - windowScreenY
    };
    if (![screenClient.x, screenClient.y].every(Number.isFinite)) return fallback;

    const clientNextX = fallback.x - state.pointerOffsetX;
    const clientNextY = fallback.y - state.pointerOffsetY;
    const screenNextX = screenClient.x - state.pointerOffsetX;
    const screenNextY = screenClient.y - state.pointerOffsetY;
    const clientDistance = Math.hypot(clientNextX - body.x, clientNextY - body.y);
    const screenDistance = Math.hypot(screenNextX - body.x, screenNextY - body.y);

    return screenDistance + 1 < clientDistance ? screenClient : fallback;
}

function _getNekoIdleCat1PlaygroundBodyForEvent(state, event) {
    if (!state || !event) return null;
    const target = event.target;
    let matched = null;
    state.bodies.forEach((body) => {
        if (matched || !body || !body.element) return;
        if (body.element === target || (typeof body.element.contains === 'function' && body.element.contains(target))) {
            matched = body;
        }
    });
    return matched;
}

function _handleNekoIdleCat1PlaygroundPointerDown(button, event) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || !state.active || state.released || !event) return false;
    const body = _getNekoIdleCat1PlaygroundBodyForEvent(state, event);
    if (!body) return false;
    return _handleNekoIdleCat1PlaygroundPointerDownForBody(button, body, event);
}

function _handleNekoIdleCat1PlaygroundPointerDownForBody(button, body, event) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || !state.active || state.released || !body || !event) return false;
    if (event.button !== undefined && event.button !== 0) return false;
    try { event.stopPropagation(); } catch (_) {}
    state.pointerBodyId = body.id;
    state.pointerId = event.pointerId !== undefined ? event.pointerId : null;
    state.suppressClickBodyId = '';
    if (state.suppressClickTimer) {
        clearTimeout(state.suppressClickTimer);
        state.suppressClickTimer = 0;
    }
    state.pointerStartX = event.clientX;
    state.pointerStartY = event.clientY;
    state.pointerMoved = false;
    state.pointerOffsetX = event.clientX - body.x;
    state.pointerOffsetY = event.clientY - body.y;
    if (body.rotationEnabled) {
        body.angularVelocity = 0;
        body.rotationSettling = false;
    }
    state.lastPointerSamples = [{
        x: event.clientX,
        y: event.clientY,
        timestamp: Date.now()
    }];
    if (body.element && typeof body.element.setPointerCapture === 'function' && event.pointerId !== undefined) {
        try { body.element.setPointerCapture(event.pointerId); } catch (_) {}
    }
    return true;
}

function _getNekoIdleCat1PlaygroundActiveDesktopBody() {
    let matchedButton = null;
    let matchedBody = null;
    _forEachNekoIdleReturnButton((button) => {
        if (matchedBody || !_isNekoIdleCat1PlaygroundDropActive(button)) return;
        const state = button && button.__nekoIdleCat1PlaygroundDropState;
        if (!state || !state.bodies) return;
        state.bodies.forEach((body) => {
            if (matchedBody || !body || !body.desktop) return;
            matchedButton = button;
            matchedBody = body;
        });
    });
    return matchedButton && matchedBody ? { button: matchedButton, body: matchedBody } : null;
}

function _handleNekoIdleCat1PlaygroundDesktopPointerEvent(event) {
    const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
    if (!detail) return false;
    const active = _getNekoIdleCat1PlaygroundActiveDesktopBody();
    if (!active || !active.body || !active.body.desktop) return false;
    const button = active.button;
    const body = active.body;
    const screenX = Number(detail.screenX);
    const screenY = Number(detail.screenY);
    if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return false;
    const pointerEvent = {
        type: detail.type || '',
        pointerId: detail.pointerId === null || detail.pointerId === undefined ? null : Number(detail.pointerId),
        button: Number.isFinite(Number(detail.button)) ? Number(detail.button) : 0,
        buttons: Number.isFinite(Number(detail.buttons)) ? Number(detail.buttons) : 0,
        screenX: screenX,
        screenY: screenY,
        clientX: Number(detail.screenX) - (Number(window.screenX) || 0),
        clientY: Number(detail.screenY) - (Number(window.screenY) || 0),
        preventDefault() {},
        stopPropagation() {},
        stopImmediatePropagation() {}
    };
    if (pointerEvent.type === 'pointerdown') {
        return _handleNekoIdleCat1PlaygroundPointerDownForBody(button, body, pointerEvent);
    }
    if (pointerEvent.type === 'pointermove') {
        return _handleNekoIdleCat1PlaygroundPointerMove(button, pointerEvent);
    }
    if (pointerEvent.type === 'pointerup' || pointerEvent.type === 'pointercancel') {
        return _handleNekoIdleCat1PlaygroundPointerUp(button, pointerEvent);
    }
    return false;
}

function _handleNekoIdleCat1PlaygroundPointerMove(button, event) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || !state.active || state.released || !state.pointerBodyId || !event) return false;
    const body = state.bodies.get(state.pointerBodyId);
    if (!body) return false;
    const pointer = _resolveNekoIdleCat1PlaygroundPointerClient(state, body, event);
    const moveDistance = Math.hypot(pointer.x - state.pointerStartX, pointer.y - state.pointerStartY);
    if (!state.draggingBodyId) {
        if (moveDistance <= _NEKO_IDLE_CAT1_PLAYGROUND_MIN_CLICK_DRAG_PX) return true;
        try { event.stopPropagation(); } catch (_) {}
        state.phase = 'dragging';
        state.draggingBodyId = body.id;
        state.pointerMoved = true;
        body.dragging = true;
        body.grounded = false;
        if (body.id === 'cat') _setNekoIdleCat1PlaygroundCatAirArt(button);
        const container = state.container;
        if (container) container.setAttribute('data-neko-cat1-playground-dragging', body.id);
        _startNekoIdleCat1PlaygroundPhysics(button);
    }
    try { event.preventDefault(); } catch (_) {}
    const nextX = pointer.x - state.pointerOffsetX;
    const nextY = pointer.y - state.pointerOffsetY;
    _updateNekoIdleCat1PlaygroundBodyBounds(body);
    body.grounded = false;
    body.vx = 0;
    body.vy = 0;
    const clampedX = Math.max(body.wallLeft, Math.min(nextX, body.wallRight));
    const clampedY = Math.min(nextY, body.floorY);
    _setNekoIdleCat1PlaygroundBodyPosition(
        body,
        clampedX,
        clampedY,
        { force: true }
    );
    state.pointerMoved = state.pointerMoved ||
        Math.hypot(pointer.x - state.pointerStartX, pointer.y - state.pointerStartY) > _NEKO_IDLE_CAT1_PLAYGROUND_MIN_CLICK_DRAG_PX;
    state.lastPointerSamples.push({
        x: clampedX + state.pointerOffsetX,
        y: clampedY + state.pointerOffsetY,
        timestamp: Date.now()
    });
    if (state.lastPointerSamples.length > _NEKO_IDLE_CAT1_PLAYGROUND_POINTER_SAMPLE_LIMIT) {
        state.lastPointerSamples.shift();
    }
    const art = button && button.querySelector('.neko-idle-return-art');
    if (body.id === 'cat' && art) {
        _setNekoIdleReturnArtSource(art, _NEKO_IDLE_CAT1_PLAYGROUND_AIR_ASSET_URL, _NEKO_IDLE_TIER_CAT1, { animate: false });
    }
    return true;
}

function _handleNekoIdleCat1PlaygroundPointerUp(button, event) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || !state.active || state.released || !state.pointerBodyId) return false;
    const body = state.bodies.get(state.pointerBodyId);
    if (!body) return false;
    if (!state.draggingBodyId) {
        if (body.element && typeof body.element.releasePointerCapture === 'function' && event && event.pointerId !== undefined) {
            try { body.element.releasePointerCapture(event.pointerId); } catch (_) {}
        }
        state.pointerBodyId = '';
        state.pointerId = null;
        state.lastPointerSamples = [];
        return false;
    }
    try { if (event) event.preventDefault(); } catch (_) {}
    if (body.element && typeof body.element.releasePointerCapture === 'function' && event && event.pointerId !== undefined) {
        try { body.element.releasePointerCapture(event.pointerId); } catch (_) {}
    }
    const velocity = _getNekoIdleCat1PlaygroundPointerVelocity(state.lastPointerSamples);
    body.vx = velocity.vx;
    body.vy = velocity.vy;
    body.angularVelocity = _getNekoIdleCat1PlaygroundThrowAngularVelocity(body, velocity, state);
    body.dragging = false;
    body.grounded = false;
    state.phase = 'ballistic';
    state.draggingBodyId = '';
    state.pointerBodyId = '';
    state.pointerId = null;
    state.suppressClickBodyId = body.id;
    if (state.suppressClickTimer) {
        clearTimeout(state.suppressClickTimer);
    }
    state.suppressClickTimer = setTimeout(() => {
        const latestState = button && button.__nekoIdleCat1PlaygroundDropState;
        if (latestState === state) {
            latestState.pointerMoved = false;
            latestState.suppressClickBodyId = '';
            latestState.suppressClickTimer = 0;
        }
    }, 80);
    const container = state.container;
    if (container) container.removeAttribute('data-neko-cat1-playground-dragging');
    _startNekoIdleCat1PlaygroundPhysics(button);
    return true;
}

function _suppressNekoIdleCat1PlaygroundNonCatNativeEvent(event) {
    if (!event) return;
    try { event.preventDefault(); } catch (_) {}
    try { event.stopPropagation(); } catch (_) {}
    try { event.stopImmediatePropagation(); } catch (_) {}
}

function _clearNekoIdleCat1PlaygroundPointerListeners(button) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || !state.pointerHandlers) return;
    state.pointerHandlers.forEach((binding) => {
        if (!binding || !binding.element) return;
        binding.element.removeEventListener(binding.type, binding.handler, binding.options || false);
    });
    state.pointerHandlers = [];
}

function _bindNekoIdleCat1PlaygroundBodyInput(button, body, bind) {
    if (!body || !body.element || typeof bind !== 'function') return;
    body.element.style.pointerEvents = 'auto';
    bind(body.element, 'pointerdown', (event) => {
        _handleNekoIdleCat1PlaygroundPointerDown(button, event);
    });
    if (body.id === 'cat') {
        bind(body.element, 'click', (event) => {
            _handleNekoIdleCat1PlaygroundCatClick(button, event);
        });
        return;
    }
    bind(body.element, 'mousedown', _suppressNekoIdleCat1PlaygroundNonCatNativeEvent, true);
    bind(body.element, 'touchstart', _suppressNekoIdleCat1PlaygroundNonCatNativeEvent, { capture: true, passive: false });
    bind(body.element, 'click', _suppressNekoIdleCat1PlaygroundNonCatNativeEvent, true);
}

function _installNekoIdleCat1PlaygroundPointerListeners(button) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!state || !state.active || state.released) return;
    _clearNekoIdleCat1PlaygroundPointerListeners(button);
    const bind = (element, type, handler, options) => {
        if (!element || typeof element.addEventListener !== 'function') return;
        element.addEventListener(type, handler, options || false);
        state.pointerHandlers.push({ element, type, handler, options: options || false });
    };
    state.bodies.forEach((body) => {
        _bindNekoIdleCat1PlaygroundBodyInput(button, body, bind);
    });
    bind(document, 'pointermove', (event) => {
        _handleNekoIdleCat1PlaygroundPointerMove(button, event);
    });
    bind(document, 'pointerup', (event) => {
        _handleNekoIdleCat1PlaygroundPointerUp(button, event);
    });
    bind(document, 'pointercancel', (event) => {
        _handleNekoIdleCat1PlaygroundPointerUp(button, event);
    });
    bind(window, 'resize', () => {
        if (!_isNekoIdleCat1PlaygroundDropActive(button)) return;
        _refreshNekoIdleCat1PlaygroundViewportBottom(button);
        state.bodies.forEach(_reclampNekoIdleCat1PlaygroundBodyAfterBoundsChange);
        _startNekoIdleCat1PlaygroundPhysics(button);
    });
}

function _dispatchNekoIdleReturnClickFromButton(button) {
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!button || !container) return false;
    const rect = container.getBoundingClientRect();
    const prefix = button.id && button.id.endsWith('-btn-return')
        ? button.id.slice(0, -'-btn-return'.length)
        : 'avatar';
    const event = new CustomEvent(`${prefix}-return-click`, {
        detail: {
            returnButtonRect: {
                left: rect.left,
                top: rect.top,
                width: rect.width,
                height: rect.height
            }
        }
    });
    const dispatchReturnEvent = () => {
        window.dispatchEvent(event);
    };
    if (
        typeof window.playNekoModelCatTransition === 'function' &&
        !_isNekoGoodbyeIdleBallButton(button)
    ) {
        window.playNekoModelCatTransition({
            direction: 'cat-to-model',
            anchorRect: rect,
            coverRect: window._savedGoodbyeRect || null,
            container: container
        }).catch((error) => {
            console.warn('[AvatarButtonMixin] model/cat return transition failed:', error);
            container.removeAttribute('data-neko-model-cat-transitioning');
        });
        dispatchReturnEvent();
        return true;
    }
    dispatchReturnEvent();
    return true;
}

function _handleNekoIdleCat1PlaygroundCatClick(button, event) {
    if (!_isNekoIdleCat1PlaygroundDropActive(button)) return false;
    const state = button.__nekoIdleCat1PlaygroundDropState;
    if (state && (state.draggingBodyId || state.suppressClickBodyId === 'cat')) {
        if (event) {
            try { event.preventDefault(); } catch (_) {}
            try { event.stopPropagation(); } catch (_) {}
        }
        return true;
    }
    if (event) {
        try { event.preventDefault(); } catch (_) {}
        try { event.stopPropagation(); } catch (_) {}
    }
    _stopNekoIdleCat1PlaygroundPhysics(button);
    _clearNekoIdleCat1PlaygroundPointerListeners(button);
    _releaseNekoIdleCat1PlaygroundDropLifecycle(button, 'cat-click');
    _dispatchNekoIdleReturnClickFromButton(button);
    return true;
}

function _startNekoIdleCat1PlaygroundDropAfterYarnTargetReady(button, detail) {
    if (!button) return false;
    _cancelNekoIdleCat1PlaygroundPendingEntry(button);
    _clearNekoIdleCat1QuestionMark(button);
    _setNekoIdleCat1QuestionMarkKeyboardTarget(null);
    if (_getNekoIdleCat1PairMoveChatTarget()) {
        return _startNekoIdleCat1PlaygroundDrop(button, detail);
    }

    _requestNekoIdleCat1PlaygroundYarnTarget(detail);
    const startedAt = Date.now();
    const pending = {
        frame: 0,
        timer: 0,
        token: startedAt
    };
    button.__nekoIdleCat1PlaygroundPendingEntry = pending;

    const finish = () => {
        if (!button || button.__nekoIdleCat1PlaygroundPendingEntry !== pending) return false;
        button.__nekoIdleCat1PlaygroundPendingEntry = null;
        _startNekoIdleCat1PlaygroundDrop(button, detail);
        return true;
    };
    const poll = () => {
        if (!button || button.__nekoIdleCat1PlaygroundPendingEntry !== pending) return;
        pending.frame = 0;
        if (_getNekoIdleCat1PairMoveChatTarget() ||
            Date.now() - startedAt >= _NEKO_IDLE_CAT1_PLAYGROUND_YARN_TARGET_WAIT_MS) {
            finish();
            return;
        }
        if (typeof window.requestAnimationFrame === 'function') {
            pending.frame = window.requestAnimationFrame(poll);
        } else {
            pending.timer = setTimeout(poll, 40);
        }
    };

    if (typeof window.requestAnimationFrame === 'function') {
        pending.frame = window.requestAnimationFrame(poll);
    } else {
        pending.timer = setTimeout(poll, 40);
    }
    return true;
}

function _startNekoIdleCat1PlaygroundDrop(button, detail) {
    if (!button) return false;
    if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) return false;
    const state = _acquireNekoIdleCat1PlaygroundDropLifecycle(button, detail);
    if (!state) return false;
    _setNekoIdleCat1PlaygroundCatAirArt(button);
    if (!_registerNekoIdleCat1PlaygroundPhysicsBodies(button)) {
        _releaseNekoIdleCat1PlaygroundDropLifecycle(button, 'no-bodies');
        return false;
    }
    _installNekoIdleCat1PlaygroundPointerListeners(button);
    _refreshNekoIdleCat1PlaygroundViewportBottom(button);
    _startNekoIdleCat1PlaygroundPhysics(button);
    return true;
}

function _handleNekoIdleCat1PlaygroundEntryRequest(event) {
    const button = _getNekoIdleCat1PlaygroundEntryButton();
    if (!button) return false;
    const detail = event && event.detail ? event.detail : null;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) {
        _clearNekoIdleCat1QuestionMark(button);
        _clearNekoIdleCat1PlaygroundQuestionBlockClone(button);
        return false;
    }
    if (detail && detail.questionBlockScreenRect &&
        !_isNekoIdleCat1PlaygroundEntryOrDropActive(button, 'question-mark-entry')) {
        _storeNekoIdleCat1PlaygroundQuestionBlockClone(
            button,
            _createNekoIdleCat1PlaygroundQuestionBlockCloneFromScreenRect(detail.questionBlockScreenRect, button)
        );
    }
    return _startNekoIdleCat1PlaygroundDropAfterYarnTargetReady(button, detail);
}

if (typeof window !== 'undefined') {
    window.addEventListener('neko:idle-cat1-playground-entry-request', _handleNekoIdleCat1PlaygroundEntryRequest);
    window.addEventListener('neko:idle-cat1-playground-desktop-pointer', _handleNekoIdleCat1PlaygroundDesktopPointerEvent);
    window.addEventListener('pagehide', () => {
        _releaseAllNekoIdleCat1PlaygroundDropLifecycles('pagehide');
    });
    window.addEventListener('beforeunload', () => {
        _releaseAllNekoIdleCat1PlaygroundDropLifecycles('beforeunload');
    });
}
