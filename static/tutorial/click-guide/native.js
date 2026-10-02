(function (root) {
    'use strict';
    const api = root.NekoClickGuide;
    api.createNativePresentation = function () {
        const bridge = root.nekoTutorialOverlay;
        if (!bridge?.clickGuideUpdate) return null;
        const runId = api.createRunId('click-view-');
        let sequence = 0;
        let frame;
        let callbacks;
        let closed = false;
        let sending = false;
        let lastKey;
        let sentAt = 0;
        let nativeAvailability = null;
        const receive = event => {
            const data = event.detail;
            if (!closed && data?.runId === runId && data.step === frame?.step) callbacks?.[data.action]?.();
        };
        async function publish() {
            if (closed || sending || !frame) return;
            const key = JSON.stringify(frame);
            if (key === lastKey && Date.now() - sentAt < 1000) return;
            sending = true;
            lastKey = key;
            sentAt = Date.now();
            try {
                const publishedStep = frame.step;
                const result = await bridge.clickGuideUpdate({ runId, sequence: ++sequence, frame });
                if (!result?.ok && !closed) callbacks?.failed?.();
                if (result?.ok && frame?.step === publishedStep
                    && typeof result.nativeTargetAvailable === 'boolean'
                    && nativeAvailability !== result.nativeTargetAvailable) {
                    nativeAvailability = result.nativeTargetAvailable;
                    callbacks?.nativeTargetAvailability?.(nativeAvailability);
                }
            } catch (error) {
                if (!closed) callbacks?.failed?.();
            } finally { sending = false; }
        }
        const timer = setInterval(publish, 50);
        return {
            bind(actions) { callbacks = actions; root.addEventListener('neko:click-guide-action', receive); },
            nativeTargetAvailable() { return nativeAvailability; },
            update(value) {
                if (frame?.step !== value.step) nativeAvailability = null;
                frame = { ...value, dark: document.documentElement.dataset.theme === 'dark'
                    || document.documentElement.classList.contains('dark'),
                    cardBackgroundUrl: new URL('/static/assets/tutorial/click-guide/card-background.png', root.location.href).href,
                    cursorUrl: new URL('/static/assets/tutorial/ghost-cursor/default-ghost-cursor.png', root.location.href).href,
                    clickCursorUrl: new URL('/static/assets/tutorial/ghost-cursor/click-ghost-cursor.png', root.location.href).href,
                    rect: value.rect && { ...value.rect,
                    width: value.rect.right - value.rect.left, height: value.rect.bottom - value.rect.top },
                    secondaryRect: value.secondaryRect && { ...value.secondaryRect,
                    width: value.secondaryRect.right - value.secondaryRect.left,
                    height: value.secondaryRect.bottom - value.secondaryRect.top } };
            },
            async close() {
                if (closed) return;
                closed = true;
                clearInterval(timer);
                root.removeEventListener('neko:click-guide-action', receive);
                await bridge.clickGuideClose({ runId });
            }
        };
    };
})(window);
