(function (root) {
    'use strict';
    const api = root.NekoClickGuide = root.NekoClickGuide || {};
    api.isElementVisible = function (element) {
        if (!element?.isConnected) return false;
        const view = element.ownerDocument.defaultView;
        for (let parent = element; parent && parent.nodeType === 1; parent = parent.parentElement) {
            const style = view.getComputedStyle(parent);
            if (style.display === 'none' || style.visibility === 'hidden'
                || style.opacity === '0' || parent.style.opacity === '0') return false;
        }
        return true;
    };
    api.resolveTarget = function (target, doc = root.document) {
        const view = doc.defaultView;
        const candidates = typeof target === 'string' ? doc.querySelectorAll(target)
            : [typeof target === 'function' ? target() : target];
        return Array.from(candidates).find(element => {
            if (!api.isElementVisible(element)) return false;
            const rect = element.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0
                && rect.right > 0 && rect.bottom > 0 && rect.left < view.innerWidth && rect.top < view.innerHeight;
        }) || null;
    };
    // rAF also follows CSS transforms, wheel rotation and model dragging.
    api.trackTarget = function (target, update, focusRect, padding = 6) {
        let frame;
        let stopped = false;
        function tick() {
            if (stopped) return;
            const element = api.resolveTarget(target);
            const r = element && (focusRect?.(element) || element.getBoundingClientRect());
            update(element, r && {
                left: Math.max(0, r.left - padding), top: Math.max(0, r.top - padding),
                right: Math.min(root.innerWidth, r.right + padding), bottom: Math.min(root.innerHeight, r.bottom + padding)
            });
            frame = root.requestAnimationFrame(tick);
        }
        tick();
        return () => { stopped = true; root.cancelAnimationFrame(frame); };
    };
})(window);
