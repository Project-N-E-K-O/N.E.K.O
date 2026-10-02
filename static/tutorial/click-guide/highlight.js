(function (root) {
    'use strict';
    const api = root.NekoClickGuide = root.NekoClickGuide || {};
    api.focusRadius = function (rect, shape, element, radiusMode) {
        if (!rect || shape === 'circle') return 0;
        if (radiusMode === 'target' && element) {
            const radius = parseFloat(element.ownerDocument.defaultView.getComputedStyle(element).borderTopLeftRadius);
            if (Number.isFinite(radius)) return Math.min(radius, (rect.right - rect.left) / 2, (rect.bottom - rect.top) / 2);
        }
        return Math.min(12, (rect.right - rect.left) / 5, (rect.bottom - rect.top) / 4);
    };
    api.createHighlight = function (parent) {
        const doc = parent.ownerDocument;
        const ring = doc.createElement('div');
        ring.className = 'click-guide-highlight yui-guide-spotlight-frame';
        ring.setAttribute('aria-hidden', 'true');
        for (const [tag, className] of [
            ['div', 'yui-guide-spotlight-chrome'],
            ['span', 'yui-guide-spotlight-sweep'],
            ['div', 'yui-guide-spotlight-circle-skin'],
            ['div', 'yui-guide-spotlight-decoration yui-guide-spotlight-ear-left'],
            ['div', 'yui-guide-spotlight-decoration yui-guide-spotlight-ear-right'],
            ['div', 'yui-guide-spotlight-decoration yui-guide-spotlight-paw']
        ]) {
            const decoration = doc.createElement(tag);
            decoration.className = className;
            ring.appendChild(decoration);
        }
        const cursor = doc.createElement('div');
        cursor.className = 'click-guide-ghost-cursor';
        ring.appendChild(cursor);
        parent.appendChild(ring);
        return {
            setTransition(enabled) { ring.classList.toggle('animate-focus', enabled); },
            update(rect, shape, element, catEars, clickable, radiusMode, cursorOnTarget, cursorOffset) {
                ring.hidden = !rect;
                ring.classList.toggle('is-visible', !!rect);
                ring.classList.toggle('is-click-step', !!clickable);
                ring.classList.toggle('cursor-on-target', !!cursorOnTarget);
                if (!rect) return null;
                const style = element && element.ownerDocument.defaultView.getComputedStyle(element);
                const radius = style && parseFloat(style.borderTopLeftRadius);
                const isCircle = shape === 'circle' || (shape !== 'rect'
                    && Math.abs((rect.right - rect.left) - (rect.bottom - rect.top)) < 12
                    && element.offsetWidth > 0
                    && radius >= Math.min(element.offsetWidth, element.offsetHeight) / 2 - 2);
                ring.classList.toggle('is-circle-image', !!isCircle);
                ring.classList.toggle('is-compact', !isCircle && (rect.right - rect.left < 100 || rect.bottom - rect.top < 32));
                ring.classList.toggle('has-cat-ears', !isCircle && !!catEars);
                ring.classList.toggle('flip-cursor', rect.right > doc.defaultView.innerWidth - 54);
                cursor.style.left = cursorOffset ? cursorOffset.x - 16 + 'px' : '';
                cursor.style.top = cursorOffset ? cursorOffset.y - 16 + 'px' : '';
                Object.assign(ring.style, {
                    left: rect.left + 'px', top: rect.top + 'px',
                    width: (rect.right - rect.left) + 'px', height: (rect.bottom - rect.top) + 'px',
                    borderRadius: isCircle ? '50%' : api.focusRadius(rect, 'rect', element, radiusMode) + 'px'
                });
                return isCircle ? 'circle' : 'rect';
            },
            destroy() { ring.remove(); }
        };
    };
})(window);
