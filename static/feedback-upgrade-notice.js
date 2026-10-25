/* A visible notice for updated clients. No timer, playback gate or auto-update. */
(function () {
    'use strict';
    let disposed = false, fetching = null, notice = null, required = false, revision = 0;
    function render() {
        if (disposed) return;
        if (!required) { if (notice) notice.remove(); notice = null; return; }
        if (!document.body || notice) return;
        notice = document.createElement('aside');
        notice.setAttribute('role', 'status');
        notice.style.cssText = 'position:fixed;left:16px;right:16px;bottom:16px;z-index:10020;padding:12px 16px;border-radius:12px;background:#fff4dd;color:#183b55;box-shadow:0 3px 18px #0002;font:14px system-ui;pointer-events:auto';
        const text = document.createElement('span'), link = document.createElement('a');
        text.textContent = window.t ? window.t('feedbackDevices.upgrade') : 'Update the desktop app to resume history uploads. Normal use is unaffected.';
        link.textContent = window.t ? window.t('feedbackDevices.update') : 'Get the update';
        link.href = 'https://github.com/Project-N-E-K-O/N.E.K.O/releases'; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.style.marginInlineStart = '12px';
        notice.appendChild(text); notice.appendChild(link); document.body.appendChild(notice);
    }
    async function readStatus() {
        if (disposed || fetching || document.hidden) return;
        const security = window.nekoLocalMutationSecurity;
        if (!security || typeof security.getMutationHeaders !== 'function') return;
        const operation = {}, expectedRevision = revision;
        fetching = operation;
        try {
            const headers = await security.getMutationHeaders();
            const response = await fetch('/api/proactive/feedback/status', { headers, cache: 'no-store', signal: AbortSignal.timeout(5000) });
            const value = await response.json();
            if (!disposed && fetching === operation && revision === expectedRevision
                && response.ok && value && value.success === true) { required = value.upgrade_required === true; render(); }
        } catch (_) { /* An unavailable status cannot claim that upgrade completed. */ }
        finally { if (fetching === operation) fetching = null; }
    }
    window.addEventListener('neko:feedback-upgrade-required', () => { ++revision; required = true; render(); });
    window.addEventListener('localechange', () => { if (notice) notice.remove(); notice = null; render(); });
    window.addEventListener('focus', () => { void readStatus(); });
    window.addEventListener('pagehide', () => { disposed = true; ++revision; fetching = null; if (notice) notice.remove(); notice = null; });
    window.addEventListener('pageshow', () => { if (disposed) { disposed = false; render(); void readStatus(); } });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { render(); void readStatus(); }, { once: true });
    else { render(); void readStatus(); }
}());
