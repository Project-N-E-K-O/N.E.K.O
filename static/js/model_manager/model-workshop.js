/* Only the native manager may finish the edit check and apply a paired selection. */
(function () {
    'use strict';
    const KEY = 'neko.modelWorkshop.launch';
    const ID = /^[A-Za-z0-9_-]{32}$/;
    const ACTIVE = new Set(['browsing', 'downloading', 'installing', 'installed', 'applying']);
    function mount(panel, host) {
        if (!panel || panel.dataset.mounted) return;
        panel.dataset.mounted = 'true';
        const control = name => panel.querySelector('[data-workshop="' + name + '"]');
        const start = control('start'), cancel = control('cancel'), retry = control('retry');
        const status = control('status'), progress = control('progress');
        let requestId = null, context = null, timer = null, stopped = false, token = null;
        let working = false, terminal = false, invalidated = false, cancelling = false, version = 0, failures = 0;
        let message = 'connectedIdle', parameters = {};
        function remember(value) {
            // One opaque handle, scoped to this instance's tab; no pairing or OAuth credentials.
            try { if (value) sessionStorage.setItem(KEY, value); else sessionStorage.removeItem(KEY); } catch (_) { /* In-page recovery remains available. */ }
        }
        function say(key, values = {}) {
            message = key; parameters = values; status.textContent = host.translate(key, values);
        }
        function labels() {
            panel.querySelectorAll('[data-i18n^="modelWorkshop."]').forEach(element => {
                element.textContent = host.translate(element.dataset.i18n.slice('modelWorkshop.'.length));
            });
            say(message, parameters);
        }
        function busy(value) {
            start.disabled = value; cancel.hidden = !value; progress.hidden = !value;
            progress.removeAttribute('value');
        }
        async function request(suffix, body) {
            const headers = {};
            if (body !== undefined) {
                if (!token) {
                    const config = await fetch('/api/config/page_config', { signal: AbortSignal.timeout(12000), redirect: 'error' }).then(response => response.json());
                    token = config.autostart_csrf_token;
                    if (typeof token !== 'string' || !token) throw new Error('csrf_unavailable');
                }
                headers['X-CSRF-Token'] = token; headers['Content-Type'] = 'application/json';
            }
            const response = await fetch('/api/model-market/launch' + suffix, { method: body === undefined ? 'GET' : 'POST',
                body: body === undefined ? undefined : JSON.stringify(body), headers, credentials: 'same-origin',
                redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(15000) });
            const data = await response.json();
            if (!response.ok || data.success !== true) throw Object.assign(new Error('model_command_failed'), { code: data.error_code });
            return data;
        }
        function later() { clearTimeout(timer); if (!stopped && !terminal) timer = setTimeout(poll, 1000); }
        async function accept(data) {
            if (stopped || terminal) return;
            if (data.request_id !== requestId || typeof data.character_name !== 'string') throw new Error('invalid_task');
            cancel.disabled = data.phase === 'applying' || data.phase === 'saved';
            if (data.phase === 'installed') {
                if (cancelling) { later(); return; }
                // A result alone has no authority to overwrite an edited page or another role.
                if (invalidated || !await host.current(context, data.character_name)) {
                    invalidated = true;
                    remember('blocked:' + requestId);
                    // Another local waiter may already have crossed the save
                    // gate. Render its real response rather than claim rollback.
                    await accept(await request('/' + requestId + '/cancel', {}));
                    return;
                }
                if (cancelling || stopped) { later(); return; }
                say('connectedApplying');
                await accept(await request('/' + requestId + '/apply', {}));
                return;
            }
            if (data.phase === 'saved' || data.phase === 'completed') {
                terminal = true; remember(null); busy(false); retry.hidden = true;
                say(data.error_code ? 'connectedSavedWarning' : 'connectedSaved');
                // Notify the original role only. Reload this clean manager through
                // its existing startup loader, never submit its other form fields.
                host.applied(data, !invalidated && await host.current(context, data.character_name));
                return;
            }
            if (!ACTIVE.has(data.phase)) {
                terminal = true; remember(null); busy(false);
                say(data.phase === 'cancelled' ? (invalidated ? 'connectedEdited' : 'cancelled')
                    : data.error_code === 'model_market_target_changed' ? 'connectedTargetChanged'
                    : ['market_auth_required', 'market_auth_pending'].includes(data.error_code) ? 'connectedLogin'
                    : 'connectedFailed');
                return;
            }
            say('connected' + data.phase[0].toUpperCase() + data.phase.slice(1), { name: data.character_name });
            later();
        }
        async function poll() {
            if (!requestId || stopped || terminal || working) return;
            working = true; retry.hidden = true;
            const readVersion = version;
            try {
                const data = await request('/' + requestId);
                // A cancellation response supersedes an older status response.
                if (readVersion === version) await accept(data);
                failures = 0;
            } catch (_) {
                if (!stopped && !terminal) {
                    say('statusUnknown'); if (++failures < 3) later(); else retry.hidden = false;
                }
            } finally { working = false; }
        }
        start.addEventListener('click', async () => {
            if (working || (requestId && !terminal)) return;
            // Reserve a browser popup before awaiting pairing; Electron uses its
            // existing external-browser API and keeps the native manager alive.
            const external = window.electronShell?.openExternal;
            const popup = external ? null : window.open('about:blank', '_blank');
            if (!external && !popup) return say('connectedPopup');
            if (popup) popup.opener = null;
            working = true; busy(true); say('preparing');
            try {
                context = await host.capture();
                if (!await host.current(context, context.character)) {
                    popup?.close(); busy(false); say('connectedEdited'); return;
                }
                const data = await request('', {});
                requestId = data.request_id; terminal = false; invalidated = false; cancelling = false; failures = 0;
                if (!ID.test(requestId) || !await host.current(context, data.character_name)) throw new Error('target_changed');
                const url = new URL(data.url);
                if ((url.protocol !== 'https:' && !(url.protocol === 'http:'
                    && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)))
                    || url.username || url.password) throw new Error('invalid_market');
                remember(requestId);
                if (external) await external(data.url); else popup.location.replace(data.url);
                say('connectedBrowsing', { name: data.character_name }); later();
            } catch (_) {
                popup?.close();
                // If launch succeeded, retire its authority instead of leaving a hidden selection.
                if (ID.test(requestId || '')) { try { await request('/' + requestId + '/cancel', {}); } catch (_) { /* No selection was sent here. */ } }
                requestId = null; terminal = true; remember(null); busy(false); say('connectedOpenFailed');
            } finally { working = false; }
        });
        cancel.addEventListener('click', async () => {
            if (!requestId || terminal) return;
            ++version; cancelling = true; cancel.disabled = true;
            try { await accept(await request('/' + requestId + '/cancel', {})); }
            catch (_) { say('statusUnknown'); later(); }
        });
        retry.addEventListener('click', () => { failures = 0; void poll(); });
        const edit = event => {
            // Editing invalidates this page's authority even if values are changed back.
            if (requestId && !terminal && !panel.contains(event.target)) {
                invalidated = true;
                remember('blocked:' + requestId); // Reload cannot renew the old permission.
            }
        };
        document.addEventListener('input', edit, true); document.addEventListener('change', edit, true);
        labels(); window.i18next?.on('languageChanged', labels);
        window.addEventListener('pagehide', () => {
            stopped = true; clearTimeout(timer);
            document.removeEventListener('input', edit, true); document.removeEventListener('change', edit, true);
            window.i18next?.off('languageChanged', labels);
            // Download/install continues. Without this local owner no role-save is requested.
        }, { once: true });
        let saved; try { saved = sessionStorage.getItem(KEY); } catch (_) { /* See remember(). */ }
        if (saved?.startsWith('blocked:')) { invalidated = true; saved = saved.slice(8); }
        if (ID.test(saved || '')) {
            requestId = saved; busy(true);
            host.capture().then(value => { context = value; if (!stopped) void poll(); });
        } else { remember(null); busy(false); }
    }
    window.NekoModelWorkshop = { mount };
})();
