(function () {
    'use strict';
    const base = '/api/proactive/feedback';
    const nodes = Object.fromEntries(['status','upgrade','device-controls','device-name','replacement','replacement-id','activate','refresh','devices','coming-soon','coming-soon-title','management-content'].map(id => [id, document.getElementById(id)]));
    let view = 0, disposed = false, busy = false, mutation = null, current = null, csrf = null, statusKey = '';
    let replacementChoice = null, read = null, scheduledRefresh = null, unsubscribeAccount = null, accountChanging = false;
    const t = key => typeof window.t === 'function' ? window.t('feedbackDevices.' + key) : key;
    const status = key => {
        // Keep the last authoritative capability view during a pending read.
        if (key === 'loading') { statusKey = key; nodes.status.textContent = t(key); return; }
        const showComingSoon = key === 'notEnabled';
        const outgoing = nodes[showComingSoon ? 'management-content' : 'coming-soon'];
        const moveFocus = !outgoing.hidden && outgoing.contains(document.activeElement);
        statusKey = key;
        nodes.status.textContent = t(key);
        nodes['coming-soon'].hidden = !showComingSoon;
        nodes['management-content'].hidden = showComingSoon;
        if (moveFocus) nodes[showComingSoon ? 'coming-soon-title' : 'refresh'].focus({ preventScroll: true });
    };
    const announce = state => window.dispatchEvent(new CustomEvent('neko:feedback-status', { detail: state }));
    async function request(path, body, retry = true, epoch = view, signal) {
        if (disposed || epoch !== view) throw Error('session_changed');
        const deadline = () => signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000);
        if (!csrf) {
            const response = await fetch('/api/config/page_config', { cache: 'no-store', signal: deadline() });
            const config = await response.json();
            if (!response.ok || typeof config.autostart_csrf_token !== 'string') throw Error('unavailable');
            if (disposed || epoch !== view) throw Error('session_changed');
            csrf = config.autostart_csrf_token;
        }
        const response = await fetch(base + path, { method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: body === undefined ? undefined : JSON.stringify(body), signal: deadline() });
        const value = await response.json();
        if (disposed || epoch !== view) throw Error('session_changed');
        // This guard rejects before any control mutation. A restarted backend
        // can rotate its token while the management page remains open.
        if (response.status === 403 && value && value.error_code === 'csrf_validation_failed' && retry) {
            if (disposed || epoch !== view) throw Error('session_changed');
            csrf = null;
            return request(path, body, false, epoch, signal);
        }
        if (!response.ok || !value || value.success !== true) throw Error(value && value.error_code || 'unavailable');
        return value;
    }
    function render() {
        if (!current) return;
        const epoch = view;
        const devices = current.devices || [];
        const ownerKey = current.owner_key;
        const active = devices.filter(row => row.active);
        const local = devices.find(row => row.is_current && row.active);
        const full = !local && active.length >= current.max_active_devices;
        nodes.replacement.hidden = !full;
        const chosen = replacementChoice && replacementChoice.owner === ownerKey
            && active.some(row => !row.is_current && row.registration_id === replacementChoice.id
                && row.registration_version === replacementChoice.version) ? replacementChoice.id : '';
        if (!chosen) replacementChoice = null;
        nodes['replacement-id'].replaceChildren();
        const placeholder = document.createElement('option');
        placeholder.value = ''; placeholder.textContent = t('selectDevice');
        nodes['replacement-id'].appendChild(placeholder);
        for (const row of active.filter(row => !row.is_current)) {
            const option = document.createElement('option');
            option.value = row.registration_id; option.textContent = row.display_name || row.registration_id.slice(0, 8);
            nodes['replacement-id'].appendChild(option);
        }
        nodes['replacement-id'].value = chosen;
        nodes.activate.disabled = busy || !!local || (full && !nodes['replacement-id'].value);
        nodes['replacement-id'].disabled = busy;
        nodes['device-name'].disabled = busy;
        nodes.refresh.disabled = busy;
        nodes.devices.replaceChildren();
        for (const row of devices) {
            const li = document.createElement('li'), text = document.createElement('span');
            text.textContent = (row.display_name || row.registration_id.slice(0, 8)) + (row.is_current ? ' · ' + t('thisDevice') : '') + ' · ' + t(row.active ? 'enabled' : 'removed');
            li.appendChild(text);
            if (row.active) {
                const button = document.createElement('button'); button.type = 'button'; button.disabled = busy;
                button.textContent = t('remove');
                button.addEventListener('click', () => mutate('/devices/' + encodeURIComponent(row.registration_id) + '/remove', {
                    expected_registration_version: row.registration_version, expected_owner_key: ownerKey }, epoch));
                li.appendChild(button);
            }
            nodes.devices.appendChild(li);
        }
    }
    function retire() {
        ++view;
        if (read) read.abort();
        read = null; current = null; replacementChoice = null;
        // Detached remove callbacks also carry their view, so A -> B -> A
        // cannot revive an action captured before either account switch.
        // Transfer focus before retiring a focused input or detached remove button.
        if (nodes['device-controls'].contains(document.activeElement)) nodes.refresh.focus({ preventScroll: true });
        nodes.devices.replaceChildren(); nodes['replacement-id'].replaceChildren();
        nodes.activate.disabled = true; nodes['replacement-id'].disabled = true;
        nodes['device-name'].disabled = true; nodes['device-controls'].hidden = true;
        nodes.refresh.disabled = busy;
        announce(null);
    }
    function refresh() {
        if (disposed || accountChanging) return Promise.resolve();
        if (scheduledRefresh) return scheduledRefresh;
        retire(); status('loading');
        // Focus and visibility commonly arrive together. One microtask combines
        // that burst without a timer or a second background polling loop.
        const scheduled = Promise.resolve().then(() => {
            if (scheduledRefresh !== scheduled) return;
            scheduledRefresh = null;
            if (!disposed) return load(view);
        });
        scheduledRefresh = scheduled;
        return scheduled;
    }
    function stateMessage(state) {
        if (state.upload_paused) return 'paused';
        if (['device_activation_required', 'registration_removed'].includes(state.control_state)) return 'awaitingEnable';
        if (state.last_sync_error) return 'uploadError';
        if (state.identity_verified === false) return 'identityPending';
        if (state.fully_synced === true) return 'fullySynced';
        if (state.pending > 0 || state.settings_journal_pending > 0 || state.held > 0) return 'uploadPending';
        return 'ready';
    }
    async function load(epoch) {
        const operation = new AbortController();
        read = operation;
        try {
            const state = await request('/status', undefined, true, epoch, operation.signal);
            if (disposed || epoch !== view) return;
            announce(state);
            if (disposed || epoch !== view) return;
            nodes.upgrade.hidden = !state.upgrade_required;
            if (state.upgrade_required || state.protocol_version !== 2) {
                status(state.upgrade_required ? 'upgrade' : 'notEnabled'); return;
            }
            if (state.signed_in === false) { status('signedOut'); return; }
            const value = await request('/devices', undefined, true, epoch, operation.signal);
            if (disposed || epoch !== view) return;
            if (state.owner_key !== value.owner_key) throw Error('session_changed');
            current = value; nodes['device-controls'].hidden = false;
            status(stateMessage(state)); render();
        } catch (error) {
            if (disposed || epoch !== view) return;
            announce(null);
            if (disposed || epoch !== view) return;
            status(error.message === 'cloud_conflict' ? 'conflict' : 'unavailable');
        } finally {
            if (read === operation) read = null;
        }
    }
    async function mutate(path, body, expectedView = view) {
        if (busy || !current || expectedView !== view || body.expected_owner_key !== current.owner_key) return;
        const epoch = view, operation = {}, ownerKey = current.owner_key;
        let failure = '';
        mutation = operation; busy = true; render();
        try { await request(path, body); }
        catch (error) { failure = error.message === 'cloud_conflict' ? 'conflict' : 'unavailable'; }
        finally {
            if (mutation === operation) {
                mutation = null; busy = false;
                if (!disposed) {
                    const sameView = epoch === view;
                    const refreshView = view + 1;
                    await refresh();
                    if (failure && sameView && !disposed && view === refreshView && current && current.owner_key === ownerKey) status(failure);
                }
            }
        }
    }
    nodes.activate.disabled = true;
    nodes.activate.addEventListener('click', () => {
        if (!current) return;
        const body = { expected_owner_key: current.owner_key, activation_context: current.activation_context };
        if (!nodes.replacement.hidden) {
            if (!replacementChoice || replacementChoice.owner !== current.owner_key
                || replacementChoice.id !== nodes['replacement-id'].value) return;
            body.replace_registration_id = replacementChoice.id;
            body.expected_replace_registration_version = replacementChoice.version;
        }
        const name = nodes['device-name'].value.trim();
        if (name) body.display_name = name;
        void mutate('/devices/activate', body);
    });
    nodes['replacement-id'].addEventListener('change', () => {
        const selected = current && current.devices.find(row => row.active && !row.is_current
            && row.registration_id === nodes['replacement-id'].value);
        replacementChoice = selected && Number.isSafeInteger(selected.registration_version) && selected.registration_version >= 1
            ? { owner: current.owner_key, id: selected.registration_id, version: selected.registration_version } : null;
        render();
    });
    nodes.refresh.addEventListener('click', () => { if (!busy) void refresh(); });
    window.addEventListener('localechange', () => { if (statusKey) status(statusKey); render(); });
    window.addEventListener('neko:account-changing', () => { if (!disposed) { accountChanging = true; scheduledRefresh = null; retire(); status('loading'); } });
    window.addEventListener('neko:account-changed', () => { accountChanging = false; if (!disposed) void refresh(); });
    window.addEventListener('focus', () => { if (!disposed) void refresh(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden && !disposed) void refresh(); });
    function watchAccount() {
        if (!unsubscribeAccount && typeof window.electronShell?.onAccountChanged === 'function') {
            unsubscribeAccount = window.electronShell.onAccountChanged(() => { accountChanging = false; if (!disposed) void refresh(); });
        }
    }
    window.addEventListener('pagehide', () => {
        disposed = true; accountChanging = false; scheduledRefresh = null; retire();
        if (unsubscribeAccount) { unsubscribeAccount(); unsubscribeAccount = null; }
        // An already-sent mutation retains its owner until it settles or its
        // own deadline expires. A BFCache restore cannot issue a duplicate.
    });
    window.addEventListener('pageshow', () => { if (disposed) { disposed = false; watchAccount(); void refresh(); } });
    watchAccount();
    void refresh();
}());
