(function (root) {
    'use strict';
    const api = root.NekoClickGuide;
    const tools = ['screenshot', 'avatar', 'translate', 'jukebox', 'import', 'export', 'galgame'];
    const find = selector => api.resolveTarget(selector);
    const t = key => root.t('clickGuide.' + key);
    let revealLock = () => {};
    const avatar = suffix => () => find(['live2d', 'vrm', 'mmd', 'pngtuber'].map(p => `#${p}-${suffix}`).join(','));
    const manager = () => {
        const button = avatar('btn-settings')();
        const prefix = button?.id.split('-')[0];
        return prefix && root[prefix + 'Manager'];
    };
    function step(id, target, options = {}) {
        return { id, title: t(id + '.title'), body: t(id + '.body'), target, ...options };
    }
    const click = (id, target, ready) => step(id, target, { advanceOnClick: true, requireClick: true, ready });
    const optional = (id, target, options) => step(id, target, { nextLabel: t('next'), ...options });
    const windowGuide = () => ({ title: t('openedWindow.title'), body: t('openedWindow.body'),
        nextLabel: t('openedWindow.close'),
        closePending: t('openedWindow.pending'),
        manualClose: t('openedWindow.manualClose'), returnToPage: t('openedWindow.returnToPage'),
        closeSelector: '[data-neko-window-control="close"], #backToMainBtn, .jukebox-close, .page-title-bar .close-page-btn, .neko-window-controls .close-btn, .chat-export-preview-close' });
    function popupView(id) {
        const button = avatar('btn-' + id)();
        const prefix = button?.id.split('-')[0];
        const popup = prefix && find(`#${prefix}-popup-${id}`);
        if (!popup) return null;
        return { title: t(id + '.title'), body: t(id + 'Menu.body'), target: popup,
            shape: 'rect', nextLabel: t('closeMenu'),
            onAdvance() { manager()?.closeAllPopups?.(); } };
    }

    function historyBarRect(element) {
        const button = element.getBoundingClientRect();
        const line = root.getComputedStyle(element, '::before');
        const width = line.width.endsWith('%')
            ? button.width * parseFloat(line.width) / 100 : parseFloat(line.width);
        const height = parseFloat(line.height);
        if (!Number.isFinite(width) || !Number.isFinite(height)) return button;
        const x = button.left + button.width / 2;
        const y = button.top + button.height / 2;
        return { left: x - width / 2, right: x + width / 2,
            top: y - height / 2, bottom: y + height / 2 };
    }

    function expandedHistoryRect(element) {
        const bar = historyBarRect(element);
        const panel = document.querySelector('.compact-export-history-anchor[data-compact-export-history-open="true"] .compact-export-history-panel');
        const area = panel?.getBoundingClientRect();
        if (!area?.width || !area?.height) return bar;
        return { left: Math.min(bar.left, area.left), top: Math.min(bar.top, area.top),
            right: Math.max(bar.right, area.right), bottom: Math.max(bar.bottom, area.bottom) };
    }

    function floatingButtonsRect(element) {
        const prefix = element.id.replace(/-floating-buttons$/, '');
        const buttons = ['mic', 'agent', 'social', 'settings', 'goodbye']
            .map(id => element.querySelector(`#${prefix}-btn-${id}`));
        const rects = buttons.filter(button => button && root.getComputedStyle(button).display !== 'none')
            .map(button => button.getBoundingClientRect()).filter(rect => rect.width && rect.height);
        if (!rects.length) return element.getBoundingClientRect();
        return { left: Math.min(...rects.map(rect => rect.left)),
            top: Math.min(...rects.map(rect => rect.top)),
            right: Math.max(...rects.map(rect => rect.right)),
            bottom: Math.max(...rects.map(rect => rect.bottom)) };
    }

    api.prepareChat = async function () {
        await api.waitUntil(() => root.reactChatWindowHost?.getState && root.reactChatWindowHost?.openWindow,
            new AbortController().signal, 10000);
        const host = root.reactChatWindowHost;
        if (!host?.getState) throw new Error('chat_host_unavailable');
        const snapshot = host.getState();
        const chatWasVisible = document.getElementById('react-chat-window-overlay')?.hidden === false;
        const historyOpen = find('.compact-history-visibility-handle')?.getAttribute('aria-expanded') === 'true';
        const fanOpen = find('.compact-input-tool-fan')?.dataset.compactInputToolFanOpen === 'true';
        const wheelIndex = tools.findIndex(tool => document.querySelector(`.compact-input-tool-item-${tool}`)?.dataset.compactToolWheelSlot === '0');
        const native = root.nekoChatWindow;
        let nativeSnapshot;
        try {
            if (native?.prepareExpandedForTutorial) {
                nativeSnapshot = await native.prepareExpandedForTutorial();
                if (!nativeSnapshot.ready) throw new Error('native_chat_not_ready');
            }
            await host.openWindow();
            await api.waitUntil(() => host.getState()?.mounted, new AbortController().signal, 10000);
            host.setChatSurfaceMode('compact');
            host.setCompactChatState('input');
        } catch (error) {
            try {
                if (nativeSnapshot?.wasCollapsed) await native.restoreCollapsedAfterTutorial?.();
                else if (!chatWasVisible) host.closeWindow?.();
            } catch (restoreError) {
                console.warn('[ClickGuide] Failed to restore chat after preparation:', restoreError);
            }
            throw error;
        }
        // The React composer appears on the next render; inspect its real draft only then.
        await api.waitUntil(() => !!document.querySelector('.compact-chat-surface-frame .composer-input')
            || host.getState()?.composerHidden, new AbortController().signal, 2000).catch(() => {});
        // Only presentation is restored. Drafts, attachments and user-selected switches are untouched.
        return async () => {
            host.setAvatarToolMenuOpen(false, 'click-guide-end');
            host.deactivateAvatarTool();
            host.setCompactToolFanOpen(fanOpen, 'click-guide-end');
            if (wheelIndex >= 0) host.setCompactToolWheelIndex(wheelIndex, 'click-guide-end');
            host.setCompactHistoryOpen(historyOpen, 'click-guide-end');
            host.setChatSurfaceMode(snapshot.chatSurfaceMode);
            host.setCompactChatState(snapshot.compactChatState);
            if (nativeSnapshot?.wasCollapsed) await native.restoreCollapsedAfterTutorial?.();
            else if (!chatWasVisible) host.closeWindow?.();
        };
    };
    api.chatSteps = function () {
        const host = () => root.reactChatWindowHost;
        const history = '.compact-history-visibility-handle';
        const historyOpen = () => find(history)?.getAttribute('aria-expanded') === 'true';
        const fan = () => find('.compact-input-tool-fan')?.dataset.compactInputToolFanOpen === 'true';
        let skipTools = false;
        const hasDraft = () => !!document.querySelector('.compact-input-tool-toggle[type="submit"]')
            || !!host()?.getState?.()?.composerAttachments?.length;
        const noDraft = () => !skipTools && !hasDraft();
        const startedWithDraft = hasDraft();
        const inputAvailable = !!document.querySelector('.compact-chat-surface-frame .composer-input:not(:disabled):not([readonly])');
        let reviewedOverview = false;
        const items = [
            optional('chatOverview', '.compact-chat-surface-frame', {
                shape: 'rect', padding: 0, radiusMode: 'target',
                body() {
                    if (reviewedOverview) return t('chatOverviewBack.body');
                    if (startedWithDraft) return t('chatOverviewDraft.body');
                    if (!inputAvailable) return t('unavailable');
                    return t(find('.composer-input')?.value.trim() ? 'chatOverviewSend.body' : 'chatOverview.body');
                },
                requireInput: !startedWithDraft && inputAvailable,
                advanceOnKey: startedWithDraft || !inputAvailable ? null : 'Enter',
                keyTarget: '.compact-chat-surface-frame .composer-input',
                keyReady: element => !!element.value.trim(),
                ready: () => !document.querySelector('.composer-input')?.value.trim(),
                enter(signal, { backward } = {}) {
                    if (backward) {
                        reviewedOverview = true;
                        this.requireInput = false;
                        this.advanceOnKey = null;
                    }
                    host().setCompactChatState('input');
                    host().setCompactHistoryOpen(false, 'click-guide-overview');
                    host().setCompactToolFanOpen(false, 'click-guide-overview');
                }
            }),
            { ...click('history', history, historyOpen),
                shape: 'rect', catEars: true,
                focusRect: element => historyOpen() ? expandedHistoryRect(element) : historyBarRect(element),
                animateFocus: historyOpen, cursorVisible: () => !historyOpen(),
                async onAdvance({ by, signal }) {
                    if (by !== 'target') return;
                    await api.waitUntil(historyOpen, signal);
                    const revealUntil = Date.now() + 550;
                    await api.waitUntil(() => Date.now() >= revealUntil, signal, 3000);
                },
                enter() { host().setCompactHistoryOpen(false, 'click-guide'); } },
            { ...click('historyClose', history, () => find(history)?.getAttribute('aria-expanded') === 'false'),
                shape: 'rect', catEars: true, focusRect: historyBarRect,
                animateFocus: true, cursorOnTarget: true,
                secondaryTarget: '.compact-export-history-anchor[data-compact-export-history-open="true"] .compact-export-history-panel',
                secondaryShape: 'rect',
                enter(signal, { backward } = {}) {
                    if (backward) host().setCompactHistoryOpen(true, 'click-guide-back');
                } },
            step('tools', () => find(hasDraft() ? '.composer-input' : '.compact-input-tool-toggle[type="button"]'), {
                advanceOnHover: true, hoverTarget: () => !hasDraft() && find('.compact-input-tool-toggle[type="button"]'), ready: fan,
                cardAvoid: () => document.querySelector('.compact-input-tool-fan')?.getBoundingClientRect(),
                body: () => t(hasDraft() ? 'toolsDraft.body' : 'tools.body'),
                enter() {
                    host().setCompactChatState('input');
                    host().setCompactToolFanOpen(false, 'click-guide-tools');
                },
                onAdvance({ by }) {
                    skipTools = by === 'next';
                },
                // A draft can hide the tool button. Clearing it is the user's choice.
                nextLabel: t('skipTools')
            })
        ];
        tools.forEach((tool, index) => {
            items.push(optional(tool, `.compact-input-tool-item-${tool}`, {
                when: noDraft,
                body: () => t(tool + '.body') + ' ' + t('toolContinueHint'),
                advanceOnClick: true,
                consumeTargetClick: true,
                cardAvoid: () => document.querySelector('.compact-input-tool-fan')?.getBoundingClientRect(),
                enter() {
                    if (host().getChatSurfaceMode() === 'minimized') host().setChatSurfaceMode('compact');
                    host().setAvatarToolMenuOpen(false, 'click-guide');
                    host().deactivateAvatarTool();
                    if (find(history)?.getAttribute('aria-expanded') === 'true') {
                        host().setCompactHistoryOpen(false, 'click-guide-wheel');
                    }
                    // Opening a fan does not alter a draft; the React host keeps it intact.
                    host().setCompactToolFanOpen(true, 'click-guide');
                    host().setCompactToolWheelIndex(index, 'click-guide');
                }
            }));
        });
        items.push({ ...click('minimize', '.compact-chat-minimize-ball', () => host().getChatSurfaceMode() === 'minimized'),
            enter() {
                if (host().getChatSurfaceMode() === 'minimized') host().setChatSurfaceMode('compact');
                host().setAvatarToolMenuOpen(false, 'click-guide');
                host().deactivateAvatarTool();
                host().setCompactToolFanOpen(false, 'click-guide');
                host().setCompactHistoryOpen(false, 'click-guide');
            } });
        items.push({ ...click('restore', '#react-chat-window-shell.is-minimized', () => host().getChatSurfaceMode() === 'compact'),
            when: () => host().getChatSurfaceMode() === 'minimized',
            nativeTarget: 'minimizedBall', shape: 'circle' });
        return items;
    };
    api.floatingSteps = function () {
        const close = () => manager()?.closeAllPopups?.();
        let wentHome = false;
        return [
            optional('floatingOverview', () => {
                const prefix = root.universalTutorialManager?.constructor.detectModelPrefix();
                return prefix && find(`#${prefix}-floating-buttons`);
            }, { shape: 'rect', focusRect: floatingButtonsRect, enter: close,
                advanceOnClick: true, consumeTargetClick: true,
                acceptTargetClick(event, group) {
                    const button = event.target.closest?.('[id$="-btn-mic"], [id$="-btn-agent"], [id$="-btn-social"], [id$="-btn-settings"], [id$="-btn-goodbye"]');
                    return !!button && group.contains(button);
                },
                cursorTarget: avatar('btn-mic'),
                body: () => t('floatingOverview.body') + ' ' + t('floatingOverviewContinueHint') }),
            optional('mic', avatar('btn-mic'), { enter: close, view: () => popupView('mic') }),
            optional('agent', avatar('btn-agent'), { enter: close, view: () => popupView('agent'), windowGuide: windowGuide() }),
            optional('social', avatar('btn-social'), { enter: close, windowGuide: windowGuide(), view() {
                return find('.neko-social-embed-close')
                    ? click('socialClose', '.neko-social-embed-close', () => !find('.neko-social-embed-close')) : null;
            } }),
            optional('settings', avatar('btn-settings'), { enter: close,
                view: () => popupView('settings'), windowGuide: windowGuide() }),
            optional('goodbye', avatar('btn-goodbye'), {
                async enter(signal, { backward } = {}) {
                    close();
                    if (backward && find('.neko-idle-return-btn')) {
                        find('.neko-idle-return-btn').click();
                        await api.waitUntil(() => !!avatar('btn-goodbye')(), signal);
                    }
                },
                advanceOnClick: true, ready: () => !!find('.neko-idle-return-btn'),
                onAdvance({ by }) { wentHome = by === 'target'; }
            }),
            step('return', '.neko-idle-return-btn', {
                when: () => wentHome && !!find('.neko-idle-return-btn'),
                advanceOnClick: true, ready: () => !!avatar('btn-settings')(),
                requireClick: true,
                async enter(signal, { backward } = {}) {
                    if (backward && !find('.neko-idle-return-btn')) {
                        avatar('btn-goodbye')()?.click();
                        await api.waitUntil(() => !!find('.neko-idle-return-btn'), signal);
                    }
                }
            }),
            optional('lock', avatar('lock-icon'), { enter() { close(); revealLock(); } }),
            step('finish', null, { nextLabel: t('finishButton') })
        ];
    };
    api.prepareFloating = async function () {
        const prefix = root.universalTutorialManager.constructor.detectModelPrefix();
        const signal = new AbortController().signal;
        const isAway = () => root[prefix + 'Manager']?._goodbyeClicked === true || root[prefix + 'Manager']?._isInReturnState === true;
        const wasAway = isAway();
        const recall = async () => {
            await api.waitUntil(() => !!find('.neko-idle-return-btn'), signal);
            find('.neko-idle-return-btn').click();
            await api.waitUntil(() => !isAway(), signal);
        };
        if (wasAway) await recall();
        await api.waitUntil(() => document.getElementById(prefix + '-floating-buttons'), signal);
        const container = document.getElementById(prefix + '-floating-buttons');
        const oldMarker = container.getAttribute('data-in-tutorial');
        const styles = ['display', 'opacity', 'visibility'].map(name => [name, container.style.getPropertyValue(name), container.style.getPropertyPriority(name)]);
        const mgr = root[prefix + 'Manager'];
        const lock = document.getElementById(prefix + '-lock-icon');
        const lockStyles = lock && ['display', 'opacity', 'visibility'].map(name => [name, lock.style.getPropertyValue(name), lock.style.getPropertyPriority(name)]);
        // Near a screen edge the lock icon can overlap the last toolbar button.
        // Keep its own lesson separate without moving the model or the toolbar.
        lock?.classList.add('click-guide-hidden-control');
        revealLock = () => {
            lock?.classList.remove('click-guide-hidden-control');
            if (lock) {
                lock.style.setProperty('display', 'block', 'important');
                lock.style.setProperty('opacity', '1', 'important');
                lock.style.setProperty('visibility', 'visible', 'important');
            }
        };
        const openMenus = ['mic', 'agent', 'settings'].filter(id => find(`#${prefix}-popup-${id}`));
        container.setAttribute('data-in-tutorial', 'true');
        container.style.display = 'flex';
        container.style.opacity = '1';
        container.style.visibility = 'visible';
        return async () => {
            try {
                if (!wasAway && isAway()) {
                    await recall();
                }
                mgr?.closeAllPopups?.();
                for (const id of openMenus) {
                    const popup = mgr?.createPopup(id);
                    if (popup && popup.style.display !== 'flex') mgr.showPopup(id, popup);
                }
            } finally {
                revealLock = () => {};
                lock?.classList.remove('click-guide-hidden-control');
                for (const [name, value, priority] of isAway() ? [] : (lockStyles || [])) {
                    if (value) lock.style.setProperty(name, value, priority);
                    else lock.style.removeProperty(name);
                }
                if (oldMarker === null) container.removeAttribute('data-in-tutorial');
                else container.setAttribute('data-in-tutorial', oldMarker);
                for (const [name, value, priority] of isAway() ? [] : styles) {
                    if (value) container.style.setProperty(name, value, priority);
                    else container.style.removeProperty(name);
                }
                if (wasAway && !isAway()) {
                    // Restore the original away state through the existing button handler.
                    document.getElementById(prefix + '-btn-goodbye')?.click();
                    await api.waitUntil(isAway, signal);
                }
            }
        };
    };
})(window);
