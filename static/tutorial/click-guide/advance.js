(function (root) {
    'use strict';
    const api = root.NekoClickGuide = root.NekoClickGuide || {};
    api.waitUntil = function (predicate, signal, timeout = 6000) {
        return new Promise((resolve, reject) => {
            const started = Date.now();
            let timer;
            function finish(error) {
                root.clearTimeout(timer);
                signal.removeEventListener('abort', abort);
                error ? reject(error) : resolve();
            }
            function abort() { finish(new DOMException('Aborted', 'AbortError')); }
            function check() {
                try {
                    if (signal.aborted) return abort();
                    if (predicate()) return finish();
                    if (Date.now() - started >= timeout) return finish(new Error('target_not_ready'));
                    timer = root.setTimeout(check, 50);
                } catch (error) { finish(error); }
            }
            signal.addEventListener('abort', abort, { once: true });
            check();
        });
    };
    api.onTargetClick = function (doc, target, signal, callback, capture) {
        // Observe real actions, or consume a guide-only click before the control handles it.
        let pressedTarget = null;
        let pressedContext;
        doc.addEventListener('pointerdown', event => {
            const element = api.resolveTarget(target, doc);
            pressedTarget = element?.contains(event.target) ? element : null;
            pressedContext = pressedTarget && capture?.();
        }, { capture: true, signal });
        doc.addEventListener('pointercancel', () => { pressedTarget = pressedContext = null; }, { capture: true, signal });
        const listener = event => {
            // Some controls change their selector during pointerup, before click is dispatched.
            const currentTarget = api.resolveTarget(target, doc);
            const element = currentTarget || pressedTarget;
            const context = currentTarget ? capture?.() : pressedContext;
            pressedTarget = null;
            pressedContext = null;
            if (element && element.contains(event.target) && !element.disabled
                && (!context?.acceptTargetClick || context.acceptTargetClick(event, element))) {
                if (context?.consumeTargetClick) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                }
                root.setTimeout(() => { if (!signal.aborted) callback(event, context); }, 0);
            }
        };
        doc.addEventListener('click', listener, { capture: true, signal });
    };
    api.onTargetKey = function (doc, target, signal, key, ready, callback) {
        doc.addEventListener('keydown', event => {
            const element = api.resolveTarget(target, doc);
            if (event.key !== key || event.isComposing || event.keyCode === 229 || event.shiftKey || event.target !== element
                || element.disabled || element.readOnly || !ready(element)) return;
            // Let the input's own key handler submit before checking its resulting state.
            root.setTimeout(() => { if (!signal.aborted) callback(); }, 0);
        }, { capture: true, signal });
    };
})(window);
