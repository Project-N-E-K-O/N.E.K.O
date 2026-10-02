(function (root) {
    'use strict';
    const api = root.NekoClickGuide = root.NekoClickGuide || {};
    api.createRunId = prefix => prefix + (typeof root.crypto?.randomUUID === 'function'
        ? root.crypto.randomUUID() : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2));
    api.createRunner = function ({ steps, labels, onEnd, onStep, presentation,
        startIndex = 0, initialHistory = [], initialSkipped = [], backAtStart = false }) {
        let index = -1;
        const history = [...initialHistory];
        const skippedIndices = new Set(initialSkipped);
        let ended = false;
        let busy = false;
        let controller;
        let stopTracking;
        let leave;
        let readinessFailed = false;
        let nativeFallbackEligible = false;
        let nativeFallbackShown = false;
        let windowWatch;
        let currentView;
        let inspected = false;
        let presentationFrame;
        const view = () => currentView || steps[index];
        const expectsTarget = display => !!(display.target || display.nativeTarget);
        const animateFocus = display => typeof display.animateFocus === 'function'
            ? !!display.animateFocus() : !!display.animateFocus;
        const previousFocus = document.activeElement;
        const layer = document.createElement('div');
        layer.className = 'click-guide-layer';
        const mask = api.createMask(layer);
        const highlight = api.createHighlight(layer);
        const secondaryHighlight = api.createHighlight(layer);
        const card = document.createElement('section');
        card.className = 'click-guide-card';
        card.setAttribute('role', 'dialog');
        card.setAttribute('aria-label', labels.tour);
        card.tabIndex = -1;
        const progress = document.createElement('div');
        progress.className = 'click-guide-progress';
        const title = document.createElement('h2');
        const body = document.createElement('p');
        const status = document.createElement('p');
        status.className = 'click-guide-status';
        status.setAttribute('aria-live', 'polite');
        const actions = document.createElement('div');
        actions.className = 'click-guide-actions';
        const skip = document.createElement('button');
        skip.type = 'button';
        skip.textContent = labels.skip;
        const back = document.createElement('button');
        back.type = 'button';
        back.className = 'click-guide-back';
        back.textContent = labels.back || 'Back';
        const next = document.createElement('button');
        next.type = 'button';
        next.className = 'click-guide-next';
        actions.append(skip, back, next);
        card.append(progress, title, body, status, actions);
        layer.append(card);

        async function cleanupStep() {
            controller?.abort();
            windowWatch = currentView = null;
            inspected = false;
            stopTracking?.();
            stopTracking = null;
            presentationFrame = null;
            const cleanup = leave;
            leave = null;
            if (typeof cleanup === 'function') await cleanup();
        }
        async function finish(reason) {
            if (ended) return;
            ended = true;
            try { await cleanupStep(); }
            finally {
                document.removeEventListener('keydown', escape);
                root.removeEventListener('neko:click-guide-window-skip', windowSkip);
                layer.remove();
                try { await presentation?.close(); }
                finally {
                    if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
                    await onEnd?.(reason);
                }
            }
        }
        const windowSkip = () => void finish('skipped');
        function escape(event) {
            // Escape belongs to the composer and open menus; skipping is explicit.
            if (event.isComposing || event.key === 'Escape') return;
            if (event.key === 'Tab') {
                const target = api.resolveTarget(view()?.target);
                const selector = 'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"], a[href]';
                const controls = [...card.querySelectorAll(selector)];
                if (target?.matches(selector)) controls.unshift(target);
                if (target) controls.unshift(...target.querySelectorAll(selector));
                const current = controls.indexOf(document.activeElement);
                const nextIndex = (current + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
                event.preventDefault();
                controls[nextIndex]?.focus({ preventScroll: true });
            }
        }
        async function advance(fromTarget = false, clickedView) {
            if (ended || busy) return;
            busy = true;
            const step = clickedView || view();
            const signal = controller?.signal;
            try {
                await step?.onAdvance?.({ by: fromTarget ? 'target' : 'next', signal });
                if (fromTarget && step?.ready) await api.waitUntil(step.ready, signal, step.readyTimeout);
                if (signal?.aborted || ended) return;
                if (!step?.resume) {
                    history.push(index);
                    await show(index + 1);
                }
            } catch (error) {
                if (!ended && error.name !== 'AbortError') {
                    readinessFailed = true;
                    status.textContent = labels.unavailable;
                }
            } finally {
                busy = false;
                refreshPresentationActions();
            }
        }
        async function retreat() {
            if (ended || busy || back.disabled) return;
            busy = true;
            try {
                const signal = controller?.signal;
                if (windowWatch?.view()) {
                    for (let depth = 0; depth < 8 && windowWatch.view(); depth++) {
                        await windowWatch.view().onAdvance?.({ by: 'back', signal });
                        await new Promise(resolve => root.setTimeout(resolve, 120));
                    }
                    if (windowWatch.view()) throw new Error('window_still_open');
                } else if (currentView && currentView !== steps[index]) {
                    await currentView.onAdvance?.({ by: 'back', signal });
                    if (currentView.ready) await api.waitUntil(currentView.ready, signal);
                }
                if (signal?.aborted || ended) return;
                if (!history.length) { await finish('back'); return; }
                await show(history.pop(), true);
            } catch (error) {
                if (!ended && error.name !== 'AbortError') {
                    readinessFailed = true;
                    status.textContent = labels.unavailable;
                }
            } finally {
                busy = false;
                refreshPresentationActions();
            }
        }
        function refreshPresentationActions() {
            if (!presentation || !presentationFrame || presentationFrame.step !== index || ended) return;
            presentationFrame = { ...presentationFrame, nextDisabled: next.disabled, backDisabled: back.disabled };
            presentation.update(presentationFrame);
        }
        function syncNativeFallback(available) {
            if (!nativeFallbackEligible || !steps[index]?.nativeTarget || controller?.signal.aborted || ended) return;
            if (available === true && nativeFallbackShown) {
                nativeFallbackShown = false;
                readinessFailed = false;
                status.textContent = '';
                next.disabled = !!steps[index].requireClick;
            } else if (available !== true && !nativeFallbackShown) {
                nativeFallbackShown = true;
                readinessFailed = true;
                status.textContent = labels.nativeFallback;
                next.disabled = false;
            } else return;
            if (presentationFrame?.step === index) {
                presentationFrame = { ...presentationFrame, cardFallback: readinessFailed,
                    status: status.textContent, nextDisabled: next.disabled };
                presentation?.update(presentationFrame);
            }
        }
        async function show(nextIndex, backward = false) {
            await cleanupStep();
            if (ended) return;
            index = nextIndex;
            while (!backward && index < steps.length && steps[index].when && !steps[index].when()) {
                skippedIndices.add(index);
                index++;
            }
            if (index >= steps.length) return finish('completed');
            skippedIndices.delete(index);
            const step = steps[index];
            readinessFailed = false;
            nativeFallbackEligible = nativeFallbackShown = false;
            controller = new AbortController();
            const signal = controller.signal;
            progress.textContent = (labels.section ? labels.section + ' · ' : '')
                + `${index + 1 - [...skippedIndices].filter(skipped => skipped <= index).length} / ${steps.length - skippedIndices.size}`;
            title.textContent = step.title;
            body.textContent = typeof step.body === 'function' ? step.body() : step.body;
            status.textContent = '';
            next.textContent = step.nextLabel || labels.next;
            next.disabled = false;
            back.disabled = !history.length && !backAtStart;
            card.style.display = expectsTarget(step) ? 'none' : '';
            if (!animateFocus(step)) {
                mask.update(null, root.innerWidth, root.innerHeight);
                highlight.update(null);
                secondaryHighlight.update(null);
            }
            highlight.setTransition?.(animateFocus(step));
            try {
                leave = await step.enter?.(signal, { backward });
                if (ended || signal.aborted) { await cleanupStep(); return; }
            } catch (error) {
                if (signal.aborted) return;
                status.textContent = labels.unavailable;
            }
            if (step.windowGuide && !presentation) {
                windowWatch = api.watchOpenedWindow({ signal, copy: step.windowGuide, labels,
                    onReturn: () => { if (!ended && !signal.aborted && !busy) void advance(false, step); } });
            }
            if (step.windowGuide && presentation) {
                root.addEventListener('neko-avatar-popup-navigate', event => {
                    if (event.detail?.url) step.windowGuide.url = event.detail.url;
                }, { signal });
            }
            let targetSeen = !step.target;
            const activeView = () => windowWatch?.view() || step.view?.() || step;
            stopTracking = api.trackTarget(() => api.resolveTarget(activeView().target), (element, rect) => {
                if (signal.aborted) return;
                const previous = currentView;
                currentView = activeView();
                if (currentView !== step) inspected = true;
                if (inspected && currentView === step && previous && !previous.resume && !busy) {
                    void advance(); return;
                }
                const display = currentView;
                const description = typeof display.body === 'function' ? display.body() : display.body;
                // Keep the card's text nodes stable while only target geometry changes.
                if (title.textContent !== display.title) title.textContent = display.title;
                if (body.textContent !== description) body.textContent = description;
                const nextLabel = display.nextLabel || labels.next;
                if (next.textContent !== nextLabel) next.textContent = nextLabel;
                card.style.display = expectsTarget(display) && !rect && !readinessFailed ? 'none' : '';
                const transitioning = animateFocus(display);
                highlight.setTransition?.(transitioning);
                const cursorElement = display.cursorTarget && api.resolveTarget(display.cursorTarget);
                const cursorRect = cursorElement?.getBoundingClientRect();
                const cursorOffset = rect && cursorRect && {
                    x: (cursorRect.left + cursorRect.right) / 2 - rect.left,
                    y: (cursorRect.top + cursorRect.bottom) / 2 - rect.top
                };
                const cursorVisible = !!display.advanceOnClick &&
                    (typeof display.cursorVisible === 'function' ? display.cursorVisible() : display.cursorVisible !== false);
                const shape = highlight.update(rect, display.shape, element, display.catEars,
                    cursorVisible, display.radiusMode, display.cursorOnTarget, cursorOffset);
                const radius = api.focusRadius(rect, shape, element, display.radiusMode);
                const secondaryElement = display.secondaryTarget && api.resolveTarget(display.secondaryTarget);
                const bounds = secondaryElement && (display.secondaryFocusRect?.(secondaryElement)
                    || secondaryElement.getBoundingClientRect());
                const secondaryPadding = display.secondaryPadding || 0;
                const secondaryRect = bounds && {
                    left: Math.max(0, bounds.left - secondaryPadding),
                    top: Math.max(0, bounds.top - secondaryPadding),
                    right: Math.min(root.innerWidth, bounds.right + secondaryPadding),
                    bottom: Math.min(root.innerHeight, bounds.bottom + secondaryPadding)
                };
                secondaryHighlight.setTransition?.(transitioning);
                const secondaryShape = secondaryHighlight.update(secondaryRect, display.secondaryShape,
                    secondaryElement, display.secondaryCatEars, false, display.secondaryRadiusMode);
                const secondaryRadius = api.focusRadius(secondaryRect, secondaryShape,
                    secondaryElement, display.secondaryRadiusMode);
                mask.update(rect, root.innerWidth, root.innerHeight, shape, radius,
                    secondaryRect, secondaryShape, secondaryRadius);
                if (element) targetSeen = true;
                const width = card.getBoundingClientRect().width;
                const height = card.getBoundingClientRect().height;
                const left = rect ? (rect.left + rect.right - width) / 2 : (root.innerWidth - width) / 2;
                let top = rect ? rect.bottom + 16 : (root.innerHeight - height) / 2;
                if (rect && top + height > root.innerHeight - 12) top = rect.top - height - 16;
                // Prefer the side if neither above nor below fits without covering the target.
                let x = left;
                if (rect && top < 12) {
                    x = rect.right + 16 + width <= root.innerWidth ? rect.right + 16 : rect.left - width - 16;
                    top = (rect.top + rect.bottom - height) / 2;
                }
                const avoid = display.cardAvoid?.();
                const cardAvoid = avoid?.width && avoid?.height
                    ? { left: avoid.left, top: avoid.top, right: avoid.right, bottom: avoid.bottom,
                        width: avoid.width, height: avoid.height } : null;
                if (cardAvoid?.width && cardAvoid?.height) {
                    if (cardAvoid.top - height - 20 >= 12) top = cardAvoid.top - height - 20;
                    else if (cardAvoid.left - width - 20 >= 12) {
                        x = cardAvoid.left - width - 20;
                        top = (cardAvoid.top + cardAvoid.bottom - height) / 2;
                    } else if (cardAvoid.right + width + 20 <= root.innerWidth - 12) {
                        x = cardAvoid.right + 20;
                        top = (cardAvoid.top + cardAvoid.bottom - height) / 2;
                    }
                }
                card.style.left = Math.max(12, Math.min(root.innerWidth - width - 12, x)) + 'px';
                card.style.top = Math.max(12, Math.min(root.innerHeight - height - 12, top)) + 'px';
                next.disabled = !readinessFailed && (display.requireInput === true
                    || (display.requireClick === true
                        && ((!!element && !element.disabled && element.getAttribute('aria-disabled') !== 'true')
                            || (!!presentation && !!step.nativeTarget))));
                back.disabled = !history.length && !backAtStart;
                presentationFrame = { step: index, rect, nativeTarget: step.nativeTarget, cardAvoid,
                    windowGuide: step.windowGuide,
                    shape: shape || display.shape, radius, catEars: !!display.catEars,
                    secondaryRect, secondaryShape, secondaryRadius,
                    animateFocus: transitioning,
                    clickable: cursorVisible, cursorOnTarget: !!display.cursorOnTarget,
                    cursorOffset,
                    targetExpected: expectsTarget(display), cardFallback: readinessFailed,
                    title: title.textContent, body: body.textContent,
                    progress: progress.textContent, status: status.textContent, labels,
                    nextLabel: next.textContent, nextDisabled: next.disabled || busy,
                    backDisabled: back.disabled || busy };
                presentation?.update(presentationFrame);
            }, element => activeView().focusRect?.(element) || element.getBoundingClientRect(), step.padding);
            const unavailableTimer = root.setTimeout(() => {
                const keyTarget = step.advanceOnKey && api.resolveTarget(step.keyTarget);
                if (!signal.aborted && ((!targetSeen && (!step.nativeTarget || !presentation))
                    || (step.advanceOnKey && (!keyTarget || keyTarget.disabled || keyTarget.readOnly)))) {
                    readinessFailed = true;
                    status.textContent = labels.unavailable;
                }
            }, 2000);
            const nativeFallbackTimer = step.nativeTarget && presentation && root.setTimeout(() => {
                if (signal.aborted) return;
                nativeFallbackEligible = true;
                syncNativeFallback(presentation.nativeTargetAvailable?.());
            }, 6000);
            signal.addEventListener('abort', () => root.clearTimeout(unavailableTimer), { once: true });
            signal.addEventListener('abort', () => root.clearTimeout(nativeFallbackTimer), { once: true });
            api.onTargetClick(document, () => api.resolveTarget(activeView().target), signal, (event, clicked) => {
                if (clicked?.advanceOnClick) void advance(true, clicked);
            }, activeView);
            if (step.advanceOnHover) {
                // Follow the wheel's real open state: its hover may occur before React
                // replaces the send button, or while the pointer is already in place.
                const observeOpen = root.setInterval(() => {
                    if (!busy && step.ready?.()) void advance(true);
                }, 50);
                signal.addEventListener('abort', () => root.clearInterval(observeOpen), { once: true });
            }
            if (step.advanceOnKey) {
                let awaitingSend = false;
                api.onTargetKey(document, step.keyTarget, signal, step.advanceOnKey, step.keyReady, () => {
                    if (awaitingSend) return;
                    awaitingSend = true;
                    api.waitUntil(step.ready, signal, 1500).then(() => void advance(true))
                        .catch(() => {}).finally(() => { awaitingSend = false; });
                });
            }
            const input = step.requireInput && step.keyTarget && api.resolveTarget(step.keyTarget);
            (input || card).focus({ preventScroll: true });
            onStep?.(step, index);
        }
        skip.onclick = () => void finish('skipped');
        back.onclick = () => void retreat();
        next.onclick = () => void advance();
        return {
            start() {
                document.body.append(layer);
                if (presentation) {
                    layer.classList.add('click-guide-native');
                    layer.style.visibility = 'hidden';
                    presentation.bind({ next: () => { if (!next.disabled) void advance(); },
                        back: () => { if (!back.disabled) void retreat(); },
                        target: () => { if (steps[index]?.nativeTarget) void advance(true); },
                        nativeTargetAvailability: syncNativeFallback,
                        returned: () => void advance(),
                        skip: () => void finish('skipped'), failed: () => void finish('failed') });
                }
                document.addEventListener('keydown', escape);
                root.addEventListener('neko:click-guide-window-skip', windowSkip);
                return show(startIndex, startIndex !== 0);
            },
            stop: finish,
            get index() { return index; },
            get history() { return [...history]; },
            get skipped() { return [...skippedIndices]; }
        };
    };
})(window);
