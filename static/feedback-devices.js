(function () {
    'use strict';
    const base = '/api/proactive/feedback';
    const nodes = Object.fromEntries(['status','upgrade','device-controls','device-name','replacement','replacement-id','activate','refresh','devices'].map(id => [id, document.getElementById(id)]));
    let view = 0, disposed = false, busy = false, mutation = null, current = null, csrf = null, statusKey = '';
    let replacementChoice = null;
    const t = key => typeof window.t === 'function' ? window.t('feedbackDevices.' + key) : key;
    const status = key => { statusKey = key; nodes.status.textContent = t(key); };
    async function request(path, body, retry = true, epoch = view) {
        if (disposed || epoch !== view) throw Error('session_changed');
        if (!csrf) {
            const response = await fetch('/api/config/page_config', { cache: 'no-store', signal: AbortSignal.timeout(10000) });
            const config = await response.json();
            if (!response.ok || typeof config.autostart_csrf_token !== 'string') throw Error('unavailable');
            if (disposed || epoch !== view) throw Error('session_changed');
            csrf = config.autostart_csrf_token;
        }
        const response = await fetch(base + path, { method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(10000) });
        const value = await response.json();
        // This guard rejects before any control mutation. A restarted backend
        // can rotate its token while the management page remains open.
        if (response.status === 403 && value && value.error_code === 'csrf_validation_failed' && retry) {
            if (disposed || epoch !== view) throw Error('session_changed');
            csrf = null;
            return request(path, body, false, epoch);
        }
        if (!response.ok || !value || value.success !== true) throw Error(value && value.error_code || 'unavailable');
        return value;
    }
    function render() {
        if (!current) return;
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
                    expected_registration_version: row.registration_version, expected_owner_key: ownerKey }));
                li.appendChild(button);
            }
            nodes.devices.appendChild(li);
        }
    }
    async function refresh() {
        const epoch = ++view;
        // A failed follow-up read must still leave an exit from a finished mutation.
        nodes.refresh.disabled = busy;
        try {
            const state = await request('/status');
            if (disposed || epoch !== view) return;
            nodes.upgrade.hidden = !state.upgrade_required;
            if (state.upgrade_required || state.protocol_version !== 2) {
                current = null; nodes.devices.replaceChildren(); nodes.activate.disabled = true;
                nodes['device-controls'].hidden = true;
                status(state.upgrade_required ? 'upgrade' : 'notEnabled'); return;
            }
            const value = await request('/devices');
            if (disposed || epoch !== view) return;
            if (state.owner_key !== value.owner_key) throw Error('session_changed');
            current = value; nodes['device-controls'].hidden = false;
            status('ready'); render();
        } catch (error) {
            if (disposed || epoch !== view) return;
            current = null; nodes.devices.replaceChildren(); nodes.activate.disabled = true;
            status(error.message === 'cloud_conflict' ? 'conflict' : 'unavailable');
        }
    }
    async function mutate(path, body) {
        if (busy || !current) return;
        const epoch = view, operation = {}, ownerKey = current.owner_key;
        let failure = '';
        mutation = operation; busy = true; render();
        try { await request(path, body); }
        catch (error) { failure = error.message === 'cloud_conflict' ? 'conflict' : 'unavailable'; }
        finally {
            if (mutation === operation) {
                mutation = null; busy = false;
                if (!disposed && epoch === view) {
                    const refreshView = view + 1;
                    await refresh();
                    if (failure && !disposed && view === refreshView && current && current.owner_key === ownerKey) status(failure);
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
    window.addEventListener('pagehide', () => { disposed = true; ++view; mutation = null; busy = false; });
    window.addEventListener('pageshow', () => { if (disposed) { disposed = false; void refresh(); } });
    void refresh();
}());
