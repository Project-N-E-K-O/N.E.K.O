(function () {
    'use strict';

    var OVERLAY_ID = 'neko-avatar-drop-overlay';
    var STYLE_ID = 'neko-avatar-drop-intake-style';
    var activeTarget = null;
    var lastTargetAt = 0;
    var hideTimer = 0;
    var busy = false;
    var bubbleRaf = 0;
    var bubblePlaced = false;
    var bubbleX = 0;
    var bubbleY = 0;
    var bubbleTargetX = 0;
    var bubbleTargetY = 0;

    function tr(key, params, fallback) {
        if (typeof window.t === 'function') {
            var value = window.t(key, params || {});
            if (value && value !== key) return value;
        }
        var text = fallback || '';
        Object.keys(params || {}).forEach(function (name) {
            text = text.replace(new RegExp('\\{\\{' + name + '\\}\\}', 'g'), String(params[name]));
        });
        return text;
    }

    function dataTransferHasFiles(dataTransfer) {
        if (!dataTransfer) return false;
        if (dataTransfer.files && dataTransfer.files.length > 0) return true;
        if (dataTransfer.items && dataTransfer.items.length > 0) {
            return Array.from(dataTransfer.items).some(function (item) {
                return item && item.kind === 'file';
            });
        }
        return Array.from(dataTransfer.types || []).some(function (type) {
            return /^files$/i.test(String(type || ''));
        });
    }

    function getFilesFromDataTransfer(dataTransfer) {
        if (!dataTransfer) return [];
        var files = Array.from(dataTransfer.files || []);
        if (files.length > 0) return files;
        return Array.from(dataTransfer.items || [])
            .filter(function (item) {
                return item && item.kind === 'file' && typeof item.getAsFile === 'function';
            })
            .map(function (item) { return item.getAsFile(); })
            .filter(function (file) { return file instanceof File; });
    }

    function normalizeRect(rect) {
        if (!rect) return null;
        var left = Number(rect.left != null ? rect.left : rect.x);
        var top = Number(rect.top != null ? rect.top : rect.y);
        var width = Number(rect.width);
        var height = Number(rect.height);
        var right = Number(rect.right);
        var bottom = Number(rect.bottom);

        if (!Number.isFinite(width) && Number.isFinite(left) && Number.isFinite(right)) {
            width = right - left;
        }
        if (!Number.isFinite(height) && Number.isFinite(top) && Number.isFinite(bottom)) {
            height = bottom - top;
        }
        if (!Number.isFinite(right) && Number.isFinite(left) && Number.isFinite(width)) {
            right = left + width;
        }
        if (!Number.isFinite(bottom) && Number.isFinite(top) && Number.isFinite(height)) {
            bottom = top + height;
        }
        if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(width) || !Number.isFinite(height)) {
            return null;
        }
        if (width < 24 || height < 24) return null;

        return {
            left: left,
            top: top,
            right: right,
            bottom: bottom,
            width: width,
            height: height
        };
    }

    function inflateRect(rect, amount) {
        var pad = Number(amount || 0);
        return {
            left: rect.left - pad,
            top: rect.top - pad,
            right: rect.right + pad,
            bottom: rect.bottom + pad,
            width: rect.width + pad * 2,
            height: rect.height + pad * 2
        };
    }

    function pointInRect(x, y, rect) {
        return !!rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
    }

    function isVisibleElement(element) {
        if (!element) return false;
        var style = window.getComputedStyle(element);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
            return false;
        }
        var rect = normalizeRect(element.getBoundingClientRect());
        return !!rect;
    }

    function isUsefulVisualRect(rect) {
        if (!rect) return false;
        var viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        var viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
        if (viewportWidth > 0 && viewportHeight > 0
                && rect.width > viewportWidth * 0.92
                && rect.height > viewportHeight * 0.92) {
            return false;
        }
        return rect.width >= 48 && rect.height >= 48;
    }

    function getManagerBounds(manager, methodName) {
        try {
            if (manager && typeof manager[methodName] === 'function') {
                return normalizeRect(manager[methodName]());
            }
        } catch (_) {}
        return null;
    }

    function getActiveAnchorBounds() {
        var helper = window.avatarReactionBubble;
        if (!helper || typeof helper.getActiveAvatarBubbleAnchor !== 'function') return null;
        try {
            var anchor = helper.getActiveAvatarBubbleAnchor();
            var rect = normalizeRect(anchor && anchor.bounds);
            if (rect && isUsefulVisualRect(rect)) {
                return { type: anchor.type || 'avatar', rect: rect };
            }
        } catch (_) {}
        return null;
    }

    function getFallbackBounds() {
        var candidates = [
            { type: 'mmd', id: 'mmd-container', manager: window.mmdManager },
            { type: 'vrm', id: 'vrm-container', manager: window.vrmManager },
            { type: 'live2d', id: 'live2d-container', manager: window.live2dManager }
        ];

        for (var i = 0; i < candidates.length; i += 1) {
            var candidate = candidates[i];
            var container = document.getElementById(candidate.id);
            if (!isVisibleElement(container)) continue;
            var managerRect = getManagerBounds(candidate.manager, 'getModelScreenBounds');
            if (managerRect && isUsefulVisualRect(managerRect)) {
                return { type: candidate.type, rect: managerRect };
            }
        }

        var pngImage = document.querySelector('#pngtuber-container .pngtuber-image');
        if (isVisibleElement(pngImage)) {
            var pngRect = normalizeRect(pngImage.getBoundingClientRect());
            if (pngRect && isUsefulVisualRect(pngRect)) {
                return { type: 'pngtuber', rect: pngRect };
            }
        }

        return null;
    }

    function getDropTargetAtPoint(x, y) {
        var target = getActiveAnchorBounds() || getFallbackBounds();
        if (!target || !target.rect) return null;
        var hitRect = inflateRect(target.rect, 28);
        if (!pointInRect(x, y, hitRect)) return null;
        return { type: target.type, rect: hitRect, visualRect: target.rect };
    }

    function getTargetElement(target) {
        if (!target) return null;
        if (target.nodeType === 1) return target;
        return target.parentElement || null;
    }

    function isChatSurfaceDropTarget(event) {
        if (!event || !dataTransferHasFiles(event.dataTransfer)) return false;
        var targetElement = getTargetElement(event.target);
        if (!targetElement) return false;

        var shell = document.getElementById('react-chat-window-shell');
        if (shell && shell.contains(targetElement)) return true;
        var inputArea = document.getElementById('text-input-area');
        if (inputArea && inputArea.contains(targetElement)) return true;
        var textInput = document.getElementById('textInputBox');
        if (textInput && textInput.contains(targetElement)) return true;

        if (typeof targetElement.closest !== 'function') return false;
        return !!targetElement.closest([
            '#react-chat-window-root',
            '#react-chat-window-shell',
            '#text-input-area',
            '#textInputBox',
            '#screenshot-thumbnail-container',
            '#screenshots-list',
            '.compact-chat-surface-frame',
            '.compact-chat-surface-shell',
            '.composer-panel',
            '.composer-input-shell',
            '.composer-input',
            '.composer-attachments',
            '[data-compact-geometry-owner="surface"]'
        ].join(', '));
    }

    function ensureStyle() {
        if (document.getElementById(STYLE_ID)) return;
        var style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = [
            '#' + OVERLAY_ID + ' {',
            '  position: fixed;',
            '  left: 0;',
            '  top: 0;',
            '  pointer-events: none;',
            '  z-index: 2147483000;',
            '  box-sizing: border-box;',
            '  opacity: 0;',
            '  transform: translate3d(-9999px, -9999px, 0);',
            '  transition: opacity 120ms ease;',
            '  will-change: transform, opacity;',
            '}',
            '#' + OVERLAY_ID + '.is-visible {',
            '  opacity: 1;',
            '}',
            '#' + OVERLAY_ID + ' .avatar-drop-label {',
            '  position: relative;',
            '  display: block;',
            '  max-width: min(300px, 72vw);',
            '  padding: 8px 12px;',
            '  border-radius: 14px;',
            '  background: rgba(32, 36, 48, 0.9);',
            '  color: #fff;',
            '  font: 600 13px/1.3 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;',
            '  white-space: normal;',
            '  box-shadow: 0 8px 20px rgba(20, 24, 40, 0.24);',
            '  backdrop-filter: blur(12px);',
            '  -webkit-backdrop-filter: blur(12px);',
            '}',
            '#' + OVERLAY_ID + ' .avatar-drop-label::before {',
            '  content: "";',
            '  position: absolute;',
            '  left: -5px;',
            '  top: 50%;',
            '  width: 10px;',
            '  height: 10px;',
            '  transform: translateY(-50%) rotate(45deg);',
            '  background: rgba(32, 36, 48, 0.9);',
            '}'
        ].join('\n');
        document.head.appendChild(style);
    }

    function getOverlay() {
        ensureStyle();
        var overlay = document.getElementById(OVERLAY_ID);
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.id = OVERLAY_ID;
        overlay.setAttribute('aria-hidden', 'true');
        var label = document.createElement('div');
        label.className = 'avatar-drop-label';
        overlay.appendChild(label);
        document.body.appendChild(overlay);
        return overlay;
    }

    function applyBubblePosition(overlay, x, y) {
        overlay.style.transform = 'translate3d(' + Math.round(x) + 'px, ' + Math.round(y) + 'px, 0)';
    }

    function tickBubbleFollow() {
        bubbleRaf = 0;
        var overlay = document.getElementById(OVERLAY_ID);
        if (!overlay || !overlay.classList.contains('is-visible')) return;

        bubbleX += (bubbleTargetX - bubbleX) * 0.24;
        bubbleY += (bubbleTargetY - bubbleY) * 0.24;
        if (Math.abs(bubbleTargetX - bubbleX) < 0.5) bubbleX = bubbleTargetX;
        if (Math.abs(bubbleTargetY - bubbleY) < 0.5) bubbleY = bubbleTargetY;
        applyBubblePosition(overlay, bubbleX, bubbleY);

        if (bubbleX !== bubbleTargetX || bubbleY !== bubbleTargetY) {
            bubbleRaf = window.requestAnimationFrame(tickBubbleFollow);
        }
    }

    function scheduleBubbleFollow() {
        if (!bubbleRaf) {
            bubbleRaf = window.requestAnimationFrame(tickBubbleFollow);
        }
    }

    function setBubbleTarget(event, overlay) {
        var viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        var viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
        var width = overlay.offsetWidth || 180;
        var height = overlay.offsetHeight || 36;
        var rawX = Number(event && event.clientX) + 18;
        var rawY = Number(event && event.clientY) - Math.round(height * 0.5);
        if (!Number.isFinite(rawX)) rawX = 0;
        if (!Number.isFinite(rawY)) rawY = 0;

        bubbleTargetX = Math.min(Math.max(8, rawX), Math.max(8, viewportWidth - width - 8));
        bubbleTargetY = Math.min(Math.max(8, rawY), Math.max(8, viewportHeight - height - 8));
        if (!bubblePlaced) {
            bubblePlaced = true;
            bubbleX = bubbleTargetX;
            bubbleY = bubbleTargetY;
            applyBubblePosition(overlay, bubbleX, bubbleY);
        }
        scheduleBubbleFollow();
    }

    function showOverlay(target, labelText, event) {
        window.clearTimeout(hideTimer);
        var overlay = getOverlay();
        var label = overlay.querySelector('.avatar-drop-label');
        if (label) label.textContent = labelText;
        overlay.classList.add('is-visible');
        setBubbleTarget(event, overlay);
    }

    function hideOverlayNow() {
        var overlay = document.getElementById(OVERLAY_ID);
        if (overlay) overlay.classList.remove('is-visible');
        activeTarget = null;
        bubblePlaced = false;
        if (bubbleRaf) {
            window.cancelAnimationFrame(bubbleRaf);
            bubbleRaf = 0;
        }
    }

    function hideOverlay(delay) {
        window.clearTimeout(hideTimer);
        var wait = Math.max(0, delay || 0);
        if (wait <= 0) {
            hideOverlayNow();
            return;
        }
        hideTimer = window.setTimeout(hideOverlayNow, wait);
    }

    function showToast(message, duration) {
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, duration || 2600);
        }
    }

    function isHomeTutorialInteractionLocked() {
        try {
            return typeof window.isNekoHomeTutorialInteractionLocked === 'function'
                && window.isNekoHomeTutorialInteractionLocked() === true;
        } catch (_) {
            return false;
        }
    }

    function getEventTarget(event, options) {
        options = options || {};
        if (!event || !dataTransferHasFiles(event.dataTransfer)) return null;
        if (isChatSurfaceDropTarget(event)) return null;
        var target = getDropTargetAtPoint(event.clientX, event.clientY);
        if (target) {
            activeTarget = target;
            lastTargetAt = Date.now();
            return target;
        }
        if (options.allowRecentTarget === true && activeTarget && Date.now() - lastTargetAt < 180) {
            return activeTarget;
        }
        return null;
    }

    function handleDragOver(event) {
        if (isChatSurfaceDropTarget(event)) {
            hideOverlay(0);
            return;
        }
        if (busy) {
            if (event && dataTransferHasFiles(event.dataTransfer)) {
                event.preventDefault();
                event.stopPropagation();
                if (event.dataTransfer) {
                    event.dataTransfer.dropEffect = 'none';
                }
            }
            return;
        }
        var target = getEventTarget(event);
        if (!target) {
            hideOverlay(0);
            return;
        }

        event.preventDefault();
        event.stopPropagation();
        if (event.dataTransfer) {
            event.dataTransfer.dropEffect = isHomeTutorialInteractionLocked() ? 'none' : 'copy';
        }
        showOverlay(target, tr('app.avatarDropOverlay', {}, 'Release to hand over files'), event);
    }

    async function handleDrop(event) {
        if (isChatSurfaceDropTarget(event)) {
            hideOverlay(0);
            return;
        }
        if (busy) {
            if (event && dataTransferHasFiles(event.dataTransfer)) {
                event.preventDefault();
                event.stopPropagation();
            }
            return;
        }
        var target = getEventTarget(event, { allowRecentTarget: true });
        if (!target) return;

        event.preventDefault();
        event.stopPropagation();

        if (isHomeTutorialInteractionLocked()) {
            hideOverlay(0);
            return;
        }

        var files = getFilesFromDataTransfer(event.dataTransfer);
        if (!files.length) {
            hideOverlay(0);
            return;
        }

        busy = true;
        showOverlay(target, tr('app.avatarDropParsing', {}, 'Reading dropped files...'), event);
        showToast(tr('app.avatarDropParsing', {}, 'Reading dropped files...'), 1800);

        try {
            var parser = window.NekoAvatarDropParser;
            if (!parser || typeof parser.parseFiles !== 'function') {
                throw new Error('parser_unavailable');
            }

            var result = await parser.parseFiles(files);
            var accepted = result && Array.isArray(result.accepted) ? result.accepted : [];
            var rejected = result && Array.isArray(result.rejected) ? result.rejected : [];

            if (!accepted.length && !rejected.length) {
                showToast(tr('app.avatarDropNoSupportedItems', {}, 'These files cannot be read yet'), 3600);
                return;
            }

            if (!window.appButtons || typeof window.appButtons.sendAvatarDropPayload !== 'function') {
                throw new Error('send_unavailable');
            }

            var sent = await window.appButtons.sendAvatarDropPayload({
                items: accepted,
                targetType: target.type,
                rejected: rejected
            });

            if (!sent) {
                showToast(tr('app.avatarDropReadFailed', {}, 'Failed to read files'), 3600);
                return;
            }

            if (!accepted.length && rejected.length > 0) {
                showToast(tr('app.avatarDropNoSupportedItems', {}, 'These files cannot be read yet'), 3600);
            } else if (rejected.length > 0) {
                showToast(tr(
                    'app.avatarDropPartial',
                    { accepted: accepted.length, rejected: rejected.length },
                    'Read {{accepted}} item(s), skipped {{rejected}}'
                ), 3600);
            } else {
                showToast(tr(
                    'app.avatarDropAccepted',
                    { count: accepted.length },
                    'Handed over {{count}} file(s)'
                ), 2600);
            }
        } catch (error) {
            console.warn('[AvatarDrop] read failed:', error && error.message ? error.message : error);
            showToast(tr('app.avatarDropReadFailed', {}, 'Failed to read files'), 3600);
        } finally {
            busy = false;
            hideOverlay(120);
        }
    }

    function init() {
        document.addEventListener('dragover', handleDragOver, true);
        document.addEventListener('drop', handleDrop, true);
        document.addEventListener('dragend', function () { hideOverlay(0); }, true);
        document.addEventListener('dragleave', function (event) {
            var viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
            var viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
            if (!event.relatedTarget
                    && (event.clientX <= 0 || event.clientY <= 0
                        || event.clientX >= viewportWidth || event.clientY >= viewportHeight)) {
                hideOverlay(0);
            }
        }, true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
