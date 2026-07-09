(function () {
    'use strict';

    const STORAGE_KEY = 'neko:avatar-multiscreen-drag-hint:v1';
    const SNOOZE_MS = 3 * 24 * 60 * 60 * 1000;
    const MISS_WINDOW_MS = 30 * 1000;
    const REQUIRED_MISSES = 2;
    const EDGE_RELEASE_THRESHOLD_PX = 180;
    const MIN_EDGE_DRAG_DISTANCE_PX = 48;

    const FALLBACK_TEXT = {
        title: 'Move YUI to another screen',
        body: 'Keep dragging toward the screen edge and release. If she is too close to the edge, she will bounce back first.',
        ack: 'Got it',
        never: 'Do not remind me'
    };

    let isPromptVisible = false;
    let missRecordQueue = Promise.resolve();
    let approachRecordQueue = Promise.resolve();
    let approachRecordPending = false;
    let latestApproachRecord = null;
    let lastDisplaySwitchMissCall = null;

    function now() {
        return Date.now();
    }

    function normalizeSource(source) {
        return source || 'avatar';
    }

    function translate(key, fallback) {
        try {
            if (typeof window.t !== 'function') return fallback;
            const value = window.t(key);
            return value && value !== key ? value : fallback;
        } catch (_) {
            return fallback;
        }
    }

    function readState() {
        try {
            const raw = window.localStorage && window.localStorage.getItem(STORAGE_KEY);
            if (!raw) return {};
            const parsed = JSON.parse(raw);
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch (_) {
            return {};
        }
    }

    function writeState(state) {
        try {
            if (!window.localStorage) return;
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state || {}));
        } catch (_) {}
    }

    function isSuppressed(state) {
        if (!state || typeof state !== 'object') return false;
        if (state.never === true) return true;
        return Number(state.snoozeUntil) > now();
    }

    function hasDisplaySwitchBridge() {
        return Boolean(
            window.electronScreen &&
            typeof window.electronScreen.getAllDisplays === 'function' &&
            typeof window.electronScreen.getCurrentDisplay === 'function' &&
            typeof window.electronScreen.moveWindowToDisplay === 'function'
        );
    }

    async function hasMultipleDisplays() {
        try {
            if (!hasDisplaySwitchBridge()) return false;
            const displays = await window.electronScreen.getAllDisplays();
            return Array.isArray(displays) && displays.length > 1;
        } catch (_) {
            return false;
        }
    }

    function ensureStyles() {
        if (document.getElementById('avatar-multiscreen-drag-hint-style')) return;

        const style = document.createElement('style');
        style.id = 'avatar-multiscreen-drag-hint-style';
        style.textContent = `
            @keyframes avatarMultiscreenDragHintDropIn {
                from {
                    opacity: 0;
                    transform: translate(-50%, -16px);
                }
                to {
                    opacity: 1;
                    transform: translate(-50%, 0);
                }
            }
            #avatar-multiscreen-drag-hint {
                position: fixed;
                left: 50%;
                top: calc(env(safe-area-inset-top, 0px) + 16px);
                z-index: 100001;
                isolation: isolate;
                width: min(430px, calc(100vw - 28px));
                box-sizing: border-box;
                display: grid;
                gap: 10px;
                overflow: hidden;
                padding: 18px 18px 14px;
                border-radius: 20px;
                border: 1px solid rgba(98, 160, 217, 0.24);
                background:
                    radial-gradient(circle at 10% 8%, rgba(111, 194, 255, 0.16), transparent 118px),
                    radial-gradient(circle at 100% 0%, rgba(255, 221, 145, 0.16), transparent 108px),
                    linear-gradient(180deg, rgba(251, 254, 255, 0.98), rgba(239, 248, 255, 0.98));
                color: #14385d;
                box-shadow:
                    inset 0 1px 0 rgba(255, 255, 255, 0.72),
                    0 24px 80px rgba(37, 91, 143, 0.2);
                font-family: "Avenir Next", "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", -apple-system, BlinkMacSystemFont, sans-serif;
                pointer-events: auto;
                opacity: 0;
                transform: translate(-50%, -16px);
                transition: opacity 0.22s cubic-bezier(0.1, 0.9, 0.2, 1),
                    transform 0.22s cubic-bezier(0.1, 0.9, 0.2, 1);
                animation: avatarMultiscreenDragHintDropIn 0.22s cubic-bezier(0.1, 0.9, 0.2, 1) forwards;
            }
            #avatar-multiscreen-drag-hint.avatar-multiscreen-drag-hint-visible {
                opacity: 1;
                transform: translate(-50%, 0);
            }
            #avatar-multiscreen-drag-hint::before {
                content: "";
                position: absolute;
                inset: -16px;
                z-index: 0;
                opacity: 0.14;
                pointer-events: none;
                background-image:
                    url("/static/icons/paw_ui.png"),
                    url("/static/icons/paw_ui.png"),
                    url("/static/icons/paw_ui.png");
                background-repeat: no-repeat;
                background-size: 72px 54px, 50px 38px, 58px 44px;
                background-position: 94% 12%, 12% 96%, 72% 88%;
            }
            #avatar-multiscreen-drag-hint > * {
                position: relative;
                z-index: 1;
            }
            #avatar-multiscreen-drag-hint .avatar-multiscreen-drag-hint-title {
                font-size: 15px;
                line-height: 1.35;
                font-weight: 800;
                margin: 0;
                color: #14385d;
            }
            #avatar-multiscreen-drag-hint .avatar-multiscreen-drag-hint-body {
                font-size: 13px;
                line-height: 1.62;
                color: rgba(43, 73, 103, 0.72);
                margin: 0;
            }
            #avatar-multiscreen-drag-hint .avatar-multiscreen-drag-hint-actions {
                display: flex;
                gap: 8px;
                justify-content: flex-end;
                flex-wrap: wrap;
                margin-top: 2px;
            }
            #avatar-multiscreen-drag-hint button {
                min-height: 34px;
                padding: 0 16px;
                border: 1px solid rgba(96, 159, 216, 0.24);
                border-radius: 999px;
                font-size: 12px;
                line-height: 1;
                cursor: pointer;
                white-space: nowrap;
                font-weight: 800;
                transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
            }
            #avatar-multiscreen-drag-hint button:hover {
                transform: translateY(-1px);
                border-color: rgba(59, 140, 221, 0.42);
                box-shadow: 0 10px 24px rgba(49, 114, 175, 0.12);
            }
            #avatar-multiscreen-drag-hint button:active {
                transform: translateY(0);
            }
            #avatar-multiscreen-drag-hint .avatar-multiscreen-drag-hint-ack {
                border-color: rgba(255, 255, 255, 0.62);
                background: linear-gradient(135deg, rgba(76, 169, 255, 0.94), rgba(47, 150, 242, 0.92));
                color: #f8fbff;
                box-shadow: 0 14px 28px rgba(47, 143, 233, 0.22);
            }
            #avatar-multiscreen-drag-hint .avatar-multiscreen-drag-hint-ack:hover {
                background: linear-gradient(135deg, rgba(65, 159, 248, 0.98), rgba(40, 139, 229, 0.96));
            }
            #avatar-multiscreen-drag-hint .avatar-multiscreen-drag-hint-never {
                background: rgba(244, 250, 255, 0.92);
                color: #145287;
            }
            #avatar-multiscreen-drag-hint .avatar-multiscreen-drag-hint-never:hover {
                background: rgba(255, 255, 255, 0.96);
            }
        `;
        document.head.appendChild(style);
    }

    function removePrompt() {
        const prompt = document.getElementById('avatar-multiscreen-drag-hint');
        if (prompt) prompt.remove();
        isPromptVisible = false;
    }

    function ackPrompt() {
        const state = readState();
        state.snoozeUntil = now() + SNOOZE_MS;
        state.recentMissCount = 0;
        state.lastMissAt = 0;
        writeState(state);
        removePrompt();
    }

    function dismissForever() {
        const state = readState();
        state.never = true;
        state.recentMissCount = 0;
        state.lastMissAt = 0;
        writeState(state);
        removePrompt();
    }

    function showPrompt() {
        if (isPromptVisible || document.getElementById('avatar-multiscreen-drag-hint')) {
            isPromptVisible = true;
            return;
        }
        if (!document.body) return;

        ensureStyles();

        const prompt = document.createElement('div');
        prompt.id = 'avatar-multiscreen-drag-hint';
        prompt.setAttribute('role', 'status');
        prompt.setAttribute('aria-live', 'polite');

        const title = document.createElement('p');
        title.className = 'avatar-multiscreen-drag-hint-title';
        title.textContent = translate('app.multiScreenDragHint.title', FALLBACK_TEXT.title);

        const body = document.createElement('p');
        body.className = 'avatar-multiscreen-drag-hint-body';
        body.textContent = translate('app.multiScreenDragHint.body', FALLBACK_TEXT.body);

        const actions = document.createElement('div');
        actions.className = 'avatar-multiscreen-drag-hint-actions';

        const neverButton = document.createElement('button');
        neverButton.type = 'button';
        neverButton.className = 'avatar-multiscreen-drag-hint-never';
        neverButton.textContent = translate('app.multiScreenDragHint.never', FALLBACK_TEXT.never);
        neverButton.addEventListener('click', dismissForever);

        const ackButton = document.createElement('button');
        ackButton.type = 'button';
        ackButton.className = 'avatar-multiscreen-drag-hint-ack';
        ackButton.textContent = translate('app.multiScreenDragHint.ack', FALLBACK_TEXT.ack);
        ackButton.addEventListener('click', ackPrompt);

        actions.appendChild(neverButton);
        actions.appendChild(ackButton);
        prompt.appendChild(title);
        prompt.appendChild(body);
        prompt.appendChild(actions);
        document.body.appendChild(prompt);
        requestAnimationFrame(function () {
            prompt.classList.add('avatar-multiscreen-drag-hint-visible');
        });
        isPromptVisible = true;
    }

    function recordDisplaySwitchMiss(source) {
        const normalizedSource = normalizeSource(source);
        lastDisplaySwitchMissCall = {
            source: normalizedSource,
            at: now()
        };
        const nextRecord = missRecordQueue.then(function () {
            return recordDisplaySwitchMissNow(normalizedSource);
        });
        missRecordQueue = nextRecord.catch(function () {});
        return nextRecord;
    }

    function normalizeDisplay(display) {
        if (!display || typeof display !== 'object') return null;
        const x = Number.isFinite(display.screenX) ? display.screenX : (display.bounds && display.bounds.x);
        const y = Number.isFinite(display.screenY) ? display.screenY : (display.bounds && display.bounds.y);
        const width = Number.isFinite(display.width) ? display.width : (display.bounds && display.bounds.width);
        const height = Number.isFinite(display.height) ? display.height : (display.bounds && display.bounds.height);
        if (!Number.isFinite(x) || !Number.isFinite(y) || !(width > 0) || !(height > 0)) return null;
        return {
            id: display.id,
            x,
            y,
            width,
            height,
            right: x + width,
            bottom: y + height
        };
    }

    function isSameDisplay(displayA, displayB) {
        if (!displayA || !displayB) return false;
        if (displayA.id !== undefined && displayB.id !== undefined) return displayA.id === displayB.id;
        return displayA.x === displayB.x && displayA.y === displayB.y &&
            displayA.width === displayB.width && displayA.height === displayB.height;
    }

    function getPointerEdgeIntents(pointer, currentDisplay) {
        if (!pointer || !currentDisplay) return [];
        const startX = Number(pointer.startScreenX);
        const startY = Number(pointer.startScreenY);
        const releaseX = Number(pointer.screenX);
        const releaseY = Number(pointer.screenY);
        if (!Number.isFinite(startX) || !Number.isFinite(startY) ||
            !Number.isFinite(releaseX) || !Number.isFinite(releaseY)) {
            return [];
        }

        const deltaX = releaseX - startX;
        const deltaY = releaseY - startY;
        const candidates = [
            {
                edge: 'right',
                distance: deltaX,
                near: releaseX >= currentDisplay.right - EDGE_RELEASE_THRESHOLD_PX
            },
            {
                edge: 'left',
                distance: -deltaX,
                near: releaseX <= currentDisplay.x + EDGE_RELEASE_THRESHOLD_PX
            },
            {
                edge: 'bottom',
                distance: deltaY,
                near: releaseY >= currentDisplay.bottom - EDGE_RELEASE_THRESHOLD_PX
            },
            {
                edge: 'top',
                distance: -deltaY,
                near: releaseY <= currentDisplay.y + EDGE_RELEASE_THRESHOLD_PX
            }
        ].filter(candidate => candidate.near && candidate.distance >= MIN_EDGE_DRAG_DISTANCE_PX);

        if (candidates.length === 0) return [];
        candidates.sort((left, right) => right.distance - left.distance);
        return candidates.map(candidate => candidate.edge);
    }

    function touchesDisplayEdge(display, currentDisplay, edge) {
        if (edge === 'right') return Math.abs(display.x - currentDisplay.right) <= 1;
        if (edge === 'left') return Math.abs(display.right - currentDisplay.x) <= 1;
        if (edge === 'bottom') return Math.abs(display.y - currentDisplay.bottom) <= 1;
        if (edge === 'top') return Math.abs(display.bottom - currentDisplay.y) <= 1;
        return false;
    }

    function releasePointMatchesDisplayEdge(display, edge, releaseX, releaseY) {
        if (!Number.isFinite(releaseX) || !Number.isFinite(releaseY)) return false;
        if (edge === 'right' || edge === 'left') {
            return releaseY >= display.y && releaseY < display.bottom;
        }
        if (edge === 'bottom' || edge === 'top') {
            return releaseX >= display.x && releaseX < display.right;
        }
        return false;
    }

    function hasAdjacentDisplayForEdge(displays, currentDisplay, edge, pointer) {
        if (!Array.isArray(displays) || !currentDisplay || !edge) return false;
        const releaseX = Number(pointer && pointer.screenX);
        const releaseY = Number(pointer && pointer.screenY);
        for (const rawDisplay of displays) {
            const display = normalizeDisplay(rawDisplay);
            if (!display || isSameDisplay(display, currentDisplay)) continue;
            if (touchesDisplayEdge(display, currentDisplay, edge) &&
                releasePointMatchesDisplayEdge(display, edge, releaseX, releaseY)) {
                return true;
            }
        }
        return false;
    }

    async function hasPointerAdjacentEdgeIntent(pointer) {
        if (!hasDisplaySwitchBridge()) return false;
        const displays = await window.electronScreen.getAllDisplays();
        if (!Array.isArray(displays) || displays.length <= 1) return false;
        const currentDisplay = normalizeDisplay(await window.electronScreen.getCurrentDisplay());
        if (!currentDisplay) return false;
        const edges = getPointerEdgeIntents(pointer, currentDisplay);
        return edges.length > 0 &&
            edges.some(edge => hasAdjacentDisplayForEdge(displays, currentDisplay, edge, pointer));
    }

    function wasDisplaySwitchMissRecordedSince(source, startedAt) {
        const since = Number(startedAt);
        if (!Number.isFinite(since)) return false;
        const normalizedSource = normalizeSource(source);
        if (lastDisplaySwitchMissCall &&
            lastDisplaySwitchMissCall.source === normalizedSource &&
            Number(lastDisplaySwitchMissCall.at) >= since) {
            return true;
        }
        const state = readState();
        return state.lastSource === normalizedSource && Number(state.lastMissAt) >= since;
    }

    async function recordPointerEdgeRelease(source, pointer) {
        try {
            if (!(await hasPointerAdjacentEdgeIntent(pointer))) return false;
            const startedAt = Number(pointer && pointer.startedAt);
            if (wasDisplaySwitchMissRecordedSince(source, startedAt)) return false;
            return recordDisplaySwitchMiss(source);
        } catch (_) {
            return false;
        }
    }

    async function recordPointerEdgeApproachNow(source, pointer) {
        try {
            const state = readState();
            if (isSuppressed(state) || isPromptVisible) return false;
            if (!(await hasPointerAdjacentEdgeIntent(pointer))) return false;
            state.lastSource = normalizeSource(source);
            state.lastMissAt = now();
            writeState(state);
            showPrompt();
            return true;
        } catch (_) {
            return false;
        }
    }

    async function drainPointerEdgeApproaches(initialRecord) {
        let currentRecord = initialRecord;
        try {
            while (currentRecord) {
                const shown = await recordPointerEdgeApproachNow(
                    currentRecord.source,
                    currentRecord.pointer
                );
                if (shown) {
                    latestApproachRecord = null;
                    return true;
                }
                currentRecord = latestApproachRecord;
                latestApproachRecord = null;
            }
            return false;
        } finally {
            approachRecordPending = false;
        }
    }

    async function recordPointerEdgeApproach(source, pointer) {
        if (approachRecordPending) {
            latestApproachRecord = { source, pointer };
            return false;
        }
        approachRecordPending = true;
        const nextRecord = approachRecordQueue.then(function () {
            return drainPointerEdgeApproaches({ source, pointer });
        });
        approachRecordQueue = nextRecord.catch(function () {});
        return nextRecord;
    }

    async function recordDisplaySwitchMissNow(source) {
        const state = readState();
        if (isSuppressed(state) || isPromptVisible) return false;
        if (!(await hasMultipleDisplays())) return false;

        const currentTime = now();
        const lastMissAt = Number(state.lastMissAt) || 0;
        const recentCount = currentTime - lastMissAt <= MISS_WINDOW_MS
            ? (Number(state.recentMissCount) || 0) + 1
            : 1;

        state.lastMissAt = currentTime;
        state.recentMissCount = recentCount;
        state.lastSource = source || 'avatar';
        writeState(state);

        if (state.recentMissCount >= REQUIRED_MISSES) {
            showPrompt();
            return true;
        }
        return false;
    }

    function isModelCenterOutsideCurrentWindow(model) {
        try {
            if (!model || typeof model.getBounds !== 'function') return false;
            const bounds = model.getBounds();
            if (!bounds) return false;
            const centerX = (Number(bounds.left) + Number(bounds.right)) / 2;
            const centerY = (Number(bounds.top) + Number(bounds.bottom)) / 2;
            if (!Number.isFinite(centerX) || !Number.isFinite(centerY)) return false;
            return centerX < 0 || centerX >= window.innerWidth || centerY < 0 || centerY >= window.innerHeight;
        } catch (_) {
            return false;
        }
    }

    async function isModelCenterOnAnotherDisplay(model) {
        try {
            if (!hasDisplaySwitchBridge() || !isModelCenterOutsideCurrentWindow(model)) return false;
            if (!model || typeof model.getBounds !== 'function') return false;
            const bounds = model.getBounds();
            if (!bounds) return false;
            const centerX = (Number(bounds.left) + Number(bounds.right)) / 2;
            const centerY = (Number(bounds.top) + Number(bounds.bottom)) / 2;
            if (!Number.isFinite(centerX) || !Number.isFinite(centerY)) return false;

            const currentDisplay = normalizeDisplay(await window.electronScreen.getCurrentDisplay());
            if (!currentDisplay) return false;
            const screenX = currentDisplay.x + centerX;
            const screenY = currentDisplay.y + centerY;
            const displays = await window.electronScreen.getAllDisplays();
            if (!Array.isArray(displays) || displays.length <= 1) return false;

            return displays.some(function (rawDisplay) {
                const display = normalizeDisplay(rawDisplay);
                return display && !isSameDisplay(display, currentDisplay) &&
                    screenX >= display.x && screenX < display.right &&
                    screenY >= display.y && screenY < display.bottom;
            });
        } catch (_) {
            return false;
        }
    }

    function installLive2DDisplaySwitchMissHook() {
        const Manager = window.Live2DManager;
        const proto = Manager && Manager.prototype;
        if (!proto || typeof proto._checkAndSwitchDisplay !== 'function') return false;
        if (proto._checkAndSwitchDisplay.__nekoDisplaySwitchMissHooked) return true;

        const originalCheckAndSwitchDisplay = proto._checkAndSwitchDisplay;
        const wrappedCheckAndSwitchDisplay = async function (model) {
            const displaySwitched = await originalCheckAndSwitchDisplay.apply(this, arguments);
            if (!displaySwitched && await isModelCenterOnAnotherDisplay(model)) {
                recordDisplaySwitchMiss('live2d');
            }
            return displaySwitched;
        };
        wrappedCheckAndSwitchDisplay.__nekoDisplaySwitchMissHooked = true;
        wrappedCheckAndSwitchDisplay.__nekoOriginalCheckAndSwitchDisplay = originalCheckAndSwitchDisplay;
        proto._checkAndSwitchDisplay = wrappedCheckAndSwitchDisplay;
        return true;
    }

    function scheduleLive2DDisplaySwitchMissHook() {
        [0, 50, 250, 1000, 3000, 6000].forEach(function (delay) {
            setTimeout(installLive2DDisplaySwitchMissHook, delay);
        });
    }

    function markDisplaySwitchSuccess(source) {
        const state = readState();
        state.successAt = now();
        state.successSource = source || 'avatar';
        state.recentMissCount = 0;
        state.lastMissAt = 0;
        writeState(state);
        removePrompt();
        return true;
    }

    window.NekoAvatarMultiScreenDragHint = {
        recordDisplaySwitchMiss,
        recordPointerEdgeApproach,
        recordPointerEdgeRelease,
        markDisplaySwitchSuccess,
        ackPrompt,
        dismissForever,
        _readState: readState
    };

    scheduleLive2DDisplaySwitchMissHook();
})();
