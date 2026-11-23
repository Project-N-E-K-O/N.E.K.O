/*
 * 长按起拖的手感：光标处的空心进度圆环 + 拖拽时跟着指针的"选项卡"。
 *
 * 单独成文件有两个原因：表情制作页和状态表演台都要这套手势（以前各写了一份
 * 320ms/8px 的常量，改一处漏一处）；而页面内的那份要有真模板才进得去测试，
 * 独立模块可以直接拿合成事件驱动。
 */
(function (root, factory) {
    'use strict';

    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
        return;
    }

    root.NekoEmoteDragHold = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const HOLD_MS = 320;
    const MOVE_SLOP = 8;
    const RING_SIZE = 34;
    // 圆环不在按下瞬间出现：普通点击不该闪一下环，那会让人以为点坏了。
    const RING_DELAY_MS = 110;

    function prefersReducedMotion() {
        return typeof matchMedia === 'function'
            && matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    /**
     * 从一次 pointerdown 起监视长按。
     * 圆环是空心的（只画描边不画心），填满＝起拖；提前移开或松手都会取消。
     */
    function armHold(event, handlers) {
        const done = typeof handlers.onComplete === 'function' ? handlers.onComplete : function () {};
        const cancelledBy = typeof handlers.onCancel === 'function' ? handlers.onCancel : function () {};
        const holdMs = Number(handlers.holdMs) > 0 ? Number(handlers.holdMs) : HOLD_MS;
        const startX = event.clientX;
        const startY = event.clientY;
        const pointerId = event.pointerId;
        let latest = event;
        let timer = 0;
        let ringAppearTimer = 0;
        let frame = 0;
        let ring = null;
        let held = false;
        let finished = false;

        if (event.pointerType === 'mouse' && event.button !== 0) {
            cancelledBy('button');
            return { cancel: function () {} };
        }

        const killNativeDrag = (dragEvent) => {
            // 卡片里有 <img>，浏览器会把它当成原生拖拽素材：一旦接管，
            // 我们的 pointermove 就断了（ghost 刚建就被移除），而且它会自动滚动列表。
            dragEvent.preventDefault();
            if (dragEvent.dataTransfer) dragEvent.dataTransfer.effectAllowed = 'move';
        };
        const detach = () => {
            window.clearTimeout(timer);
            if (ringAppearTimer) window.clearTimeout(ringAppearTimer);
            if (frame) cancelAnimationFrame(frame);
            window.removeEventListener('pointermove', move, true);
            window.removeEventListener('pointerup', up, true);
            window.removeEventListener('pointercancel', cancel, true);
            window.removeEventListener('dragstart', killNativeDrag, true);
        };
        const move = (moveEvent) => {
            if (pointerId != null && moveEvent.pointerId != null && moveEvent.pointerId !== pointerId) return;
            if (!held && Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) > MOVE_SLOP) {
                cancel();
                return;
            }
            if (ring) positionRing(ring, moveEvent.clientX, moveEvent.clientY);
            latest = moveEvent;
        };
        const up = (upEvent) => {
            if (pointerId != null && upEvent?.pointerId != null && upEvent.pointerId !== pointerId) return;
            if (finished) return;
            finished = true;
            held = false;
            detach();
            hideRing(ring);
            ring = null;
            cancelledBy('release');
        };
        const cancel = (cancelEvent) => {
            if (pointerId != null && cancelEvent?.pointerId != null && cancelEvent.pointerId !== pointerId) return;
            if (finished) return;
            finished = true;
            held = false;
            detach();
            hideRing(ring);
            ring = null;
            cancelledBy('abort');
        };

        timer = window.setTimeout(() => {
            if (finished) return;
            held = true;
            // 先让圆环"咔"一下再开始拖：没有这一步，长按到点这件事只有数字在动
            completeRing(ring, () => {
                if (finished) return;
                finished = true;
                detach();
                ring = null;
                done({ clientX: latest.clientX, clientY: latest.clientY });
            });
        }, holdMs);

        window.addEventListener('dragstart', killNativeDrag, true);
        ringAppearTimer = window.setTimeout(() => {
            ringAppearTimer = 0;
            if (finished) return;
            ring = createRing(startX, startY, holdMs, RING_DELAY_MS);
        }, Number(handlers.ringDelayMs) >= 0 ? Number(handlers.ringDelayMs) : RING_DELAY_MS);
        window.addEventListener('pointermove', move, true);
        window.addEventListener('pointerup', up, true);
        window.addEventListener('pointercancel', cancel, true);
        return { cancel, isHeld: () => held };
    }

    function createRing(x, y, holdMs, appearedAfter) {
        if (typeof document === 'undefined' || !document.body) return null;
        const radius = (RING_SIZE - 5) / 2;
        const circumference = 2 * Math.PI * radius;
        const ring = document.createElement('span');
        ring.className = 'pack-hold-ring';
        // 圆心留空：填满的只是那一圈描边
        ring.innerHTML = '<svg viewBox="0 0 ' + RING_SIZE + ' ' + RING_SIZE + '" aria-hidden="true">'
            + '<circle class="pack-hold-track" cx="' + RING_SIZE / 2 + '" cy="' + RING_SIZE / 2
            + '" r="' + radius + '" fill="none" stroke-width="3"></circle>'
            + '<circle class="pack-hold-progress" cx="' + RING_SIZE / 2 + '" cy="' + RING_SIZE / 2
            + '" r="' + radius + '" fill="none" stroke-width="3" stroke-linecap="round" '
            + 'stroke-dasharray="' + circumference + '" stroke-dashoffset="' + circumference + '"'
            + ' transform="rotate(-90 ' + RING_SIZE / 2 + ' ' + RING_SIZE / 2 + ')"></circle>'
            + '</svg>';
        ring.dataset.circumference = String(circumference);
        ring.dataset.holdMs = String(Number(holdMs) > 0 ? Number(holdMs) : HOLD_MS);
        ring.dataset.appearedAfter = String(Number(appearedAfter) || 0);
        positionRing(ring, x, y);
        document.body.appendChild(ring);
        requestAnimationFrame(() => {
            const progress = ring.querySelector('.pack-hold-progress');
            const total = Number(ring.dataset.holdMs || HOLD_MS);
            const remain = Math.max(60, total - Number(ring.dataset.appearedAfter || 0));
            const started = performance.now();
            const tick = (now) => {
                if (!ring.isConnected) return;
                const ratio = Math.min(1, (now - started) / remain);
                if (progress) {
                    progress.setAttribute('stroke-dashoffset',
                        String(circumference * (1 - ratio)));
                }
                if (ratio < 1) requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
        });
        return ring;
    }

    function positionRing(ring, x, y) {
        if (!ring) return;
        ring.style.left = `${x}px`;
        ring.style.top = `${y}px`;
    }

    function completeRing(ring, after) {
        if (!ring) { after(); return; }
        ring.classList.add('is-full');
        const settle = () => { ring.remove(); after(); };
        if (prefersReducedMotion()) { settle(); return; }
        window.setTimeout(settle, 170);
    }

    function hideRing(ring) {
        if (!ring) return;
        ring.classList.add('is-aborted');
        window.setTimeout(() => ring.remove(), prefersReducedMotion() ? 0 : 110);
    }

    /**
     * 拖出来的东西长得像它来自的那张选项卡：缩略图 + 名字。
     * 只有一行文字的 ghost 会让人以为自己在拖空气。
     */
    /**
     * 起拖之后，紧随其后的那一次 click 不算"用户想点这一项"。
     *
     * 关键是计时起点：必须从**松手那一刻**算，不能从起拖那一刻算。
     * 从起拖算的话，用户按住不动多悬停一会儿再松手，抑制窗口早就过期了，
     * 松手后紧跟的 click 就会被当成一次选择 —— 这个 bug 在主界面和动作库里
     * 各出现过一次。
     */
    function createClickGuard(windowMs) {
        const span = Number(windowMs) > 0 ? Number(windowMs) : 600;
        let until = 0;
        let armed = false;
        let source = null;
        let pointerId = null;
        let clearTimer = 0;

        const reset = () => {
            until = 0;
            source = null;
            window.clearTimeout(clearTimer);
            window.removeEventListener('pointerdown', reset, true);
        };

        const stamp = (event) => {
            if (!armed) return;
            if (pointerId != null && event?.pointerId != null && event.pointerId !== pointerId) return;
            armed = false;
            window.removeEventListener('pointerup', stamp, true);
            window.removeEventListener('pointercancel', stamp, true);
            until = Date.now() + span;
            window.addEventListener('pointerdown', reset, { once: true, capture: true });
            clearTimer = window.setTimeout(reset, span);
        };

        return {
            // 起拖时调用；松手（或取消）时才真正开始计时
            arm(element = null, owner = null) {
                if (armed) return;
                reset();
                source = element;
                pointerId = owner;
                armed = true;
                window.addEventListener('pointerup', stamp, { capture: true });
                window.addEventListener('pointercancel', stamp, { capture: true });
            },
            shouldSwallow(event) {
                if (event?.detail === 0) return false; // Keyboard activation is a new action.
                if (source && event && event.currentTarget !== source && !source.contains(event.target)) return false;
                if (armed) return true;      // 还按着/还在拖，当然不算点击
                return Date.now() < until;
            },
            _releaseForTest: stamp,
        };
    }

    /**
     * 让手里的东西跟着甩：横向速度换成倾角，停下后自己回正。
     * 不做这件事，卡片像钉在光标上的一片贴图。
     */
    function createGhostSwing(ghost) {
        let tilt = 0;
        let target = 0;
        let lastX = null;
        let raf = 0;
        let alive = true;
        const reduce = prefersReducedMotion();

        const step = () => {
            if (!alive) return;
            tilt += (target - tilt) * 0.28;
            target *= 0.82;
            if (Math.abs(tilt) < 0.05 && Math.abs(target) < 0.05) tilt = 0;
            ghost.style.setProperty('--drag-tilt', `${tilt.toFixed(2)}deg`);
            raf = requestAnimationFrame(step);
        };

        return {
            moveTo(x, y) {
                if (lastX !== null && !reduce) {
                    const dx = x - lastX;
                    target = Math.max(-15, Math.min(15, target + dx * 0.9));
                }
                lastX = x;
                ghost.style.left = `${x}px`;
                ghost.style.top = `${y}px`;
            },
            start() { if (!raf) raf = requestAnimationFrame(step); },
            stop() {
                alive = false;
                if (raf) cancelAnimationFrame(raf);
                raf = 0;
            },
            tilt: () => tilt,
        };
    }

    function buildGhost(payload, sourceElement) {
        const ghost = document.createElement('div');
        ghost.className = 'pack-drag-ghost';
        const thumb = document.createElement('span');
        thumb.className = 'pack-drag-thumb';
        const icon = sourceElement && sourceElement.querySelector
            ? (sourceElement.querySelector('img') || sourceElement.querySelector('canvas'))
            : null;
        // 优先用 payload 直接给的图：列表卡片现在用 CSS 背景，不再每卡一个 <img>
        const direct = String((payload && payload.thumbUrl) || '');
        if (direct) {
            const image = document.createElement('img');
            image.src = direct;
            image.alt = '';
            thumb.appendChild(image);
        } else if (icon && icon.tagName === 'IMG' && icon.getAttribute('src')) {
            const image = document.createElement('img');
            image.src = icon.getAttribute('src');
            image.alt = '';
            thumb.appendChild(image);
        } else if (icon && icon.tagName === 'CANVAS') {
            const copy = document.createElement('img');
            try {
                copy.src = icon.toDataURL('image/png');
                copy.alt = '';
                thumb.appendChild(copy);
            } catch (_) {
                thumb.classList.add('is-blank');
            }
        } else {
            thumb.classList.add('is-blank');
        }
        const label = document.createElement('span');
        label.className = 'pack-drag-label';
        label.textContent = String((payload && payload.label) || '');
        ghost.append(thumb, label);
        return ghost;
    }

    return Object.freeze({
        HOLD_MS, RING_DELAY_MS, MOVE_SLOP, RING_SIZE,
        armHold, buildGhost, createRing, createGhostSwing, createClickGuard,
        prefersReducedMotion,
    });
}));
