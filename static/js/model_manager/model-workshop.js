/* The manager owns background checks; the market owns visible task feedback. */
(function () {
    'use strict';
    const KEY = 'neko.modelWorkshop.launch';
    const ID = /^[A-Za-z0-9_-]{32}$/;
    const ACTIVE = new Set(['browsing', 'downloading', 'installing', 'installed', 'applying']);
    function mount(panel, host) {
        if (!panel || panel.dataset.mounted) return;
        panel.dataset.mounted = 'true';
        const start = panel.querySelector('[data-workshop="start"]');
        let requestId = null, context = null, timer = null, stopped = false, token = null;
        let working = false, terminal = false, invalidated = false, failures = 0;
        // Only bound disconnected polling here; Main retains the authoritative expiry.
        let retryUntil = Date.now() + 30 * 60 * 1000;
        function remember(value) {
            // Keep only the opaque handle and edit-invalidated bit, never credentials.
            try { if (value) sessionStorage.setItem(KEY, value); else sessionStorage.removeItem(KEY); } catch (_) { /* In-page recovery still works. */ }
        }
        function labels() { panel.querySelector('[data-i18n="modelWorkshop.title"]').textContent = host.translate('title'); }
        function finish() { terminal = true; remember(null); clearTimeout(timer); start.disabled = false; }
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
        function later(delay = 1000) { clearTimeout(timer); if (!stopped && !terminal) timer = setTimeout(poll, delay); }
        async function accept(data) {
            if (stopped || terminal) return;
            if (data.request_id !== requestId || typeof data.character_name !== 'string') throw new Error('invalid_task');
            if (data.phase === 'installed') {
                // Removing inline UI must not remove the edit/role authorization gate.
                const current = await host.current(context, data.character_name);
                if (invalidated || !current) {
                    invalidated = true; remember('blocked:' + requestId);
                    // Respect the actual save result if cancellation loses a commit race.
                    await accept(await request('/' + requestId + '/cancel', {}));
                    return;
                }
                if (!stopped) await accept(await request('/' + requestId + '/apply', {}));
                return;
            }
            if (data.phase === 'saved' || data.phase === 'completed') {
                finish();
                // The existing role-filtered notification works while minimized too.
                host.applied(data, !invalidated && await host.current(context, data.character_name));
                return;
            }
            if (!ACTIVE.has(data.phase)) { finish(); return; }
            later();
        }
        async function poll() {
            if (!requestId || stopped || terminal || working) return;
            working = true;
            try { await accept(await request('/' + requestId)); failures = 0; }
            catch (error) {
                if (!stopped && !terminal) {
                    if (error.code === 'model_market_expired' || Date.now() >= retryUntil) finish();
                    else {
                        // No inline retry/progress UI: retry reads of the same task,
                        // with bounded backoff; never replay a selection or a save.
                        later(Math.min(30000, 1000 * 2 ** Math.min(++failures, 5)));
                    }
                }
            } finally { working = false; }
        }
        start.addEventListener('click', async () => {
            if (working || (requestId && !terminal)) return;
            const shell = window.electronShell;
            const external = typeof shell?.openExternal === 'function';
            // Reserve a browser popup within the click gesture, before pairing.
            const popup = external ? null : window.open('about:blank', '_blank');
            if (!external && !popup) return;
            if (popup) popup.opener = null;
            working = true; start.disabled = true;
            try {
                context = await host.capture();
                if (!await host.current(context, context.character)) { popup?.close(); start.disabled = false; return; }
                const data = await request('', {});
                requestId = data.request_id; terminal = false; invalidated = false; failures = 0;
                retryUntil = Date.now() + 30 * 60 * 1000;
                if (!ID.test(requestId) || !await host.current(context, data.character_name)) throw new Error('target_changed');
                const url = new URL(data.url);
                if ((url.protocol !== 'https:' && !(url.protocol === 'http:'
                    && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)))
                    || url.username || url.password) throw new Error('invalid_market');
                remember(requestId);
                if (external) {
                    const opened = await shell.openExternal(data.url);
                    if (opened?.success === false) throw new Error('market_open_failed');
                    // The existing IPC minimizes this sender window, not the newly
                    // focused browser. Keep the manager alive for automatic apply.
                    try { await window.nekoWindowControl?.minimize?.(); }
                    catch (_) { console.warn('[ModelWorkshop] Could not minimize the model manager'); }
                } else {
                    popup.location.replace(data.url);
                    try { popup.focus(); } catch (_) { /* Browser focus policy is best effort. */ }
                }
                later();
            } catch (_) {
                popup?.close();
                if (ID.test(requestId || '')) { try { await request('/' + requestId + '/cancel', {}); } catch (_) { /* No selection is sent by launch. */ } }
                requestId = null; finish();
                console.warn('[ModelWorkshop] Could not open the market');
            } finally { working = false; }
        });
        const edit = event => {
            if (requestId && !terminal && !panel.contains(event.target)) {
                invalidated = true; remember('blocked:' + requestId);
            }
        };
        document.addEventListener('input', edit, true); document.addEventListener('change', edit, true);
        labels(); window.i18next?.on('languageChanged', labels);
        window.addEventListener('pagehide', () => {
            stopped = true; clearTimeout(timer);
            document.removeEventListener('input', edit, true); document.removeEventListener('change', edit, true);
            window.i18next?.off('languageChanged', labels);
        }, { once: true });
        let saved; try { saved = sessionStorage.getItem(KEY); } catch (_) { /* See remember(). */ }
        if (saved?.startsWith('blocked:')) { invalidated = true; saved = saved.slice(8); }
        if (ID.test(saved || '')) {
            requestId = saved; start.disabled = true;
            host.capture().then(value => { context = value; if (!stopped) void poll(); }).catch(finish);
        } else { remember(null); start.disabled = false; }
    }
    window.NekoModelWorkshop = { mount };
})();
