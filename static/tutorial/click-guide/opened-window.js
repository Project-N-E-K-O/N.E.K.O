(function (root) {
    'use strict';
    const api = root.NekoClickGuide;
    // Observe only windows opened during this lesson. Preserve arguments, return
    // values and the existing opener/security policy of each business action.
    api.watchOpenedWindow = function ({ signal, copy, labels, onReturn }) {
        let child;
        let card;
        let layer;
        let mask;
        let highlight;
        let links = [];
        let stopped = false;
        const children = [];
        const closing = new WeakSet();
        const wrappedWindows = new WeakMap();
        let inspectionOwner;
        let oldInspection;
        let removeCloseListener;
        const removers = [];
        function clearView() {
            removeCloseListener?.(); removeCloseListener = null;
            layer?.remove(); links.forEach(link => link.remove());
            try {
                if (inspectionOwner && !inspectionOwner.closed) {
                    if (oldInspection === undefined) delete inspectionOwner.__nekoClickGuideWindowInspection;
                    else inspectionOwner.__nekoClickGuideWindowInspection = oldInspection;
                    inspectionOwner.dispatchEvent(new inspectionOwner.CustomEvent('neko:click-guide-window-inspection'));
                }
            } catch (_) { /* the child may have navigated to another origin */ }
            inspectionOwner = null;
            card = layer = mask = highlight = null;
            links = [];
        }
        function remember(value) {
            if (!value || value === root || value.closed || stopped || children.includes(value)) return;
            clearView();
            children.push(value);
            child = value;
        }
        function allowCloseConfirmation() {
            closing.add(child);
            highlight?.update(null);
            mask?.update({ left: 0, top: 0, right: child.innerWidth, bottom: child.innerHeight },
                child.innerWidth, child.innerHeight, 'rect', 0);
            if (card) card.querySelector('p').textContent = copy.closePending || copy.body;
        }
        function requestClose() {
            allowCloseConfirmation();
            try {
                const button = api.resolveTarget(copy.closeSelector, child.document);
                if (button) { button.click(); return; }
            } catch (_) { /* cross-origin page keeps its native close behavior */ }
            child?.close();
        }
        function watchOpeners(owner) {
            const wrappers = wrappedWindows.get(owner) || {};
            wrappedWindows.set(owner, wrappers);
            for (const name of ['open', 'openOrFocusWindow']) {
                const original = owner[name];
                if (typeof original !== 'function' || original === wrappers[name]) continue;
                const wrapped = function (...args) {
                    const result = original.apply(this, args);
                    remember(result);
                    return result;
                };
                owner[name] = wrappers[name] = wrapped;
                removers.push(() => { if (owner[name] === wrapped) owner[name] = original; });
            }
        }
        watchOpeners(root);
        function inspect() {
            if (!child || stopped) return;
            if (child.closed) {
                clearView();
                while (children.at(-1)?.closed) children.pop();
                child = children.at(-1);
                if (!child) { onReturn(); return; }
            }
            // A shared named-window handle can only request focus and report
            // closure; it does not expose a page or a working close method.
            if (child.window !== child) return;
            // Cross-origin pages retain their own security boundary. Their tab
            // can still be closed normally, or from the parent guide's button.
            try {
                const doc = child.document;
                if (child.location.origin !== root.location.origin || !doc.body) return;
                watchOpeners(child);
                if (!card?.isConnected) {
                    clearView();
                    inspectionOwner = child;
                    oldInspection = child.__nekoClickGuideWindowInspection;
                    child.__nekoClickGuideWindowInspection = true;
                    child.dispatchEvent(new child.CustomEvent('neko:click-guide-window-inspection'));
                    for (const file of ['/static/css/yui-guide.css', '/static/tutorial/click-guide/click-guide.css']) {
                        const link = doc.createElement('link'); link.rel = 'stylesheet';
                        link.href = new URL(file, root.location.href).href; doc.head.append(link); links.push(link);
                    }
                    layer = doc.createElement('div'); layer.className = 'click-guide-layer';
                    mask = api.createMask(layer); highlight = api.createHighlight(layer);
                    card = doc.createElement('section');
                    card.className = 'click-guide-card click-guide-window-return';
                    card.setAttribute('role', 'dialog');
                    const title = doc.createElement('h2'); title.textContent = copy.title;
                    const body = doc.createElement('p'); body.textContent = copy.body;
                    const actions = doc.createElement('div'); actions.className = 'click-guide-actions';
                    const close = doc.createElement('button'); close.className = 'click-guide-next';
                    close.textContent = copy.nextLabel; close.onclick = requestClose;
                    const skip = doc.createElement('button'); skip.textContent = labels.skip;
                    skip.onclick = () => root.dispatchEvent(new CustomEvent('neko:click-guide-window-skip'));
                    actions.append(skip, close); card.append(title, body, actions); layer.append(card); doc.body.append(layer);
                    const observeClose = event => {
                        const button = api.resolveTarget(copy.closeSelector, doc);
                        if (button?.contains(event.target)) allowCloseConfirmation();
                    };
                    doc.addEventListener('click', observeClose, true);
                    removeCloseListener = () => doc.removeEventListener('click', observeClose, true);
                }
                const button = closing.has(child) ? null : api.resolveTarget(copy.closeSelector, doc);
                const bounds = button?.getBoundingClientRect();
                const rect = bounds && { left: bounds.left - 6, top: bounds.top - 6,
                    right: bounds.right + 6, bottom: bounds.bottom + 6 };
                const shape = highlight.update(rect, undefined, button, false, !!button);
                mask.update(rect || { left: 0, top: 0, right: child.innerWidth, bottom: child.innerHeight },
                    child.innerWidth, child.innerHeight, shape, api.focusRadius(rect, shape, button));
                card.querySelector('p').textContent = closing.has(child) ? (copy.closePending || copy.body) : copy.body;
            } catch (_) { /* browser-enforced cross-origin access */ }
        }
        const timer = root.setInterval(inspect, 100);
        signal.addEventListener('abort', () => {
            stopped = true; root.clearInterval(timer); clearView();
            removers.reverse().forEach(remove => {
                try { remove(); } catch (_) { /* a nested page may have navigated cross-origin */ }
            });
            children.length = 0;
            child = null;
        }, { once: true });
        return {
            view() {
                // Keep the return view until inspection has resumed the parent;
                // a closed nested page must not look like the end of the lesson.
                if (!child) return null;
                if (child.window !== child) {
                    const shared = child;
                    return { ...copy, body: copy.manualClose, nextLabel: copy.returnToPage,
                        target: null, resume: true, onAdvance() { shared.focus(); } };
                }
                return { ...copy, target: null, resume: children.length > 1, onAdvance: async ({ signal: currentSignal }) => {
                    const closingChild = child;
                    if (closingChild && !closingChild.closed) requestClose();
                    await api.waitUntil(() => !closingChild || closingChild.closed, currentSignal, 8000);
                } };
            }
        };
    };
})(window);
