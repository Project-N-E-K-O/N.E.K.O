(function initEdgePeekController(global) {
    'use strict';
    if (!global || global.NekoEdgePeekController) return;
    const states = new WeakMap();
    const active = new Set();
    let removalObserver = null;
    const diagnostics = [];
    function trace(stage, detail) {
        diagnostics.push({ time: Date.now(), stage, detail: detail || {} });
        if (diagnostics.length > 80) diagnostics.shift();
    }
    const FADE_CLASS = 'is-cat1-edge-peek-locked-hover-fade';
    function key(button) { return button || null; }
    function begin(info) {
        if (!info || !info.button) return null;
        const button = key(info.button);
        const previous = states.get(button);
        if (previous && previous !== info) clear(button);
        const state = Object.assign({ mode: 'desktop-window', phase: 'idle', edge: null, locked: global.edgePeekLockEnabled === true, fadeTimer: 0, fadeActive: false, sourceRunner: null }, info);
        states.set(button, state); active.add(button); sync(state);
        if (!removalObserver && global.MutationObserver && global.document && global.document.documentElement) {
            removalObserver = new global.MutationObserver(pruneDetached);
            removalObserver.observe(global.document.documentElement, { childList: true, subtree: true });
        }
        trace('begin', { phase: state.phase, locked: state.locked, mode: state.mode, hasContainer: !!state.container });
        return state;
    }
    function sync(state) {
        if (!state || !state.container) return;
        state.container.classList.toggle('is-cat1-edge-peek-active', state.phase !== 'idle');
        state.container.classList.toggle('is-cat1-edge-peek-locked', state.locked === true);
        state.container.toggleAttribute('data-edge-peek-locked', state.locked === true);
        if (state.locked === true && state.phase === 'peeking') {
            if (state._previousContainerCursor === undefined) state._previousContainerCursor = state.container.style.cursor;
            state.container.style.cursor = 'default';
        } else if (state._previousContainerCursor !== undefined) {
            state.container.style.cursor = state._previousContainerCursor;
            state._previousContainerCursor = undefined;
        }
    }
    function setPhase(button, phase) { const s=states.get(key(button)); if (!s) return false; s.phase=phase; if (phase !== 'peeking') clearFade(s); sync(s); return true; }
    function applyLock(button, value) { const s=states.get(key(button)); if (!s) return false; s.locked=!!value; if (!s.locked) clearFade(s); sync(s); return true; }
    function clearFade(s) { if (s.fadeTimer) { clearTimeout(s.fadeTimer); s.fadeTimer=0; } s.fadeActive=false; if (s.container) { const art=s.container.querySelector('.neko-idle-return-art'); if (art) art.classList.remove(FADE_CLASS); } }
    function clear(button) {
        const s = states.get(key(button));
        if (!s) return false;
        clearFade(s); s.locked = false; s.phase = 'idle'; sync(s);
        states.delete(key(button)); active.delete(key(button));
        if (!active.size && removalObserver) {
            removalObserver.disconnect(); removalObserver = null;
        }
        return true;
    }
    function pruneDetached() {
        for (const button of active) {
            const state = states.get(button);
            if (button.isConnected === false || (state && state.container && state.container.isConnected === false)) clear(button);
        }
    }
    function cancel(button, options) { return clear(button, options); }
    function find(button) {
        pruneDetached();
        const exact = states.get(key(button));
        if (exact) return exact;
        if (!button) return null;
        for (const candidate of active) {
            const state = states.get(candidate);
            if (!state || !state.container) continue;
            if (state.container === button
                || (state.container.contains && state.container.contains(button))
                || (button.contains && button.contains(state.container))) return state;
        }
        return null;
    }
    function isActive(button) { pruneDetached(); if (button) return !!find(button) && find(button).phase !== 'idle'; return active.size > 0; }
    function isLocked(button) { const s=find(button); return !!s && s.phase === 'peeking' && s.locked === true; }
    function isAnyLocked() {
        pruneDetached();
        for (const button of active) {
            if (isLocked(button)) return true;
        }
        return false;
    }
    function isModelInputLocked() {
        const managers = [global.live2dManager, global.vrmManager, global.mmdManager, global.pngtuberManager];
        return managers.some(function (manager) {
            return !!(manager && (manager.isLocked === true || (manager.interaction && manager.interaction.isLocked === true)));
        });
    }
    function shouldBlockReturnBallDrag(button, container) {
        if (isModelEdgePeekLocked()) return true;
        if (button && isLocked(button)) return true;
        if (container && isLocked(container)) return true;
        if (container && container.getAttribute && container.getAttribute('data-edge-peek-locked') === 'true') return true;
        if (global.edgePeekLockEnabled === true) {
            const nodes = [button, container].filter(Boolean);
            const edgeClasses = [
                'is-cat1-edge-peek-left', 'is-cat1-edge-peek-right', 'is-cat1-edge-peek-top', 'is-cat1-edge-peek-bottom',
                'is-cat1-desktop-window-edge-peek-peeking', 'is-cat1-desktop-window-edge-peek-walking',
                'is-cat1-desktop-window-top-edge-active', 'is-cat1-desktop-window-top-edge-walking'
            ];
            if (nodes.some(node => node.classList && edgeClasses.some(name => node.classList.contains(name)))) return true;
        }
        return false;
    }
    function isModelEdgePeekLocked() {
        const manager = global.live2dManager;
        const state = manager && manager._live2DPeekState;
        return global.edgePeekLockEnabled === true && !!state
            && state.active === true && state.phase === 'peeking'
            && !!state.model && state.model === manager.currentModel
            && !state.model.destroyed && state.model.visible !== false;
    }
    function getActiveMode(button) { const s=find(button); return s && s.phase !== 'idle' ? s.mode : null; }
    function getActiveEdge(button) { const s=find(button); return s && s.phase !== 'idle' ? s.edge : null; }
    function nearArt(state, event) {
        if (!state || state.phase !== 'peeking' || !state.locked || !state.container) return false;
        const art = state.container.querySelector('.neko-idle-return-art');
        if (!art || !art.getBoundingClientRect) return false;
        const r = art.getBoundingClientRect(); const x = event.clientX; const y = event.clientY; const m = 24;
        return x >= r.left - m && x <= r.right + m && y >= r.top - m && y <= r.bottom + m;
    }
    function scheduleFade(state) {
        if (global.lockedHoverFadeEnabled === false || state.fadeActive || state.fadeTimer || !state.container) return;
        state.fadeTimer = setTimeout(function () { state.fadeTimer = 0; if (global.lockedHoverFadeEnabled === false || state.phase !== 'peeking' || !state.locked) return; const art=state.container && state.container.querySelector('.neko-idle-return-art'); if (art) { art.classList.add(FADE_CLASS); state.fadeActive=true; } }, 1000);
    }
    function onMouseMove(event) {
        pruneDetached();
        active.forEach(function (button) { const state=states.get(button); if (!state || state.phase !== 'peeking' || !state.locked) return; if (nearArt(state,event)) scheduleFade(state); else clearFade(state); });
    }
    function onLockChanged() { const enabled=global.edgePeekLockEnabled === true; active.forEach(b=>applyLock(b, enabled)); }
    function onLockedHoverFadeChanged() {
        if (global.lockedHoverFadeEnabled === false) {
            active.forEach(function (button) {
                const state = states.get(button);
                if (state) clearFade(state);
            });
        }
    }
    function getDiagnostics() { return diagnostics.slice(); }
    function resetHoverFade() {
        pruneDetached();
        active.forEach(button => clearFade(states.get(button)));
    }
    // Last-resort capture guard: every renderer drag implementation ultimately
    // starts from a pointer press on the return-ball surface. Keeping this at
    // the controller level closes entry points that do not share one handler.
    function onPointerDown(event) {
        const target = event && event.target;
        const button = target && target.closest ? target.closest('.neko-idle-return-btn') : null;
        if (button && isLocked(button)) {
            // Preserve the browser's synthesized click for a stationary touch.
            // Stopping propagation is sufficient to keep drag handlers idle.
            if (event.type !== 'touchstart' && event.pointerType !== 'touch') event.preventDefault();
            event.stopImmediatePropagation();
        }
    }
    global.addEventListener('mousemove', onMouseMove);
    global.addEventListener('blur', resetHoverFade);
    global.addEventListener('mouseleave', resetHoverFade);
    global.addEventListener('neko-locked-hover-fade-changed', onLockedHoverFadeChanged);
    if (global.document) {
        global.document.addEventListener('mouseleave', resetHoverFade);
        global.document.addEventListener('visibilitychange', resetHoverFade);
        global.document.addEventListener('pointerdown', onPointerDown, true);
        global.document.addEventListener('mousedown', onPointerDown, true);
        global.document.addEventListener('touchstart', onPointerDown, { capture: true, passive: false });
    }
    global.NekoEdgePeekController = Object.freeze({ begin, setPhase, applyLock, isActive, isLocked, isAnyLocked, isModelInputLocked, shouldBlockReturnBallDrag, getActiveMode, getActiveEdge, getDiagnostics, record: trace, cancel, clear });
    global.addEventListener('neko-edge-peek-lock-changed', onLockChanged);
})(typeof window !== 'undefined' ? window : null);
