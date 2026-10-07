(function (root) {
    'use strict';
    const api = root.NekoClickGuide = root.NekoClickGuide || {};

    // One SVG paints the entire dim layer; transparent panes only block clicks outside the target.
    api.createMask = function (parent) {
        const doc = parent.ownerDocument;
        const svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.classList.add('click-guide-mask-visual');
        svg.setAttribute('aria-hidden', 'true');
        const defs = doc.createElementNS('http://www.w3.org/2000/svg', 'defs');
        const mask = doc.createElementNS('http://www.w3.org/2000/svg', 'mask');
        mask.id = 'click-guide-aperture';
        mask.setAttribute('maskUnits', 'userSpaceOnUse');
        mask.setAttribute('maskContentUnits', 'userSpaceOnUse');
        mask.style.maskType = 'luminance';
        const base = doc.createElementNS('http://www.w3.org/2000/svg', 'rect');
        base.setAttribute('fill', 'white');
        const rectangle = doc.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rectangle.setAttribute('fill', 'black');
        const circle = doc.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('fill', 'black');
        const secondaryRectangle = doc.createElementNS('http://www.w3.org/2000/svg', 'rect');
        secondaryRectangle.setAttribute('fill', 'black');
        const secondaryCircle = doc.createElementNS('http://www.w3.org/2000/svg', 'circle');
        secondaryCircle.setAttribute('fill', 'black');
        mask.append(base, rectangle, circle, secondaryRectangle, secondaryCircle);
        defs.appendChild(mask);
        const fill = doc.createElementNS('http://www.w3.org/2000/svg', 'rect');
        fill.setAttribute('fill', 'rgba(8, 13, 25, .72)');
        fill.setAttribute('mask', 'url(#click-guide-aperture)');
        svg.append(defs, fill);
        parent.appendChild(svg);
        const panes = Array.from({ length: 4 }, () => {
            const pane = doc.createElement('div');
            pane.className = 'click-guide-mask';
            pane.setAttribute('aria-hidden', 'true');
            parent.appendChild(pane);
            return pane;
        });
        return {
            update(rect, width, height, shape, radius, secondaryRect, secondaryShape, secondaryRadius) {
                const r = rect || { left: 0, top: 0, right: 0, bottom: 0 };
                const left = Math.max(0, Math.min(width, r.left));
                const right = Math.max(left, Math.min(width, r.right));
                const top = Math.max(0, Math.min(height, r.top));
                const bottom = Math.max(top, Math.min(height, r.bottom));
                svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
                mask.setAttribute('x', '0');
                mask.setAttribute('y', '0');
                mask.setAttribute('width', String(width));
                mask.setAttribute('height', String(height));
                for (const element of [base, fill]) {
                    element.setAttribute('width', String(width));
                    element.setAttribute('height', String(height));
                }
                rectangle.hidden = !rect || shape === 'circle';
                circle.hidden = !rect || shape !== 'circle';
                rectangle.style.display = rectangle.hidden ? 'none' : '';
                circle.style.display = circle.hidden ? 'none' : '';
                if (rect) {
                    rectangle.setAttribute('x', String(left));
                    rectangle.setAttribute('y', String(top));
                    rectangle.setAttribute('width', String(right - left));
                    rectangle.setAttribute('height', String(bottom - top));
                    rectangle.setAttribute('rx', String(radius || 0));
                    circle.setAttribute('cx', String((left + right) / 2));
                    circle.setAttribute('cy', String((top + bottom) / 2));
                    circle.setAttribute('r', String(Math.min(right - left, bottom - top) / 2));
                }
                secondaryRectangle.style.display = secondaryRect && secondaryShape !== 'circle' ? '' : 'none';
                secondaryCircle.style.display = secondaryRect && secondaryShape === 'circle' ? '' : 'none';
                if (secondaryRect) {
                    const x = Math.max(0, Math.min(width, secondaryRect.left));
                    const y = Math.max(0, Math.min(height, secondaryRect.top));
                    const w = Math.max(0, Math.min(width, secondaryRect.right) - x);
                    const h = Math.max(0, Math.min(height, secondaryRect.bottom) - y);
                    secondaryRectangle.setAttribute('x', String(x));
                    secondaryRectangle.setAttribute('y', String(y));
                    secondaryRectangle.setAttribute('width', String(w));
                    secondaryRectangle.setAttribute('height', String(h));
                    secondaryRectangle.setAttribute('rx', String(secondaryRadius || 0));
                    secondaryCircle.setAttribute('cx', String(x + w / 2));
                    secondaryCircle.setAttribute('cy', String(y + h / 2));
                    secondaryCircle.setAttribute('r', String(Math.min(w, h) / 2));
                }
                [[0, 0, width, top], [0, bottom, width, height - bottom],
                    [0, top, left, bottom - top], [right, top, width - right, bottom - top]]
                    .forEach(([x, y, w, h], index) => Object.assign(panes[index].style, {
                        left: x + 'px', top: y + 'px', width: w + 'px', height: h + 'px'
                    }));
            },
            destroy() { panes.forEach(pane => pane.remove()); svg.remove(); }
        };
    };
})(window);
